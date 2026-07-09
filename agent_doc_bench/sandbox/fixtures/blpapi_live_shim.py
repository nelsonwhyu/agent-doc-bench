"""Instrumentation for live-mode BLPAPI execution (BLOOMBERG_MODE=live).

Wraps the real `blpapi.Session` so the execution scorer can observe
*structural* metadata about a session against a real Bloomberg Terminal —
event types, message counts, elapsed time between calls — without ever
touching the actual field values (prices, security data, ...) a generated
script requests or receives. Nothing captured here is a data point; it is
purely session/event shape and timing, safe to report to LangSmith.

Copied into the sandbox tmp dir and imported by live_runner.py — never
imported by agent_doc_bench itself, and never used in mock mode.
"""

from __future__ import annotations

import json
import time
from pathlib import Path


class _Recorder:
    def __init__(self) -> None:
        self.events: list[dict] = []
        self._last_ts = time.monotonic()

    def record(self, kind: str, **fields) -> None:
        now = time.monotonic()
        try:
            self.events.append(
                {"kind": kind, "elapsed_ms": round((now - self._last_ts) * 1000, 2), **fields}
            )
        except Exception:
            pass
        self._last_ts = now

    def flush(self, path: Path) -> None:
        path.write_text(json.dumps(self.events, indent=2))


def install(metadata_path: Path) -> _Recorder:
    """Monkeypatch blpapi.Session to record shape/timing only.

    Every wrapped method must be a thin pass-through on failure — if
    instrumentation itself raises, the generated script's own behavior
    must not be affected, so all recording is best-effort and swallows
    its own errors.
    """
    import blpapi

    recorder = _Recorder()
    real_start = blpapi.Session.start
    real_stop = blpapi.Session.stop
    real_send_request = blpapi.Session.sendRequest
    real_next_event = blpapi.Session.nextEvent

    def start(self, *args, **kwargs):
        result = real_start(self, *args, **kwargs)
        recorder.record("session_start", ok=bool(result))
        return result

    def stop(self, *args, **kwargs):
        result = real_stop(self, *args, **kwargs)
        recorder.record("session_stop")
        return result

    def send_request(self, request, *args, **kwargs):
        result = real_send_request(self, request, *args, **kwargs)
        recorder.record("send_request", request_type=type(request).__name__)
        return result

    def next_event(self, *args, **kwargs):
        event = real_next_event(self, *args, **kwargs)
        try:
            message_count = sum(1 for _ in event)
        except Exception:
            message_count = None
        recorder.record("event", event_type=int(event.eventType()), message_count=message_count)
        return event

    blpapi.Session.start = start
    blpapi.Session.stop = stop
    blpapi.Session.sendRequest = send_request
    blpapi.Session.nextEvent = next_event

    return recorder
