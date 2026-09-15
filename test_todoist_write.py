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
