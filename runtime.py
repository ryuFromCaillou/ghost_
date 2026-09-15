"""Read stored reality for a GHOST brief or explicitly complete PRIMARY ORDER."""
import argparse
from dataclasses import dataclass
import os
import sqlite3
import sys

from ..task_state import GhostTaskState
from . import paths
from .brief import BriefError, GhostBrief, build_brief, render_brief
from .completion_events import TaskCompletionEvent, load_events
from .goal_store import GoalStoreError, load_goal_registry
from .goals import GoalRegistry
from .normalize import NormalizationError, normalize_todoist_snapshot
from .objective import ObjectiveSelection, ObjectiveSelectionError, select_objective
from .progress import ProgressProjection, ProjectionError, project_progress
from .task_selection import TaskSelection, TaskSelectionError, select_task
from .reader import SnapshotReadError, TodoistSnapshot, load_snapshot
from .todoist_write import TodoistWriteError, close_task


class BriefRunError(Exception):
    """Operator-facing failure; the original exception is retained as __cause__."""


@dataclass(frozen=True)
class BriefRun:
    # The reader-validated UTC timestamp is snapshot.metadata['synced_at'].
    snapshot: TodoistSnapshot
    task_state: GhostTaskState
    registry: GoalRegistry
    completion_events: tuple[TaskCompletionEvent, ...]
    projection: ProgressProjection
    selection: ObjectiveSelection
    brief: GhostBrief
    task_selection: TaskSelection


def run_brief(*, snapshot_path=None, registry_path=None, history_path=None) -> BriefRun:
    """Read existing state only. Defaults come from paths; no freshness cutoff.

    All locally stored completion events are passed to projection, without a
    date filter. Missing state is an error, including for an empty registry.
    """
    snapshot_path = paths.TODOIST_EXTERNAL_STATE_ROOT if snapshot_path is None else snapshot_path
    registry_path = paths.GOAL_REGISTRY_PATH if registry_path is None else registry_path
    history_path = paths.TODOIST_HISTORY_DB_PATH if history_path is None else history_path
    try:
        snapshot = load_snapshot(snapshot_path)
    except SnapshotReadError as exc:
        raise BriefRunError(f'Todoist snapshot unavailable or invalid: {snapshot_path}. {exc}') from exc
    try:
        task_state = normalize_todoist_snapshot(snapshot)
    except NormalizationError as exc:
        raise BriefRunError(f'Normalization failed: {exc}') from exc
    try:
        registry = load_goal_registry(registry_path)
    except GoalStoreError as exc:
        reason = 'not found' if isinstance(exc.__cause__, FileNotFoundError) else 'unavailable or invalid'
        raise BriefRunError(f'Goal registry {reason}: {registry_path}.') from exc
    try:
        events = load_events(history_path)
    except (OSError, sqlite3.Error, ValueError, TypeError, RecursionError) as exc:
        raise BriefRunError(f'Completion history unavailable or invalid: {history_path}. {exc}') from exc
    try:
        projection = project_progress(registry, task_state, events)
        selection = select_objective(registry, projection)
        task_selection = select_task(registry, task_state,
                                     None if selection.objective is None else selection.objective.milestone_id)
        brief = build_brief(registry, task_state, projection, selection, task_selection)
    except (ProjectionError, ObjectiveSelectionError, TaskSelectionError, BriefError) as exc:
        raise BriefRunError(f'{type(exc).__name__}: {exc}') from exc
    return BriefRun(snapshot, task_state, registry, events, projection, selection, brief, task_selection)


def run_complete(*, snapshot_path=None, registry_path=None, history_path=None) -> bool:
    """Resolve PRIMARY ORDER from stored state and close it only after confirmation."""
    run = run_brief(snapshot_path=snapshot_path, registry_path=registry_path,
                    history_path=history_path)
    task = run.task_selection.primary
    if task is None:
        raise BriefRunError('No PRIMARY ORDER to complete')
    if task.id.source != 'todoist':
        raise BriefRunError('PRIMARY ORDER is not a Todoist task')
    print(f'PRIMARY ORDER\n{task.title}\n')
    try:
        answer = input('Mark this task complete in Todoist? [y/N] ')
    except (EOFError, KeyboardInterrupt):
        answer = ''
        print()
    if answer.strip().lower() not in ('y', 'yes'):
        print('Completion cancelled.')
        return False
    close_task(os.environ.get('TODOIST_API_TOKEN'), task.id.source_id)
    print('Completed in Todoist.\nRun `sync` to refresh GHOST state.')
    return True


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('brief', help='Show the current PRIMARY ORDER')
    commands.add_parser('complete', help='Confirm and complete the current PRIMARY ORDER in Todoist')
    args = parser.parse_args(argv)
    try:
        if args.command == 'complete':
            run_complete()
            return 0
        run = run_brief()
        output = render_brief(run.brief)
    except (BriefRunError, BriefError, TodoistWriteError) as exc:
        print(f'GHOST ERROR\n{exc}', file=sys.stderr)
        return 1
    print(output, end='')
    return 0


if __name__ == '__main__':
    sys.exit(main())
