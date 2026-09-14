from dataclasses import FrozenInstanceError, replace
from datetime import datetime
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from integrations.task_state import (GhostTask, GhostTaskState, SnapshotProvenance,
                                     SourceIdentity, TaskStatus)
from integrations.todoist.completion_events import TaskCompletionEvent
from integrations.todoist.goals import Goal, GoalRegistry, Milestone, Status, TaskMilestoneLink, with_goal_status, with_milestone_status
from integrations.todoist.progress import (ProjectionError, project_progress,
                                           project_milestone_progress)

PROVENANCE = SnapshotProvenance('todoist', 'in-memory', datetime.fromisoformat('2026-09-13T00:00:00Z'))


def task(id='a', status=TaskStatus.ACTIVE, source='todoist'):
    return GhostTask(SourceIdentity(source, id), id, None, None, None, None, (),
                     None, status, None, None, None, None, PROVENANCE, {})


def state(*tasks):
    return GhostTaskState(tasks, (), (), PROVENANCE)


def event(id='a', stamp='2026-09-12T12:00:00Z', source='todoist'):
    return TaskCompletionEvent(SourceIdentity(source, id), datetime.fromisoformat(stamp),
                               id, None, None, None, source, {'evidence': [1]})


def registry(*ids):
    return GoalRegistry([Goal('g', 'Goal')], [Milestone('m', 'g', 'Milestone')],
                        [TaskMilestoneLink('todoist', id, 'm') for id in ids])


class ProgressTests(unittest.TestCase):
    def project(self, tasks=(), events=(), ids=('a',)):
        return project_milestone_progress(registry(*ids), state(*tasks), events, 'm')

    def test_explicit_status_is_independent_of_all_evidence(self):
        for goal_status in Status:
            for milestone_status in Status:
                r = with_goal_status(registry('a'), 'g', goal_status)
                r = with_milestone_status(r, 'm', milestone_status)
                for current in (state(), state(task()), state(task(status=TaskStatus.COMPLETED))):
                    for events in ((), (event(),)):
                        p = project_progress(r, current, events)
                        self.assertIs(p.goals[0].status, goal_status)
                        self.assertIs(p.milestones[0].status, milestone_status)
                        self.assertIs(project_milestone_progress(r, current, events, 'm').status,
                                      milestone_status)

    def test_complete_milestone_keeps_active_tasks_and_history(self):
        r = with_goal_status(registry('a', 'b'), 'g', Status.ACTIVE)
        r = with_milestone_status(r, 'm', Status.ACTIVE)
        current = state(task('a'), task('b'))
        events = [event('a', f'2026-09-{day:02}T12:00:00Z') for day in (1, 3, 5, 8, 12)]
        before = project_progress(r, current, events)
        updated = with_milestone_status(r, 'm', Status.COMPLETE)
        after = project_progress(updated, current, events)
        self.assertEqual(after.milestones[0], replace(before.milestones[0], status=Status.COMPLETE))
        self.assertEqual(after.goals, before.goals)
        self.assertEqual(len(after.milestones[0].active_task_ids), 2)
        self.assertEqual(after.milestones[0].completion_event_count, 5)
        self.assertIs(r.milestones[0].status, Status.ACTIVE)

    def test_active_actual_models(self):
        result = project_progress(registry('a'), state(task()), ())
        m, g = result.milestones[0], result.goals[0]
        self.assertEqual(m.active_task_ids, (SourceIdentity('todoist', 'a'),))
        self.assertEqual(g.linked_task_ids, m.linked_task_ids)
        self.assertEqual(g.milestone_ids, ('m',))
        self.assertEqual(m.completed_task_ids, ())
        self.assertEqual(m.missing_task_ids, ())
        self.assertEqual(m.completion_event_count, 0)
        self.assertIsNone(m.last_completed_at)

    def test_currently_completed_without_history(self):
        m = self.project([task(status=TaskStatus.COMPLETED)])
        self.assertEqual(m.completed_task_ids, m.linked_task_ids)
        self.assertEqual(m.active_task_ids, ())
        self.assertEqual(m.completion_event_count, 0)

    def test_missing_with_history(self):
        m = self.project(events=[event()])
        self.assertEqual(m.missing_task_ids, m.linked_task_ids)
        self.assertEqual(m.completed_task_ids, ())
        self.assertEqual(m.active_task_ids, ())
        self.assertEqual(m.completion_event_count, 1)

    def test_reopened_active_with_history(self):
        m = self.project([task()], [event()])
        self.assertEqual(m.active_task_ids, m.linked_task_ids)
        self.assertEqual(m.completed_task_ids, ())
        self.assertEqual(m.completion_event_count, 1)

    def test_recurring_occurrences(self):
        events = [event(stamp=f'2026-09-{day:02}T12:00:00Z') for day in (12, 1, 5)]
        m = self.project([task()], events)
        self.assertEqual(m.completion_event_count, 3)
        self.assertEqual(m.last_completed_at, events[0].completed_at)

    def test_multiple_tasks_and_unlinked_evidence(self):
        m = self.project([task('b', TaskStatus.COMPLETED), task('a')],
                         [event('a'), event('b'), event('unlinked')], ('c', 'b', 'a'))
        self.assertEqual(m.linked_task_ids, tuple(SourceIdentity('todoist', x) for x in ('c', 'b', 'a')))
        self.assertEqual(m.missing_task_ids, (SourceIdentity('todoist', 'c'),))
        self.assertEqual(m.completed_task_ids, (SourceIdentity('todoist', 'b'),))
        self.assertEqual(m.active_task_ids, (SourceIdentity('todoist', 'a'),))
        self.assertEqual(m.completion_event_count, 2)

    def test_multiple_goals_milestones_and_order(self):
        r = GoalRegistry([Goal('z', ''), Goal('a', '')],
                         [Milestone('m2', 'z', ''), Milestone('m3', 'a', ''), Milestone('m1', 'z', '')],
                         [TaskMilestoneLink('todoist', 'a', 'm1'),
                          TaskMilestoneLink('todoist', 'c', 'm3'),
                          TaskMilestoneLink('todoist', 'b', 'm2')])
        events = [event('a', '2026-09-01T00:00:00Z'), event('b'), event('c')]
        p = project_progress(r, state(task('a'), task('b', TaskStatus.COMPLETED)), iter(events))
        self.assertEqual(tuple(m.milestone_id for m in p.milestones), ('m2', 'm3', 'm1'))
        self.assertEqual(tuple(g.goal_id for g in p.goals), ('z', 'a'))
        g = p.goals[0]
        self.assertEqual(g.milestone_ids, ('m2', 'm1'))
        self.assertEqual(g.linked_task_ids, (SourceIdentity('todoist', 'b'), SourceIdentity('todoist', 'a')))
        self.assertEqual(g.active_task_ids, (SourceIdentity('todoist', 'a'),))
        self.assertEqual(g.completed_task_ids, (SourceIdentity('todoist', 'b'),))
        self.assertEqual(g.completion_event_count, 2)
        self.assertEqual(g.last_completed_at, events[1].completed_at)
        self.assertEqual(p.goals[1].missing_task_ids, (SourceIdentity('todoist', 'c'),))
        self.assertEqual(p.goals[1].completion_event_count, 1)
        for m in p.milestones:
            self.assertEqual(m, project_milestone_progress(r, state(task('a'), task('b', TaskStatus.COMPLETED)), events, m.milestone_id))

    def test_provider_collision_does_not_match(self):
        m = self.project([task('123', source='other')], [event('123', source='other')], ('123',))
        self.assertEqual(m.missing_task_ids, (SourceIdentity('todoist', '123'),))
        self.assertEqual(m.completion_event_count, 0)

    def test_provider_collision_both_linked_retained(self):
        r = GoalRegistry([Goal('g', '')], [Milestone('m', 'g', '')],
                         [TaskMilestoneLink(source, '123', 'm') for source in ('todoist', 'other')])
        p = project_progress(r, state(task('123'), task('123', TaskStatus.COMPLETED, 'other')),
                             [event('123'), event('123', source='other'), event('123', source='other')])
        g = p.goals[0]
        self.assertEqual(len(g.linked_task_ids), 2)
        self.assertEqual(g.active_task_ids, (SourceIdentity('todoist', '123'),))
        self.assertEqual(g.completed_task_ids, (SourceIdentity('other', '123'),))
        self.assertEqual(g.completion_event_count, 3)

    def test_unknown_milestone(self):
        with self.assertRaisesRegex(ProjectionError, 'Unknown milestone'):
            project_milestone_progress(registry(), state(), (), 'unknown')

    def test_zero_links(self):
        m = self.project(ids=())
        for name in ('linked_task_ids', 'active_task_ids', 'completed_task_ids', 'missing_task_ids'):
            self.assertEqual(getattr(m, name), ())
        self.assertEqual(m.completion_event_count, 0)
        self.assertIsNone(m.last_completed_at)

    def test_zero_milestones_and_empty_registry(self):
        p = project_progress(GoalRegistry([Goal('g', '')]), state(), ())
        self.assertEqual(p.milestones, ())
        self.assertEqual(p.goals[0].milestone_ids, ())
        self.assertEqual(p.goals[0].linked_task_ids, ())
        self.assertEqual(p.goals[0].completion_event_count, 0)
        self.assertIsNone(p.goals[0].last_completed_at)
        self.assertEqual(project_progress(GoalRegistry(), state(), ()).goals, ())

    def test_timezone_max(self):
        events = [event(stamp='2026-09-12T23:00:00+05:00'),
                  event(stamp='2026-09-12T20:00:00+00:00')]
        p = project_progress(registry('a'), state(), events)
        self.assertEqual(p.milestones[0].last_completed_at, events[1].completed_at)
        self.assertEqual(p.goals[0].last_completed_at, events[1].completed_at)

    def test_determinism_immutability_purity_and_no_input_mutation(self):
        r, s, events = registry('a'), state(task()), [event()]
        before = repr((r, s, events))
        with patch('builtins.open', side_effect=AssertionError('File IO')), \
             patch('pathlib.Path.open', side_effect=AssertionError('File IO')), \
             patch('socket.socket', side_effect=AssertionError('Network')), \
             patch('sqlite3.connect', side_effect=AssertionError('SQLite')):
            p = project_progress(r, s, events)
            self.assertEqual(p, project_progress(r, s, reversed(events)))
        self.assertEqual(before, repr((r, s, events)))
        for obj, field in ((p, 'goals'), (p.goals[0], 'linked_task_ids'),
                           (p.milestones[0], 'active_task_ids')):
            with self.assertRaises(FrozenInstanceError):
                setattr(obj, field, ())
        with self.assertRaises(TypeError):
            p.milestones[0].linked_task_ids[0] = SourceIdentity('other', 'x')
        with self.assertRaises(FrozenInstanceError):
            p.milestones[0].linked_task_ids[0].source = 'other'

    def test_ambiguous_or_unsupported_current_state(self):
        for s in (state(task(), task()), state(replace(task(), status='unknown'))):
            with self.assertRaises(ProjectionError):
                project_progress(registry('a'), s, ())


if __name__ == '__main__':
    unittest.main()
