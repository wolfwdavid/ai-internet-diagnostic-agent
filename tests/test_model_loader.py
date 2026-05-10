"""Model loader tests (AGENT-06) — mocked snapshot_download.

Verifies:
- MODEL_REVISION pinned to v1.0.0 (Pitfall 7 — HF Hub revision drift).
- Local-first fallback: snapshot_download(local_files_only=True) called FIRST,
  network call only on LocalEntryNotFoundError.
- path_for() returns artifacts/<name> under the snapshot directory.
"""
from __future__ import annotations

from pathlib import Path


def test_revision_pinned_to_v1_0_0():
    from agent.model_loader import MODEL_REPO, MODEL_REVISION

    assert MODEL_REVISION == "v1.0.0", (
        f"Pitfall 7 — MODEL_REVISION drifted: {MODEL_REVISION!r}"
    )
    assert MODEL_REPO == "WolfDavid/ai-internet-diagnostic-model"


def test_local_files_only_first_then_network_fallback(tmp_state_dir, mocker, tmp_path):
    # Import LocalEntryNotFoundError defensively (moved between hub versions).
    try:
        from huggingface_hub.errors import LocalEntryNotFoundError
    except ImportError:
        from huggingface_hub.utils import LocalEntryNotFoundError

    snap = tmp_path / "snap"
    snap.mkdir()
    side_effects = [LocalEntryNotFoundError("not in cache"), str(snap)]
    mock_dl = mocker.patch(
        "agent.model_loader.snapshot_download", side_effect=side_effects
    )

    from agent.model_loader import fetch_or_use_cached

    result = fetch_or_use_cached()
    assert isinstance(result, Path)
    assert result == Path(str(snap))
    # Two calls: first with local_files_only=True, second with local_files_only=False
    assert mock_dl.call_count == 2
    first_kwargs = mock_dl.call_args_list[0].kwargs
    second_kwargs = mock_dl.call_args_list[1].kwargs
    assert first_kwargs["local_files_only"] is True
    assert second_kwargs["local_files_only"] is False
    assert first_kwargs["revision"] == "v1.0.0"
    assert second_kwargs["revision"] == "v1.0.0"


def test_local_files_only_succeeds_no_network_call(tmp_state_dir, mocker, tmp_path):
    snap = tmp_path / "snap"
    snap.mkdir()
    mock_dl = mocker.patch(
        "agent.model_loader.snapshot_download", return_value=str(snap)
    )

    from agent.model_loader import fetch_or_use_cached

    result = fetch_or_use_cached()
    assert result == Path(str(snap))
    # Only one call: the local_files_only=True path succeeded — no network.
    assert mock_dl.call_count == 1


def test_path_for_returns_artifacts_subdir(tmp_state_dir, mocker, tmp_path):
    snap = tmp_path / "snap"
    (snap / "artifacts").mkdir(parents=True)
    mocker.patch("agent.model_loader.snapshot_download", return_value=str(snap))

    from agent.model_loader import path_for

    p = path_for("classifier.joblib")
    # Two independent assertions — both must hold for the contract.
    assert "artifacts" in str(p), (
        f"path_for must place artifact under an 'artifacts' subdir; got {p}"
    )
    assert p.name == "classifier.joblib", (
        f"path_for filename mismatch; got {p.name}"
    )
