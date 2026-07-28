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
