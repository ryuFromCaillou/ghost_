from dataclasses import replace
from datetime import datetime, timezone
from itertools import permutations
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from integrations.task_state import GhostTask, GhostTaskState, Project, Section, SourceIdentity, SnapshotProvenance, TaskStatus
from integrations.todoist.task_selection import select_task, TaskSelectionError

PROVENANCE = SnapshotProvenance('todoist', 'fixture', datetime(2026, 9, 1, tzinfo=timezone.utc))
PROJECT = Project(SourceIdentity('todoist', 'p'), 'Project')


def task(ident='a', parent=None, completed=False, **raw):
    return GhostTask(SourceIdentity('todoist', ident), ident, None, PROJECT, None,
                     SourceIdentity('todoist', parent) if parent else None, (), None,
                     TaskStatus.COMPLETED if completed else TaskStatus.ACTIVE,
                     None, None, None, None, PROVENANCE, raw)


def state(*tasks):
    return GhostTaskState(tuple(tasks), (PROJECT,), (), PROVENANCE)


def ids(selection):
    return [t.source_id for t in (() if selection.primary is None else (selection.primary,)) + selection.next]


class TaskSelectionTests(unittest.TestCase):
    def test_top_level_active(self):
        s = select_task(state(task()))
        self.assertEqual(ids(s), ['a'])
        self.assertEqual(s.ancestors, ())

    def test_parent_with_active_child(self):
        s = select_task(state(task('a'), task('b', 'a')))
        self.assertEqual(ids(s), ['b'])
        self.assertEqual([t.source_id for t in s.ancestors], ['a'])

    def test_nested_and_multiple_roots_depth_first(self):
        s = select_task(state(task('r', child_order=1), task('p', 'r'), task('leaf', 'p'),
                              task('second', child_order=2)))
        self.assertEqual(ids(s), ['leaf', 'second'])
        self.assertEqual([t.source_id for t in s.ancestors], ['r', 'p'])

    def test_completed_child_makes_active_parent_actionable(self):
        self.assertEqual(ids(select_task(state(task('a'), task('b', 'a', True)))), ['a'])

    def test_completed_leaf_not_actionable(self):
        self.assertEqual(ids(select_task(state(task(completed=True)))), [])

    def test_completed_parent_does_not_hide_active_child(self):
        self.assertEqual(ids(select_task(state(task('a', completed=True), task('b', 'a')))), ['b'])

    def test_direct_child_rule_is_not_transitive_dependency(self):
        s = select_task(state(task('a'), task('b', 'a', True), task('c', 'b')))
        self.assertEqual(ids(s), ['a', 'c'])

    def test_empty(self):
        self.assertEqual(ids(select_task(state())), [])

    def test_missing_parent_rejected(self):
        with self.assertRaises(TaskSelectionError):
            select_task(state(task(parent='missing')))

    def test_cycles_and_self_parent_rejected(self):
        for tasks in ((task('a', 'a'),), (task('a', 'b'), task('b', 'a'))):
            with self.subTest(tasks=tasks), self.assertRaises(TaskSelectionError):
                select_task(state(*tasks))

    def test_duplicate_identity_rejected(self):
        with self.assertRaises(TaskSelectionError):
            select_task(state(task(), task()))

    def test_cross_project_parent_rejected(self):
        with self.assertRaises(TaskSelectionError):
            select_task(state(task('a'), replace(task('b', 'a'), project=None)))

    def test_fractional_order_wins_over_legacy(self):
        tasks = (task('a', order_key='a2', child_order=1), task('z', order_key='a1', child_order=2))
        self.assertEqual(ids(select_task(state(*tasks))), ['z', 'a'])

    def test_partial_fractional_group_uses_legacy(self):
        self.assertEqual(ids(select_task(state(task('a', order_key='a1', child_order=2),
                                              task('z', child_order=1)))), ['z', 'a'])

    def test_missing_legacy_sorts_last(self):
        self.assertEqual(ids(select_task(state(task('a'), task('z', child_order=0)))), ['z', 'a'])

    def test_snapshot_permutations_and_ties(self):
        tasks = (task('z', child_order=1), task('b', child_order=1), task('a', child_order=2))
        for order in permutations(tasks):
            self.assertEqual(ids(select_task(state(*order))), ['b', 'z', 'a'])

    def test_identity_includes_provider(self):
        a = task()
        b = replace(a, id=SourceIdentity('another', 'a'))
        self.assertEqual(select_task(state(a, b)).primary.id, b.id)

    def test_invalid_ordering_fails(self):
        for raw in ({'child_order': True}, {'child_order': '1'}, {'order_key': 1}, {'order_key': ''}):
            with self.subTest(raw=raw), self.assertRaises(TaskSelectionError):
                select_task(state(task(**raw)))

    def test_project_and_section_provider_order(self):
        q = Project(SourceIdentity('todoist', 'q'), 'Q')
        s1 = Section(SourceIdentity('todoist', 's1'), 'S1', PROJECT.id)
        s2 = Section(SourceIdentity('todoist', 's2'), 'S2', PROJECT.id)
        current = state(replace(task('a'), section=s1), replace(task('b'), section=s2),
                        task('unsectioned'), replace(task('q'), project=q))
        result = select_task(current, projects=({'id':'p', 'child_order':2}, {'id':'q', 'child_order':1}),
                             sections=({'id':'s1', 'section_order':2}, {'id':'s2', 'section_order':1}))
        self.assertEqual(ids(result), ['q', 'unsectioned', 'b', 'a'])

    def test_fractional_project_section_order(self):
        q = Project(SourceIdentity('todoist', 'q'), 'Q')
        s1 = Section(SourceIdentity('todoist', 's1'), 'S1', PROJECT.id)
        s2 = Section(SourceIdentity('todoist', 's2'), 'S2', PROJECT.id)
        current = state(replace(task('a'), section=s1), replace(task('b'), section=s2),
                        replace(task('q'), project=q))
        result = select_task(current, projects=({'id':'p', 'order_key':'a1'}, {'id':'q', 'order_key':'a2'}),
                             sections=({'id':'s1', 'order_key':'a2'}, {'id':'s2', 'order_key':'a1'}))
        self.assertEqual(ids(result), ['b', 'a', 'q'])

    def test_provider_rows_and_tasks_can_be_reordered(self):
        q = Project(SourceIdentity('todoist', 'q'), 'Q')
        s1 = Section(SourceIdentity('todoist', 's1'), 'S1', PROJECT.id)
        s2 = Section(SourceIdentity('todoist', 's2'), 'S2', PROJECT.id)
        tasks = (replace(task('a'), section=s1), replace(task('b'), section=s2),
                 replace(task('q'), project=q))
        projects = ({'id':'p', 'child_order':2}, {'id':'q', 'child_order':1})
        sections = ({'id':'s1', 'section_order':2}, {'id':'s2', 'section_order':1})
        for ordering in permutations(tasks):
            for project_rows in permutations(projects):
                for section_rows in permutations(sections):
                    self.assertEqual(ids(select_task(state(*ordering), projects=project_rows,
                                                     sections=section_rows)), ['q', 'b', 'a'])

    def test_deep_tree_iterative(self):
        tasks = [task(str(i), str(i-1) if i else None) for i in range(1500)]
        result = select_task(state(*tasks))
        self.assertEqual(ids(result), ['1499'])
        self.assertEqual(len(result.ancestors), 1499)

    def test_selection_does_not_mutate_state(self):
        current = state(task('a'), task('b', 'a'))
        before = repr(current)
        self.assertEqual(select_task(current), select_task(current))
        self.assertEqual(repr(current), before)
