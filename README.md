# ARK Todoist import

Python 3.11+; no dependencies. Reads only `GET /api/v1/tasks`, `/projects`,
`/sections`, following `next_cursor` on every endpoint. No Todoist writes.

Export credentials in your shell, then run:

```sh
set -a
. ~/.config/ghost.env
set +a
python3 ~/ghost/integrations/todoist/sync.py
```

The importer only reads `TODOIST_API_TOKEN` from the process environment.
It never loads credential files. Errors exit nonzero without printing remote
response bodies or credentials. Requests time out after 30 seconds; redirects
are refused. There are no automatic retries; rerun after transient failures.
Use `--page-size 10` to exercise pagination against a small account.

Output: `~/ghost/state/external/todoist/{tasks,projects,sections,sync_metadata}.json`.
The three resource files are arrays of unchanged API objects, including all
returned fields and hierarchy IDs. Metadata includes UTC time, counts and page
counts. JSON uses UTF-8, two-space indentation and sorted object keys.

All retrieval finishes before disk publication. Each import writes a private
snapshot directory beside the output, then atomically replaces the `todoist`
symlink. Writers are serialized with a filesystem lock. Failed retrieval or
staging leaves the previous snapshot intact. Existing real output directories
are refused rather than overwritten. Successful older generations are retained
in `~/ghost/state/external/.todoist-snapshots/`; retention cleanup is manual.
A killed process may leave an unpublished generation; it does not become current.

For a consistent multi-file read, ARK should resolve the `todoist` symlink once
and read all four files from that resolved directory. The API reads themselves
are sequential, not a remote transaction: account edits during import can cause
cross-resource differences. Duplicate IDs or repeated cursors fail the import.
No relationships or missing fields are synthesized. Projects and sections are
the active accessible collections exposed by these endpoints; archived objects
and completed-task history are outside this import.

The integration is outside the existing `~/ghost/entry` Git repository and does
not hook into its evaluator or SQLite state. `~/ghost/.gitignore` excludes
secrets and runtime state if this root becomes version controlled.

Validation:

```sh
cd ~/ghost/integrations/todoist
python3 -m unittest -v
```

API reference: https://developer.todoist.com/api/v1/

## Snapshot reader

`reader.load_snapshot(current=DEFAULT_OUTPUT)` returns a frozen
`TodoistSnapshot` with `directory`, `projects`, `sections`, `tasks`, and
`metadata`. From this directory:

```python
from reader import SnapshotReadError, load_snapshot

try:
    snapshot = load_snapshot()
except SnapshotReadError as error:
    print(f"Snapshot rejected: {error}")
else:
    print(snapshot.directory, len(snapshot.tasks))
```

When importing from the `~/ghost` root, use
`from integrations.todoist.reader import load_snapshot`.
No credentials or network access are needed. The existing publisher constants
are reused without invoking synchronization.

The reader requires a current symlink, resolves it exactly once with strict
resolution to an absolute directory, and checks that all four inputs are regular
files (not symlinks) before parsing any. All subsequent reads use only that
pinned directory. A concurrent publication cannot mix generations. Any missing
file, read/parse failure, or failed validation raises `SnapshotReadError`; no
partial snapshot is returned and nothing is repaired.

Validation requires unique nonempty string IDs, string project/section names,
resolvable project and section references, same-project section/parent links,
and an acyclic task parent hierarchy with no missing parents. Tasks require
content, boolean `checked`, string labels, nullable string section/parent IDs,
and object-or-null due/deadline/duration fields. Metadata must identify Todoist,
have an ISO-8601 UTC timestamp and matching integer resource counts. If present,
page counts must be positive integers for all three resources. Duplicate JSON
keys and nonstandard NaN/Infinity constants are rejected. Uninterpreted fields
are retained; dictionaries become read-only mappings and arrays become tuples,
including nested values. No GHOST normalization is performed.

Assumptions: published generation contents remain unchanged and retained during
reads, as the publisher currently guarantees. This does not protect against an
unrelated process editing generation files in place. A remotely inconsistent
import with broken references is rejected rather than repaired. Page counts
cannot be independently reconstructed from flattened arrays. Reading preserves
contents, modification/change timestamps and the publication symlink; filesystem
access timestamps may change according to mount policy.

The unittest command above runs both importer and reader tests. Reader tests
cover normal loading, a publication switch between file reads with exactly one
resolution, missing/malformed files, broken references/cycles, invalid schemas
and metadata, missing/dangling/looping current links, linked inputs, disappearing
files, no network or filesystem mutations, and deep in-memory immutability.

## GHOST task normalization

No canonical task model exists in `entry/ghost`; its current state service exposes
telemetry. Provider-independent frozen models now live in
`integrations/task_state.py`, outside the Todoist adapter and independent of the
existing runtime. No runtime wiring or persistence is added.

From `~/ghost`:

```python
from integrations.todoist.reader import load_snapshot
from integrations.todoist.normalize import normalize_todoist_snapshot

state = normalize_todoist_snapshot(load_snapshot())
by_id = {task.id: task for task in state.tasks}
for task in state.tasks:
    print(task.title, task.project.name if task.project else None, task.status.value)
    parent = by_id.get(task.parent_id)
```

For a compact local read → normalize → inspect command:

```sh
cd ~/ghost
python3 -m integrations.todoist.normalize
# Include normalized task details (may contain personal content):
python3 -m integrations.todoist.normalize --show-tasks
```

`normalize_todoist_snapshot(snapshot) -> GhostTaskState` is a pure conversion of
one reader-validated `TodoistSnapshot`. It performs no filesystem or network IO,
does not re-read current, and does not repair hierarchy. Conversion failures
raise `NormalizationError`. Input sequence order is preserved; parents are
references, never recursively embedded objects. Returned objects, nested raw
mappings, and sequences are immutable. Repeated normalization produces equal
state with no newly generated IDs or timestamps.

Mapping contract:

| Source | GHOST field / semantics |
| --- | --- |
| `id` | `task.id = SourceIdentity('todoist', original_id)`; `source` and `source_id` convenience properties |
| `content`, `description` | `title`, `description`; strings unchanged |
| `project_id` | `project` with provider-aware ID and resolved name |
| `section_id` | `section` with provider-aware ID, resolved name, and project identity |
| `parent_id` | Provider-aware `parent_id`, equal to the parent's `task.id` |
| `labels` | `tags` tuple; absent/null is `None`, supplied empty array is `()` |
| `priority` | 1 → normal, 2 → medium, 3 → high, 4 → urgent; missing/null → `None` |
| `checked` | false → active, true → completed |
| `due`, `deadline` | Separate `TaskDate` objects; absent/null → `None` |
| `added_at`, `updated_at` | `created_at`, `updated_at` parsed datetimes; absent/null → `None` |
| Snapshot directory, `synced_at` | Shared immutable provenance on state and every task: source, absolute generation, snapshot timestamp |
| Full original task | Explicit deeply immutable `raw` extension for source-specific inspection |

`TaskDate.value` is a Python `date` for date-only input and a `datetime` for timed
input. An explicit `datetime` field takes precedence over `date`; API v1 also
encodes timed values directly in `date`. Naive/floating times remain naive;
offsets are preserved and named timezone strings are retained separately.
Nothing is converted to midnight or to the machine timezone. Recurrence maps
to `recurring`, the source expression to `expression`, and its language to
`language`; no recurrence schedule is computed. Missing metadata stays `None`.
The original date object remains available through the task's `raw` extension.

Priority mapping follows the API's **task object** definition (1 natural,
4 very urgent), not the conflicting write-parameter prose saying 1 highest:
https://developer.todoist.com/api/v1/#tag/Tasks . The mapping does not assign or
change priorities. Verify that discrepancy before any future write-back or
priority-driven execution; original numbers remain available in `raw`.

Source-specific fields intentionally left in `raw` include duration, ordering
keys/positions, assignment/user IDs, completion timestamps/counters, collapse
flags and other UI/API metadata. Unmapped project/section attributes remain in
the immutable source snapshot; normalized context includes identity and names.
Downstream ordinary task logic should use normalized fields, not `raw`.

The normalizer tolerates missing optional task fields on an already materialized
snapshot; this does not relax the existing file reader's stricter input contract
(e.g. the reader still requires labels and nullable hierarchy keys). Identity
is provider-aware within each resource collection; multiple accounts of the
same provider are not introduced in this phase.

Validation on the current local generation: 23 tasks, 2 projects, 5 sections,
4 resolvable children. No current tasks have due dates, so date-only, fixed and
floating datetimes, deadlines, and recurring metadata are covered by fixtures.
`python3 -m unittest -v` from this directory runs 27 tests: 12 normalization,
10 reader, and 5 importer tests. All passed. Tests include provider-aware
identity, context resolution, child-before-parent ordering, optional values,
status/priority mapping, provenance, source immutability and determinism.
