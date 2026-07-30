from __future__ import annotations

import threading
import time
from collections.abc import Callable
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import as_completed
from dataclasses import dataclass

from mootdx_next.constants import EX_HOSTS
from mootdx_next.constants import HQ_HOSTS
from mootdx_next.models import RequestContext
from mootdx_next.models import ServerEndpoint
from mootdx_next.protocol.std_quotes import StdQuoteProtocol
from mootdx_next.protocol.ex_quotes import ExQuoteProtocol
from mootdx_next.transport.constants import EX_SETUP_PAYLOADS
from mootdx_next.transport.socket_transport import SyncSocketTransport

HQ_CANDIDATE_TTL_SECONDS = 10 * 60
HQ_CANDIDATE_LIMIT = 5
HQ_PROBE_TIMEOUT_MS = 1200
HQ_PROBE_WORKERS = 12
HQ_PROBE_SYMBOL = "600036"
EX_CANDIDATE_TTL_SECONDS = 10 * 60
EX_CANDIDATE_LIMIT = 5
EX_PROBE_TIMEOUT_MS = 1500
EX_PROBE_WORKERS = 12
EX_PROBE_MARKET = 31
EX_PROBE_SYMBOL = "00700"


@dataclass(frozen=True, slots=True)
class ServerCandidate:
    host: str
    port: int
    label: str | None = None
    latency_ms: float | None = None


CandidateSnapshot = tuple[ServerCandidate, ...]
CandidateLoader = Callable[[], Iterable[ServerCandidate]]


class CandidateRegistry:
    def __init__(
        self,
        loader: CandidateLoader,
        ttl_seconds: float = HQ_CANDIDATE_TTL_SECONDS,
        time_fn: Callable[[], float] = time.monotonic,
    ) -> None:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be greater than zero")

        self._loader = loader
        self._ttl_seconds = float(ttl_seconds)
        self._time_fn = time_fn
        self._condition = threading.Condition()
        self._candidates: CandidateSnapshot = ()
        self._expires_at = 0.0
        self._loaded = False
        self._refreshing = False

    @property
    def ttl_seconds(self) -> float:
        return self._ttl_seconds

    def get(self) -> CandidateSnapshot:
        return self._load(force=False)

    def refresh(self) -> CandidateSnapshot:
        return self._load(force=True)

    def invalidate(self) -> None:
        with self._condition:
            self._expires_at = 0.0

    def snapshot(self) -> CandidateSnapshot:
        with self._condition:
            return self._candidates

    def _load(self, force: bool) -> CandidateSnapshot:
        with self._condition:
            if self._refreshing:
                self._condition.wait_for(lambda: not self._refreshing)
                if self._loaded:
                    return self._candidates

            if not force and self._loaded and self._time_fn() < self._expires_at:
                return self._candidates

            self._refreshing = True

        try:
            candidates = self._normalize(self._loader())
        except BaseException:
            with self._condition:
                self._refreshing = False
                self._condition.notify_all()
            raise

        with self._condition:
            self._candidates = candidates
            self._expires_at = self._time_fn() + self._ttl_seconds
            self._loaded = True
            self._refreshing = False
            self._condition.notify_all()
            return self._candidates

    @staticmethod
    def _normalize(candidates: Iterable[ServerCandidate]) -> CandidateSnapshot:
        normalized: list[ServerCandidate] = []
        seen: set[tuple[str, int]] = set()

        for candidate in candidates:
            item = ServerCandidate(
                host=str(candidate.host),
                port=int(candidate.port),
                label=candidate.label,
                latency_ms=None if candidate.latency_ms is None else float(candidate.latency_ms),
            )
            key = (item.host, item.port)
            if key in seen:
                continue
            seen.add(key)
            normalized.append(item)

        return tuple(normalized)


def _probe_hq_candidates() -> CandidateSnapshot:
    candidates: list[ServerCandidate] = []
    worker_count = min(HQ_PROBE_WORKERS, len(HQ_HOSTS))
    with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="mootdx-next-probe") as executor:
        futures = {
            executor.submit(_probe_one_hq_candidate, label, host, port): (label, host, port)
            for label, host, port in HQ_HOSTS
        }
        for future in as_completed(futures):
            try:
                candidate = future.result()
            except Exception:
                continue
            if candidate is not None:
                candidates.append(candidate)

    candidates.sort(key=lambda item: item.latency_ms if item.latency_ms is not None else float("inf"))
    return tuple(candidates[:HQ_CANDIDATE_LIMIT])


def _probe_one_hq_candidate(label: str, host: str, port: int) -> ServerCandidate | None:
    return probe_hq_candidate(label, host, port)


def probe_hq_candidate(label: str, host: str, port: int) -> ServerCandidate | None:
    """Probe one standard-market endpoint for both quote and bar capabilities."""

    protocol = StdQuoteProtocol()
    transport = SyncSocketTransport()
    endpoint = ServerEndpoint(host=host, port=int(port), label=label)
    started = time.perf_counter()
    try:
        quote_context = RequestContext(
            api="candidate_quotes",
            params={"symbol": HQ_PROBE_SYMBOL},
            timeout_ms=HQ_PROBE_TIMEOUT_MS,
        )
        quote_payload = protocol.encode("quotes", symbols=[(1, HQ_PROBE_SYMBOL)])
        quote_envelope = transport.send(quote_context, quote_payload, endpoint)
        quotes = protocol.decode("quotes", quote_envelope)
        if not quotes:
            return None

        bars_context = RequestContext(
            api="candidate_bars",
            params={"symbol": HQ_PROBE_SYMBOL, "frequency": 9},
            timeout_ms=HQ_PROBE_TIMEOUT_MS,
        )
        bars_payload = protocol.encode(
            "bars",
            frequency=9,
            market=1,
            code=HQ_PROBE_SYMBOL,
            start=0,
            count=1,
        )
        bars_envelope = transport.send(bars_context, bars_payload, endpoint)
        bars = protocol.decode("bars", bars_envelope, frequency=9)
        if not bars:
            return None
    finally:
        transport.close()

    return ServerCandidate(
        host=host,
        port=int(port),
        label=label,
        latency_ms=(time.perf_counter() - started) * 1000,
    )


def _probe_ex_candidates() -> CandidateSnapshot:
    candidates: list[ServerCandidate] = []
    worker_count = min(EX_PROBE_WORKERS, len(EX_HOSTS))
    with ThreadPoolExecutor(
        max_workers=worker_count,
        thread_name_prefix="mootdx-next-ex-probe",
    ) as executor:
        futures = {
            executor.submit(probe_ex_candidate, label, host, port): (label, host, port)
            for label, host, port in EX_HOSTS
        }
        for future in as_completed(futures):
            try:
                candidate = future.result()
            except Exception:
                continue
            if candidate is not None:
                candidates.append(candidate)

    candidates.sort(key=lambda item: item.latency_ms if item.latency_ms is not None else float("inf"))
    return tuple(candidates[:EX_CANDIDATE_LIMIT])


def probe_ex_candidate(label: str, host: str, port: int) -> ServerCandidate | None:
    """Probe an ExHq endpoint beyond TCP reachability.

    A candidate must decode the market table and instrument count and return a
    real Hong Kong daily bar.  This excludes nodes that accept a socket but do
    not implement the complete extended-market protocol.
    """

    protocol = ExQuoteProtocol()
    transport = SyncSocketTransport(setup_payloads=EX_SETUP_PAYLOADS)
    endpoint = ServerEndpoint(host=host, port=int(port), label=label)
    started = time.perf_counter()
    try:
        markets_context = RequestContext(
            api="candidate_ex_markets",
            params={},
            timeout_ms=EX_PROBE_TIMEOUT_MS,
        )
        markets_envelope = transport.send(
            markets_context,
            protocol.encode("markets"),
            endpoint,
        )
        markets = protocol.decode("markets", markets_envelope)
        if not any(int(row["market"]) == EX_PROBE_MARKET for row in markets):
            return None

        count_context = RequestContext(
            api="candidate_ex_instrument_count",
            params={},
            timeout_ms=EX_PROBE_TIMEOUT_MS,
        )
        count_envelope = transport.send(
            count_context,
            protocol.encode("instrument_count"),
            endpoint,
        )
        if int(protocol.decode("instrument_count", count_envelope)) <= 0:
            return None

        bars_context = RequestContext(
            api="candidate_ex_bars",
            params={"market": EX_PROBE_MARKET, "symbol": EX_PROBE_SYMBOL},
            timeout_ms=EX_PROBE_TIMEOUT_MS,
        )
        bars_payload = protocol.encode(
            "bars",
            category=9,
            market=EX_PROBE_MARKET,
            code=EX_PROBE_SYMBOL,
            start=0,
            count=1,
        )
        bars_envelope = transport.send(bars_context, bars_payload, endpoint)
        bars = protocol.decode("bars", bars_envelope, category=9)
        if not bars or float(bars[0]["close"]) <= 0:
            return None
    finally:
        transport.close()

    return ServerCandidate(
        host=host,
        port=int(port),
        label=label,
        latency_ms=(time.perf_counter() - started) * 1000,
    )


_hq_candidate_registry = CandidateRegistry(_probe_hq_candidates)
_ex_candidate_registry = CandidateRegistry(
    _probe_ex_candidates,
    ttl_seconds=EX_CANDIDATE_TTL_SECONDS,
)


def get_hq_candidates() -> CandidateSnapshot:
    return _hq_candidate_registry.get()


def refresh_hq_candidates() -> CandidateSnapshot:
    return _hq_candidate_registry.refresh()


def invalidate_hq_candidates() -> None:
    _hq_candidate_registry.invalidate()


def hq_candidate_snapshot() -> CandidateSnapshot:
    return _hq_candidate_registry.snapshot()


def get_ex_candidates() -> CandidateSnapshot:
    return _ex_candidate_registry.get()


def refresh_ex_candidates() -> CandidateSnapshot:
    return _ex_candidate_registry.refresh()


def invalidate_ex_candidates() -> None:
    _ex_candidate_registry.invalidate()


def ex_candidate_snapshot() -> CandidateSnapshot:
    return _ex_candidate_registry.snapshot()
