"""Local-only inference (AGENT-05) — predict_verdict + narrate_templated end-to-end.

Verifies:
- run_local_inference() returns a schema-valid Verdict with non-empty top_k.
- The agent NEVER imports `anthropic` (the narrator's [llm] extra is intentionally
  not in the agent's deps — marquee AGENT-05 assertion).
- _predict_verdict_impl was actually vendored (the integration test below
  drives a real LogisticRegression artifact through the function body and
  asserts top_k is a full ranking summing to 1).
"""
from __future__ import annotations

import sys

import pytest
from wifi_diag_schema import Verdict


def _make_window(synthetic_frame, n: int = 30):
    return [
        synthetic_frame.model_copy(
            update={"timestamp": synthetic_frame.timestamp + i}
        )
        for i in range(n)
    ]


def test_local_inference_produces_valid_verdict(tmp_state_dir, synthetic_frame, mocker):
    """Mocked: confirms run_local_inference wires classifier output -> narrator -> Verdict."""
    # Build a stub Verdict matching the schema (top_class + confidence + top_k + non-empty
    # headline/suggested_fix — the schema requires all of these).
    stub_verdict = Verdict(
        top_class="auth_8021x_eap_fail",
        confidence=0.85,
        top_k=[
            ("auth_8021x_eap_fail", 0.85),
            ("ap_roam_rekey_fail", 0.10),
            ("radius_timeout", 0.05),
        ],
        headline="Pre-narrator stub",
        suggested_fix="Pre-narrator stub: narrator will replace.",
        evidence=[],
    )
    narrated = stub_verdict.model_copy(
        update={
            "headline": "Your school's 802.1X session failed",
            "suggested_fix": "Re-enter your school credentials",
        }
    )
    mocker.patch("agent.inference._predict_verdict_impl", return_value=stub_verdict)
    mocker.patch("agent.inference.narrate_templated", return_value=narrated)
    mocker.patch("agent.model_loader.path_for", return_value="/tmp/fake.joblib")

    from agent.inference import run_local_inference

    window = _make_window(synthetic_frame, n=30)
    verdict = run_local_inference(window)
    assert isinstance(verdict, Verdict)
    assert len(verdict.top_k) >= 1
    assert verdict.headline  # filled by narrator


def test_inference_does_not_import_anthropic(tmp_state_dir, synthetic_frame):
    """AGENT-05 — the agent's local-only mode must never import anthropic."""
    # Force a fresh import of agent.inference so the assertion sees a clean state.
    if "agent.inference" in sys.modules:
        del sys.modules["agent.inference"]
    if "anthropic" in sys.modules:
        del sys.modules["anthropic"]
    try:
        import agent.inference  # noqa: F401
    except ModuleNotFoundError:
        # Acceptable if numpy/pandas/etc. aren't installed yet — the point of
        # this test is the assertion below.
        pytest.skip(
            "agent.inference dependencies not installed; skipping anthropic-absence smoke"
        )
    assert "anthropic" not in sys.modules, (
        "AGENT-05 violation: agent.inference must NOT import anthropic; "
        "the narrator's [llm] extra is intentionally not in agent deps."
    )


def test_predict_verdict_loads_real_artifact(
    tmp_state_dir, synthetic_frame, tmp_path
):
    """Non-mocked integration: verifies _predict_verdict_impl was actually vendored.

    Builds a tiny fixture classifier with the same predict_proba contract as the
    real CalibratedClassifierCV-wrapped LightGBM (n_features matches the vendored
    CLASSIFIER_FEATURES; n_classes matches CLASSES). Drives it through the
    vendored function body and asserts top_k is a full ranking summing to 1.

    This is the gate that catches "did the executor actually paste the body?".
    """
    import joblib
    import numpy as np

    # HistGradientBoostingClassifier handles NaN natively, matching the real
    # LightGBM-backed CalibratedClassifierCV's NaN-tolerant contract — the
    # synthetic_frame fixture has several optional telemetry fields set to None,
    # which pandas surfaces as NaN inside _frames_to_array. LogisticRegression
    # would reject the NaN-bearing input; HistGradientBoosting passes through.
    from sklearn.ensemble import HistGradientBoostingClassifier

    from agent.inference import CLASSES, CLASSIFIER_FEATURES, _predict_verdict_impl

    n_features = len(CLASSIFIER_FEATURES)
    n_classes = len(CLASSES)
    rng = np.random.default_rng(42)
    X_train = rng.normal(size=(50, n_features))
    # Each class appears at least once so the classifier sees all 10 classes.
    y_train = np.tile(np.arange(n_classes), 5)[:50]
    clf = HistGradientBoostingClassifier(max_iter=50, random_state=42).fit(
        X_train, y_train
    )
    artifact_path = tmp_path / "classifier.joblib"
    joblib.dump(clf, artifact_path)

    # Window whose last frame has network_mode="enterprise" (8 classes enabled).
    window = [
        synthetic_frame.model_copy(update={"network_mode": "enterprise"})
        for _ in range(5)
    ]

    verdict = _predict_verdict_impl(artifact_path, window)

    # Real-vendoring assertions — these would fail on a NotImplementedError stub
    # AND would fail on a constant/hardcoded return.
    assert isinstance(verdict, Verdict)
    assert len(verdict.top_k) == n_classes, (
        f"top_k must be full ranking (K={n_classes}); got {len(verdict.top_k)}"
    )
    assert all(0.0 <= float(p) <= 1.0 for _, p in verdict.top_k)
    total = sum(float(p) for _, p in verdict.top_k)
    assert abs(total - 1.0) < 1e-6, (
        f"top_k probabilities must sum to 1; got {total}"
    )
    # top_class corresponds to the highest top_k entry (verbatim vendor behavior).
    assert verdict.top_class == verdict.top_k[0][0]
    assert verdict.confidence == verdict.top_k[0][1]
