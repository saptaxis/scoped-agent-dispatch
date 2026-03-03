"""Prompt file parsing for batch operations."""

from pathlib import Path


def parse_prompt_file(path: Path) -> list[str]:
    """Parse a ---delimited prompt file.

    Each block separated by a line containing only '---' is one prompt.
    Empty blocks are skipped. Whitespace is stripped.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Prompt file not found: {path}")

    content = path.read_text()
    if not content.strip():
        return []

    blocks = content.split("\n---\n")
    prompts = []
    for block in blocks:
        stripped = block.strip()
        if stripped:
            prompts.append(stripped)
    return prompts
