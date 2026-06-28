"""Shared test fixtures for the agent test suite."""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from wifi_diag_schema import TelemetryFrame
from wifi_diag_schema.telemetry import PingContinuity


@pytest.fixture
def tmp_state_dir(monkeypatch, tmp_path) -> Path:
    """Redirect platformdirs to a temp directory so daemon/buffer/history tests are isolated.

    Patches both `platformdirs.user_*_dir` (top-level imports) and the same names re-exported
    via `platformdirs.api` so that any code path importing the function name into a module
    namespace at import time still sees the redirected value.
    """
    cache = tmp_path / "cache"
    data = tmp_path / "data"
    state = tmp_path / "state"
    config = tmp_path / "config"
    log = tmp_path / "log"
    for d in (cache, data, state, config, log):
        d.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr("platformdirs.user_cache_dir", lambda *a, **k: str(cache))
    monkeypatch.setattr("platformdirs.user_data_dir", lambda *a, **k: str(data))
    monkeypatch.setattr("platformdirs.user_state_dir", lambda *a, **k: str(state))
    monkeypatch.setattr("platformdirs.user_config_dir", lambda *a, **k: str(config))
    monkeypatch.setattr("platformdirs.user_log_dir", lambda *a, **k: str(log))
    return tmp_path


@pytest.fixture
def synthetic_frame() -> TelemetryFrame:
    """Build a minimal valid TelemetryFrame for unit tests.

    Satisfies extra="forbid" + the full required-field set on TelemetryFrame
    (see ../wifi-diag-schema/src/wifi_diag_schema/telemetry.py). Optional fields
    use the schema defaults (None) implicitly.
    """
    return TelemetryFrame(
        timestamp=time.time(),
        os="windows",
        network_mode="enterprise",
        rssi_dbm=-60,
        bssid="0" * 64,  # SHA-256 hex (matches ^[0-9a-f]{64}$)
        bssid_mode="hashed",
        channel=36,
        ping_continuity=PingContinuity(
            window_ms=2000,
            avg_rtt_ms=12.3,
            packet_loss_pct=0.0,
            jitter_ms=1.5,
        ),
        dhcp_event_class="none",
        auth_event_class="8021x_success",
        captive_portal_detected=False,
        mac_randomization_state="off",
        driver_state="normal",
        window_ms=120000,
    )
