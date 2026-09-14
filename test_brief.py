from dataclasses import FrozenInstanceError, replace
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from integrations.task_state import SourceIdentity, TaskStatus
from integrations.todoist.goals import Goal, Milestone, GoalRegistry, Status, TaskMilestoneLink
from integrations.todoist.progress import project_progress
from integrations.todoist.objective import select_objective
from integrations.todoist.brief import BriefError, build_brief, render_brief
from test_progress import task, state, event


def fixture():
    registry = GoalRegistry(
        [Goal('thesis', 'Thesis', status=Status.ACTIVE),
         Goal('revenue', 'Revenue', status=Status.ACTIVE)],
        [Milestone('experimental_validation', 'thesis', 'Experimental validation', status=Status.BLOCKED),
         Milestone('manuscript', 'thesis', 'Manuscript', status=Status.ACTIVE),
         Milestone('portfolio_offer', 'revenue', 'Portfolio offer', status=Status.ACTIVE)],
        [TaskMilestoneLink('todoist', 'write_methods', 'manuscript'),
         TaskMilestoneLink('todoist', 'incorporate_diagnostics', 'manuscript'),
         TaskMilestoneLink('todoist', 'send_offer', 'portfolio_offer')])
    current = state(*(replace(task(id), title=title, raw={'content': 'Do not read raw text'})
                      for id, title in [('send_offer', 'Send offer'),
                                        ('incorporate_diagnostics', 'Incorporate diagnostics'),
                                        ('write_methods', 'Write methods')]))
    return registry, current


def pipeline(registry, current, events=()):
    projection = project_progress(registry, current, events)
    selection = select_objective(registry, projection)
    return build_brief(registry, current, projection, selection)


PRIMARY = '''GHOST BRIEF

PRIMARY
Thesis
Manuscript
Advance milestone: Manuscript

TASKS
- Write methods
- Incorporate diagnostics
'''
BLOCKED = '\nBLOCKED\n- Thesis — Experimental validation\n'
QUEUED = '\nQUEUED\n- Revenue — Portfolio offer\n'


class BriefTests(unittest.TestCase):
    def setUp(self):
        self.registry, self.current = fixture()
        self.projection = project_progress(self.registry, self.current, ())
        self.selection = select_objective(self.registry, self.projection)

    def build(self, **updates):
        args = dict(registry=self.registry, task_state=self.current,
                    projection=self.projection, selection=self.selection)
        args.update(updates)
        return build_brief(**args)

    def test_full_pipeline_exact_render(self):
        brief = pipeline(self.registry, self.current, (event('write_methods'),))
        self.assertEqual(render_brief(brief), PRIMARY + BLOCKED + QUEUED)
        self.assertEqual(brief.primary.goal_id, 'thesis')
        self.assertEqual(brief.primary.milestone_id, 'manuscript')
        self.assertEqual(tuple(t.source for t in brief.primary.tasks), self.selection.objective.task_ids)
        self.assertEqual(brief.message_codes, ('primary_objective_selected', 'blocked_work_present', 'queued_work_present'))

    def test_primary_only_exact_render(self):
        r = replace(self.registry, goals=self.registry.goals[:1],
                    milestones=self.registry.milestones[1:2], task_links=self.registry.task_links[:2])
        brief = pipeline(r, self.current)
        self.assertEqual(render_brief(brief), PRIMARY)
        self.assertEqual(brief.message_codes, ('primary_objective_selected',))

    def test_primary_queued_exact_render(self):
        r = replace(self.registry, milestones=self.registry.milestones[1:])
        self.assertEqual(render_brief(pipeline(r, self.current)), PRIMARY + QUEUED)

    def test_no_primary_blocked_exact_render(self):
        brief = pipeline(self.registry, state())
        self.assertEqual(render_brief(brief), 'GHOST BRIEF\n\nPRIMARY\nNone\n' + BLOCKED)
        self.assertEqual(brief.message_codes, ('no_primary_objective', 'blocked_work_present'))

    def test_empty_exact_render(self):
        brief = pipeline(GoalRegistry(), state())
        self.assertEqual(render_brief(brief), 'GHOST BRIEF\n\nPRIMARY\nNone\n\nNo actionable work.\n')
        self.assertEqual(brief.message_codes, ('no_primary_objective', 'no_actionable_work'))

    def test_reordered_goals(self):
        r = replace(self.registry, goals=tuple(reversed(self.registry.goals)))
        brief = pipeline(r, self.current)
        self.assertEqual(render_brief(brief), '''GHOST BRIEF

PRIMARY
Revenue
Portfolio offer
Advance milestone: Portfolio offer

TASKS
- Send offer

BLOCKED
- Thesis — Experimental validation

QUEUED
- Thesis — Manuscript
''')

    def test_planned_and_complete_milestones_hidden(self):
        for status in (Status.PLANNED, Status.COMPLETE):
            r = replace(self.registry, milestones=tuple(replace(m, status=status) for m in self.registry.milestones))
            brief = pipeline(r, self.current, (event('write_methods'),))
            self.assertIsNone(brief.primary)
            self.assertEqual((brief.blocked, brief.queued), ((), ()))
            self.assertEqual(brief.message_codes, ('no_primary_objective', 'no_actionable_work'))

    def test_inactive_goals_hide_all_their_work(self):
        for status in (Status.PLANNED, Status.BLOCKED, Status.COMPLETE):
            r = replace(self.registry, goals=(replace(self.registry.goals[0], status=status), self.registry.goals[1]))
            brief = pipeline(r, self.current)
            self.assertEqual(brief.primary.goal_id, 'revenue')
            self.assertEqual((brief.blocked, brief.queued), ((), ()))

    def test_no_active_tasks_or_history_only_not_queued(self):
        for current in (state(), state(*(replace(t, status=TaskStatus.COMPLETED) for t in self.current.tasks))):
            brief = pipeline(self.registry, current, (event('write_methods'), event('send_offer')))
            self.assertIsNone(brief.primary)
            self.assertEqual(brief.queued, ())
        r = replace(self.registry, milestones=(*self.registry.milestones,
                    Milestone('no_links', 'thesis', 'No links', status=Status.ACTIVE)))
        self.assertNotIn('no_links', tuple(x.milestone_id for x in pipeline(r, self.current).queued))

    def test_provider_identity_and_normalized_text(self):
        r = replace(self.registry, task_links=(TaskMilestoneLink('other', 'write_methods', 'manuscript'),
                                               *self.registry.task_links))
        current = replace(self.current, tasks=(*self.current.tasks,
                          replace(task('write_methods', source='other'), title='Other provider task')))
        brief = pipeline(r, current)
        self.assertEqual(tuple(t.content for t in brief.primary.tasks),
                         ('Other provider task', 'Write methods', 'Incorporate diagnostics'))
        self.assertEqual(brief.primary.tasks[0].source, SourceIdentity('other', 'write_methods'))
        self.assertEqual(brief.primary.tasks[1].source, SourceIdentity('todoist', 'write_methods'))
        text = render_brief(brief)
        for internal in ('SourceIdentity', 'todoist', 'write_methods', 'Do not read raw text', 'primary_objective_selected'):
            self.assertNotIn(internal, text)

    def test_preserves_supplied_task_order_and_summary(self):
        objective = replace(self.selection.objective, task_ids=tuple(reversed(self.selection.objective.task_ids)),
                            summary='Advance this supplied decision')
        brief = self.build(selection=replace(self.selection, objective=objective))
        self.assertEqual(tuple(t.source for t in brief.primary.tasks), objective.task_ids)
        self.assertEqual(brief.primary.summary, objective.summary)

    def test_does_not_reselect_or_promote_when_primary_absent(self):
        brief = self.build(selection=replace(self.selection, objective=None))
        self.assertIsNone(brief.primary)
        self.assertEqual(tuple(q.milestone_id for q in brief.queued), ('manuscript', 'portfolio_offer'))
        self.assertEqual(brief.message_codes, ('no_primary_objective', 'blocked_work_present', 'queued_work_present'))

    def test_invalid_objective_identity_and_task_set(self):
        objective = self.selection.objective
        invalid = [replace(objective, goal_id='unknown'),
                   replace(objective, milestone_id='unknown'),
                   replace(objective, goal_id='revenue'),
                   replace(objective, task_ids=(SourceIdentity('todoist', 'unknown'),)),
                   replace(objective, task_ids=(SourceIdentity('todoist', 'send_offer'),)),
                   replace(objective, task_ids=objective.task_ids[:1]),
                   replace(objective, task_ids=objective.task_ids * 2),
                   replace(objective, task_ids=()),
                   replace(objective, milestone_id='experimental_validation')]
        for obj in invalid:
            with self.subTest(obj=obj):
                with self.assertRaises(BriefError):
                    self.build(selection=replace(self.selection, objective=obj))

    def test_selected_task_missing_or_completed(self):
        selected_id = self.selection.objective.task_ids[0]
        for current in (replace(self.current, tasks=tuple(t for t in self.current.tasks if t.id != selected_id)),
                        replace(self.current, tasks=tuple(replace(t, status=TaskStatus.COMPLETED)
                                if t.id == selected_id else t for t in self.current.tasks))):
            with self.assertRaises(BriefError):
                self.build(task_state=current)
            # Even with a fresh projection, the old decision remains invalid.
            with self.assertRaises(BriefError):
                self.build(task_state=current, projection=project_progress(self.registry, current, ()))

    def test_duplicate_current_identity(self):
        with self.assertRaisesRegex(BriefError, 'Duplicate current task identity'):
            self.build(task_state=replace(self.current, tasks=(*self.current.tasks, self.current.tasks[0])))

    def test_projection_registry_disagreement(self):
        p = self.projection
        for projection in (replace(p, milestones=p.milestones[1:]),
                           replace(p, milestones=(*p.milestones, p.milestones[0])),
                           replace(p, goals=(replace(p.goals[0], status=Status.COMPLETE), *p.goals[1:])),
                           replace(p, milestones=(replace(p.milestones[0], goal_id='revenue'), *p.milestones[1:]))):
            with self.subTest(projection=projection):
                with self.assertRaises(BriefError):
                    self.build(projection=projection)

    def test_stale_queued_classification_fails(self):
        current = replace(self.current, tasks=tuple(replace(t, status=TaskStatus.COMPLETED)
                          if t.source_id == 'send_offer' else t for t in self.current.tasks))
        with self.assertRaises(BriefError):
            self.build(task_state=current)

    def test_purity_determinism_no_mutation_immutability(self):
        inputs = (self.registry, self.current, self.projection, self.selection)
        before = repr(inputs)
        with patch('builtins.open', side_effect=AssertionError('Filesystem')), \
             patch('pathlib.Path.open', side_effect=AssertionError('Filesystem')), \
             patch('socket.socket', side_effect=AssertionError('Network')), \
             patch('sqlite3.connect', side_effect=AssertionError('SQLite')), \
             patch('os.getenv', side_effect=AssertionError('Environment')), \
             patch('os.environ', {}), \
             patch('integrations.todoist.objective.select_objective', side_effect=AssertionError('Reselection')), \
             patch('integrations.todoist.progress.project_progress', side_effect=AssertionError('Reprojection')):
            first = build_brief(*inputs)
            second = build_brief(*inputs)
            self.assertEqual(first, second)
            self.assertEqual(render_brief(first), render_brief(second))
        self.assertEqual(before, repr(inputs))
        for obj, field in ((first, 'primary'), (first.primary, 'summary'),
                           (first.primary.tasks[0], 'content'), (first.blocked[0], 'goal_title'),
                           (first.queued[0], 'milestone_title')):
            with self.assertRaises(FrozenInstanceError):
                setattr(obj, field, 'changed')
        with self.assertRaises(TypeError):
            first.primary.tasks[0] = None


if __name__ == '__main__':
    unittest.main()
