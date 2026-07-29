"""Tests for the per-format trace readers."""

import json
from pathlib import Path

from scad.readers import read_claude_transcript
from scad.records import GRADE_FULL, KIND_MAIN


def write_jsonl(path: Path, records: list[dict]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in records))
    return path


CLAUDE_LINES = [
    {"type": "user", "sessionId": "S1", "uuid": "u1", "timestamp": "2026-07-28T10:00:00.000Z",
     "cwd": "/repo", "gitBranch": "main", "version": "2.0.0",
     "message": {"role": "user", "content": "do the thing"}},
    {"type": "assistant", "sessionId": "S1", "uuid": "u2", "timestamp": "2026-07-28T10:00:05.000Z",
     "cwd": "/repo", "gitBranch": "main",
     "message": {"role": "assistant", "content": [
         {"type": "thinking", "thinking": "considering"},
         {"type": "text", "text": "on it"},
         {"type": "tool_use", "name": "Bash", "input": {"command": "ls"}},
     ]}},
    {"type": "user", "sessionId": "S1", "uuid": "u3", "timestamp": "2026-07-28T10:00:07.000Z",
     "message": {"role": "user", "content": [
         {"type": "tool_result", "content": "a.txt\nb.txt"},
     ]}},
    {"type": "ai-title", "sessionId": "S1", "aiTitle": "Doing the thing"},
    {"type": "file-history-snapshot", "sessionId": "S1", "snapshot": {"x": 1}},
    {"type": "queue-operation", "sessionId": "S1", "operation": "add"},
]


class TestClaudeTranscript:
    def test_session_fields(self, tmp_path):
        p = write_jsonl(tmp_path / "S1.jsonl", CLAUDE_LINES)
        session, turns, end = read_claude_transcript(p)
        assert session.id == "S1"
        assert session.kind == KIND_MAIN
        assert session.agent == "claude"
        assert session.source == "claude-transcript"
        assert session.cwd == "/repo"
        assert session.git_branch == "main"
        assert session.title == "Doing the thing"
        assert session.grade == GRADE_FULL

    def test_started_and_ended_span_the_timestamps(self, tmp_path):
        p = write_jsonl(tmp_path / "S1.jsonl", CLAUDE_LINES)
        session, _, _ = read_claude_transcript(p)
        assert session.started < session.ended
        assert session.started > 0

    def test_content_blocks_become_typed_turns(self, tmp_path):
        p = write_jsonl(tmp_path / "S1.jsonl", CLAUDE_LINES)
        _, turns, _ = read_claude_transcript(p)
        kinds = [t.kind for t in turns]
        assert kinds == ["text", "thinking", "text", "tool_use", "tool_result"]
        assert turns[1].text == "considering"
        assert turns[3].tool_name == "Bash"
        assert turns[4].text == "a.txt\nb.txt"

    def test_non_conversation_lines_are_skipped(self, tmp_path):
        """~45% of real lines are snapshots, modes, queue operations."""
        p = write_jsonl(tmp_path / "S1.jsonl", CLAUDE_LINES)
        _, turns, _ = read_claude_transcript(p)
        assert len(turns) == 5

    def test_every_turn_carries_a_usable_raw_offset(self, tmp_path):
        p = write_jsonl(tmp_path / "S1.jsonl", CLAUDE_LINES)
        _, turns, _ = read_claude_transcript(p)
        raw = p.read_bytes()
        for t in turns:
            line = raw[t.raw_offset:raw.index(b"\n", t.raw_offset)]
            json.loads(line)          # the offset points at a real record

    def test_end_offset_is_the_file_size(self, tmp_path):
        p = write_jsonl(tmp_path / "S1.jsonl", CLAUDE_LINES)
        _, _, end = read_claude_transcript(p)
        assert end == p.stat().st_size

    def test_resume_from_offset_reads_only_new_lines(self, tmp_path):
        p = write_jsonl(tmp_path / "S1.jsonl", CLAUDE_LINES)
        _, _, first_end = read_claude_transcript(p)
        with p.open("a") as fh:
            fh.write(json.dumps({
                "type": "assistant", "sessionId": "S1", "timestamp": "2026-07-28T11:00:00.000Z",
                "message": {"role": "assistant", "content": [{"type": "text", "text": "more"}]},
            }) + "\n")
        _, turns, end = read_claude_transcript(p, start_offset=first_end)
        assert [t.text for t in turns] == ["more"]
        assert end == p.stat().st_size

    def test_malformed_lines_are_skipped_not_fatal(self, tmp_path):
        p = tmp_path / "S1.jsonl"
        p.write_text(
            json.dumps(CLAUDE_LINES[0]) + "\n"
            + "{ this is not json\n"
            + json.dumps(CLAUDE_LINES[1]) + "\n"
        )
        session, turns, _ = read_claude_transcript(p)
        assert session.id == "S1"
        assert len(turns) == 4          # 1 user text + 3 assistant blocks

    def test_unknown_content_block_is_ignored(self, tmp_path):
        p = write_jsonl(tmp_path / "S1.jsonl", [{
            "type": "assistant", "sessionId": "S1", "timestamp": "2026-07-28T10:00:00.000Z",
            "message": {"role": "assistant", "content": [
                {"type": "some_future_block", "data": 1},
                {"type": "text", "text": "kept"},
            ]},
        }])
        _, turns, _ = read_claude_transcript(p)
        assert [t.kind for t in turns] == ["text"]

    def test_empty_file_yields_no_session(self, tmp_path):
        p = tmp_path / "empty.jsonl"
        p.write_text("")
        session, turns, end = read_claude_transcript(p)
        assert session is None
        assert turns == []
        assert end == 0

    def test_large_tool_result_is_capped_and_flagged(self, tmp_path):
        big = "x" * 100_000
        p = write_jsonl(tmp_path / "S1.jsonl", [{
            "type": "user", "sessionId": "S1", "timestamp": "2026-07-28T10:00:00.000Z",
            "message": {"role": "user", "content": [{"type": "tool_result", "content": big}]},
        }])
        _, turns, _ = read_claude_transcript(p)
        assert turns[0].truncated is True
        assert len(turns[0].text) == 65536
        assert turns[0].raw_offset == 0     # full content still reachable


from scad.readers import identity_from_path, read_claude_any  # noqa: E402
from scad.records import KIND_SUBAGENT, KIND_WORKFLOW  # noqa: E402


SUBAGENT_LINES = [
    {"type": "assistant", "sessionId": "PARENT-UUID", "agentId": "a0778b6f68fa2fe99",
     "isSidechain": True, "slug": "frolicking-knitting-lantern",
     "timestamp": "2026-07-28T10:00:00.000Z", "cwd": "/repo",
     "message": {"role": "assistant", "content": [{"type": "text", "text": "subagent work"}]}},
]


class TestIdentityFromPath:
    def test_main_transcript(self, tmp_path):
        p = tmp_path / "projects" / "-repo" / "SESSION-UUID.jsonl"
        ident = identity_from_path(p)
        assert ident["kind"] == KIND_MAIN
        assert ident["id"] == "SESSION-UUID"
        assert ident["parent_session_id"] is None
        assert ident["agent_id"] is None

    def test_subagent(self, tmp_path):
        p = tmp_path / "projects" / "-repo" / "PARENT-UUID" / "subagents" / "agent-a0778b.jsonl"
        ident = identity_from_path(p)
        assert ident["kind"] == KIND_SUBAGENT
        assert ident["id"] == "a0778b"
        assert ident["agent_id"] == "a0778b"
        assert ident["parent_session_id"] == "PARENT-UUID"
        assert ident["workflow_id"] is None

    def test_workflow_agent(self, tmp_path):
        p = (tmp_path / "projects" / "-repo" / "PARENT-UUID" / "subagents"
             / "workflows" / "wf_08b34d98" / "agent-abc.jsonl")
        ident = identity_from_path(p)
        assert ident["kind"] == KIND_WORKFLOW
        assert ident["id"] == "abc"
        assert ident["parent_session_id"] == "PARENT-UUID"
        assert ident["workflow_id"] == "wf_08b34d98"


class TestReadClaudeAny:
    def test_subagent_keys_on_agent_id_not_session_id(self, tmp_path):
        """The bug this guards: sessionId is the PARENT's, so keying on it
        collapses every subagent of a session onto one row."""
        p = write_jsonl(
            tmp_path / "projects" / "-repo" / "PARENT-UUID" / "subagents" / "agent-a0778b.jsonl",
            SUBAGENT_LINES,
        )
        session, turns, _ = read_claude_any(p)
        assert session.id == "a0778b"
        assert session.id != "PARENT-UUID"
        assert session.parent_session_id == "PARENT-UUID"
        assert session.kind == KIND_SUBAGENT
        assert session.source == "claude-subagent"
        assert session.title is None          # only main sessions get aiTitle
        assert [t.text for t in turns] == ["subagent work"]

    def test_two_subagents_of_one_parent_are_two_rows(self, tmp_path):
        base = tmp_path / "projects" / "-repo" / "PARENT-UUID" / "subagents"
        a = write_jsonl(base / "agent-aaa.jsonl", SUBAGENT_LINES)
        b = write_jsonl(base / "agent-bbb.jsonl", SUBAGENT_LINES)
        ids = {read_claude_any(a)[0].id, read_claude_any(b)[0].id}
        assert ids == {"aaa", "bbb"}

    def test_main_transcript_still_reads_as_main(self, tmp_path):
        p = write_jsonl(tmp_path / "projects" / "-repo" / "S1.jsonl", CLAUDE_LINES)
        session, _, _ = read_claude_any(p)
        assert session.kind == KIND_MAIN
        assert session.id == "S1"
        assert session.source == "claude-transcript"


from scad.readers import read_claude_history  # noqa: E402
from scad.records import GRADE_SKELETON  # noqa: E402


HISTORY_LINES = [
    {"display": "/plugin ", "timestamp": 1772182041158,
     "project": "/Users/vsr/vsr-tmp", "sessionId": "S-OLD"},
    {"display": "what next?", "timestamp": 1783860843984,
     "project": "/Users/vsr/code/nd", "sessionId": "S-NEW"},
    {"display": "and again", "timestamp": 1783860999999,
     "project": "/Users/vsr/code/nd", "sessionId": "S-NEW"},
]


class TestClaudeHistory:
    def test_one_row_per_distinct_session(self, tmp_path):
        p = write_jsonl(tmp_path / "history.jsonl", HISTORY_LINES)
        sessions, _ = read_claude_history(p)
        assert {s.id for s in sessions} == {"S-OLD", "S-NEW"}

    def test_rows_are_skeleton_with_no_turns(self, tmp_path):
        p = write_jsonl(tmp_path / "history.jsonl", HISTORY_LINES)
        sessions, _ = read_claude_history(p)
        for s in sessions:
            assert s.grade == GRADE_SKELETON
            assert s.source == "claude-history"
            assert s.kind == KIND_MAIN

    def test_project_field_becomes_cwd(self, tmp_path):
        p = write_jsonl(tmp_path / "history.jsonl", HISTORY_LINES)
        sessions, _ = read_claude_history(p)
        by_id = {s.id: s for s in sessions}
        assert by_id["S-NEW"].cwd == "/Users/vsr/code/nd"

    def test_started_and_ended_span_that_session_only(self, tmp_path):
        p = write_jsonl(tmp_path / "history.jsonl", HISTORY_LINES)
        by_id = {s.id: s for s in read_claude_history(p)[0]}
        assert by_id["S-NEW"].started == 1783860843984
        assert by_id["S-NEW"].ended == 1783860999999

    def test_first_prompt_becomes_the_title(self, tmp_path):
        """A skeleton has no aiTitle; the first prompt is the only label available."""
        p = write_jsonl(tmp_path / "history.jsonl", HISTORY_LINES)
        by_id = {s.id: s for s in read_claude_history(p)[0]}
        assert by_id["S-NEW"].title == "what next?"

    def test_lines_without_a_session_id_are_ignored(self, tmp_path):
        p = write_jsonl(tmp_path / "history.jsonl", [{"display": "x", "timestamp": 1}])
        sessions, _ = read_claude_history(p)
        assert sessions == []

    def test_end_offset_supports_resume(self, tmp_path):
        p = write_jsonl(tmp_path / "history.jsonl", HISTORY_LINES)
        _, end = read_claude_history(p)
        assert end == p.stat().st_size


from scad.readers import read_codex_rollout  # noqa: E402


CODEX_LINES = [
    {"timestamp": "2026-07-25T11:31:14.000Z", "type": "session_meta",
     "payload": {"id": "C1", "timestamp": "2026-07-25T11:31:14.000Z", "cwd": "/repo",
                 "originator": "cli", "cli_version": "1.2.3", "model_provider": "openai"}},
    {"timestamp": "2026-07-25T11:31:20.000Z", "type": "turn_context",
     "payload": {"cwd": "/repo", "model": "gpt-x", "approval_policy": "auto"}},
    {"timestamp": "2026-07-25T11:31:25.000Z", "type": "response_item",
     "payload": {"type": "message", "role": "assistant",
                 "content": [{"type": "output_text", "text": "the answer"}]}},
    {"timestamp": "2026-07-25T11:31:25.000Z", "type": "event_msg",
     "payload": {"type": "agent_message", "message": "the answer"}},
    {"timestamp": "2026-07-25T11:31:30.000Z", "type": "response_item",
     "payload": {"type": "reasoning", "content": None,
                 "summary": [{"type": "summary_text", "text": "**Running shell command**"}],
                 "encrypted_content": "gAAAAABpgLPZ" + "x" * 900}},
    {"timestamp": "2026-07-25T11:31:31.000Z", "type": "event_msg",
     "payload": {"type": "agent_reasoning", "text": "**Running shell command**"}},
    {"timestamp": "2026-07-25T11:31:35.000Z", "type": "response_item",
     "payload": {"type": "function_call", "name": "shell",
                 "arguments": "{\"cmd\":\"ls\"}", "call_id": "c1"}},
    {"timestamp": "2026-07-25T11:31:36.000Z", "type": "response_item",
     "payload": {"type": "function_call_output", "call_id": "c1", "output": "a.txt"}},
]


class TestCodexRollout:
    def test_session_comes_from_session_meta(self, tmp_path):
        p = write_jsonl(tmp_path / "rollout.jsonl", CODEX_LINES)
        session, _, _ = read_codex_rollout(p)
        assert session.id == "C1"
        assert session.agent == "codex"
        assert session.source == "codex-rollout"
        assert session.cwd == "/repo"
        assert session.kind == KIND_MAIN

    def test_turns_are_not_doubled(self, tmp_path):
        """The critical one: event_msg and response_item carry identical content."""
        p = write_jsonl(tmp_path / "rollout.jsonl", CODEX_LINES)
        _, turns, _ = read_codex_rollout(p)
        texts = [t.text for t in turns if t.kind == "text"]
        assert texts == ["the answer"]          # once, not twice

    def test_reasoning_is_the_plaintext_summary(self, tmp_path):
        p = write_jsonl(tmp_path / "rollout.jsonl", CODEX_LINES)
        _, turns, _ = read_codex_rollout(p)
        thinking = [t for t in turns if t.kind == "thinking"]
        assert len(thinking) == 1
        assert thinking[0].text == "**Running shell command**"

    def test_encrypted_content_is_never_stored(self, tmp_path):
        p = write_jsonl(tmp_path / "rollout.jsonl", CODEX_LINES)
        _, turns, _ = read_codex_rollout(p)
        for t in turns:
            assert "gAAAAAB" not in t.text

    def test_tool_calls_and_outputs(self, tmp_path):
        p = write_jsonl(tmp_path / "rollout.jsonl", CODEX_LINES)
        _, turns, _ = read_codex_rollout(p)
        calls = [t for t in turns if t.kind == "tool_use"]
        outputs = [t for t in turns if t.kind == "tool_result"]
        assert calls[0].tool_name == "shell"
        assert outputs[0].text == "a.txt"

    def test_turn_context_cwd_updates_the_session(self, tmp_path):
        """Codex cwd can change mid-session; the latest wins."""
        lines = CODEX_LINES + [{
            "timestamp": "2026-07-25T12:00:00.000Z", "type": "turn_context",
            "payload": {"cwd": "/elsewhere", "model": "gpt-x"},
        }]
        p = write_jsonl(tmp_path / "rollout.jsonl", lines)
        session, _, _ = read_codex_rollout(p)
        assert session.cwd == "/elsewhere"

    def test_missing_session_meta_yields_no_session(self, tmp_path):
        p = write_jsonl(tmp_path / "rollout.jsonl", [CODEX_LINES[2]])
        session, _, _ = read_codex_rollout(p)
        assert session is None

    def test_malformed_lines_are_skipped(self, tmp_path):
        p = tmp_path / "rollout.jsonl"
        p.write_text(json.dumps(CODEX_LINES[0]) + "\nnot json\n" + json.dumps(CODEX_LINES[2]) + "\n")
        session, turns, _ = read_codex_rollout(p)
        assert session.id == "C1"
        assert len(turns) == 1


from scad.records import (  # noqa: E402
    OUTCOME_AWAITING_QUESTION,
    OUTCOME_AWAITING_USER,
    OUTCOME_IN_FLIGHT,
    OUTCOME_INTERRUPTED,
    OUTCOME_TOOL_RESULT_LAST,
    OUTCOME_USER_LAST,
)


def codex_item(payload: dict, ts="2026-07-25T11:32:00.000Z") -> dict:
    return {"timestamp": ts, "type": "response_item", "payload": payload}


class TestCodexOutcome:
    """The same outcome vocabulary, derived from codex's tail shapes.

    Measured over the 107 archived rollouts: 104 end on an assistant message,
    2 on a `function_call_output`, 1 on a user message, and none has an
    unmatched `call_id` at EOF.
    """

    def test_a_trailing_output_is_tool_result_last(self, tmp_path):
        """CODEX_LINES ends on function_call_output c1 — a result awaiting the model."""
        p = write_jsonl(tmp_path / "rollout.jsonl", CODEX_LINES)
        session, _, _ = read_codex_rollout(p)
        assert session.outcome == OUTCOME_TOOL_RESULT_LAST

    def test_assistant_message_last_is_awaiting_user(self, tmp_path):
        lines = CODEX_LINES + [codex_item({
            "type": "message", "role": "assistant",
            "content": [{"type": "output_text", "text": "all done"}]})]
        session, _, _ = read_codex_rollout(write_jsonl(tmp_path / "r.jsonl", lines))
        assert session.outcome == OUTCOME_AWAITING_USER

    def test_user_message_last_is_user_last(self, tmp_path):
        lines = CODEX_LINES + [codex_item({
            "type": "message", "role": "user",
            "content": [{"type": "input_text", "text": "one more thing"}]})]
        session, _, _ = read_codex_rollout(write_jsonl(tmp_path / "r.jsonl", lines))
        assert session.outcome == OUTCOME_USER_LAST

    def test_a_call_with_no_output_is_in_flight(self, tmp_path):
        lines = CODEX_LINES + [codex_item({
            "type": "function_call", "name": "shell",
            "arguments": "{\"cmd\":\"sleep 99\"}", "call_id": "c2"})]
        session, _, _ = read_codex_rollout(write_jsonl(tmp_path / "r.jsonl", lines))
        assert session.outcome == OUTCOME_IN_FLIGHT

    def test_calls_pair_to_outputs_by_call_id_not_position(self, tmp_path):
        """An output for a DIFFERENT call does not answer the open one."""
        lines = CODEX_LINES + [
            codex_item({"type": "function_call", "name": "shell",
                        "arguments": "{}", "call_id": "c2"}),
            codex_item({"type": "function_call_output", "call_id": "c9", "output": "stale"}),
        ]
        session, _, _ = read_codex_rollout(write_jsonl(tmp_path / "r.jsonl", lines))
        assert session.outcome == OUTCOME_IN_FLIGHT

    def test_custom_tool_calls_count_the_same(self, tmp_path):
        lines = CODEX_LINES + [codex_item({
            "type": "custom_tool_call", "name": "apply_patch",
            "input": "*** Begin Patch", "call_id": "c3"})]
        assert read_codex_rollout(
            write_jsonl(tmp_path / "a.jsonl", lines))[0].outcome == OUTCOME_IN_FLIGHT

        lines = lines + [codex_item({
            "type": "custom_tool_call_output", "call_id": "c3", "output": "ok"})]
        assert read_codex_rollout(
            write_jsonl(tmp_path / "b.jsonl", lines))[0].outcome == OUTCOME_TOOL_RESULT_LAST

    def test_reasoning_does_not_disturb_the_tail(self, tmp_path):
        """Reasoning is not a turn anyone is waiting on."""
        lines = CODEX_LINES[:4] + [codex_item({
            "type": "reasoning", "content": None,
            "summary": [{"type": "summary_text", "text": "thinking"}]})]
        session, _, _ = read_codex_rollout(write_jsonl(tmp_path / "r.jsonl", lines))
        assert session.outcome == OUTCOME_AWAITING_USER

    def test_a_rollout_with_no_conversation_has_no_outcome(self, tmp_path):
        """An honest NULL: session_meta only, so there is no tail to read."""
        p = write_jsonl(tmp_path / "r.jsonl", CODEX_LINES[:2])
        session, _, _ = read_codex_rollout(p)
        assert session.outcome is None

    def test_last_stop_reason_has_no_codex_analogue(self, tmp_path):
        p = write_jsonl(tmp_path / "r.jsonl", CODEX_LINES)
        session, _, _ = read_codex_rollout(p)
        assert session.last_stop_reason is None


def assistant(content, stop_reason="end_turn", ts="2026-07-28T10:00:00.000Z", **extra):
    rec = {"type": "assistant", "sessionId": "S1", "timestamp": ts,
           "message": {"role": "assistant", "content": content, "stop_reason": stop_reason}}
    rec.update(extra)
    return rec


class TestSessionOutcome:
    def test_ask_user_question_is_awaiting_question(self, tmp_path):
        """The one case where 'it wants input' is structural, not a guess."""
        p = write_jsonl(tmp_path / "S1.jsonl", [assistant(
            [{"type": "tool_use", "name": "AskUserQuestion", "input": {"q": "which?"}}],
            stop_reason="tool_use",
        )])
        session, _, _ = read_claude_transcript(p)
        assert session.outcome == OUTCOME_AWAITING_QUESTION

    def test_model_spoke_last_is_awaiting_user(self, tmp_path):
        p = write_jsonl(tmp_path / "S1.jsonl", [assistant([{"type": "text", "text": "done"}])])
        session, _, _ = read_claude_transcript(p)
        assert session.outcome == OUTCOME_AWAITING_USER
        assert session.last_stop_reason == "end_turn"

    def test_tool_use_without_a_result_is_in_flight(self, tmp_path):
        p = write_jsonl(tmp_path / "S1.jsonl", [assistant(
            [{"type": "tool_use", "name": "Bash", "input": {"command": "ls"}}],
            stop_reason="tool_use",
        )])
        session, _, _ = read_claude_transcript(p)
        assert session.outcome == OUTCOME_IN_FLIGHT

    def test_tool_use_with_a_result_is_not_in_flight(self, tmp_path):
        p = write_jsonl(tmp_path / "S1.jsonl", [
            assistant([{"type": "tool_use", "name": "Bash", "input": {}}], stop_reason="tool_use"),
            {"type": "user", "sessionId": "S1", "timestamp": "2026-07-28T10:00:01.000Z",
             "message": {"role": "user", "content": [{"type": "tool_result", "content": "ok"}]}},
            assistant([{"type": "text", "text": "finished"}], ts="2026-07-28T10:00:02.000Z"),
        ])
        session, _, _ = read_claude_transcript(p)
        assert session.outcome == OUTCOME_AWAITING_USER

    def test_user_spoke_last(self, tmp_path):
        p = write_jsonl(tmp_path / "S1.jsonl", [
            assistant([{"type": "text", "text": "done"}]),
            {"type": "user", "sessionId": "S1", "timestamp": "2026-07-28T10:01:00.000Z",
             "message": {"role": "user", "content": "another thing"}},
        ])
        session, _, _ = read_claude_transcript(p)
        assert session.outcome == OUTCOME_USER_LAST

    def test_interrupt_is_counted_and_wins_at_the_tail(self, tmp_path):
        p = write_jsonl(tmp_path / "S1.jsonl", [
            assistant([{"type": "text", "text": "working"}], stop_reason="tool_use",
                      interruptedMessageId="m1"),
        ])
        session, _, _ = read_claude_transcript(p)
        assert session.n_interrupts == 1
        assert session.outcome == OUTCOME_INTERRUPTED

    def test_denials_and_errors_are_counted(self, tmp_path):
        p = write_jsonl(tmp_path / "S1.jsonl", [
            assistant([{"type": "text", "text": "a"}], toolDenialKind="user_reject"),
            assistant([{"type": "text", "text": "b"}], isApiErrorMessage=True),
        ])
        session, _, _ = read_claude_transcript(p)
        assert session.n_tool_denials == 1
        assert session.n_errors == 1

    def test_a_result_with_no_reply_is_tool_result_last(self, tmp_path):
        """The ordinary terminal state of a subagent transcript — 943 of 1070
        NULL-outcome rows on the real index, and 200/200 of a hand sample.

        It fell through every branch by construction: `last_role == 'user'` is
        excluded from user-last by last_was_tool_result, and that same
        tool_result has already cleared pending_tool_use, so in-flight cannot
        fire either.
        """
        p = write_jsonl(tmp_path / "S1.jsonl", [
            assistant([{"type": "tool_use", "name": "Bash", "input": {}}], stop_reason="tool_use"),
            {"type": "user", "sessionId": "S1", "timestamp": "2026-07-28T10:00:01.000Z",
             "message": {"role": "user", "content": [{"type": "tool_result", "content": "ok"}]}},
        ])
        session, _, _ = read_claude_transcript(p)
        assert session.outcome == OUTCOME_TOOL_RESULT_LAST

    def test_it_is_not_folded_into_in_flight(self, tmp_path):
        """Opposite sides of the same stall: in-flight is a call awaiting its
        RESULT; this is a result awaiting the MODEL. Collapsing them would make
        the column lie about which side the work is stuck on."""
        assert OUTCOME_TOOL_RESULT_LAST != OUTCOME_IN_FLIGHT

        call_only = write_jsonl(tmp_path / "A.jsonl", [assistant(
            [{"type": "tool_use", "name": "Bash", "input": {}}], stop_reason="tool_use")])
        result_last = write_jsonl(tmp_path / "B.jsonl", [
            assistant([{"type": "tool_use", "name": "Bash", "input": {}}], stop_reason="tool_use"),
            {"type": "user", "sessionId": "S1", "timestamp": "2026-07-28T10:00:01.000Z",
             "message": {"role": "user", "content": [{"type": "tool_result", "content": "ok"}]}},
        ])
        assert read_claude_transcript(call_only)[0].outcome == OUTCOME_IN_FLIGHT
        assert read_claude_transcript(result_last)[0].outcome == OUTCOME_TOOL_RESULT_LAST

    def test_an_answered_result_still_wins_over_tool_result_last(self, tmp_path):
        """Only the TAIL matters: a result the model then answered is not a stall."""
        p = write_jsonl(tmp_path / "S1.jsonl", [
            assistant([{"type": "tool_use", "name": "Bash", "input": {}}], stop_reason="tool_use"),
            {"type": "user", "sessionId": "S1", "timestamp": "2026-07-28T10:00:01.000Z",
             "message": {"role": "user", "content": [{"type": "tool_result", "content": "ok"}]}},
            assistant([{"type": "text", "text": "done"}], ts="2026-07-28T10:00:02.000Z"),
        ])
        assert read_claude_transcript(p)[0].outcome == OUTCOME_AWAITING_USER

    def test_an_interrupt_still_wins_over_a_trailing_result(self, tmp_path):
        p = write_jsonl(tmp_path / "S1.jsonl", [
            assistant([{"type": "tool_use", "name": "Bash", "input": {}}], stop_reason="tool_use"),
            {"type": "user", "sessionId": "S1", "timestamp": "2026-07-28T10:00:01.000Z",
             "interruptedMessageId": "m1",
             "message": {"role": "user", "content": [{"type": "tool_result", "content": "ok"}]}},
        ])
        assert read_claude_transcript(p)[0].outcome == OUTCOME_INTERRUPTED

    def test_no_heuristic_on_question_marks(self, tmp_path):
        """Prose ending in '?' must NOT be labelled awaiting-question — that is a
        semantic judgement this layer refuses to make."""
        p = write_jsonl(tmp_path / "S1.jsonl", [
            assistant([{"type": "text", "text": "Should I use the resolver here?"}]),
        ])
        session, _, _ = read_claude_transcript(p)
        assert session.outcome == OUTCOME_AWAITING_USER


from scad.readers import read_job_state  # noqa: E402


SNAP = {"name": "nd-5", "sessionId": "S1", "state": "blocked",
        "needs": "drop the bioRxiv PDF to ~/Downloads/",
        "detail": "workflow salvaged (28/29 results)",
        "updatedAt": "2026-07-28T17:56:43.450Z"}


class TestJobState:
    """`state-history.jsonl` — the only place a session's human name exists."""

    def test_reads_the_fields_that_matter(self, tmp_path):
        p = write_jsonl(tmp_path / "state-history.jsonl", [SNAP])
        states, end = read_job_state(p)
        assert len(states) == 1
        s = states[0]
        assert s.session_id == "S1"
        assert s.name == "nd-5"
        assert s.state == "blocked"
        assert s.needs.startswith("drop the bioRxiv")
        assert s.detail.startswith("workflow salvaged")
        assert s.updated_at == 1785261403450
        assert end == p.stat().st_size

    def test_carries_cwd_and_created_at(self, tmp_path):
        """When no transcript survives, this snapshot is the whole session row —
        so where it ran and when it started have to come from here."""
        p = write_jsonl(tmp_path / "state-history.jsonl", [
            {**SNAP, "cwd": "/repo/nd", "createdAt": "2026-07-28T17:00:00.000Z"},
        ])
        states, _ = read_job_state(p)
        assert states[0].cwd == "/repo/nd"
        assert states[0].created_at == 1785258000000

    def test_latest_snapshot_wins(self, tmp_path):
        p = write_jsonl(tmp_path / "state-history.jsonl", [
            SNAP,
            {**SNAP, "state": "done", "needs": None,
             "updatedAt": "2026-07-28T18:30:00.000Z"},
        ])
        states, _ = read_job_state(p)
        assert len(states) == 1
        assert states[0].state == "done"
        assert states[0].needs is None

    def test_one_row_per_session_id(self, tmp_path):
        """A job dir can be reused across resumes; each session keeps its own name."""
        p = write_jsonl(tmp_path / "state-history.jsonl", [
            SNAP, {**SNAP, "sessionId": "S2", "name": "nd-6"},
        ])
        states, _ = read_job_state(p)
        assert {s.session_id: s.name for s in states} == {"S1": "nd-5", "S2": "nd-6"}

    def test_lines_without_a_session_id_are_ignored(self, tmp_path):
        p = write_jsonl(tmp_path / "state-history.jsonl",
                        [{"name": "orphan", "state": "running"}, SNAP])
        states, _ = read_job_state(p)
        assert [s.session_id for s in states] == ["S1"]

    def test_malformed_lines_are_survived(self, tmp_path):
        p = tmp_path / "state-history.jsonl"
        p.write_text("{not json\n" + json.dumps(SNAP) + "\n")
        states, _ = read_job_state(p)
        assert [s.name for s in states] == ["nd-5"]

    def test_missing_optional_fields_are_none(self, tmp_path):
        p = write_jsonl(tmp_path / "state-history.jsonl", [{"sessionId": "S1"}])
        states, _ = read_job_state(p)
        assert (states[0].name, states[0].state, states[0].needs) == (None, None, None)


# --- notes: the authored tier -------------------------------------------------

from scad.readers import read_notes  # noqa: E402

NOTE = {
    "ts": "2026-07-29T11:00:00+05:30",
    "span": "since-last",
    "topic": "notes-store",
    "relation": "continue",
    "parent": None,
    "title": "Built the notes store",
    "text": "**Frame**\nsomething\n",
    "tags": ["notes", "jsonl", "append-only"],
    "entities": ["session-index.md"],
    "sessions": ["claude:S1"],
    "invalidation": "if the store moves",
    "cwd_at_write": "/Users/vsr/code/scad",
}


class TestReadNotes:
    def test_one_record_per_line_in_order(self, tmp_path):
        p = write_jsonl(tmp_path / "S1.jsonl", [NOTE, {**NOTE, "title": "second"}])
        notes, end = read_notes(p)
        assert [n.title for n in notes] == ["Built the notes store", "second"]
        assert end == p.stat().st_size

    def test_ts_becomes_epoch_ms(self, tmp_path):
        p = write_jsonl(tmp_path / "S1.jsonl", [NOTE])
        notes, _ = read_notes(p)
        assert notes[0].ts == 1785303000000   # 2026-07-29T05:30:00Z

    def test_indexed_fields_are_carried(self, tmp_path):
        p = write_jsonl(tmp_path / "S1.jsonl", [NOTE])
        n = read_notes(p)[0][0]
        assert (n.topic, n.relation, n.parent) == ("notes-store", "continue", None)
        assert n.tags == ["notes", "jsonl", "append-only"]
        assert n.entities == ["session-index.md"]

    def test_cwd_at_write_is_carried_so_the_project_survives_the_trace(self, tmp_path):
        p = write_jsonl(tmp_path / "S1.jsonl", [NOTE])
        assert read_notes(p)[0][0].cwd_at_write == "/Users/vsr/code/scad"

    def test_resume_reads_only_what_was_appended(self, tmp_path):
        p = write_jsonl(tmp_path / "S1.jsonl", [NOTE])
        first_end = p.stat().st_size
        with p.open("a") as fh:
            fh.write(json.dumps({**NOTE, "title": "later"}) + "\n")
        notes, end = read_notes(p, first_end)
        assert [n.title for n in notes] == ["later"]
        assert end == p.stat().st_size

    def test_a_malformed_line_is_skipped_not_fatal(self, tmp_path):
        p = tmp_path / "S1.jsonl"
        p.write_text("{not json\n" + json.dumps(NOTE) + "\n")
        notes, _ = read_notes(p)
        assert [n.title for n in notes] == ["Built the notes store"]

    def test_a_sparse_record_survives(self, tmp_path):
        # The record is written by a model; missing optional fields are normal
        # and must never cost the note.
        p = write_jsonl(tmp_path / "S1.jsonl", [{"title": "bare"}])
        n = read_notes(p)[0][0]
        assert (n.title, n.ts, n.topic, n.tags) == ("bare", None, None, [])

    def test_non_list_tags_are_coerced_rather_than_dropped(self, tmp_path):
        p = write_jsonl(tmp_path / "S1.jsonl", [{**NOTE, "tags": "notes"}])
        assert read_notes(p)[0][0].tags == ["notes"]
