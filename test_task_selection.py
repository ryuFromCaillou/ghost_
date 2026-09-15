from dataclasses import FrozenInstanceError, replace
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from integrations.task_state import TaskStatus
from integrations.todoist.task_selection import select_task, TaskSelectionError
from integrations.todoist.progress import project_progress
from test_brief import fixture
from test_progress import state, task, event


class TaskSelectionTests(unittest.TestCase):
    def setUp(self):
        self.registry, self.current = fixture()

    def select(self, current=None, registry=None):
        return select_task(self.registry if registry is None else registry,
                           self.current if current is None else current, 'manuscript')

    def test_first_linked_active_wins_multiple_eligible(self):
        result = self.select()
        self.assertEqual(result.primary.source_id, 'write_methods')
        self.assertEqual(tuple(t.source_id for t in result.secondary), ('incorporate_diagnostics',))
        self.assertEqual(result.reason, 'first_active_task_in_registry_link_order')

    def test_link_order_is_priority_not_snapshot_order(self):
        registry = replace(self.registry, task_links=tuple(reversed(self.registry.task_links)))
        result = self.select(registry=registry)
        self.assertEqual(result.primary.source_id, 'incorporate_diagnostics')
        self.assertEqual(tuple(t.source_id for t in result.secondary), ('write_methods',))
        self.assertEqual(result, self.select(replace(self.current, tasks=tuple(reversed(self.current.tasks))), registry))

    def test_completed_first_skipped(self):
        result = self.select(state(task('write_methods', TaskStatus.COMPLETED), task('incorporate_diagnostics')))
        self.assertEqual(result.primary.source_id, 'incorporate_diagnostics')
        self.assertEqual(result.secondary, ())

    def test_missing_first_skipped_one_eligible(self):
        result = self.select(state(task('incorporate_diagnostics')))
        self.assertEqual(result.primary.source_id, 'incorporate_diagnostics')
        self.assertEqual(result.secondary, ())

    def test_all_non_active_statuses_skipped(self):
        # The current enum has only ACTIVE/COMPLETED; guard future statuses too.
        for status in ('inactive', 'blocked', None, TaskStatus.COMPLETED):
            with self.subTest(status=status):
                result = self.select(state(task('write_methods', status), task('incorporate_diagnostics')))
                self.assertEqual(result.primary.source_id, 'incorporate_diagnostics')

    def test_zero_eligible_does_not_promote_other_milestone(self):
        for current in (state(), state(task('send_offer')), state(task('write_methods', TaskStatus.COMPLETED))):
            result = self.select(current)
            self.assertIsNone(result.primary)
            self.assertEqual(result.secondary, ())
            self.assertEqual(result.reason, 'no_eligible_active_linked_task')

    def test_history_does_not_affect_eligibility(self):
        for current in (self.current, state(task('send_offer')), state(task('write_methods', TaskStatus.COMPLETED))):
            before = self.select(current)
            for events in ((), (event('write_methods'),), (event('write_methods'), event('incorporate_diagnostics'))):
                project_progress(self.registry, current, events)
                self.assertEqual(self.select(current), before)
            self.assertEqual(before.primary is not None, current is self.current)

    def test_provider_identity_is_respected(self):
        result = self.select(state(task('write_methods', source='other'), task('incorporate_diagnostics')))
        self.assertEqual(result.primary.source_id, 'incorporate_diagnostics')

    def test_no_milestone_selected(self):
        result = select_task(self.registry, self.current, None)
        self.assertEqual((result.primary, result.secondary, result.reason), (None, (), 'no_selected_milestone'))

    def test_unknown_milestone_and_duplicate_identity_rejected(self):
        with self.assertRaises(TaskSelectionError):
            select_task(self.registry, self.current, 'unknown')
        with self.assertRaises(TaskSelectionError):
            self.select(state(task('write_methods'), task('write_methods')))

    def test_deterministic_pure_no_mutation(self):
        before = repr((self.registry, self.current))
        with patch('builtins.open', side_effect=AssertionError('IO')), \
             patch('pathlib.Path.open', side_effect=AssertionError('IO')), \
             patch('socket.socket', side_effect=AssertionError('Network')), \
             patch('sqlite3.connect', side_effect=AssertionError('Database')):
            first = self.select()
            for _ in range(10):
                self.assertEqual(self.select(), first)
        self.assertEqual(before, repr((self.registry, self.current)))
        self.assertIs(first.primary, self.current.tasks[2])
        with self.assertRaises(FrozenInstanceError):
            first.primary = None


if __name__ == '__main__':
    unittest.main()
