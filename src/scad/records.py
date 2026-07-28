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
