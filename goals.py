"""Pure, provider-independent goal hierarchy and explicit task associations.

Collections accept lists or tuples and preserve order as immutable tuples.
Invalid models or registries raise ValueError; missing lookups return None.
Task existence and completion remain the task provider's responsibility.
"""
from dataclasses import dataclass, replace
from enum import Enum


class Status(str, Enum):
    """Explicit semantic assertion, independent of task evidence."""

    PLANNED = 'planned'
    ACTIVE = 'active'
    BLOCKED = 'blocked'
    COMPLETE = 'complete'


def _status(value):
    if not isinstance(value, Status):
        raise ValueError('status must be a Status')


def _identifier(value, name):
    if not isinstance(value, str) or not value:
        raise ValueError(f'{name} must be a nonempty string')


def _content(title, description):
    if not isinstance(title, str):
        raise ValueError('title must be a string')
    if description is not None and not isinstance(description, str):
        raise ValueError('description must be a string or None')


@dataclass(frozen=True)
class Goal:
    id: str
    title: str
    description: str | None = None
    status: Status = Status.PLANNED

    def __post_init__(self):
        _identifier(self.id, 'id')
        _content(self.title, self.description)
        _status(self.status)


@dataclass(frozen=True)
class Milestone:
    id: str
    goal_id: str
    title: str
    description: str | None = None
    status: Status = Status.PLANNED

    def __post_init__(self):
        _identifier(self.id, 'id')
        _identifier(self.goal_id, 'goal_id')
        _content(self.title, self.description)
        _status(self.status)


@dataclass(frozen=True)
class TaskMilestoneLink:
    task_source: str
    task_source_id: str
    milestone_id: str

    def __post_init__(self):
        for name in ('task_source', 'task_source_id', 'milestone_id'):
            _identifier(getattr(self, name), name)


@dataclass(frozen=True)
class GoalRegistry:
    goals: tuple[Goal, ...] = ()
    milestones: tuple[Milestone, ...] = ()
    task_links: tuple[TaskMilestoneLink, ...] = ()

    def __post_init__(self):
        for name, model in (('goals', Goal), ('milestones', Milestone),
                            ('task_links', TaskMilestoneLink)):
            values = getattr(self, name)
            if (not isinstance(values, (list, tuple))
                    or any(not isinstance(value, model) for value in values)):
                raise ValueError(f'{name} must be a list or tuple of {model.__name__}')
            object.__setattr__(self, name, tuple(values))

        for model in (*self.goals, *self.milestones):
            _status(model.status)

        goal_ids = {goal.id for goal in self.goals}
        milestone_ids = {milestone.id for milestone in self.milestones}
        if len(goal_ids) != len(self.goals):
            raise ValueError('Duplicate goal IDs')
        if len(milestone_ids) != len(self.milestones):
            raise ValueError('Duplicate milestone IDs')
        for milestone in self.milestones:
            if milestone.goal_id not in goal_ids:
                raise ValueError(f'Missing goal: {milestone.goal_id}')
        identities = set()
        for link in self.task_links:
            if link.milestone_id not in milestone_ids:
                raise ValueError(f'Missing milestone: {link.milestone_id}')
            identity = (link.task_source, link.task_source_id)
            if identity in identities:
                raise ValueError(f'Duplicate task association: {identity}')
            identities.add(identity)

    def get_goal(self, goal_id: str) -> Goal | None:
        return next((goal for goal in self.goals if goal.id == goal_id), None)

    def get_milestone(self, milestone_id: str) -> Milestone | None:
        return next((milestone for milestone in self.milestones
                     if milestone.id == milestone_id), None)

    def milestone_for_task(self, source: str, source_id: str) -> Milestone | None:
        link = next((link for link in self.task_links
                     if (link.task_source, link.task_source_id) == (source, source_id)), None)
        return None if link is None else self.get_milestone(link.milestone_id)

    def goal_for_task(self, source: str, source_id: str) -> Goal | None:
        milestone = self.milestone_for_task(source, source_id)
        return None if milestone is None else self.get_goal(milestone.goal_id)


def with_goal_status(registry: GoalRegistry, goal_id: str, status: Status) -> GoalRegistry:
    """Return a new registry with an explicit assertion; no transition policy or IO."""
    _status(status)
    if registry.get_goal(goal_id) is None:
        raise ValueError(f'Unknown goal: {goal_id}')
    return replace(registry, goals=tuple(
        replace(goal, status=status) if goal.id == goal_id else goal
        for goal in registry.goals))


def with_milestone_status(registry: GoalRegistry, milestone_id: str,
                          status: Status) -> GoalRegistry:
    """Return a new registry, preserving links, goals, and milestone order."""
    _status(status)
    if registry.get_milestone(milestone_id) is None:
        raise ValueError(f'Unknown milestone: {milestone_id}')
    return replace(registry, milestones=tuple(
        replace(milestone, status=status) if milestone.id == milestone_id else milestone
        for milestone in registry.milestones))
