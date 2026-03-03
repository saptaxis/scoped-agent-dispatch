"""Tests for prompt file parsing."""
import pytest

from scad.prompts import parse_prompt_file


class TestParsePromptFile:
    """Tests for ---delimited prompt file parsing."""

    def test_single_prompt(self, tmp_path):
        """Single prompt without delimiter."""
        f = tmp_path / "prompts.txt"
        f.write_text("Analyze document A\n")
        prompts = parse_prompt_file(f)
        assert prompts == ["Analyze document A"]

    def test_multiple_prompts(self, tmp_path):
        """Multiple prompts separated by ---."""
        f = tmp_path / "prompts.txt"
        f.write_text(
            "Analyze document A\n"
            "---\n"
            "Analyze document B\n"
            "---\n"
            "Analyze document C\n"
        )
        prompts = parse_prompt_file(f)
        assert len(prompts) == 3
        assert prompts[0] == "Analyze document A"
        assert prompts[1] == "Analyze document B"
        assert prompts[2] == "Analyze document C"

    def test_multiline_prompts(self, tmp_path):
        """Prompts can span multiple lines."""
        f = tmp_path / "prompts.txt"
        f.write_text(
            "First line\nSecond line\nThird line\n"
            "---\n"
            "Another prompt\n"
        )
        prompts = parse_prompt_file(f)
        assert len(prompts) == 2
        assert "First line\nSecond line\nThird line" == prompts[0]

    def test_strips_whitespace(self, tmp_path):
        """Leading/trailing whitespace is stripped from each prompt."""
        f = tmp_path / "prompts.txt"
        f.write_text("\n  Prompt with spaces  \n---\n\nAnother prompt\n\n")
        prompts = parse_prompt_file(f)
        assert prompts[0] == "Prompt with spaces"
        assert prompts[1] == "Another prompt"

    def test_empty_blocks_skipped(self, tmp_path):
        """Empty blocks between --- are skipped."""
        f = tmp_path / "prompts.txt"
        f.write_text("Prompt A\n---\n---\nPrompt B\n")
        prompts = parse_prompt_file(f)
        assert len(prompts) == 2

    def test_empty_file_returns_empty(self, tmp_path):
        """Empty file returns empty list."""
        f = tmp_path / "prompts.txt"
        f.write_text("")
        prompts = parse_prompt_file(f)
        assert prompts == []

    def test_file_not_found_raises(self, tmp_path):
        """Non-existent file raises FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            parse_prompt_file(tmp_path / "missing.txt")
