import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from integrations.todoist.brief import build_brief, render_brief
from integrations.todoist.strategic_context import StrategicContext
from integrations.todoist.task_selection import select_task
from test_task_selection import task, state

CONTEXT = StrategicContext('Show my family that chasing your dreams is possible.',
                           'Become a traveler / documentarian.')
HEADER = ('GHOST BRIEF\n\nWHY\nShow my family that chasing your dreams is possible.\n\n'
          'DIRECTION\nBecome a traveler / documentarian.\n\nPRIMARY ORDER\n')


class BriefTests(unittest.TestCase):
    def render(self, *tasks):
        return render_brief(build_brief(CONTEXT, select_task(state(*tasks))))

    def test_exact_tree_context_and_next(self):
        self.assertEqual(self.render(task('Fix truck', child_order=1),
                                     task('Diagnose', 'Fix truck'),
                                     task('Check codes', 'Diagnose'),
                                     task('Other project work', child_order=2)),
                         HEADER + 'Check codes\n\nUNDER\nFix truck\nDiagnose\n\nNEXT\n- Other project work\n')

    def test_top_level_omits_under(self):
        self.assertEqual(self.render(task('Work')), HEADER + 'Work\n')

    def test_empty_frontier_keeps_context(self):
        self.assertEqual(self.render(), HEADER + 'None\n\nNo actionable work.\n')

    def test_completed_only(self):
        self.assertEqual(self.render(task(completed=True)), HEADER + 'None\n\nNo actionable work.\n')

    def test_no_old_operational_terminology(self):
        output = self.render(task())
        for word in ('GOAL', 'MILESTONE', 'ADVANCES', 'BLOCKED', 'QUEUED'):
            self.assertNotIn(word, output)

    def test_repeatable_render(self):
        brief = build_brief(CONTEXT, select_task(state(task('a'), task('b'))))
        self.assertEqual(render_brief(brief), render_brief(brief))
