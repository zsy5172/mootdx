from __future__ import annotations

import threading
import time
from collections.abc import Callable
from collections.abc import Iterable
from dataclasses import dataclass

HQ_CANDIDATE_TTL_SECONDS = 10 * 60
HQ_CANDIDATE_LIMIT = 5


@dataclass(frozen=True, slots=True)
class ServerCandidate:
    host: str
    port: int
    label: str | None = None


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
            )
            key = (item.host, item.port)
            if key in seen:
                continue
            seen.add(key)
            normalized.append(item)

        return tuple(normalized)


def _probe_hq_candidates() -> CandidateSnapshot:
    from mootdx.consts import HQ_HOSTS
    from mootdx.server import server as probe_servers

    addresses = probe_servers(index="HQ", limit=HQ_CANDIDATE_LIMIT, console=False, sync=False)
    labels = {(host, int(port)): label for label, host, port in HQ_HOSTS}
    return tuple(
        ServerCandidate(host=host, port=int(port), label=labels.get((host, int(port)), "std-next"))
        for host, port in addresses
    )


_hq_candidate_registry = CandidateRegistry(_probe_hq_candidates)


def get_hq_candidates() -> CandidateSnapshot:
    return _hq_candidate_registry.get()


def refresh_hq_candidates() -> CandidateSnapshot:
    return _hq_candidate_registry.refresh()


def invalidate_hq_candidates() -> None:
    _hq_candidate_registry.invalidate()


def hq_candidate_snapshot() -> CandidateSnapshot:
    return _hq_candidate_registry.snapshot()
