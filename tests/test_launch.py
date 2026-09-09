"""Tests for the launch record and `scad session launch`.

**No test here starts a real agent.** Every launch path runs against a stub
script (`STUB`, below) that renders captured screens and records the keys it
was sent — a real launch costs a model call and puts a junk session in the
corpus. And every tmux call goes to a private socket, never the default server:
`tmux new-session` on the default socket lands in whatever the human has open.
"""

import json
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from scad.launch import launches_root, read_record, record_path, write_record

TEST_SOCKET = "scad-test-suite"


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("SCAD_HOME", str(tmp_path / ".scad"))
    # Belt and braces: even the pure tests get the private socket, so a future
    # test that reaches tmux by accident cannot reach the human's server.
    monkeypatch.setenv("SCAD_TMUX_SOCKET", TEST_SOCKET)
    return tmp_path / ".scad"


RECORD = {
    "agent": "codex",
    "session_id": "019fb23c-6ce5-7580-b1b0-5a69839a946b",
    "cwd": "/Users/vsr/Desktop/scad-handoff-demo",
    "tmux": "scad-cx-1430:0.0",
    "started": "2026-07-30T14:30:00Z",
    "resume": "cd /Users/vsr/Desktop/scad-handoff-demo && codex resume 019fb23c",
    "provenance": "tui-native",
}


class TestWhereRecordsLive:
    def test_one_file_per_session_under_scad_home(self, _home):
        assert record_path("S1") == _home / "launches" / "S1.json"

    def test_the_root_is_not_the_archive(self, _home):
        """`reindex --rebuild` drops and rebuilds the index from the archive.

        A launch record is an authored fact about an event — nothing can
        recompute it — so it lives beside notes, not inside the archive.
        """
        assert launches_root() == _home / "launches"


class TestRoundTrip:
    def test_what_was_written_is_what_is_read(self):
        write_record(RECORD)
        assert read_record(RECORD["session_id"]) == RECORD

    def test_the_file_is_json_a_human_can_read(self):
        path = write_record(RECORD)
        assert json.loads(path.read_text())["provenance"] == "tui-native"
        assert path.read_text().endswith("\n")

    def test_an_unknown_session_has_no_record(self):
        assert read_record("never-launched") is None

    def test_a_record_with_no_session_id_is_refused(self):
        with pytest.raises(ValueError):
            write_record({k: v for k, v in RECORD.items() if k != "session_id"})


class TestDegradingRatherThanRaising:
    """Reading a record is on the resume path, which must still work when the
    record is unreadable — the index alone is enough to resume (§5)."""

    def test_a_corrupt_record_reads_as_absent(self, _home):
        path = record_path("S1")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{not json")
        assert read_record("S1") is None

    def test_a_record_that_is_not_an_object_reads_as_absent(self, _home):
        path = record_path("S1")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('["a list"]')
        assert read_record("S1") is None

    def test_a_session_id_that_is_not_a_filename_cannot_escape_the_store(self):
        """An id arrives from the command line, so it names a path component
        only if we insist that it does."""
        assert read_record("../../etc/passwd") is None
        with pytest.raises(ValueError):
            write_record({**RECORD, "session_id": "../escape"})


# --- captured screens -------------------------------------------------------
# Measured on this machine on 2026-07-30 by launching the TUI in a tmux pane
# and reading it with capture-pane. Nothing below is paraphrased: it is the
# fixture the gate detection is built against, and the stub agent renders it.

UPDATE_GATE = """\
  ✨ Update available! 0.142.5 -> 0.146.0
  Release notes: https://github.com/openai/codex/releases/latest
› 1. Update now (runs `sh -c 'curl -fsSL https://chatgpt.com/codex/install.sh |
     CODEX_NON_INTERACTIVE=1 sh'`)
  2. Skip
  3. Skip until next version
  Press enter to continue
"""

TRUST_GATE = """\
> You are in /Users/vsr/Desktop/scad-launch-demo
  Do you trust the contents of this directory? Working with untrusted contents
  comes with higher risk of prompt injection. Trusting the directory allows
  project-local config, hooks, and exec policies to load.
› 1. Yes, continue
  2. No, quit
  Press enter to continue
"""

CODEX_READY = """\
╭───────────────────────────────────────────╮
│ >_ OpenAI Codex (v0.146.0)                │
│ model:     gpt-5.6-sol   /model to change │
│ directory: /Users/vsr/Desktop/demo        │
╰───────────────────────────────────────────╯
› Find and fix a bug in @filename
  gpt-5.6-sol default · ~/Desktop/demo
"""

KIMI_READY = """\
 K3 thinking: high  ~/Desktop/demo  main
 context: 0% (0/256k)
"""

CLAUDE_READY = """\
 ⏸ manual mode on · ? for shortcuts
"""


class TestReadingAGate:
    """Two gates stand in front of an unattended codex launch, and one of them
    runs `curl | sh` if you press Enter at it. So the pane is read, the options
    are parsed, and the choice is made by LABEL — never by position, never by
    pressing Enter."""

    def test_the_update_gate_is_recognised(self):
        from scad.launch import GATE, pane_state

        assert pane_state(UPDATE_GATE) == GATE

    def test_the_trust_gate_is_recognised(self):
        from scad.launch import GATE, pane_state

        assert pane_state(TRUST_GATE) == GATE

    def test_a_ready_pane_is_not_a_gate(self):
        from scad.launch import READY, pane_state

        for screen in (CODEX_READY, KIMI_READY, CLAUDE_READY):
            assert pane_state(screen) == READY

    def test_an_empty_pane_is_still_starting(self):
        from scad.launch import STARTING, pane_state

        assert pane_state("") == STARTING
        assert pane_state("\n\n   \n") == STARTING

    def test_the_discriminator_is_the_shape_not_the_prose(self):
        """Version drift rewrites the wording; a gate is still a numbered list
        that ends `Press enter to continue`."""
        from scad.launch import GATE, pane_state

        invented = ("  Something entirely new happened\n"
                    "› 1. Yes, continue\n  2. No, quit\n  Press enter to continue\n")
        assert pane_state(invented) == GATE

    def test_the_update_gate_is_skipped_never_accepted(self):
        """Option 1 runs the installer. The only safe answer is Skip."""
        from scad.launch import gate_choice

        assert gate_choice(UPDATE_GATE) == "2"

    def test_the_trust_gate_is_accepted(self):
        from scad.launch import gate_choice

        assert gate_choice(TRUST_GATE) == "1"

    def test_no_option_that_updates_is_ever_chosen(self):
        """The guard that matters most: whatever the numbering, the choice must
        not be the one whose label runs a shell pipe."""
        from scad.launch import gate_choice, gate_options

        reordered = UPDATE_GATE.replace("1. Update now", "9. Update now")
        labels = dict(gate_options(reordered))
        assert "Update now" not in labels[gate_choice(reordered)]

    def test_an_unrecognised_gate_is_not_guessed_at(self):
        """A screen whose options we cannot read is reported, not answered."""
        from scad.launch import gate_choice

        strange = ("  Choose a shell\n› 1. bash\n  2. fish\n"
                   "  Press enter to continue\n")
        assert gate_choice(strange) is None

    def test_options_are_parsed_off_the_highlighted_line_too(self):
        """The selected option carries a `›` marker; it is still an option."""
        from scad.launch import gate_options

        assert ("1", "Yes, continue") in gate_options(TRUST_GATE)

    def test_a_wrapped_option_label_does_not_become_its_own_option(self):
        """The update gate's first label runs onto a second line."""
        from scad.launch import gate_options

        assert [n for n, _ in gate_options(UPDATE_GATE)] == ["1", "2", "3"]


class TestNamingTheTmuxSession:
    def test_the_name_says_agent_and_time(self):
        from scad.launch import tmux_session_name

        assert tmux_session_name("codex", now=time.struct_time(
            (2026, 7, 30, 14, 30, 0, 0, 0, 0))) == "scad-cx-1430"

    def test_each_family_has_its_own_code(self):
        from scad.launch import tmux_session_name

        at = time.struct_time((2026, 7, 30, 14, 30, 0, 0, 0, 0))
        names = {tmux_session_name(a, now=at) for a in ("claude", "codex", "kimi")}
        assert len(names) == 3

    def test_a_name_already_taken_does_not_collide(self):
        """Two launches in the same minute are ordinary; tmux refuses a
        duplicate session name outright."""
        from scad.launch import tmux_session_name

        at = time.struct_time((2026, 7, 30, 14, 30, 0, 0, 0, 0))
        assert tmux_session_name("kimi", taken={"scad-km-1430"}, now=at) == "scad-km-1430-2"
        assert tmux_session_name(
            "kimi", taken={"scad-km-1430", "scad-km-1430-2"}, now=at) == "scad-km-1430-3"


class TestTheKimiDiffer:
    """kimi is the only family whose id has to be waited for, and its index
    line declares its own working directory — so the answer is confirmed
    against a path we chose rather than correlated by time."""

    def _entry(self, uuid, workdir):
        return {"sessionId": f"session_{uuid}",
                "sessionDir": f"/Users/vsr/.kimi-code/sessions/wd_x/session_{uuid}",
                "workDir": workdir}

    def test_one_new_line_for_our_directory_is_the_answer(self, tmp_path):
        from scad.launch import kimi_candidates

        entries = [self._entry("old", "/elsewhere"),
                   self._entry("new", str(tmp_path))]
        assert kimi_candidates(entries, since=1, cwd=tmp_path) == ["new"]

    def test_the_id_is_stored_the_way_the_index_stores_it(self, tmp_path):
        """`kimi_identity_from_path` keys the row on the bare uuid, so a record
        carrying the prefixed form would not join to anything."""
        from scad.launch import kimi_candidates

        got = kimi_candidates([self._entry("abc-123", str(tmp_path))],
                              since=0, cwd=tmp_path)
        assert got == ["abc-123"]

    def test_a_new_line_for_someone_elses_directory_is_not_ours(self, tmp_path):
        """Another kimi starting at the same moment writes a line too."""
        from scad.launch import kimi_candidates

        assert kimi_candidates([self._entry("theirs", "/somewhere/else")],
                               since=0, cwd=tmp_path) == []

    def test_lines_that_were_already_there_are_never_ours(self, tmp_path):
        from scad.launch import kimi_candidates

        entries = [self._entry("before", str(tmp_path))]
        assert kimi_candidates(entries, since=1, cwd=tmp_path) == []

    def test_two_new_lines_are_both_reported(self, tmp_path):
        """Refusing to guess needs both candidates, not the first one."""
        from scad.launch import kimi_candidates

        entries = [self._entry("a", str(tmp_path)), self._entry("b", str(tmp_path))]
        assert kimi_candidates(entries, since=0, cwd=tmp_path) == ["a", "b"]

    def test_the_comparison_survives_a_symlinked_home(self, tmp_path):
        """~/Dropbox is a CloudStorage symlink on this machine and kimi records
        a realpath, so an unresolved comparison never matches."""
        from scad.launch import kimi_candidates

        real = tmp_path / "real"
        real.mkdir()
        link = tmp_path / "link"
        link.symlink_to(real)
        assert kimi_candidates([self._entry("s", str(real))],
                               since=0, cwd=link) == ["s"]

    def test_a_malformed_index_line_costs_only_itself(self, tmp_path):
        from scad.launch import read_kimi_index

        index = tmp_path / "session_index.jsonl"
        index.write_text('{"sessionId":"session_a","workDir":"/x"}\n'
                         "{not json\n"
                         '"a string"\n'
                         '{"sessionId":"session_b","workDir":"/y"}\n')
        assert [e["sessionId"] for e in read_kimi_index(index)] == \
            ["session_a", "session_b"]

    def test_a_missing_index_reads_as_empty(self, tmp_path):
        from scad.launch import read_kimi_index

        assert read_kimi_index(tmp_path / "nope.jsonl") == []


class TestTheCodexRolloutId:
    def test_the_id_comes_off_the_filename(self):
        from scad.launch import rollout_id_from_name

        name = "rollout-2026-07-30T18-44-48-019fb329-c969-7be1-9233-8794b9c1019b.jsonl"
        assert rollout_id_from_name(name) == "019fb329-c969-7be1-9233-8794b9c1019b"

    def test_the_timestamp_is_not_mistaken_for_the_id(self):
        """The filename is `rollout-<timestamp>-<uuid>`, and the timestamp has
        dashes of its own."""
        from scad.launch import rollout_id_from_name

        got = rollout_id_from_name(
            "rollout-2026-07-30T14-25-32-019fb23c-6ce5-7580-b1b0-5a69839a946b.jsonl")
        assert got == "019fb23c-6ce5-7580-b1b0-5a69839a946b"

    def test_anything_else_is_not_a_rollout(self):
        from scad.launch import rollout_id_from_name

        assert rollout_id_from_name("history.jsonl") is None
        assert rollout_id_from_name("rollout-nope.jsonl") is None

    def test_ids_are_gathered_from_the_whole_dated_tree(self, tmp_path):
        from scad.launch import rollout_ids

        day = tmp_path / "2026" / "07" / "30"
        day.mkdir(parents=True)
        for uid in ("019fb329-c969-7be1-9233-8794b9c1019b",
                    "019fb321-6176-7142-add0-05def4a10797"):
            (day / f"rollout-2026-07-30T18-44-48-{uid}.jsonl").write_text("{}\n")
        assert len(rollout_ids(tmp_path)) == 2

    def test_a_missing_sessions_tree_is_an_empty_set(self, tmp_path):
        from scad.launch import rollout_ids

        assert rollout_ids(tmp_path / "nope") == set()


class TestThePrimingTurn:
    """Codex writes no rollout until a turn happens, so one must be sent before
    its id exists. What that turn says is load-bearing."""

    def test_it_is_a_constant_so_a_test_can_assert_it(self):
        from scad.launch import PRIMING_PROMPT

        assert PRIMING_PROMPT == ("This is a scad-launched session. Take no action "
                                  "and read nothing. Reply with exactly: ready.")

    def test_it_names_scad_so_the_session_can_reach_scads_skills(self):
        from scad.launch import PRIMING_PROMPT

        assert "scad" in PRIMING_PROMPT

    def test_it_forbids_action_because_skills_are_live_trigger_words(self):
        """`scad`, `remember` and `recall` install into every family, so naming
        scad in turn 1 makes them live. A fired `remember` writes junk into the
        authored notes tier — the one tier nothing can re-derive."""
        from scad.launch import PRIMING_PROMPT

        assert "Take no action and read nothing" in PRIMING_PROMPT


# --- the stub agent -----------------------------------------------------------
# Stands in for claude / codex / kimi in every launch test. It proves the pane
# is a pty (a TUI needs cbreak, which a pipe refuses), renders the captured
# screens above on cue, records every key it was sent, and writes whichever
# artefact the family under test resolves its id from. No model, no session.

STUB = r'''
import json, os, pathlib, sys, time, tty

d = pathlib.Path(os.environ["SCAD_STUB_DIR"])
d.mkdir(parents=True, exist_ok=True)
(d / "argv.json").write_text(json.dumps(sys.argv[1:]))
try:
    tty.setcbreak(sys.stdin.fileno())          # only a pty allows this
    is_pty = sys.stdin.isatty()
except Exception:
    is_pty = False
(d / "isatty").write_text("tty" if is_pty else "pipe")

keys = []

def note(value):
    keys.append(value)
    (d / "keys.json").write_text(json.dumps(keys))

def emit(text):
    sys.stdout.write("\033[2J\033[H" + text)   # a TUI redraws; so does this
    sys.stdout.flush()

def read_line(echo=False, deaf_until=0.0):
    """Read a line, optionally echoing it and ignoring an early submit.

    `deaf_until` models what kimi actually does: a pasted prompt is ingested
    asynchronously, and a newline arriving before ingestion finishes is
    swallowed. The launcher must therefore wait for the text to appear on
    screen before pressing Enter, not fire both in the same breath.
    """
    buf = ""
    while True:
        ch = sys.stdin.read(1)
        if ch == "":
            return buf
        if ch in ("\r", "\n"):
            if time.monotonic() < deaf_until:
                continue                      # too early — swallowed, as kimi does
            return buf
        buf += ch
        if echo:
            emit("composer\n> " + buf)

for step in json.loads((d / "script.json").read_text()):
    if "print" in step:
        emit(step["print"])
    if step.get("read") == "key":
        note(sys.stdin.read(1))
    elif step.get("read") == "line":
        note(read_line(echo=step.get("echo", False),
                       deaf_until=time.monotonic() + step.get("deaf", 0.0)))
    if "then" in step:
        emit(step["then"])
    for w in step.get("write", []):
        p = pathlib.Path(w["path"])
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a") as fh:
            fh.write(w["text"])

while True:                                     # sit there, like a TUI
    if sys.stdin.read(1) == "":
        break
'''


@pytest.mark.skipif(shutil.which("tmux") is None, reason="needs tmux")
class TestLaunching:
    """Every launch below drives the stub, never an agent.

    tmux lives on a private socket for the whole class. `tmux new-session` on
    the default socket creates a session inside whatever the human has open,
    and `kill-server` there ends their day.
    """

    @pytest.fixture(autouse=True)
    def _fast_and_isolated(self, tmp_path, monkeypatch):
        import scad.launch as launch_mod

        monkeypatch.setenv("SCAD_TMUX_SOCKET", TEST_SOCKET)
        # Real waits, short deadlines: the stub is up in milliseconds, so a
        # test that hits a deadline has found a bug rather than a slow machine.
        monkeypatch.setattr(launch_mod, "POLL_INTERVAL", 0.05)
        monkeypatch.setattr(launch_mod, "START_DEADLINE", 2.0)
        monkeypatch.setattr(launch_mod, "KIMI_DEADLINE", 1.0)
        monkeypatch.setattr(launch_mod, "ROLLOUT_DEADLINE", 1.0)
        home = tmp_path / "home"
        home.mkdir()
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
        self.home = home
        yield
        subprocess.run(["tmux", "-L", TEST_SOCKET, "kill-server"],
                       capture_output=True)

    def _stub(self, tmp_path, script) -> tuple[str, Path]:
        """(command to launch, directory the stub reports into)."""
        import sys as _sys

        stub_dir = tmp_path / "stub"
        stub_dir.mkdir(parents=True, exist_ok=True)
        (stub_dir / "script.json").write_text(json.dumps(script))
        path = tmp_path / "stub_agent.py"
        path.write_text(STUB)
        # The stub's directory travels in the command, not in the environment:
        # tmux hands a new session the SERVER's environment, which belongs to
        # whichever test started the server first.
        return f"SCAD_STUB_DIR={stub_dir} {_sys.executable} {path}", stub_dir

    def _wait_for(self, path, seconds=8.0):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if path.exists():
                return True
            time.sleep(0.05)
        return False

    def _panes(self):
        result = subprocess.run(
            ["tmux", "-L", TEST_SOCKET, "list-panes", "-a", "-F",
             "#{session_name}:#{window_index}.#{pane_index}"],
            capture_output=True, text=True)
        return set(result.stdout.split())

    def _kimi_line(self, uuid, workdir):
        return json.dumps({"sessionId": f"session_{uuid}",
                           "sessionDir": f"/k/session_{uuid}",
                           "workDir": str(workdir)}) + "\n"

    # --- the pty, which is the whole reason tmux is here ---------------------

    def test_the_agent_gets_a_pty(self, tmp_path):
        """Not a detail: a non-TTY stdout alone stamps a Claude session
        `entrypoint: sdk-cli`, and its own /resume picker drops those. The
        launch channel and the picker-visibility fix are one decision."""
        from scad.launch import launch

        binary, stub = self._stub(tmp_path, [{"print": CLAUDE_READY}])
        launch("claude", tmp_path, binary=binary)

        assert self._wait_for(stub / "isatty")
        assert (stub / "isatty").read_text() == "tty"

    def test_the_recorded_target_is_a_pane_that_exists(self, tmp_path):
        from scad.launch import launch

        binary, _ = self._stub(tmp_path, [{"print": CLAUDE_READY}])
        record = launch("claude", tmp_path, binary=binary)

        assert record["tmux"] in self._panes()

    # --- claude: the id is minted --------------------------------------------

    def test_claude_is_launched_with_the_id_scad_chose(self, tmp_path):
        from scad.launch import MINTED, launch

        binary, stub = self._stub(tmp_path, [{"print": CLAUDE_READY}])
        record = launch("claude", tmp_path, binary=binary)

        assert self._wait_for(stub / "argv.json")
        assert json.loads((stub / "argv.json").read_text()) == \
            ["--session-id", record["session_id"]]
        assert record["provenance"] == MINTED

    def test_a_prompt_is_typed_then_submitted_separately(self, tmp_path):
        """`send-keys` without `-l` drops the text silently, so the text and
        the Enter are always two calls."""
        from scad.launch import launch

        binary, stub = self._stub(
            tmp_path, [{"print": CLAUDE_READY, "read": "line"}])
        launch("claude", tmp_path, binary=binary, prompt="do the thing")

        assert self._wait_for(stub / "keys.json")
        assert json.loads((stub / "keys.json").read_text()) == ["do the thing"]

    def test_a_tui_that_ingests_slowly_still_gets_its_turn(self, tmp_path):
        """Regression, found by launching kimi for real on 2026-07-30.

        The pane sat at `context: 0% (0/256k)` with the prompt still in the
        composer: the text had landed and the Enter had been swallowed. kimi
        ingests a pasted prompt asynchronously and drops a newline that arrives
        mid-ingest, so typing and submitting in the same breath loses the
        turn — silently, because the launch itself succeeds. kimi's id does not
        depend on the turn, so the record was written and the run looked clean.

        The stub reproduces exactly that: it echoes what it reads and is deaf
        to Enter for the first 600ms. Nothing the suite had could catch this —
        every other stub reads stdin directly, so no amount of stub testing
        exercised a *rendering* race.
        """
        from scad.launch import launch

        binary, stub = self._stub(tmp_path, [
            {"print": CLAUDE_READY, "read": "line", "echo": True, "deaf": 0.6}])
        launch("claude", tmp_path, binary=binary, prompt="port the parser")

        assert self._wait_for(stub / "keys.json")
        assert json.loads((stub / "keys.json").read_text()) == ["port the parser"]

    def test_with_no_prompt_nothing_is_typed_at_all(self, tmp_path):
        """scad sends the first turn and then the session belongs to the human.
        Claude needs no priming — its id exists before launch."""
        from scad.launch import launch

        binary, stub = self._stub(tmp_path, [{"print": CLAUDE_READY}])
        launch("claude", tmp_path, binary=binary)
        time.sleep(0.5)

        assert not (stub / "keys.json").exists()

    # --- kimi: the id is read back and confirmed ------------------------------

    def test_kimi_takes_the_id_off_its_own_index_line(self, tmp_path):
        from scad.launch import TUI_NATIVE, launch

        index = self.home / ".kimi-code" / "session_index.jsonl"
        binary, _ = self._stub(tmp_path, [
            {"write": [{"path": str(index),
                        "text": self._kimi_line("aaa-111", tmp_path)}],
             "print": KIMI_READY}])
        record = launch("kimi", tmp_path, binary=binary)

        assert record["session_id"] == "aaa-111"      # bare, as the index keys it
        assert record["provenance"] == TUI_NATIVE

    def test_a_line_for_another_directory_is_not_taken_as_ours(self, tmp_path):
        from scad.launch import UNRESOLVED, launch

        index = self.home / ".kimi-code" / "session_index.jsonl"
        binary, _ = self._stub(tmp_path, [
            {"write": [{"path": str(index),
                        "text": self._kimi_line("theirs", "/somewhere/else")}],
             "print": KIMI_READY}])
        record = launch("kimi", tmp_path, binary=binary)

        assert record["session_id"] is None
        assert record["provenance"] == UNRESOLVED

    def test_no_index_line_leaves_the_pane_running_and_says_so(self, tmp_path):
        """Launch succeeded, id unknown. The session is real — losing the pane
        as well would turn a findable session into a lost one."""
        from scad.launch import UNRESOLVED, launch

        binary, _ = self._stub(tmp_path, [{"print": KIMI_READY}])
        record = launch("kimi", tmp_path, binary=binary)

        assert record["provenance"] == UNRESOLVED
        assert record["tmux"] in self._panes()
        assert "problem" in record

    def test_two_index_lines_refuse_to_guess(self, tmp_path):
        """Same discipline as `--current` refusing when an agent's own variable
        is absent: report both, pick neither."""
        from scad.launch import UNRESOLVED, launch

        index = self.home / ".kimi-code" / "session_index.jsonl"
        binary, _ = self._stub(tmp_path, [
            {"write": [{"path": str(index),
                        "text": self._kimi_line("first", tmp_path)
                                + self._kimi_line("second", tmp_path)}],
             "print": KIMI_READY}])
        record = launch("kimi", tmp_path, binary=binary)

        assert record["session_id"] is None
        assert record["provenance"] == UNRESOLVED
        assert record["candidates"] == ["first", "second"]

    # --- codex: gates, then a turn, then the rollout --------------------------

    def _codex_script(self, rollout, prompt_step=None):
        return [
            {"print": UPDATE_GATE, "read": "key", "then": TRUST_GATE},
            {"read": "key", "then": CODEX_READY},
            {"read": "line",
             "write": [{"path": str(rollout), "text": "{}\n"}]},
        ]

    def _rollout(self, uuid):
        return (self.home / ".codex" / "sessions" / "2026" / "07" / "30" /
                f"rollout-2026-07-30T18-44-48-{uuid}.jsonl")

    def test_codex_clears_both_gates_by_label_and_takes_the_rollout_id(self, tmp_path):
        from scad.launch import PRIMING_PROMPT, TUI_NATIVE, launch

        uuid = "019fb329-c969-7be1-9233-8794b9c1019b"
        binary, stub = self._stub(tmp_path, self._codex_script(self._rollout(uuid)))
        record = launch("codex", tmp_path, binary=binary)

        assert record["session_id"] == uuid
        assert record["provenance"] == TUI_NATIVE
        # 2 skips the update (option 1 runs `curl | sh`); 1 accepts trust.
        assert json.loads((stub / "keys.json").read_text()) == \
            ["2", "1", PRIMING_PROMPT]

    def test_a_supplied_prompt_is_the_first_turn_not_a_synthetic_one(self, tmp_path):
        from scad.launch import launch

        uuid = "019fb329-c969-7be1-9233-8794b9c1019b"
        binary, stub = self._stub(tmp_path, self._codex_script(self._rollout(uuid)))
        launch("codex", tmp_path, binary=binary, prompt="port the parser")

        assert json.loads((stub / "keys.json").read_text())[-1] == "port the parser"

    def test_an_unreadable_gate_is_reported_and_never_answered(self, tmp_path):
        """The update gate's highlighted default runs an installer, so a screen
        whose options we cannot read gets no keystroke at all."""
        from scad.launch import UNRESOLVED, launch

        strange = ("  Something new is being asked\n› 1. Frobnicate\n"
                   "  2. Wibble\n  Press enter to continue\n")
        binary, stub = self._stub(tmp_path, [{"print": strange}])
        record = launch("codex", tmp_path, binary=binary)

        assert record["provenance"] == UNRESOLVED
        assert not (stub / "keys.json").exists()
        assert "Frobnicate" in record["problem"]

    def test_no_rollout_after_the_turn_is_unresolved_not_a_guess(self, tmp_path):
        from scad.launch import UNRESOLVED, launch

        binary, _ = self._stub(tmp_path, [
            {"print": CODEX_READY, "read": "line"}])
        record = launch("codex", tmp_path, binary=binary)

        assert record["session_id"] is None
        assert record["provenance"] == UNRESOLVED
        assert record["tmux"] in self._panes()

    def test_a_rollout_that_was_already_there_is_not_mistaken_for_ours(self, tmp_path):
        from scad.launch import UNRESOLVED, launch

        old = self._rollout("019fb23c-6ce5-7580-b1b0-5a69839a946b")
        old.parent.mkdir(parents=True)
        old.write_text("{}\n")
        binary, _ = self._stub(tmp_path, [{"print": CODEX_READY, "read": "line"}])
        record = launch("codex", tmp_path, binary=binary)

        assert record["provenance"] == UNRESOLVED

    # --- the record -----------------------------------------------------------

    def test_the_record_is_on_disk_before_the_call_returns(self, tmp_path):
        """A caller must never observe a launch with no record."""
        from scad.launch import launch, read_record

        binary, _ = self._stub(tmp_path, [{"print": CLAUDE_READY}])
        record = launch("claude", tmp_path, binary=binary)

        assert read_record(record["session_id"]) == record

    def test_the_record_carries_the_resume_command(self, tmp_path):
        from scad.launch import launch

        binary, _ = self._stub(tmp_path, [{"print": CLAUDE_READY}])
        record = launch("claude", tmp_path, binary=binary)

        assert record["resume"] == \
            f"cd {tmp_path} && claude --resume {record['session_id']}"

    def test_an_unresolved_launch_is_still_recorded_somewhere_readable(self, tmp_path):
        from scad.launch import launch, launches_root

        binary, _ = self._stub(tmp_path, [{"print": KIMI_READY}])
        record = launch("kimi", tmp_path, binary=binary)

        written = list(launches_root().glob("*.json"))
        assert len(written) == 1
        assert json.loads(written[0].read_text())["tmux"] == record["tmux"]


class TestRefusingToLaunch:
    def test_no_tmux_refuses_rather_than_falling_back(self, tmp_path, monkeypatch):
        """The deliberate exception to degrade-never-raise. A non-pty launch
        silently produces the `sdk-cli` stamp, and a degraded result that looks
        fine and is wrong is worse than no result."""
        import scad.launch as launch_mod

        monkeypatch.setattr(launch_mod, "tmux_available", lambda: False)
        with pytest.raises(launch_mod.LaunchError) as exc:
            launch_mod.launch("claude", tmp_path)
        assert "tmux" in str(exc.value)

    def test_an_unknown_agent_is_refused(self, tmp_path):
        from scad.launch import LaunchError, launch

        with pytest.raises(LaunchError):
            launch("pi", tmp_path)

    def test_a_cwd_that_is_not_a_directory_is_refused(self, tmp_path):
        from scad.launch import LaunchError, launch

        with pytest.raises(LaunchError):
            launch("claude", tmp_path / "nope")


TRUST_DIALOG = """ Accessing workspace:

 /Users/vsr/code/docspatial

 Quick safety check: Is this a project you created or one you trust?

 > No, exit
   Yes, I trust this folder

 Enter to confirm . Esc to cancel"""


class TestAGateScadMayNotAnswer:
    """The folder-trust dialog killed a session by being unrecognised.

    It is an ARROW menu, so the numbered-option matcher found nothing, and it
    says "Enter to confirm" rather than "Press enter to continue", so the gate
    marker missed too. The pane read as READY, the priming turn was typed into
    it, and the Enter that submits answered the highlighted default -- `No,
    exit`. Claude quit, no transcript was written, and the session was missing
    from /resume as well.
    """

    def test_the_trust_dialog_is_not_mistaken_for_a_ready_pane(self):
        from scad.launch import pane_state, GATE
        assert pane_state(TRUST_DIALOG) == GATE

    def test_it_is_reported_as_something_only_a_human_can_answer(self):
        from scad.launch import blocking_gate
        reason = blocking_gate(TRUST_DIALOG)
        assert reason and "trust" in reason.lower()

    def test_scad_never_picks_an_option_in_it(self):
        # gate_choice exists to clear NUMBERED gates. It must find nothing here:
        # choosing on the human's behalf is the whole thing being refused.
        from scad.launch import gate_choice
        assert gate_choice(TRUST_DIALOG) is None

    def test_an_ordinary_pane_is_still_ready(self):
        from scad.launch import pane_state, READY, blocking_gate
        ordinary = "? for shortcuts\n> "
        assert pane_state(ordinary) == READY
        assert blocking_gate(ordinary) is None

    def test_a_numbered_gate_is_still_cleared(self):
        # The refusal must not swallow the gates scad legitimately answers.
        from scad.launch import gate_choice
        update = ("A new version is available\n"
                  "  1. Update now\n"
                  "› 2. Skip\n"
                  "Press enter to continue")
        assert gate_choice(update) == "2"
