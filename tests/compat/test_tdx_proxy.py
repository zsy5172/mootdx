from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from compat.runners.run_tdx_proxy import _build_tcpdump_command
from compat.tdx_proxy import TdxCaptureProxy
from compat.tdx_proxy import parse_host_port


async def _start_echo_server() -> tuple[asyncio.AbstractServer, str, int]:
    async def echo(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            while True:
                data = await reader.read(65536)
                if not data:
                    break
                writer.write(data)
                await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    server = await asyncio.start_server(echo, "127.0.0.1", 0)
    host, port = server.sockets[0].getsockname()[:2]
    return server, str(host), int(port)


async def _start_reset_echo_server() -> tuple[asyncio.AbstractServer, str, int]:
    async def echo_then_reset(
        reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        data = await reader.read(65536)
        writer.write(data)
        await writer.drain()
        writer.transport.abort()

    server = await asyncio.start_server(echo_then_reset, "127.0.0.1", 0)
    host, port = server.sockets[0].getsockname()[:2]
    return server, str(host), int(port)


async def _request(host: str, port: int, request: bytes) -> bytes:
    reader, writer = await asyncio.open_connection(host, port)
    writer.write(request)
    await writer.drain()
    response = await reader.read()
    writer.close()
    await writer.wait_closed()
    return response


async def _wait_for_completed_session(
    path: Path, session_id: int = 1
) -> dict[str, object]:
    metadata_path = path / f"session-{session_id:06d}" / "metadata.json"
    for _ in range(100):
        if metadata_path.exists():
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            if metadata["status"] != "active":
                return metadata
        await asyncio.sleep(0.01)
    raise AssertionError("capture session did not finish")


def test_parse_host_port_supports_hostnames_and_ipv6() -> None:
    assert parse_host_port("110.41.174.169:7709") == ("110.41.174.169", 7709)
    assert parse_host_port("[::1]:7709") == ("::1", 7709)

    with pytest.raises(ValueError, match="invalid target"):
        parse_host_port("missing-port")
    with pytest.raises(ValueError, match="invalid target port"):
        parse_host_port("localhost:70000")


def test_tcpdump_command_uses_large_configurable_buffer(tmp_path: Path) -> None:
    output = tmp_path / "traffic.pcap"
    command = _build_tcpdump_command(
        executable="/usr/sbin/tcpdump",
        interface="any",
        output=output,
        listen_port=8899,
        allowed_ports={7720, 7709},
        buffer_kib=8192,
    )

    assert command == [
        "/usr/sbin/tcpdump",
        "-i",
        "any",
        "-B",
        "8192",
        "--immediate-mode",
        "-U",
        "-s",
        "0",
        "-w",
        str(output),
        "tcp port 7709 or tcp port 7720 or tcp port 8899",
    ]

    with pytest.raises(ValueError, match="must be positive"):
        _build_tcpdump_command(
            executable="tcpdump",
            interface="any",
            output=output,
            listen_port=8899,
            allowed_ports={7709},
            buffer_kib=0,
        )


def test_http_connect_tunnels_and_records_both_directions(tmp_path: Path) -> None:
    async def run() -> None:
        upstream, upstream_host, upstream_port = await _start_echo_server()
        proxy = TdxCaptureProxy(
            output_dir=tmp_path,
            listen_port=0,
            allowed_ports={upstream_port},
        )
        proxy_host, proxy_port = await proxy.start()
        payload = b"\x0c\x0c\x18\x6cTDX-CONNECT"

        try:
            reader, writer = await asyncio.open_connection(proxy_host, proxy_port)
            request = (
                f"CONNECT {upstream_host}:{upstream_port} HTTP/1.1\r\n"
                f"Host: {upstream_host}:{upstream_port}\r\n"
                "\r\n"
            ).encode("ascii")
            writer.write(request)
            await writer.drain()
            response_head = await reader.readuntil(b"\r\n\r\n")
            assert response_head.startswith(b"HTTP/1.1 200 ")

            writer.write(payload)
            await writer.drain()
            assert await reader.readexactly(len(payload)) == payload
            writer.close()
            await writer.wait_closed()

            metadata = await _wait_for_completed_session(tmp_path)
            session_dir = tmp_path / "session-000001"
            assert metadata["mode"] == "connect"
            assert metadata["status"] == "completed"
            assert metadata["target"] == {"host": upstream_host, "port": upstream_port}
            assert metadata["bytes"] == {
                "client_to_server": len(payload),
                "server_to_client": len(payload),
            }
            assert (session_dir / "client-to-server.bin").read_bytes() == payload
            assert (session_dir / "server-to-client.bin").read_bytes() == payload
            assert (session_dir / "connect-request.txt").read_bytes() == request

            events = [
                json.loads(line)
                for line in (session_dir / "events.jsonl")
                .read_text(encoding="utf-8")
                .splitlines()
            ]
            assert [
                event["direction"] for event in events if event["kind"] == "data"
            ] == [
                "client_to_server",
                "server_to_client",
            ]
        finally:
            await proxy.close()
            upstream.close()
            await upstream.wait_closed()

    asyncio.run(run())


def test_raw_fallback_and_http_marker_share_one_capture(tmp_path: Path) -> None:
    async def run() -> None:
        upstream, upstream_host, upstream_port = await _start_echo_server()
        proxy = TdxCaptureProxy(
            output_dir=tmp_path,
            listen_port=0,
            allowed_ports={upstream_port},
            raw_upstream=(upstream_host, upstream_port),
        )
        proxy_host, proxy_port = await proxy.start()
        payload = b"\x0c\x1f\x18\x76TDX-RAW"

        try:
            reader, writer = await asyncio.open_connection(proxy_host, proxy_port)
            writer.write(payload)
            await writer.drain()
            assert await reader.readexactly(len(payload)) == payload

            mark_response = await _request(
                proxy_host,
                proxy_port,
                (
                    "POST /mark HTTP/1.1\r\n"
                    "Host: localhost\r\n"
                    "Content-Length: 16\r\n"
                    "\r\n"
                    "open-order-limit"
                ).encode("ascii"),
            )
            assert mark_response.startswith(b"HTTP/1.1 200 ")
            assert b"open-order-limit" in mark_response

            writer.close()
            await writer.wait_closed()
            metadata = await _wait_for_completed_session(tmp_path)
            session_dir = tmp_path / "session-000001"
            assert metadata["mode"] == "raw"
            assert (session_dir / "client-to-server.bin").read_bytes() == payload
            assert (session_dir / "server-to-client.bin").read_bytes() == payload

            events = [
                json.loads(line)
                for line in (session_dir / "events.jsonl")
                .read_text(encoding="utf-8")
                .splitlines()
            ]
            marker = next(event for event in events if event["kind"] == "marker")
            assert marker["label"] == "open-order-limit"

            global_markers = [
                json.loads(line)
                for line in (tmp_path / "markers.jsonl")
                .read_text(encoding="utf-8")
                .splitlines()
            ]
            assert global_markers == [
                {
                    "kind": "marker",
                    "label": "open-order-limit",
                    "monotonic_ns": global_markers[0]["monotonic_ns"],
                    "sessions": [1],
                    "timestamp": global_markers[0]["timestamp"],
                }
            ]
        finally:
            await proxy.close()
            upstream.close()
            await upstream.wait_closed()

    asyncio.run(run())


def test_connect_rejects_non_allowlisted_port(tmp_path: Path) -> None:
    async def run() -> None:
        proxy = TdxCaptureProxy(
            output_dir=tmp_path,
            listen_port=0,
            allowed_ports={7709},
        )
        proxy_host, proxy_port = await proxy.start()
        try:
            response = await _request(
                proxy_host,
                proxy_port,
                b"CONNECT 127.0.0.1:7710 HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n",
            )
            assert response.startswith(b"HTTP/1.1 502 ")
            assert b"not allowed" in response
            assert list(tmp_path.glob("session-*")) == []
        finally:
            await proxy.close()

    asyncio.run(run())


def test_reused_output_directory_and_active_shutdown_are_safe(tmp_path: Path) -> None:
    async def run() -> None:
        (tmp_path / "session-000007").mkdir()
        upstream, upstream_host, upstream_port = await _start_echo_server()
        proxy = TdxCaptureProxy(
            output_dir=tmp_path,
            listen_port=0,
            allowed_ports={upstream_port},
            raw_upstream=(upstream_host, upstream_port),
        )
        proxy_host, proxy_port = await proxy.start()
        reader, writer = await asyncio.open_connection(proxy_host, proxy_port)
        payload = b"\x0cTDX-LONG-CONNECTION"

        try:
            writer.write(payload)
            await writer.drain()
            assert await reader.readexactly(len(payload)) == payload
            assert (tmp_path / "session-000008" / "metadata.json").exists()

            await proxy.close()
            metadata = await _wait_for_completed_session(tmp_path, session_id=8)
            assert metadata["status"] == "failed"
            assert metadata["error"] == "proxy stopped while tunnel was active"
            assert (
                tmp_path / "session-000008" / "client-to-server.bin"
            ).read_bytes() == payload
        finally:
            writer.close()
            await writer.wait_closed()
            upstream.close()
            await upstream.wait_closed()

    asyncio.run(run())


def test_upstream_reset_after_complete_response_is_normal_shutdown(
    tmp_path: Path,
) -> None:
    async def run() -> None:
        upstream, upstream_host, upstream_port = await _start_reset_echo_server()
        proxy = TdxCaptureProxy(
            output_dir=tmp_path,
            listen_port=0,
            allowed_ports={upstream_port},
            raw_upstream=(upstream_host, upstream_port),
        )
        proxy_host, proxy_port = await proxy.start()
        payload = b"\x0cTDX-RESET-AFTER-RESPONSE"

        try:
            reader, writer = await asyncio.open_connection(proxy_host, proxy_port)
            writer.write(payload)
            await writer.drain()
            assert await reader.readexactly(len(payload)) == payload
            assert await reader.read() == b""

            metadata = await _wait_for_completed_session(tmp_path)
            assert metadata["status"] == "completed"
            assert "error" not in metadata
            writer.close()
            await writer.wait_closed()
        finally:
            await proxy.close()
            upstream.close()
            await upstream.wait_closed()

    asyncio.run(run())
