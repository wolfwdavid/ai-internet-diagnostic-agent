"""Linux NetworkManager collector via dbus-next (Pitfall 12 mitigation included).

AGENT-01 [Linux] + AGENT-02 [Linux]. v1 scope:

- System bus introspect on ``org.freedesktop.NetworkManager`` to read the
  global daemon state (the ``State`` property — codes 0/10/20/30/40/50/60/70).
- Map that state into one of the 6 allowed ``AuthEventClass`` schema values.
- Pitfall 12 fallback: if NetworkManager is absent (introspect raises a
  ``ServiceUnknown`` / connection error), emit a baseline-only TelemetryFrame
  with ``auth_event_class="none"`` — DO NOT crash. Supported stacks per
  CLAUDE.md (Pitfall 12 mitigation list):

    Ubuntu 22.04+, Fedora 38+, Pop!_OS 22.04+, Linux Mint 21+,
    Arch Linux with the ``NetworkManager`` package.

  Unsupported at v1 (collector still produces a valid baseline frame):
  Arch with ``iwd``, distros using ``systemd-networkd`` standalone, Alpine
  / Void without NM. See README "Supported platforms" for the full table.
- wpa_supplicant (``fi.w1.wpa_supplicant1``) signal subscription is documented
  as a v1.x extension and intentionally NOT wired here — the v1 scope keeps
  the surface tight (a single read against NetworkManager's State property)
  to minimize per-OS code surface area per ARCHITECTURE.md Anti-Pattern 6.

**Asyncio bridge:** NetworkManager's D-Bus interface is async-native (dbus-next
exposes ``MessageBus.connect()`` / ``introspect()`` / proxy methods as
coroutines), but ``Collector.sample()`` is synchronous (matches the Collector
ABC and the daemon's per-tick contract). We run a tiny per-call event loop
because the daemon's sampling cadence is 1-5s — the per-call loop overhead is
negligible compared to a D-Bus round trip, and the simpler model avoids
sharing async state with the rest of the agent.

**Privacy boundary (Pitfall 6):** ``redact_to_schema`` is the ONLY function
in this module that constructs a ``TelemetryFrame``. The collector hands it
a payload ``dict`` containing the integer NM state code (an ``int``, not a
D-Bus introspection object), plus the baseline payload. No D-Bus object
references, no introspection XML, no bus names or object paths survive
past the redaction boundary into the serialized frame.
"""

from __future__ import annotations

import asyncio
import logging

# Lazy import — dbus-next only available on Linux + when the [linux] extra is
# installed. The tests mock ``sys.modules["dbus_next"]`` so this import works
# on Windows / macOS CI runners; if neither path is satisfied the module
# attributes default to ``None`` and the collector falls into the
# baseline-only branch (Pitfall 12).
try:
    from dbus_next import BusType  # type: ignore[import-not-found]
    from dbus_next.aio import MessageBus  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover - exercised only on un-installed envs
    MessageBus = None  # type: ignore[assignment,misc]
    BusType = None  # type: ignore[assignment,misc]

from wifi_diag_schema import TelemetryFrame

from agent.collectors.base import Collector
from agent.collectors.baseline import collect_baseline
from agent.redaction import redact_to_schema

log = logging.getLogger("agent.collectors.linux")

# ---------------------------------------------------------------------------
# NetworkManager state codes
# https://developer.gnome.org/NetworkManager/stable/nm-dbus-types.html
# ---------------------------------------------------------------------------
NM_STATE_UNKNOWN = 0
NM_STATE_ASLEEP = 10
NM_STATE_DISCONNECTED = 20
NM_STATE_DISCONNECTING = 30
NM_STATE_CONNECTING = 40
NM_STATE_CONNECTED_LOCAL = 50
NM_STATE_CONNECTED_SITE = 60
NM_STATE_CONNECTED_GLOBAL = 70

NM_BUS_NAME = "org.freedesktop.NetworkManager"
NM_OBJECT_PATH = "/org/freedesktop/NetworkManager"


def _classify_nm_state(state: int) -> str:
    """Map a NetworkManager state code to the schema's AuthEventClass enum.

    v1 conservative mapping (no wpa_supplicant signal evidence yet):

    - CONNECTED_{LOCAL,SITE,GLOBAL} -> ``"8021x_success"``
    - DISCONNECTED / DISCONNECTING -> ``"8021x_fail"``
    - CONNECTING / ASLEEP / UNKNOWN -> ``"none"`` (no claim either way)

    Only the 6 allowed AuthEventClass values may be returned:
    ``{"none", "8021x_success", "8021x_fail", "radius_timeout", "eap_fail",
    "eapol_m3_timeout"}``. Returning a free-text value would be caught by
    ``redact_to_schema``'s enum-coercion step (mapped to ``"none"``) — but
    the explicit conservative mapping here is the documented intent.
    """
    if state in (
        NM_STATE_CONNECTED_LOCAL,
        NM_STATE_CONNECTED_SITE,
        NM_STATE_CONNECTED_GLOBAL,
    ):
        return "8021x_success"
    if state in (NM_STATE_DISCONNECTED, NM_STATE_DISCONNECTING):
        return "8021x_fail"
    return "none"


async def _async_sample_nm() -> dict:
    """Probe NetworkManager via the D-Bus system bus.

    Returns ``{"nm_present": bool, "state": int | None}``. Wrapped in a
    try/except at every D-Bus call site so the Pitfall 12 fallback (NM not
    on the bus, no permission, dbus daemon unreachable) always returns a
    well-typed dict instead of propagating an exception.
    """
    if MessageBus is None or BusType is None:
        return {"nm_present": False, "state": None}

    try:
        bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
    except Exception as exc:  # noqa: BLE001 - any D-Bus error means no NM here
        log.debug("dbus connect failed: %s", exc)
        return {"nm_present": False, "state": None}

    try:
        try:
            introspection = await bus.introspect(NM_BUS_NAME, NM_OBJECT_PATH)
        except Exception as exc:  # noqa: BLE001
            # Pitfall 12: NM is not running on this system (e.g., Arch+iwd,
            # systemd-networkd standalone). Fall back to baseline-only.
            log.info(
                "NetworkManager not detected on D-Bus: %s — "
                "falling back to baseline-only telemetry collection",
                exc,
            )
            return {"nm_present": False, "state": None}

        try:
            proxy = bus.get_proxy_object(NM_BUS_NAME, NM_OBJECT_PATH, introspection)
            iface = proxy.get_interface(NM_BUS_NAME)
            # dbus-next auto-generates ``get_<property>()`` accessors from
            # introspection XML; ``State`` is the daemon's overall state code.
            state = await iface.get_state()  # type: ignore[attr-defined]
            return {"nm_present": True, "state": int(state)}
        except Exception as exc:  # noqa: BLE001
            log.debug("NetworkManager state read failed: %s", exc)
            return {"nm_present": True, "state": None}
    finally:
        try:
            await bus.disconnect()
        except Exception:  # noqa: BLE001 - cleanup must never raise
            pass


def _sample_nm_sync() -> dict:
    """Run the async D-Bus probe inside a per-call event loop.

    The daemon's tick cadence (1-5 s) makes per-call loop construction cheap
    relative to a single D-Bus round trip, and avoids holding a long-lived
    asyncio loop in the otherwise-sync collector path.
    """
    try:
        return asyncio.run(_async_sample_nm())
    except RuntimeError:
        # ``asyncio.run`` raises ``RuntimeError`` if a loop is already running
        # in this thread (rare for the daemon, but possible under test
        # harnesses that drive collectors from an event loop). Construct a
        # fresh loop and close it deterministically.
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(_async_sample_nm())
        finally:
            loop.close()


class LinuxCollector(Collector):
    """Linux-specific collector via NetworkManager on the D-Bus system bus.

    Falls back to a baseline-only frame (psutil + icmplib) when NetworkManager
    is absent (Pitfall 12 mitigation). Emits a single info-level log line on
    the first fallback so the user can see WHY their telemetry is sparse,
    then silences itself to avoid log spam at 1-5s tick cadence.
    """

    def __init__(self) -> None:
        # Whether we've already logged the "NM not detected" notice. Prevents
        # log-spam on systems without NetworkManager — the log line is
        # informative the first time, noise on every subsequent tick.
        self._nm_warned: bool = False

    def sample(self) -> TelemetryFrame:
        payload = collect_baseline()
        payload["os"] = "linux"

        nm_result = _sample_nm_sync()
        if nm_result["nm_present"] and nm_result["state"] is not None:
            payload["auth_event_class"] = _classify_nm_state(nm_result["state"])
        else:
            # Pitfall 12: NM absent or unreadable. Baseline-only path.
            if not self._nm_warned:
                log.info(
                    "NetworkManager not detected. v1 supports Ubuntu 22.04+, "
                    "Fedora 38+, Pop!_OS, Mint, and Arch+NetworkManager. "
                    "iwd / systemd-networkd standalone deferred to v1.x. "
                    "Falling back to baseline (psutil + icmplib).",
                )
                self._nm_warned = True
            payload["auth_event_class"] = "none"

        # Single redaction boundary (Pitfall 6). Every collector dict must go
        # through this — never construct TelemetryFrame directly here.
        return redact_to_schema(payload)
