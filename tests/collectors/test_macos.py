"""macOS collector tests — runs on every OS via mocked CoreWLAN + subprocess.

Per CONTEXT D-PRIV-04 the v1 macOS event source defaults to
``log show --predicate 'subsystem == "com.apple.wifi"'`` non-sudo;
``WIFI_DIAG_USE_WDUTIL=1`` env var is the only opt-in path to the
sudo-required ``wdutil`` call (no CLI flag).

These tests assert:

1. ``CWInterface`` is the BSSID/SSID/RSSI source on the signed-Python success path.
2. Pitfall 2 / Pitfall 11: on Sonoma 14.4+ with ad-hoc-signed Python,
   ``bssid()``/``ssid()`` return ``None``; collector emits a degraded-not-broken
   frame whose schema-required ``bssid`` field is backfilled by a sentinel hash.
3. Default ``log show`` path NEVER invokes ``sudo`` or ``wdutil``.
4. ``WIFI_DIAG_USE_WDUTIL=1`` flips to the ``sudo wdutil`` opt-in path.
5. The unified-logging predicate targets ``subsystem == "com.apple.wifi"``.
6. EAPOL log lines map to the schema's 6-value ``AuthEventClass`` enum.
7. ``redact_to_schema`` (plan 04-05) is the single boundary — no raw log JSON
   substring escapes through ``MacOSCollector.sample().model_dump_json()``.
8. Suite runs on every OS in CI because CoreWLAN + ``subprocess.run`` are mocked.
"""
from __future__ import annotations

import json
import sys
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# Canned `log show --style ndjson` output — one JSON record per line.
# Mix of association success, EAPOL handshake failure, EAP auth failure.
# ---------------------------------------------------------------------------
CANNED_LOG_NDJSON = "\n".join([
    json.dumps({
        "subsystem": "com.apple.wifi", "category": "Default",
        "eventMessage": "Association complete",
        "timestamp": "2026-05-10 14:00:00.000",
    }),
    json.dumps({
        "subsystem": "com.apple.eapol", "category": "Default",
        "eventMessage": "EAPOL: 4-way handshake failure",
        "timestamp": "2026-05-10 14:00:01.000",
    }),
    json.dumps({
        "subsystem": "com.apple.wifi", "category": "Default",
        "eventMessage": "EAP authentication failed",
        "timestamp": "2026-05-10 14:00:02.000",
    }),
])


@pytest.fixture
def mock_corewlan(mocker):
    """Mock the CoreWLAN module so tests run on non-macOS CI runners.

    Signed-Python path: bssid() and ssid() return real values.
    """
    cw_mod = MagicMock()
    iface = MagicMock()
    iface.bssid.return_value = "aa:bb:cc:dd:ee:ff"
    iface.ssid.return_value = "ExampleSchoolWiFi"
    iface.rssiValue.return_value = -55
    iface.serviceActive.return_value = True
    cw_mod.CWInterface.sharedInstance.return_value = iface
    mocker.patch.dict(sys.modules, {"CoreWLAN": cw_mod})
    # objc is a transitive dep of pyobjc; satisfy imports on non-macOS runners.
    mocker.patch.dict(sys.modules, {"objc": MagicMock()})
    # Force re-import of the collector module so it picks up the fake CoreWLAN.
    sys.modules.pop("agent.collectors.macos", None)
    return iface


@pytest.fixture
def mock_corewlan_adhoc_signed(mocker):
    """Sonoma 14.4+ ad-hoc-signed-Python case (Pitfall 11): bssid()/ssid() return None.

    Collector must NOT crash; must emit a frame with sentinel-hashed bssid.
    """
    cw_mod = MagicMock()
    iface = MagicMock()
    iface.bssid.return_value = None
    iface.ssid.return_value = None
    iface.rssiValue.return_value = -60
    iface.serviceActive.return_value = True
    cw_mod.CWInterface.sharedInstance.return_value = iface
    mocker.patch.dict(sys.modules, {"CoreWLAN": cw_mod})
    mocker.patch.dict(sys.modules, {"objc": MagicMock()})
    sys.modules.pop("agent.collectors.macos", None)
    return iface


@pytest.fixture
def mock_log_show(mocker):
    """Mock subprocess.run for ``log show`` / ``wdutil``."""
    result = MagicMock()
    result.stdout = CANNED_LOG_NDJSON
    result.returncode = 0
    return mocker.patch("agent.collectors.macos.subprocess.run", return_value=result)


@pytest.fixture
def mock_baseline(mocker):
    """Mock the shared baseline collector so tests run without real psutil/icmplib data.

    Forces ``_detect_os`` to return ``"macos"`` regardless of platform.system() —
    the collector will override the os field anyway, but mocking baseline
    keeps payload shape predictable on every CI runner.
    """
    fake_psutil = mocker.patch("agent.collectors.baseline.psutil")
    fake_psutil.net_if_stats.return_value = {
        "en0": MagicMock(isup=True, speed=300, mtu=1500),
    }
    fake_psutil.net_io_counters.return_value = {
        "en0": MagicMock(
            bytes_sent=0, bytes_recv=0, errin=0, errout=0, dropin=0, dropout=0,
        ),
    }
    fake_icmp = mocker.patch("agent.collectors.baseline.icmplib")
    fake_icmp.ping.return_value = MagicMock(
        min_rtt=10, avg_rtt=12, max_rtt=15, packet_loss=0.0, jitter=1.0,
    )
    # Force baseline._detect_os to return macos regardless of platform.system().
    mocker.patch("agent.collectors.baseline._detect_os", return_value="macos")
    return None


def test_signed_python_path_populates_bssid(
    tmp_state_dir, mock_corewlan, mock_log_show, mock_baseline,
):
    """Signed-Python path: CWInterface.bssid() returns a real MAC; frame.bssid is the
    64-char SHA-256 hex (per plan 04-05 ``bssid_hash`` contract)."""
    from agent.collectors.macos import MacOSCollector

    collector = MacOSCollector()
    frame = collector.sample()
    assert frame.os == "macos"
    # bssid_hash output matches schema regex ^[0-9a-f]{64}$
    assert frame.bssid is not None and len(frame.bssid) == 64
    assert frame.bssid_mode == "hashed"


def test_adhoc_signed_path_handles_none_bssid(
    tmp_state_dir, mock_corewlan_adhoc_signed, mock_log_show, mock_baseline,
):
    """Pitfall 11: ad-hoc-signed Python on Sonoma 14.4+ returns None from bssid().
    Collector must NOT crash; must emit a frame with schema-required bssid populated."""
    from agent.collectors.macos import MacOSCollector

    collector = MacOSCollector()
    # Must NOT raise even though CWInterface.bssid() returns None.
    frame = collector.sample()
    assert frame.os == "macos"
    # bssid field is required by schema; degraded path supplies a sentinel hash.
    assert frame.bssid is not None
    assert len(frame.bssid) == 64
    assert frame.bssid_mode == "hashed"


def test_default_path_uses_log_show_non_sudo(
    tmp_state_dir, mock_corewlan, mock_log_show, mock_baseline, monkeypatch,
):
    """D-PRIV-04: default code path is `log show` non-sudo; never invokes sudo/wdutil."""
    monkeypatch.delenv("WIFI_DIAG_USE_WDUTIL", raising=False)
    from agent.collectors.macos import MacOSCollector

    MacOSCollector().sample()
    cmd = mock_log_show.call_args[0][0]
    assert cmd[0] == "log", f"expected `log show`; got {cmd!r}"
    assert "sudo" not in cmd
    assert "wdutil" not in cmd


def test_wdutil_opt_in_via_env_var(
    tmp_state_dir, mock_corewlan, mock_log_show, mock_baseline, monkeypatch,
):
    """D-PRIV-04: WIFI_DIAG_USE_WDUTIL=1 is the ONLY way to reach the sudo wdutil path."""
    monkeypatch.setenv("WIFI_DIAG_USE_WDUTIL", "1")
    from agent.collectors.macos import MacOSCollector

    MacOSCollector().sample()
    cmd = mock_log_show.call_args[0][0]
    assert cmd[0] == "sudo", f"expected `sudo wdutil` opt-in path; got {cmd!r}"
    assert "wdutil" in cmd


def test_log_show_predicate_targets_apple_wifi(
    tmp_state_dir, mock_corewlan, mock_log_show, mock_baseline, monkeypatch,
):
    """Unified-logging predicate must scope to the com.apple.wifi subsystem."""
    monkeypatch.delenv("WIFI_DIAG_USE_WDUTIL", raising=False)
    from agent.collectors.macos import MacOSCollector

    MacOSCollector().sample()
    cmd_str = " ".join(mock_log_show.call_args[0][0])
    assert 'subsystem == "com.apple.wifi"' in cmd_str


def test_eapol_log_line_maps_to_eap_fail(
    tmp_state_dir, mock_corewlan, mock_log_show, mock_baseline, monkeypatch,
):
    """Canned log show ndjson has EAPOL failure + EAP failure lines; auth_event_class
    must map to one of the schema's failure-class enum values."""
    monkeypatch.delenv("WIFI_DIAG_USE_WDUTIL", raising=False)
    from agent.collectors.macos import MacOSCollector

    frame = MacOSCollector().sample()
    assert frame.auth_event_class in ("eap_fail", "8021x_fail", "eapol_m3_timeout"), (
        f"Expected an EAP/EAPOL fail mapping; got {frame.auth_event_class!r}"
    )


def test_no_raw_log_lines_in_emitted_frame(
    tmp_state_dir, mock_corewlan, mock_log_show, mock_baseline, monkeypatch,
):
    """Pitfall 6 / privacy boundary: collector emits a TelemetryFrame whose
    JSON serialization contains NO raw log show substrings."""
    monkeypatch.delenv("WIFI_DIAG_USE_WDUTIL", raising=False)
    from agent.collectors.macos import MacOSCollector

    frame = MacOSCollector().sample()
    dump = frame.model_dump_json()
    assert "subsystem" not in dump
    assert "EAPOL: 4-way" not in dump
    assert "eventMessage" not in dump
    assert "com.apple.wifi" not in dump


def test_collector_runs_on_non_macos_via_mocks(
    tmp_state_dir, mock_corewlan, mock_log_show, mock_baseline,
):
    """Sanity: the entire test suite must run on Win/Linux CI runners — proves that
    CoreWLAN + subprocess are fully mocked and no real macOS-only system call leaks."""
    from agent.collectors.macos import MacOSCollector

    frame = MacOSCollector().sample()
    assert frame.os == "macos"
    # Required schema fields are all populated.
    assert frame.bssid is not None
    assert frame.bssid_mode == "hashed"
    assert frame.auth_event_class is not None
    assert frame.window_ms in (30000, 120000)
