# Compatibility Harness

This directory contains the replay and baseline comparison harness for the rewrite branch.

- `specs/` stores runnable API case definitions.
- `corpus/` stores replayable request/response bodies plus expected artifacts.
- `runners/run_api_capture.py` captures live artifacts, records corpus cases, and replays offline corpus.
- `runners/compare_artifacts.py` compares two normalized artifacts with a named comparator profile.
- `docker/legacy-baseline.Dockerfile` builds a fixed Python 3.11 image with `mootdx==0.11.7` and `tdxpy==0.2.7`.
- `artifacts/` stores transient outputs from local, replay, or Docker-based runs.

The new branch runtime should use offline replay as the primary regression gate and keep live capture as a secondary smoke path.
