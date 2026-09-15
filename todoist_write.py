"""Explicit Todoist writes; no local state changes or retries."""
from http.client import HTTPException
from dataclasses import dataclass
from datetime import date
import json
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


@dataclass(frozen=True)
class CreatedTask:
    """Validated creation receipt; values come from Todoist, not a local snapshot."""

    id: str
    content: str
    parent_id: str | None


def _valid_id(value):
    return isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_-]+', value) is not None


def _response_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON key')
        result[key] = value
    return result


def _invalid_constant(value):
    raise ValueError('Non-finite JSON number')


def create_task(token: str, content: str, *, parent_id=None, description=None,
                project_id=None, section_id=None, due_string=None, due_date=None,
                priority=None) -> CreatedTask:
    """Create one task; callers chain returned IDs for children and stop on error.

    No retries, rollback, snapshot updates, or implicit reads. A lost/malformed
    response can leave a remotely created task: inspect Todoist before retrying.
    """
    if not isinstance(token, str) or not re.fullmatch(r'[\x21-\x7e]+', token):
        raise TodoistWriteError('TODOIST_API_TOKEN is missing or invalid; export it before creating')
    if not isinstance(content, str) or not content.strip():
        raise TodoistWriteError('Task content must be a nonempty string')
    payload = {'content': content}
    for name, value in (('parent_id', parent_id), ('project_id', project_id), ('section_id', section_id)):
        if value is not None:
            if not _valid_id(value):
                raise TodoistWriteError(f'Invalid Todoist {name}')
            payload[name] = value
    for name, value in (('description', description), ('due_string', due_string), ('due_date', due_date)):
        if value is not None:
            if not isinstance(value, str) or (name != 'description' and not value.strip()):
                raise TodoistWriteError(f'Invalid task {name}')
            payload[name] = value
    if due_string is not None and due_date is not None:
        raise TodoistWriteError('Specify either due_string or due_date, not both')
    if due_date is not None:
        try:
            if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', due_date):
                raise ValueError
            date.fromisoformat(due_date)
        except ValueError:
            raise TodoistWriteError('due_date must be a valid YYYY-MM-DD date') from None
    if priority is not None:
        if type(priority) is not int or not 1 <= priority <= 4:
            raise TodoistWriteError('priority must be an integer from 1 to 4')
        payload['priority'] = priority
    request = Request('https://api.todoist.com/api/v1/tasks',
                      data=json.dumps(payload).encode('utf-8'),
                      headers={'Authorization': f'Bearer {token}',
                               'Content-Type': 'application/json', 'Accept': 'application/json'},
                      method='POST')
    try:
        with build_opener(NoRedirect()).open(request, timeout=30) as response:
            if response.status != 200:
                raise TodoistWriteError(
                    f'Todoist create failed: unexpected response status {response.status}; '
                    'remote outcome unverified')
            data = json.load(response, object_pairs_hook=_response_object,
                             parse_constant=_invalid_constant)
            if (not isinstance(data, dict) or not _valid_id(data.get('id'))
                    or not isinstance(data.get('content'), str) or not data['content'].strip()
                    or 'parent_id' not in data
                    or (data['parent_id'] is not None and not _valid_id(data['parent_id']))):
                raise ValueError('Invalid creation receipt')
            return CreatedTask(data['id'], data['content'], data['parent_id'])
    except HTTPError as exc:
        raise TodoistWriteError(f'Todoist create failed: HTTP {exc.code}') from None
    except (URLError, OSError, HTTPException):
        raise TodoistWriteError('Todoist create failed: network failure; remote outcome unverified') from None
    except (ValueError, TypeError, RecursionError):
        raise TodoistWriteError('Todoist create failed: malformed response; remote outcome unverified') from None
