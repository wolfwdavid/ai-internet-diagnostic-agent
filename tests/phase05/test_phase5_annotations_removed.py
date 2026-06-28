"""Gotcha 10 — ``[Phase 5]`` annotation strings must be gone post-Phase-5.

Phase 4 deliberately placed ``[Phase 5]`` markers in the consent prompt and
PRIVACY.md as removable seeds. This test asserts the cleanup ran.
"""

from __future__ import annotations

import re
from pathlib import Path

AGENT_ROOT = Path(__file__).resolve().parents[2]
AGENT_PKG = AGENT_ROOT / "agent"


def test_no_phase5_annotations_in_agent_source():
    """Grep scope = ``agent/`` package (production source), per plan 05-02 ACs.

    Test files under ``tests/`` are out of scope — they may legitimately
    reference the literal ``[Phase 5]`` string when verifying its absence
    elsewhere.
    """
    pattern = re.compile(r"\[Phase 5\]")
    bad: list[str] = []
    for p in AGENT_PKG.rglob("*.py"):
        parts = set(p.parts)
        if ".venv" in parts or "__pycache__" in parts or "node_modules" in parts:
            continue
        text = p.read_text(encoding="utf-8", errors="ignore")
        if pattern.search(text):
            bad.append(str(p.relative_to(AGENT_ROOT)))
    assert not bad, f"Found [Phase 5] annotations in agent/: {bad}"


def test_no_phase5_annotations_in_privacy_md():
    p = AGENT_ROOT / "PRIVACY.md"
    if not p.exists():
        return
    text = p.read_text(encoding="utf-8")
    assert "[Phase 5]" not in text, "PRIVACY.md still contains [Phase 5] annotation"
