"""Per-install salt persistence + randomness (PRIV-01 / D-PRIV-04)."""
from __future__ import annotations

from pathlib import Path

import platformdirs

from agent.salt import load_or_create_salt


def test_salt_persisted_across_calls(tmp_state_dir):
    a = load_or_create_salt()
    b = load_or_create_salt()
    assert a == b
    assert len(a) >= 32
    salt_path = Path(platformdirs.user_config_dir("wifi-diag")) / "salt.bin"
    assert salt_path.exists()


def test_salt_is_random(tmp_state_dir):
    salt = load_or_create_salt()
    # 32 random bytes; first 16 are extremely unlikely to be all zero
    assert salt[:16] != b"\x00" * 16
