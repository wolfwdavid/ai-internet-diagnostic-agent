"""Model loader tests (AGENT-06) — mocked snapshot_download.

Verifies:
- MODEL_REVISION pinned to v1.0.0 (Pitfall 7 — HF Hub revision drift).
- Local-first fallback: snapshot_download(local_files_only=True) called FIRST,
  network call only on LocalEntryNotFoundError.
- path_for() returns artifacts/<name> under the snapshot directory.
- GAP-2 closure (Phase 6 plan 06-02): fetch_or_use_cached raises an
  instructive SystemExit (not raw RepositoryNotFoundError /
  RevisionNotFoundError) when the HF Hub model repo or tag is missing.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from agent import model_loader
from agent.model_loader import (
    LocalEntryNotFoundError,
    RepositoryNotFoundError,
    RevisionNotFoundError,
    fetch_or_use_cached,
)


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


# ---------------------------------------------------------------------------
# GAP-2 closure (Phase 6 plan 06-02) — HF Hub 404 graceful degradation.
#
# fetch_or_use_cached() must raise SystemExit with an instructive message
# (NOT a raw RepositoryNotFoundError / RevisionNotFoundError traceback) when
# the network-fallback snapshot_download fails because the repo or pinned
# revision does not exist on the Hub.
# ---------------------------------------------------------------------------


def _make_snapshot_download_mock(
    first_exc: BaseException, second_exc: BaseException | None
):
    """Return a mock callable that raises ``first_exc`` on the first call and
    ``second_exc`` on the second call (or returns a fake path if
    ``second_exc`` is None)."""
    calls = {"n": 0}

    def _mock(**kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise first_exc
        if second_exc is not None:
            raise second_exc
        return "/tmp/fake-snapshot"

    _mock.calls = calls  # type: ignore[attr-defined]
    return _mock


def test_fetch_or_use_cached_repo_not_found(
    tmp_state_dir, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When HF Hub returns 404 on the repo, fetch_or_use_cached raises
    SystemExit with an instructive message naming the repo."""
    mock_dl = _make_snapshot_download_mock(
        LocalEntryNotFoundError("cache miss"),
        RepositoryNotFoundError("repo missing on Hub"),
    )
    monkeypatch.setattr(model_loader, "snapshot_download", mock_dl)

    with pytest.raises(SystemExit) as exc_info:
        fetch_or_use_cached()

    msg = str(exc_info.value)
    assert "WolfDavid/ai-internet-diagnostic-model" in msg, (
        f"SystemExit msg missing repo name: {msg!r}"
    )
    assert "not found" in msg.lower(), (
        f"SystemExit msg missing 'not found' guidance: {msg!r}"
    )


def test_fetch_or_use_cached_revision_not_found(
    tmp_state_dir, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When HF Hub returns 404 on the revision tag, fetch_or_use_cached
    raises SystemExit with an instructive message naming the revision."""
    mock_dl = _make_snapshot_download_mock(
        LocalEntryNotFoundError("cache miss"),
        RevisionNotFoundError("tag missing on Hub"),
    )
    monkeypatch.setattr(model_loader, "snapshot_download", mock_dl)

    with pytest.raises(SystemExit) as exc_info:
        fetch_or_use_cached()

    msg = str(exc_info.value)
    assert "v1.0.0" in msg, (
        f"SystemExit msg missing revision name: {msg!r}"
    )
    assert "tag" in msg.lower(), (
        f"SystemExit msg missing 'tag' guidance: {msg!r}"
    )
