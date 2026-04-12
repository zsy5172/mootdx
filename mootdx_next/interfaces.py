from __future__ import annotations

from abc import ABC
from abc import abstractmethod
from typing import Any

from mootdx_next.models import RequestContext
from mootdx_next.models import ResponseEnvelope
from mootdx_next.models import ServerEndpoint
from mootdx_next.models import TransportMetrics


class AbstractTransport(ABC):
    @abstractmethod
    def connect(self, server: ServerEndpoint, timeout_ms: int | None = None) -> None:
        raise NotImplementedError

    @abstractmethod
    def close(self) -> None:
        raise NotImplementedError

    @abstractmethod
    def is_connected(self) -> bool:
        raise NotImplementedError

    @abstractmethod
    def send(self, context: RequestContext, payload: bytes, server: ServerEndpoint) -> ResponseEnvelope:
        raise NotImplementedError


class AbstractProtocol(ABC):
    @abstractmethod
    def encode(self, api: str, **kwargs: Any) -> bytes:
        raise NotImplementedError

    @abstractmethod
    def decode(self, api: str, envelope: ResponseEnvelope, **kwargs: Any) -> object:
        raise NotImplementedError


class AbstractScheduler(ABC):
    @abstractmethod
    def select_server(
        self,
        context: RequestContext,
        excluded: set[tuple[str, int]] | None = None,
    ) -> ServerEndpoint:
        raise NotImplementedError

    @abstractmethod
    def record_success(self, server: ServerEndpoint, metrics: TransportMetrics) -> None:
        raise NotImplementedError

    @abstractmethod
    def record_failure(self, server: ServerEndpoint, exc: Exception) -> None:
        raise NotImplementedError
