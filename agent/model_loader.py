"""HF Hub model artifact loader (AGENT-06).

Local-first fallback pattern (RESEARCH Example 6):
  1. snapshot_download(local_files_only=True) — succeeds if cached.
  2. On LocalEntryNotFoundError: snapshot_download(local_files_only=False) — first run.

This is the offline-first contract — once the cache is warm, the agent does
NOT call out to the network on subsequent diagnoses. Privacy posture +
graceful degradation on flaky connectivity in one stroke.

Pitfall 7 — MODEL_REVISION is pinned in code; bumping it = new agent release.

huggingface_hub moved LocalEntryNotFoundError between
``huggingface_hub.utils`` (<0.25) and ``huggingface_hub.errors`` (>=0.25);
import defensively so the agent works against either version.
"""
from __future__ import annotations

from pathlib import Path

import platformdirs
from huggingface_hub import snapshot_download

try:
    from huggingface_hub.errors import LocalEntryNotFoundError
except ImportError:  # huggingface_hub < 0.25 keeps it under .utils
    from huggingface_hub.utils import LocalEntryNotFoundError

MODEL_REPO = "WolfDavid/ai-internet-diagnostic-model"
MODEL_REVISION = "v1.0.0"


def _cache_dir() -> Path:
    p = Path(platformdirs.user_cache_dir("wifi-diag")) / "models"
    p.mkdir(parents=True, exist_ok=True)
    return p


def fetch_or_use_cached() -> Path:
    """Return the local snapshot directory for the pinned revision.

    Tries the cache first; only falls back to a network call on
    LocalEntryNotFoundError. This is what makes AGENT-05 offline-first.
    """
    cache = _cache_dir()
    try:
        snap = snapshot_download(
            repo_id=MODEL_REPO,
            revision=MODEL_REVISION,
            cache_dir=str(cache),
            local_files_only=True,
        )
        return Path(snap)
    except LocalEntryNotFoundError:
        # First run, or revision bumped — go to network.
        snap = snapshot_download(
            repo_id=MODEL_REPO,
            revision=MODEL_REVISION,
            cache_dir=str(cache),
            local_files_only=False,
        )
        return Path(snap)


def path_for(filename: str) -> Path:
    """Return absolute path to an artifact within the snapshot's ``artifacts/`` dir."""
    snap = fetch_or_use_cached()
    return snap / "artifacts" / filename
