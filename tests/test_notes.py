"""Tests for the notes store — the one tier that is never rederivable."""

import json
from pathlib import Path

import pytest

from scad.notes import PROJECT_DIR_CAP, encode_cwd


class TestEncodeCwd:
    """Claude Code's projects/<encoded-cwd> naming.

    Verified against the shipped binary (2.1.220) and against real directory
    names on this machine. The function there is:

        function RA(e){let t=e.replace(/[^a-zA-Z0-9]/g,"-");
                       if(t.length<=200)return t;
                       return `${t.slice(0,200)}-${o0h(e)}`}

    so EVERY non-alphanumeric byte collapses to a dash — not just `/`. That is
    a lossy, one-way mapping: `a.b`, `a_b` and `a-b` all encode to `a-b`, which
    is why nothing here ever decodes.
    """

    def test_slashes_become_dashes_with_a_leading_one(self):
        assert encode_cwd("/Users/vsr/code") == "-Users-vsr-code"

    def test_dots_underscores_and_spaces_also_become_dashes(self):
        # Empirically confirmed: a real session in "/private/tmp/scad enc.test_dir-1"
        # landed in ~/.claude/projects/-private-tmp-scad-enc-test-dir-1
        assert encode_cwd("/private/tmp/scad enc.test_dir-1") == \
            "-private-tmp-scad-enc-test-dir-1"

    def test_existing_hyphens_survive_unchanged(self):
        assert encode_cwd("/Users/vsr/code/scoped-agent-dispatch") == \
            "-Users-vsr-code-scoped-agent-dispatch"

    def test_a_long_path_is_truncated_and_hash_suffixed(self):
        raw = "/" + "/".join(f"segment{i:03d}" for i in range(40))
        out = encode_cwd(raw)
        assert len(out) > PROJECT_DIR_CAP
        head, _, suffix = out.rpartition("-")
        assert head == "-".join(raw.split("/"))[:PROJECT_DIR_CAP].rstrip("-") or \
            head.startswith("-segment000")
        assert suffix.isalnum()

    def test_hash_suffix_matches_claude_codes_string_hash(self):
        # (t << 5) - t + charCode | 0, then Math.abs(...).toString(36)
        raw = "/" + "x" * 260
        out = encode_cwd(raw)
        h = 0
        for ch in raw:
            h = ((h << 5) - h + ord(ch)) & 0xFFFFFFFF
            if h >= 0x80000000:
                h -= 0x100000000
        digits = "0123456789abcdefghijklmnopqrstuvwxyz"
        n, expect = abs(h), ""
        while n:
            n, r = divmod(n, 36)
            expect = digits[r] + expect
        assert out.endswith("-" + (expect or "0"))
