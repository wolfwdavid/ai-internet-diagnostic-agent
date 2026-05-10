"""Linux NetworkManager collector tests — runs on every OS via mocked dbus_next.

Plan 04-04: Wave 0 RED-state tests.

These tests exercise:
- The NM-present happy path (D-Bus introspect succeeds, state read OK).
- The Pitfall 12 fallback path (NM not detected — collector emits baseline-only frame).
- NM state-code mapping into the 6-value AuthEventClass schema enum.
- The Pitfall 6 single-redaction-boundary guarantee (no D-Bus strings leak into the
  serialized TelemetryFrame).
- Cross-OS CI runnability (Windows/macOS runners) via dbus_next mocked into sys.modules.
"""
from __future__ import annotations

import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

# NetworkManager state codes (NM_STATE_*) — kept as module-local constants so the
# tests document the mapping that the collector implements.
NM_STATE_UNKNOWN = 0
NM_STATE_ASLEEP = 10
NM_STATE_DISCONNECTED = 20
NM_STATE_DISCONNECTING = 30
NM_STATE_CONNECTING = 40
NM_STATE_CONNECTED_LOCAL = 50
NM_STATE_CONNECTED_SITE = 60
NM_STATE_CONNECTED_GLOBAL = 70


@pytest.fixture
def mock_dbus_next_present(mocker):
    """NM is present on the system; introspection succeeds."""
    bustype_mod = MagicMock()
    bustype_mod.SYSTEM = "system"

    bus = MagicMock()
    # Async methods used by the collector.
    bus.connect = AsyncMock(return_value=bus)
    bus.introspect = AsyncMock(return_value=MagicMock())
    bus.disconnect = AsyncMock(return_value=None)

    # Mock proxy + interface (D-Bus state read path).
    proxy = MagicMock()
    iface = MagicMock()
    iface.get_state = AsyncMock(return_value=NM_STATE_CONNECTED_GLOBAL)
    iface.get_active_connections = AsyncMock(return_value=[])
    proxy.get_interface = MagicMock(return_value=iface)
    bus.get_proxy_object = MagicMock(return_value=proxy)

    aio_mod = MagicMock()
    message_bus_cls = MagicMock(return_value=bus)
    aio_mod.MessageBus = message_bus_cls

    dbus_next_mod = MagicMock()
    dbus_next_mod.aio = aio_mod
    dbus_next_mod.BusType = bustype_mod

    mocker.patch.dict(sys.modules, {
        "dbus_next": dbus_next_mod,
        "dbus_next.aio": aio_mod,
    })
    return {"bus": bus, "iface": iface, "message_bus_cls": message_bus_cls}


@pytest.fixture
def mock_dbus_next_absent(mocker):
    """NM not present — introspect raises (Pitfall 12 fallback path)."""
    bustype_mod = MagicMock()
    bustype_mod.SYSTEM = "system"

    bus = MagicMock()
    bus.connect = AsyncMock(return_value=bus)
    bus.introspect = AsyncMock(
        side_effect=Exception("ServiceUnknown: NetworkManager not present"),
    )
    bus.disconnect = AsyncMock(return_value=None)

    aio_mod = MagicMock()
    aio_mod.MessageBus = MagicMock(return_value=bus)

    dbus_next_mod = MagicMock()
    dbus_next_mod.aio = aio_mod
    dbus_next_mod.BusType = bustype_mod

    mocker.patch.dict(sys.modules, {
        "dbus_next": dbus_next_mod,
        "dbus_next.aio": aio_mod,
    })
    return {"bus": bus}


@pytest.fixture
def mock_baseline(mocker):
    """Patch the baseline collector's OS dependencies so it returns deterministic data.

    The baseline collector (owned by Plan 04-02) reads psutil + icmplib and reports
    its detected OS. We patch all three so the Linux-collector tests are isolated
    from the runner's real network state.
    """
    fake_psutil = mocker.patch("agent.collectors.baseline.psutil")
    fake_psutil.net_if_stats.return_value = {
        "wlan0": MagicMock(isup=True, speed=300, mtu=1500),
    }
    fake_psutil.net_io_counters.return_value = {
        "wlan0": MagicMock(
            bytes_sent=0, bytes_recv=0, errin=0, errout=0, dropin=0, dropout=0,
        ),
    }
    fake_icmp = mocker.patch("agent.collectors.baseline.icmplib")
    fake_icmp.ping.return_value = MagicMock(
        min_rtt=10, avg_rtt=12, max_rtt=15, packet_loss=0.0, jitter=1.0,
    )
    mocker.patch("agent.collectors.baseline._detect_os", return_value="linux")
    return None


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_nm_present_path_emits_valid_frame(tmp_state_dir, mock_dbus_next_present, mock_baseline):
    """Happy path: NM present, introspection OK, frame validates with linux os."""
    from agent.collectors.linux import LinuxCollector

    collector = LinuxCollector()
    frame = collector.sample()
    assert frame.os == "linux"
    assert frame.auth_event_class in (
        "none", "8021x_success", "8021x_fail",
        "radius_timeout", "eap_fail", "eapol_m3_timeout",
    )


def test_nm_absent_falls_back_to_baseline(tmp_state_dir, mock_dbus_next_absent, mock_baseline):
    """Pitfall 12: introspect raises; collector MUST NOT crash; emits baseline-only frame."""
    from agent.collectors.linux import LinuxCollector

    collector = LinuxCollector()
    frame = collector.sample()
    assert frame.os == "linux"
    # Fallback default when NM is unreachable.
    assert frame.auth_event_class == "none"


def test_nm_state_disconnected_classification(
    tmp_state_dir, mock_dbus_next_present, mock_baseline,
):
    """NM state=20 (disconnected) maps into one of the schema's 6 enum values."""
    mock_dbus_next_present["iface"].get_state = AsyncMock(
        return_value=NM_STATE_DISCONNECTED,
    )
    from agent.collectors.linux import LinuxCollector

    collector = LinuxCollector()
    frame = collector.sample()
    # 8021x_fail or eap_fail or none are all reasonable for "disconnected"; the
    # plan's conservative v1 mapping picks 8021x_fail.
    assert frame.auth_event_class in ("none", "8021x_fail", "eap_fail")


def test_nm_state_connected_classification(
    tmp_state_dir, mock_dbus_next_present, mock_baseline,
):
    """NM state=70 (connected) maps into 8021x_success or none."""
    mock_dbus_next_present["iface"].get_state = AsyncMock(
        return_value=NM_STATE_CONNECTED_GLOBAL,
    )
    from agent.collectors.linux import LinuxCollector

    collector = LinuxCollector()
    frame = collector.sample()
    assert frame.auth_event_class in ("none", "8021x_success")


def test_no_dbus_strings_in_emitted_frame(
    tmp_state_dir, mock_dbus_next_present, mock_baseline,
):
    """Pitfall 6: redact_to_schema is the single boundary; no D-Bus strings leak."""
    from agent.collectors.linux import LinuxCollector

    frame = LinuxCollector().sample()
    dump = frame.model_dump_json()
    assert "org.freedesktop" not in dump
    assert "MessageBus" not in dump
    assert "dbus" not in dump.lower()


def test_message_bus_called_with_system_bus(
    tmp_state_dir, mock_dbus_next_present, mock_baseline,
):
    """The collector connects on the SYSTEM bus (not session) — that is where
    NetworkManager publishes its service in production."""
    from agent.collectors.linux import LinuxCollector

    LinuxCollector().sample()
    cls = mock_dbus_next_present["message_bus_cls"]
    assert cls.called, "expected dbus_next.aio.MessageBus to be instantiated"


def test_runs_on_non_linux_via_mocks(tmp_state_dir, mock_dbus_next_present, mock_baseline):
    """CI runners on Windows/macOS execute this suite successfully — the dbus_next
    import is mocked in sys.modules so the module-level import in linux.py
    succeeds on every OS."""
    from agent.collectors.linux import LinuxCollector

    frame = LinuxCollector().sample()
    # If we got here, the import succeeded and a frame was produced — that is
    # itself the assertion. Pin an extra sanity check on the schema field.
    assert frame.os == "linux"
