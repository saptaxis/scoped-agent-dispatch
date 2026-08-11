"""Normalized session and turn records — the vocabulary shared by readers and index.

Deliberately import-free: readers depend on this, the index depends on this, and
neither depends on the other.
"""

from dataclasses import dataclass

KIND_MAIN = "main"
KIND_SUBAGENT = "subagent"
KIND_WORKFLOW = "workflow-agent"

GRADE_FULL = "full"
GRADE_SKELETON = "skeleton"

# Tool results are the size driver. Measured across 543 real results: p50 1.9 KB,
# p90 12 KB, p95 17.5 KB, p99 45 KB, max 443 KB. 64 KB stores 99% whole.
TOOL_RESULT_CAP = 65536

# Terminal state of a session, derived from structure only — never from reading
# the words. See readers.derive_outcome for why there is no "it ended with a
# question mark" rule.
OUTCOME_AWAITING_QUESTION = "awaiting-question"   # model called AskUserQuestion
OUTCOME_AWAITING_USER = "awaiting-user"           # model spoke last, nobody replied
OUTCOME_INTERRUPTED = "interrupted"               # user stopped it
OUTCOME_IN_FLIGHT = "in-flight"                   # tool call with no result
OUTCOME_USER_LAST = "user-last"                   # user spoke, model never answered
OUTCOME_TOOL_RESULT_LAST = "tool-result-last"     # a tool returned; the model never spoke again

# `in-flight` and `tool-result-last` are opposite sides of the same stall and are
# deliberately NOT one value: in-flight is a call awaiting its RESULT,
# tool-result-last is a result awaiting the MODEL. Collapsing them would make the
# column lie about which side the work is stuck on — the one question it exists
# to answer.


@dataclass(frozen=True)
class SessionRecord:
    """One row of `sessions`.

    `id` is assigned per kind because sessionId is NOT unique: every subagent
    file repeats its parent's. main -> session uuid; subagent and
    workflow-agent -> agentId.
    """

    id: str
    kind: str
    agent: str
    source: str
    parent_session_id: str | None = None
    agent_id: str | None = None
    workflow_id: str | None = None
    scad_run_id: str | None = None
    cwd: str | None = None
    title: str | None = None
    # Two labels, deliberately not one column. `title` is DERIVED — the agent's
    # own summary (aiTitle), or on a skeleton row the first prompt verbatim.
    # `name` is CHOSEN — what the human typed at `/rename`, or the name the
    # harness gave the job. A session nobody named has none, and that blank is
    # the honest answer: filling it from `title` is what made a renamed session
    # display as the literal string "/rename writing-wm-evals-research".
    name: str | None = None
    git_branch: str | None = None
    started: int | None = None
    ended: int | None = None
    grade: str = GRADE_FULL
    outcome: str | None = None
    last_stop_reason: str | None = None
    n_interrupts: int = 0
    n_tool_denials: int = 0
    n_errors: int = 0


@dataclass(frozen=True)
class JobStateRecord:
    """One snapshot of the harness's own job state, keyed on the session it ran.

    Two layers here, and they are not the same kind of fact:

    - STRUCTURAL: `state` (blocked | failed | …). The harness owns the live
      process — it can see a PTY idle at a prompt. An index reading JSONL after
      the fact cannot, which is why our own `outcome` is coarser and derived.
    - SEMANTIC: `needs` and `detail` are MODEL-WRITTEN PROSE. Nothing structural
      produces "drop the bioRxiv PDF to ~/Downloads". They belong to the notes
      tier — self-report — not to trace evidence, and should be read as claims
      rather than as measurements.

    `name` is a large part of why this source exists: a dispatched job is named
    (nd-5) where a transcript may not be. It is not the only such source —
    `/rename` writes a `custom-title` record the transcript reader picks up —
    but it is the only one for a job with no surviving trace.

    `cwd`, `created_at` and `updated_at` are carried because this source can be
    the ONLY surviving record of a session — nd-3 has no transcript and no
    history.jsonl line anywhere — in which case the snapshot has to be enough to
    build a session row on its own: where it ran and when.
    """

    session_id: str
    name: str | None = None
    state: str | None = None
    needs: str | None = None
    detail: str | None = None
    cwd: str | None = None
    created_at: int | None = None
    updated_at: int | None = None


@dataclass(frozen=True)
class NoteRecord:
    """One row of `notes` — the indexed projection of a `/remember` capture.

    Deliberately NOT the whole record. The note file is truth; this carries only
    what makes a note *findable* (`sessions tagged X` as a query rather than a
    grep), so `text` is read off disk when someone actually wants it. That
    asymmetry is the point: losing this table costs a reindex, while losing the
    file costs the note, and a schema that copied everything would blur which of
    the two is the artifact.

    `relation` is absent on purpose: it is derived from `parent` and from the
    topics already in the thread (see `notes.derived_relation`), so storing it
    would be storing an answer the query can compute.

    `cwd_at_write` is the exception, and the reason it exists at all: it is not
    a `notes` column but the note's only statement of where it happened, so the
    project stays derivable after the transcript that knew the cwd is pruned.
    """

    ts: int | None = None
    kind: str | None = None           # info | handoff | bug | request | verification
    topic: str | None = None
    parent: str | None = None
    project: str | None = None        # authored override; NULL = the session's project
    title: str | None = None
    tags: tuple | list = ()
    entities: tuple | list = ()
    cwd_at_write: str | None = None


@dataclass(frozen=True)
class TurnRecord:
    """One row of `turns`. `idx` is assigned by the index, not the reader, so a
    resumed parse can continue numbering without the reader tracking state."""

    ts: int | None
    role: str | None
    kind: str            # text | thinking | tool_use | tool_result
    text: str
    tool_name: str | None = None
    truncated: bool = False
    raw_offset: int = 0
