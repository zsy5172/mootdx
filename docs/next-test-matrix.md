# Next engine test matrix

The next-engine suite uses three layers so network availability does not make
the default test run flaky.

## 1. Deterministic API and parameter matrix

`tests/core_engine/test_next_api_matrix.py` is the public contract inventory.
It fails when a public method is added without updating the matrix and covers:

- every `SyncClient` business API;
- every currently supported `AsyncClient` forwarding API;
- every `NextStdQuotes` compatibility method;
- every Pandas adapter;
- the synchronous and asynchronous financial-file APIs, including
  `fetch_and_parse`;
- the standard and extension local Reader public inventories;
- all 12 wire frequency values and every string alias;
- request window boundaries, including the 800-bar, 1800-live-transaction,
  2000-historical-transaction, and 2000-special-price limits;
- compact integer, dashed string, and plain string dates;
- SH, SZ, BJ, and explicit market prefixes;
- invalid type, invalid range, invalid market, and empty upstream responses.

`AsyncClient` has typed wrappers for every `SyncClient` business API. The
inventory test requires exact parity and fails if either facade changes without
updating the matrix. `AsyncPandasClient`/`PandasClient` and
`AsyncFinancialFileClient`/`FinancialFileClient` are checked by the same rule.

Run the deterministic matrix with:

```bash
pytest \
  tests/core_engine/test_next_api_matrix.py \
  tests/core_engine/test_next_adapter_matrix.py \
  tests/core_engine/test_report_file_client.py \
  tests/compat/test_matrix.py -q
```

## 2. Legacy corpus, real adjustment events, and Reader baselines

The committed corpus under `compat/corpus/` verifies request bytes, response
decoding, result shapes, aliases, empty responses, and legacy equivalence. The
history corpus includes populated `get_k_data`, `k`, and `ohlc` artifacts plus
the real 2026 adjustment windows for `600036` and `510500`.

`tests/compat/test_adjustment_event_matrix.py` uses the captured TDX bars and
`xdxr` packets to verify:

- the 2026-07-10 `600036` cash event;
- both `510500` split/consolidation events and all four cash events;
- qfq/hfq affine ETF handling;
- LOF, closed-fund/REIT cash events and reverse-order hfq composition;
- category 12 non-tradable-share contractions, explicit category 13/14 guards
  for proportional adjustment, and desktop-compatible category 13/14 handling
  for `tdx_qfq`/`tdx_hfq`;
- Beijing-market bars/xdxr adjustment, including pre-listing event baselines;
- requested-date minute price adjustment while preserving volume and amount;
- long suspensions and ranges that do not cross unresolved events;
- multiple corporate actions inside one suspension gap, future ex-dates, and
  half-up price rounding at stock/fund precision;
- weekly, monthly, quarterly, and yearly OHLC aggregation after daily
  adjustment;
- populated adjusted `get_k_data`, `k`, and `ohlc` result shapes.

```bash
nox -s compat_replay
```

Local Reader behavior is compared against committed artifacts captured in a
Python 3.11 Docker image containing `mootdx==0.11.7` and `tdxpy==0.2.7`:

```bash
nox -s reader_baseline_capture
pytest tests/compat/test_reader_baseline.py -q
```

## 3. Opt-in exhaustive historical/live matrix

`tests/core_engine/test_next_full_live_matrix.py` exercises all server-backed
interfaces, the `0x0452` special-price table and single-symbol rule fallback,
all 12 bar frequencies, market/date/window variants, async calls, F10 with
`600036`, block data, real `600036`/`510500` adjustment combinations, populated
history wrappers, and the legacy-compatible facade. It is skipped by default.
The production `transaction()` API always contacts the upstream server and
preserves an empty response. The matrix only skips its populated real-time
assertion outside a weekday trading session; historical `transactions()` is
always exercised.

```bash
MOOTDX_RUN_LIVE_MATRIX=1 pytest tests/core_engine/test_next_full_live_matrix.py -q
```

or:

```bash
nox -s next_live_matrix
```

## 4. Trading-session matrix

`tests/core_engine/test_trading_session_live_matrix.py` is reserved for a live
A-share session and requires populated data. This is a live-test precondition,
not a production API gate. It covers:

- `minute()` and `minutes(today)` with stable-row equivalence;
- `transaction()` at `start=0/10` and `offset=1/10/800/1800`;
- raw, async, Pandas, and `Quotes.factory(engine="next")` entry points;
- legacy and next decoding of the same minute/transaction response;
- normalized high-level artifacts under `compat/artifacts/`.

This suite is intentionally not scheduled in GitHub Actions because TDX quote
nodes may be unreachable from runners outside China. Run it manually from a
network that can connect to the configured TDX nodes after confirming that the
A-share market is open. The strict session guard makes an out-of-session manual
run fail rather than report a skipped success.

For a manual trading-session run:

```bash
MOOTDX_RUN_TRADING_SESSION_LIVE=1 \
MOOTDX_REQUIRE_TRADING_SESSION=1 \
pytest tests/core_engine/test_trading_session_live_matrix.py -q
```

or:

```bash
nox -s trading_session_live
```

The normalized raw and high-level outputs are written to `compat/artifacts/`
for inspection after the manual run.

Live failures must be classified as transport availability, empty upstream
data, protocol decode failure, or API contract failure before changing an
expected result.
