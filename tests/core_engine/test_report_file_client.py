from __future__ import annotations

import struct

from mootdx_next.api.report_files import ReportFileClient
from mootdx_next.models import RequestContext
from mootdx_next.models import ResponseEnvelope
from mootdx_next.models import ServerEndpoint


class ReportTransport:
    def __init__(self, bodies: list[bytes]) -> None:
        self.bodies = list(bodies)
        self.calls: list[tuple[RequestContext, bytes, ServerEndpoint]] = []
        self.closed = False

    def send(self, context: RequestContext, payload: bytes, server: ServerEndpoint) -> ResponseEnvelope:
        self.calls.append((context, payload, server))
        return ResponseEnvelope(body=self.bodies.pop(0), server=server)

    def close(self) -> None:
        self.closed = True


def _body(data: bytes) -> bytes:
    return struct.pack("<I", len(data)) + data


def _client(*bodies: bytes) -> tuple[ReportFileClient, ReportTransport]:
    transport = ReportTransport(list(bodies))
    server = ServerEndpoint(host="127.0.0.1", port=7709, label="test")
    client = ReportFileClient(server=server, timeout_ms=3210, transport_factory=lambda: transport)
    return client, transport


def test_report_file_client_fetch_chunk_routes_filename_offset_and_timeout() -> None:
    client, transport = _client(_body(b"test"))

    result = client.fetch_chunk("tdxfin/gpcw.txt", offset=123)

    assert result == {"chunksize": 4, "chunkdata": b"test"}
    context, payload, server = transport.calls[0]
    assert context.api == "report_file"
    assert context.params == {"filename": "tdxfin/gpcw.txt", "offset": 123}
    assert context.timeout_ms == 3210
    assert payload.endswith(b"tdxfin/gpcw.txt".ljust(100, b"\x00"))
    assert server == client.server


def test_report_file_client_downloads_known_size_and_reports_progress() -> None:
    client, transport = _client(_body(b"abc"), _body(b"de"))
    progress = []

    result = client.fetch_file(
        "tdxfin/file.zip",
        filesize=5,
        reporthook=lambda downloaded, total: progress.append((downloaded, total)),
    )

    assert result == bytearray(b"abcde")
    assert progress == [(3, 5), (5, 5)]
    assert [call[0].params["offset"] for call in transport.calls] == [0, 3]


def test_report_file_client_unknown_size_stops_at_first_empty_chunk() -> None:
    client, transport = _client(_body(b"abc"), _body(b""))

    result = client.fetch_file("tdxfin/gpcw.txt")

    assert result == bytearray(b"abc")
    assert len(transport.calls) == 2


def test_report_file_client_known_size_stops_after_three_empty_chunks() -> None:
    client, transport = _client(_body(b""), _body(b""), _body(b""))

    result = client.fetch_file("tdxfin/file.zip", filesize=10)

    assert result == bytearray()
    assert len(transport.calls) == 3


def test_report_file_client_close_delegates_to_transport() -> None:
    client, transport = _client()

    client.close()

    assert transport.closed is True
