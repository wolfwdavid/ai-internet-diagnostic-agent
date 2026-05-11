"""Shared fixtures for Phase 5 plan 05-02 tests.

These fixtures isolate the cursor file under ``platformdirs.user_cache_dir``
and provide a guaranteed-unreachable URL for the Wave-0 probe.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest


@pytest.fixture
def tmp_cache_dir(tmp_path, monkeypatch) -> Path:
    """Redirect ``platformdirs.user_cache_dir`` to a tmp_path.

    Mirrors the top-level ``tmp_state_dir`` fixture but only patches
    ``user_cache_dir`` (cursor lives in cache).
    """
    cache = tmp_path / "cache"
    cache.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("platformdirs.user_cache_dir", lambda *a, **k: str(cache))
    return cache


@pytest.fixture
def fake_space_url() -> str:
    """URL guaranteed to refuse connections (probe surface)."""
    return "http://127.0.0.1:1"


@pytest.fixture
def paused_space_id(monkeypatch) -> str:
    """Live Space id, used ONLY if ``WIFI_DIAG_PROBE_LIVE=1`` is set.

    Otherwise returns the fake unreachable URL.
    """
    if os.environ.get("WIFI_DIAG_PROBE_LIVE") == "1":
        return "WolfDavid/wifi-diag"
    return "http://127.0.0.1:1"
