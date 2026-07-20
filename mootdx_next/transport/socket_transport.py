from __future__ import annotations

import socket
import struct
import time
import zlib
from typing import Callable
from typing import Sequence

from mootdx_next.errors import EmptyResponseError
from mootdx_next.errors import InvalidResponseHeaderError
from mootdx_next.errors import PayloadDecompressionError
from mootdx_next.errors import TransportConnectionError
from mootdx_next.errors import TransportTimeoutError
from mootdx_next.interfaces import AbstractTransport
from mootdx_next.models import RequestContext
from mootdx_next.models import ResponseEnvelope
from mootdx_next.models import ResponseHeader
from mootdx_next.models import ServerEndpoint
from mootdx_next.models import TransportMetrics
from mootdx_next.transport.constants import DEFAULT_CONNECT_TIMEOUT_MS
from mootdx_next.transport.constants import RSP_HEADER_LEN
from mootdx_next.transport.constants import STD_SETUP_PAYLOADS
from mootdx_next.transport.heartbeat import build_heartbeat_context


class SyncSocketTransport(AbstractTransport):
    def __init__(
        self,
        socket_factory: Callable[[int, int], socket.socket] | None = None,
        time_fn: Callable[[], float] | None = None,
        setup_payloads: Sequence[bytes] | None = STD_SETUP_PAYLOADS,
    ) -> None:
        self._socket_factory = socket_factory or socket.socket
        self._time_fn = time_fn or time.perf_counter
        self._setup_payloads = tuple(setup_payloads or ())
        self._sock: socket.socket | None = None
        self._server: ServerEndpoint | None = None
        self.metrics = TransportMetrics()

    def connect(self, server: ServerEndpoint, timeout_ms: int | None = None) -> None:
        if self._server == server and self.is_connected():
            return

        if self.is_connected():
            self.metrics.reconnect_count += 1
            self.close()

        timeout_s = (timeout_ms or DEFAULT_CONNECT_TIMEOUT_MS) / 1000
        sock = self._open_socket(server, timeout_s)
        self._sock = sock
        try:
            self._perform_setup()
        except Exception:
            self.close()
            raise
        else:
            self._server = server
            self.metrics.connect_count += 1

    def close(self) -> None:
        if self._sock is None:
            self._server = None
            return

        try:
            self._sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass

        try:
            self._sock.close()
        finally:
            self._sock = None
            self._server = None

    def is_connected(self) -> bool:
        return self._sock is not None and self._server is not None

    def send(self, context: RequestContext, payload: bytes, server: ServerEndpoint) -> ResponseEnvelope:
        started = self._time_fn()

        try:
            self.connect(server, timeout_ms=context.timeout_ms)
            assert self._sock is not None
            header, decoded_body, wire_body_size = self._exchange_payload(payload)

            elapsed_ms = (self._time_fn() - started) * 1000
            self._mark_success(len(header.raw) + wire_body_size, elapsed_ms)

            return ResponseEnvelope(
                header=header,
                body=decoded_body,
                server=server,
                elapsed_ms=elapsed_ms,
            )
        except (socket.timeout, TransportTimeoutError) as exc:
            self._mark_failure(timeout_error=True)
            self.close()
            raise TransportTimeoutError(str(exc)) from exc
        except (InvalidResponseHeaderError, EmptyResponseError, PayloadDecompressionError):
            self._mark_failure()
            self.close()
            raise
        except (OSError, TransportConnectionError) as exc:
            self._mark_failure()
            self.close()
            raise TransportConnectionError(str(exc)) from exc

    def send_heartbeat(
        self,
        payload: bytes,
        server: ServerEndpoint,
        timeout_ms: int | None = None,
    ) -> ResponseEnvelope:
        self.metrics.heartbeat_count += 1
        context = build_heartbeat_context(timeout_ms=timeout_ms)
        return self.send(context, payload, server)

    def _open_socket(self, server: ServerEndpoint, timeout_s: float) -> socket.socket:
        sock = self._socket_factory(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout_s)
        try:
            sock.connect((server.host, server.port))
        except socket.timeout as exc:
            try:
                sock.close()
            finally:
                raise TransportTimeoutError(str(exc)) from exc
        except OSError as exc:
            try:
                sock.close()
            finally:
                raise TransportConnectionError(str(exc)) from exc

        return sock

    def _perform_setup(self) -> None:
        for payload in self._setup_payloads:
            self._exchange_payload(payload, count_request=False)

    def _exchange_payload(
        self,
        payload: bytes,
        count_request: bool = True,
    ) -> tuple[ResponseHeader, bytes, int]:
        assert self._sock is not None
        self._sock.sendall(payload)
        self.metrics.bytes_sent += len(payload)
        if count_request:
            self.metrics.sent_requests += 1

        header = self._read_header()
        body = self._read_body(header)
        decoded_body = self._decode_body(header, body)
        return header, decoded_body, len(body)

    def _recv_exact(self, size: int) -> bytes:
        if size < 0:
            raise InvalidResponseHeaderError("response size cannot be negative")

        if size == 0:
            return b""

        assert self._sock is not None

        chunks = bytearray()
        while len(chunks) < size:
            chunk = self._sock.recv(size - len(chunks))
            if not chunk:
                break
            chunks.extend(chunk)

        return bytes(chunks)

    def _read_header(self) -> ResponseHeader:
        raw = self._recv_exact(RSP_HEADER_LEN)
        if not raw:
            raise EmptyResponseError("server returned an empty response header")

        if len(raw) != RSP_HEADER_LEN:
            raise InvalidResponseHeaderError(f"expected {RSP_HEADER_LEN} header bytes, got {len(raw)}")

        try:
            reserved_1, reserved_2, reserved_3, compressed_size, uncompressed_size = struct.unpack("<IIIHH", raw)
        except struct.error as exc:
            raise InvalidResponseHeaderError("failed to parse response header") from exc

        if compressed_size < 0 or uncompressed_size < 0:
            raise InvalidResponseHeaderError("response sizes cannot be negative")

        return ResponseHeader(
            raw=raw,
            reserved_1=reserved_1,
            reserved_2=reserved_2,
            reserved_3=reserved_3,
            compressed_size=compressed_size,
            uncompressed_size=uncompressed_size,
        )

    def _read_body(self, header: ResponseHeader) -> bytes:
        if header.compressed_size == 0:
            return b""

        body = self._recv_exact(header.compressed_size)
        if not body:
            raise EmptyResponseError("server returned an empty response body")

        if len(body) != header.compressed_size:
            raise EmptyResponseError(
                f"expected {header.compressed_size} body bytes, got {len(body)}"
            )

        return body

    def _decode_body(self, header: ResponseHeader, body: bytes) -> bytes:
        if header.compressed_size == 0:
            return body

        if header.compressed_size == header.uncompressed_size:
            return body

        try:
            decoded = zlib.decompress(body)
        except zlib.error as exc:
            raise PayloadDecompressionError("failed to decompress response payload") from exc

        if len(decoded) != header.uncompressed_size:
            raise PayloadDecompressionError(
                f"expected {header.uncompressed_size} decompressed bytes, got {len(decoded)}"
            )

        return decoded

    def _mark_success(self, bytes_received: int, elapsed_ms: float) -> None:
        self.metrics.bytes_received += bytes_received
        self.metrics.last_latency_ms = elapsed_ms

    def _mark_failure(self, timeout_error: bool = False) -> None:
        self.metrics.failed_requests += 1
        if timeout_error:
            self.metrics.timeout_count += 1
