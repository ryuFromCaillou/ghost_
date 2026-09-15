"""Pure task selection beneath an already-selected milestone; no IO or history."""
from dataclasses import dataclass

from ..task_state import GhostTask, GhostTaskState, SourceIdentity, TaskStatus
from .goals import GoalRegistry


class TaskSelectionError(ValueError):
    """The selected milestone is unknown or current identities are ambiguous."""


@dataclass(frozen=True)
class TaskSelection:
    primary: GhostTask | None
    secondary: tuple[GhostTask, ...]
    reason: str


def select_task(registry: GoalRegistry, task_state: GhostTaskState,
                milestone_id: str | None) -> TaskSelection:
    """First current ACTIVE linked task wins in GoalRegistry task-link order.

    Remaining eligible tasks retain that order. Missing and all non-ACTIVE
    statuses are skipped. No objective selection, history, scheduling, or state
    changes occur. None means no selected milestone; never promote another one.
    Unknown milestones and duplicate current identities are rejected.
    """
    if milestone_id is None:
        return TaskSelection(None, (), 'no_selected_milestone')
    if registry.get_milestone(milestone_id) is None:
        raise TaskSelectionError(f'Unknown milestone: {milestone_id}')
    tasks = {}
    for task in task_state.tasks:
        if task.id in tasks:
            raise TaskSelectionError(f'Duplicate current task identity: {task.id}')
        tasks[task.id] = task
    eligible = []
    for link in registry.task_links:
        if link.milestone_id != milestone_id:
            continue
        task = tasks.get(SourceIdentity(link.task_source, link.task_source_id))
        if task is not None and task.status == TaskStatus.ACTIVE:
            eligible.append(task)
    if not eligible:
        return TaskSelection(None, (), 'no_eligible_active_linked_task')
    return TaskSelection(eligible[0], tuple(eligible[1:]), 'first_active_task_in_registry_link_order')
