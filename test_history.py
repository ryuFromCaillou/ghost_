from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
import io
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from integrations.task_state import SourceIdentity
from integrations.todoist.history import (HistoryError, fetch_completions,
    ingest_history, normalize_completion)
from integrations.todoist.completion_events import append_events, load_events

START = datetime(2026, 1, 1, tzinfo=timezone.utc)
END = START + timedelta(days=1)


def row(stamp='2026-01-01T12:00:00Z', **updates):
    return dict(id='task-1', completed_at=stamp, content='Finished café',
                project_id='archived-project', section_id=None, parent_id='missing-parent',
                checked=False, extension={'values': [1, {'ok': True}]}, **updates)


class API:
    def __init__(self, *pages):
        self.pages = iter(pages)
        self.requests = []

    def open(self, request, timeout):
        self.requests.append(request)
        assert request.get_method() == 'GET'
        assert urlparse(request.full_url).path == '/api/v1/tasks/completed/by_completion_date'
        assert timeout == 30
        page = next(self.pages)
        if isinstance(page, Exception):
            raise page
        return io.BytesIO(json.dumps(page).encode())


def page(*rows, cursor=None):
    return {'items': list(rows), 'next_cursor': cursor}


class HistoryTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name) / 'history.sqlite3'

    def test_normalization_immutable_and_unresolved_references(self):
        raw = row()
        event = normalize_completion(raw)
        self.assertEqual(event.task_id, SourceIdentity('todoist', 'task-1'))
        self.assertEqual(event.parent_id.source_id, 'missing-parent')
        self.assertEqual(event.title, 'Finished café')
        raw['extension']['values'][1]['ok'] = False
        self.assertTrue(event.raw['extension']['values'][1]['ok'])
        with self.assertRaises(TypeError):
            event.raw['extension']['values'][1]['ok'] = False
        with self.assertRaises(FrozenInstanceError):
            event.title = 'changed'

    def test_replay_recurrence_and_first_evidence_survive_empty_import(self):
        first, second = row(), row('2026-01-01T13:00:00Z')
        api = API(page(first, cursor='a+b/='), page(first, second))
        self.assertEqual(ingest_history('secret', START, END, self.path, opener=api), 2)
        self.assertEqual(parse_qs(urlparse(api.requests[1].full_url).query)['cursor'], ['a+b/='])
        first['content'] = 'Changed later'
        first['completed_at'] = '2026-01-01T13:00:00+01:00'
        self.assertEqual(ingest_history('secret', START, END, self.path,
                                       opener=API(page(first))), 0)
        self.assertEqual(ingest_history('secret', START, END, self.path,
                                       opener=API(page())), 0)
        events = load_events(self.path)
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0], normalize_completion(row()))
        self.assertEqual(events[1], normalize_completion(second))

    def test_failed_later_page_preserves_database(self):
        append_events(self.path, [normalize_completion(row())])
        before = self.path.read_bytes()
        api = API(page(row('2026-01-01T13:00:00Z'), cursor='next'),
                  HTTPError('https://example.invalid', 503, 'secret body', {}, None))
        with self.assertRaisesRegex(HistoryError, 'HTTP 503'):
            ingest_history('secret', START, END, self.path, opener=api)
        self.assertEqual(self.path.read_bytes(), before)

    def test_terminal_pages_with_null_or_omitted_cursor(self):
        for payload in [page(row()), {'items': [row()]}, page(), {'items': []}]:
            with self.subTest(payload=payload):
                api = API(payload)
                events = fetch_completions('secret', START, END, opener=api)
                self.assertEqual(events, tuple(normalize_completion(value)
                                               for value in payload['items']))
                self.assertEqual(len(api.requests), 1)

    def test_valid_cursor_pagination(self):
        first, second = row(), row('2026-01-01T13:00:00Z')
        api = API(page(first, cursor='a+b/='), {'items': [second]})
        events = fetch_completions('secret', START, END, opener=api)
        self.assertEqual(events, (normalize_completion(first), normalize_completion(second)))
        self.assertEqual(len(api.requests), 2)
        queries = [parse_qs(urlparse(request.full_url).query) for request in api.requests]
        self.assertNotIn('cursor', queries[0])
        self.assertEqual(queries[1]['cursor'], ['a+b/='])

    def test_invalid_cursors(self):
        for cursor in [42, False, [], {}, '']:
            with self.subTest(cursor=cursor), self.assertRaisesRegex(HistoryError, 'cursor'):
                ingest_history('secret', START, END, self.path, opener=API(page(cursor=cursor)))
            self.assertFalse(self.path.exists())

    def test_missing_or_invalid_items(self):
        for items in [{}, {'items': None}, {'items': {}}, {'items': ''}, {'items': 42}]:
            for cursor in [{}, {'next_cursor': None}]:
                payload = dict(items, **cursor)
                with self.subTest(payload=payload), self.assertRaisesRegex(
                        HistoryError, 'Invalid completion page'):
                    ingest_history('secret', START, END, self.path, opener=API(payload))
                self.assertFalse(self.path.exists())

    def test_malformed_pages_and_records_publish_nothing(self):
        invalid_rows = [dict(row(), id=None), dict(row(), id=12),
                        dict(row(), completed_at=None), dict(row(), completed_at='2026-01-01'),
                        dict(row(), completed_at='2026-01-01T12:00:00'),
                        dict(row(), content=None), dict(row(), section_id=''),
                        dict(row(), completed_at='2026-01-02T12:00:00Z')]
        payloads = [page(value) for value in invalid_rows]
        payloads += [{'results': [], 'next_cursor': None}, page(cursor=42)]
        for payload in payloads:
            with self.subTest(payload=payload), self.assertRaises(HistoryError):
                ingest_history('secret', START, END, self.path, opener=API(payload))
            self.assertFalse(self.path.exists())

    def test_repeated_cursor(self):
        with self.assertRaisesRegex(HistoryError, 'cursor'):
            ingest_history('secret', START, END, self.path,
                           opener=API(page(cursor='x'), page(cursor='x')))
        self.assertFalse(self.path.exists())

    def test_long_range_windows_and_boundary(self):
        boundary = START + timedelta(days=30)
        api = API(page(), page(row(boundary.isoformat())))
        events = fetch_completions('secret', START, START + timedelta(days=31), opener=api)
        queries = [parse_qs(urlparse(req.full_url).query) for req in api.requests]
        self.assertEqual(queries[0]['until'], queries[1]['since'])
        self.assertEqual(events[0].completed_at, boundary)

    def test_database_batch_rolls_back_on_insert_failure(self):
        append_events(self.path, [normalize_completion(row())])
        with sqlite3.connect(self.path) as db:
            db.execute("""CREATE TRIGGER fail_insert BEFORE INSERT ON task_completion_events
                WHEN NEW.title = 'fail' BEGIN SELECT RAISE(ABORT, 'test failure'); END""")
        bad = row('2026-01-01T14:00:00Z')
        bad['content'] = 'fail'
        with self.assertRaises(HistoryError):
            ingest_history('secret', START, END, self.path,
                           opener=API(page(row('2026-01-01T13:00:00Z'), bad)))
        self.assertEqual(len(load_events(self.path)), 1)

    def test_offline_read_does_not_create_missing_database(self):
        with self.assertRaises(sqlite3.OperationalError):
            load_events(self.path)
        self.assertFalse(self.path.exists())

    def test_active_snapshot_is_untouched_and_history_survives_removal(self):
        from test_sync import FakeAPI
        from sync import sync
        current = self.path.parent / 'todoist'
        sync('secret', current, opener=FakeAPI())
        generation = current.resolve()
        before = {p.name: p.read_bytes() for p in generation.iterdir()}
        completed = dict(row(), id='tasks-1')
        ingest_history('secret', START, END, self.path, opener=API(page(completed)))
        self.assertEqual(current.resolve(), generation)
        self.assertEqual({p.name: p.read_bytes() for p in generation.iterdir()}, before)

        class EmptyTasksAPI(FakeAPI):
            def open(self, request, timeout):
                if urlparse(request.full_url).path.endswith('/tasks'):
                    return io.BytesIO(b'{"results": [], "next_cursor": null}')
                return super().open(request, timeout)

        sync('secret', current, opener=EmptyTasksAPI())
        self.assertEqual(json.loads((current / 'tasks.json').read_text()), [])
        self.assertEqual(load_events(self.path), (normalize_completion(completed),))

    def test_input_validation_before_io(self):
        api = API()
        for token, start, end, limit in [('', START, END, 200),
                ('secret', END, START, 200), ('secret', START.replace(tzinfo=None), END, 200),
                ('secret', START, END, 0)]:
            with self.assertRaises(HistoryError):
                ingest_history(token, start, end, self.path, limit, api)
        self.assertEqual(api.requests, [])
        self.assertFalse(self.path.exists())


if __name__ == '__main__':
    unittest.main()
