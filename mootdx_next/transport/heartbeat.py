from __future__ import annotations

from mootdx_next.models import RequestContext


def build_heartbeat_context(timeout_ms: int | None = None) -> RequestContext:
    return RequestContext(api="heartbeat", timeout_ms=timeout_ms or 15000)
