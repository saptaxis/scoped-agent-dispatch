"""Tests for the session viewer."""

import pytest

from scad.live import ClaudeSession, TmuxPane
from scad.view import Reentry, reentry_for


@pytest.fixture(autouse=True)
def _no_real_claude_registry(monkeypatch):
    """No test may read the real `~/.claude/sessions`.

    `gather()` reaches for the live registry by default — on a developer's
    machine that is a directory full of their actual sessions, and a test whose
    result depends on what they happen to have open is not a test. Anything
    that wants live sessions passes them in.
    """
    monkeypatch.setattr("scad.view.claude_live_sessions", lambda *a, **k: [])


def row(**kw) -> dict:
    base = {"id": "S1", "agent": "claude", "cwd": "/Users/vsr/code/scad",
            "scad_run_id": None, "kind": "main"}
    base.update(kw)
    return base


class TestReentry:
    def test_live_tmux_pane_wins(self):
        panes = [TmuxPane("main:1.2", "/Users/vsr/code/scad", "2.1.219")]
        r = reentry_for(row(), panes, set())
        assert r.kind == "tmux"
        assert r.target == "main:1.2"
        assert r.goto == "tmux select-window -t main:1 \\; select-pane -t main:1.2"

    def test_the_command_selects_the_pane_not_just_the_window(self):
        """`select-window -t main:3.0` silently ignores the pane component.

        Verified on the real machine: with window 3's active pane set to 3.1
        (a zsh), `tmux select-window -t main:3.0` landed on main:3.1 running
        zsh, not on the agent in 3.0. The window must be selected AND the pane
        selected explicitly, or the command lands on whichever pane was last
        active in that window.
        """
        panes = [TmuxPane("main:3.0", "/Users/vsr/code/scad", "2.1.205")]
        r = reentry_for(row(), panes, set())
        assert "select-pane -t main:3.0" in r.goto
        # The escaped semicolon survives a shell paste as a tmux command separator.
        assert " \\; " in r.goto

    def test_a_pane_in_the_same_cwd_that_is_not_an_agent_is_ignored(self):
        """Three panes share a directory on the real machine; only agent panes count."""
        panes = [TmuxPane("main:2.0", "/Users/vsr/code/scad", "zsh")]
        r = reentry_for(row(), panes, set())
        assert r.kind == "resume"

    def test_several_agent_panes_list_all_candidates(self):
        panes = [
            TmuxPane("main:1.2", "/Users/vsr/code/scad", "2.1.219"),
            TmuxPane("main:3.0", "/Users/vsr/code/scad", "2.1.205"),
        ]
        r = reentry_for(row(), panes, set())
        assert r.kind == "tmux"
        assert "main:1.2" in r.goto
        assert "main:3.0" in r.note
        assert "ambiguous" in r.note.lower()

    def test_running_container_beats_resume(self):
        r = reentry_for(row(scad_run_id="demo-Jul28-1200"), [], {"demo-Jul28-1200"})
        assert r.kind == "container"
        assert r.target == "demo-Jul28-1200"
        assert r.goto == "scad run attach demo-Jul28-1200"

    def test_dead_container_falls_back_to_resume(self):
        r = reentry_for(row(scad_run_id="old-Mar01-1200"), [], set())
        assert r.kind == "resume"

    def test_closed_claude_session_resumes_by_uuid(self):
        r = reentry_for(row(), [], set())
        assert r.kind == "resume"
        assert r.command == "cd /Users/vsr/code/scad && claude --resume S1"

    def test_closed_codex_session_uses_codex_resume(self):
        r = reentry_for(row(agent="codex", id="C9"), [], set())
        assert r.command == "cd /Users/vsr/code/scad && codex resume C9"

    def test_a_cwd_that_no_longer_exists_still_produces_a_command(self):
        """Recorded cwds outlive their directories; the command is still the best hint."""
        r = reentry_for(row(cwd="/gone/away"), [], set())
        assert "cd /gone/away" in r.command

    def test_a_cwd_with_spaces_is_quoted_for_the_shell(self):
        """84 real cwds contain spaces — "Saptarishi Apartments", "scad enc.test_dir-1".

        Unquoted, `cd` takes the first word and the command silently fails or
        lands somewhere else entirely.
        """
        r = reentry_for(row(cwd="/tmp/scad enc.test_dir-1"), [], set())
        assert r.command == "cd '/tmp/scad enc.test_dir-1' && claude --resume S1"

    def test_an_ordinary_cwd_is_left_unquoted(self):
        """Quoting every path would make the common case ugly for no gain."""
        r = reentry_for(row(), [], set())
        assert r.command == "cd /Users/vsr/code/scad && claude --resume S1"

    def test_missing_cwd_resumes_without_a_cd(self):
        r = reentry_for(row(cwd=None), [], set())
        assert r.command == "claude --resume S1"

    def test_subagent_has_no_reentry(self):
        """You cannot resume a subagent — you resume its parent."""
        r = reentry_for(row(kind="subagent"), [], set())
        assert r.kind == "none"
        assert r.command == ""


class TestTheCommandIsAlwaysTheResumeCommand:
    """What is displayed and copied never depends on what is open right now.

    `reentry_for` used to answer two questions with one field: a live pane
    returned a tmux target *instead of* a resume command, so the one string
    that always works — and still works tomorrow, after the pane is gone —
    never appeared for exactly the rows most likely to be clicked. The live
    place is still known; it moved to `target`/`goto`, where the resume path
    consults it and the row shows it as metadata.
    """

    def test_a_live_pane_does_not_displace_the_resume_command(self):
        panes = [TmuxPane("main:1.2", "/Users/vsr/code/scad", "2.1.219")]
        r = reentry_for(row(), panes, set())
        assert r.command == "cd /Users/vsr/code/scad && claude --resume S1"

    def test_a_container_session_offers_its_run_instead(self):
        """Superseded intent. This asserted that a running container must not
        displace the resume command -- correct for the live-first bug it was
        written against, and wrong about container sessions specifically: a
        session with a `scad_run_id` has its transcript under
        `~/.scad/runs/<id>/claude/projects/`, not `~/.claude/projects/`, so a
        host resume cannot find it whatever the cwd. The run is the way in.

        The original point still holds for host sessions -- see the pane test
        above, where a live pane does not displace the command."""
        r = reentry_for(row(scad_run_id="demo-Jul28-1200"), [], {"demo-Jul28-1200"})
        assert "claude --resume" not in r.command
        assert r.command == "scad run attach demo-Jul28-1200"

    def test_kind_still_says_which_section_the_row_belongs_to(self):
        """The page selects its live section on `kind`, so it must survive."""
        panes = [TmuxPane("main:1.2", "/Users/vsr/code/scad", "2.1.219")]
        assert reentry_for(row(), panes, set()).kind == "tmux"

    def test_a_closed_session_has_no_place_to_go(self):
        r = reentry_for(row(), [], set())
        assert (r.target, r.goto) == ("", "")


class TestKimiResume:
    """kimi is the third family and had no entry at all, so kimi rows offered
    nothing in either surface.

    The id needs putting back together. `kimi_identity_from_path` strips the
    `session_` prefix off the directory name to key the row, and kimi's own CLI
    rejects the bare uuid: `kimi -S <uuid>` answers `Session "<uuid>" not
    found.` while the prefixed form resolves. Measured against kimi-code on
    2026-07-30.
    """

    def test_the_prefix_the_index_stripped_is_put_back(self):
        r = reentry_for(row(agent="kimi", id="a01d5fe5-2327-428d-8c37-fdde5a99f714"), [], set())
        assert r.command == ("cd /Users/vsr/code/scad && "
                             "kimi --session session_a01d5fe5-2327-428d-8c37-fdde5a99f714")

    def test_an_id_that_already_carries_the_prefix_is_not_doubled(self):
        r = reentry_for(row(agent="kimi", id="session_a01d5fe5"), [], set())
        assert r.command.endswith("kimi --session session_a01d5fe5")

    def test_an_unknown_agent_still_falls_back_to_claude(self):
        assert "claude --resume S1" in reentry_for(row(agent="pi"), [], set()).command


import time
from datetime import datetime

from scad.index import append_turns, connect, upsert_session
from scad.records import KIND_MAIN, SessionRecord, TurnRecord
from scad.view import gather


def _store(conn, sid, outcome, ended_days_ago=0, cwd="/repo", agent="claude", kind=KIND_MAIN,
           project="proj"):
    ended = int((time.time() - ended_days_ago * 86400) * 1000)
    rec = SessionRecord(id=sid, kind=kind, agent=agent, source="claude-transcript",
                        cwd=cwd, started=ended - 1000, ended=ended, outcome=outcome)
    upsert_session(conn, rec, machine="mac", project=project, archive_path=f"/arc/{sid}.jsonl",
                   source_size=1, source_mtime=1, parsed_offset=1)
    return rec


class TestGather:
    def test_waiting_holds_only_sessions_that_asked_something(self, tmp_path):
        """`awaiting-user` is how nearly every finished session ends: the agent
        spoke last. Counted as waiting, it was 183 of 232 sessions in August
        against one real question, which made the list useless."""
        conn = connect(tmp_path / "i.sqlite")
        _store(conn, "W1", "awaiting-user")
        _store(conn, "Q1", "awaiting-question")
        _store(conn, "D1", "tool-result-last")
        data = gather(conn, [], set())
        assert [r["id"] for r in data["waiting"]] == ["Q1"]

    def test_newest_first(self, tmp_path):
        conn = connect(tmp_path / "i.sqlite")
        _store(conn, "NEW", "awaiting-question", ended_days_ago=1)
        _store(conn, "OLD", "awaiting-question", ended_days_ago=6)
        assert [r["id"] for r in gather(conn, [], set())["waiting"]] == ["NEW", "OLD"]

    def test_a_question_is_never_too_old_to_be_waiting(self, tmp_path):
        """An unanswered question is still unanswered after 14 days. The window
        used to drop it, which is how the only real question on the author's
        machine disappeared from the page."""
        conn = connect(tmp_path / "i.sqlite")
        _store(conn, "OLD", "awaiting-question", ended_days_ago=90)
        assert [r["id"] for r in gather(conn, [], set())["waiting"]] == ["OLD"]

    def test_subagents_never_appear_in_waiting(self, tmp_path):
        conn = connect(tmp_path / "i.sqlite")
        _store(conn, "SUB", "awaiting-question", kind="subagent")
        assert gather(conn, [], set())["waiting"] == []

    def test_live_lists_sessions_with_a_matching_pane(self, tmp_path):
        conn = connect(tmp_path / "i.sqlite")
        _store(conn, "L1", "tool-result-last", cwd="/repo")
        panes = [TmuxPane("main:1.0", "/repo", "2.1.219")]
        data = gather(conn, panes, set())
        assert [r["id"] for r in data["live"]] == ["L1"]

    def test_every_row_carries_a_reentry(self, tmp_path):
        conn = connect(tmp_path / "i.sqlite")
        _store(conn, "W1", "awaiting-question", cwd=str(tmp_path))
        r = gather(conn, [], set())["waiting"][0]
        assert r["reentry"]["kind"] == "resume"
        assert "claude --resume W1" in r["reentry"]["command"]

    def test_waiting_rows_carry_the_last_turn_text(self, tmp_path):
        """So you can remember where the conversation left off."""
        conn = connect(tmp_path / "i.sqlite")
        _store(conn, "W1", "awaiting-question")
        append_turns(conn, "W1", [
            TurnRecord(ts=1, role="assistant", kind="text", text="first"),
            TurnRecord(ts=2, role="assistant", kind="text", text="the last thing said"),
        ])
        assert gather(conn, [], set())["waiting"][0]["last_text"] == "the last thing said"

    def test_all_lists_only_human_started_sessions(self, tmp_path):
        """A subagent is triggered by an agent, cannot be resumed, and was never
        started by you — 1309 of 1462 real rows. It is not a peer."""
        conn = connect(tmp_path / "i.sqlite")
        _store(conn, "A", "awaiting-question")
        _store(conn, "B", "tool-result-last", kind="subagent")
        rows = gather(conn, [], set())["all"]
        assert [r["id"] for r in rows] == ["A"]

    def test_a_parent_carries_its_agent_count(self, tmp_path):
        conn = connect(tmp_path / "i.sqlite")
        _store(conn, "P", "awaiting-question")
        for kid in ("K1", "K2"):
            _store(conn, kid, "tool-result-last", kind="subagent")
            conn.execute("UPDATE sessions SET parent_session_id='P' WHERE id=?", (kid,))
        conn.commit()
        assert gather(conn, [], set())["all"][0]["n_agents"] == 2


import json as _json

from scad.view import render


class TestRender:
    def _data(self):
        return {"waiting": [{"id": "S1", "name": None, "project": "proj", "cwd": "/repo",
                             "title": "a title", "outcome": "awaiting-question", "needs": None,
                             "harness_state": None, "agent": "claude", "kind": "main",
                             "n_turns": 3, "started": 1, "ended": 2, "grade": "full",
                             "scad_run_id": None, "last_text": "where we left off",
                             "reentry": {"kind": "resume",
                                         "command": "cd /repo && claude --resume S1", "note": ""}}],
                "live": [], "all": [], "generated": 1785000000000,
                "waiting_at_hand": [], "waiting_closed": [],
                "grouped_closed": []}

    def test_is_one_self_contained_document(self):
        html = render(self._data())
        assert html.startswith("<!doctype html>")
        assert "</html>" in html
        # No external anything — the page must work with no network.
        for bad in ("http://", "https://", "<script src", "<link rel=\"stylesheet\""):
            assert bad not in html

    def test_embeds_the_data_as_parseable_json(self):
        html = render(self._data())
        start = html.index("const DATA = ") + len("const DATA = ")
        end = html.index(";\n", start)
        assert _json.loads(html[start:end])["waiting"][0]["id"] == "S1"

    def test_escapes_html_in_user_text(self):
        data = self._data()
        data["waiting_at_hand"] = data["waiting"]
        data["waiting"][0]["title"] = "<script>alert(1)</script>"
        html = render(data)
        assert "<script>alert(1)</script>" not in html
        assert "&lt;script&gt;" in html

    def test_survives_apostrophes_and_non_ascii(self):
        """Real needs text contains both: "drop Hincapié & O'Leary … PDF"."""
        data = self._data()
        data["waiting_at_hand"] = data["waiting"]
        data["waiting"][0]["needs"] = "drop Hincapié & O'Leary 2026 bioRxiv PDF"
        html = render(data)
        assert "Hincapi" in html
        start = html.index("const DATA = ") + len("const DATA = ")
        end = html.index(";\n", start)
        assert "O'Leary" in _json.loads(html[start:end])["waiting"][0]["needs"]

    def test_shows_the_reentry_command(self):
        data = self._data()
        data["waiting_at_hand"] = data["waiting"]
        assert "claude --resume S1" in render(data)

    def test_the_copy_handler_attribute_is_quoted(self):
        """Unquoted attributes containing parens are tolerated by browsers but wrong."""
        html = render(self._data())
        assert 'onclick="copy(this)"' in html
        assert "onclick=copy(this)" not in html

    def test_empty_data_still_renders(self):
        html = render({"waiting": [], "live": [], "all": [], "generated": 1,
                       "waiting_at_hand": [], "waiting_closed": [],
                       "grouped_closed": []})
        assert "</html>" in html
        assert "Nothing waiting" in html


class TestModuleSeparation:
    """Discovery shells out; rendering queries. Neither does the other's job.

    Promised by the plan's global constraints, which left it unwritten.
    """

    def test_live_does_not_depend_on_the_index(self):
        import scad.live

        src = open(scad.live.__file__).read()
        assert "scad.index" not in src

    def test_view_does_not_shell_out(self):
        import scad.view

        src = open(scad.view.__file__).read()
        assert "subprocess" not in src


class TestLiveIsOnePerPlace:
    def test_one_row_per_pane_not_per_session_sharing_a_cwd(self, tmp_path):
        """Matching is by cwd, so every session that ever ran in a live pane's
        directory matches it — 36 rows for 8 panes on the real machine."""
        conn = connect(tmp_path / "i.sqlite")
        _store(conn, "OLD", "tool-result-last", ended_days_ago=9, cwd="/repo")
        _store(conn, "NEW", "tool-result-last", ended_days_ago=1, cwd="/repo")
        panes = [TmuxPane("main:1.0", "/repo", "2.1.219")]

        live = gather(conn, panes, set())["live"]

        assert [r["id"] for r in live] == ["NEW"]

    def test_distinct_panes_each_keep_a_row(self, tmp_path):
        conn = connect(tmp_path / "i.sqlite")
        _store(conn, "A", "tool-result-last", cwd="/one")
        _store(conn, "B", "tool-result-last", cwd="/two")
        panes = [TmuxPane("main:1.0", "/one", "2.1.219"),
                 TmuxPane("main:2.0", "/two", "2.1.219")]

        assert len(gather(conn, panes, set())["live"]) == 2

    def test_the_older_sessions_are_still_in_all(self, tmp_path):
        conn = connect(tmp_path / "i.sqlite")
        _store(conn, "OLD", "tool-result-last", ended_days_ago=9, cwd="/repo")
        _store(conn, "NEW", "tool-result-last", ended_days_ago=1, cwd="/repo")
        data = gather(conn, [TmuxPane("main:1.0", "/repo", "2.1.219")], set())
        assert {r["id"] for r in data["all"]} == {"OLD", "NEW"}

    def test_the_collapse_keys_on_the_place_not_on_the_command(self, tmp_path):
        """The coupling that made the viewer amendment dangerous.

        Rows used to be deduped by `reentry.command`, which was the tmux target
        for every live row — one string per pane, so the collapse worked by
        accident. A resume command is unique per session, so keying on it would
        have quietly restored the 36-rows-for-8-panes dump with every test
        above still green: they use one session per pane. This one does not.
        """
        conn = connect(tmp_path / "i.sqlite")
        _store(conn, "OLD", "tool-result-last", ended_days_ago=9, cwd="/repo")
        _store(conn, "NEW", "tool-result-last", ended_days_ago=1, cwd="/repo")
        panes = [TmuxPane("main:1.0", "/repo", "2.1.219")]

        live = gather(conn, panes, set())["live"]

        assert len(live) == 1
        assert live[0]["reentry"]["command"] != live[0]["reentry"]["target"]

    def test_two_containers_stay_two_rows(self, tmp_path):
        conn = connect(tmp_path / "i.sqlite")
        for sid, run in (("A", "r1"), ("B", "r2")):
            now = int(time.time() * 1000)
            rec = SessionRecord(id=sid, kind=KIND_MAIN, agent="claude",
                                source="claude-transcript", cwd=f"/workspace/{sid}",
                                started=now - 1000, ended=now, outcome="tool-result-last")
            upsert_session(conn, rec, machine="mac", project="x",
                           archive_path=f"/a/{sid}.jsonl", source_size=1,
                           source_mtime=1, parsed_offset=1, scad_run_id=run)
        assert len(gather(conn, [], {"r1", "r2"})["live"]) == 2


class TestStatus:
    def test_roster_proves_a_session_is_open(self, tmp_path):
        """The only exact signal: the daemon maps sessionId -> a live pid."""
        conn = connect(tmp_path / "i.sqlite")
        _store(conn, "S1", "awaiting-question", cwd="/repo")
        data = gather(conn, [], set(), live_ids={"S1"})
        assert data["waiting"][0]["status"] == "open"

    def test_an_agent_in_the_same_cwd_is_only_maybe(self, tmp_path):
        """claude does not hold its transcript open, so a running agent cannot be
        traced to a session. With one project per window, this is the normal case."""
        conn = connect(tmp_path / "i.sqlite")
        _store(conn, "S1", "awaiting-question", cwd="/repo")
        panes = [TmuxPane("main:1.0", "/repo", "2.1.219")]
        assert gather(conn, panes, set())["waiting"][0]["status"] == "maybe-open"

    def test_no_agent_anywhere_is_closed(self, tmp_path):
        conn = connect(tmp_path / "i.sqlite")
        _store(conn, "S1", "awaiting-question", cwd="/repo")
        assert gather(conn, [], set())["waiting"][0]["status"] == "closed"

    def test_a_shell_in_the_cwd_does_not_make_it_maybe(self, tmp_path):
        conn = connect(tmp_path / "i.sqlite")
        _store(conn, "S1", "awaiting-question", cwd="/repo")
        panes = [TmuxPane("main:1.0", "/repo", "zsh")]
        assert gather(conn, panes, set())["waiting"][0]["status"] == "closed"

    def test_a_running_container_is_open(self, tmp_path):
        conn = connect(tmp_path / "i.sqlite")
        now = int(time.time() * 1000)
        rec = SessionRecord(id="C1", kind=KIND_MAIN, agent="claude", source="claude-transcript",
                            cwd="/workspace/x", started=now - 1000, ended=now,
                            outcome="awaiting-question")
        upsert_session(conn, rec, machine="mac", project="x", archive_path="/a.jsonl",
                       source_size=1, source_mtime=1, parsed_offset=1, scad_run_id="r1")
        assert gather(conn, [], {"r1"})["waiting"][0]["status"] == "open"

    def test_roster_beats_a_merely_shared_cwd(self, tmp_path):
        conn = connect(tmp_path / "i.sqlite")
        _store(conn, "S1", "awaiting-question", cwd="/repo")
        panes = [TmuxPane("main:1.0", "/repo", "2.1.219")]
        assert gather(conn, panes, set(), live_ids={"S1"})["waiting"][0]["status"] == "open"

    def test_the_page_shows_the_resume_command_even_when_a_pane_matches(self, tmp_path):
        """A tmux target is ambiguous; the resume command never is."""
        conn = connect(tmp_path / "i.sqlite")
        _store(conn, "S1", "awaiting-question", cwd=str(tmp_path))
        panes = [TmuxPane("main:1.0", str(tmp_path), "2.1.219")]
        html = render(gather(conn, panes, set()))
        assert "claude --resume S1" in html
        assert "maybe-open" in html


class TestTheOpenPaneIsMetadata:
    """Where a session is open now is worth saying — but as a fact about the
    row, not as the thing you copy."""

    def _html(self, tmp_path):
        conn = connect(tmp_path / "i.sqlite")
        _store(conn, "S1", "awaiting-question", cwd="/repo")
        return render(gather(conn, [TmuxPane("main:1.0", "/repo", "2.1.219")], set()))

    def test_the_row_names_the_pane(self, tmp_path):
        # The pane is a labelled fact now rather than a phrase in a prose meta
        # line. Still metadata, still on the row, still not a command.
        html = self._html(tmp_path)
        assert "main:1.0" in html and "<dt>where</dt>" in html

    def test_walking_there_is_still_one_click_away(self, tmp_path):
        assert "select-pane -t main:1.0" in self._html(tmp_path)


class TestGrouping:
    def test_waiting_splits_into_at_hand_and_closed(self, tmp_path):
        """An open pane means switch windows; nothing open means resume."""
        conn = connect(tmp_path / "i.sqlite")
        _store(conn, "HERE", "awaiting-question", cwd="/open")
        _store(conn, "GONE", "awaiting-question", cwd="/closed")
        data = gather(conn, [TmuxPane("main:1.0", "/open", "2.1.205", window="w")], set())
        assert [r["id"] for r in data["waiting_at_hand"]] == ["HERE"]
        assert [r["id"] for r in data["waiting_closed"]] == ["GONE"]

    def test_closed_groups_ordered_by_recency_not_size(self, tmp_path):
        conn = connect(tmp_path / "i.sqlite")
        _store(conn, "A1", "awaiting-question", ended_days_ago=9, cwd="/a")
        _store(conn, "A2", "awaiting-question", ended_days_ago=9, cwd="/a")
        _store(conn, "B1", "awaiting-question", ended_days_ago=1, cwd="/b")
        conn.execute("UPDATE sessions SET project='big' WHERE id IN ('A1','A2')")
        conn.execute("UPDATE sessions SET project='recent' WHERE id='B1'")
        conn.commit()
        groups = gather(conn, [], set())["grouped_closed"]
        assert [g["project"] for g in groups] == ["recent", "big"]


class TestResumeCwd:
    def test_never_cds_into_an_agents_own_state_directory(self, tmp_path):
        """codex records ChatGPT-project sessions under ~/.codex — cd-ing there
        lands you inside the tool's state, not in a repo."""
        conn = connect(tmp_path / "i.sqlite")
        _store(conn, "C1", "awaiting-question", agent="codex",
               cwd="/Users/vsr/.codex/.chatgpt-projects/g-p-68d7ac")
        cmd = gather(conn, [], set())["waiting"][0]["reentry"]["command"]
        assert cmd == "codex resume C1"
        assert "cd " not in cmd

    def test_claude_state_dir_is_also_refused(self, tmp_path):
        conn = connect(tmp_path / "i.sqlite")
        _store(conn, "C2", "awaiting-question", cwd="/Users/vsr/.claude/projects/x")
        assert gather(conn, [], set())["waiting"][0]["reentry"]["command"] == "claude --resume C2"

    def test_an_ordinary_repo_still_gets_its_cd(self, tmp_path):
        conn = connect(tmp_path / "i.sqlite")
        repo = tmp_path / "code" / "scad"
        repo.mkdir(parents=True)
        _store(conn, "C3", "awaiting-question", cwd=str(repo))
        cmd = gather(conn, [], set())["waiting"][0]["reentry"]["command"]
        assert cmd == f"cd {repo} && claude --resume C3"

    def test_a_dotdir_that_is_not_agent_state_is_untouched(self, tmp_path):
        conn = connect(tmp_path / "i.sqlite")
        nvim = tmp_path / ".config" / "nvim"
        nvim.mkdir(parents=True)
        _store(conn, "C4", "awaiting-question", cwd=str(nvim))
        assert f"cd {nvim}" in gather(conn, [], set())["waiting"][0]["reentry"]["command"]


class TestHumanName:
    """The name position holds only a name a human chose.

    `title` is derived — the agent's own summary, or on a skeleton row the
    literal first message. Putting it where a name belongs is what made a
    renamed session display as "/rename writing-wm-evals-research".
    """

    def _html(self, tmp_path, **columns):
        conn = connect(tmp_path / "i.sqlite")
        _store(conn, "S1", "awaiting-question", cwd="/repo")
        for column, value in columns.items():
            conn.execute(f"UPDATE sessions SET {column} = ? WHERE id = 'S1'", (value,))
        conn.commit()
        return render(gather(conn, [], set()))

    def test_a_renamed_session_is_labelled_by_its_name(self, tmp_path):
        html = self._html(tmp_path, name="jul29-session-cli", title="Doing the thing")
        # The heading holds the label and now the status pill beside it, so the
        # assertion is that the label opens the heading, not that it is all of it.
        assert '<div class="t">jul29-session-cli' in html

    def test_an_unnamed_session_shows_its_id_not_its_title(self, tmp_path):
        html = self._html(tmp_path, title="/rename writing-wm-evals-research")
        assert '<div class="t">S1' in html
        assert '<div class="t">/rename' not in html

    def test_the_client_side_label_follows_the_same_rule(self, tmp_path):
        """`All sessions` is rendered in the browser from the embedded rows."""
        html = self._html(tmp_path, title="Doing the thing")
        assert "r.name || r.id.slice(0, 12)" in html

    def test_the_derived_title_is_still_carried(self, tmp_path):
        """Demoted, not discarded: it stays on the row and stays filterable."""
        html = self._html(tmp_path, title="Doing the thing")
        assert "Doing the thing" in html


class TestPresentation:
    def _full(self, tmp_path):
        conn = connect(tmp_path / "i.sqlite")
        _store(conn, "W1", "awaiting-question", cwd="/open")
        _store(conn, "C1", "awaiting-question", cwd="/closed")
        panes = [TmuxPane("main:3.0", "/open", "2.1.205", window="scad")]
        return render(gather(conn, panes, set()))

    def test_no_section_builds_its_own_table(self, tmp_path):
        """Separate tables each sized their own columns, so nothing lined up.
        One shared grid row makes drift impossible."""
        html = self._full(tmp_path)
        assert "<table" not in html
        assert 'class="row' in html

    def test_sections_share_the_same_row_markup(self, tmp_path):
        html = self._full(tmp_path)
        # panes, waiting-at-hand and closed all render through _row. Counted
        # against the row itself rather than a sibling cell: the cells changed
        # when the layout went to two columns, and the invariant did not.
        assert html.count('class="who"') == html.count('<div class="row')

    def test_status_pills_are_colour_coded(self, tmp_path):
        html = self._full(tmp_path)
        assert 'class="pill maybe-open"' in html
        for token in ("--open:", "--maybe:", "--shut:"):
            assert token in html

    def test_dark_mode_is_defined_for_the_palette(self, tmp_path):
        html = self._full(tmp_path)
        assert "prefers-color-scheme: dark" in html
        assert "--card:#181b21" in html

    def test_a_question_row_is_flagged(self, tmp_path):
        # Membership, not the exact string: the class list also carries layout
        # state now, and pinning the whole attribute broke on a change that was
        # not about flagging at all.
        import re as _re
        classes = _re.findall(r'<div class="(row[^"]*)"', self._full(tmp_path))
        assert any("q" in c.split() for c in classes)

    def test_agents_are_visually_distinguished(self, tmp_path):
        html = self._full(tmp_path)
        assert "--claude:" in html and "--codex:" in html

    def test_still_self_contained(self, tmp_path):
        html = self._full(tmp_path)
        for bad in ("http://", "https://", "<script src", '<link rel="stylesheet"'):
            assert bad not in html


class TestHoverTitles:
    def test_a_command_chip_carries_its_full_text(self, tmp_path):
        conn = connect(tmp_path / "i.sqlite")
        long_cwd = tmp_path / "Library" / "CloudStorage" / "a-very-long-project-name-here"
        long_cwd.mkdir(parents=True)
        _store(conn, "S1", "awaiting-question", cwd=str(long_cwd))
        html = render(gather(conn, [], set()))
        assert f'title="cd {long_cwd} &amp;&amp; claude --resume S1"' in html

    def test_a_truncated_title_keeps_the_original_on_hover(self, tmp_path):
        """_clip actually removes characters, so without the title the rest of
        the text would be unrecoverable from the page."""
        conn = connect(tmp_path / "i.sqlite")
        _store(conn, "S1", "awaiting-question", cwd="/repo")
        long_title = "x" * 200
        conn.execute("UPDATE sessions SET title = ? WHERE id = 'S1'", (long_title,))
        conn.commit()
        html = render(gather(conn, [], set()))
        assert f'title="{long_title}"' in html
        assert "…" in html

    def test_a_short_title_gets_no_needless_tooltip(self, tmp_path):
        conn = connect(tmp_path / "i.sqlite")
        _store(conn, "S1", "awaiting-question", cwd="/repo")
        conn.execute("UPDATE sessions SET title = 'short' WHERE id = 'S1'")
        conn.commit()
        assert 'title="short"' not in render(gather(conn, [], set()))

    def test_titles_are_escaped_in_the_attribute(self, tmp_path):
        """A quote in a title would otherwise break out of the attribute."""
        conn = connect(tmp_path / "i.sqlite")
        _store(conn, "S1", "awaiting-question", cwd="/repo")
        conn.execute("""UPDATE sessions SET title = ? WHERE id = 'S1'""",
                     ('a "quoted" <b>x</b> ' + "y" * 100,))
        conn.commit()
        html = render(gather(conn, [], set()))
        assert '&quot;quoted&quot;' in html
        assert '<b>x</b>' not in html


class TestExpandableText:
    def _html_with_long_title(self, tmp_path, title):
        conn = connect(tmp_path / "i.sqlite")
        _store(conn, "S1", "awaiting-question", cwd="/repo")
        conn.execute("UPDATE sessions SET title = ? WHERE id = 'S1'", (title,))
        conn.commit()
        return render(gather(conn, [], set()))

    def test_clipped_text_carries_the_full_string_for_expansion(self, tmp_path):
        """A tooltip cannot be selected, so hover-only text cannot be copied."""
        long_title = "y" * 200
        html = self._html_with_long_title(tmp_path, long_title)
        assert f'data-full="{long_title}"' in html
        assert 'onclick="expand(event, this)"' in html

    def test_the_expand_handler_makes_it_selectable(self, tmp_path):
        html = self._html_with_long_title(tmp_path, "z" * 200)
        assert "function expand(" in html
        assert "el.textContent = el.dataset.full" in html
        assert "user-select: text" in html

    def test_expanding_drops_the_now_redundant_tooltip(self, tmp_path):
        html = self._html_with_long_title(tmp_path, "z" * 200)
        assert 'el.removeAttribute("title")' in html

    def test_expanding_does_not_trigger_the_row_copy(self, tmp_path):
        """Chips copy on click; expanding sits inside the same row and must not
        also fire a copy."""
        html = self._html_with_long_title(tmp_path, "z" * 200)
        assert "ev.stopPropagation()" in html

    def test_data_full_is_attribute_escaped(self, tmp_path):
        html = self._html_with_long_title(tmp_path, 'has "quotes" ' + "w" * 200)
        assert 'data-full="has &quot;quotes&quot;' in html

    def test_short_text_is_neither_clipped_nor_clickable(self, tmp_path):
        html = self._html_with_long_title(tmp_path, "brief")
        assert 'data-full="brief"' not in html


class TestNotesSection:
    def _with_note(self, tmp_path, **over):
        conn = connect(tmp_path / "i.sqlite")
        _store(conn, "S1", "awaiting-question", cwd="/repo")
        row = {"session_id": "S1", "idx": 0, "ts": int(time.time() * 1000),
               "kind": "info", "topic": "notes-store", "parent": None, "project": None,
               "title": "Built the notes store", "tags": '["append-only","jsonl"]',
               "entities": '["notes.py"]', "memo_path": "/n/S1.jsonl"}
        row.update(over)
        conn.execute(
            "INSERT INTO memos (session_id, idx, ts, kind, topic, parent, project, "
            "title, tags, entities, memo_path) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            tuple(row[k] for k in ("session_id", "idx", "ts", "kind", "topic", "parent",
                                   "project", "title", "tags", "entities", "memo_path")))
        conn.commit()
        return conn

    def test_notes_are_gathered(self, tmp_path):
        conn = self._with_note(tmp_path)
        data = gather(conn, [], set())
        assert [n["session_id"] for n in data["memos"]] == ["S1"]

    def test_a_notes_title_and_kind_reach_the_page(self, tmp_path):
        """Notes moved onto their session's row (2026-09-27) and the row carries
        what identifies a note — its kind and what it said. **Tag chips went
        with the section**: up to twelve per note, four notes per row, would
        have dominated every row. Tags stay searchable through
        `scad notes ls --about` and `scad search --notes`."""
        html = render(gather(self._with_note(tmp_path), [], set()))
        assert 'class="fold-tag">memos<' in html
        assert 'class="memo-kind"' in html
        assert 'class="tag">append-only<' not in html

    def test_topic_and_relation_are_shown(self, tmp_path):
        # relation is derived, not stored: a first note on a fresh topic with no
        # parent is a shift, and the page says so without the column existing.
        html = render(gather(self._with_note(tmp_path), [], set()))
        assert "notes-store" in html and "shift" in html

    def test_a_branch_note_shows_its_parent(self, tmp_path):
        conn = self._with_note(tmp_path, parent="earlier-topic")
        html = render(gather(conn, [], set()))
        assert "earlier-topic" in html and "branch" in html

    def test_malformed_tags_json_does_not_break_the_page(self, tmp_path):
        conn = self._with_note(tmp_path, tags="not json")
        assert "</html>" in render(gather(conn, [], set()))

    def test_an_empty_notes_store_says_how_to_write_one(self, tmp_path):
        """The Notes section was the only place the page named the tier. With
        the section gone the hint has to live somewhere, or removing it to make
        notes visible would have made them harder to discover."""
        conn = connect(tmp_path / "i.sqlite")
        _store(conn, "S1", "awaiting-question", cwd="/repo")
        assert "/memo-write" in render(gather(conn, [], set()))


def _session(sid, **over) -> ClaudeSession:
    """A registry record for a Claude process that is running right now."""
    fields = {"session_id": sid, "pid": 4242, "cwd": "/repo", "name": "",
              "status": "idle", "waiting_for": "", "started_at": 0,
              "kind": "interactive", "entrypoint": "cli", "version": "2.1.219"}
    fields.update(over)
    return ClaudeSession(**fields)


class TestOpenNow:
    """The registry names live sessions exactly, so the page can too.

    `gather()` has always taken `live_ids`, and nothing ever passed one — so
    `status_for` could never return `open` and every session read `maybe-open`.
    """

    def test_a_live_session_the_index_knows_becomes_a_row(self, tmp_path):
        conn = connect(tmp_path / "i.sqlite")
        _store(conn, "S1", "awaiting-question", cwd="/repo")
        data = gather(conn, [], set(),
                      live_sessions=[_session("S1", name="jul29-viewer", status="busy")])
        row = data["open_now"][0]
        assert row["id"] == "S1"
        assert row["name"] == "jul29-viewer"        # from the registry
        assert row["status"] == "busy"              # from the registry
        assert row["project"] == "proj"             # from the index
        assert row["n_turns"] == 0
        assert row["ended"]
        assert "claude --resume S1" in row["reentry"]["command"]

    def test_a_waiting_session_carries_what_it_is_blocked_on(self, tmp_path):
        conn = connect(tmp_path / "i.sqlite")
        _store(conn, "S1", "awaiting-question", cwd="/repo")
        data = gather(conn, [], set(), live_sessions=[
            _session("S1", status="waiting", waiting_for="permission prompt")])
        assert data["open_now"][0]["status"] == "waiting"
        assert data["open_now"][0]["waiting_for"] == "permission prompt"

    def test_rows_sort_by_last_activity_not_by_session_start(self, tmp_path):
        """"Newest" means last message, not when the process was launched — a
        session opened this morning and untouched since is not the live one."""
        conn = connect(tmp_path / "i.sqlite")
        _store(conn, "STALE", "awaiting-question", ended_days_ago=9, cwd="/a")
        _store(conn, "FRESH", "awaiting-question", ended_days_ago=1, cwd="/b")
        data = gather(conn, [], set(), live_sessions=[
            _session("STALE", started_at=9_000_000),     # started most recently
            _session("FRESH", started_at=1_000_000),
        ])
        assert [r["id"] for r in data["open_now"]] == ["FRESH", "STALE"]

    def test_a_live_session_the_index_never_saw_still_appears(self, tmp_path):
        """A session started minutes ago has not been archived yet. Dropping it
        from a section called "open now" is the worst failure this can have."""
        conn = connect(tmp_path / "i.sqlite")
        _store(conn, "KNOWN", "awaiting-question", cwd="/repo")
        data = gather(conn, [], set(), live_sessions=[
            _session("KNOWN"),
            _session("BRAND-NEW", cwd="/elsewhere", name="just-started",
                     started_at=int(time.time() * 1000)),
        ])
        new = next(r for r in data["open_now"] if r["id"] == "BRAND-NEW")
        assert new["name"] == "just-started"        # registry's own name
        assert new["cwd"] == "/elsewhere"           # registry's own cwd
        assert new["indexed"] is False
        assert new["project"] is None
        assert "claude --resume BRAND-NEW" in new["reentry"]["command"]

    def test_the_index_never_invents_a_row_for_a_session_it_has_not_seen(self, tmp_path):
        conn = connect(tmp_path / "i.sqlite")
        data = gather(conn, [], set(), live_sessions=[_session("GHOST")])
        assert data["all"] == []
        assert [r["id"] for r in data["open_now"]] == ["GHOST"]

    def test_a_live_session_is_open_not_maybe_open(self, tmp_path):
        """The bug: `live_ids` defaulted to empty because nothing passed one."""
        conn = connect(tmp_path / "i.sqlite")
        _store(conn, "S1", "awaiting-question", cwd="/repo")
        panes = [TmuxPane("main:1.0", "/repo", "2.1.219")]
        data = gather(conn, panes, set(), live_sessions=[_session("S1")])
        assert data["waiting"][0]["status"] == "open"
        assert data["all"][0]["status"] == "open"

    def test_gather_reads_the_registry_when_nothing_is_injected(self, tmp_path, monkeypatch):
        conn = connect(tmp_path / "i.sqlite")
        _store(conn, "S1", "awaiting-question", cwd="/repo")
        monkeypatch.setattr("scad.view.claude_live_sessions",
                            lambda *a, **k: [_session("S1", status="busy")])
        data = gather(conn, [], set())
        assert [r["id"] for r in data["open_now"]] == ["S1"]
        assert data["waiting"][0]["status"] == "open"

    def test_an_unreadable_registry_leaves_the_page_intact(self, tmp_path, monkeypatch):
        """A machine with no ~/.claude/sessions renders exactly as before."""
        conn = connect(tmp_path / "i.sqlite")
        _store(conn, "S1", "awaiting-question", cwd="/repo")

        def boom(*a, **k):
            raise OSError("no such directory")

        monkeypatch.setattr("scad.view.claude_live_sessions", boom)
        data = gather(conn, [], set())
        assert data["open_now"] == []
        assert data["waiting"][0]["status"] == "closed"
        assert "</html>" in render(data)

    def test_an_explicit_live_id_is_still_honoured(self, tmp_path):
        """The daemon roster and the process registry are separate evidence."""
        conn = connect(tmp_path / "i.sqlite")
        _store(conn, "S1", "awaiting-question", cwd="/repo")
        data = gather(conn, [], set(), live_ids={"S1"})
        assert data["waiting"][0]["status"] == "open"

    def test_non_claude_sessions_keep_todays_behaviour(self, tmp_path):
        """There is no registry for codex or kimi, and guessing one from cwd or
        timing would be inference. A codex pane stays `maybe-open`."""
        conn = connect(tmp_path / "i.sqlite")
        _store(conn, "CX", "awaiting-question", cwd="/repo", agent="codex")
        data = gather(conn, [TmuxPane("main:1.0", "/repo", "codex")], set())
        # The pane lists — something is running there — but as a pane, with no
        # session claimed for it. That is the honest half of what is known.
        assert [(r["id"], r["is_pane"], r["agent"]) for r in data["live_now"]] == \
            [(None, True, "codex")]
        assert data["waiting"][0]["status"] == "maybe-open"


class TestOpenNowSection:
    def _html(self, tmp_path, sessions, **kw):
        conn = connect(tmp_path / "i.sqlite")
        _store(conn, "S1", "awaiting-question", cwd="/repo")
        return render(gather(conn, [], set(), live_sessions=sessions, **kw))

    def test_it_is_the_first_section_on_the_page(self, tmp_path):
        html = self._html(tmp_path, [_session("S1", name="mine")])
        assert html.index(">Live ") < html.index("Waiting")
        assert html.index(">Live ") < html.index("All sessions")

    def test_the_registry_status_is_visible(self, tmp_path):
        html = self._html(tmp_path, [_session("S1", status="busy")])
        assert 'class="pill busy">busy<' in html

    def test_a_waiting_session_names_what_it_wants(self, tmp_path):
        html = self._html(tmp_path, [
            _session("S1", status="waiting", waiting_for="permission prompt")])
        assert 'class="pill waiting">waiting<' in html
        assert "permission prompt" in html

    def test_the_resume_command_is_offered(self, tmp_path):
        assert "claude --resume S1" in self._html(tmp_path, [_session("S1")])

    def test_an_unindexed_session_is_still_drawn(self, tmp_path):
        html = self._html(tmp_path, [_session("NEW1", name="fresh", cwd="/elsewhere")])
        assert "fresh" in html
        assert "claude --resume NEW1" in html

    def test_nothing_open_says_so_plainly(self, tmp_path):
        html = self._html(tmp_path, [])
        assert "Nothing is running" in html

    def test_it_reuses_the_shared_row_markup(self, tmp_path):
        html = self._html(tmp_path, [_session("S1")])
        assert html.count('class="who"') == html.count('<div class="row')

    def test_user_text_is_escaped(self, tmp_path):
        html = self._html(tmp_path, [_session("S1", name="<script>alert(1)</script>")])
        assert "<script>alert(1)</script>" not in html
        assert "&lt;script&gt;" in html


class TestProjectTabs:
    """One page, scoped in the browser. The data is already embedded, so
    switching project costs nothing and needs no second file."""

    def _seed(self, tmp_path):
        conn = connect(tmp_path / "i.sqlite")
        _store(conn, "OLD1", "tool-result-last", ended_days_ago=9, cwd="/o", project="stale")
        _store(conn, "OLD2", "tool-result-last", ended_days_ago=9, cwd="/o", project="stale")
        _store(conn, "NEW1", "awaiting-question", ended_days_ago=1, cwd="/n", project="fresh")
        return conn

    def test_a_tab_per_project_ordered_by_recent_activity(self, tmp_path):
        """Alphabetical would bury the project you were just in."""
        tabs = gather(self._seed(tmp_path), [], set())["tabs"]
        assert [t["project"] for t in tabs] == ["fresh", "stale"]

    def test_each_tab_carries_its_counts(self, tmp_path):
        tabs = {t["project"]: t for t in gather(self._seed(tmp_path), [], set())["tabs"]}
        assert tabs["stale"]["sessions"] == 2
        assert tabs["stale"]["waiting"] == 0
        assert tabs["fresh"]["sessions"] == 1
        assert tabs["fresh"]["waiting"] == 1

    def test_subagents_are_not_counted(self, tmp_path):
        conn = self._seed(tmp_path)
        _store(conn, "SUB", "tool-result-last", kind="subagent", project="fresh")
        tabs = {t["project"]: t for t in gather(conn, [], set())["tabs"]}
        assert tabs["fresh"]["sessions"] == 1

    def test_a_row_with_no_project_gets_no_tab(self, tmp_path):
        """An empty label would collide with the All tab's own empty value."""
        conn = self._seed(tmp_path)
        conn.execute("UPDATE sessions SET project = NULL WHERE id = 'OLD1'")
        conn.commit()
        assert "" not in {t["project"] for t in gather(conn, [], set())["tabs"]}


class TestTabsInThePage:
    def _html(self, tmp_path, **kw):
        conn = connect(tmp_path / "i.sqlite")
        _store(conn, "S1", "awaiting-question", cwd="/a", project="alpha")
        _store(conn, "S2", "awaiting-question", cwd="/b", project="beta")
        return render(gather(conn, [], set(), **kw))

    def test_the_strip_lists_every_project_and_an_all_default(self, tmp_path):
        html = self._html(tmp_path)
        assert 'data-tab=""' in html                     # All
        assert 'data-tab="alpha"' in html
        assert 'data-tab="beta"' in html
        # All is the selected tab, so the page opens exactly as it did before.
        assert 'class="tab on hot" data-tab=""' in html
        # The count on a tab is what is waiting there — nothing waiting has to
        # look different from five things waiting.
        assert 'class="tab hot" data-tab="alpha"' in html

    def test_switching_needs_no_re_render(self, tmp_path):
        """Everything is already in the document; the tabs only hide rows."""
        html = self._html(tmp_path)
        assert "function applyScope()" in html
        assert "scad view" not in html.split("<script>")[1]

    def test_every_row_declares_its_project(self, tmp_path):
        html = self._html(tmp_path)
        assert 'data-project="alpha"' in html
        assert 'data-project="beta"' in html

    def test_open_now_rows_declare_their_project_too(self, tmp_path):
        """Scoping that missed a section would be worse than no scoping."""
        conn = connect(tmp_path / "i.sqlite")
        _store(conn, "S1", "awaiting-question", cwd="/a", project="alpha")
        html = render(gather(conn, [], set(), live_sessions=[_session("S1")]))
        section = html[html.index(">Live "):html.index("Waiting")]
        assert 'data-project="alpha"' in section

    def test_notes_rows_declare_their_project(self, tmp_path):
        conn = connect(tmp_path / "i.sqlite")
        _store(conn, "S1", "awaiting-question", cwd="/a", project="alpha")
        conn.execute("INSERT INTO memos (session_id, idx, ts, topic, title, memo_path) "
                     "VALUES ('S1', 0, 1, 'topic', 'a note', '/n.jsonl')")
        conn.commit()
        html = render(gather(conn, [], set()))
        # Notes ride on their session's row now, so they inherit the row's
        # project rather than declaring one of their own.
        assert 'data-project="alpha"' in html
        assert "a note" in html

    def test_an_empty_section_says_so_in_words(self, tmp_path):
        html = self._html(tmp_path)
        assert "Nothing here for " in html
        assert 'class="empty scoped"' in html

    def test_hidden_rows_are_really_hidden(self, tmp_path):
        """`.row` sets display:grid, which outranks the UA's [hidden] rule."""
        html = self._html(tmp_path)
        assert "[hidden] {{ display: none !important; }}".replace("{{", "{").replace("}}", "}") in html

    def test_a_project_name_cannot_break_out_of_the_markup(self, tmp_path):
        conn = connect(tmp_path / "i.sqlite")
        _store(conn, "S1", "awaiting-question", cwd="/a", project='ev"il<script>')
        html = render(gather(conn, [], set()))
        assert '<script>' not in html.replace("<script>\nconst DATA", "")
        assert "&quot;il&lt;script&gt;" in html

    def test_the_all_sessions_list_is_scoped_by_the_same_selection(self, tmp_path):
        html = self._html(tmp_path)
        assert "SCOPE" in html
        assert 'r.project || ""' in html


class TestStaleness:
    """A page left open all day must show its own age.

    There is no refresh button and cannot be one: this is a `file://` document
    with no server, so it cannot run `scad reindex`, and a button that re-read
    an unchanged file would look like refreshing while changing nothing.
    """

    def _html(self, tmp_path):
        conn = connect(tmp_path / "i.sqlite")
        _store(conn, "S1", "awaiting-question", cwd="/repo")
        return render(gather(conn, [], set()))

    def test_the_absolute_time_is_in_the_header(self, tmp_path):
        data = gather(connect(tmp_path / "i.sqlite"), [], set())
        data["generated"] = 1785000000000
        stamp = datetime.fromtimestamp(1785000000000 / 1000).strftime("%Y-%m-%d %H:%M")
        header = render(data).split("</header>")[0]
        assert stamp in header

    def test_the_relative_age_keeps_counting_in_the_browser(self, tmp_path):
        """Honest: the page genuinely knows more about its age as time passes,
        which is the one thing it can update without a server."""
        html = self._html(tmp_path)
        assert "setInterval" in html
        assert "DATA.generated" in html
        assert 'id="gen"' in html

    def test_an_old_page_marks_itself_stale(self, tmp_path):
        html = self._html(tmp_path)
        assert "stale" in html

    def test_the_regenerate_command_is_there_to_copy(self, tmp_path):
        header = self._html(tmp_path).split("</header>")[0]
        assert 'onclick="copy(this)"' in header
        assert "scad view" in header

    def test_there_is_no_fake_refresh(self, tmp_path):
        """A meta-refresh or a reload button re-reads the same file and reports
        success — the exact failure mode this section exists to prevent."""
        html = self._html(tmp_path)
        for fake in ("http-equiv", "location.reload", "window.location =",
                     "<button>Refresh", "setTimeout(() => location"):
            assert fake not in html


class TestTabCountsMatchTheSections:
    def test_the_number_on_a_tab_is_what_the_waiting_sections_draw(self, tmp_path):
        """A count that disagrees with the rows below it is worse than none."""
        conn = connect(tmp_path / "i.sqlite")
        for sid in ("A1", "A2", "A3"):
            _store(conn, sid, "awaiting-question", cwd="/a", project="alpha")
        _store(conn, "A4", "tool-result-last", cwd="/a", project="alpha")
        _store(conn, "B1", "awaiting-question", cwd="/b", project="beta")
        data = gather(conn, [], set())

        tab = next(t for t in data["tabs"] if t["project"] == "alpha")
        drawn = [r for r in data["waiting_at_hand"] + data["waiting_closed"]
                 if r["project"] == "alpha"]
        assert tab["waiting"] == len(drawn) == 3
        assert tab["sessions"] == len([r for r in data["all"] if r["project"] == "alpha"]) == 4

    def test_all_hides_nothing(self, tmp_path):
        """The default selection has to leave the page exactly as it was.

        Asserted through the predicate rather than a literal source line: the
        old form pinned one expression and broke the moment a second filter
        axis existed, without anything being wrong.
        """
        conn = connect(tmp_path / "i.sqlite")
        _store(conn, "S1", "awaiting-question", cwd="/a", project="alpha")
        html = render(gather(conn, [], set()))
        assert 'r.hidden = !matches(r);' in html
        # Each axis must treat empty as match-all. Asserted per axis rather
        # than by pinning one expression: the predicate has been rewritten
        # twice for reasons unrelated to this invariant, and broke both times.
        assert 'SCOPE !== "" && (el.dataset.project || "") !== SCOPE' in html
        assert 'AGENT !== "" && (el.dataset.agent || "") !== AGENT' in html

    def test_project_agent_and_text_narrow_together(self, tmp_path):
        """Three axes ANDing in one predicate. The text box previously drove
        only the client-rendered list, so typing a query filtered `All
        sessions` and left every other section showing rows that did not
        match — while the summary line reported the narrowed count."""
        conn = connect(tmp_path / "i.sqlite")
        _store(conn, "S1", "awaiting-question", cwd="/a", project="alpha")
        html = render(gather(conn, [], set()))
        # All three axes are consulted by one predicate, so they compose by AND
        # and every section obeys all of them.
        for axis in ("dataset.project", "dataset.agent", "f.value"):
            assert axis in html
        assert "return !q || (el.textContent" in html

    def test_every_row_declares_its_agent(self, tmp_path):
        """One selector for the whole page, so a new section cannot forget to
        join the filter — the same reason `data-project` is on every row."""
        conn = connect(tmp_path / "i.sqlite")
        _store(conn, "S1", "awaiting-question", cwd="/a", project="alpha")
        html = render(gather(conn, [], set()))
        assert 'data-agent="claude"' in html


class TestTheEmbeddedScriptParses:
    """One bad token takes the whole `<script>` down, silently.

    This is not hypothetical. An escaped quote written inside the Python
    template collapsed to three bare quote characters in the emitted page — a
    JS syntax error — so the All-sessions list never drew, `copy()` and
    `expand()` were never defined, and every click handler on the page was
    dead. The page still looked plausible, which is why nothing caught it:
    only the parts rendered in Python were visible.
    """

    def _html(self, tmp_path):
        conn = connect(tmp_path / "i.sqlite")
        _store(conn, "S1", "awaiting-question", cwd="/repo")
        conn.execute("""UPDATE sessions SET title = ? WHERE id = 'S1'""",
                     ('a "quoted" & <tagged> title',))
        conn.commit()
        return render(gather(conn, [], set()))

    def test_the_escaper_is_well_formed(self, tmp_path):
        html = self._html(tmp_path)
        assert '\'"\':"&quot;"' in html
        assert '""":' not in html

    def test_the_whole_script_parses(self, tmp_path):
        """A real parse, when a JS engine is at hand — the only check that
        covers tokens nobody thought to assert on."""
        import shutil
        import subprocess

        node = shutil.which("node")
        if not node:
            pytest.skip("no node to parse with")
        script = self._html(tmp_path).split("<script>", 1)[1].rsplit("</script>", 1)[0]
        path = tmp_path / "page.js"
        path.write_text(script)
        result = subprocess.run([node, "--check", str(path)],
                                capture_output=True, text=True)
        assert result.returncode == 0, result.stderr


class TestNotesAreVisibleOnTheRow:
    """A session with notes must say so where you are already looking.

    Notes are the authored tier — the one thing here that can never be
    re-derived — and until now the page listed them in their own section only.
    A session with three notes rendered identically to one with none, so the
    only way to discover a note was to scroll elsewhere and match session ids
    by eye. The tier you cannot see is the tier you stop writing to.
    """

    def _conn(self, tmp_path):
        conn = connect(tmp_path / "i.sqlite")
        conn.execute(
            "INSERT INTO sessions (id, kind, agent, machine, grade, source, cwd, "
            "project, ended, n_turns) VALUES "
            "('S1','main','claude','m','full','claude-transcript','/r','p',9,3)")
        conn.execute(
            "INSERT INTO sessions (id, kind, agent, machine, grade, source, cwd, "
            "project, ended, n_turns) VALUES "
            "('S2','main','claude','m','full','claude-transcript','/r','p',8,3)")
        for i in range(2):
            conn.execute(
                "INSERT INTO memos (session_id, idx, ts, topic, title, memo_path) "
                "VALUES ('S1', ?, 1, 't', 'ti', '/n.jsonl')", (i,))
        conn.commit()
        return conn

    def test_a_row_carries_its_note_count(self, tmp_path):
        data = gather(self._conn(tmp_path), [], set(), live_sessions=[])
        by_id = {r["id"]: r for r in data["all"]}
        assert by_id["S1"]["n_memos"] == 2

    def test_a_session_without_notes_reports_zero_not_none(self, tmp_path):
        # Zero must be a number so the renderer can test it without guarding
        # for None, and so "no notes" is a stated fact rather than missing data.
        data = gather(self._conn(tmp_path), [], set(), live_sessions=[])
        by_id = {r["id"]: r for r in data["all"]}
        assert by_id["S2"]["n_memos"] == 0

    def test_the_count_reaches_the_rendered_page(self, tmp_path):
        html = render(gather(self._conn(tmp_path), [], set(), live_sessions=[]))
        assert "2 memos" in html


class TestWhatASessionWasAbout:
    """A uuid and a turn count never answer "which of these is the thing I was
    doing". The opener says what a session is; the last word says where it
    stopped. Both are read from turns, because `title` is present on only 72 of
    1571 real sessions (codex: none at all) and cannot carry this."""

    def _conn(self, tmp_path, turns, name="i"):
        conn = connect(tmp_path / f"{name}.sqlite")
        conn.execute(
            "INSERT INTO sessions (id, kind, agent, machine, grade, source, cwd, "
            "project, ended, n_turns) VALUES "
            "('S1','main','claude','m','full','claude-transcript','/r','p',9,9)")
        for i, (role, kind, text) in enumerate(turns):
            conn.execute(
                "INSERT INTO turns (session_id, idx, role, kind, text) "
                "VALUES ('S1', ?, ?, ?, ?)", (i, role, kind, text))
        conn.commit()
        return conn

    def test_the_opening_ask_is_the_first_human_turn(self, tmp_path):
        from scad.view import _first_text

        conn = self._conn(tmp_path, [("user", "text", "port the parser"),
                                     ("assistant", "text", "done")])
        assert _first_text(conn, "S1") == "port the parser"

    def test_machinery_the_agent_wrote_to_itself_is_skipped(self, tmp_path):
        """A session opening with a reminder or an environment block is about
        whatever came after it. 3% of first turns measured are one of these."""
        from scad.view import _first_text

        wrappers = ("<system-reminder>\nnamed this session\n</system-reminder>",
                    "<environment_context>\n<cwd>/r</cwd>\n</environment_context>",
                    "<recommended_plugins> Atlassian </recommended_plugins>")
        for n, wrapper in enumerate(wrappers):
            conn = self._conn(tmp_path, [("user", "text", wrapper),
                                         ("user", "text", "the real ask")], name=f"w{n}")
            assert _first_text(conn, "S1") == "the real ask"

    def test_a_pasted_skill_body_is_not_what_the_session_is_about(self, tmp_path):
        """A slash command pastes its whole skill into the first turn. It says
        which skill ran, never what the human wanted."""
        from scad.view import _first_text

        conn = self._conn(tmp_path, [
            ("user", "text", "Base directory for this skill: /s/recall\nYou are picking up"),
            ("user", "text", "catch me up on scad")])
        assert _first_text(conn, "S1") == "catch me up on scad"

    def test_a_session_with_nothing_human_in_it_says_nothing(self, tmp_path):
        from scad.view import _first_text

        conn = self._conn(tmp_path, [("assistant", "text", "hello")])
        assert _first_text(conn, "S1") == ""

    def test_the_last_word_is_a_message_not_a_tool_result(self, tmp_path):
        """61% of sessions end on a tool_result (945 of 1537 measured), so the
        literal last turn showed tool output on most rows — which answers what
        a tool returned, never where the conversation stopped. That a session
        ended mid-tool-loop is already said by `outcome`."""
        from scad.view import _last_text

        conn = self._conn(tmp_path, [
            ("user", "text", "port the parser"),
            ("assistant", "text", "here is the plan"),
            ("user", "tool_result", "{'files': ['a.py', 'b.py']}")])
        assert _last_text(conn, "S1") == "here is the plan"

    def test_both_ends_reach_every_row_not_only_the_waiting_ones(self, tmp_path):
        """The full list is where "which one was that" gets asked, so a row
        there needs the same context as a row in the waiting section."""
        conn = self._conn(tmp_path, [("user", "text", "port the parser"),
                                     ("assistant", "text", "done")])
        row = next(r for r in gather(conn, [], set(), live_sessions=[])["all"]
                   if r["id"] == "S1")
        assert row["first_text"] == "port the parser"
        assert row["last_text"] == "done"


class TestAContainerSessionCannotBeResumedOnTheHost:
    """A session that ran inside a scad container has a `/workspace/...` cwd
    that does not exist on the host, and its transcript lives in the run's
    bind-mounted claude dir rather than `~/.claude`.

    `cd /workspace/orglens && claude --resume <id>` therefore fails, and it
    failed while looking exactly like every other row's command. The way back
    into a container session is its run.
    """

    ROW = {"id": "C1", "agent": "claude", "kind": "main",
           "cwd": "/workspace/orglens", "scad_run_id": "orglens-p07-Aug03-0045"}

    def test_no_host_resume_is_offered(self):
        from scad.view import resume_command

        assert "claude --resume" not in resume_command(self.ROW)

    def test_the_run_is_the_way_back_in(self):
        from scad.view import _actions

        cmds = " ".join(_actions({**self.ROW, "reentry": {}}))
        assert "scad run attach orglens-p07-Aug03-0045" in cmds

    def test_an_ordinary_host_session_is_untouched(self):
        from scad.view import resume_command

        host = {"id": "H1", "agent": "claude", "kind": "main", "cwd": "/repo"}
        assert resume_command(host) == "cd /repo && claude --resume H1"


class TestOneRowPerOpenSession:
    """Measured 2026-09-24: `open_now_rows` returned 19 rows for 14 distinct
    sessions, so five sessions appeared twice on the page. A session is one
    row; the extra processes holding its id belong on that row."""

    def _held(self):
        from scad.live import ClaudeSession
        return [ClaudeSession("S", 200, cwd="/docs", name="current", updated_at=20,
                              started_at=2000),
                ClaudeSession("S", 100, cwd="/docs", name="stale", updated_at=10,
                              started_at=1000),
                ClaudeSession("T", 300, cwd="/other", name="t", updated_at=5,
                              started_at=500)]

    def test_a_doubly_held_session_gets_one_row(self, monkeypatch):
        from scad.view import open_now_rows
        monkeypatch.setattr("scad.live._process_parents", lambda: {})
        rows = open_now_rows(self._held(), [], [], set())
        assert [r["id"] for r in rows] == ["S", "T"]
        assert rows[0]["pid"] == 200 and rows[0]["name"] == "current"

    def test_the_other_holders_are_on_the_row_with_their_pane(self, monkeypatch):
        from scad.live import TmuxPane
        from scad.view import open_now_rows
        panes = [TmuxPane("scad-cl-1128:0.0", "/docs", "2.1.270", pid=90)]
        monkeypatch.setattr("scad.live._process_parents", lambda: {100: 90})
        rows = open_now_rows(self._held(), [], panes, set())
        assert rows[0]["also_held_by"] == [
            {"pid": 100, "name": "stale", "pane": "scad-cl-1128:0.0"}]
        assert rows[1]["also_held_by"] == []

    def test_the_page_says_a_session_is_held_twice(self, tmp_path, monkeypatch):
        from scad.index import connect
        from scad.view import gather, render
        monkeypatch.setattr("scad.live._process_parents", lambda: {})
        conn = connect(tmp_path / "i.sqlite")
        html = render(gather(conn, [], set(), live_sessions=self._held()))
        assert "also open" in html


class TestOneLiveSection:
    """`Open now` and `Agent panes` were mostly the same rows sourced two ways:
    the registry gives exact identity and no location, tmux gives exact location
    and a guessed occupant. Approved in design 2026-08-02, blocked until the
    pane could be a fact rather than a guess — which process-tree matching made true
    in 0.6.0. One row per live session, plus any agent pane that cannot be
    resolved to one, saying so."""

    @pytest.fixture(autouse=True)
    def _no_real_launches(self, monkeypatch, tmp_path):
        # live_rows reads launch records, and a real one naming a pane at one
        # of these targets in the running tmux turned a pane row into a session.
        monkeypatch.setenv("SCAD_HOME", str(tmp_path / "scad-home"))

    def _sessions(self):
        from scad.live import ClaudeSession
        return [ClaudeSession("S", 200, cwd="/docs", name="current", updated_at=20,
                              started_at=2000)]

    def _panes(self):
        from scad.live import TmuxPane
        return [TmuxPane("main:5.0", "/docs", "2.1.270", window="docs", pid=90),
                TmuxPane("main:6.0", "/other", "codex", window="cx", pid=91)]

    def test_a_resolved_pane_is_not_listed_twice(self, monkeypatch):
        from scad.view import live_rows
        monkeypatch.setattr("scad.live._process_parents", lambda: {200: 90})
        rows = live_rows(self._sessions(), self._panes(), [], set())
        assert [(r["id"], r["is_pane"]) for r in rows] == [("S", False), (None, True)]
        assert rows[0]["target"] == "main:5.0", "the session carries its proven pane"

    def test_an_unresolvable_pane_still_lists_and_says_so(self, monkeypatch):
        from scad.view import live_rows
        monkeypatch.setattr("scad.live._process_parents", lambda: {})
        rows = live_rows([], self._panes(), [], set())
        assert len(rows) == 2 and all(r["is_pane"] for r in rows)
        assert {r["agent"] for r in rows} == {"claude", "codex"}

    def test_a_launch_record_names_a_codex_pane_as_its_session(self, monkeypatch, tmp_path):
        """codex and kimi publish no registry, so the process tree cannot name
        their session — but a launch record does, for any family. Without this
        a scad-launched codex session showed as an anonymous pane."""
        from scad.view import live_rows
        monkeypatch.setattr("scad.live._process_parents", lambda: {})
        rows = live_rows([], self._panes(), [{"id": "CX1", "project": "p", "n_turns": 3}], set(),
                         records=[{"session_id": "CX1", "tmux": "main:6.0", "agent": "codex"}])
        by = {(r["id"], r["is_pane"]) for r in rows}
        assert ("CX1", False) in by, rows
        assert (None, True) in by, "the unrecorded claude pane still lists as a pane"

    def test_a_live_session_with_no_pane_at_all_still_lists(self, monkeypatch):
        """A session the registry proves is running, whose pane scad cannot
        name. Losing it from the live list is the worst failure here."""
        from scad.view import live_rows
        monkeypatch.setattr("scad.live._process_parents", lambda: {})
        rows = live_rows(self._sessions(), [], [], set())
        assert [r["id"] for r in rows] == ["S"]
        assert rows[0]["target"] in (None, "")

    def test_the_page_has_one_live_section_and_no_agent_panes_heading(self, tmp_path, monkeypatch):
        from scad.index import connect
        from scad.view import gather, render
        monkeypatch.setattr("scad.live._process_parents", lambda: {200: 90})
        html = render(gather(connect(tmp_path / "i.sqlite"), self._panes(), set(),
                             live_sessions=self._sessions()))
        assert 'data-sec="live"' in html
        assert "Agent panes" not in html
        assert 'data-sec="open-now"' not in html and 'data-sec="panes"' not in html


class TestRecencyBuckets:
    """The all-sessions list is a flat wall — ~337 rows here, some months old.
    The open item asked whether `--days` should default to something finite;
    grouping answers it without a threshold, because nothing gets hidden. The
    list is drawn in the browser from DATA, so the buckets are JS.
    """

    def _html(self, tmp_path):
        from scad.index import connect
        from scad.view import gather, render
        return render(gather(connect(tmp_path / "i.sqlite"), [], set()))

    def test_the_page_carries_a_bucket_function_and_its_labels(self, tmp_path):
        html = self._html(tmp_path)
        assert "function bucketOf" in html
        for label in ("Today", "Yesterday", "This week", "This month", "Older"):
            assert label in html, label

    def test_the_buckets_are_ordered_newest_first(self, tmp_path):
        html = self._html(tmp_path)
        order = [html.index(f'"{lab}"') for lab in
                 ("Today", "Yesterday", "This week", "This month", "Older")]
        assert order == sorted(order), "bucket labels must be declared newest first"

    def test_rows_are_grouped_under_bucket_headings(self, tmp_path):
        """The grouping has to happen where the list is built, or a filter would
        leave headings with nothing under them."""
        html = self._html(tmp_path)
        assert "bucketed(" in html
        assert 'class="bucket"' in html


class TestNotesOnTheRow:
    """Notes are the authored tier — the one thing here that can never be
    re-derived — and they sat in a section of their own, invisible unless you
    scrolled to it and matched session ids by eye. On the row, the separate
    section has nothing left to do."""

    def _seed(self, tmp_path):
        import json as _json
        import time as _time
        from scad.index import append_memos, connect
        from scad.records import MemoRecord
        conn = connect(tmp_path / "i.sqlite")
        _store(conn, "S1", "awaiting-question", cwd="/repo", project="proj")
        now = int(_time.time() * 1000)
        append_memos(conn, "S1", [
            MemoRecord(ts=now - 200, kind="handoff", topic="the-topic", title="first note"),
            MemoRecord(ts=now - 100, kind="bug", topic="a-bug", title="second note"),
        ], "/notes/S1.jsonl")
        conn.commit()
        return conn

    def test_a_rows_notes_are_on_the_row(self, tmp_path):
        from scad.view import gather
        conn = self._seed(tmp_path)
        row = next(r for r in gather(conn, [], set())["all"] if r["id"] == "S1")
        assert row["n_memos"] == 2
        # Newest first, and enough to read: what kind it was and what it said.
        assert [n["title"] for n in row["memos"]] == ["second note", "first note"]
        assert row["memos"][0]["kind"] == "bug"
        assert row["memos"][0]["topic"] == "a-bug"

    def test_a_session_with_no_notes_carries_an_empty_list(self, tmp_path):
        from scad.index import connect
        from scad.view import gather
        conn = connect(tmp_path / "i.sqlite")
        _store(conn, "S2", "awaiting-question", cwd="/repo")
        row = next(r for r in gather(conn, [], set())["all"] if r["id"] == "S2")
        assert row["memos"] == [] and row["n_memos"] == 0

    def test_the_server_rendered_row_shows_them(self, tmp_path):
        from scad.view import session_row
        html = session_row({"id": "S1", "agent": "claude", "kind": "main", "n_memos": 1,
                            "memos": [{"kind": "handoff", "topic": "t", "title": "the note",
                                       "ts": 1}]})
        assert "the note" in html and "handoff" in html

    def test_the_page_has_no_notes_section_of_its_own(self, tmp_path):
        from scad.view import gather, render
        html = render(gather(self._seed(tmp_path), [], set()))
        assert 'data-sec="notes"' not in html
        assert "second note" in html, "the note itself must still be on the page"

    def test_the_client_side_list_renders_them_too(self, tmp_path):
        from scad.view import gather, render
        html = render(gather(self._seed(tmp_path), [], set()))
        assert "function memoBlock" in html


def _alias_home(tmp_path, monkeypatch, text=""):
    from scad import aliases

    home = tmp_path / ".scad"
    home.mkdir(exist_ok=True)
    monkeypatch.setenv("SCAD_HOME", str(home))
    (home / "aliases").write_text(text)
    aliases.reset()
    return home


class TestRowsServeWhereTheDirectoryIsNow:
    """The row's `cwd` is the one the export serves, set before the resume
    command and the pane match are built from it."""

    def _moved(self, tmp_path, monkeypatch):
        new = tmp_path / "new" / "myproj"
        new.mkdir(parents=True)
        old = tmp_path / "old" / "myproj"
        _alias_home(tmp_path, monkeypatch, f"{old} -> {new}\n")
        return old, new

    def test_a_moved_row_carries_both_paths(self, tmp_path, monkeypatch):
        old, new = self._moved(tmp_path, monkeypatch)
        conn = connect(tmp_path / "i.sqlite")
        _store(conn, "S1", "awaiting-question", cwd=str(old))
        row = gather(conn, [], set())["waiting"][0]
        assert row["cwd"] == str(new)
        assert row["cwd_recorded"] == str(old)

    def test_a_gone_row_with_no_rule_keeps_its_recorded_path(self, tmp_path, monkeypatch):
        _alias_home(tmp_path, monkeypatch)
        conn = connect(tmp_path / "i.sqlite")
        _store(conn, "S1", "awaiting-question", cwd=str(tmp_path / "gone"))
        row = gather(conn, [], set())["waiting"][0]
        assert row["cwd"] == row["cwd_recorded"] == str(tmp_path / "gone")


class TestAGoneDirectory:
    """The page follows `session resume`: a moved directory resumes in its new
    place, and a gone one still resumes, without a `cd` that would fail."""

    def test_a_moved_row_resumes_in_the_new_path(self, tmp_path, monkeypatch):
        new = tmp_path / "new"
        new.mkdir()
        _alias_home(tmp_path, monkeypatch, f"{tmp_path}/old -> {new}\n")
        conn = connect(tmp_path / "i.sqlite")
        _store(conn, "S1", "awaiting-question", cwd=str(tmp_path / "old"))
        row = gather(conn, [], set())["waiting"][0]
        assert row["reentry"]["command"] == f"cd {new} && claude --resume S1"
        assert not row["cwd_gone"]

    def test_a_gone_row_resumes_without_the_cd_and_says_why(self, tmp_path, monkeypatch):
        _alias_home(tmp_path, monkeypatch)
        conn = connect(tmp_path / "i.sqlite")
        _store(conn, "S1", "awaiting-question", cwd=str(tmp_path / "gone"))
        data = gather(conn, [], set())
        row = data["waiting"][0]
        assert row["cwd_gone"]
        assert row["reentry"]["command"] == "claude --resume S1"
        html = render(data)
        assert 'data-cmd="claude --resume S1"' in html
        assert "directory gone" in html

    def test_rows_that_never_had_a_host_directory_keep_theirs(self, tmp_path, monkeypatch):
        _alias_home(tmp_path, monkeypatch)
        conn = connect(tmp_path / "i.sqlite")
        _store(conn, "C1", "awaiting-question", agent="codex", cwd=str(tmp_path / ".codex" / "p"))
        row = gather(conn, [], set())["waiting"][0]
        assert not row["cwd_gone"]
        assert row["reentry"]["command"] == "codex resume C1"


class TestContextFill:
    """How much of each session's context is used, wherever a session shows."""

    def test_the_short_label(self):
        from scad.view import context_short
        assert context_short(None, None) == ""
        assert context_short(653_162, 1_000_000) == "65%"
        assert context_short(150_000, None) == "150k"
        assert context_short(900, None) == "900"

    def test_the_long_label(self):
        from scad.view import context_long
        assert context_long(215_227, 258_400) == "215,227 of 258,400 tokens (83%)"
        assert context_long(150_000, None) == "150,000 tokens (window unknown)"
        assert context_long(None, None) == ""

    def test_a_row_on_the_page_shows_it(self, tmp_path):
        conn = connect(tmp_path / "i.sqlite")
        _store(conn, "S1", "awaiting-question")
        conn.execute("UPDATE sessions SET context_tokens = 215227, context_window = 258400")
        conn.commit()
        data = gather(conn, [], set(), live_sessions=[])
        row = next(r for r in data["all"] if r["id"] == "S1")
        assert row["context_label"] == "83%"
        assert "83%" in render(data)

    def test_the_client_side_rows_show_it_too(self, tmp_path):
        html = render(gather(connect(tmp_path / "i.sqlite"), [], set(), live_sessions=[]))
        assert "r.context_label" in html


class TestNearlyFull:
    """Nearly full means write a handoff now, while the session still has room
    to write a good one: 80% of a known window, or 160k tokens when the window
    is unknown (80% of the smaller Claude window; a 1M session is flagged early,
    which is the safe mistake)."""

    def test_the_rule(self):
        from scad.view import nearly_full
        assert nearly_full(206_720, 258_400) is True       # 80% of a known window
        assert nearly_full(200_000, 258_400) is False
        assert nearly_full(160_000, None) is True           # unknown window
        assert nearly_full(159_999, None) is False
        assert nearly_full(650_000, 1_000_000) is False
        assert nearly_full(None, None) is False

    def test_a_nearly_full_row_is_marked_on_the_page(self, tmp_path):
        conn = connect(tmp_path / "i.sqlite")
        _store(conn, "S1", "awaiting-question")
        conn.execute("UPDATE sessions SET context_tokens = 230000, context_window = 258400")
        conn.commit()
        data = gather(conn, [], set(), live_sessions=[])
        row = next(r for r in data["all"] if r["id"] == "S1")
        assert row["nearly_full"] is True
        html = render(data)
        assert 'class="ctx-full"' in html and "write a handoff" in html

    def test_the_client_side_rows_mark_it_too(self, tmp_path):
        html = render(gather(connect(tmp_path / "i.sqlite"), [], set(), live_sessions=[]))
        assert "r.nearly_full" in html
