"""Pure milestone-level recommendations; registry order is explicit priority.

Validate the complete supplied projection before selecting. All milestones are
considered in goal order, then milestone order within each goal. Exclusions list
only ineligible milestones; eligible lower-priority work is not an exclusion.
"""
from dataclasses import dataclass

from ..task_state import SourceIdentity
from .goals import GoalRegistry, Status
from .progress import ProgressProjection

SELECTED_REASONS = ('goal_active', 'milestone_active', 'actionable_tasks_present',
                    'highest_registry_priority')


class ObjectiveSelectionError(ValueError):
    """Registry and projection do not describe corresponding semantic state."""


@dataclass(frozen=True)
class Objective:
    goal_id: str
    milestone_id: str
    task_ids: tuple[SourceIdentity, ...]
    reason_codes: tuple[str, ...]
    summary: str


@dataclass(frozen=True)
class ObjectiveExclusion:
    milestone_id: str
    reason_code: str


@dataclass(frozen=True)
class ObjectiveSelection:
    objective: Objective | None
    considered_milestone_ids: tuple[str, ...]
    excluded: tuple[ObjectiveExclusion, ...]


def _index(records, field, expected):
    indexed = {}
    for record in records:
        id = getattr(record, field)
        if id in indexed:
            raise ObjectiveSelectionError(f'Duplicate projection {field}: {id}')
        if id not in expected:
            raise ObjectiveSelectionError(f'Unknown projection {field}: {id}')
        indexed[id] = record
    if set(indexed) != set(expected):
        raise ObjectiveSelectionError(f'Missing projection {field}')
    return indexed


def _validate(registry, projection):
    goals = _index(projection.goals, 'goal_id', {g.id for g in registry.goals})
    milestones = _index(projection.milestones, 'milestone_id',
                        {m.id for m in registry.milestones})
    links = {m.id: [] for m in registry.milestones}
    children = {g.id: [] for g in registry.goals}
    for link in registry.task_links:
        links[link.milestone_id].append(SourceIdentity(link.task_source, link.task_source_id))
    task_fields = ('linked_task_ids', 'active_task_ids', 'completed_task_ids', 'missing_task_ids')
    for milestone in registry.milestones:
        p = milestones[milestone.id]
        children[milestone.goal_id].append(p)
        if p.goal_id != milestone.goal_id or p.status is not milestone.status:
            raise ObjectiveSelectionError(f'Milestone parent/status mismatch: {milestone.id}')
        if p.linked_task_ids != tuple(links[milestone.id]):
            raise ObjectiveSelectionError(f'Milestone links/order mismatch: {milestone.id}')
        classified = []
        for field in task_fields[1:]:
            values = getattr(p, field)
            if values != tuple(identity for identity in p.linked_task_ids if identity in values):
                raise ObjectiveSelectionError(f'Milestone classification/order mismatch: {milestone.id}')
            classified.extend(values)
        if len(classified) != len(set(classified)) or set(classified) != set(p.linked_task_ids):
            raise ObjectiveSelectionError(f'Milestone classifications must partition links: {milestone.id}')
    for goal in registry.goals:
        p = goals[goal.id]
        members = children[goal.id]
        if p.status is not goal.status or p.milestone_ids != tuple(m.milestone_id for m in members):
            raise ObjectiveSelectionError(f'Goal status/milestones mismatch: {goal.id}')
        for field in task_fields:
            expected = tuple(identity for member in members for identity in getattr(member, field))
            if getattr(p, field) != expected:
                raise ObjectiveSelectionError(f'Goal task aggregation mismatch: {goal.id}')
    return children


def select_objective(registry: GoalRegistry, projection: ProgressProjection) -> ObjectiveSelection:
    """Select the first ACTIVE milestone under an ACTIVE goal with active tasks.

History counts/timestamps never affect selection. Validate identities, statuses,
links, and current classifications, trusting the caller's historical evidence.
No projection rebuilding, source access, or semantic state changes occur here.
"""
    children = _validate(registry, projection)
    milestone_models = {m.id: m for m in registry.milestones}
    considered, excluded = [], []
    objective = None
    for goal in registry.goals:
        for progress in children[goal.id]:
            milestone = milestone_models[progress.milestone_id]
            considered.append(milestone.id)
            if goal.status is not Status.ACTIVE:
                reason = 'goal_not_active'
            elif milestone.status is Status.BLOCKED:
                reason = 'milestone_blocked'
            elif milestone.status is Status.COMPLETE:
                reason = 'milestone_complete'
            elif milestone.status is not Status.ACTIVE:
                reason = 'milestone_not_active'
            elif not progress.active_task_ids:
                reason = 'no_actionable_tasks'
            else:
                if objective is None:
                    objective = Objective(goal.id, milestone.id, progress.active_task_ids,
                                          SELECTED_REASONS, f'Advance milestone: {milestone.title}')
                continue
            excluded.append(ObjectiveExclusion(milestone.id, reason))
    return ObjectiveSelection(objective, tuple(considered), tuple(excluded))
