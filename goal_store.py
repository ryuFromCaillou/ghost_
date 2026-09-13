"""Strict JSON persistence for the goal registry; inspect counts with -m goal_store."""
from dataclasses import asdict
import json
import os
from pathlib import Path
import sys
import tempfile

if __package__:
    from .goals import Goal, GoalRegistry, Milestone, TaskMilestoneLink
else:
    from goals import Goal, GoalRegistry, Milestone, TaskMilestoneLink

DEFAULT_PATH = Path.home() / 'ghost/state/goals.json'
_MODELS = {'goals': Goal, 'milestones': Milestone, 'task_links': TaskMilestoneLink}


class GoalStoreError(Exception):
    """A registry could not be loaded or saved; the cause contains details."""


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON key')
        result[key] = value
    return result


def _invalid_constant(value):
    raise ValueError('Nonstandard JSON numeric constant')


def load_goal_registry(path=DEFAULT_PATH) -> GoalRegistry:
    """Load an existing UTF-8 file without repairs or inferred associations."""
    try:
        with Path(path).open(encoding='utf-8') as stream:
            payload = json.load(stream, object_pairs_hook=_object,
                                parse_constant=_invalid_constant)
        if not isinstance(payload, dict) or set(payload) != set(_MODELS):
            raise ValueError('Expected exactly goals, milestones, and task_links')
        collections = {}
        for name, model in _MODELS.items():
            rows = payload[name]
            if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
                raise ValueError(f'{name} must be an array of objects')
            collections[name] = [model(**row) for row in rows]
        return GoalRegistry(**collections)
    except (OSError, TypeError, ValueError, RecursionError) as exc:
        raise GoalStoreError('Could not load goal registry') from exc


def save_goal_registry(registry, path=DEFAULT_PATH):
    """Serialize in collection order and atomically replace the destination."""
    temporary = None
    try:
        if not isinstance(registry, GoalRegistry):
            raise TypeError('Expected GoalRegistry')
        serialized = json.dumps(asdict(registry), ensure_ascii=False, sort_keys=True,
                                indent=2, allow_nan=False) + '\n'
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', newline='\n',
                                         dir=destination.parent, prefix='.goals-',
                                         suffix='.tmp', delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(serialized)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
    except (OSError, TypeError, ValueError, RecursionError) as exc:
        raise GoalStoreError('Could not save goal registry') from exc
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main():
    try:
        registry = load_goal_registry()
    except GoalStoreError:
        print('Could not load goal registry', file=sys.stderr)
        return 1
    print(f'Goals: {len(registry.goals)}')
    print(f'Milestones: {len(registry.milestones)}')
    print(f'Task links: {len(registry.task_links)}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
