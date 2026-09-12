from dataclasses import FrozenInstanceError, replace
from datetime import date, datetime, timedelta
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

# Run with the integration's established unittest discovery command.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from integrations.task_state import SourceIdentity, TaskPriority, TaskStatus
from integrations.todoist.normalize import normalize_todoist_snapshot, NormalizationError
from integrations.todoist.reader import load_snapshot, _freeze
from test_reader import fixture


class NormalizationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        generation = self.root / 'generation'
        generation.mkdir()
        data = fixture('A')
        data['tasks'][0].update(description='Details', priority=4,
                                added_at='2026-09-01T10:00:00Z', updated_at='2026-09-02T11:00:00Z')
        for name, value in data.items():
            (generation / (name + '.json')).write_text(json.dumps(value), encoding='utf-8')
        current = self.root / 'current'
        current.symlink_to(generation)
        self.snapshot = load_snapshot(current)

    def modified(self, **updates):
        row = dict(self.snapshot.tasks[0])
        row.update(updates)
        return replace(self.snapshot, tasks=(_freeze(row), *self.snapshot.tasks[1:]))

    def test_complete_normalization(self):
        state = normalize_todoist_snapshot(self.snapshot)
        self.assertEqual((len(state.tasks), len(state.projects), len(state.sections)), (2, 1, 1))
        task = state.tasks[0]
        self.assertEqual((task.title, task.description, task.tags), ('A', 'Details', ('label',)))
        self.assertEqual(task.status, TaskStatus.ACTIVE)
        self.assertEqual(task.created_at, datetime.fromisoformat('2026-09-01T10:00:00Z'))
        self.assertEqual(task.updated_at, datetime.fromisoformat('2026-09-02T11:00:00Z'))
        self.assertEqual(task.provenance, state.provenance)
        self.assertEqual(state.provenance.generation, str(self.snapshot.directory))
        self.assertEqual(state.provenance.synced_at, datetime.fromisoformat(self.snapshot.metadata['synced_at']))

    def test_identity_preservation(self):
        task = normalize_todoist_snapshot(self.snapshot).tasks[0]
        self.assertEqual(task.id, SourceIdentity('todoist', 'A-parent'))
        self.assertEqual(task.source_id, 'A-parent')
        self.assertEqual(task.source, 'todoist')
        self.assertNotEqual(task.id, SourceIdentity('another-provider', task.source_id))

    def test_context_resolution(self):
        state = normalize_todoist_snapshot(self.snapshot)
        task = state.tasks[0]
        self.assertEqual(task.project, state.projects[0])
        self.assertEqual(task.section, state.sections[0])
        self.assertEqual(task.project.name, 'A')
        self.assertEqual(task.section.name, 'A')
        self.assertEqual(task.section.project_id, task.project.id)

    def test_hierarchy_independent_of_order(self):
        snapshot = replace(self.snapshot, tasks=tuple(reversed(self.snapshot.tasks)))
        state = normalize_todoist_snapshot(snapshot)
        by_id = {t.id: t for t in state.tasks}
        self.assertEqual([t.source_id for t in state.tasks], [t['id'] for t in snapshot.tasks])
        self.assertEqual(by_id[state.tasks[0].parent_id].source_id, 'A-parent')

    def test_missing_optional_fields(self):
        # Reader currently requires some fields; adapter also tolerates absent
        # optional values on already materialized snapshots without weakening it.
        row = dict(self.snapshot.tasks[0])
        for key in ('description', 'due', 'deadline', 'section_id', 'parent_id',
                    'labels', 'priority', 'added_at', 'updated_at'):
            row.pop(key, None)
        state = normalize_todoist_snapshot(replace(self.snapshot, tasks=(_freeze(row),)))
        task = state.tasks[0]
        for key in ('description', 'due', 'deadline', 'section', 'parent_id', 'tags',
                    'priority', 'created_at', 'updated_at'):
            self.assertIsNone(getattr(task, key), key)
        task = normalize_todoist_snapshot(self.modified(labels=[])).tasks[0]
        self.assertEqual(task.tags, ())

    def test_date_only_and_distinct_deadline(self):
        task = normalize_todoist_snapshot(self.modified(deadline={'date': '2026-09-20'})).tasks[0]
        self.assertIs(type(task.due.value), date)
        self.assertEqual(task.due.value, date(2026, 9, 12))
        self.assertEqual(task.deadline.value, date(2026, 9, 20))
        self.assertIsNone(task.due.timezone)

    def test_datetime_and_timezone(self):
        for due in ({'date': '2026-09-12T14:30:00+02:00', 'timezone': 'Europe/Paris'},
                    {'date': '2026-09-12', 'datetime': '2026-09-12T14:30:00+02:00',
                     'timezone': 'Europe/Paris'}):
            with self.subTest(due=due):
                task = normalize_todoist_snapshot(self.modified(due=due)).tasks[0]
                self.assertIs(type(task.due.value), datetime)
                self.assertEqual(task.due.value.hour, 14)
                self.assertEqual(task.due.value.utcoffset(), timedelta(hours=2))
                self.assertEqual(task.due.timezone, 'Europe/Paris')

    def test_floating_datetime(self):
        task = normalize_todoist_snapshot(self.modified(due={'date': '2026-09-12T14:30:00',
                                                             'timezone': None})).tasks[0]
        self.assertEqual(task.due.value, datetime(2026, 9, 12, 14, 30))
        self.assertIsNone(task.due.value.tzinfo)
        self.assertIsNone(task.due.timezone)

    def test_recurring_metadata(self):
        due = dict(date='2026-09-12', is_recurring=True, string='every day', lang='en')
        task = normalize_todoist_snapshot(self.modified(due=due)).tasks[0]
        self.assertEqual((task.due.recurring, task.due.expression, task.due.language),
                         (True, 'every day', 'en'))
        self.assertEqual(task.due.value, date(2026, 9, 12))

    def test_priority_and_completion(self):
        for priority, expected in enumerate((TaskPriority.NORMAL, TaskPriority.MEDIUM,
                                            TaskPriority.HIGH, TaskPriority.URGENT), start=1):
            task = normalize_todoist_snapshot(self.modified(priority=priority, checked=True)).tasks[0]
            self.assertEqual(task.priority, expected)
            self.assertEqual(task.status, TaskStatus.COMPLETED)
            self.assertEqual(task.raw['priority'], priority)

    def test_no_mutation_and_determinism(self):
        before = repr(self.snapshot)
        with patch('socket.socket', side_effect=AssertionError('Network access')),\
             patch.object(Path, 'open', side_effect=AssertionError('Filesystem access')):
            a = normalize_todoist_snapshot(self.snapshot)
            b = normalize_todoist_snapshot(self.snapshot)
        self.assertEqual(a, b)
        self.assertEqual(before, repr(self.snapshot))
        with self.assertRaises(FrozenInstanceError):
            a.tasks[0].title = 'changed'
        with self.assertRaises(TypeError):
            a.tasks[0].raw['extra']['preserved'][0] = 2
        self.assertEqual(a.tasks[0].raw['extra']['preserved'], (1,))

    def test_invalid_values_fail_cleanly(self):
        for updates in ({'due': {'date': 'bad'}}, {'priority': True}, {'priority': 5},
                        {'description': []}, {'added_at': '2026-09-12'},
                        {'due': {'is_recurring': 'yes'}}, {'labels': 'bad'}):
            with self.subTest(updates=updates):
                with self.assertRaises(NormalizationError):
                    normalize_todoist_snapshot(self.modified(**updates))


if __name__ == '__main__':
    unittest.main()
