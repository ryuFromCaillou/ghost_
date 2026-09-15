from dataclasses import FrozenInstanceError, fields, replace
import unittest

from goals import Why, Direction, Goal, GoalRegistry, Milestone, Status, TaskMilestoneLink, with_goal_status, with_milestone_status


class GoalTests(unittest.TestCase):
    def setUp(self):
        self.goal = Goal('g', 'Learn', 'A goal', direction_id='d')
        self.first = Milestone('m1', 'g', 'Read')
        self.second = Milestone('m2', 'g', 'Practice')
        self.link = TaskMilestoneLink('todoist', 't', 'm1')
        self.registry = GoalRegistry(goals=[self.goal], milestones=[self.first, self.second], task_links=[self.link], directions=[Direction('d', 'Learning')], whys=[])

    def test_status_defaults_and_explicit_values(self):
        self.assertIs(self.goal.status, Status.PLANNED)
        self.assertIs(self.first.status, Status.PLANNED)
        for status in Status:
            for model in (self.goal, self.first):
                self.assertIs(replace(model, status=status).status, status)

    def test_invalid_status(self):
        for value in ('active', 'invalid', None, 1, True, [], {}):
            for model in (self.goal, self.first):
                with self.subTest(value=value, model=model):
                    with self.assertRaises(ValueError):
                        replace(model, status=value)
            for helper, id in ((with_goal_status, 'g'), (with_milestone_status, 'm1')):
                with self.assertRaises(ValueError):
                    helper(self.registry, id, value)

    def test_registry_revalidates_status(self):
        for model, field in ((self.goal, 'goals'), (self.first, 'milestones')):
            malformed = replace(model)
            object.__setattr__(malformed, 'status', 'active')
            with self.assertRaises(ValueError):
                replace(self.registry, **{field: (malformed,)})

    def test_status_replacements_preserve_inputs_and_order(self):
        from unittest.mock import patch
        registry = replace(self.registry, goals=(Goal('other', '', direction_id='d'), self.goal))
        before = repr(registry)
        for helper, id, field in ((with_goal_status, 'g', 'goals'),
                                  (with_milestone_status, 'm1', 'milestones')):
            for initial in Status:
                original = helper(registry, id, initial)
                for status in Status:
                    with patch('builtins.open', side_effect=AssertionError('IO')):
                        updated = helper(original, id, status)
                    self.assertIsNot(updated, original)
                    self.assertEqual(tuple(x.id for x in getattr(updated, field)),
                                     tuple(x.id for x in getattr(original, field)))
                    self.assertEqual(updated.task_links, original.task_links)
                    for collection in ('goals', 'milestones'):
                        for old, new in zip(getattr(original, collection), getattr(updated, collection)):
                            if collection == field and old.id == id:
                                self.assertIs(new.status, status)
                                self.assertIs(old.status, initial)
                                self.assertEqual(new, replace(old, status=status))
                            else:
                                self.assertIs(new, old)
                    if status == initial:
                        self.assertEqual(updated, original)
        self.assertEqual(repr(registry), before)

    def test_status_replacement_unknown_ids(self):
        for helper, message in ((with_goal_status, 'Unknown goal'),
                                (with_milestone_status, 'Unknown milestone')):
            with self.assertRaisesRegex(ValueError, message):
                helper(self.registry, 'unknown', Status.ACTIVE)

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
            replace(self.registry, goals=(self.goal, Goal('g', 'Different title', direction_id='d')))

    def test_duplicate_milestone_ids(self):
        with self.assertRaisesRegex(ValueError, 'Duplicate milestone'):
            replace(self.registry, milestones=(self.first, replace(self.first, title='Other')))

    def test_duplicate_task_association(self):
        for duplicate in (self.link, replace(self.link, milestone_id='m2')):
            with self.subTest(duplicate=duplicate):
                with self.assertRaisesRegex(ValueError, 'Duplicate task association'):
                    replace(self.registry, task_links=(self.link, duplicate))

    def test_empty_and_non_string_identifiers(self):
        for model, names in ((self.goal, ('id', 'direction_id')), (self.first, ('id', 'goal_id')),
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
        for name in ('whys', 'directions', 'goals', 'milestones', 'task_links'):
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
        registry = GoalRegistry(goals=goals, milestones=milestones, task_links=links, directions=[Direction('d', 'Learning')], whys=[])
        for values in (goals, milestones, links):
            values.clear()
        self.assertEqual(registry, GoalRegistry(goals=(self.goal,), milestones=(self.first,), task_links=(self.link,), directions=[Direction('d', 'Learning')], whys=[]))
        for values in (registry.goals, registry.milestones, registry.task_links):
            with self.assertRaises(TypeError):
                values[0] = None

    def test_deterministic_equality(self):
        other = GoalRegistry(goals=(Goal('g', 'Learn', 'A goal', direction_id='d'),),
                             milestones=(Milestone('m1', 'g', 'Read'), Milestone('m2', 'g', 'Practice')),
                             task_links=(TaskMilestoneLink('todoist', 't', 'm1'),), directions=[Direction('d', 'Learning')], whys=[])
        self.assertEqual(self.registry, other)
        self.assertEqual(hash(self.registry), hash(other))
        self.assertNotEqual(self.registry, replace(other, task_links=()))
        self.assertEqual(GoalRegistry(), GoalRegistry(goals=[], milestones=[], task_links=[]))


    def test_full_ancestry_helpers(self):
        why = Why('w', 'Keep growing', 'Context')
        direction = Direction('d', 'Learning', why_id='w')
        registry = replace(self.registry, whys=[why], directions=[direction])
        self.assertEqual(registry.get_why('w'), why)
        self.assertEqual(registry.get_direction('d'), direction)
        self.assertEqual(registry.direction_for_goal('g'), direction)
        self.assertEqual(registry.direction_for_task('todoist', 't'), direction)
        self.assertEqual(registry.why_for_direction('d'), why)
        self.assertEqual(registry.why_for_goal('g'), why)
        self.assertEqual(registry.why_for_task('todoist', 't'), why)

    def test_optional_why_and_unknown_ancestry(self):
        for method, args in [('get_why', ('unknown',)), ('get_direction', ('unknown',)),
                             ('direction_for_goal', ('unknown',)),
                             ('direction_for_task', ('todoist', 'unknown')),
                             ('why_for_direction', ('unknown',)), ('why_for_goal', ('unknown',)),
                             ('why_for_task', ('unknown', 't')),
                             ('why_for_direction', ('d',)), ('why_for_goal', ('g',)),
                             ('why_for_task', ('todoist', 't'))]:
            with self.subTest(method=method, args=args):
                self.assertIsNone(getattr(self.registry, method)(*args))

    def test_missing_and_duplicate_ancestry(self):
        with self.assertRaisesRegex(ValueError, 'Missing why'):
            replace(self.registry, directions=[Direction('d', '', why_id='missing')])
        with self.assertRaisesRegex(ValueError, 'Missing direction'):
            replace(self.registry, directions=[])
        for name, model in [('whys', Why('w', '')), ('directions', Direction('d', ''))]:
            with self.assertRaisesRegex(ValueError, 'Duplicate'):
                replace(self.registry, **{name: [model, model]})

    def test_ancestry_validation_and_no_status(self):
        with self.assertRaises(TypeError):
            Goal('g', 'Missing required direction')
        for model in (Why('w', ''), Direction('d', '')):
            self.assertNotIn('status', {f.name for f in fields(model)})
            with self.assertRaises(TypeError):
                replace(model, status=Status.COMPLETE)
            for updates in ({'id': ''}, {'id': None}, {'title': None}, {'description': []}):
                with self.assertRaises(ValueError):
                    replace(model, **updates)
        for why_id in ('', 1, [], True):
            with self.assertRaises(ValueError):
                Direction('d', '', why_id=why_id)

    def test_ancestry_immutable_order(self):
        whys = [Why('z', ''), Why('a', '')]
        directions = [Direction('d', '', why_id='z'), Direction('a', '')]
        registry = replace(self.registry, whys=whys, directions=directions)
        whys.clear()
        directions.clear()
        self.assertEqual(tuple(w.id for w in registry.whys), ('z', 'a'))
        self.assertEqual(tuple(d.id for d in registry.directions), ('d', 'a'))
        for model in (*registry.whys, *registry.directions):
            with self.assertRaises(FrozenInstanceError):
                model.title = 'changed'
        for collection in (registry.whys, registry.directions):
            with self.assertRaises(TypeError):
                collection[0] = None
        for helper, ident in ((with_goal_status, 'g'), (with_milestone_status, 'm1')):
            updated = helper(registry, ident, Status.COMPLETE)
            self.assertEqual(updated.whys, registry.whys)
            self.assertEqual(updated.directions, registry.directions)


if __name__ == '__main__':
    unittest.main()
