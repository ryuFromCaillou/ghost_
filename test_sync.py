import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlparse

import sync


class FakeAPI:
    def __init__(self, fail=False):
        self.requests = []
        self.fail = fail

    def open(self, request, timeout):
        self.requests.append(request)
        assert request.get_method() == 'GET'
        resource = urlparse(request.full_url).path.rsplit('/', 1)[-1]
        query = parse_qs(urlparse(request.full_url).query)
        if self.fail and resource == 'sections':
            raise HTTPError(request.full_url, 503, 'unavailable', {}, None)
        page = 2 if 'cursor' in query else 1
        item = {'id': f'{resource}-{page}', 'name': 'Readable café',
                'project_id': 'projects-1', 'section_id': 'sections-1',
                'parent_id': 'tasks-1' if page == 2 else None,
                'unknown_field': {'raw': True}}
        return io.BytesIO(json.dumps({'results': [item], 'next_cursor': 'a+b/=' if page == 1 else None}).encode())


class SyncTests(unittest.TestCase):
    def test_full_import_and_rerun(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'todoist'
            api = FakeAPI()
            metadata = sync.sync('test', output, opener=api)
            previous = output.resolve()
            self.assertEqual(metadata['page_counts'], dict.fromkeys(sync.RESOURCES, 2))
            self.assertEqual(len(api.requests), 6)
            tasks = json.loads((output / 'tasks.json').read_text())
            self.assertEqual(tasks[1]['parent_id'], 'tasks-1')
            self.assertEqual(tasks[1]['unknown_field'], {'raw': True})
            self.assertEqual(parse_qs(urlparse(api.requests[1].full_url).query)['cursor'], ['a+b/='])
            sync.sync('test', output, opener=FakeAPI())
            self.assertNotEqual(previous, output.resolve())
            self.assertEqual(tasks, json.loads((output / 'tasks.json').read_text()))

    def test_failures_preserve_every_file(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'todoist'
            sync.sync('test', output, opener=FakeAPI())
            original = output.resolve()
            before = {p.name: p.read_bytes() for p in output.iterdir()}
            with self.assertRaises(sync.SyncError):
                sync.sync('test', output, opener=FakeAPI(fail=True))
            for target in ('sync.json.dump', 'sync.os.replace'):
                with patch(target, side_effect=OSError('injected disk failure')):
                    with self.assertRaises(OSError):
                        sync.sync('test', output, opener=FakeAPI())
                self.assertEqual(original, output.resolve())
                self.assertEqual(before, {p.name: p.read_bytes() for p in output.iterdir()})

    def test_missing_token(self):
        with self.assertRaisesRegex(sync.SyncError, 'TODOIST_API_TOKEN'):
            sync.sync('')

    def test_malformed_pages_and_cursor_loops(self):
        for payloads in ([{}], [{'results': [], 'next_cursor': 5}],
                         [{'results': [], 'next_cursor': 'same'}] * 2,
                         [{'results': [{'id': '1'}], 'next_cursor': 'next'},
                          {'results': [{'id': '1'}]}]):
            with self.subTest(payloads=payloads):
                api = FakeAPI()
                with patch.object(api, 'open', side_effect=[io.BytesIO(json.dumps(p).encode()) for p in payloads]):
                    with self.assertRaises(sync.SyncError):
                        sync.fetch_all('tasks', 'test', opener=api)

    def test_absent_cursor_and_empty_collection(self):
        api = FakeAPI()
        with patch.object(api, 'open', return_value=io.BytesIO(b'{"results": []}')):
            self.assertEqual(sync.fetch_all('sections', 'test', opener=api), ([], 1))


if __name__ == '__main__':
    unittest.main()
