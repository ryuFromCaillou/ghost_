"""Read stored task trees for a GHOST brief or explicitly complete PRIMARY ORDER."""
import argparse
from dataclasses import asdict, dataclass
import json
import os
import sys

from ..task_state import GhostTaskState
from . import paths
from .brief import GhostBrief, build_brief, render_brief
from .strategic_context import StrategicContext, StrategicContextError, load_strategic_context
from .normalize import NormalizationError, normalize_todoist_snapshot
from .task_selection import TaskSelection, TaskSelectionError, select_task
from .reader import SnapshotReadError, TodoistSnapshot, load_snapshot
from .todoist_write import TodoistWriteError, close_task, create_task


class BriefRunError(Exception):
    """Concise operator-facing failure."""


@dataclass(frozen=True)
class BriefRun:
    snapshot: TodoistSnapshot
    task_state: GhostTaskState
    context: StrategicContext
    task_selection: TaskSelection
    brief: GhostBrief


def run_brief(*, snapshot_path=None, context_path=None) -> BriefRun:
    """Read existing state only; history is not an actionability input."""
    snapshot_path = paths.TODOIST_EXTERNAL_STATE_ROOT if snapshot_path is None else snapshot_path
    context_path = paths.STRATEGIC_CONTEXT_PATH if context_path is None else context_path
    try:
        snapshot = load_snapshot(snapshot_path)
    except SnapshotReadError as exc:
        raise BriefRunError('Todoist snapshot unavailable or invalid') from exc
    try:
        task_state = normalize_todoist_snapshot(snapshot)
    except NormalizationError as exc:
        raise BriefRunError('Normalization failed') from exc
    try:
        context = load_strategic_context(context_path)
    except StrategicContextError as exc:
        raise BriefRunError(str(exc)) from exc
    try:
        selection = select_task(task_state, projects=snapshot.projects, sections=snapshot.sections)
    except TaskSelectionError as exc:
        raise BriefRunError(str(exc)) from exc
    return BriefRun(snapshot, task_state, context, selection, build_brief(context, selection))


def run_complete(*, snapshot_path=None, context_path=None) -> bool:
    """Resolve PRIMARY ORDER from stored state and close it only after confirmation."""
    run = run_brief(snapshot_path=snapshot_path, context_path=context_path)
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
    create = commands.add_parser('create', help='Create one Todoist task; print its creation receipt as JSON')
    create.add_argument('content', help='Task content')
    create.add_argument('--parent', dest='parent_id', help='Todoist parent task ID')
    create.add_argument('--description')
    create.add_argument('--project', dest='project_id')
    create.add_argument('--section', dest='section_id')
    due = create.add_mutually_exclusive_group()
    due.add_argument('--due-string')
    due.add_argument('--due-date', help='YYYY-MM-DD')
    create.add_argument('--priority', type=int, choices=range(1, 5), help='Raw Todoist API priority (1–4)')
    create.add_argument('--yes', action='store_true', help='Explicitly authorize this creation without prompting')
    args = parser.parse_args(argv)
    try:
        if args.command == 'create':
            if not args.yes:
                print(f'CREATE TASK\n{args.content}', file=sys.stderr)
                if args.parent_id is not None:
                    print(f'PARENT\n{args.parent_id}', file=sys.stderr)
                print('Create this task in Todoist? [y/N] ', end='', file=sys.stderr, flush=True)
                try:
                    answer = input()
                except (EOFError, KeyboardInterrupt):
                    answer = ''
                if answer.strip().lower() not in ('y', 'yes'):
                    print('Creation cancelled.', file=sys.stderr)
                    return 0
            created = create_task(os.environ.get('TODOIST_API_TOKEN'), args.content,
                                  parent_id=args.parent_id, description=args.description,
                                  project_id=args.project_id, section_id=args.section_id,
                                  due_string=args.due_string, due_date=args.due_date,
                                  priority=args.priority)
            print(json.dumps(asdict(created)))
            return 0
        if args.command == 'complete':
            run_complete()
            return 0
        run = run_brief()
        output = render_brief(run.brief)
    except (BriefRunError, TodoistWriteError) as exc:
        print(f'GHOST ERROR\n{exc}', file=sys.stderr)
        return 1
    print(output, end='')
    return 0


if __name__ == '__main__':
    sys.exit(main())
