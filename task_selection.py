"""Pure deterministic actionable frontier of the current task forest."""
from collections import defaultdict
from dataclasses import dataclass

from ..task_state import GhostTask, GhostTaskState, TaskStatus


class TaskSelectionError(ValueError):
    """Current hierarchy or ordering metadata is malformed."""


@dataclass(frozen=True)
class TaskSelection:
    primary: GhostTask | None
    next: tuple[GhostTask, ...]
    ancestors: tuple[GhostTask, ...]


def _ordered(rows, legacy):
    """Sort one provider scope; never mix fractional and integer positions."""
    for identity, raw in rows:
        key, old = raw.get('order_key'), raw.get(legacy)
        if key is not None and (not isinstance(key, str) or not key):
            raise TaskSelectionError('Invalid provider order_key')
        if old is not None and type(old) is not int:
            raise TaskSelectionError('Invalid provider ordering integer')
    fractional = bool(rows) and all(raw.get('order_key') is not None for _, raw in rows)
    if fractional:
        return [i for i, r in sorted(rows, key=lambda pair: (pair[1]['order_key'], pair[0]))]
    return [i for i, r in sorted(rows, key=lambda pair: (
        pair[1].get(legacy) is None, pair[1].get(legacy) or 0, pair[0]))]


def select_task(task_state: GhostTaskState, *, projects=(), sections=()) -> TaskSelection:
    """Depth-first provider order, filtered to ACTIVE tasks with no ACTIVE child.

    Raw project/section rows supply metadata absent from normalized context models.
    History and strategic context are deliberately not inputs. See README for
    grouping, fallback, and cross-project ordering details.
    """
    tasks = {}
    identity = lambda i: (i.source, i.source_id)
    for task in task_state.tasks:
        if task.id in tasks:
            raise TaskSelectionError('Duplicate current task identity')
        if task.status not in (TaskStatus.ACTIVE, TaskStatus.COMPLETED):
            raise TaskSelectionError('Unsupported current task status')
        tasks[task.id] = task
    children = defaultdict(list)
    for task in tasks.values():
        if task.parent_id is not None:
            parent = tasks.get(task.parent_id)
            if parent is None:
                raise TaskSelectionError('Task references a missing parent')
            if parent.project != task.project:
                raise TaskSelectionError('Task parent belongs to a different project')
        children[task.parent_id].append(task)
    done = set()
    for task in tasks.values():
        chain, current = set(), task.id
        while current is not None and current not in done:
            if current in chain:
                raise TaskSelectionError('Task parent hierarchy contains a cycle')
            chain.add(current)
            current = tasks[current].parent_id
        done.update(chain)

    project_raw = {('todoist', r['id']): r for r in projects}
    section_raw = {('todoist', r['id']): r for r in sections}
    project_ids = {identity(t.project.id) if t.project else ('', '') for t in tasks.values()}
    project_rank = {p: n for n, p in enumerate(_ordered(
        [(p, project_raw.get(p, {})) for p in project_ids], 'child_order'))}
    section_rank = {}
    for p in project_ids:
        ids = {identity(t.section.id) for t in tasks.values() if t.section is not None
               and (identity(t.project.id) if t.project else ('', '')) == p}
        section_rank.update({(p, s): n for n, s in enumerate(_ordered(
            [(s, section_raw.get(s, {})) for s in ids], 'section_order'))})

    def ordered(group):
        scopes = defaultdict(list)
        lookup = {identity(t.id): t for t in group}
        for t in group:
            p = identity(t.project.id) if t.project else ('', '')
            s = identity(t.section.id) if t.section else None
            scopes[(project_rank[p], -1 if s is None else section_rank[(p, s)])].append(
                (identity(t.id), t.raw))
        return [lookup[i] for scope in sorted(scopes)
                for i in _ordered(scopes[scope], 'child_order')]

    frontier = []
    stack = list(reversed(ordered(children[None])))
    while stack:
        task = stack.pop()
        direct = children[task.id]
        if task.status == TaskStatus.ACTIVE and not any(t.status == TaskStatus.ACTIVE for t in direct):
            frontier.append(task)
        stack.extend(reversed(ordered(direct)))
    primary = frontier[0] if frontier else None
    ancestors = []
    parent_id = primary.parent_id if primary else None
    while parent_id is not None:
        parent = tasks[parent_id]
        ancestors.append(parent)
        parent_id = parent.parent_id
    return TaskSelection(primary, tuple(frontier[1:]), tuple(reversed(ancestors)))
