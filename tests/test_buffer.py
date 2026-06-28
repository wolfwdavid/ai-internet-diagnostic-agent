"""Wave 0 RED-state tests for the rolling 120s buffer (D-AGENT-01, D-AGENT-04)."""

from __future__ import annotations

import time

from agent.buffer import (
    append_frame,
    flag_drop,
    list_flagged_drops,
    mark_diagnosed,
    snapshot_recent,
)


def test_append_and_snapshot_roundtrip(tmp_state_dir, synthetic_frame):
    for _ in range(5):
        append_frame(synthetic_frame)
    frames = snapshot_recent(120)
    assert len(frames) == 5


def test_snapshot_window_trims_to_120s(tmp_state_dir, synthetic_frame):
    # Build a frame with timestamp 240s in the past — should NOT appear in 120s snapshot
    old = synthetic_frame.model_copy(update={"timestamp": time.time() - 240})
    new = synthetic_frame.model_copy(update={"timestamp": time.time()})
    append_frame(old)
    append_frame(new)
    frames = snapshot_recent(120)
    assert len(frames) == 1


def test_flag_drop_and_list(tmp_state_dir):
    drop_id = flag_drop(time.time(), "icmp_loss_3consec")
    assert isinstance(drop_id, int) and drop_id > 0
    drops = list_flagged_drops(only_undiagnosed=True)
    assert any(d["id"] == drop_id and d["reason"] == "icmp_loss_3consec" for d in drops)
    mark_diagnosed(drop_id)
    assert all(d["id"] != drop_id for d in list_flagged_drops(only_undiagnosed=True))
