#!/usr/bin/env python3
"""Read-only Todoist API v1 snapshot import. Python 3.11+, standard library only."""
import argparse
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener
import uuid

if __package__:
    from .paths import TODOIST_EXTERNAL_STATE_ROOT
else:
    from paths import TODOIST_EXTERNAL_STATE_ROOT

API = "https://api.todoist.com/api/v1"
DEFAULT_OUTPUT = TODOIST_EXTERNAL_STATE_ROOT
RESOURCES = ("tasks", "projects", "sections")


class SyncError(Exception):
    pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def fetch_all(resource, token, page_size=200, opener=None):
    opener = opener or build_opener(NoRedirect())
    items, cursors, ids = [], set(), set()
    cursor = None
    pages = 0
    while True:
        params = {"limit": page_size}
        if cursor is not None:
            params["cursor"] = cursor
        request = Request(f"{API}/{resource}?{urlencode(params)}",
                          headers={"Authorization": f"Bearer {token}",
                                   "Accept": "application/json"}, method="GET")
        try:
            with opener.open(request, timeout=30) as response:
                payload = json.load(response)
        except HTTPError as exc:
            raise SyncError(f"{resource}: HTTP {exc.code}; snapshot unchanged") from None
        except (URLError, TimeoutError, OSError):
            raise SyncError(f"{resource}: network failure; snapshot unchanged") from None
        except (ValueError, UnicodeError):
            raise SyncError(f"{resource}: invalid JSON; snapshot unchanged") from None
        if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
            raise SyncError(f"{resource}: invalid paginated response")
        for item in payload["results"]:
            if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not item["id"]:
                raise SyncError(f"{resource}: missing or invalid object ID")
            if item["id"] in ids:
                raise SyncError(f"{resource}: duplicate ID across pages; retry import")
            ids.add(item["id"])
            items.append(item)
        pages += 1
        cursor = payload.get("next_cursor")
        if cursor is None:
            return items, pages
        if not isinstance(cursor, str) or not cursor or cursor in cursors:
            raise SyncError(f"{resource}: invalid or repeated pagination cursor")
        cursors.add(cursor)


def sync(token, output=DEFAULT_OUTPUT, page_size=200, opener=None):
    if not token or not token.strip():
        raise SyncError("TODOIST_API_TOKEN is missing or empty; export it before syncing")
    output = Path(output).absolute()
    output.parent.mkdir(parents=True, exist_ok=True)
    # Serialize writers. Never resolve output: it is the atomic publication pointer.
    with (output.parent / f".{output.name}.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if output.exists() and not output.is_symlink():
            raise SyncError(f"Refusing to replace existing non-symlink directory: {output}")
        data, page_counts = {}, {}
        for resource in RESOURCES:
            data[resource], page_counts[resource] = fetch_all(resource, token, page_size, opener)
        metadata = {
            "source": "todoist",
            "synced_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "task_count": len(data["tasks"]),
            "project_count": len(data["projects"]),
            "section_count": len(data["sections"]),
            "page_counts": page_counts,
        }
        generations = output.parent / f".{output.name}-snapshots"
        generations.mkdir(mode=0o700, exist_ok=True)
        stage = Path(tempfile.mkdtemp(prefix="snapshot-", dir=generations))
        pointer = output.parent / f".{output.name}-{uuid.uuid4().hex}.tmp"
        committed = False
        try:
            for name, value in {**data, "sync_metadata": metadata}.items():
                with (stage / f"{name}.json").open("w", encoding="utf-8") as handle:
                    json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False)
                    handle.write("\n")
                    handle.flush()
                    os.fsync(handle.fileno())
            pointer.symlink_to(stage.relative_to(output.parent), target_is_directory=True)
            os.replace(pointer, output)
            committed = True
        finally:
            if pointer.is_symlink():
                pointer.unlink()
            if not committed:
                shutil.rmtree(stage)
        return metadata


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--page-size", type=int, default=200, help="Objects per page, 1–200 (default: 200)")
    args = parser.parse_args()
    if not 1 <= args.page_size <= 200:
        parser.error("--page-size must be between 1 and 200")
    try:
        metadata = sync(os.environ.get("TODOIST_API_TOKEN"), page_size=args.page_size)
    except SyncError as exc:
        print(f"Todoist sync failed: {exc}", file=sys.stderr)
        return 1
    except (OSError, ValueError):
        print("Todoist sync failed: local snapshot could not be written", file=sys.stderr)
        return 1
    print("Todoist sync complete")
    for resource in RESOURCES:
        print(f"{resource}: {metadata[resource[:-1] + '_count']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
