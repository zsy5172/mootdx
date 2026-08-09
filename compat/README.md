# Compatibility Harness

This directory contains the replay and baseline comparison harness for the rewrite branch.

- `specs/` stores runnable API case definitions.
- `corpus/` stores replayable request/response bodies plus expected artifacts.
- `runners/run_api_capture.py` captures live artifacts, records corpus cases, and replays offline corpus.
- `runners/compare_artifacts.py` compares two normalized artifacts with a named comparator profile.
- `runners/analyze_tdx_capture.py` splits lossless proxy streams and decodes known desktop-client commands.
- `docker/legacy-baseline.Dockerfile` builds a fixed Python 3.11 image with `mootdx==0.11.7` and `tdxpy==0.2.7`.
- `reader_baselines/v1/` stores Docker-captured local Reader artifacts for standard and extension data.
- `artifacts/` stores transient outputs from local, replay, or Docker-based runs.

The new branch runtime uses offline replay and fixed Reader artifacts as the primary regression gates. Historical and
adjustment live checks can run outside market hours; populated current-day minute/transaction checks remain in a dedicated
manual trading-session suite because TDX nodes may reject connections from CI runners outside China.

## TDX CONNECT capture proxy

`compat.runners.run_tdx_proxy` is a local HTTP CONNECT proxy for isolating requests made by the desktop TDX client. It
records each tunnel as separate client-to-server and server-to-client byte streams, writes timestamped chunk events, and
can insert operator markers immediately before or after a UI action. An optional raw fallback is available when a client
must be pointed at a local HQ endpoint instead of using its HTTP proxy setting.

Start a CONNECT capture on a trusted local interface:

```bash
uv run python -m compat.runners.run_tdx_proxy \
  --listen-host 0.0.0.0 \
  --listen-port 8899 \
  --output-dir data/tdx-probes/limit-sh-600036 \
  --pcap data/tdx-probes/limit-sh-600036/traffic.pcap
```

`--pcap` starts `tcpdump`, which must be installed and have packet-capture permission. Without that option the proxy still
records lossless application byte streams. The runner requests an 8192 KiB kernel capture buffer by default; use
`--tcpdump-buffer-kib` if the host needs a different value. Ports 7709 and 7720 are allowed by default; repeat
`--allow-port` when a confirmed public quote service uses another port.

Configure the desktop client to use this machine's IP and port 8899 as its HTTP proxy. When the client runs on Windows and
the proxy runs in WSL, `127.0.0.1` works with mirrored networking; otherwise use the WSL address reported by
`hostname -I`. Keep `--listen-host 127.0.0.1` unless access from Windows requires `0.0.0.0`.

Add a timestamp marker immediately before a single client action:

```bash
curl -X POST http://127.0.0.1:8899/mark --data "before-open-order-sh-600036"
```

Add a second marker after the UI has populated its values, then stop the proxy:

```bash
curl -X POST http://127.0.0.1:8899/mark --data "after-open-order-sh-600036"
curl -X POST http://127.0.0.1:8899/shutdown
```

Every `session-NNNNNN/` directory contains:

- `metadata.json`: client endpoint, CONNECT target, timing, byte counts, and completion status;
- `connect-request.txt`: the CONNECT request, separate from the tunneled TDX protocol;
- `client-to-server.bin` and `server-to-client.bin`: exact application streams;
- `events.jsonl`: ordered stream offsets, lengths, timestamps, and action markers.

Analyze a completed session without relying on the potentially lossy PCAP:

```bash
uv run python -m compat.runners.analyze_tdx_capture \
  data/tdx-probes/limit-sh-600036/session-000004 \
  --command 0x0547 \
  --code 600036 \
  --search-price 42.90 \
  --search-price 35.10
```

The analyzer validates every request and response boundary, aligns frames in stream order, applies the known command-level
XOR for `0x0547` and `0x054E`, and parses the `0x0547` enhanced five-level quote. Price searches cover text, float,
integer cents, TDX absolute-price, and close/pre-close delta encodings. An empty match list means those common encodings
were not present in the decoded command response; it does not prove that an as-yet unknown nested encoding cannot contain
the value.

The capture should cover one public quote action only. Opening an order-entry panel to read a displayed limit price is
enough; do not submit an order, capture a brokerage session, enable port 443 without a specific reason, or include account
credentials in a corpus.

If CONNECT behavior needs to be compared with a direct HQ connection, use the raw fallback:

```bash
uv run python -m compat.runners.run_tdx_proxy \
  --listen-host 0.0.0.0 \
  --listen-port 8899 \
  --raw-upstream 110.41.174.169:7709 \
  --output-dir data/tdx-probes/raw-control
```
