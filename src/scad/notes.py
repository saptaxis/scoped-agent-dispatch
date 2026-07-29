"""The notes store — `/remember` capture, on disk.

The one tier that is never rederivable. Turns can be re-extracted while raw
survives and sessions can be recomputed from turns, but a note is self-report:
if the file is lost, nothing anywhere can reconstruct it. So the file is truth
and the `notes` table is only an index over it, appending is the only write,
and nothing in here ever deletes, truncates or rewrites.

Path: `~/.scad/notes/<agent>/<session-uuid>.jsonl`

Session-keyed and project-free on purpose. A project is a *computed* property
of a cwd — redefining what counts as a project root is a config edit, and any
durable path containing `<project>` would orphan its files the moment that
happened. Sharding by agent instead keeps the one property of a note that
cannot be recomputed.
"""

import unicodedata
from pathlib import Path

from scad.config import get_scad_home

# Claude Code truncates an encoded project directory at 200 characters and
# appends a hash of the full path. Both values are read from the shipped binary,
# not guessed: `iRt=200`, and `o0h(e) = Math.abs(art(e)).toString(36)`.
PROJECT_DIR_CAP = 200

_B36 = "0123456789abcdefghijklmnopqrstuvwxyz"


def _js_string_hash(text: str) -> int:
    """Claude Code's `art(e)`: the classic Java 31-multiplier hash, 32-bit signed.

        function art(e){let t=0;for(let r=0;r<e.length;r++)
                        t=(t<<5)-t+e.charCodeAt(r)|0;return t}

    `|0` truncates to a signed 32-bit int on every iteration, so overflow wraps
    rather than growing — Python ints do not, hence the explicit mask.
    """
    h = 0
    for ch in text:
        h = ((h << 5) - h + ord(ch)) & 0xFFFFFFFF
    return h - 0x100000000 if h >= 0x80000000 else h


def _base36(value: int) -> str:
    if value == 0:
        return "0"
    out = ""
    while value:
        value, rem = divmod(value, 36)
        out = _B36[rem] + out
    return out


def encode_cwd(cwd: str) -> str:
    """Encode a working directory the way Claude Code names `projects/<dir>`.

    Every non-alphanumeric character becomes a dash — not only `/`. Verified
    two ways: the encoder in the 2.1.220 binary is
    `e.replace(/[^a-zA-Z0-9]/g,"-")`, and a real session started in
    `/private/tmp/scad enc.test_dir-1` landed in
    `~/.claude/projects/-private-tmp-scad-enc-test-dir-1`.

    The mapping is therefore lossy and ONE-WAY: `a.b`, `a_b` and `a-b` all
    encode to `a-b`. Never try to decode a directory name back to a path — read
    the `cwd` field out of a record instead.
    """
    text = unicodedata.normalize("NFC", cwd)
    encoded = "".join(c if c.isascii() and c.isalnum() else "-" for c in text)
    if len(encoded) <= PROJECT_DIR_CAP:
        return encoded
    return f"{encoded[:PROJECT_DIR_CAP]}-{_base36(abs(_js_string_hash(text)))}"


def notes_root() -> Path:
    """`~/.scad/notes` — durable, never pruned, not part of the archive."""
    return get_scad_home() / "notes"


def note_path(session_id: str, agent: str = "claude") -> Path:
    return notes_root() / agent / f"{session_id}.jsonl"
