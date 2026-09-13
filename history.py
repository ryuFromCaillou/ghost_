"""Read-only Todoist completion ingestion, independent of active snapshots.

Run from ~/ghost with python3 -m integrations.todoist.history.
"""
import argparse
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sqlite3
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener

from ..task_state import SourceIdentity
from .paths import TODOIST_HISTORY_DB_PATH
from .completion_events import TaskCompletionEvent, append_events, load_events

API = 'https://api.todoist.com/api/v1/tasks/completed/by_completion_date'
DEFAULT_HISTORY = TODOIST_HISTORY_DB_PATH
WINDOW = timedelta(days=30)  # Safely below the API's three-month range maximum.


class HistoryError(Exception):
    """Completion ingestion failed; no partial batch is published."""


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _timestamp(value):
    if not isinstance(value, str) or 'T' not in value:
        raise ValueError('Expected ISO datetime')
    result = datetime.fromisoformat(value)
    if result.utcoffset() is None:
        raise ValueError('Expected timezone-aware datetime')
    return result.astimezone(timezone.utc)


def normalize_completion(row):
    """Use endpoint evidence, never infer completion from snapshot disappearance."""
    try:
        if not isinstance(row, dict):
            raise ValueError('Expected task object')
        def identity(value):
            return None if value is None else SourceIdentity('todoist', value)
        return TaskCompletionEvent(
            identity(row['id']), _timestamp(row['completed_at']), row['content'],
            identity(row.get('project_id')), identity(row.get('section_id')),
            identity(row.get('parent_id')), 'todoist', row)
    except (KeyError, TypeError, ValueError, OverflowError, RecursionError):
        raise HistoryError('Invalid completed task') from None


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON key')
        result[key] = value
    return result


def _invalid_constant(value):
    raise ValueError('Non-finite JSON number')


def fetch_completions(token, since, until, page_size=200, opener=None):
    """Fetch all pages in [since, until), splitting long ranges into 30-day windows."""
    if not isinstance(token, str) or not token.strip():
        raise HistoryError('TODOIST_API_TOKEN is missing or empty')
    if (not isinstance(since, datetime) or not isinstance(until, datetime)
            or since.utcoffset() is None or until.utcoffset() is None or since >= until):
        raise HistoryError('Expected timezone-aware since < until')
    if type(page_size) is not int or not 1 <= page_size <= 200:
        raise HistoryError('Page size must be between 1 and 200')
    since, until = since.astimezone(timezone.utc), until.astimezone(timezone.utc)
    opener = opener or build_opener(NoRedirect())
    events = []
    while since < until:
        end = min(since + WINDOW, until)
        cursor, cursors = None, set()
        while True:
            params = dict(since=since.isoformat(), until=end.isoformat(), limit=page_size)
            if cursor is not None:
                params['cursor'] = cursor
            request = Request(API + '?' + urlencode(params), method='GET', headers={
                'Authorization': f'Bearer {token}', 'Accept': 'application/json'})
            try:
                with opener.open(request, timeout=30) as response:
                    payload = json.load(response, object_pairs_hook=_object,
                                        parse_constant=_invalid_constant)
            except HTTPError as exc:
                raise HistoryError(f'Completion API: HTTP {exc.code}') from None
            except (URLError, TimeoutError, OSError):
                raise HistoryError('Completion API: network failure') from None
            except (ValueError, UnicodeError, RecursionError):
                raise HistoryError('Completion API: invalid JSON') from None
            if (not isinstance(payload, dict) or not isinstance(payload.get('items'), list)
                    or 'next_cursor' not in payload):
                raise HistoryError('Invalid completion page')
            for row in payload['items']:
                event = normalize_completion(row)
                if not since <= event.completed_at < end:
                    raise HistoryError('Completion timestamp outside requested window')
                events.append(event)
            cursor = payload['next_cursor']
            if cursor is None:
                break
            if not isinstance(cursor, str) or not cursor or cursor in cursors:
                raise HistoryError('Invalid or repeated completion cursor')
            cursors.add(cursor)
        since = end
    return tuple(events)


def ingest_history(token, since, until, path=DEFAULT_HISTORY, page_size=200, opener=None):
    events = fetch_completions(token, since, until, page_size, opener)
    try:
        return append_events(path, events)
    except (OSError, sqlite3.Error, ValueError):
        raise HistoryError('Completion history could not be written') from None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--since', required=True, type=_timestamp,
                        help='Inclusive ISO datetime with timezone')
    parser.add_argument('--until', type=_timestamp, default=datetime.now(timezone.utc),
                        help='Exclusive ISO datetime with timezone; default: now')
    parser.add_argument('--database', type=Path, default=DEFAULT_HISTORY)
    parser.add_argument('--page-size', type=int, default=200)
    args = parser.parse_args()
    try:
        count = ingest_history(os.environ.get('TODOIST_API_TOKEN'), args.since, args.until,
                               args.database, args.page_size)
    except HistoryError as exc:
        parser.exit(1, f'Todoist completion ingestion failed: {exc}\n')
    print(f'Todoist completion history: {count} new events')


if __name__ == '__main__':
    main()
