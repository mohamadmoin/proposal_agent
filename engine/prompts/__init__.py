"""Prompt files: one Markdown file per step, with named placeholders such as {{title}}.

Why files and not Python strings: prompts are content. A clinician can read and improve them in a pull request
without touching code (docs/DESIGN.md, D7).
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

PROMPTS_DIR = Path(__file__).resolve().parent
PLACEHOLDER = re.compile(r"\{\{\s*([a-z_][a-z0-9_]*)\s*\}\}")


class PromptError(KeyError):
    """A placeholder in a prompt file has no value."""


@lru_cache(maxsize=64)
def load(name: str) -> str:
    """The raw text of engine/prompts/<name>.md."""
    return (PROMPTS_DIR / f"{name}.md").read_text(encoding="utf-8")


def placeholders(name: str) -> set[str]:
    """The placeholder names a prompt file uses."""
    return set(PLACEHOLDER.findall(load(name)))


def render(name: str, **values: object) -> str:
    """Fill every {{placeholder}}. A missing value is an error, so a prompt never goes out half-filled."""

    def replace(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in values:
            raise PromptError(f"prompt {name!r} needs a value for {{{{{key}}}}}")
        return str(values[key])

    return PLACEHOLDER.sub(replace, load(name)).strip()
