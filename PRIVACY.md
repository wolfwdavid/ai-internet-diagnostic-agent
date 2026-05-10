# PRIVACY — AI Internet Diagnostic Agent

> Written from the `wifi_diag_schema.TelemetryFrame` allowlist, not aspirationally.

The privacy posture is **structural**, not behavioral. The Pydantic schema
`wifi_diag_schema.TelemetryFrame` is configured `extra="forbid"`. Every Phase 4
collector emits values that conform to the schema-allowlist. The CI gate
`tests/test_redaction_roundtrip.py` (hypothesis property test) fails the build
if any non-allowlist field appears in serialized telemetry.

## What is collected

| Field | Source | Notes |
|---|---|---|
| `timestamp` | local clock | Unix epoch float |
| `os` | `platform.system()` | windows / macos / linux |
| `network_mode` | classifier preprocessing | enterprise / captive / home / unknown |
| `rssi_dbm` | psutil + per-OS APIs | signal strength |
| `bssid` | per-OS APIs, **hashed** | SHA-256 with per-install salt by default |
| `bssid_mode` | constant | "hashed" by default; "raw" only with `--with-bssid-raw` |
| `channel` | per-OS APIs | Wi-Fi channel number |
| `ping_continuity` | `icmplib.async_ping` summary | window_ms / avg_rtt_ms / packet_loss_pct / jitter_ms |
| `latency_jitter_ms` | jitter aggregation | continuity-disruption signal |
| `dns_resolution_ms` | DNS probe | `dns_resolver_fail` class signal |
| `auth_event_class` | per-OS event log → enum | NEVER raw event-log strings |
| `dhcp_event_class` | per-OS event log → enum | NEVER raw DHCP packets |
| `captive_portal_detected` | HTTP probe to neutral host | bool |
| `mac_randomization_state` | per-OS APIs → enum | off / per_network / per_session / rejected |
| `driver_state` | per-OS APIs → enum | normal / post_wake_init / power_save_active / u_apsd_active / error / unknown |
| `per_packet_retry_count` | optional | extended-set field; `None` when unavailable |
| `rts_cts_rate` | optional | extended-set field; `None` when unavailable |
| `beacon_rssi_dbm` | optional | extended-set field; `None` when unavailable |
| `neighbor_ap_count_5ghz` | optional | extended-set field; `None` when unavailable |
| `window_ms` | constant | 30000 or 120000 (D-04) |

*(The canonical allowlist lives in
`wifi-diag-schema/src/wifi_diag_schema/telemetry.py` —
`TelemetryFrame.model_fields`. PRIVACY.md is generated from that list.)*

## What is NOT collected

There is **no field** in `TelemetryFrame` for any of the following — and
`extra="forbid"` prevents collectors from accidentally adding them:

- `raw_message` — raw event-log XML strings
- `username` / `eap_identity` / `Identity` — never extracted from event logs
- `password` — never extracted from event logs
- `cert_subject_dn` / `UserCert` — TLS certificate contents
- `ssid` is collected only with explicit per-event consent (Phase 5; not yet wired)
- `bssid` raw form is opt-in via `--with-bssid-raw` flag; default is hashed

## Where data lives

| Path | Purpose | Lifetime |
|---|---|---|
| `platformdirs.user_cache_dir/wifi-diag/buffer/buffer.db` | Rolling 120s telemetry buffer (D-AGENT-04) | Volatile; auto-pruned |
| `platformdirs.user_data_dir/wifi-diag/history.db` | Diagnosis history (D-HISTORY-04) | 90-day retention by default |
| `platformdirs.user_config_dir/wifi-diag/salt.bin` | Per-install BSSID hash salt | Permanent; mode 0o600 (POSIX) |
| `platformdirs.user_state_dir/wifi-diag/daemon.pid` | Daemon PID file | While daemon running |

## Threat model

schema-allowlist redaction strips credentials / EAP-IDs / certificate content /
raw event-log strings BEFORE anything reaches the buffer or history. The threat
model is: an attacker with disk read access sees BSSIDs (hashed by default),
RSSI traces, ping continuity to 8.8.8.8, and per-OS auth-event class enums.
Low severity. Encryption-at-rest deferred to v1.x once dogfood proves the
threat is real.

Backup software: `user_cache_dir` is excluded by default in many backup tools
(Time Machine, Backblaze). `user_data_dir/wifi-diag/history.db` IS backed up
by default — exclude it manually if your threat model requires it.

## Consent model

Per-event consent (D-CONSENT-01..04). Every `agent diagnose` invocation is one
event. NO session memory; NO config-file-based "remember my choice."

Default: **Local-only** — verdict computed on this laptop, nothing leaves.
Phase 5 will wire two cloud options:
- Cloud (redacted) — anonymized fingerprint, no SSID
- Cloud (with SSID) — adds your network name

Cloud options are visibly disabled in Phase 4 with `[Phase 5]` annotations.

Non-interactive: `agent diagnose --consent local` skips the prompt.

## Effective configuration

- default consent: local
- history retention: 90 days (configurable via `agent history retention --days N`)
- model revision: v1.0.0 (pinned in code; bump = new agent release)
- schema-allowlist contract: every TelemetryFrame field listed above + below
- BSSID hashing: SHA-256 with per-install salt; deterministic per-install

## Schema-allowlist field reference

<!-- The test_privacy_doc.py CI test asserts every TelemetryFrame.model_fields
     key appears at least once in this document. The auto-generated marker
     block below enumerates the schema field names verbatim from
     TelemetryFrame.model_fields.keys() at the time PRIVACY.md was written. -->

The following is the verbatim list of `TelemetryFrame.model_fields` keys
(schema v1.1.0). Adding a field to the schema requires updating PRIVACY.md
in the same commit (the CI gate enforces this):

- timestamp
- os
- network_mode
- rssi_dbm
- bssid
- bssid_mode
- channel
- ping_continuity
- latency_jitter_ms
- dns_resolution_ms
- dhcp_event_class
- auth_event_class
- captive_portal_detected
- mac_randomization_state
- driver_state
- per_packet_retry_count
- rts_cts_rate
- beacon_rssi_dbm
- neighbor_ap_count_5ghz
- window_ms
