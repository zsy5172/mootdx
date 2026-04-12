from __future__ import annotations

import socket
import struct
import zlib

import pytest

from mootdx_next.errors import EmptyResponseError
from mootdx_next.errors import InvalidResponseHeaderError
from mootdx_next.errors import PayloadDecompressionError
from mootdx_next.errors import TransportConnectionError
from mootdx_next.errors import TransportTimeoutError
from mootdx_next.models import RequestContext
from mootdx_next.models import ServerEndpoint
from mootdx_next.transport.socket_transport import SyncSocketTransport


def _pack_header(compressed_size: int, uncompressed_size: int) -> bytes:
    return struct.pack("<IIIHH", 1, 2, 3, compressed_size, uncompressed_size)


class FakeSocket:
    def __init__(
        self,
        recv_chunks: list[bytes] | None = None,
        connect_error: Exception | None = None,
        send_error: Exception | None = None,
        recv_error: Exception | None = None,
    ) -> None:
        self.recv_chunks = list(recv_chunks or [])
        self.connect_error = connect_error
        self.send_error = send_error
        self.recv_error = recv_error
        self.timeout = None
        self.connected_to = None
        self.closed = False
        self.shutdown_called = False
        self.sent_payloads: list[bytes] = []

    def settimeout(self, timeout: float) -> None:
        self.timeout = timeout

    def connect(self, address: tuple[str, int]) -> None:
        if self.connect_error is not None:
            raise self.connect_error
        self.connected_to = address

    def sendall(self, payload: bytes) -> None:
        if self.send_error is not None:
            raise self.send_error
        self.sent_payloads.append(payload)

    def recv(self, size: int) -> bytes:
        if self.recv_error is not None:
            raise self.recv_error
        if not self.recv_chunks:
            return b""

        chunk = self.recv_chunks.pop(0)
        if len(chunk) > size:
            self.recv_chunks.insert(0, chunk[size:])
            return chunk[:size]

        return chunk

    def shutdown(self, how: int) -> None:
        self.shutdown_called = True

    def close(self) -> None:
        self.closed = True


class SocketFactory:
    def __init__(self, sockets: list[FakeSocket]) -> None:
        self.sockets = list(sockets)
        self.created: list[FakeSocket] = []

    def __call__(self, family: int, type_: int) -> FakeSocket:
        assert family == socket.AF_INET
        assert type_ == socket.SOCK_STREAM
        if not self.sockets:
            raise AssertionError("no fake sockets remaining")
        sock = self.sockets.pop(0)
        self.created.append(sock)
        return sock


def test_send_reads_uncompressed_response() -> None:
    body = b"\x2a\x00"
    fake_socket = FakeSocket(recv_chunks=[_pack_header(len(body), len(body)), body])
    transport = SyncSocketTransport(socket_factory=SocketFactory([fake_socket]), setup_payloads=())
    server = ServerEndpoint(host="127.0.0.1", port=7709)

    envelope = transport.send(RequestContext(api="stock_count"), b"ping", server)

    assert envelope.header is not None
    assert envelope.header.compressed_size == len(body)
    assert envelope.body == body
    assert envelope.server == server
    assert envelope.elapsed_ms is not None
    assert fake_socket.sent_payloads == [b"ping"]
    assert transport.metrics.connect_count == 1
    assert transport.metrics.sent_requests == 1
    assert transport.metrics.bytes_sent == 4
    assert transport.metrics.bytes_received == len(body) + 16
    assert transport.metrics.failed_requests == 0


def test_send_decompresses_response_body() -> None:
    decoded_body = b"hello transport"
    compressed_body = zlib.compress(decoded_body)
    fake_socket = FakeSocket(
        recv_chunks=[_pack_header(len(compressed_body), len(decoded_body)), compressed_body]
    )
    transport = SyncSocketTransport(socket_factory=SocketFactory([fake_socket]), setup_payloads=())

    envelope = transport.send(
        RequestContext(api="stock_count"),
        b"payload",
        ServerEndpoint(host="127.0.0.1", port=7709),
    )

    assert envelope.body == decoded_body
    assert transport.metrics.bytes_received == len(compressed_body) + 16


def test_connect_reuses_same_server() -> None:
    factory = SocketFactory([FakeSocket()])
    transport = SyncSocketTransport(socket_factory=factory, setup_payloads=())
    server = ServerEndpoint(host="127.0.0.1", port=7709)

    transport.connect(server)
    transport.connect(server)

    assert len(factory.created) == 1
    assert transport.metrics.connect_count == 1
    assert transport.metrics.reconnect_count == 0


def test_connect_switches_server_and_reconnects() -> None:
    first_socket = FakeSocket()
    second_socket = FakeSocket()
    factory = SocketFactory([first_socket, second_socket])
    transport = SyncSocketTransport(socket_factory=factory, setup_payloads=())

    transport.connect(ServerEndpoint(host="127.0.0.1", port=7709))
    transport.connect(ServerEndpoint(host="127.0.0.2", port=7709))

    assert first_socket.closed is True
    assert second_socket.closed is False
    assert transport.metrics.connect_count == 2
    assert transport.metrics.reconnect_count == 1


def test_send_raises_on_truncated_header() -> None:
    transport = SyncSocketTransport(socket_factory=SocketFactory([FakeSocket(recv_chunks=[b"short"])]), setup_payloads=())

    with pytest.raises(InvalidResponseHeaderError):
        transport.send(
            RequestContext(api="stock_count"),
            b"payload",
            ServerEndpoint(host="127.0.0.1", port=7709),
        )

    assert transport.metrics.failed_requests == 1


def test_send_raises_on_premature_body_end() -> None:
    header = _pack_header(5, 5)
    fake_socket = FakeSocket(recv_chunks=[header, b"12"])
    transport = SyncSocketTransport(socket_factory=SocketFactory([fake_socket]), setup_payloads=())

    with pytest.raises(EmptyResponseError):
        transport.send(
            RequestContext(api="stock_count"),
            b"payload",
            ServerEndpoint(host="127.0.0.1", port=7709),
        )

    assert transport.metrics.failed_requests == 1


def test_send_raises_on_empty_header() -> None:
    transport = SyncSocketTransport(socket_factory=SocketFactory([FakeSocket(recv_chunks=[b""])]), setup_payloads=())

    with pytest.raises(EmptyResponseError):
        transport.send(
            RequestContext(api="stock_count"),
            b"payload",
            ServerEndpoint(host="127.0.0.1", port=7709),
        )


def test_send_raises_on_invalid_compressed_payload() -> None:
    broken_body = b"not-zlib"
    fake_socket = FakeSocket(recv_chunks=[_pack_header(len(broken_body), 20), broken_body])
    transport = SyncSocketTransport(socket_factory=SocketFactory([fake_socket]), setup_payloads=())

    with pytest.raises(PayloadDecompressionError):
        transport.send(
            RequestContext(api="stock_count"),
            b"payload",
            ServerEndpoint(host="127.0.0.1", port=7709),
        )


def test_send_maps_socket_timeout_to_transport_timeout() -> None:
    fake_socket = FakeSocket(send_error=socket.timeout("timed out"))
    transport = SyncSocketTransport(socket_factory=SocketFactory([fake_socket]), setup_payloads=())

    with pytest.raises(TransportTimeoutError):
        transport.send(
            RequestContext(api="stock_count"),
            b"payload",
            ServerEndpoint(host="127.0.0.1", port=7709),
        )

    assert transport.metrics.failed_requests == 1
    assert transport.metrics.timeout_count == 1


def test_send_maps_connect_error_to_transport_connection_error() -> None:
    fake_socket = FakeSocket(connect_error=OSError("connect failed"))
    transport = SyncSocketTransport(socket_factory=SocketFactory([fake_socket]), setup_payloads=())

    with pytest.raises(TransportConnectionError):
        transport.send(
            RequestContext(api="stock_count"),
            b"payload",
            ServerEndpoint(host="127.0.0.1", port=7709),
        )

    assert transport.metrics.failed_requests == 1


def test_send_heartbeat_reuses_send_path() -> None:
    body = b"\x2a\x00"
    fake_socket = FakeSocket(recv_chunks=[_pack_header(len(body), len(body)), body])
    transport = SyncSocketTransport(socket_factory=SocketFactory([fake_socket]), setup_payloads=())
    server = ServerEndpoint(host="127.0.0.1", port=7709)

    envelope = transport.send_heartbeat(b"hb", server, timeout_ms=1000)

    assert envelope.body == body
    assert transport.metrics.heartbeat_count == 1
    assert transport.metrics.sent_requests == 1
