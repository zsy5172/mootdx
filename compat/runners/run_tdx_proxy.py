from __future__ import annotations

import argparse
import asyncio
import shutil
import signal
import subprocess
from pathlib import Path

from compat.tdx_proxy import TdxCaptureProxy
from compat.tdx_proxy import parse_host_port


def _start_tcpdump(
    *,
    interface: str,
    output: Path,
    listen_port: int,
    allowed_ports: set[int],
    buffer_kib: int,
) -> subprocess.Popen:
    executable = shutil.which("tcpdump")
    if executable is None:
        raise RuntimeError("tcpdump is not installed; install it or omit --pcap")

    output.parent.mkdir(parents=True, exist_ok=True)
    command = _build_tcpdump_command(
        executable=executable,
        interface=interface,
        output=output,
        listen_port=listen_port,
        allowed_ports=allowed_ports,
        buffer_kib=buffer_kib,
    )
    print("tcpdump:", " ".join(command), flush=True)
    return subprocess.Popen(command)


def _build_tcpdump_command(
    *,
    executable: str,
    interface: str,
    output: Path,
    listen_port: int,
    allowed_ports: set[int],
    buffer_kib: int,
) -> list[str]:
    if buffer_kib <= 0:
        raise ValueError("tcpdump buffer must be positive")

    ports = sorted({listen_port, *allowed_ports})
    capture_filter = " or ".join(f"tcp port {port}" for port in ports)
    return [
        executable,
        "-i",
        interface,
        "-B",
        str(buffer_kib),
        "--immediate-mode",
        "-U",
        "-s",
        "0",
        "-w",
        str(output),
        capture_filter,
    ]


def _stop_tcpdump(process: subprocess.Popen | None) -> None:
    if process is None or process.poll() is not None:
        return
    process.send_signal(signal.SIGINT)
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


async def _run(args: argparse.Namespace) -> int:
    raw_upstream = parse_host_port(args.raw_upstream) if args.raw_upstream else None
    allowed_ports = set(args.allow_port or [7709, 7720])
    if raw_upstream is not None:
        allowed_ports.add(raw_upstream[1])

    tcpdump = None
    if args.pcap:
        tcpdump = _start_tcpdump(
            interface=args.tcpdump_interface,
            output=Path(args.pcap),
            listen_port=args.listen_port,
            allowed_ports=allowed_ports,
            buffer_kib=args.tcpdump_buffer_kib,
        )
        await asyncio.sleep(0.2)
        if tcpdump.poll() is not None:
            raise RuntimeError(
                f"tcpdump exited before capture started with status {tcpdump.returncode}"
            )

    proxy = TdxCaptureProxy(
        output_dir=args.output_dir,
        listen_host=args.listen_host,
        listen_port=args.listen_port,
        allowed_ports=allowed_ports,
        raw_upstream=raw_upstream,
    )
    loop = asyncio.get_running_loop()

    def stop() -> None:
        proxy.shutdown_event.set()

    installed_signals = []
    for signum in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(signum, stop)
            installed_signals.append(signum)
        except NotImplementedError:  # pragma: no cover - Windows event loop
            pass

    try:
        host, port = await proxy.start()
        print(f"TDX capture proxy listening on {host}:{port}", flush=True)
        print(f"capture directory: {Path(args.output_dir).resolve()}", flush=True)
        print(
            f'mark action: curl -X POST http://{host}:{port}/mark --data "label"',
            flush=True,
        )
        print(f"stop proxy: curl -X POST http://{host}:{port}/shutdown", flush=True)
        if raw_upstream is None:
            print("mode: HTTP CONNECT", flush=True)
        else:
            print(
                f"mode: HTTP CONNECT + raw fallback to {raw_upstream[0]}:{raw_upstream[1]}",
                flush=True,
            )
        await proxy.serve_forever()
        return 0
    finally:
        await proxy.close()
        if tcpdump is not None:
            await asyncio.sleep(0.25)
        _stop_tcpdump(tcpdump)
        for signum in installed_signals:
            loop.remove_signal_handler(signum)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Capture TDX HTTP CONNECT and raw HQ traffic"
    )
    parser.add_argument("--listen-host", default="127.0.0.1")
    parser.add_argument("--listen-port", type=int, default=8899)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument(
        "--allow-port",
        type=int,
        action="append",
        help="allowed CONNECT target port; repeat as needed (default: 7709, 7720)",
    )
    parser.add_argument(
        "--raw-upstream",
        help="optional HOST:PORT fallback for clients configured with the proxy as a raw HQ server",
    )
    parser.add_argument(
        "--pcap", help="start tcpdump and write packets to this pcap/pcapng path"
    )
    parser.add_argument("--tcpdump-interface", default="any")
    parser.add_argument(
        "--tcpdump-buffer-kib",
        type=int,
        default=8192,
        help="tcpdump kernel capture buffer in KiB (default: 8192)",
    )
    args = parser.parse_args()

    try:
        return asyncio.run(_run(args))
    except KeyboardInterrupt:
        return 130
    except (OSError, RuntimeError, ValueError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
