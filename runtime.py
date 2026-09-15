"""Read stored reality and run the pure GHOST brief pipeline."""
import argparse
from dataclasses import dataclass
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


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('brief',))
    parser.parse_args(argv)
    try:
        run = run_brief()
        output = render_brief(run.brief)
    except (BriefRunError, BriefError) as exc:
        print(f'GHOST ERROR\n{exc}', file=sys.stderr)
        return 1
    print(output, end='')
    return 0


if __name__ == '__main__':
    sys.exit(main())
