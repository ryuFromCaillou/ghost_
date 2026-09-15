# GHOST: context and Todoist task trees

```text
WHY / DIRECTION
        ↓
Todoist task tree
        ↓
actionable frontier
        ↓
deterministic selection
        ↓
PRIMARY ORDER
        ↓
operator/Codex
        ↓
ghost complete
        ↓
Todoist
        ↓
sync
        ↓
next order
```

Todoist owns operational task hierarchy. ARK/GHOST does not duplicate or persist
another task tree. WHY and DIRECTION govern the work but have no status, task
links, scheduling rank, or eligibility rules. A normal Todoist task/subtask enters
GHOST's current work universe after sync; no registry edit is required.

Parent/child means **containment, not dependency**. GHOST neither infers prerequisite
relationships nor completes parents when children complete.

## Current-state pipeline

`runtime.run_brief()` reads one published snapshot, normalizes it, loads strategic
context, calls the pure `select_task()` once, and builds a brief. `ghost complete`
calls the same pipeline. For the same stored inputs, both commands select the
same PRIMARY. Brief performs no network requests, synchronization, database
access, or filesystem writes. Stale snapshots remain accepted: refresh explicitly.

Provider-independent immutable models remain in `integrations/task_state.py`.
Normalization preserves `SourceIdentity('todoist', original_id)`, `parent_id`,
ACTIVE/COMPLETED status, project/section context, dates and provenance. Original
task objects remain in immutable `task.raw`; original project/section attributes
remain in the immutable snapshot. No provider identity is synthesized or remapped.

### Exact actionability rule

A task is actionable precisely when it is present in current normalized state,
is ACTIVE, and has **no direct ACTIVE child**. Completed and missing tasks are
never actionable. An ACTIVE parent with only completed children is actionable.
An active child of a completed parent is still actionable. In the unusual chain
ACTIVE parent → COMPLETED child → ACTIVE grandchild, both parent and grandchild
are actionable: the rule concerns direct children, not inferred dependencies.

The reader rejects missing parents, cycles, and cross-project parent references.
The selector also rejects these for directly supplied normalized state; it never
repairs a malformed hierarchy. Completion history cannot make a task actionable
or ineligible. The current snapshot alone supplies task completion status.

### Exact deterministic ordering contract

The current snapshot contains task `order_key`, `child_order`, and `day_order`.
Task ordering fields survive normalization in `task.raw`. Project `order_key` /
`child_order` and section `order_key` / `section_order` remain in snapshot rows;
runtime passes these rows to the selector without changing the normalized models.

1. Order represented projects as a flat list. Within each project, visit
   unsectioned root tasks first, then sections in provider order.
2. Within each sibling scope (same project, section, and parent), order tasks by
   provider order. Traverse the forest depth first, visiting parents before their
   descendants. Child groups use the same project/section grouping if needed.
3. Filter this traversal to the actionable rule above. PRIMARY is the first item
   or none; NEXT is every remaining currently actionable task, across all roots.

For each project list, section list, or task sibling group: if **every member** has
an `order_key`, compare these strings lexicographically. Otherwise use ascending
legacy integers for the entire group (`child_order` for projects/tasks,
`section_order` for sections), with missing/null positions last. Break equal or
missing positions using `(provider, provider_id)` lexicographically. Invalid
non-null ordering types fail through `GHOST ERROR`; no positions are invented.
Only projects/sections represented by current tasks participate in these groups.

Input array order, task titles, semantic context, completion history, priority,
due dates, and `day_order` do not rank work. This contract approximates Todoist's
manual project list order, not Today, filters, custom sorts, or workspace folders.
Projects are flattened because the normalized project model has no parent or
folder structure. Fractional project keys across different project-parent scopes
are compared as a deterministic fallback, not a claim to reproduce that UI.
The [Todoist API reference](https://developer.todoist.com/api/v1/) documents
lexicographic `order_key` sorting within sibling scopes.

### Brief shape

```text
GHOST BRIEF

WHY
Make time for meaningful work.

DIRECTION
Build a useful project.

PRIMARY ORDER
Check codes

UNDER
Fix truck
Diagnose misfire

NEXT
- Inspect cylinders
- Acquire prices
```

UNDER lists the complete ancestor chain from root to immediate parent; top-level
PRIMARY omits it. NEXT means **remaining currently actionable frontier**, not
linked work under a milestone. Empty frontiers retain WHY/DIRECTION, print PRIMARY
ORDER `None`, and `No actionable work.` Empty NEXT is omitted.

## Strategic persistence and migration

Shared durable files live under `~/ghost/integrations/state/`:

- `strategic-context.json`: exactly two nonempty strings, `why` and `direction`.
- `goals.json`: preserved legacy archive, ignored by runtime.
- `todoist-history.sqlite3`: unchanged raw completion evidence.
- `external/todoist`: current published snapshot symlink; older generations remain
  under `external/.todoist-snapshots/`.

```json
{
  "why": "Make time for meaningful work.",
  "direction": "Build a useful project."
}
```

The one-shot migration is explicit, separate from brief/complete:

```sh
cd ~/ghost/integrations/todoist
python3 -B migrate_context.py
```

It extracts the sole WHY and its sole linked DIRECTION from `goals.json`, refusing
ambiguous or malformed input. It atomically publishes the new context file without
overwriting an existing destination. An identical destination allows harmless
reruns; a conflicting one fails. `--source` and `--destination` support explicit
paths. The original registry stays byte-for-byte at its original path as the
deterministic archive; no legacy statuses, descriptions, IDs, or links are deleted
from that archive. Only the two titles move into active strategic context.

Runtime requires the small context file and current snapshot. It never migrates
implicitly or falls back to the old registry. Goal, Milestone, TaskMilestoneLink,
progress projection, and milestone objective selection code have been removed.
Shared defaults remain centralized in `paths.py`; explicit Python snapshot and
context path arguments remain available.

## Commands and completion safety

```sh
cd ~/ghost/integrations/todoist
./ghost --help
./ghost brief
./ghost complete
# After remote completion, explicitly refresh:
python3 sync.py
./ghost brief
```

`ghost complete` displays the current selected task and asks
`Mark this task complete in Todoist? [y/N] `. Only `y` or `yes` (case insensitive,
surrounding whitespace ignored) proceeds. Other input, EOF, or interrupt cancels
without a write. Missing PRIMARY fails before prompting.

After confirmation, the unchanged adapter sends exactly one authenticated
`POST https://api.todoist.com/api/v1/tasks/{task_id}/close`, with a 30-second
timeout, redirect refusal, no retries, and exactly HTTP 200/204 accepted.
This status handling does not independently verify resulting remote task state.
Credentials come only from `TODOIST_API_TOKEN` in the environment; commands do not
source credential files or print tokens or remote response bodies. Failures use
the concise `GHOST ERROR` boundary. No automatic sync, local task completion,
parent completion, history append, or strategic-context mutation occurs.

## Validation

```sh
cd ~/ghost/integrations/todoist
python3 -m unittest -v
git diff --check
./ghost --help
./ghost brief
```

Tests cover tree validation, direct-child actionability, deterministic provider
ordering and ties, exact rendering, one-shot migration, selection without links,
new tasks/subtasks entering after mocked sync, and confirmation/cancellation.
Completion writes are mocked. Importer, reader, normalization, history ingestion,
and write-adapter regression tests remain separate. No test performs a live write.

## Todoist import

Python 3.11+; no dependencies. Reads only `GET /api/v1/tasks`, `/projects`,
`/sections`, following `next_cursor` on every endpoint. The importer performs no Todoist writes.
The separate `ghost complete` command below explicitly closes PRIMARY ORDER after confirmation.

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

Output: `~/ghost/integrations/state/external/todoist/{tasks,projects,sections,sync_metadata}.json`.
The three resource files are arrays of unchanged API objects, including all
returned fields and hierarchy IDs. Metadata includes UTC time, counts and page
counts. JSON uses UTF-8, two-space indentation and sorted object keys.

All retrieval finishes before disk publication. Each import writes a private
snapshot directory beside the output, then atomically replaces the `todoist`
symlink. Writers are serialized with a filesystem lock. Failed retrieval or
staging leaves the previous snapshot intact. Existing real output directories
are refused rather than overwritten. Successful older generations are retained
in `~/ghost/integrations/state/external/.todoist-snapshots/`; retention cleanup is manual.
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

## Durable completion history

The separate `history.py` path reads completed tasks and appends immutable
`TaskCompletionEvent` evidence to
`~/ghost/integrations/state/todoist-history.sqlite3`. It neither reads nor publishes
active snapshots, and does not write to Todoist. Todoist remains authoritative
for current state; historical evidence does not imply a task is still completed.

With `TODOIST_API_TOKEN` exported, run from `~/ghost`:

```sh
python3 -m integrations.todoist.history \
  --since 2026-09-01T00:00:00Z --until 2026-09-13T00:00:00Z
```

`--since` is required and inclusive; `--until` is exclusive and defaults to now.
Both require timezone-aware ISO datetimes. `--database` overrides the separate
history file, and `--page-size` accepts 1–200. Rerun overlapping ranges to capture
late-arriving evidence. There is no automatic scheduling or persistent high-water
mark; callers choose coverage explicitly. Long ranges are split into contiguous
30-day windows, following every cursor in each window. All retrieval and
validation finishes before a single SQLite transaction appends the batch.
Network, pagination, or normalization failures publish no events; failed inserts
roll back the batch. SQLite serializes concurrent writers (30-second timeout).
Requests use GET, refuse redirects, time out after 30 seconds, and do not retry.
Errors omit credentials and response bodies.

The provider-independent model/store lives in `completion_events.py` within this
integration's Git repository and reuses `integrations.task_state.SourceIdentity`.
It has no dependency on the Todoist API or active snapshot modules. Events carry
provider-aware task/project/section/parent identities, UTC `completed_at`, title,
source, and deeply immutable original `raw` data. Missing optional hierarchy IDs
remain `None`; historical references need not exist in the active snapshot.
Invalid IDs, missing completion timestamps, naive timestamps, and malformed
content are rejected. Completion is established by the completion endpoint and
its timestamp, not by `checked` or disappearance from a snapshot.

The durable key is `(source, task_id, completed_at)` with timestamps canonicalized
to UTC. Replay is idempotent; a later completion of the same task is a separate
event. For duplicate keys the first stored title, context, and raw evidence win.
The storage API never updates or deletes events. Reopening, deleting, or removing
a task from a later snapshot does not remove its evidence. This key cannot
distinguish two completions of one task at the exact same provider timestamp.
One Todoist account per database is assumed, matching the existing identity model.

Offline consumer example, from `~/ghost`:

```python
from integrations.todoist.history import DEFAULT_HISTORY
from integrations.todoist.completion_events import load_events

for event in load_events(DEFAULT_HISTORY):
    print(event.task_id, event.completed_at, event.title)
```

`load_events` opens the database read-only and returns an immutable tuple ordered
by completion time, source, and task ID; a missing database raises a SQLite error
without creating a file. Retain/back up the database to retain history. This path
can only preserve evidence returned by Todoist: it cannot reconstruct completions
already unavailable remotely or guarantee every recurring occurrence is returned.

Endpoint and response contract: [Todoist completion API](https://developer.todoist.com/api/v1/#tag/Tasks).
The endpoint returns `items` and `next_cursor`, with completion-date ranges of up
to three months. No live account import is part of the automated validation.
Run `python3 -m unittest -v` from this directory to test both pipelines, including
history persistence after a task disappears from a subsequently published snapshot.
