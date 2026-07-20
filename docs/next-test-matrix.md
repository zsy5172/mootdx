# Next engine test matrix

The next-engine suite uses three layers so network availability does not make
the default test run flaky.

## 1. Deterministic API and parameter matrix

`tests/core_engine/test_next_api_matrix.py` is the public contract inventory.
It fails when a public method is added without updating the matrix and covers:

- every `SyncClient` business API;
- every currently supported `AsyncClient` forwarding API;
- every `NextStdQuotes` compatibility method;
- every Pandas adapter and the report-file client loop;
- all 12 wire frequency values and every string alias;
- request window boundaries (`start=0/20`, `offset=1/15/800`);
- compact integer, dashed string, and plain string dates;
- SH, SZ, BJ, and explicit market prefixes;
- invalid type, invalid range, invalid market, and session-state classes.

`AsyncClient` has typed wrappers for every `SyncClient` business API. The
inventory test requires exact parity and fails if either facade changes without
updating the matrix.

Run the deterministic matrix with:

```bash
pytest \
  tests/core_engine/test_next_api_matrix.py \
  tests/core_engine/test_next_adapter_matrix.py \
  tests/core_engine/test_report_file_client.py \
  tests/compat/test_matrix.py -q
```

## 2. Corpus replay

The committed corpus under `compat/corpus/` verifies request bytes, response
decoding, result shapes, aliases, empty responses, and legacy equivalence.

```bash
nox -s compat_replay
```

## 3. Opt-in exhaustive live matrix

`tests/core_engine/test_next_full_live_matrix.py` exercises all server-backed
interfaces, all 12 bar frequencies, market/date/window variants, async calls,
F10, block data, and the legacy-compatible facade. It is skipped by default.

```bash
MOOTDX_RUN_LIVE_MATRIX=1 pytest tests/core_engine/test_next_full_live_matrix.py -q
```

or:

```bash
nox -s next_live_matrix
```

Live failures must be classified as transport availability, empty upstream
data, protocol decode failure, or API contract failure before changing an
expected result.
