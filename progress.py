"""Pure progress evidence from already-loaded registry, task state, and events.

Task ID tuples contain SourceIdentity, never ambiguous provider-local strings.
Current status and historical evidence are independent. Every supplied matching
completion occurrence is counted; callers provide the desired evidence scope.
"""
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from ..task_state import GhostTaskState, SourceIdentity, TaskStatus
from .completion_events import TaskCompletionEvent
from .goals import GoalRegistry, Status


class ProjectionError(ValueError):
    """A milestone is unknown or current task state is ambiguous/unsupported."""


@dataclass(frozen=True)
class MilestoneProgress:
    milestone_id: str
    goal_id: str
    linked_task_ids: tuple[SourceIdentity, ...]
    active_task_ids: tuple[SourceIdentity, ...]
    completed_task_ids: tuple[SourceIdentity, ...]
    missing_task_ids: tuple[SourceIdentity, ...]
    completion_event_count: int
    last_completed_at: datetime | None
    status: Status


@dataclass(frozen=True)
class GoalProgress:
    goal_id: str
    milestone_ids: tuple[str, ...]
    linked_task_ids: tuple[SourceIdentity, ...]
    active_task_ids: tuple[SourceIdentity, ...]
    completed_task_ids: tuple[SourceIdentity, ...]
    missing_task_ids: tuple[SourceIdentity, ...]
    completion_event_count: int
    last_completed_at: datetime | None
    status: Status


@dataclass(frozen=True)
class ProgressProjection:
    milestones: tuple[MilestoneProgress, ...]
    goals: tuple[GoalProgress, ...]


def _indexes(task_state, completion_events):
    tasks = {}
    for task in task_state.tasks:
        if task.id in tasks:
            raise ProjectionError(f'Duplicate current task identity: {task.id}')
        if task.status not in (TaskStatus.ACTIVE, TaskStatus.COMPLETED):
            raise ProjectionError(f'Unsupported current task status: {task.status}')
        tasks[task.id] = task.status
    evidence = {}
    for event in completion_events:
        count, latest = evidence.get(event.task_id, (0, None))
        evidence[event.task_id] = (count + 1, event.completed_at if latest is None
                                   else max(latest, event.completed_at))
    return tasks, evidence


def _milestone(milestone, links, tasks, evidence):
    linked = tuple(SourceIdentity(link.task_source, link.task_source_id) for link in links)
    active = tuple(identity for identity in linked if tasks.get(identity) == TaskStatus.ACTIVE)
    completed = tuple(identity for identity in linked if tasks.get(identity) == TaskStatus.COMPLETED)
    missing = tuple(identity for identity in linked if identity not in tasks)
    matches = [evidence[identity] for identity in linked if identity in evidence]
    return MilestoneProgress(milestone.id, milestone.goal_id, linked, active, completed,
                             missing, sum(count for count, _ in matches),
                             max((stamp for _, stamp in matches), default=None),
                             status=milestone.status)


def project_milestone_progress(
    registry: GoalRegistry,
    task_state: GhostTaskState,
    completion_events: Iterable[TaskCompletionEvent],
    milestone_id: str,
) -> MilestoneProgress:
    """Preserve task-link order; missing means absent only from supplied state."""
    milestone = registry.get_milestone(milestone_id)
    if milestone is None:
        raise ProjectionError(f'Unknown milestone: {milestone_id}')
    tasks, evidence = _indexes(task_state, completion_events)
    return _milestone(milestone, (link for link in registry.task_links
                                 if link.milestone_id == milestone_id), tasks, evidence)


def project_progress(
    registry: GoalRegistry,
    task_state: GhostTaskState,
    completion_events: Iterable[TaskCompletionEvent],
) -> ProgressProjection:
    """Preserve registry order, aggregating goal tasks in milestone/link order."""
    tasks, evidence = _indexes(task_state, completion_events)
    links = {milestone.id: [] for milestone in registry.milestones}
    for link in registry.task_links:
        links[link.milestone_id].append(link)
    milestones = tuple(_milestone(m, links[m.id], tasks, evidence) for m in registry.milestones)
    by_goal = {goal.id: [] for goal in registry.goals}
    for milestone in milestones:
        by_goal[milestone.goal_id].append(milestone)
    goals = []
    for goal in registry.goals:
        children = by_goal[goal.id]
        def combine(field):
            return tuple(dict.fromkeys(identity for child in children
                                       for identity in getattr(child, field)))
        goals.append(GoalProgress(
            goal.id, tuple(child.milestone_id for child in children),
            *(combine(field) for field in ('linked_task_ids', 'active_task_ids',
                                          'completed_task_ids', 'missing_task_ids')),
            sum(child.completion_event_count for child in children),
            max((child.last_completed_at for child in children
                 if child.last_completed_at is not None), default=None),
            status=goal.status))
    return ProgressProjection(milestones, tuple(goals))
