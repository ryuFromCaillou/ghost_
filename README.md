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

## Shared state paths

`~/ghost/integrations/state/` holds shared durable orchestration state;
`~/ghost/integrations/todoist/` holds the Todoist adapter/integration code.
Todoist does not own `goals.json`. Future integrations may consume or contribute
to shared state, and `integrations/` may later become the orchestration/API layer.
This is the current intent, not a finalized architecture.

```text
~/ghost/integrations/
├── state/
│   ├── goals.json
│   ├── todoist-history.sqlite3
│   └── external/
│       ├── .todoist.lock
│       ├── .todoist-snapshots/       # retained generations
│       └── todoist/                 # symlink to a published generation
│           ├── tasks.json
│           ├── projects.json
│           ├── sections.json
│           └── sync_metadata.json
└── todoist/                         # adapter code, including paths.py
```

The pure `paths.py` module centralizes defaults, using the user's home directory
at import time. Importing it creates no directories or files. Goal persistence
uses `goal_store.DEFAULT_PATH`; snapshot publishing and reading share
`DEFAULT_OUTPUT`; completion ingestion uses `history.DEFAULT_HISTORY`.
Explicit Python path arguments and history's `--database` override still apply.
From the adapter directory, `python3 -m goal_store` inspects the default registry.

These defaults replace the former `~/ghost/state/` locations. Existing state is
left untouched: there is no migration, copying, deletion, compatibility symlink,
or fallback read from the old locations. No database contents or schema change.

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
`python3 -m unittest -v` from this directory includes normalization,
reader, and importer tests. Tests include provider-aware
identity, context resolution, child-before-parent ordering, optional values,
status/priority mapping, provenance, source immutability and determinism.

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

## Read-only progress projection

```text
Todoist current snapshot (normalized GhostTaskState)
        +
completion history (TaskCompletionEvent occurrences)
        +
GoalRegistry
        ↓
ProgressProjection
```

Current state answers **“what is true now?”** Completion history answers
**“what happened?”** GoalRegistry answers **“what does this task mean?”**
`progress.py` combines those without changing any source. Callers supply all
three already-loaded inputs; the projection performs no filesystem, SQLite, or
network access and generates no timestamps.

From `~/ghost`, import `project_progress` or `project_milestone_progress` from
`integrations.todoist.progress`. The latter also takes a `milestone_id` and raises
`ProjectionError` for an unknown milestone. Both use the existing `GhostTaskState`
and `TaskCompletionEvent` models. Duplicate current identities or unsupported
current statuses raise `ProjectionError` rather than silently choosing a value.

Frozen `MilestoneProgress` and `GoalProgress` objects expose linked, active,
currently completed, and missing task-ID tuples, plus `completion_event_count`
and `last_completed_at` (None without matching evidence). Task-ID tuples contain
`SourceIdentity(source, source_id)` objects, rather than bare strings, to preserve
provider identity even when two providers use the same local ID. Missing means
only absent from the supplied current task state; it implies no deletion,
completion, invalidity, or access restriction.

Only current `GhostTask.status` determines active/completed classification.
A reopened active task can have completion evidence while its currently completed
classification remains empty. Every supplied matching event counts, including
recurring/recompleted occurrences; the projection does not deduplicate input
events. The latest matching completion timestamp is compared as an aware datetime.
The event model already canonicalizes timestamps to UTC.

`ProgressProjection.milestones` and `.goals` preserve registry order. Milestone
tasks preserve task-link order; goal tasks aggregate in milestone order and then
link order, without duplicate provider identities. Goal event counts sum milestone
counts and the latest timestamp spans all its milestones. Empty milestones and
goals remain in the output. Semantic status is copied directly from the registry. No percentages, automatic
completion, scheduling, or write-back are inferred.


## Explicit goal and milestone status

| Dimension | Meaning |
| --- | --- |
| Current task state | What is true about tasks now |
| Completion history | What task completions happened |
| GoalRegistry | What tasks mean |
| ProgressProjection | Evidence of progress |
| Goal/Milestone status | What ARK/GHOST explicitly asserts is the current semantic state |

Task evidence does not automatically transition semantic status.

`goals.Status` is shared by frozen `Goal` and `Milestone` models:
`PLANNED`, `ACTIVE`, `BLOCKED`, and `COMPLETE`. Both default to `Status.PLANNED`;
Python constructors and replacement helpers require enum members, not strings.
The new field follows `description`, preserving existing positional model calls.
`TaskMilestoneLink` is unchanged.

Persistence represents status as exactly `"planned"`, `"active"`, `"blocked"`, or
`"complete"` in each goal and milestone JSON object. Loading converts these strings
into enum members; absent status defaults to PLANNED for legacy files. Explicit
nulls, unknown strings, and other malformed values are rejected and wrapped in
`GoalStoreError`. Loading never rewrites a file. An explicit save includes status
fields, retaining deterministic UTF-8 JSON and atomic replacement. Older versions
of the strict loader cannot read the added fields. The existing `python3 -m
goal_store` CLI still prints only counts.

Pure helpers in `goals.py` return a new registry, preserve collection order and
unaffected models/links, and never persist:

```python
from integrations.todoist.goals import Status, with_goal_status, with_milestone_status

registry = with_goal_status(registry, "thesis", Status.ACTIVE)
registry = with_milestone_status(registry, "experimental_validation", Status.COMPLETE)
```

Unknown IDs and invalid status values raise `ValueError`. Every valid status can
replace every other status, including reopening COMPLETE as ACTIVE. Same-status
replacement returns a new, equal registry. The original registry stays unchanged.

`MilestoneProgress.status` and `GoalProgress.status` expose the corresponding
explicit registry status without reconciliation. A COMPLETE milestone with active
tasks is valid. All completed tasks do not complete a milestone, and all COMPLETE
milestones do not complete their goal. Direct construction of these projection
records now requires a `status` argument; projection function signatures are
unchanged. No automatic transitions are implemented. Objective selection is described below.


## Objective selection

ProgressProjection answers: **“Where is there evidence of progress?”**
Explicit status answers: **“What semantic state has ARK/GHOST asserted?”**
Objective selection answers: **“Which active milestone should move next?”**

```text
GoalRegistry + ProgressProjection
                ↓
         ObjectiveSelector
                ↓
         ObjectiveSelection
```

Call `select_objective(registry, projection)` from
`integrations.todoist.objective` with already-loaded models. It does not rebuild
progress, read sources, generate timestamps, change statuses, or save anything.
This is not yet scheduling or autonomous execution.

Only ACTIVE goals and ACTIVE milestones with at least one currently active linked
task are actionable. PLANNED, BLOCKED, and COMPLETE remain excluded even when
active task evidence exists. Completed, missing, or historical-only tasks are
never actionable. Completion history does not reopen semantic state.

Registry order is explicit priority order: traverse `GoalRegistry.goals`, then
for each goal traverse its milestones in `GoalRegistry.milestones` order. The
first eligible milestone wins. This goal-first order takes precedence over global
milestone order across different goals. Titles, completion counts, and last
completion timestamps have no ranking effect.

Frozen `Objective` contains `goal_id`, `milestone_id`, `task_ids` (a tuple of
`SourceIdentity` values), `reason_codes`, and `summary`. All active linked tasks
in the chosen milestone are included in task-link order. The deterministic
summary is `Advance milestone: {milestone.title}`. Selected reason codes are
`goal_active`, `milestone_active`, `actionable_tasks_present`, and
`highest_registry_priority`.

Frozen `ObjectiveSelection` contains `objective` (None if no work is eligible),
`considered_milestone_ids`, and an `excluded` tuple of frozen `ObjectiveExclusion`
records (`milestone_id`, `reason_code`). All milestones are considered in priority
order, including those after the winner. Only ineligible milestones are excluded;
lower-priority eligible work remains eligible. Each exclusion has one reason,
with parent-goal ineligibility taking precedence:

- `goal_not_active`: parent goal is PLANNED, BLOCKED, or COMPLETE.
- `milestone_blocked`: milestone is BLOCKED under an ACTIVE goal.
- `milestone_complete`: milestone is COMPLETE under an ACTIVE goal.
- `milestone_not_active`: milestone is PLANNED under an ACTIVE goal.
- `no_actionable_tasks`: both are ACTIVE but no active current linked task exists.

Before selecting, the complete projection is checked against the registry.
Unknown, duplicate, or missing goal/milestone projections, parent/status mismatch,
link/order mismatch, inconsistent current classifications, and inconsistent goal
task aggregation raise `ObjectiveSelectionError`. Current classifications must
partition linked identities. Projection record order itself does not set priority;
registry order does. Historical counts/timestamps are trusted and ignored by the
selector. A stale status projection must be rebuilt by the caller, not repaired
by the selector. The existing domain models require no changes.

## Operator brief

ObjectiveSelector decides what should move next. GhostBrief presents that decision
to the operator. GhostBrief does not make strategic decisions. It renders decisions
already made by the objective layer and exposes nearby blocked/queued work.

```text
GoalRegistry + GhostTaskState + CompletionHistory
                      ↓
              ProgressProjection
                      ↓
              ObjectiveSelection
                      ↓
                 GhostBrief
                      ↓
                  Operator
```

The operator can later be a human or execution agent. The separation remains:
state != interpretation, interpretation != decision, decision != presentation,
and presentation != execution. GhostBrief never alters what ObjectiveSelector
decided.

The pure API in `integrations.todoist.brief` accepts all layers explicitly:

```python
from integrations.todoist.brief import build_brief, render_brief

brief = build_brief(registry, task_state, projection, selection)
text = render_brief(brief)
```

`build_brief` performs no loading, projection rebuilding, or objective selection.
It resolves goal/milestone titles from GoalRegistry and task content from normalized
`GhostTask.title`, never raw Todoist data. It preserves the supplied objective's
summary and task order. Frozen models are `BriefTask`, `BriefObjective`,
`BriefBlockedItem`, `BriefQueuedItem`, and `GhostBrief`; collections are tuples and
task identities remain provider-aware. Blocked/queued records include `goal_title`
so the renderer needs only the brief, with no source lookups.

PRIMARY presents the supplied objective. BLOCKED includes only BLOCKED milestones
under ACTIVE goals. QUEUED includes other ACTIVE milestones under ACTIVE goals
with current active linked tasks. Both sections follow goal registry order, then
milestone registry order within each goal. Planned and complete milestones, and
all work under non-ACTIVE goals, are omitted. Completed, missing, or historical-only
tasks never make work actionable.

An absent objective stays absent, even if the caller supplies actionable work:
it is shown as queued, never promoted by the brief. The builder checks the decision's
identity and active task set, not its priority or diagnostic reason codes. Selection
remains the caller-supplied decision. Message codes are emitted in this order:
`primary_objective_selected` or `no_primary_objective`, then `blocked_work_present`
and `queued_work_present` when applicable. `no_actionable_work` is emitted only
when primary, blocked, and queued are all empty.

Invalid correspondence raises `BriefError`: unknown/duplicate/missing projection
identities, registry status/parent/link disagreements, inconsistent classification
or goal aggregation, duplicate current task identities, stale current task
classifications, unknown objective goals/milestones, wrong parent goal, inactive
semantic status, or an objective task set that differs from its active linked
projection set. Every selected task must resolve to one ACTIVE normalized task.
The selector's existing validation is exposed as `validate_projection` and reused
without running selection. No source is repaired or mutated.

`render_brief` emits plain text with `GHOST BRIEF`, PRIMARY, and, when a primary
exists, TASKS. Nonempty BLOCKED and QUEUED sections follow. Empty PRIMARY prints
`None`; a completely empty brief adds `No actionable work.`. Output uses blank
lines between sections and one final newline. IDs, reason/message codes, timestamps,
completion counts, and diagnostic metadata are not printed. Titles, task content,
and the supplied summary are rendered directly without generated prose.

No CLI is added in this phase. The core is ready for a later `ghost brief` adapter;
that adapter must own durable loading and the policy for unavailable snapshots,
registries, or history. The brief itself introduces no loading or fallback policy.
There is no scheduling, execution, notification, persistence, or provider write-back.
