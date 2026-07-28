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
