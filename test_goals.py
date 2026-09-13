from dataclasses import FrozenInstanceError, fields, replace
import unittest

from goals import Goal, GoalRegistry, Milestone, TaskMilestoneLink


class GoalTests(unittest.TestCase):
    def setUp(self):
        self.goal = Goal('g', 'Learn', 'A goal')
        self.first = Milestone('m1', 'g', 'Read')
        self.second = Milestone('m2', 'g', 'Practice')
        self.link = TaskMilestoneLink('todoist', 't', 'm1')
        self.registry = GoalRegistry([self.goal], [self.first, self.second], [self.link])

    def test_resolution(self):
        self.assertEqual(self.registry.get_goal('g'), self.goal)
        self.assertEqual(self.registry.get_milestone('m1'), self.first)
        self.assertEqual(self.registry.milestone_for_task('todoist', 't'), self.first)
        self.assertEqual(self.registry.goal_for_task('todoist', 't'), self.goal)

    def test_multiple_milestones_and_provider_identity(self):
        registry = replace(self.registry, task_links=(
            self.link, TaskMilestoneLink('other', 't', 'm2')))
        self.assertEqual(registry.milestone_for_task('other', 't'), self.second)
        self.assertEqual(registry.goal_for_task('other', 't'), self.goal)
        self.assertEqual(registry.goal_for_task('todoist', 't'), self.goal)

    def test_missing_association_and_lookups(self):
        self.assertIsNone(self.registry.milestone_for_task('todoist', 'unknown'))
        self.assertIsNone(self.registry.goal_for_task('unknown', 't'))
        self.assertIsNone(self.registry.get_goal('unknown'))
        self.assertIsNone(self.registry.get_milestone('unknown'))
        self.assertIsNone(GoalRegistry().goal_for_task('todoist', 't'))

    def test_missing_goal(self):
        with self.assertRaisesRegex(ValueError, 'Missing goal'):
            replace(self.registry, goals=())

    def test_missing_milestone(self):
        with self.assertRaisesRegex(ValueError, 'Missing milestone'):
            replace(self.registry, milestones=())

    def test_duplicate_goal_ids(self):
        with self.assertRaisesRegex(ValueError, 'Duplicate goal'):
            replace(self.registry, goals=(self.goal, Goal('g', 'Different title')))

    def test_duplicate_milestone_ids(self):
        with self.assertRaisesRegex(ValueError, 'Duplicate milestone'):
            replace(self.registry, milestones=(self.first, replace(self.first, title='Other')))

    def test_duplicate_task_association(self):
        for duplicate in (self.link, replace(self.link, milestone_id='m2')):
            with self.subTest(duplicate=duplicate):
                with self.assertRaisesRegex(ValueError, 'Duplicate task association'):
                    replace(self.registry, task_links=(self.link, duplicate))

    def test_empty_and_non_string_identifiers(self):
        for model, names in ((self.goal, ('id',)), (self.first, ('id', 'goal_id')),
                             (self.link, ('task_source', 'task_source_id', 'milestone_id'))):
            for name in names:
                for value in ('', None, 1, [], True):
                    with self.subTest(model=model, name=name, value=value):
                        with self.assertRaises(ValueError):
                            replace(model, **{name: value})

    def test_invalid_content_and_collections(self):
        for model in (self.goal, self.first):
            for updates in ({'title': None}, {'title': []}, {'description': []}):
                with self.subTest(model=model, updates=updates):
                    with self.assertRaises(ValueError):
                        replace(model, **updates)
        for name in ('goals', 'milestones', 'task_links'):
            for value in (None, {}, 'bad', [object()]):
                with self.subTest(name=name, value=value):
                    with self.assertRaises(ValueError):
                        replace(self.registry, **{name: value})

    def test_immutability(self):
        for model in (self.goal, self.first, self.link, self.registry):
            for field in fields(model):
                with self.subTest(model=model, field=field.name):
                    with self.assertRaises(FrozenInstanceError):
                        setattr(model, field.name, None)
        goals, milestones, links = [self.goal], [self.first], [self.link]
        registry = GoalRegistry(goals, milestones, links)
        for values in (goals, milestones, links):
            values.clear()
        self.assertEqual(registry, GoalRegistry((self.goal,), (self.first,), (self.link,)))
        for values in (registry.goals, registry.milestones, registry.task_links):
            with self.assertRaises(TypeError):
                values[0] = None

    def test_deterministic_equality(self):
        other = GoalRegistry((Goal('g', 'Learn', 'A goal'),),
                             (Milestone('m1', 'g', 'Read'), Milestone('m2', 'g', 'Practice')),
                             (TaskMilestoneLink('todoist', 't', 'm1'),))
        self.assertEqual(self.registry, other)
        self.assertEqual(hash(self.registry), hash(other))
        self.assertNotEqual(self.registry, replace(other, task_links=()))
        self.assertEqual(GoalRegistry(), GoalRegistry([], [], []))


if __name__ == '__main__':
    unittest.main()
