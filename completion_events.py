"""Provider-independent completion evidence and append-only local storage.

Kept beside the adapter so this independently versioned integration contains the
whole history path; this module has no Todoist API or snapshot dependencies.
"""
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from types import MappingProxyType
from typing import Any

from ..task_state import SourceIdentity


def _freeze(value):
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    return value


def _plain(value):
    if isinstance(value, Mapping):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value


@dataclass(frozen=True)
class TaskCompletionEvent:
    task_id: SourceIdentity
    completed_at: datetime
    title: str
    project_id: SourceIdentity | None
    section_id: SourceIdentity | None
    parent_id: SourceIdentity | None
    source: str
    raw: Mapping[str, Any]

    def __post_init__(self):
        if not isinstance(self.source, str) or not self.source:
            raise ValueError('Expected nonempty source')
        if self.task_id is None:
            raise ValueError('Task identity is required')
        for identity in (self.task_id, self.project_id, self.section_id, self.parent_id):
            if identity is None:
                continue
            if (not isinstance(identity, SourceIdentity) or identity.source != self.source
                    or not isinstance(identity.source_id, str) or not identity.source_id):
                raise ValueError('Invalid event identity')
        if (not isinstance(self.completed_at, datetime)
                or self.completed_at.utcoffset() is None):
            raise ValueError('Completion time must be timezone-aware')
        if not isinstance(self.title, str) or not isinstance(self.raw, Mapping):
            raise ValueError('Invalid event content')
        # JSON round trip both copies caller-owned data and rejects non-JSON values.
        raw = json.loads(json.dumps(_plain(self.raw), allow_nan=False))
        object.__setattr__(self, 'raw', _freeze(raw))
        object.__setattr__(self, 'completed_at', self.completed_at.astimezone(timezone.utc))


_SCHEMA = '''CREATE TABLE IF NOT EXISTS task_completion_events (
    source TEXT NOT NULL,
    task_id TEXT NOT NULL,
    completed_at TEXT NOT NULL,
    title TEXT NOT NULL,
    project_id TEXT,
    section_id TEXT,
    parent_id TEXT,
    raw TEXT NOT NULL,
    PRIMARY KEY (source, task_id, completed_at)
)'''


def append_events(path, events):
    """Commit a complete batch atomically; first observed evidence wins on replay."""
    rows = []
    for event in events:
        rows.append((event.source, event.task_id.source_id, event.completed_at.isoformat(),
                     event.title, *(None if ident is None else ident.source_id for ident in
                                    (event.project_id, event.section_id, event.parent_id)),
                     json.dumps(_plain(event.raw), ensure_ascii=False, allow_nan=False)))
    path = Path(path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=30)
    try:
        with connection:
            connection.execute(_SCHEMA)
            before = connection.total_changes
            connection.executemany('''INSERT INTO task_completion_events VALUES
                (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(source, task_id, completed_at) DO NOTHING''', rows)
            return connection.total_changes - before
    finally:
        connection.close()


def load_events(path):
    """Read durable evidence offline without creating or mutating the database."""
    uri = Path(path).expanduser().absolute().as_uri() + '?mode=ro'
    connection = sqlite3.connect(uri, uri=True, timeout=30)
    try:
        rows = connection.execute('''SELECT source, task_id, completed_at, title,
            project_id, section_id, parent_id, raw FROM task_completion_events
            ORDER BY completed_at, source, task_id''').fetchall()
        return tuple(TaskCompletionEvent(
            SourceIdentity(source, task), datetime.fromisoformat(stamp), title,
            *(None if ident is None else SourceIdentity(source, ident)
              for ident in (project, section, parent)), source, json.loads(raw))
            for source, task, stamp, title, project, section, parent, raw in rows)
    finally:
        connection.close()
