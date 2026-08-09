from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime
from datetime import timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs
from urllib.parse import urlsplit

HTTP_METHOD_PREFIXES = (b"CONNECT ", b"GET ", b"POST ")
MAX_HTTP_HEADER_BYTES = 64 * 1024
STREAM_CHUNK_SIZE = 64 * 1024


def parse_host_port(value: str) -> tuple[str, int]:
    text = value.strip()
    if not text:
        raise ValueError("target cannot be blank")

    if text.startswith("["):
        closing = text.find("]")
        if closing < 0 or closing + 1 >= len(text) or text[closing + 1] != ":":
            raise ValueError(f"invalid target: {value}")
        host = text[1:closing]
        port_text = text[closing + 2 :]
    else:
        try:
            host, port_text = text.rsplit(":", 1)
        except ValueError as exc:
            raise ValueError(f"invalid target: {value}") from exc

    if not host:
        raise ValueError(f"invalid target: {value}")
    try:
        port = int(port_text)
    except ValueError as exc:
        raise ValueError(f"invalid target port: {value}") from exc
    if not 1 <= port <= 65535:
        raise ValueError(f"invalid target port: {value}")
    return host, port


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


class CaptureSession:
    def __init__(
        self,
        root: Path,
        session_id: int,
        *,
        mode: str,
        target: tuple[str, int],
        client: object,
    ) -> None:
        self.session_id = session_id
        self.mode = mode
        self.target = target
        self.client = client
        self.started_at = _utc_now()
        self.started_monotonic_ns = time.monotonic_ns()
        self.directory = root / f"session-{session_id:06d}"
        self.directory.mkdir(parents=True, exist_ok=False)
        self._streams = {
            "client_to_server": (self.directory / "client-to-server.bin").open("wb"),
            "server_to_client": (self.directory / "server-to-client.bin").open("wb"),
        }
        self._offsets = {"client_to_server": 0, "server_to_client": 0}
        self._sequence = 0
        self._events_path = self.directory / "events.jsonl"
        self._events = self._events_path.open("a", encoding="utf-8")
        self._event_lock = asyncio.Lock()
        self._closed = False
        self._write_metadata(status="active")

    async def record(self, direction: str, data: bytes) -> None:
        if not data:
            return
        stream = self._streams[direction]
        offset = self._offsets[direction]
        stream.write(data)
        stream.flush()
        self._offsets[direction] += len(data)
        await self._append_event(
            {
                "kind": "data",
                "direction": direction,
                "offset": offset,
                "length": len(data),
            }
        )

    async def mark(self, label: str) -> None:
        await self._append_event({"kind": "marker", "label": label})

    async def close(self, error: str | None = None) -> None:
        if self._closed:
            return
        self._closed = True
        await self._append_event({"kind": "session_end", "error": error})
        for stream in self._streams.values():
            stream.close()
        self._events.close()
        self._write_metadata(status="failed" if error else "completed", error=error)

    async def _append_event(self, event: dict[str, object]) -> None:
        async with self._event_lock:
            event = {
                "sequence": self._sequence,
                "timestamp": _utc_now(),
                "monotonic_ns": time.monotonic_ns(),
                **event,
            }
            self._sequence += 1
            self._events.write(
                json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n"
            )
            self._events.flush()

    def _write_metadata(self, *, status: str, error: str | None = None) -> None:
        metadata = {
            "session_id": self.session_id,
            "mode": self.mode,
            "status": status,
            "target": {"host": self.target[0], "port": self.target[1]},
            "client": str(self.client),
            "started_at": self.started_at,
            "started_monotonic_ns": self.started_monotonic_ns,
            "bytes": dict(self._offsets),
        }
        if status != "active":
            metadata["ended_at"] = _utc_now()
            metadata["duration_ns"] = time.monotonic_ns() - self.started_monotonic_ns
        if error:
            metadata["error"] = error
        _write_json(self.directory / "metadata.json", metadata)


class TdxCaptureProxy:
    def __init__(
        self,
        *,
        output_dir: str | Path,
        listen_host: str = "127.0.0.1",
        listen_port: int = 8899,
        allowed_ports: set[int] | frozenset[int] | None = None,
        raw_upstream: tuple[str, int] | None = None,
    ) -> None:
        self.output_dir = Path(output_dir)
        self.listen_host = listen_host
        self.listen_port = int(listen_port)
        self.allowed_ports = frozenset(allowed_ports or {7709, 7720})
        self.raw_upstream = raw_upstream
        self.shutdown_event = asyncio.Event()
        self._server: asyncio.AbstractServer | None = None
        self._active: dict[int, CaptureSession] = {}
        self._handlers: set[asyncio.Task] = set()
        self._session_id = 0
        self._session_lock = asyncio.Lock()
        self._global_events_lock = asyncio.Lock()

    @property
    def address(self) -> tuple[str, int]:
        if self._server is None or not self._server.sockets:
            raise RuntimeError("proxy is not running")
        host, port = self._server.sockets[0].getsockname()[:2]
        return str(host), int(port)

    async def start(self) -> tuple[str, int]:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        existing_session_ids = []
        for path in self.output_dir.glob("session-*"):
            suffix = path.name[len("session-") :]
            if path.is_dir() and suffix.isdigit():
                existing_session_ids.append(int(suffix))
        self._session_id = max(existing_session_ids, default=0)
        self._server = await asyncio.start_server(
            self._handle_client, self.listen_host, self.listen_port
        )
        return self.address

    async def serve_forever(self) -> None:
        if self._server is None:
            await self.start()
        await self.shutdown_event.wait()

    async def close(self) -> None:
        server = self._server
        self._server = None
        if server is not None:
            server.close()
        current = asyncio.current_task()
        handlers = [
            task for task in self._handlers if task is not current and not task.done()
        ]
        for task in handlers:
            task.cancel()
        if handlers:
            await asyncio.gather(*handlers, return_exceptions=True)
        if server is not None:
            await server.wait_closed()

    async def mark(self, label: str) -> list[int]:
        normalized = label.strip()
        if not normalized:
            raise ValueError("marker label cannot be blank")

        session_ids = sorted(self._active)
        event = {
            "timestamp": _utc_now(),
            "monotonic_ns": time.monotonic_ns(),
            "kind": "marker",
            "label": normalized,
            "sessions": session_ids,
        }
        async with self._global_events_lock:
            with (self.output_dir / "markers.jsonl").open(
                "a", encoding="utf-8"
            ) as stream:
                stream.write(
                    json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n"
                )
        for session in list(self._active.values()):
            await session.mark(normalized)
        return session_ids

    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        handler = asyncio.current_task()
        if handler is not None:
            self._handlers.add(handler)
        try:
            initial = await reader.read(STREAM_CHUNK_SIZE)
            if not initial:
                return

            if _could_be_http(initial):
                head, remaining = await _read_http_head(reader, initial)
                await self._handle_http(reader, writer, head, remaining)
                return

            if self.raw_upstream is None:
                await _send_http_response(
                    writer, 400, {"error": "CONNECT request required"}
                )
                return
            await self._open_tunnel(
                reader,
                writer,
                mode="raw",
                target=self.raw_upstream,
                initial_client_data=initial,
            )
        except (ConnectionError, asyncio.IncompleteReadError):
            pass
        except Exception as exc:  # pragma: no cover - defensive server boundary
            try:
                await _send_http_response(writer, 502, {"error": str(exc)})
            except ConnectionError:
                pass
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except ConnectionError:
                pass
            if handler is not None:
                self._handlers.discard(handler)

    async def _handle_http(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        head: bytes,
        remaining: bytes,
    ) -> None:
        request_line, headers = _parse_http_head(head)
        method, target, _ = request_line.split(" ", 2)
        method = method.upper()

        if method == "CONNECT":
            upstream = parse_host_port(target)
            self._validate_target(upstream)
            await self._open_tunnel(
                reader,
                writer,
                mode="connect",
                target=upstream,
                initial_client_data=remaining,
                connect_request=head,
            )
            return

        parsed = urlsplit(target)
        if method == "GET" and parsed.path == "/health":
            await _send_http_response(
                writer,
                200,
                {
                    "status": "ok",
                    "active_sessions": sorted(self._active),
                    "allowed_ports": sorted(self.allowed_ports),
                },
            )
            return

        if method == "POST" and parsed.path == "/mark":
            body = await _read_http_body(reader, remaining, headers)
            label = _marker_label(parsed.query, headers, body)
            sessions = await self.mark(label)
            await _send_http_response(
                writer, 200, {"status": "marked", "label": label, "sessions": sessions}
            )
            return

        if method == "POST" and parsed.path == "/shutdown":
            await _send_http_response(writer, 200, {"status": "stopping"})
            self.shutdown_event.set()
            return

        await _send_http_response(writer, 404, {"error": "unknown control endpoint"})

    def _validate_target(self, target: tuple[str, int]) -> None:
        if target[1] not in self.allowed_ports:
            raise PermissionError(f"target port {target[1]} is not allowed")

    async def _open_tunnel(
        self,
        client_reader: asyncio.StreamReader,
        client_writer: asyncio.StreamWriter,
        *,
        mode: str,
        target: tuple[str, int],
        initial_client_data: bytes = b"",
        connect_request: bytes | None = None,
    ) -> None:
        self._validate_target(target)
        try:
            upstream_reader, upstream_writer = await asyncio.open_connection(*target)
        except OSError as exc:
            if mode == "connect":
                await _send_http_response(
                    client_writer,
                    502,
                    {"error": f"cannot connect to {target[0]}:{target[1]}"},
                )
            raise ConnectionError(str(exc)) from exc

        session = await self._new_session(
            mode=mode, target=target, client=client_writer.get_extra_info("peername")
        )
        if connect_request is not None:
            (session.directory / "connect-request.txt").write_bytes(connect_request)

        if mode == "connect":
            client_writer.write(
                b"HTTP/1.1 200 Connection Established\r\nProxy-Agent: mootdx-capture\r\n\r\n"
            )
            await client_writer.drain()

        error: str | None = None
        pump_tasks: set[asyncio.Task] = set()
        try:
            if initial_client_data:
                await session.record("client_to_server", initial_client_data)
                upstream_writer.write(initial_client_data)
                await upstream_writer.drain()

            client_task = asyncio.create_task(
                self._pump(client_reader, upstream_writer, session, "client_to_server")
            )
            server_task = asyncio.create_task(
                self._pump(upstream_reader, client_writer, session, "server_to_client")
            )
            pump_tasks = {client_task, server_task}
            done, pending = await asyncio.wait(
                pump_tasks,
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in done:
                task.result()
            if pending:
                more_done, pending = await asyncio.wait(pending, timeout=1)
                for task in more_done:
                    task.result()
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
        except (ConnectionError, OSError) as exc:
            error = str(exc)
        except asyncio.CancelledError:
            error = "proxy stopped while tunnel was active"
            raise
        finally:
            pending_tasks = [task for task in pump_tasks if not task.done()]
            for task in pending_tasks:
                task.cancel()
            if pending_tasks:
                await asyncio.gather(*pending_tasks, return_exceptions=True)
            upstream_writer.close()
            try:
                await upstream_writer.wait_closed()
            except ConnectionError:
                pass
            await session.close(error=error)
            self._active.pop(session.session_id, None)

    async def _new_session(
        self,
        *,
        mode: str,
        target: tuple[str, int],
        client: object,
    ) -> CaptureSession:
        async with self._session_lock:
            self._session_id += 1
            session = CaptureSession(
                self.output_dir,
                self._session_id,
                mode=mode,
                target=target,
                client=client,
            )
            self._active[session.session_id] = session
            return session

    @staticmethod
    async def _pump(
        source: asyncio.StreamReader,
        destination: asyncio.StreamWriter,
        session: CaptureSession,
        direction: str,
    ) -> None:
        try:
            while True:
                data = await source.read(STREAM_CHUNK_SIZE)
                if not data:
                    break
                await session.record(direction, data)
                destination.write(data)
                await destination.drain()
        except (BrokenPipeError, ConnectionResetError):
            return

        try:
            destination.write_eof()
        except (AttributeError, OSError):
            pass


def _could_be_http(data: bytes) -> bool:
    upper = data.upper()
    return any(
        prefix.startswith(upper) or upper.startswith(prefix)
        for prefix in HTTP_METHOD_PREFIXES
    )


async def _read_http_head(
    reader: asyncio.StreamReader, initial: bytes
) -> tuple[bytes, bytes]:
    buffer = bytearray(initial)
    while b"\r\n\r\n" not in buffer:
        if len(buffer) > MAX_HTTP_HEADER_BYTES:
            raise ValueError("HTTP proxy request header is too large")
        chunk = await reader.read(STREAM_CHUNK_SIZE)
        if not chunk:
            raise asyncio.IncompleteReadError(bytes(buffer), None)
        buffer.extend(chunk)

    marker = buffer.index(b"\r\n\r\n") + 4
    return bytes(buffer[:marker]), bytes(buffer[marker:])


def _parse_http_head(head: bytes) -> tuple[str, dict[str, str]]:
    try:
        text = head.decode("iso-8859-1")
        lines = text.split("\r\n")
        request_line = lines[0]
        method, _, version = request_line.split(" ", 2)
    except (UnicodeDecodeError, ValueError) as exc:
        raise ValueError("invalid HTTP proxy request") from exc
    if method.upper() not in {"CONNECT", "GET", "POST"} or not version.startswith(
        "HTTP/"
    ):
        raise ValueError("unsupported HTTP proxy request")

    headers: dict[str, str] = {}
    for line in lines[1:]:
        if not line:
            continue
        if ":" not in line:
            raise ValueError("invalid HTTP proxy header")
        name, value = line.split(":", 1)
        headers[name.strip().lower()] = value.strip()
    return request_line, headers


async def _read_http_body(
    reader: asyncio.StreamReader,
    remaining: bytes,
    headers: dict[str, str],
) -> bytes:
    try:
        length = int(headers.get("content-length", "0"))
    except ValueError as exc:
        raise ValueError("invalid Content-Length") from exc
    if length < 0 or length > MAX_HTTP_HEADER_BYTES:
        raise ValueError("HTTP control request body is too large")
    if len(remaining) >= length:
        return remaining[:length]
    return remaining + await reader.readexactly(length - len(remaining))


def _marker_label(query: str, headers: dict[str, str], body: bytes) -> str:
    query_label = parse_qs(query).get("label", [""])[0].strip()
    if query_label:
        return query_label

    content_type = headers.get("content-type", "")
    if "application/json" in content_type:
        try:
            value: Any = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("invalid marker JSON") from exc
        if isinstance(value, dict):
            return str(value.get("label", "")).strip()
        raise ValueError("marker JSON must be an object")
    return body.decode("utf-8").strip()


async def _send_http_response(
    writer: asyncio.StreamWriter,
    status: int,
    payload: dict[str, object],
) -> None:
    reasons = {200: "OK", 400: "Bad Request", 404: "Not Found", 502: "Bad Gateway"}
    body = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    headers = (
        f"HTTP/1.1 {status} {reasons.get(status, 'Error')}\r\n"
        "Content-Type: application/json; charset=utf-8\r\n"
        f"Content-Length: {len(body)}\r\n"
        "Connection: close\r\n"
        "\r\n"
    ).encode("ascii")
    writer.write(headers + body)
    await writer.drain()
