# gradio_client exception surface (Phase 5 plan 05-02 Wave-0 probe)

This file is APPENDED to by `tests/phase05/test_probe_exceptions.py` on every run.
The retry-predicate in `agent/transport/client.py` MUST be a superset of the types
listed here under "Unreachable URL probe" and "Live Space probe".

## Probe methodology

- Synthetic: connect to `http://127.0.0.1:1` — guaranteed connection-refused;
  surfaces the connect-time exception class.
- Live: connect to `WolfDavid/wifi-diag` while the Space is paused (owner runs
  `HfApi().pause_space()` first); surfaces the cold-start exception class. Gated
  by env-var `WIFI_DIAG_PROBE_LIVE=1` so CI does not depend on a live Space.

## Observed types

_Auto-appended by probe runs below. Latest run wins for the production retry
filter._

## Unreachable URL probe (2026-05-10T22:23:18)
- module: `httpx`
- type: `ConnectError`
- repr: `ConnectError('[WinError 10061] No connection could be made because the target machine actively refused it')`
