"""Tests for session snapshots: which open sessions are recorded, and how."""

import json

import pytest

from scad.index import connect, upsert_session
from scad.live import ClaudeSession, TmuxPane
from scad.records import SessionRecord


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("SCAD_HOME", str(tmp_path / ".scad"))
    # The process tree is injected per test; nothing reads the real one.
    monkeypatch.setattr("scad.live._process_parents", lambda: {})


def _index(tmp_path, *rows):
    conn = connect(tmp_path / "i.sqlite")
    for sid, project, name, agent, tokens, window in rows:
        upsert_session(conn, SessionRecord(id=sid, kind="main", agent=agent,
                                           source="claude-transcript", cwd=f"/w/{project}",
                                           name=name, ended=1_000, context_tokens=tokens,
                                           context_window=window),
                       machine="m", project=project, archive_path="/a", source_size=1,
                       source_mtime=1, parsed_offset=1)
    return conn


def _gather(conn, sessions=(), panes=(), records=(), parents=None, monkeypatch=None):
    from scad.snapshot import gather
    if parents is not None:
        monkeypatch.setattr("scad.live._process_parents", lambda: parents)
    return gather(conn, list(sessions), list(panes), list(records))


class TestWhatIsRecorded:
    def test_a_hand_started_claude_session_is_found_through_the_registry(self, tmp_path,
                                                                          monkeypatch):
        conn = _index(tmp_path, ("S1", "scad", "backlog", "claude", 524_490, 1_000_000))
        data = _gather(conn, [ClaudeSession("S1", 200, cwd="/w/scad", name="backlog")],
                       [TmuxPane("main:3.0", "/w/scad", "2.1.270", window="scad", pid=100)],
                       parents={200: 100}, monkeypatch=monkeypatch)
        (s,) = data["sessions"]
        assert (s["id"], s["agent"], s["found_by"]) == ("S1", "claude", "registry")
        assert (s["tmux_session"], s["window"], s["pane"]) == ("main", "scad", "main:3.0")
        assert (s["project"], s["context_tokens"], s["context_window"]) == \
            ("scad", 524_490, 1_000_000)

    def test_a_scad_launched_codex_session_is_found_through_its_launch_record(self, tmp_path):
        conn = _index(tmp_path, ("C1", "orglens", None, "codex", 215_227, 258_400))
        data = _gather(conn, [], [TmuxPane("main:4.1", "/w/orglens", "codex",
                                           window="orglens", pid=300)],
                       [{"session_id": "C1", "agent": "codex", "tmux": "main:4.1",
                         "cwd": "/w/orglens"}])
        (s,) = data["sessions"]
        assert (s["id"], s["agent"], s["found_by"], s["window"]) == \
            ("C1", "codex", "launch-record", "orglens")

    def test_an_agent_pane_nothing_names_is_not_restorable(self, tmp_path):
        conn = _index(tmp_path)
        data = _gather(conn, [], [TmuxPane("main:5.1", "/w/x", "codex", window="workflow",
                                           pid=400)])
        assert data["sessions"] == []
        assert data["not_restorable"] == [
            {"pane": "main:5.1", "window": "workflow", "command": "codex", "cwd": "/w/x"}]

    def test_a_claude_session_outside_tmux_is_kept_without_a_window(self, tmp_path):
        conn = _index(tmp_path, ("S1", "scad", None, "claude", None, None))
        data = _gather(conn, [ClaudeSession("S1", 200, cwd="/w/scad")], [])
        (s,) = data["sessions"]
        assert (s["pane"], s["window"], s["tmux_session"]) == (None, None, None)


class TestTheFile:
    def test_sessions_are_sorted_by_project_then_name(self, tmp_path, monkeypatch):
        conn = _index(tmp_path, ("A", "zeta", "b", "claude", None, None),
                      ("B", "alpha", "z", "claude", None, None),
                      ("C", "alpha", "a", "claude", None, None))
        data = _gather(conn, [ClaudeSession(s, p) for s, p in (("A", 1), ("B", 2), ("C", 3))], [])
        assert [s["id"] for s in data["sessions"]] == ["C", "B", "A"]

    def test_fields_are_in_reading_order(self, tmp_path):
        conn = _index(tmp_path, ("S1", "scad", "n", "claude", None, None))
        (s,) = _gather(conn, [ClaudeSession("S1", 200)], [])["sessions"]
        assert list(s)[:4] == ["name", "project", "agent", "id"]

    def test_take_writes_an_indented_file_and_says_what_it_holds(self, tmp_path, monkeypatch):
        from scad.snapshot import snapshots_root, take
        conn = _index(tmp_path, ("S1", "scad", "n", "claude", None, None))
        path, summary = take(conn, sessions=[ClaudeSession("S1", 200)], panes=[
            TmuxPane("main:5.1", "/w/x", "kimi", window="w", pid=9)], records=[])
        assert path.parent == snapshots_root() and path.name.startswith("open-")
        text = path.read_text()
        assert text.startswith('{\n  "format": 1,')
        assert json.loads(text)["sessions"][0]["id"] == "S1"
        assert summary == "1 session: 1 claude; 1 pane not restorable"

    def test_the_newest_fifty_are_kept(self, tmp_path):
        from scad.snapshot import KEEP, prune, snapshot_paths, snapshots_root
        root = snapshots_root()
        root.mkdir(parents=True)
        for i in range(KEEP + 3):
            (root / f"open-20261001-{i:06d}.json").write_text("{}")
        prune()
        kept = snapshot_paths()
        assert len(kept) == KEEP == 50
        assert kept[0].name == f"open-20261001-{KEEP + 2:06d}.json"   # newest first
        assert not (root / "open-20261001-000000.json").exists()

    def test_latest_is_the_newest(self, tmp_path):
        from scad.snapshot import latest, snapshots_root
        root = snapshots_root()
        assert latest() is None
        root.mkdir(parents=True)
        (root / "open-20261005-174614.json").write_text("{}")
        (root / "open-20261007-090000.json").write_text("{}")
        assert latest().name == "open-20261007-090000.json"


class TestThePaneComesFromTheRegistryFirst:
    def test_a_pane_tmux_reports_as_a_shell_is_still_found_by_its_id(self, tmp_path):
        """Measured 2026-10-07: three of seven open Claude sessions sat under a
        pane whose foreground command tmux reported as `zsh`, so the
        agent-pane filter skipped them and they had no pane. The registry's own
        pane id names it exactly."""
        conn = _index(tmp_path, ("S1", "scad", "n", "claude", None, None))
        data = _gather(conn, [ClaudeSession("S1", 40333, tmux_pane="%19")],
                       [TmuxPane("main:3.3", "/w/scad", "zsh", window="scad", pid=40324,
                                 pane_id="%19")])
        (s,) = data["sessions"]
        assert (s["pane"], s["window"]) == ("main:3.3", "scad")
