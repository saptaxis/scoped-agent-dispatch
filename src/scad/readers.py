"""Per-format readers turning raw JSONL into normalized records.

One reader per format, tolerant by design: a year of traces spans many agent
versions, so unknown record types and content blocks are ignored, malformed lines
are counted and skipped, and nothing here raises on bad input. Silence about
drift would be worse than drift, so counts are returned for reporting.
"""

import json
from datetime import datetime
from pathlib import Path

from scad.records import (
    GRADE_FULL,
    KIND_MAIN,
    KIND_SUBAGENT,
    KIND_WORKFLOW,
    TOOL_RESULT_CAP,
    SessionRecord,
    TurnRecord,
)

# Claude line types that carry no conversation. ~45% of a real transcript.
_CLAUDE_SKIP_TYPES = {
    "file-history-snapshot", "mode", "permission-mode", "queue-operation",
    "attachment", "last-prompt", "system",
}


def _epoch_ms(value) -> int | None:
    """Accept ISO-8601 (Claude) or epoch ms (history.jsonl); ignore anything else."""
    if isinstance(value, (int, float)):
        return int(value)
    if not isinstance(value, str) or not value:
        return None
    try:
        return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp() * 1000)
    except ValueError:
        return None


def _cap(text: str) -> tuple[str, bool]:
    if len(text) <= TOOL_RESULT_CAP:
        return text, False
    return text[:TOOL_RESULT_CAP], True


def _as_text(value) -> str:
    return value if isinstance(value, str) else json.dumps(value, default=str)


def _iter_lines(path: Path, start_offset: int):
    """Yield (offset, parsed_dict) for complete lines from start_offset.

    Malformed lines yield None so the caller can count them without branching on
    exceptions.
    """
    with path.open("rb") as fh:
        fh.seek(start_offset)
        offset = start_offset
        for raw in fh:
            here, offset = offset, offset + len(raw)
            if not raw.strip():
                continue
            try:
                yield here, json.loads(raw)
            except (ValueError, UnicodeDecodeError):
                yield here, None


def _claude_turns(record: dict, offset: int) -> list[TurnRecord]:
    """Explode one Claude message into typed turns, one per content block."""
    message = record.get("message")
    if not isinstance(message, dict):
        return []
    role = message.get("role")
    ts = _epoch_ms(record.get("timestamp"))
    content = message.get("content")

    if isinstance(content, str):
        return [TurnRecord(ts=ts, role=role, kind="text", text=content, raw_offset=offset)]

    turns = []
    for block in content if isinstance(content, list) else []:
        if not isinstance(block, dict):
            continue
        btype = block.get("type")
        if btype == "text":
            turns.append(TurnRecord(ts, role, "text", block.get("text") or "", raw_offset=offset))
        elif btype == "thinking":
            turns.append(TurnRecord(ts, role, "thinking", block.get("thinking") or "", raw_offset=offset))
        elif btype == "tool_use":
            turns.append(TurnRecord(
                ts, role, "tool_use", _as_text(block.get("input")),
                tool_name=block.get("name"), raw_offset=offset,
            ))
        elif btype == "tool_result":
            text, truncated = _cap(_as_text(block.get("content")))
            turns.append(TurnRecord(ts, role, "tool_result", text, truncated=truncated, raw_offset=offset))
        # any other block type is a future shape we do not need to understand
    return turns


def read_claude_transcript(
    path: Path, start_offset: int = 0
) -> tuple[SessionRecord | None, list[TurnRecord], int]:
    """Read a main Claude transcript. Returns (session, turns, end_offset)."""
    turns: list[TurnRecord] = []
    session_id = cwd = branch = title = None
    started = ended = None
    saw_any = False

    for offset, rec in _iter_lines(path, start_offset):
        if rec is None:
            continue
        saw_any = True
        session_id = session_id or rec.get("sessionId")
        cwd = cwd or rec.get("cwd")
        branch = branch or rec.get("gitBranch")
        if rec.get("aiTitle"):
            title = rec["aiTitle"]          # the agent rewrites this as a session develops
        ts = _epoch_ms(rec.get("timestamp"))
        if ts:
            started = ts if started is None else min(started, ts)
            ended = ts if ended is None else max(ended, ts)
        if rec.get("type") in _CLAUDE_SKIP_TYPES:
            continue
        turns.extend(_claude_turns(rec, offset))

    end_offset = path.stat().st_size
    if not saw_any or not session_id:
        return None, [], end_offset if saw_any else start_offset

    session = SessionRecord(
        id=session_id, kind=KIND_MAIN, agent="claude", source="claude-transcript",
        cwd=cwd, title=title, git_branch=branch,
        started=started, ended=ended, grade=GRADE_FULL,
    )
    return session, turns, end_offset


def identity_from_path(path: Path) -> dict:
    """Derive row identity from where a transcript sits, not from its contents.

    This exists because a subagent file's `sessionId` field is its PARENT's uuid.
    The records cannot tell you which subagent you are reading; only the path can:

        projects/<cwd>/<parent>.jsonl                                  -> main
        projects/<cwd>/<parent>/subagents/agent-<id>.jsonl             -> subagent
        projects/<cwd>/<parent>/subagents/workflows/wf_<w>/agent-<id>.jsonl
                                                                       -> workflow-agent
    """
    parts = path.parts
    stem = path.stem

    if "subagents" not in parts:
        return {"kind": KIND_MAIN, "id": stem, "parent_session_id": None,
                "agent_id": None, "workflow_id": None}

    sub = parts.index("subagents")
    parent = parts[sub - 1]
    agent_id = stem[len("agent-"):] if stem.startswith("agent-") else stem
    workflow_id = None
    kind = KIND_SUBAGENT

    if "workflows" in parts[sub:]:
        wf = parts.index("workflows", sub)
        if wf + 1 < len(parts) - 1:
            workflow_id = parts[wf + 1]
            kind = KIND_WORKFLOW

    return {"kind": kind, "id": agent_id, "parent_session_id": parent,
            "agent_id": agent_id, "workflow_id": workflow_id}


def read_claude_any(
    path: Path, start_offset: int = 0
) -> tuple[SessionRecord | None, list[TurnRecord], int]:
    """Read any Claude transcript — main, subagent, or workflow agent.

    The record format is identical; only identity differs, so this reuses the
    transcript reader and re-keys the session from the path.
    """
    session, turns, end = read_claude_transcript(path, start_offset)
    ident = identity_from_path(path)

    if session is None:
        return None, [], end

    if ident["kind"] == KIND_MAIN:
        return session, turns, end

    from dataclasses import replace

    session = replace(
        session,
        id=ident["id"],
        kind=ident["kind"],
        source="claude-subagent",
        parent_session_id=ident["parent_session_id"],
        agent_id=ident["agent_id"],
        workflow_id=ident["workflow_id"],
        title=None,          # only main sessions carry aiTitle
    )
    return session, turns, end
