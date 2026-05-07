# AI Internet Diagnostic — Agent

Cross-platform local agent (Windows / macOS / Linux) that collects enterprise-Wi-Fi telemetry and either runs full local diagnosis (no network egress — privacy-by-default) or streams `TelemetryFrame`s to the [Hugging Face Space](https://huggingface.co/spaces/WolfDavid/ai-internet-diagnostic) for live diagnosis.

**Phase 1 status:** Skeleton only. Per-OS collectors land in Phase 4; agent ↔ Space SSE transport in Phase 5.

## Pin posture

- `wifi-diag-schema>=1.0.0,<2.0.0` (D-13) — `[tool.uv.sources]` defaults to sibling-source path during local dev; CI swaps to PyPI install once published.
- Python 3.13 (D-15).
- Per-OS extras: `pip install ai-internet-diagnostic-agent[windows]` / `[macos]` / `[linux]` — currently empty placeholders, populated in Phase 4.

## HF mirror status

Per CONTEXT D-11: agent's HF Hub mirror is deferred. Install path is GitHub-driven; no `release.yml` ships in Phase 1. Phase 4 plan 04-* will add an HF mirror once the agent has installable per-OS collectors that benefit from a discoverable HF surface.

## Privacy posture

The schema field allowlist on `TelemetryFrame` IS the privacy contract. See `PRIVACY.md` (added in Phase 4 plan 04-05) for full data-flow documentation.

## License

Apache-2.0.
