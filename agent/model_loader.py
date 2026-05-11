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
    from huggingface_hub.errors import (
        LocalEntryNotFoundError,
        RepositoryNotFoundError,
        RevisionNotFoundError,
    )
except ImportError:  # huggingface_hub < 0.25 keeps these under .utils
    from huggingface_hub.utils import (  # type: ignore[no-redef]
        LocalEntryNotFoundError,
        RepositoryNotFoundError,
        RevisionNotFoundError,
    )

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
        # First run, or revision bumped — go to network. If the repo or tag
        # does not exist yet (e.g., a fresh agent install before the model
        # repo has been published, or an agent ahead of the model release),
        # raise an instructive SystemExit instead of letting the raw
        # huggingface_hub exception bubble up as an opaque traceback.
        #
        # GAP-2 closure (Phase 6 plan 06-02) — see
        # .planning/v1.0.0-MILESTONE-AUDIT.md::GAP-2 for the audit detail.
        try:
            snap = snapshot_download(
                repo_id=MODEL_REPO,
                revision=MODEL_REVISION,
                cache_dir=str(cache),
                local_files_only=False,
            )
        except RepositoryNotFoundError as e:
            raise SystemExit(
                f"agent: HF Hub model repo '{MODEL_REPO}' not found (404). "
                f"This usually means the model has not been published yet. "
                f"Workarounds: (1) re-run with `--consent local` and a "
                f"pre-warmed cache, or (2) wait for the repo to be "
                f"published and re-run `agent diagnose`. See the "
                f"ai-internet-diagnostic-agent README for install steps."
            ) from e
        except RevisionNotFoundError as e:
            raise SystemExit(
                f"agent: HF Hub repo '{MODEL_REPO}' exists but revision "
                f"'{MODEL_REVISION}' is not tagged. This usually means the "
                f"model repo has not pushed the v1.0.0 release tag yet. "
                f"Workarounds: (1) run `agent doctor` to see your cache "
                f"state, (2) use `agent diagnose --consent local` if you "
                f"have a pre-warmed cache, or (3) wait for the tag to be "
                f"pushed and re-run."
            ) from e
        return Path(snap)


def path_for(filename: str) -> Path:
    """Return absolute path to an artifact within the snapshot's ``artifacts/`` dir."""
    snap = fetch_or_use_cached()
    return snap / "artifacts" / filename
