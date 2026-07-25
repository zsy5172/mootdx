# Compatibility Harness

This directory contains the replay and baseline comparison harness for the rewrite branch.

- `specs/` stores runnable API case definitions.
- `corpus/` stores replayable request/response bodies plus expected artifacts.
- `runners/run_api_capture.py` captures live artifacts, records corpus cases, and replays offline corpus.
- `runners/compare_artifacts.py` compares two normalized artifacts with a named comparator profile.
- `docker/legacy-baseline.Dockerfile` builds a fixed Python 3.11 image with `mootdx==0.11.7` and `tdxpy==0.2.7`.
- `reader_baselines/v1/` stores Docker-captured local Reader artifacts for standard and extension data.
- `artifacts/` stores transient outputs from local, replay, or Docker-based runs.

The new branch runtime uses offline replay and fixed Reader artifacts as the primary regression gates. Historical and
adjustment live checks can run outside market hours; populated current-day minute/transaction checks run in the dedicated
weekday 10:00 trading-session workflow.
