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
    GRADE_SKELETON,
    KIND_MAIN,
    KIND_SUBAGENT,
    KIND_WORKFLOW,
    OUTCOME_AWAITING_QUESTION,
    OUTCOME_AWAITING_USER,
    OUTCOME_IN_FLIGHT,
    OUTCOME_INTERRUPTED,
    OUTCOME_TOOL_RESULT_LAST,
    OUTCOME_USER_LAST,
    TOOL_RESULT_CAP,
    JobStateRecord,
    NoteRecord,
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


def derive_outcome(tail: dict) -> str | None:
    """Classify how a session ended, from structure alone.

    `tail` is accumulated while scanning: the last assistant stop_reason, whether
    the final assistant turn called AskUserQuestion, whether its tool_use ever got
    a tool_result, whether an interrupt landed at the end, and who spoke last.

    There is deliberately no rule that inspects the text. "I am done" and "I have
    a question" look identical in a plain end_turn block, and a wrong label is
    worse than a coarse honest one.
    """
    if tail.get("interrupted_at_end"):
        return OUTCOME_INTERRUPTED
    if tail.get("asked_question"):
        return OUTCOME_AWAITING_QUESTION
    if tail.get("last_role") == "user" and not tail.get("last_was_tool_result"):
        return OUTCOME_USER_LAST
    if tail.get("pending_tool_use"):
        return OUTCOME_IN_FLIGHT
    if tail.get("last_role") == "assistant":
        return OUTCOME_AWAITING_USER
    if tail.get("last_was_tool_result"):
        # A tool returned and the model never spoke again — the ordinary terminal
        # state of a subagent transcript, and 73% of a real index before it had a
        # name. It reached here by construction: `last_role == "user"` is excluded
        # from user-last by last_was_tool_result, and that same tool_result has
        # already cleared pending_tool_use, so in-flight cannot fire either.
        # NOT in-flight: see records.py for why the two stay apart.
        return OUTCOME_TOOL_RESULT_LAST
    return None


def read_claude_transcript(
    path: Path, start_offset: int = 0
) -> tuple[SessionRecord | None, list[TurnRecord], int]:
    """Read a main Claude transcript. Returns (session, turns, end_offset)."""
    turns: list[TurnRecord] = []
    session_id = cwd = branch = title = None
    started = ended = None
    saw_any = False
    tail: dict = {}
    interrupts = denials = errors = 0
    last_stop = None

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

        if rec.get("interruptedMessageId"):
            interrupts += 1
            tail["interrupted_at_end"] = True
        else:
            tail.pop("interrupted_at_end", None)
        if rec.get("toolDenialKind"):
            denials += 1
        if rec.get("isApiErrorMessage"):
            errors += 1

        message = rec.get("message")
        if isinstance(message, dict):
            role = message.get("role")
            tail["last_role"] = role
            if message.get("stop_reason"):
                last_stop = message["stop_reason"]
            blocks = message.get("content")
            blocks = blocks if isinstance(blocks, list) else []
            names = [b.get("name") for b in blocks
                     if isinstance(b, dict) and b.get("type") == "tool_use"]
            kinds = {b.get("type") for b in blocks if isinstance(b, dict)}
            tail["last_was_tool_result"] = "tool_result" in kinds
            if role == "assistant":
                tail["asked_question"] = "AskUserQuestion" in names
                if names:
                    tail["pending_tool_use"] = True
            elif "tool_result" in kinds:
                tail["pending_tool_use"] = False

        turns.extend(_claude_turns(rec, offset))

    end_offset = path.stat().st_size
    if not saw_any or not session_id:
        return None, [], end_offset if saw_any else start_offset

    session = SessionRecord(
        id=session_id, kind=KIND_MAIN, agent="claude", source="claude-transcript",
        cwd=cwd, title=title, git_branch=branch,
        started=started, ended=ended, grade=GRADE_FULL,
        outcome=derive_outcome(tail), last_stop_reason=last_stop,
        n_interrupts=interrupts, n_tool_denials=denials, n_errors=errors,
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


def read_claude_history(
    path: Path, start_offset: int = 0
) -> tuple[list[SessionRecord], int]:
    """Read ~/.claude/history.jsonl into skeleton session rows.

    This is the only source that outlives transcript pruning — on one machine it
    knows 203 sessions where 13 transcripts survive. It has no turns, so rows are
    grade='skeleton' until a transcript upgrades them.
    """
    seen: dict[str, dict] = {}

    for _, rec in _iter_lines(path, start_offset):
        if rec is None:
            continue
        sid = rec.get("sessionId")
        if not sid:
            continue
        ts = _epoch_ms(rec.get("timestamp"))
        entry = seen.setdefault(sid, {
            "cwd": rec.get("project"), "title": rec.get("display"),
            "started": ts, "ended": ts,
        })
        if ts is not None:
            entry["started"] = ts if entry["started"] is None else min(entry["started"], ts)
            entry["ended"] = ts if entry["ended"] is None else max(entry["ended"], ts)

    sessions = [
        SessionRecord(
            id=sid, kind=KIND_MAIN, agent="claude", source="claude-history",
            cwd=e["cwd"], title=e["title"],
            started=e["started"], ended=e["ended"], grade=GRADE_SKELETON,
        )
        for sid, e in seen.items()
    ]
    return sessions, path.stat().st_size


def read_job_state(path: Path, start_offset: int = 0) -> tuple[list[JobStateRecord], int]:
    """Read a `state-history.jsonl` into the latest snapshot per sessionId.

    The archiver turns the harness's mutable `jobs/<short>/state.json` into this
    append-only log, one line per distinct `updatedAt`. Earlier lines are kept
    because the sequence of transitions is worth having, but only the newest
    line for a session describes it now, so that is what this yields.

    One job directory usually holds one session, but a resume can put a second
    id in the same log — hence a dict rather than a single record.
    """
    latest: dict[str, JobStateRecord] = {}

    for _, rec in _iter_lines(path, start_offset):
        if rec is None:
            continue
        sid = rec.get("sessionId")
        if not sid:
            continue
        latest[sid] = JobStateRecord(          # append order is chronological: last wins
            session_id=sid,
            name=rec.get("name"),
            state=rec.get("state"),
            needs=rec.get("needs"),
            detail=rec.get("detail"),
            cwd=rec.get("cwd"),
            created_at=_epoch_ms(rec.get("createdAt")),
            updated_at=_epoch_ms(rec.get("updatedAt")),
        )

    return list(latest.values()), path.stat().st_size


def _as_list(value) -> list:
    """Coerce a scalar to a one-element list; drop nothing.

    Notes are composed by a model, and `"tags": "notes"` instead of
    `["notes"]` is a plausible slip. Dropping the value would quietly cost the
    note its only search key.
    """
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def read_notes(path: Path, start_offset: int = 0) -> tuple[list[NoteRecord], int]:
    """Read a `/remember` note file into indexable records.

    The one reader whose source is not a trace. Everything else here parses
    something an agent emitted as a by-product; this parses something an agent
    was asked to write, which is why it is the tier that can never be
    re-derived. Same append-only shape though, so the same resume-from-offset
    mechanism works unchanged — `notes_offset` is `parsed_offset` on a different
    file.

    Tolerant for the usual reason and one extra: these records come straight
    from a model, so absent fields are ordinary rather than corrupt. A note with
    nothing but a title still gets a row.
    """
    notes: list[NoteRecord] = []

    for _, rec in _iter_lines(path, start_offset):
        if not isinstance(rec, dict):
            continue                      # None from a malformed line, or a bare scalar
        notes.append(NoteRecord(
            ts=_epoch_ms(rec.get("ts")),
            topic=rec.get("topic"),
            relation=rec.get("relation"),
            parent=rec.get("parent"),
            title=rec.get("title"),
            tags=_as_list(rec.get("tags")),
            entities=_as_list(rec.get("entities")),
            cwd_at_write=rec.get("cwd_at_write"),
        ))

    return notes, path.stat().st_size


def _codex_message_text(content) -> str:
    """Codex message content is a list of blocks with a `text` field."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            b.get("text") or "" for b in content if isinstance(b, dict)
        )
    return _as_text(content)


def read_codex_rollout(
    path: Path, start_offset: int = 0
) -> tuple[SessionRecord | None, list[TurnRecord], int]:
    """Read a codex rollout.

    Two things codex does that Claude does not, both verified against real files:

    1. Every turn is recorded TWICE — once as `event_msg`, once as `response_item`
       with identical content. `response_item` is canonical because it carries
       `call_id`, which pairs a tool call to its output. Reading both would
       duplicate the entire corpus.
    2. Reasoning is summary-only. `response_item/reasoning` has `content: null`
       and ~1 KB of `encrypted_content` we cannot read; the plaintext is a short
       summary. We take that and never store the ciphertext.

    The tail is tracked the same way the Claude reader tracks it, so the same
    `derive_outcome` yields the same vocabulary from codex's differently-named
    shapes. Only the pairing differs: codex gives every call a `call_id`, so an
    unanswered call is identified by identity rather than by position, which
    also gets the parallel-call case right — two calls and one output leaves the
    session in-flight, not tool-result-last. `interrupted_at_end` and
    `asked_question` never fire here: codex has no interrupt marker in the
    rollout and no AskUserQuestion tool, so `awaiting-question` cannot occur.
    """
    turns: list[TurnRecord] = []
    session_id = cwd = None
    started = ended = None
    tail: dict = {}
    open_calls: set = set()      # call_ids still awaiting their output

    for offset, rec in _iter_lines(path, start_offset):
        if rec is None:
            continue
        rtype = rec.get("type")
        payload = rec.get("payload")
        if not isinstance(payload, dict):
            continue
        ts = _epoch_ms(rec.get("timestamp"))
        if ts:
            started = ts if started is None else min(started, ts)
            ended = ts if ended is None else max(ended, ts)

        if rtype == "session_meta":
            session_id = payload.get("id") or session_id
            cwd = payload.get("cwd") or cwd
            continue

        if rtype == "turn_context":
            cwd = payload.get("cwd") or cwd     # cwd can change mid-session
            continue

        if rtype != "response_item":
            continue                            # event_msg is the duplicate stream

        ptype = payload.get("type")
        if ptype == "message":
            tail["last_role"] = payload.get("role")
            tail["last_was_tool_result"] = False
            turns.append(TurnRecord(
                ts, payload.get("role"), "text",
                _codex_message_text(payload.get("content")), raw_offset=offset,
            ))
        elif ptype == "reasoning":
            summary = payload.get("summary")
            text = ""
            if isinstance(summary, list):
                text = "".join(
                    s.get("text") or "" for s in summary if isinstance(s, dict)
                )
            if text:
                turns.append(TurnRecord(ts, "assistant", "thinking", text, raw_offset=offset))
        elif ptype in ("function_call", "custom_tool_call"):
            open_calls.add(payload.get("call_id"))
            tail["last_role"] = "assistant"
            tail["last_was_tool_result"] = False
            tail["pending_tool_use"] = True
            body, truncated = _cap(_as_text(payload.get("arguments") or payload.get("input")))
            turns.append(TurnRecord(
                ts, "assistant", "tool_use", body,
                tool_name=payload.get("name"), truncated=truncated, raw_offset=offset,
            ))
        elif ptype in ("function_call_output", "custom_tool_call_output"):
            open_calls.discard(payload.get("call_id"))
            tail["last_role"] = "tool"
            tail["last_was_tool_result"] = True
            tail["pending_tool_use"] = bool(open_calls)
            body, truncated = _cap(_as_text(payload.get("output")))
            turns.append(TurnRecord(
                ts, "tool", "tool_result", body, truncated=truncated, raw_offset=offset,
            ))

    end_offset = path.stat().st_size
    if not session_id:
        return None, [], end_offset

    session = SessionRecord(
        id=session_id, kind=KIND_MAIN, agent="codex", source="codex-rollout",
        cwd=cwd, started=started, ended=ended, grade=GRADE_FULL,
        outcome=derive_outcome(tail),
        # last_stop_reason has no codex analogue; NULL beats an invented one.
    )
    return session, turns, end_offset


# --- kimi ---------------------------------------------------------------------

# The agent directory that is the session itself. Anything else under `agents/`
# is a subagent it spawned.
KIMI_MAIN_AGENT = "main"

# Where a session's state.json lands after the archiver has been over it. It is a
# mutable object, not a log, so `archive_json_snapshot` appends each distinct
# version as one line under the job-state name; the newest line is the current
# state. Reading either shape means the reader works on the live tree and on the
# archive without knowing which it was handed.
KIMI_STATE_NAMES = ("state.json", "state-history.jsonl")


def _kimi_location(path: Path) -> tuple[Path | None, str | None]:
    """(session directory, agent name) for a wire.jsonl. (None, None) if neither.

    ~/.kimi-code/sessions/wd_<name>_<hash>/session_<uuid>/agents/<name>/wire.jsonl
    """
    for parent in path.parents:
        if parent.name == "agents":
            return parent.parent, path.relative_to(parent).parts[0]
    for parent in path.parents:                 # a session dir with no agents/ level
        if parent.name.startswith("session_"):
            return parent, None
    return None, None


def kimi_identity_from_path(path: Path) -> dict:
    """Derive row identity from where a wire sits, not from its contents.

    Same rule as `identity_from_path`, and for the same reason: nothing inside a
    kimi wire names its session, and the agent names that DO appear (`agent-0`,
    `agent-1`) are per-session labels rather than ids — keying on one would merge
    every session's `agent-0` into a single row. So the session uuid comes from
    the `session_<uuid>` directory and a subagent's id is that uuid qualified by
    its agent name.
    """
    session_dir, agent = _kimi_location(path)
    if session_dir is None:
        return {"kind": KIND_MAIN, "id": None, "parent_session_id": None,
                "agent_id": None, "session_dir": None}

    name = session_dir.name
    sid = name[len("session_"):] if name.startswith("session_") else name

    if agent is None or agent == KIMI_MAIN_AGENT:
        return {"kind": KIND_MAIN, "id": sid, "parent_session_id": None,
                "agent_id": None, "session_dir": session_dir}
    return {"kind": KIND_SUBAGENT, "id": f"{sid}:{agent}", "parent_session_id": sid,
            "agent_id": agent, "session_dir": session_dir}


def _kimi_state(session_dir: Path | None) -> dict:
    """The session's state.json, live or archived. `{}` when there is none.

    Never raises: a session whose state.json is missing, half-written or gone is
    still a session, and losing its cwd is a smaller loss than losing the wire.
    """
    if session_dir is None:
        return {}
    for name in KIMI_STATE_NAMES:
        candidate = session_dir / name
        if not candidate.is_file():
            continue
        try:
            raw = candidate.read_bytes()
        except OSError:
            continue
        if name.endswith(".jsonl"):
            lines = [ln for ln in raw.splitlines() if ln.strip()]
            raw = lines[-1] if lines else b""    # newest version wins
        try:
            obj = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            continue
        if isinstance(obj, dict):
            return obj
    return {}


def _kimi_text(content) -> str:
    """Kimi message content is a list of blocks with a `text` field."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(b.get("text") or "" for b in content if isinstance(b, dict))
    return _as_text(content)


def _kimi_result_text(result) -> str:
    """The tool's output, without the harness's annotation.

    `result.note` is a `<system>…lines read from file…</system>` remark the
    harness adds about paging; it is not what the tool returned, so it is not
    stored as if it were.
    """
    if isinstance(result, dict):
        output = result.get("output")
        if isinstance(output, str):
            return output
        if output is not None:
            return _as_text(output)
    return _as_text(result)


def read_kimi_wire(
    path: Path, start_offset: int = 0
) -> tuple[SessionRecord | None, list[TurnRecord], int]:
    """Read one kimi agent's wire.jsonl. Returns (session, turns, end_offset).

    CANONICAL SOURCE — the trap this reader exists to avoid. A kimi wire records
    every user turn TWICE: once as `turn.prompt` (or `turn.steer` for a mid-turn
    interjection) and once, verbatim, as a `context.append_message` with
    `role: "user"`. Verified across the whole corpus: not one prompt appears
    without its matching message. Reading both would double every user turn,
    exactly as reading codex's `event_msg` alongside `response_item` would double
    that corpus.

    `context.append_message` is canonical, for two reasons:

    1. It is a strict superset. Beyond the prompts and steers it also carries the
       injections — `<system-reminder>` blocks, `local-command-stdout` — that
       `turn.prompt` never sees, and those are real context the model read.
    2. It is what the model was actually given. `turn.prompt` is the UI event;
       `context.append_message` is the context edit that followed it.

    Assistant output is not in either stream: it arrives as
    `context.append_loop_event`, whose `content.part` blocks carry text and
    thinking and whose `tool.call` / `tool.result` pairs carry the tools. Those
    events appear once, so no de-duplication is needed there. `llm.request` is
    skipped for the same reason `event_msg` is — it is the request built from the
    context we already read.

    Tolerances match the other readers: unknown record and event types are
    ignored, malformed lines are skipped rather than raised on, and the parse
    resumes from `start_offset`.
    """
    ident = kimi_identity_from_path(path)
    state = _kimi_state(ident["session_dir"])

    turns: list[TurnRecord] = []
    started = ended = None
    saw_any = False
    tail: dict = {}
    open_calls: set = set()          # toolCallIds still awaiting their result
    last_stop = None
    denials = 0

    for offset, rec in _iter_lines(path, start_offset):
        if not isinstance(rec, dict):
            continue
        saw_any = True
        ts = _epoch_ms(rec.get("time"))
        if ts:                        # span covers every record, turn-bearing or not
            started = ts if started is None else min(started, ts)
            ended = ts if ended is None else max(ended, ts)
        rtype = rec.get("type")

        if rtype == "context.append_message":
            message = rec.get("message")
            if not isinstance(message, dict):
                continue
            role = message.get("role") or "user"
            tail.update(last_role=role, last_was_tool_result=False,
                        asked_question=False)
            turns.append(TurnRecord(
                ts, role, "text", _kimi_text(message.get("content")), raw_offset=offset))
            continue

        if rtype == "permission.record_approval_result":
            result = rec.get("result")
            decision = result.get("decision") if isinstance(result, dict) else None
            # Only "approved" appears in the corpus. Treating anything else as a
            # refusal over-counts if kimi ever grows a second approving word,
            # which is the safe direction for a column that exists to surface
            # friction — a missed denial is invisible, a spurious one is not.
            if decision is not None and decision != "approved":
                denials += 1
            continue

        if rtype != "context.append_loop_event":
            continue                  # turn.prompt / turn.steer are the duplicate stream

        event = rec.get("event")
        if not isinstance(event, dict):
            continue
        etype = event.get("type")

        if etype == "content.part":
            part = event.get("part") if isinstance(event.get("part"), dict) else {}
            ptype = part.get("type")
            if ptype == "text":
                kind, text = "text", part.get("text") or ""
            elif ptype == "think":
                kind, text = "thinking", part.get("think") or ""
            else:
                continue              # a future part shape we do not need to understand
            tail.update(last_role="assistant", last_was_tool_result=False,
                        asked_question=False)
            turns.append(TurnRecord(ts, "assistant", kind, text, raw_offset=offset))

        elif etype == "tool.call":
            open_calls.add(event.get("toolCallId") or event.get("uuid"))
            tail.update(last_role="assistant", last_was_tool_result=False,
                        pending_tool_use=True,
                        # True only while the ask is the LAST thing in the file:
                        # any later record clears it, so a question that was
                        # answered does not leave the session labelled as waiting.
                        asked_question=event.get("name") == "AskUserQuestion")
            body, truncated = _cap(_as_text(event.get("args")))
            turns.append(TurnRecord(
                ts, "assistant", "tool_use", body, tool_name=event.get("name"),
                truncated=truncated, raw_offset=offset))

        elif etype == "tool.result":
            open_calls.discard(event.get("toolCallId") or event.get("parentUuid"))
            tail.update(last_role="tool", last_was_tool_result=True,
                        pending_tool_use=bool(open_calls), asked_question=False)
            body, truncated = _cap(_kimi_result_text(event.get("result")))
            turns.append(TurnRecord(
                ts, "tool", "tool_result", body, truncated=truncated, raw_offset=offset))

        elif etype == "step.end" and event.get("finishReason"):
            last_stop = event["finishReason"]

    end_offset = path.stat().st_size
    if not saw_any or not ident["id"]:
        return None, [], end_offset if saw_any else start_offset

    is_main = ident["kind"] == KIND_MAIN
    state_started = _epoch_ms(state.get("createdAt"))
    state_ended = _epoch_ms(state.get("updatedAt"))
    if is_main:
        # main IS the session, so its own span is the session's span. A subagent's
        # is not: state.json describes the whole session, and stretching a
        # subagent that ran for a minute across nine hours would be a lie.
        started = min(x for x in (started, state_started) if x is not None) \
            if (started or state_started) else None
        ended = max(x for x in (ended, state_ended) if x is not None) \
            if (ended or state_ended) else None
    else:
        started = started if started is not None else state_started
        ended = ended if ended is not None else state_ended

    session = SessionRecord(
        id=ident["id"], kind=ident["kind"], agent="kimi", source="kimi-wire",
        parent_session_id=ident["parent_session_id"], agent_id=ident["agent_id"],
        cwd=state.get("workDir"),
        title=state.get("title") if is_main else None,   # the title names the session
        started=started, ended=ended, grade=GRADE_FULL,
        outcome=derive_outcome(tail), last_stop_reason=last_stop,
        n_tool_denials=denials,
        # n_interrupts and n_errors stay 0: a kimi wire has no abort marker and no
        # error marker. `turn.steer` is a mid-turn interjection, not a stop — and
        # it is part of the duplicate stream besides. A guessed count would be
        # worse than an honest zero.
    )
    return session, turns, end_offset
