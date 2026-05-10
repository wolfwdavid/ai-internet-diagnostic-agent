"""Local-only inference pipeline (AGENT-05).

Vendors ``predict_verdict`` from ``../ai-internet-diagnostic-model/model/inference.py``
(per RESEARCH Open Question 2 — ~120 LOC vendor cheaper than maintaining a second
PyPI package and avoids the agent importing a package literally named ``model``,
which would conflict with any local module named ``model``).

The narrator path is ``wifi_diag_narrator.templated.narrate_templated`` — LLM-FREE.
No ``anthropic`` import here, ever (``test_inference.py`` asserts this).

vendored_from: WolfDavid/ai-internet-diagnostic-model@dc95cf9918e9e1d890b2f73aeca4d80dacccf0f8
vendored_at: 2026-05-10
sync_via: this plan (04-06) when upstream model/inference.py changes
"""
from __future__ import annotations

import joblib
import numpy as np
import pandas as pd

# Narrator's __init__.py does not re-export narrate_templated at v0.1.0;
# import from the templated submodule.
from wifi_diag_narrator.templated import narrate_templated
from wifi_diag_schema import TelemetryFrame, Verdict
from wifi_diag_schema.enums import DisconnectClass, NetworkMode

from agent import model_loader

# ---------------------------------------------------------------------------
# VENDORED CONSTANTS (from model/features.py)
#
# CLASSES is the canonical 10-class disconnect label order — must match the
# trained classifier's predict_proba output column order. Vendored to avoid
# importing ``model`` (which would conflict with any other top-level
# ``model`` package on PYTHONPATH).
# ---------------------------------------------------------------------------
CLASSES: list[str] = [
    "auth_8021x_eap_fail",
    "ap_roam_rekey_fail",
    "radius_timeout",
    "captive_portal_expiry",
    "mac_randomization_reject",
    "dhcp_lease_churn",
    "dns_resolver_fail",
    "driver_power_save_wake",
    "rf_sticky_client",
    "isp_upstream_fail",
]

ANOMALY_FEATURES: tuple[str, ...] = (
    "rssi_dbm",
    "ping_continuity_avg_rtt_ms",
    "ping_continuity_packet_loss_pct",
    "ping_continuity_jitter_ms",
    "latency_jitter_ms",
    "dns_resolution_ms",
    "per_packet_retry_count",
    "beacon_rssi_dbm",
    "neighbor_ap_count_5ghz",
)

CATEGORICAL_FEATURES: tuple[str, ...] = (
    "os",
    "network_mode",
    "dhcp_event_class",
    "auth_event_class",
    "mac_randomization_state",
    "driver_state",
    "captive_portal_detected",
    "bssid_mode",
)

# 9 anomaly numerics + 8 categoricals + 3 misc numerics = 20 features.
# Order matters: the trained classifier was fit on this exact column order.
CLASSIFIER_FEATURES: tuple[str, ...] = (
    ANOMALY_FEATURES + CATEGORICAL_FEATURES + ("window_ms", "channel", "rts_cts_rate")
)

# D-MASK-02 mask table (frozen at v1; mirrored in MODEL_CARD.md).
# Vendored verbatim from model/inference.py::MASK_TABLE.
MASK_TABLE: dict[NetworkMode, frozenset[DisconnectClass]] = {
    "enterprise": frozenset(
        [
            "auth_8021x_eap_fail",
            "ap_roam_rekey_fail",
            "radius_timeout",
            "mac_randomization_reject",
            "dhcp_lease_churn",
            "dns_resolver_fail",
            "driver_power_save_wake",
            "rf_sticky_client",
        ]
    ),
    "captive": frozenset(
        [
            "captive_portal_expiry",
            "dns_resolver_fail",
            "isp_upstream_fail",
            "dhcp_lease_churn",
            "mac_randomization_reject",
        ]
    ),
    "home": frozenset(
        [
            "dhcp_lease_churn",
            "dns_resolver_fail",
            "driver_power_save_wake",
            "rf_sticky_client",
            "isp_upstream_fail",
        ]
    ),
    # D-MASK-03: unknown mode enables all 10 classes.
    "unknown": frozenset(CLASSES),
}


def apply_mask_and_renormalize(
    probs: np.ndarray, network_mode: NetworkMode
) -> np.ndarray:
    """D-CAL-09: zero masked classes, renormalize remainder to sum to 1.

    Vendored verbatim from model/inference.py::apply_mask_and_renormalize.
    """
    applicable = MASK_TABLE[network_mode]
    mask = np.array([slug in applicable for slug in CLASSES], dtype=np.bool_)
    masked = np.where(mask, probs, 0.0)
    total = masked.sum()
    if total <= 0.0:
        # Degenerate fallback (shouldn't happen at v1 — every mode has >=5 enabled).
        return np.full_like(probs, 1.0 / len(CLASSES))
    return masked / total


def _frames_to_array(frames: list) -> np.ndarray:
    """Convert TelemetryFrame instances (or dicts) into the classifier's input matrix.

    Mirrors ``model.features.load_split`` logic. Vendored from
    ``model/inference.py::_frames_to_array``, extended to accept TelemetryFrame
    instances directly (the upstream takes raw dicts).
    """
    flat: list[dict] = []
    for f in frames:
        row = dict(f.model_dump() if hasattr(f, "model_dump") else f)
        pc = row.pop("ping_continuity", None)
        if isinstance(pc, dict):
            row["ping_continuity_window_ms"] = pc.get("window_ms")
            row["ping_continuity_avg_rtt_ms"] = pc.get("avg_rtt_ms")
            row["ping_continuity_packet_loss_pct"] = pc.get("packet_loss_pct")
            row["ping_continuity_jitter_ms"] = pc.get("jitter_ms")
        flat.append(row)

    df = pd.DataFrame(flat)
    for col in CATEGORICAL_FEATURES:
        if col not in df.columns:
            df[col] = pd.Series([None] * len(df))
        df[col] = df[col].astype("category").cat.codes
    # Defensive fill for missing numeric columns (streaming partial frames).
    for col in CLASSIFIER_FEATURES:
        if col not in df.columns:
            df[col] = 0.0
    return df[list(CLASSIFIER_FEATURES)].to_numpy(dtype=np.float64)


def _predict_verdict_impl(classifier_path, frames: list) -> Verdict:
    """Run a window through the classifier and produce a schema-valid Verdict.

    Vendored (with TelemetryFrame-aware network_mode lookup) from
    ``model/inference.py::predict_verdict``.

    Aggregation: per-frame predict_proba, then average across the window.
    Mask uses the LAST frame's network_mode (most-recent semantics, D-CAL-09).
    Emits full top_k ranking (D-CAL-08, K=10) post-mask.

    Returns a Verdict with top_k populated and stub headline/suggested_fix.
    The narrator (``narrate_templated``) fills in headline/suggested_fix/evidence
    in ``run_local_inference``.
    """
    clf = joblib.load(str(classifier_path))
    X = _frames_to_array(frames)

    proba_per_frame = clf.predict_proba(X)  # (n_frames, 10)
    proba_window = proba_per_frame.mean(axis=0)  # (10,)

    # Most-recent network_mode (D-CAL-09 — mask-then-renormalize against the
    # last frame, not a window vote).
    last = frames[-1]
    network_mode: NetworkMode = (
        last.network_mode if hasattr(last, "network_mode") else last["network_mode"]
    )
    proba_masked = apply_mask_and_renormalize(proba_window, network_mode)

    order = np.argsort(-proba_masked)
    top_k: list[tuple[DisconnectClass, float]] = [
        (CLASSES[i], float(proba_masked[i])) for i in order
    ]
    top_class = top_k[0][0]
    confidence = top_k[0][1]

    return Verdict(
        top_class=top_class,
        confidence=confidence,
        top_k=top_k,
        headline=(
            f"Pre-narrator stub: classifier predicts {top_class} "
            f"({confidence:.0%})"
        ),
        suggested_fix="Pre-narrator stub: narrate_templated will fill this in.",
        evidence=[],
    )


# ---------------------------------------------------------------------------
# Public API used by agent.cli.diagnose
# ---------------------------------------------------------------------------
def run_local_inference(window: list[TelemetryFrame]) -> Verdict:
    """Local-only inference: classifier + templated narrator. NO network egress.

    AGENT-05: local-only mode runs the full classifier + templated narrator
    on the laptop. ``narrate_templated`` is LLM-free; no ``anthropic`` import
    anywhere in this module.
    """
    if not window:
        raise ValueError("inference window must contain at least 1 frame")

    # Load classifier artifact from HF Hub cache (local-first; first run downloads).
    classifier_path = model_loader.path_for("classifier.joblib")

    # 1. Predict (returns stub Verdict with top_k populated).
    stub_verdict = _predict_verdict_impl(classifier_path, window)

    # 2. Narrate locally — templated narrator is LLM-free.
    narrated = narrate_templated(stub_verdict, window)

    return narrated
