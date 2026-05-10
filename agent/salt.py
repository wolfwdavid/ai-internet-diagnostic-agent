"""Per-install BSSID salt (D-PRIV-04 / Phase 1 D-03).

32 random bytes generated on first call, persisted at
``platformdirs.user_config_dir/wifi-diag/salt.bin`` (mode ``0o600`` on POSIX,
standard ACL on Windows). Subsequent calls return the same bytes.

The salt makes the BSSID hash deterministic per-install but unlinkable across
installs. Used by ``agent.redaction.bssid_hash``.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import platformdirs

SALT_LEN = 32


def _salt_path() -> Path:
    """Return the on-disk location for the per-install salt.

    Uses ``platformdirs.user_config_dir`` so tests can monkeypatch the
    location to a tmpdir for isolation (see tests/conftest.py
    ``tmp_state_dir`` fixture).
    """
    p = Path(platformdirs.user_config_dir("wifi-diag")) / "salt.bin"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def load_or_create_salt() -> bytes:
    """Read the existing salt or create a new one.

    On first call: writes 32 random bytes from ``os.urandom`` atomically
    (write-to-tmp + rename) with mode ``0o600`` on POSIX. Subsequent calls
    return the same bytes.
    """
    path = _salt_path()
    if path.exists() and path.stat().st_size >= SALT_LEN:
        return path.read_bytes()
    salt = os.urandom(SALT_LEN)
    # Atomic write: write-to-tmp, then rename. Avoids a partial-write window
    # if the process is killed mid-write.
    tmp = path.with_suffix(".bin.tmp")
    tmp.write_bytes(salt)
    if sys.platform != "win32":
        os.chmod(tmp, 0o600)
    tmp.replace(path)
    return salt
