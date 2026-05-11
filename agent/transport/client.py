"""Tenacity-wrapped gradio_client.Client.submit for live_diagnose (AGENT-08).

Retry filter derived from agent/transport/EXCEPTION_NOTES.md observations
(httpx.ConnectError surfaced by the Wave-0 probe against an unreachable URL)
PLUS the documented httpx transient set (ReadTimeout, ConnectTimeout,
RemoteProtocolError).

Design rules (RESEARCH §Pattern 2 + Gotcha 2):
  - Tenacity wraps Client construction AND client.submit() ONLY.
  - The ``for chunk in job:`` iteration loop is NOT @retry-wrapped because
    retrying would re-execute the entire generator on the Space (re-running
    classifier + narrator + Anthropic spend). The application-level replay
    cursor (agent/transport/replay.py) is the right resume mechanism.
  - The Client instance is cached by space_id (D-STATUS-10 long-lived session)
    so the second diagnosis avoids the cold-start cost.
  - Schema-mismatch (state=schema_mismatch yielded by the Space) is a
    PermanentTransportError -- DO NOT retry.
"""
from __future__ import annotations

from typing import Iterator

import httpx
from gradio_client import Client
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)
from wifi_diag_schema import TelemetryFrame

from agent.transport.errors import (
    PermanentTransportError,
    TransientTransportError,
)
from agent.transport.handshake import build_handshake_json
from agent.transport.replay import (
    load_last_acked_ts,
    save_last_acked_ts,
)

# Module-level Client cache for D-STATUS-10 long-lived session reuse.
_CLIENT_CACHE: dict[str, Client] = {}

# Transient exception classes:
#   - Wave-0 probe (EXCEPTION_NOTES.md): httpx.ConnectError surfaces on
#     unreachable / sleeping Space.
#   - Documented httpx transients: ConnectTimeout, ReadTimeout,
#     RemoteProtocolError -- network blips during the wake/handshake window.
_TRANSIENT_HTTPX: tuple[type[Exception], ...] = (
    httpx.ConnectError,
    httpx.ConnectTimeout,
    httpx.ReadTimeout,
    httpx.RemoteProtocolError,
)


def _classify_and_raise(e: Exception) -> None:
    """Translate raw gradio_client / httpx exceptions into TransientTransportError
    or PermanentTransportError per RESEARCH §Pattern 2.

    Permanent (no retry):
      - Schema-mismatch (message contains 'schema' + 'mismatch'|'incompat').

    Transient (retry):
      - Anything in _TRANSIENT_HTTPX.
      - Default: unknown -> treat as transient ONCE per RESEARCH §Pattern 2.
    """
    msg = str(e).lower()
    if "schema" in msg and ("mismatch" in msg or "incompat" in msg):
        raise PermanentTransportError(str(e)) from e
    if isinstance(e, _TRANSIENT_HTTPX):
        raise TransientTransportError(str(e)) from e
    # Default: treat unknown as transient ONCE so tenacity decides whether to
    # retry. After exhaustion the caller sees the wrapped exception.
    raise TransientTransportError(
        f"unknown: {type(e).__name__}: {e}"
    ) from e


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=60),
    retry=retry_if_exception_type(TransientTransportError),
    reraise=True,
)
def _connect_and_submit(
    space_id: str,
    handshake_json: str,
    frames_json_list: list[str],
    owner_key: str | None,
    pair_code: str | None,
):
    """Construct (or reuse) a Client and submit() once; tenacity-wrapped.

    Retries 3 times total with wait_exponential(min=2, max=60); 2 + 4 + 8 = 14s
    of wait covers the typical HF Space cold-start window of ~10-30s, plus
    headroom for the rare 60s wake. Permanent errors (schema mismatch) raise
    immediately and are NOT retried.

    Returns (client, job) -- both held by the caller so D-STATUS-10 long-lived
    session reuse works across drops.
    """
    client = _CLIENT_CACHE.get(space_id)
    if client is None:
        try:
            client = Client(space_id, verbose=False)
        except (PermanentTransportError, TransientTransportError):
            raise
        except Exception as e:  # noqa: BLE001 -- classify before raising
            _classify_and_raise(e)
        _CLIENT_CACHE[space_id] = client
    try:
        job = client.submit(
            handshake_json,
            frames_json_list,
            owner_key,
            pair_code,
            api_name="/live_diagnose",
        )
    except (PermanentTransportError, TransientTransportError):
        raise
    except Exception as e:  # noqa: BLE001
        _classify_and_raise(e)
    return client, job


def stream_diagnose(
    space_id: str,
    frames: list[TelemetryFrame],
    owner_key: str | None,
    pair_code: str | None = None,
) -> Iterator[dict]:
    """Generator that yields chunks from the Space's live_diagnose SSE.

    Cursor-aware:
      - Only frames with timestamp > last_acked_ts are sent to the Space.
      - Cursor advances on each state=streaming yield.
      - On state=complete, cursor pins to the max ts in the sent window so a
        fresh ``agent diagnose --cloud`` invocation starts clean.

    Raises PermanentTransportError on state=schema_mismatch (fail-fast UX).

    No retry on the for-chunk-in-job loop (Gotcha 2). Application-level replay
    in the caller (CLI re-invokes ``stream_diagnose`` after tenacity exhausts).
    """
    cursor = load_last_acked_ts()
    last_ack = cursor.get("last_acked_ts", 0.0)
    to_send = [f for f in frames if f.timestamp > last_ack]
    frames_json = [f.model_dump_json() for f in to_send]
    handshake_json = build_handshake_json()

    client, job = _connect_and_submit(
        space_id, handshake_json, frames_json, owner_key, pair_code
    )
    session_hash = getattr(client, "session_hash", None) or getattr(
        job, "session_hash", None
    )
    # session_hash must be JSON-serializable for the cursor file. Coerce
    # non-string values (e.g. MagicMock under test, missing attributes) to None.
    if not isinstance(session_hash, str):
        session_hash = None

    for chunk in job:
        if not isinstance(chunk, dict):
            yield {"state": "_raw", "payload": chunk}
            continue
        state = chunk.get("state")
        if state == "schema_mismatch":
            raise PermanentTransportError(chunk.get("error", "schema mismatch"))
        if state == "streaming":
            idx = int(chunk.get("frame_index", 1)) - 1
            if 0 <= idx < len(to_send):
                save_last_acked_ts(
                    to_send[idx].timestamp, session_hash=session_hash
                )
        if state == "complete" and to_send:
            save_last_acked_ts(
                max(f.timestamp for f in to_send),
                session_hash=session_hash,
            )
        yield chunk


__all__ = [
    "_CLIENT_CACHE",
    "_classify_and_raise",
    "_connect_and_submit",
    "stream_diagnose",
]
