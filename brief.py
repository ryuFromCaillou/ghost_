"""Pure operator presentation of supplied state, interpretation, and decision."""
from dataclasses import dataclass

from ..task_state import GhostTaskState, SourceIdentity, TaskStatus
from .goals import GoalRegistry, Status
from .objective import ObjectiveSelection, ObjectiveSelectionError, validate_projection
from .progress import ProgressProjection
from .task_selection import TaskSelection, select_task


class BriefError(ValueError):
    """Supplied state and decision cannot be presented consistently."""


@dataclass(frozen=True)
class BriefTask:
    source: SourceIdentity
    content: str


@dataclass(frozen=True)
class BriefObjective:
    direction_id: str
    direction_title: str
    why_id: str | None
    why_title: str | None
    goal_id: str
    goal_title: str
    milestone_id: str
    milestone_title: str
    summary: str
    primary_task: BriefTask | None
    secondary_tasks: tuple[BriefTask, ...]

    @property
    def tasks(self) -> tuple[BriefTask, ...]:
        return (() if self.primary_task is None else (self.primary_task,)) + self.secondary_tasks


@dataclass(frozen=True)
class BriefBlockedItem:
    goal_id: str
    goal_title: str
    milestone_id: str
    milestone_title: str


@dataclass(frozen=True)
class BriefQueuedItem:
    goal_id: str
    goal_title: str
    milestone_id: str
    milestone_title: str


@dataclass(frozen=True)
class GhostBrief:
    primary: BriefObjective | None
    blocked: tuple[BriefBlockedItem, ...]
    queued: tuple[BriefQueuedItem, ...]
    message_codes: tuple[str, ...]


def build_brief(registry: GoalRegistry, task_state: GhostTaskState,
                projection: ProgressProjection, selection: ObjectiveSelection,
                task_selection: TaskSelection | None = None) -> GhostBrief:
    """Present the supplied objective unchanged, with nearby blocked/ready work.

    Validate current classifications against supplied tasks, never rebuild a
    projection or select an objective. An absent objective stays absent, even if
    actionable alternatives exist. Task priority follows registry link order; the objective remains unchanged.
    """
    try:
        children = validate_projection(registry, projection)
    except ObjectiveSelectionError as exc:
        raise BriefError(str(exc)) from exc
    tasks = {}
    for task in task_state.tasks:
        if task.id in tasks:
            raise BriefError(f'Duplicate current task identity: {task.id}')
        if task.status not in (TaskStatus.ACTIVE, TaskStatus.COMPLETED):
            raise BriefError(f'Unsupported current task status: {task.status}')
        tasks[task.id] = task
    milestones = {m.milestone_id: m for m in projection.milestones}
    for progress in projection.milestones:
        for field, status in (('active_task_ids', TaskStatus.ACTIVE),
                              ('completed_task_ids', TaskStatus.COMPLETED),
                              ('missing_task_ids', None)):
            for identity in getattr(progress, field):
                task = tasks.get(identity)
                if (status is None and task is not None) or (
                        status is not None and (task is None or task.status != status)):
                    raise BriefError(f'Current task/projection mismatch: {identity}')
    primary = None
    objective = selection.objective
    if objective is not None:
        goal = registry.get_goal(objective.goal_id)
        milestone = registry.get_milestone(objective.milestone_id)
        if goal is None or milestone is None:
            raise BriefError('Objective references unknown goal or milestone')
        if milestone.goal_id != goal.id:
            raise BriefError('Objective milestone belongs to another goal')
        if goal.status is not Status.ACTIVE or milestone.status is not Status.ACTIVE:
            raise BriefError('Objective goal and milestone must be ACTIVE')
        expected = milestones[milestone.id].active_task_ids
        if (not objective.task_ids or len(set(objective.task_ids)) != len(objective.task_ids)
                or set(objective.task_ids) != set(expected)):
            raise BriefError('Objective tasks must match the active linked projection task set')
        expected_selection = select_task(registry, task_state,
                                         None if objective is None else objective.milestone_id)
        if task_selection is not None and task_selection != expected_selection:
            raise BriefError("Task selection does not match current registry/task state")
        task_selection = expected_selection
        direction = registry.direction_for_goal(goal.id)
        why = registry.why_for_goal(goal.id)
        primary = BriefObjective(direction.id, direction.title,
                                 None if why is None else why.id,
                                 None if why is None else why.title,
                                 goal.id, goal.title, milestone.id, milestone.title,
                                 objective.summary,
                                 None if task_selection.primary is None else BriefTask(
                                     task_selection.primary.id, task_selection.primary.title),
                                 tuple(BriefTask(task.id, task.title)
                                       for task in task_selection.secondary))
    blocked, queued = [], []
    for goal in registry.goals:
        if goal.status is not Status.ACTIVE:
            continue
        for progress in children[goal.id]:
            milestone = registry.get_milestone(progress.milestone_id)
            fields = (goal.id, goal.title, milestone.id, milestone.title)
            if milestone.status is Status.BLOCKED:
                blocked.append(BriefBlockedItem(*fields))
            elif (milestone.status is Status.ACTIVE and progress.active_task_ids
                  and (objective is None or milestone.id != objective.milestone_id)):
                queued.append(BriefQueuedItem(*fields))
    codes = ['primary_objective_selected' if primary is not None else 'no_primary_objective']
    if blocked:
        codes.append('blocked_work_present')
    if queued:
        codes.append('queued_work_present')
    if primary is None and not blocked and not queued:
        codes.append('no_actionable_work')
    return GhostBrief(primary, tuple(blocked), tuple(queued), tuple(codes))


def render_brief(brief: GhostBrief) -> str:
    """Plain deterministic sections, no metadata; return one trailing newline."""
    lines = ['GHOST BRIEF']
    if brief.primary is not None:
        if brief.primary.why_title is not None:
            lines.extend(('', 'WHY', brief.primary.why_title))
        lines.extend(('', 'DIRECTION', brief.primary.direction_title))
    lines.extend(('', 'PRIMARY ORDER'))
    if brief.primary is None:
        lines.append('None')
    else:
        primary = brief.primary
        lines.append('None' if primary.primary_task is None else primary.primary_task.content)
        lines.extend(('', 'ADVANCES', primary.goal_title, primary.milestone_title))
        if primary.secondary_tasks:
            lines.extend(('', 'NEXT'))
            lines.extend(f'- {task.content}' for task in primary.secondary_tasks)
    for heading, items in (('BLOCKED', brief.blocked), ('QUEUED', brief.queued)):
        if items:
            lines.extend(('', heading))
            lines.extend(f'- {item.goal_title} — {item.milestone_title}' for item in items)
    if brief.primary is None and not brief.blocked and not brief.queued:
        lines.extend(('', 'No actionable work.'))
    return '\n'.join(lines) + '\n'
