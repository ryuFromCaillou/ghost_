from copy import deepcopy
from dataclasses import asdict
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from goal_store import GoalStoreError, load_goal_registry, save_goal_registry
from goals import Goal, GoalRegistry, Milestone, TaskMilestoneLink


class GoalStoreTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'state/goals.json'
        self.registry = GoalRegistry(
            [Goal('g', 'Apprendre café', '日本語')],
            [Milestone('m2', 'g', 'Practice'), Milestone('m1', 'g', 'Read')],
            [TaskMilestoneLink('todoist', 't', 'm1')])
        self.payload = json.loads(json.dumps(asdict(self.registry)))

    def write(self, value):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(value), encoding='utf-8')

    def assert_invalid(self, value, cause=ValueError):
        self.write(value)
        with self.assertRaises(GoalStoreError) as caught:
            load_goal_registry(self.path)
        self.assertIsInstance(caught.exception.__cause__, cause)

    def test_round_trip(self):
        save_goal_registry(self.registry, self.path)
        loaded = load_goal_registry(str(self.path))
        self.assertEqual(loaded, self.registry)
        self.assertIsInstance(loaded.goals[0], Goal)
        self.assertIsInstance(loaded.milestones[0], Milestone)
        self.assertIsInstance(loaded.task_links[0], TaskMilestoneLink)
        self.assertEqual(loaded.goal_for_task('todoist', 't'), self.registry.goals[0])

    def test_empty_round_trip(self):
        save_goal_registry(GoalRegistry(), self.path)
        self.assertEqual(load_goal_registry(self.path), GoalRegistry())

    def test_deterministic_output(self):
        save_goal_registry(self.registry, self.path)
        first = self.path.read_bytes()
        save_goal_registry(load_goal_registry(self.path), self.path)
        self.assertEqual(first, self.path.read_bytes())
        self.assertEqual(first.decode('utf-8'), json.dumps(
            self.payload, indent=2, sort_keys=True, ensure_ascii=False) + '\n')

    def test_malformed_json(self):
        self.write({})
        self.path.write_text('{', encoding='utf-8')
        with self.assertRaises(GoalStoreError) as caught:
            load_goal_registry(self.path)
        self.assertIsInstance(caught.exception.__cause__, json.JSONDecodeError)

    def test_missing_top_level_keys(self):
        for key in self.payload:
            with self.subTest(key=key):
                self.assert_invalid({k: v for k, v in self.payload.items() if k != key})

    def test_unknown_top_level_keys(self):
        self.assert_invalid(dict(self.payload, extra=[]))

    def test_invalid_goal_reference(self):
        self.payload['milestones'][0]['goal_id'] = 'missing'
        self.assert_invalid(self.payload)

    def test_invalid_milestone_reference(self):
        self.payload['task_links'][0]['milestone_id'] = 'missing'
        self.assert_invalid(self.payload)

    def test_duplicate_task_association(self):
        self.payload['task_links'].append(dict(self.payload['task_links'][0], milestone_id='m2'))
        self.assert_invalid(self.payload)

    def test_invalid_schema(self):
        for value in (None, [], 'text', 1):
            with self.subTest(value=value):
                self.assert_invalid(value)
        for name in self.payload:
            for value in (None, {}, 'text', [None], [[]]):
                with self.subTest(name=name, value=value):
                    self.assert_invalid(dict(self.payload, **{name: value}))

    def test_invalid_model_fields(self):
        for fields, cause in (({'id': ''}, ValueError), ({'title': None}, ValueError),
                              ({'unexpected': 1}, TypeError)):
            payload = deepcopy(self.payload)
            payload['goals'][0].update(fields)
            self.assert_invalid(payload, cause)
        del self.payload['goals'][0]['id']
        self.assert_invalid(self.payload, TypeError)

    def test_duplicate_ids(self):
        for name in ('goals', 'milestones'):
            with self.subTest(name=name):
                payload = deepcopy(self.payload)
                payload[name].append(payload[name][0])
                self.assert_invalid(payload)

    def test_strict_utf8_json(self):
        self.write({})
        for data in (b'\xff', json.dumps(self.payload).encode('utf-16'),
                     b'{"goals": [], "goals": [], "milestones": [], "task_links": []}',
                     b'{"goals": NaN, "milestones": [], "task_links": []}'):
            with self.subTest(data=data):
                self.path.write_bytes(data)
                with self.assertRaises(GoalStoreError) as caught:
                    load_goal_registry(self.path)
                self.assertIsInstance(caught.exception.__cause__, ValueError)

    def test_missing_file(self):
        with self.assertRaises(GoalStoreError) as caught:
            load_goal_registry(self.path)
        self.assertIsInstance(caught.exception.__cause__, FileNotFoundError)
        self.assertFalse(self.path.exists())

    def test_atomic_replacement(self):
        save_goal_registry(GoalRegistry(), self.path)
        previous = self.path.read_bytes()
        replace = os.replace

        def inspect(source, destination):
            self.assertEqual(Path(source).parent, self.path.parent)
            self.assertEqual(self.path.read_bytes(), previous)
            self.assertEqual(load_goal_registry(source), self.registry)
            replace(source, destination)

        with patch('goal_store.os.replace', side_effect=inspect) as mocked:
            save_goal_registry(self.registry, self.path)
        mocked.assert_called_once()
        self.assertEqual(load_goal_registry(self.path), self.registry)
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])

    def test_failed_save_preserves_existing_file(self):
        save_goal_registry(self.registry, self.path)
        previous = self.path.read_bytes()
        for target in ('goal_store.os.replace', 'goal_store.os.fsync'):
            with self.subTest(target=target):
                failure = OSError('Simulated failure')
                with patch(target, side_effect=failure):
                    with self.assertRaises(GoalStoreError) as caught:
                        save_goal_registry(GoalRegistry(), self.path)
                self.assertIs(caught.exception.__cause__, failure)
                self.assertEqual(self.path.read_bytes(), previous)
                self.assertEqual(list(self.path.parent.iterdir()), [self.path])

    def test_no_mutation(self):
        before = deepcopy(self.registry)
        collections = (self.registry.goals, self.registry.milestones, self.registry.task_links)
        save_goal_registry(self.registry, self.path)
        load_goal_registry(self.path)
        self.assertEqual(self.registry, before)
        for name, original in zip(('goals', 'milestones', 'task_links'), collections):
            self.assertIs(getattr(self.registry, name), original)

    def test_cli_counts_only(self):
        default = Path(self.directory.name) / 'ghost/integrations/state/goals.json'
        save_goal_registry(self.registry, default)
        result = subprocess.run([sys.executable, '-m', 'goal_store'],
                                cwd=Path(__file__).parent,
                                env=dict(os.environ, HOME=self.directory.name),
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, 'Goals: 1\nMilestones: 2\nTask links: 1\n')
        self.assertEqual(result.stderr, '')

    def test_cli_error_hides_content(self):
        default = Path(self.directory.name) / 'ghost/integrations/state/goals.json'
        default.parent.mkdir(parents=True)
        self.payload['milestones'][0]['goal_id'] = 'private reference'
        default.write_text(json.dumps(self.payload), encoding='utf-8')
        result = subprocess.run([sys.executable, '-m', 'goal_store'],
                                cwd=Path(__file__).parent,
                                env=dict(os.environ, HOME=self.directory.name),
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, '')
        self.assertEqual(result.stderr, 'Could not load goal registry\n')


if __name__ == '__main__':
    unittest.main()
