"""Tests for the session viewer."""

from scad.live import TmuxPane
from scad.view import Reentry, reentry_for


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
        assert r.command == "tmux select-window -t main:1 \\; select-pane -t main:1.2"

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
        assert "select-pane -t main:3.0" in r.command
        # The escaped semicolon survives a shell paste as a tmux command separator.
        assert " \\; " in r.command

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
        assert "main:1.2" in r.command
        assert "main:3.0" in r.note
        assert "ambiguous" in r.note.lower()

    def test_running_container_beats_resume(self):
        r = reentry_for(row(scad_run_id="demo-Jul28-1200"), [], {"demo-Jul28-1200"})
        assert r.kind == "container"
        assert r.command == "scad run attach demo-Jul28-1200"

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


import time

from scad.index import append_turns, connect, upsert_session
from scad.records import KIND_MAIN, SessionRecord, TurnRecord
from scad.view import gather


def _store(conn, sid, outcome, ended_days_ago=0, cwd="/repo", agent="claude", kind=KIND_MAIN):
    ended = int((time.time() - ended_days_ago * 86400) * 1000)
    rec = SessionRecord(id=sid, kind=kind, agent=agent, source="claude-transcript",
                        cwd=cwd, started=ended - 1000, ended=ended, outcome=outcome)
    upsert_session(conn, rec, machine="mac", project="proj", archive_path=f"/arc/{sid}.jsonl",
                   source_size=1, source_mtime=1, parsed_offset=1)
    return rec


class TestGather:
    def test_waiting_holds_only_sessions_awaiting_input(self, tmp_path):
        conn = connect(tmp_path / "i.sqlite")
        _store(conn, "W1", "awaiting-user")
        _store(conn, "Q1", "awaiting-question")
        _store(conn, "D1", "tool-result-last")
        data = gather(conn, [], set())
        assert {r["id"] for r in data["waiting"]} == {"W1", "Q1"}

    def test_questions_sort_before_plain_waiting(self, tmp_path):
        """An explicit question is a stronger claim on your attention."""
        conn = connect(tmp_path / "i.sqlite")
        _store(conn, "W1", "awaiting-user", ended_days_ago=5)
        _store(conn, "Q1", "awaiting-question", ended_days_ago=1)
        assert [r["id"] for r in gather(conn, [], set())["waiting"]] == ["Q1", "W1"]

    def test_oldest_first_within_a_group_so_nothing_rots(self, tmp_path):
        conn = connect(tmp_path / "i.sqlite")
        _store(conn, "NEW", "awaiting-user", ended_days_ago=1)
        _store(conn, "OLD", "awaiting-user", ended_days_ago=6)
        assert [r["id"] for r in gather(conn, [], set())["waiting"]] == ["OLD", "NEW"]

    def test_window_excludes_ancient_sessions(self, tmp_path):
        conn = connect(tmp_path / "i.sqlite")
        _store(conn, "OLD", "awaiting-user", ended_days_ago=90)
        assert gather(conn, [], set(), days=14)["waiting"] == []

    def test_subagents_never_appear_in_waiting(self, tmp_path):
        conn = connect(tmp_path / "i.sqlite")
        _store(conn, "SUB", "awaiting-user", kind="subagent")
        assert gather(conn, [], set())["waiting"] == []

    def test_live_lists_sessions_with_a_matching_pane(self, tmp_path):
        conn = connect(tmp_path / "i.sqlite")
        _store(conn, "L1", "tool-result-last", cwd="/repo")
        panes = [TmuxPane("main:1.0", "/repo", "2.1.219")]
        data = gather(conn, panes, set())
        assert [r["id"] for r in data["live"]] == ["L1"]

    def test_every_row_carries_a_reentry(self, tmp_path):
        conn = connect(tmp_path / "i.sqlite")
        _store(conn, "W1", "awaiting-user")
        r = gather(conn, [], set())["waiting"][0]
        assert r["reentry"]["kind"] == "resume"
        assert "claude --resume W1" in r["reentry"]["command"]

    def test_waiting_rows_carry_the_last_turn_text(self, tmp_path):
        """So you can remember where the conversation left off."""
        conn = connect(tmp_path / "i.sqlite")
        _store(conn, "W1", "awaiting-user")
        append_turns(conn, "W1", [
            TurnRecord(ts=1, role="assistant", kind="text", text="first"),
            TurnRecord(ts=2, role="assistant", kind="text", text="the last thing said"),
        ])
        assert gather(conn, [], set())["waiting"][0]["last_text"] == "the last thing said"

    def test_all_lists_only_human_started_sessions(self, tmp_path):
        """A subagent is triggered by an agent, cannot be resumed, and was never
        started by you — 1309 of 1462 real rows. It is not a peer."""
        conn = connect(tmp_path / "i.sqlite")
        _store(conn, "A", "awaiting-user")
        _store(conn, "B", "tool-result-last", kind="subagent")
        rows = gather(conn, [], set())["all"]
        assert [r["id"] for r in rows] == ["A"]

    def test_a_parent_carries_its_agent_count(self, tmp_path):
        conn = connect(tmp_path / "i.sqlite")
        _store(conn, "P", "awaiting-user")
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
                             "title": "a title", "outcome": "awaiting-user", "needs": None,
                             "harness_state": None, "agent": "claude", "kind": "main",
                             "n_turns": 3, "started": 1, "ended": 2, "grade": "full",
                             "scad_run_id": None, "last_text": "where we left off",
                             "reentry": {"kind": "resume",
                                         "command": "cd /repo && claude --resume S1", "note": ""}}],
                "live": [], "all": [], "generated": 1785000000000,
                "waiting_at_hand": [], "waiting_closed": [],
                "grouped_panes": [], "grouped_closed": []}

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
                       "grouped_panes": [], "grouped_closed": []})
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


class TestStatus:
    def test_roster_proves_a_session_is_open(self, tmp_path):
        """The only exact signal: the daemon maps sessionId -> a live pid."""
        conn = connect(tmp_path / "i.sqlite")
        _store(conn, "S1", "awaiting-user", cwd="/repo")
        data = gather(conn, [], set(), live_ids={"S1"})
        assert data["waiting"][0]["status"] == "open"

    def test_an_agent_in_the_same_cwd_is_only_maybe(self, tmp_path):
        """claude does not hold its transcript open, so a running agent cannot be
        traced to a session. With one project per window, this is the normal case."""
        conn = connect(tmp_path / "i.sqlite")
        _store(conn, "S1", "awaiting-user", cwd="/repo")
        panes = [TmuxPane("main:1.0", "/repo", "2.1.219")]
        assert gather(conn, panes, set())["waiting"][0]["status"] == "maybe-open"

    def test_no_agent_anywhere_is_closed(self, tmp_path):
        conn = connect(tmp_path / "i.sqlite")
        _store(conn, "S1", "awaiting-user", cwd="/repo")
        assert gather(conn, [], set())["waiting"][0]["status"] == "closed"

    def test_a_shell_in_the_cwd_does_not_make_it_maybe(self, tmp_path):
        conn = connect(tmp_path / "i.sqlite")
        _store(conn, "S1", "awaiting-user", cwd="/repo")
        panes = [TmuxPane("main:1.0", "/repo", "zsh")]
        assert gather(conn, panes, set())["waiting"][0]["status"] == "closed"

    def test_a_running_container_is_open(self, tmp_path):
        conn = connect(tmp_path / "i.sqlite")
        now = int(time.time() * 1000)
        rec = SessionRecord(id="C1", kind=KIND_MAIN, agent="claude", source="claude-transcript",
                            cwd="/workspace/x", started=now - 1000, ended=now,
                            outcome="awaiting-user")
        upsert_session(conn, rec, machine="mac", project="x", archive_path="/a.jsonl",
                       source_size=1, source_mtime=1, parsed_offset=1, scad_run_id="r1")
        assert gather(conn, [], {"r1"})["waiting"][0]["status"] == "open"

    def test_roster_beats_a_merely_shared_cwd(self, tmp_path):
        conn = connect(tmp_path / "i.sqlite")
        _store(conn, "S1", "awaiting-user", cwd="/repo")
        panes = [TmuxPane("main:1.0", "/repo", "2.1.219")]
        assert gather(conn, panes, set(), live_ids={"S1"})["waiting"][0]["status"] == "open"

    def test_the_page_shows_the_resume_command_even_when_a_pane_matches(self, tmp_path):
        """A tmux target is ambiguous; the resume command never is."""
        conn = connect(tmp_path / "i.sqlite")
        _store(conn, "S1", "awaiting-user", cwd="/repo")
        panes = [TmuxPane("main:1.0", "/repo", "2.1.219")]
        html = render(gather(conn, panes, set()))
        assert "claude --resume S1" in html
        assert "maybe-open" in html


class TestLivePanes:
    def test_a_codex_pane_is_not_guessed_a_claude_session(self, tmp_path):
        """Panes share cwds; without filtering by agent, a codex pane was offered
        the claude session sitting in the same directory."""
        conn = connect(tmp_path / "i.sqlite")
        _store(conn, "CLAUDE1", "awaiting-user", cwd="/repo", agent="claude")
        _store(conn, "CODEX1", "awaiting-user", cwd="/repo", agent="codex")
        panes = [TmuxPane("main:3.1", "/repo", "codex", window="scad")]
        rows = gather(conn, panes, set())["panes"]
        assert rows[0]["agent"] == "codex"
        assert rows[0]["likely_id"] == "CODEX1"

    def test_every_agent_pane_gets_a_row_even_sharing_a_cwd(self, tmp_path):
        conn = connect(tmp_path / "i.sqlite")
        _store(conn, "S1", "awaiting-user", cwd="/repo")
        panes = [TmuxPane("main:3.0", "/repo", "2.1.205", window="scad"),
                 TmuxPane("main:3.2", "/repo", "2.1.220", window="scad")]
        assert [r["target"] for r in gather(conn, panes, set())["panes"]] == ["main:3.0", "main:3.2"]

    def test_non_agent_panes_are_excluded(self, tmp_path):
        conn = connect(tmp_path / "i.sqlite")
        panes = [TmuxPane("main:2.0", "/repo", "zsh", window="docs")]
        assert gather(conn, panes, set())["panes"] == []

    def test_a_pane_with_no_matching_session_still_appears(self, tmp_path):
        conn = connect(tmp_path / "i.sqlite")
        panes = [TmuxPane("main:9.0", "/never/indexed", "2.1.219", window="new")]
        rows = gather(conn, panes, set())["panes"]
        assert rows[0]["likely_id"] is None
        assert rows[0]["goto"].startswith("tmux select-window")

    def test_window_name_and_tmux_session_are_carried(self, tmp_path):
        conn = connect(tmp_path / "i.sqlite")
        panes = [TmuxPane("main2:1.0", "/repo", "2.1.219", window="orglens")]
        row = gather(conn, panes, set())["panes"][0]
        assert row["window"] == "orglens"
        assert row["tmux_session"] == "main2"


class TestGrouping:
    def test_panes_nest_session_then_window(self, tmp_path):
        conn = connect(tmp_path / "i.sqlite")
        panes = [TmuxPane("main:3.0", "/a", "2.1.205", window="scad"),
                 TmuxPane("main:3.1", "/a", "codex", window="scad"),
                 TmuxPane("main2:1.0", "/b", "2.1.219", window="other")]
        groups = gather(conn, panes, set())["grouped_panes"]
        assert {g["session"] for g in groups} == {"main", "main2"}
        main = next(g for g in groups if g["session"] == "main")
        assert len(main["windows"]) == 1
        assert main["windows"][0]["label"] == "scad"
        assert len(main["windows"][0]["panes"]) == 2

    def test_ordered_by_last_message_not_tmux_index(self, tmp_path):
        """What you touched last is what you are coming back to."""
        conn = connect(tmp_path / "i.sqlite")
        _store(conn, "OLD", "awaiting-user", ended_days_ago=9, cwd="/old")
        _store(conn, "NEW", "awaiting-user", ended_days_ago=1, cwd="/new")
        panes = [TmuxPane("main:1.0", "/old", "2.1.205", window="stale"),
                 TmuxPane("main:9.0", "/new", "2.1.205", window="fresh")]
        windows = gather(conn, panes, set())["grouped_panes"][0]["windows"]
        assert [w["label"] for w in windows] == ["fresh", "stale"]

    def test_a_pane_with_no_known_session_sorts_last(self, tmp_path):
        conn = connect(tmp_path / "i.sqlite")
        _store(conn, "S1", "awaiting-user", ended_days_ago=3, cwd="/known")
        panes = [TmuxPane("main:1.0", "/unknown", "2.1.205", window="new"),
                 TmuxPane("main:2.0", "/known", "2.1.205", window="known")]
        windows = gather(conn, panes, set())["grouped_panes"][0]["windows"]
        assert [w["label"] for w in windows] == ["known", "new"]

    def test_waiting_splits_into_at_hand_and_closed(self, tmp_path):
        """An open pane means switch windows; nothing open means resume."""
        conn = connect(tmp_path / "i.sqlite")
        _store(conn, "HERE", "awaiting-user", cwd="/open")
        _store(conn, "GONE", "awaiting-user", cwd="/closed")
        data = gather(conn, [TmuxPane("main:1.0", "/open", "2.1.205", window="w")], set())
        assert [r["id"] for r in data["waiting_at_hand"]] == ["HERE"]
        assert [r["id"] for r in data["waiting_closed"]] == ["GONE"]

    def test_closed_groups_ordered_by_recency_not_size(self, tmp_path):
        conn = connect(tmp_path / "i.sqlite")
        _store(conn, "A1", "awaiting-user", ended_days_ago=9, cwd="/a")
        _store(conn, "A2", "awaiting-user", ended_days_ago=9, cwd="/a")
        _store(conn, "B1", "awaiting-user", ended_days_ago=1, cwd="/b")
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
        _store(conn, "C1", "awaiting-user", agent="codex",
               cwd="/Users/vsr/.codex/.chatgpt-projects/g-p-68d7ac")
        cmd = gather(conn, [], set())["waiting"][0]["reentry"]["command"]
        assert cmd == "codex resume C1"
        assert "cd " not in cmd

    def test_claude_state_dir_is_also_refused(self, tmp_path):
        conn = connect(tmp_path / "i.sqlite")
        _store(conn, "C2", "awaiting-user", cwd="/Users/vsr/.claude/projects/x")
        assert gather(conn, [], set())["waiting"][0]["reentry"]["command"] == "claude --resume C2"

    def test_an_ordinary_repo_still_gets_its_cd(self, tmp_path):
        conn = connect(tmp_path / "i.sqlite")
        _store(conn, "C3", "awaiting-user", cwd="/Users/vsr/code/scad")
        cmd = gather(conn, [], set())["waiting"][0]["reentry"]["command"]
        assert cmd == "cd /Users/vsr/code/scad && claude --resume C3"

    def test_a_dotdir_that_is_not_agent_state_is_untouched(self, tmp_path):
        conn = connect(tmp_path / "i.sqlite")
        _store(conn, "C4", "awaiting-user", cwd="/Users/vsr/.config/nvim")
        assert "cd /Users/vsr/.config/nvim" in gather(conn, [], set())["waiting"][0]["reentry"]["command"]


class TestPresentation:
    def _full(self, tmp_path):
        conn = connect(tmp_path / "i.sqlite")
        _store(conn, "W1", "awaiting-user", cwd="/open")
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
        # panes, waiting-at-hand and closed all render through _row.
        assert html.count('class="who"') == html.count('class="go"')

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
        assert 'class="row q"' in self._full(tmp_path)

    def test_agents_are_visually_distinguished(self, tmp_path):
        html = self._full(tmp_path)
        assert "--claude:" in html and "--codex:" in html

    def test_still_self_contained(self, tmp_path):
        html = self._full(tmp_path)
        for bad in ("http://", "https://", "<script src", '<link rel="stylesheet"'):
            assert bad not in html
