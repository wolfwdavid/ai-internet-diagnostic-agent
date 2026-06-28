"""ai-internet-diagnostic-agent — cross-platform local agent."""

import sys

__version__ = "0.4.0"

# Windows consoles default to a legacy code page (e.g. cp1252) that can't encode
# the status glyphs (✓ ⚠ ✗) the doctor/status tables render, which crashes the
# CLI with UnicodeEncodeError. Force UTF-8 on the standard streams at import time
# so every command renders identically across OSes without an env-var workaround.
for _stream in (sys.stdout, sys.stderr):
    _reconfigure = getattr(_stream, "reconfigure", None)
    if _reconfigure is not None:
        try:
            _reconfigure(encoding="utf-8")
        except (ValueError, OSError):  # pragma: no cover - detached/closed stream
            pass
