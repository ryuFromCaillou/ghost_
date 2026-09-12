"""Todoist snapshot to GHOST task state. Run as integrations.todoist.normalize."""
from collections.abc import Mapping
from datetime import date, datetime
from types import MappingProxyType

from ..task_state import (GhostTask, GhostTaskState, Project, Section,
                          SnapshotProvenance, SourceIdentity, TaskDate,
                          TaskPriority, TaskStatus)
from .reader import TodoistSnapshot, load_snapshot, SnapshotReadError


class NormalizationError(Exception):
    """A snapshot value cannot be represented without losing its meaning."""


PRIORITIES = {1: TaskPriority.NORMAL, 2: TaskPriority.MEDIUM,
              3: TaskPriority.HIGH, 4: TaskPriority.URGENT}


def _identity(value):
    return None if value is None else SourceIdentity('todoist', value)


def _string(value):
    if value is not None and not isinstance(value, str):
        raise NormalizationError('Expected optional string')
    return value


def _timestamp(value):
    if value is None:
        return None
    value = _string(value)
    if 'T' not in value:
        raise NormalizationError('Expected ISO datetime')
    return datetime.fromisoformat(value)


def _date(value):
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise NormalizationError('Expected date object')
    # Support both v1 date-with-time and explicit datetime representations.
    exact = _string(value.get('datetime'))
    raw = exact if exact is not None else _string(value.get('date'))
    parsed = None if raw is None else (_timestamp(raw) if exact is not None or 'T' in raw
                                       else date.fromisoformat(raw))
    recurring = value.get('is_recurring')
    if recurring is not None and type(recurring) is not bool:
        raise NormalizationError('Expected optional recurrence boolean')
    return TaskDate(parsed, _string(value.get('timezone')), recurring,
                    _string(value.get('string')), _string(value.get('lang')))


def _freeze(value):
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    return value


def normalize_todoist_snapshot(snapshot: TodoistSnapshot) -> GhostTaskState:
    """Pure conversion of one reader-validated generation; no IO or hierarchy repair."""
    try:
        provenance = SnapshotProvenance('todoist', str(snapshot.directory),
                                         _timestamp(snapshot.metadata['synced_at']))
        projects = {p['id']: Project(_identity(p['id']), p['name']) for p in snapshot.projects}
        sections = {s['id']: Section(_identity(s['id']), s['name'], _identity(s['project_id']))
                    for s in snapshot.sections}
        tasks = []
        for row in snapshot.tasks:
            priority = row.get('priority')
            if priority is not None and (type(priority) is not int or priority not in PRIORITIES):
                raise NormalizationError('Unsupported task priority')
            checked = row['checked']
            if type(checked) is not bool:
                raise NormalizationError('Expected boolean completion state')
            labels = row.get('labels')
            if labels is not None and (not isinstance(labels, (tuple, list)) or
                                       any(not isinstance(tag, str) for tag in labels)):
                raise NormalizationError('Expected optional string tags')
            tasks.append(GhostTask(
                id=_identity(row['id']), title=row['content'],
                description=_string(row.get('description')),
                project=projects[row['project_id']] if row.get('project_id') is not None else None,
                section=sections[row['section_id']] if row.get('section_id') is not None else None,
                parent_id=_identity(row.get('parent_id')),
                tags=None if labels is None else tuple(labels),
                priority=PRIORITIES.get(priority),
                status=TaskStatus.COMPLETED if checked else TaskStatus.ACTIVE,
                due=_date(row.get('due')), deadline=_date(row.get('deadline')),
                created_at=_timestamp(row.get('added_at')), updated_at=_timestamp(row.get('updated_at')),
                provenance=provenance, raw=_freeze(row)))
        return GhostTaskState(tuple(tasks), tuple(projects.values()), tuple(sections.values()), provenance)
    except NormalizationError:
        raise
    except (KeyError, TypeError, ValueError) as exc:
        raise NormalizationError(f'Cannot normalize Todoist snapshot ({type(exc).__name__})') from None


def main():
    import argparse
    from pprint import pprint
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--show-tasks', action='store_true', help='Print normalized fields, excluding raw data')
    args = parser.parse_args()
    try:
        state = normalize_todoist_snapshot(load_snapshot())
    except (SnapshotReadError, NormalizationError) as exc:
        parser.exit(1, f'Task state unavailable: {exc}\n')
    print(f'GHOST task state: {len(state.tasks)} tasks, {len(state.projects)} projects, {len(state.sections)} sections')
    print(f'Snapshot: {state.provenance.generation}')
    if args.show_tasks:
        from dataclasses import fields
        for task in state.tasks:
            pprint({field.name: getattr(task, field.name) for field in fields(task) if field.name != 'raw'})


if __name__ == '__main__':
    main()
