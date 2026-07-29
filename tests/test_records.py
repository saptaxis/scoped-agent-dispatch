"""Tests for the normalized record types."""

import pytest

from scad.records import (
    GRADE_FULL,
    GRADE_SKELETON,
    KIND_MAIN,
    KIND_SUBAGENT,
    KIND_WORKFLOW,
    TOOL_RESULT_CAP,
    JobStateRecord,
    SessionRecord,
    TurnRecord,
)


class TestSessionRecord:
    def test_main_session_needs_only_identity_and_source(self):
        r = SessionRecord(id="abc", kind=KIND_MAIN, agent="claude", source="claude-transcript")
        assert r.parent_session_id is None
        assert r.agent_id is None
        assert r.workflow_id is None
        assert r.grade == GRADE_FULL

    def test_subagent_carries_parentage(self):
        r = SessionRecord(
            id="a0778b", kind=KIND_SUBAGENT, agent="claude", source="claude-subagent",
            parent_session_id="02bc2d28", agent_id="a0778b",
        )
        assert r.parent_session_id == "02bc2d28"
        assert r.id == r.agent_id

    def test_is_frozen(self):
        r = SessionRecord(id="abc", kind=KIND_MAIN, agent="claude", source="claude-transcript")
        with pytest.raises(Exception):
            r.id = "other"

    def test_kind_constants_are_the_public_vocabulary(self):
        assert (KIND_MAIN, KIND_SUBAGENT, KIND_WORKFLOW) == ("main", "subagent", "workflow-agent")
        assert (GRADE_FULL, GRADE_SKELETON) == ("full", "skeleton")


class TestJobStateRecord:
    def test_carries_cwd_and_created_at(self):
        """A job state that names no surviving session becomes the session row
        itself, so it has to carry enough to build one: where it ran and when."""
        j = JobStateRecord(session_id="S1", name="nd-3", state="failed",
                           cwd="/repo", created_at=1, updated_at=2)
        assert (j.cwd, j.created_at, j.updated_at) == ("/repo", 1, 2)

    def test_those_fields_are_optional(self):
        j = JobStateRecord(session_id="S1")
        assert j.cwd is None
        assert j.created_at is None


class TestTurnRecord:
    def test_defaults(self):
        t = TurnRecord(ts=1, role="assistant", kind="text", text="hi")
        assert t.tool_name is None
        assert t.truncated is False
        assert t.raw_offset == 0

    def test_cap_is_64_kib(self):
        """p99 of real tool results is 45 KB; 64 KB stores 99% whole."""
        assert TOOL_RESULT_CAP == 65536
