from dataclasses import FrozenInstanceError, replace
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from integrations.todoist.goals import Goal, GoalRegistry, Milestone, Status, TaskMilestoneLink
from integrations.todoist.progress import project_progress
from integrations.todoist.objective import (ObjectiveSelectionError, SELECTED_REASONS,
                                           select_objective)
from integrations.task_state import SourceIdentity, TaskStatus
from test_progress import task, state, event


def single(goal_status=Status.ACTIVE, milestone_status=Status.ACTIVE, links=('a',)):
    return GoalRegistry([Goal('g', 'Goal', status=goal_status)],
                        [Milestone('m', 'g', 'Milestone', status=milestone_status)],
                        [TaskMilestoneLink('todoist', id, 'm') for id in links])


def priority_registry():
    return GoalRegistry(
        [Goal('thesis', 'Thesis', status=Status.ACTIVE),
         Goal('revenue', 'Revenue', status=Status.ACTIVE)],
        [Milestone('portfolio_offer', 'revenue', 'Portfolio offer', status=Status.ACTIVE),
         Milestone('experimental_validation', 'thesis', 'Experimental validation', status=Status.BLOCKED),
         Milestone('manuscript', 'thesis', 'Manuscript', status=Status.ACTIVE)],
        [TaskMilestoneLink('todoist', 'offer', 'portfolio_offer'),
         TaskMilestoneLink('todoist', 'write', 'manuscript')])


class ObjectiveTests(unittest.TestCase):
    def select(self, registry=None, current=None, events=()):
        registry = registry if registry is not None else single()
        current = current if current is not None else state(task())
        return select_objective(registry, project_progress(registry, current, events))

    def test_active_selected(self):
        result = self.select()
        self.assertEqual(result.objective.goal_id, 'g')
        self.assertEqual(result.objective.milestone_id, 'm')
        self.assertEqual(result.objective.task_ids, (SourceIdentity('todoist', 'a'),))
        self.assertEqual(result.objective.reason_codes, SELECTED_REASONS)
        self.assertEqual(result.objective.summary, 'Advance milestone: Milestone')
        self.assertEqual(result.considered_milestone_ids, ('m',))
        self.assertEqual(result.excluded, ())

    def test_inactive_milestones_with_active_tasks(self):
        for status, reason in ((Status.PLANNED, 'milestone_not_active'),
                               (Status.BLOCKED, 'milestone_blocked'),
                               (Status.COMPLETE, 'milestone_complete')):
            with self.subTest(status=status):
                result = self.select(single(milestone_status=status))
                self.assertIsNone(result.objective)
                self.assertEqual(result.excluded[0].reason_code, reason)

    def test_inactive_goals_exclude_all_children(self):
        for status in (Status.PLANNED, Status.BLOCKED, Status.COMPLETE):
            r = single(goal_status=status)
            r = replace(r, milestones=(*r.milestones, Milestone('m2', 'g', '', status=Status.BLOCKED)))
            result = self.select(r)
            self.assertIsNone(result.objective)
            self.assertEqual(tuple(x.reason_code for x in result.excluded), ('goal_not_active',) * 2)
            self.assertEqual(result.considered_milestone_ids, ('m', 'm2'))

    def test_no_actionable_tasks(self):
        cases = [(single(links=()), state(), ()),
                 (single(), state(task(status=TaskStatus.COMPLETED)), ()),
                 (single(), state(), ()),
                 (single(), state(), (event(),)),
                 (single(), state(task(status=TaskStatus.COMPLETED)), (event(),))]
        for r, current, events in cases:
            with self.subTest(current=current, events=events):
                result = self.select(r, current, events)
                self.assertIsNone(result.objective)
                self.assertEqual(result.excluded[0].reason_code, 'no_actionable_tasks')

    def test_empty_registry_and_goal_without_milestones(self):
        for r in (GoalRegistry(), GoalRegistry([Goal('g', '', status=Status.ACTIVE)])):
            result = self.select(r, state())
            self.assertIsNone(result.objective)
            self.assertEqual(result.considered_milestone_ids, ())
            self.assertEqual(result.excluded, ())

    def test_goal_priority_precedes_global_milestone_order_and_skips_blocked(self):
        r = priority_registry()
        current = state(task('offer'), task('write'))
        result = self.select(r, current)
        self.assertEqual(result.objective.milestone_id, 'manuscript')
        self.assertEqual(result.considered_milestone_ids,
                         ('experimental_validation', 'manuscript', 'portfolio_offer'))
        self.assertEqual(tuple((x.milestone_id, x.reason_code) for x in result.excluded),
                         (('experimental_validation', 'milestone_blocked'),))
        reordered = replace(r, goals=tuple(reversed(r.goals)))
        # Existing projection remains structurally valid: registry determines priority.
        result = select_objective(reordered, project_progress(r, current, ()))
        self.assertEqual((result.objective.goal_id, result.objective.milestone_id),
                         ('revenue', 'portfolio_offer'))

    def test_multiple_active_milestones_registry_order(self):
        r = single()
        r = replace(r, milestones=(Milestone('z', 'g', 'Last alphabetically', status=Status.ACTIVE), *r.milestones),
                    task_links=(*r.task_links, TaskMilestoneLink('todoist', 'b', 'z')))
        self.assertEqual(self.select(r, state(task('a'), task('b'))).objective.milestone_id, 'z')

    def test_first_goal_without_eligible_work_is_skipped(self):
        r = priority_registry()
        result = self.select(r, state(task('offer')))
        self.assertEqual(result.objective.goal_id, 'revenue')
        self.assertEqual(tuple(x.reason_code for x in result.excluded),
                         ('milestone_blocked', 'no_actionable_tasks'))

    def test_task_link_order_provider_identity_and_active_only(self):
        r = single(links=())
        r = replace(r, task_links=tuple(TaskMilestoneLink(source, id, 'm') for source, id in
                    [('other', '123'), ('todoist', 'done'), ('todoist', '123'), ('todoist', 'missing')]))
        current = state(task('123'), task('done', TaskStatus.COMPLETED), task('123', source='other'))
        result = self.select(r, current, (event('missing'),))
        self.assertEqual(result.objective.task_ids,
                         (SourceIdentity('other', '123'), SourceIdentity('todoist', '123')))

    def test_full_in_memory_thesis_chain(self):
        r = GoalRegistry([Goal('thesis', 'Thesis', status=Status.ACTIVE)],
                         [Milestone('experimental_validation', 'thesis', 'Experimental validation', status=Status.ACTIVE),
                          Milestone('manuscript', 'thesis', 'Manuscript')],
                         [TaskMilestoneLink('todoist', 'interpret_phase_21', 'experimental_validation')])
        result = self.select(r, state(task('interpret_phase_21')))
        self.assertEqual(result.objective.goal_id, 'thesis')
        self.assertEqual(result.objective.milestone_id, 'experimental_validation')
        self.assertEqual(result.objective.task_ids, (SourceIdentity('todoist', 'interpret_phase_21'),))

    def test_titles_counts_and_last_completion_do_not_rank(self):
        r = priority_registry()
        current = state(task('write'), task('offer'))
        baseline = self.select(r, current).objective
        for title in ('!!! URGENT', 'zzz', ''):
            renamed = replace(r, goals=tuple(replace(g, title=title) for g in r.goals),
                              milestones=tuple(replace(m, title=title) for m in r.milestones))
            for evidence in ((event('offer'),), (event('write'),),
                             tuple(event('offer', f'2026-09-{day:02}T12:00:00Z') for day in (1, 5, 12))):
                selected = self.select(renamed, current, evidence).objective
                self.assertEqual((selected.goal_id, selected.milestone_id, selected.task_ids),
                                 (baseline.goal_id, baseline.milestone_id, baseline.task_ids))
                self.assertEqual(selected.summary, f'Advance milestone: {title}')

    def test_determinism_purity_immutability_and_no_mutation(self):
        r = priority_registry()
        p = project_progress(r, state(task('write'), task('offer')), (event('offer'),))
        before = repr((r, p))
        with patch('builtins.open', side_effect=AssertionError('File IO')), \
             patch('pathlib.Path.open', side_effect=AssertionError('File IO')), \
             patch('socket.socket', side_effect=AssertionError('Network')), \
             patch('sqlite3.connect', side_effect=AssertionError('SQLite')), \
             patch('os.getenv', side_effect=AssertionError('Environment')), \
             patch('os.environ', {}):
            result = select_objective(r, p)
            self.assertEqual(result, select_objective(r, p))
        self.assertEqual(repr((r, p)), before)
        for obj, field in ((result, 'objective'), (result.objective, 'summary'),
                           (result.excluded[0], 'reason_code'), (result.objective.task_ids[0], 'source')):
            with self.assertRaises(FrozenInstanceError):
                setattr(obj, field, 'changed')
        with self.assertRaises(TypeError):
            result.objective.task_ids[0] = SourceIdentity('other', 'a')

    def test_structural_mismatches_fail(self):
        r = single()
        p = project_progress(r, state(task()), ())
        m, g = p.milestones[0], p.goals[0]
        invalid = [replace(p, milestones=()), replace(p, goals=()),
                   replace(p, milestones=(m, m)), replace(p, goals=(g, g)),
                   replace(p, milestones=(replace(m, milestone_id='unknown'),)),
                   replace(p, goals=(replace(g, goal_id='unknown'),)),
                   replace(p, milestones=(replace(m, goal_id='unknown'),)),
                   replace(p, milestones=(replace(m, status=Status.COMPLETE),)),
                   replace(p, goals=(replace(g, status=Status.BLOCKED),)),
                   replace(p, goals=(replace(g, milestone_ids=()),)),
                   replace(p, goals=(replace(g, active_task_ids=()),)),
                   replace(p, milestones=(replace(m, linked_task_ids=()),)),
                   replace(p, milestones=(replace(m, active_task_ids=()),)),
                   replace(p, milestones=(replace(m, active_task_ids=(SourceIdentity('other', 'a'),)),)),
                   replace(p, milestones=(replace(m, completed_task_ids=m.active_task_ids),)),
                   replace(p, milestones=(replace(m, active_task_ids=m.active_task_ids * 2),))]
        for projection in invalid:
            with self.subTest(projection=projection):
                with self.assertRaises(ObjectiveSelectionError):
                    select_objective(r, projection)

    def test_mismatch_after_eligible_winner_still_fails(self):
        r = priority_registry()
        p = project_progress(r, state(task('write'), task('offer')), ())
        bad = replace(p, milestones=tuple(replace(m, goal_id='thesis')
                      if m.milestone_id == 'portfolio_offer' else m for m in p.milestones))
        with self.assertRaises(ObjectiveSelectionError):
            select_objective(r, bad)


if __name__ == '__main__':
    unittest.main()
