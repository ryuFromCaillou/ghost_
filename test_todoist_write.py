import io
from http.client import BadStatusLine
import traceback
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener

import todoist_write as writer


class TodoistWriteTests(unittest.TestCase):
    def setUp(self):
        self.build = self.enterContext(patch.object(writer, 'build_opener'))
        self.opener = self.build.return_value
        self.response = self.opener.open.return_value.__enter__.return_value
        self.response.status = 200

    def test_post_auth_timeout_and_success(self):
        self.assertIsNone(writer.close_task('secret-token', '6XGgm6PHrGgMpCFX'))
        self.opener.open.assert_called_once()
        request = self.opener.open.call_args.args[0]
        self.assertEqual(request.full_url,
                         'https://api.todoist.com/api/v1/tasks/6XGgm6PHrGgMpCFX/close')
        self.assertEqual(request.get_method(), 'POST')
        self.assertEqual(request.get_header('Authorization'), 'Bearer secret-token')
        self.assertEqual(self.opener.open.call_args.kwargs, {'timeout': 30})
        self.assertIsInstance(self.build.call_args.args[0], writer.NoRedirect)
        self.response.read.assert_not_called()

    def test_invalid_credentials_before_io(self):
        for token in (None, '', ' ', 'bad\ntoken', 'bad\rtoken', 'bad token', 'é', 12):
            with self.subTest(token=token), self.assertRaises(writer.TodoistWriteError):
                writer.close_task(token, '123')
        self.build.assert_not_called()

    def test_204_success_without_reading_body_or_retrying(self):
        self.response.status = 204
        self.assertIsNone(writer.close_task('secret-token', '123'))
        self.opener.open.assert_called_once()
        self.response.read.assert_not_called()

    def test_invalid_ids_before_io(self):
        for task_id in (None, '', ' ', '../123', 'a/b', 'a?b', 'a#b', 'a%2fb', 'a\nb', 123):
            with self.subTest(task_id=task_id), self.assertRaises(writer.TodoistWriteError):
                writer.close_task('secret-token', task_id)
        self.build.assert_not_called()

    def test_failures_sanitized_and_never_retried(self):
        secret = 'secret-token'
        body = 'private-response-body'
        for failure in (HTTPError('https://example/' + secret, 403, secret, {},
                                  io.BytesIO(body.encode())),
                        URLError(secret + body), TimeoutError(secret + body),
                        OSError(secret + body), BadStatusLine(secret + body)):
            with self.subTest(failure=type(failure).__name__):
                self.opener.open.reset_mock()
                self.opener.open.side_effect = failure
                try:
                    writer.close_task(secret, '123')
                except writer.TodoistWriteError:
                    error = traceback.format_exc()
                else:
                    self.fail('Expected write error')
                self.assertNotIn(secret, error)
                self.assertNotIn(body, error)
                self.opener.open.assert_called_once()

    def test_unexpected_status(self):
        secret = 'secret-token'
        body = 'private-response-body'
        self.response.read.return_value = body.encode()
        for status in (201, 202, 205, 206, 299, 302, 404, 500):
            self.opener.open.reset_mock()
            with self.subTest(status=status), self.assertRaises(writer.TodoistWriteError) as caught:
                self.response.status = status
                writer.close_task(secret, '123')
            message = str(caught.exception)
            self.assertEqual(message, f'Todoist close failed: unexpected response status {status}')
            self.assertNotIn(body, message)
            self.assertNotIn(secret, message)
            self.assertNotIn('Authorization', message)
            self.response.read.assert_not_called()
            self.opener.open.assert_called_once()

    def test_redirects_refused_without_followup_request(self):
        handler = writer.NoRedirect()
        opener = build_opener(handler)
        followup = self.enterContext(patch.object(opener, 'open'))
        request = Request('https://api.todoist.com/api/v1/tasks/123/close', method='POST')
        for status in (301, 302, 303, 307, 308):
            with self.subTest(status=status), self.assertRaises(HTTPError):
                opener.error(
                    'http', request, io.BytesIO(), status, 'redirect',
                    {'location': 'https://other.example/steal'})
        followup.assert_not_called()


class TodoistCreateTests(unittest.TestCase):
    def setUp(self):
        import json
        self.token = 'secret-token'
        self.json = json
        self.build = self.enterContext(patch.object(writer, 'build_opener'))
        self.opener = self.build.return_value
        self.response = self.opener.open.return_value.__enter__.return_value
        self.response.status = 200
        self.receipt()

    def receipt(self, **updates):
        data = dict(id='provider-id', content='Parent', parent_id=None)
        data.update(updates)
        self.response.read.return_value = self.json.dumps(data).encode()

    def payload(self):
        return self.json.loads(self.opener.open.call_args.args[0].data)

    def test_root_request_and_receipt(self):
        result = writer.create_task(self.token, 'Parent')
        self.assertEqual(result, writer.CreatedTask('provider-id', 'Parent', None))
        self.assertEqual(self.payload(), {'content': 'Parent'})
        request = self.opener.open.call_args.args[0]
        self.assertEqual(request.full_url, 'https://api.todoist.com/api/v1/tasks')
        self.assertEqual(request.get_method(), 'POST')
        self.assertEqual(request.get_header('Authorization'), 'Bearer secret-token')
        self.assertEqual(request.get_header('Content-type'), 'application/json')
        self.assertEqual(request.get_header('Accept'), 'application/json')
        self.assertEqual(self.opener.open.call_args.kwargs, {'timeout': 30})
        self.assertIsInstance(self.build.call_args.args[0], writer.NoRedirect)
        self.opener.open.assert_called_once()

    def test_child_request(self):
        self.receipt(content='Child', parent_id='123')
        result = writer.create_task(self.token, 'Child', parent_id='123')
        self.assertEqual(self.payload(), {'content':'Child', 'parent_id':'123'})
        self.assertEqual(result.parent_id, '123')

    def test_returns_actual_api_values_without_fabrication(self):
        self.receipt(id='actual-id', content='Server content', parent_id='actual-parent')
        result = writer.create_task(self.token, 'Requested', parent_id='requested-parent')
        self.assertEqual(result, writer.CreatedTask('actual-id', 'Server content', 'actual-parent'))

    def test_optional_fields(self):
        fields = dict(parent_id='parent', description='Details é', project_id='project',
                      section_id='section', due_string='tomorrow', priority=4)
        writer.create_task(self.token, 'Child é', **fields)
        self.assertEqual(self.payload(), dict(content='Child é', **fields))

    def test_due_date_and_empty_description(self):
        writer.create_task(self.token, 'Parent', due_date='2026-09-30', description='')
        self.assertEqual(self.payload(), dict(content='Parent', due_date='2026-09-30', description=''))

    def test_none_fields_omitted(self):
        writer.create_task(self.token, 'Parent', parent_id=None, description=None, priority=None)
        self.assertEqual(self.payload(), {'content':'Parent'})

    def test_invalid_credentials_before_io(self):
        for token in (None, '', ' ', 'bad\ntoken', 'bad token', 'é', 12):
            with self.subTest(token=token), self.assertRaises(writer.TodoistWriteError):
                writer.create_task(token, 'Parent')
        self.build.assert_not_called()

    def test_invalid_content_before_io(self):
        for content in (None, '', '  ', 123):
            with self.subTest(content=content), self.assertRaises(writer.TodoistWriteError):
                writer.create_task(self.token, content)
        self.build.assert_not_called()

    def test_invalid_options_before_io(self):
        options = [dict(parent_id='../123'), dict(project_id=123), dict(section_id=''),
                   dict(description=12), dict(due_string=''), dict(due_date='2026-02-30'),
                   dict(due_date='20260930'), dict(due_date='private-response-body'),
                   dict(priority=True), dict(priority=0), dict(priority=5), dict(priority='1'),
                   dict(due_string='tomorrow', due_date='2026-09-30')]
        for option in options:
            with self.subTest(option=option), self.assertRaises(writer.TodoistWriteError):
                writer.create_task(self.token, 'Parent', **option)
        self.build.assert_not_called()

    def test_http_failure_invalid_parent_and_redirect_redacted(self):
        for status in (400, 401, 403, 404, 429, 500, 301, 302, 303, 307, 308):
            self.opener.open.reset_mock()
            self.opener.open.side_effect = HTTPError('https://example/secret-token', status,
                'secret-token', {}, io.BytesIO(b'private-response-body'))
            with self.subTest(status=status):
                try:
                    writer.create_task(self.token, 'Child', parent_id='missing-parent')
                except writer.TodoistWriteError as exc:
                    self.assertEqual(str(exc), f'Todoist create failed: HTTP {status}')
                    error = traceback.format_exc()
                    self.assertNotIn('secret-token', error)
                    self.assertNotIn('private-response-body', error)
                else:
                    self.fail('Creation failure must propagate')
                self.opener.open.assert_called_once()
                self.response.read.assert_not_called()

    def test_network_failures_redacted_no_retry(self):
        for failure in (URLError('secret-token private-response-body'),
                        TimeoutError('secret-token'), OSError('private-response-body'),
                        BadStatusLine('private-response-body')):
            self.opener.open.reset_mock()
            self.opener.open.side_effect = failure
            try:
                writer.create_task(self.token, 'Parent')
            except writer.TodoistWriteError as exc:
                self.assertIn('remote outcome unverified', str(exc))
                error = traceback.format_exc()
                self.assertNotIn('secret-token', error)
                self.assertNotIn('private-response-body', error)
            else:
                self.fail('Creation failure must propagate')
            self.opener.open.assert_called_once()

    def test_unexpected_status_no_body_read(self):
        for status in (201, 202, 204, 299, 302, 404, 500):
            self.opener.open.reset_mock()
            self.response.status = status
            with self.subTest(status=status), self.assertRaises(writer.TodoistWriteError):
                writer.create_task(self.token, 'Parent')
            self.response.read.assert_not_called()
            self.opener.open.assert_called_once()

    def test_malformed_response_rejected_and_redacted(self):
        values = [None, [], {}, {'id':'x', 'content':'Parent'},
                  {'id':123, 'content':'Parent', 'parent_id':None},
                  {'id':'', 'content':'Parent', 'parent_id':None},
                  {'id':'x', 'content':12, 'parent_id':None},
                  {'id':'x', 'content':'', 'parent_id':None},
                  {'id':'x', 'content':'Parent', 'parent_id':12},
                  {'id':'x', 'content':'Parent', 'parent_id':''}]
        bodies = [self.json.dumps(v).encode() for v in values] + [
            b'private-response-body secret-token', b'\xff',
            b'{"id":"x","id":"y","content":"Parent","parent_id":null}',
            b'{"id":"x","content":"Parent","parent_id":null,"extra":NaN}']
        for body in bodies:
            self.opener.open.reset_mock()
            self.response.read.return_value = body
            try:
                writer.create_task(self.token, 'Parent')
            except writer.TodoistWriteError as exc:
                self.assertIn('malformed response', str(exc))
                self.assertNotIn('secret-token', traceback.format_exc())
                self.assertNotIn('private-response-body', traceback.format_exc())
            else:
                self.fail('Malformed response accepted')
            self.opener.open.assert_called_once()

    def test_arbitrary_nesting_uses_returned_ids(self):
        parent = None
        for depth in range(4):
            ident = f'actual-{depth}'
            self.receipt(id=ident, content=f'Level {depth}', parent_id=parent)
            result = writer.create_task(self.token, f'Level {depth}', parent_id=parent)
            self.assertEqual(self.payload().get('parent_id'), parent)
            parent = result.id
        self.assertEqual(self.opener.open.call_count, 4)

    def test_midway_failure_leaves_prior_receipts_and_stops(self):
        created = []
        with self.assertRaises(writer.TodoistWriteError):
            for name in ('Parent', 'Child', 'Grandchild'):
                if created:
                    self.opener.open.side_effect = HTTPError('https://example', 400, 'bad parent', {}, None)
                created.append(writer.create_task(self.token, name,
                                                  parent_id=created[-1].id if created else None))
        self.assertEqual(created, [writer.CreatedTask('provider-id', 'Parent', None)])
        self.assertEqual(self.opener.open.call_count, 2)
        self.assertEqual([c.args[0].get_method() for c in self.opener.open.call_args_list], ['POST', 'POST'])
