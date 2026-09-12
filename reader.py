"""Load one published Todoist generation without contacting Todoist."""
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
import json
from pathlib import Path
from types import MappingProxyType
from typing import Any

if __package__:
    from .sync import DEFAULT_OUTPUT, RESOURCES
else:
    from sync import DEFAULT_OUTPUT, RESOURCES

REQUIRED_FILES = tuple(f"{name}.json" for name in RESOURCES) + ("sync_metadata.json",)


class SnapshotReadError(Exception):
    """The complete snapshot could not be loaded and validated."""


@dataclass(frozen=True)
class TodoistSnapshot:
    directory: Path
    projects: tuple[Mapping[str, Any], ...]
    sections: tuple[Mapping[str, Any], ...]
    tasks: tuple[Mapping[str, Any], ...]
    metadata: Mapping[str, Any]


def _require(condition, message):
    if not condition:
        raise SnapshotReadError(message)


def _object(pairs):
    result = {}
    for key, value in pairs:
        _require(key not in result, "Duplicate JSON object key")
        result[key] = value
    return result


def _invalid_constant(value):
    raise SnapshotReadError("Non-finite JSON number")


def _read_json(path):
    with path.open(encoding="utf-8") as stream:
        return json.load(stream, object_pairs_hook=_object, parse_constant=_invalid_constant)


def _freeze(value):
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


def _validate(data):
    indexes = {}
    for resource in RESOURCES:
        rows = data[resource]
        _require(isinstance(rows, list), f"{resource}: expected an array")
        index = {}
        for row in rows:
            _require(isinstance(row, dict), f"{resource}: expected object entries")
            ident = row.get("id")
            _require(isinstance(ident, str) and bool(ident), f"{resource}: invalid ID")
            _require(ident not in index, f"{resource}: duplicate ID")
            index[ident] = row
        indexes[resource] = index
    projects, sections, tasks = (indexes[r] for r in ("projects", "sections", "tasks"))
    for row in (*projects.values(), *sections.values()):
        _require(isinstance(row.get("name"), str), "Project/section name must be a string")
    for row in sections.values():
        _require(isinstance(row.get("project_id"), str) and row['project_id'] in projects,
                 "Section references a missing project")
    for row in tasks.values():
        _require(isinstance(row.get("project_id"), str) and row['project_id'] in projects,
                 "Task references a missing project")
        for key in ("section_id", "parent_id"):
            _require(key in row and (row[key] is None or isinstance(row[key], str)),
                     f"Task has missing or invalid {key}")
        section = row['section_id']
        if section is not None:
            _require(section in sections, "Task references a missing section")
            _require(sections[section]['project_id'] == row['project_id'],
                     "Task section belongs to a different project")
        parent = row['parent_id']
        if parent is not None:
            _require(parent in tasks, "Task references a missing parent")
            _require(tasks[parent].get('project_id') == row['project_id'],
                     "Task parent belongs to a different project")
        _require(isinstance(row.get('content'), str), "Task content must be a string")
        _require(type(row.get('checked')) is bool, "Task checked must be a boolean")
        _require(isinstance(row.get('labels'), list) and
                 all(isinstance(label, str) for label in row['labels']), "Task labels must be strings")
        for key in ('due', 'deadline', 'duration'):
            _require(key in row and (row[key] is None or isinstance(row[key], dict)),
                     f"Task {key} must be an object or null")
    # Iterative traversal also handles arbitrarily deep valid hierarchies.
    done = set()
    for ident in tasks:
        chain = set()
        while ident is not None and ident not in done:
            _require(ident not in chain, "Task parent hierarchy contains a cycle")
            chain.add(ident)
            ident = tasks[ident]['parent_id']
        done.update(chain)
    metadata = data['sync_metadata']
    _require(isinstance(metadata, dict), "Metadata must be an object")
    _require(metadata.get('source') == 'todoist', "Invalid metadata source")
    timestamp = metadata.get('synced_at')
    _require(isinstance(timestamp, str), "Missing snapshot timestamp")
    try:
        parsed = datetime.fromisoformat(timestamp)
    except ValueError:
        raise SnapshotReadError("Invalid snapshot timestamp") from None
    _require(parsed.utcoffset() == timedelta(0), "Snapshot timestamp must be UTC")
    for resource in RESOURCES:
        count = metadata.get(resource[:-1] + '_count')
        _require(type(count) is int and count == len(data[resource]),
                 f"Metadata {resource} count does not match snapshot")
    if 'page_counts' in metadata:
        pages = metadata['page_counts']
        _require(isinstance(pages, dict) and all(type(pages.get(r)) is int and pages[r] > 0
                                               for r in RESOURCES), "Invalid metadata page counts")


def load_snapshot(current=DEFAULT_OUTPUT) -> TodoistSnapshot:
    """Resolve current once; return deeply immutable raw data or SnapshotReadError.

    Published generation directories must remain unchanged and available for the
    duration of a read, as guaranteed by the existing publisher's retention policy.
    """
    try:
        current = Path(current).expanduser()
        _require(current.is_symlink(), "Current snapshot must be an existing symlink")
        directory = current.resolve(strict=True)  # Only resolution in this operation.
        _require(directory.is_dir(), "Snapshot target is not a directory")
        paths = {name: directory / name for name in REQUIRED_FILES}
        for name, path in paths.items():
            _require(not path.is_symlink() and path.is_file(),
                     f"Required snapshot file missing or not a regular file: {name}")
        data = {name.removesuffix('.json'): _read_json(path) for name, path in paths.items()}
        _validate(data)
        return TodoistSnapshot(directory=directory, projects=_freeze(data['projects']),
                               sections=_freeze(data['sections']), tasks=_freeze(data['tasks']),
                               metadata=_freeze(data['sync_metadata']))
    except SnapshotReadError:
        raise
    except (OSError, ValueError, RuntimeError, RecursionError) as exc:
        raise SnapshotReadError(f"Cannot read Todoist snapshot ({type(exc).__name__})") from None
