"""Explicit Todoist writes; no local state changes or retries."""
from http.client import HTTPException
import re
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener


class TodoistWriteError(Exception):
    """Sanitized provider write failure."""


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def close_task(token: str, task_id: str) -> None:
    """Close one task accepting only HTTP 200 or 204 as success."""
    if not isinstance(token, str) or not re.fullmatch(r'[\x21-\x7e]+', token):
        raise TodoistWriteError('TODOIST_API_TOKEN is missing or invalid; export it before completing')
    if not isinstance(task_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]+', task_id):
        raise TodoistWriteError('Invalid Todoist task ID')
    request = Request(f'https://api.todoist.com/api/v1/tasks/{task_id}/close',
                      headers={'Authorization': f'Bearer {token}'}, method='POST')
    try:
        with build_opener(NoRedirect()).open(request, timeout=30) as response:
            if response.status not in (200, 204):
                raise TodoistWriteError(
                    f'Todoist close failed: unexpected response status {response.status}')
    except HTTPError as exc:
        raise TodoistWriteError(f'Todoist close failed: HTTP {exc.code}') from None
    except (URLError, OSError, HTTPException):
        raise TodoistWriteError('Todoist close failed: network failure') from None
