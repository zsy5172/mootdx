from __future__ import annotations

import shutil
from pathlib import Path

import nox

ROOT = Path(__file__).parent
SMOKE_SPEC_PATH = ROOT / "compat" / "specs" / "stock_count" / "sz.json"
QUOTES_SMOKE_SPEC_PATH = ROOT / "compat" / "specs" / "quotes" / "single_sh.json"
BARS_SMOKE_SPEC_PATH = ROOT / "compat" / "specs" / "bars" / "daily_sh_600036_last10.json"
MINUTES_SMOKE_SPEC_PATH = ROOT / "compat" / "specs" / "minutes" / "history_sh_000001_20171010.json"
TRANSACTIONS_SMOKE_SPEC_PATH = ROOT / "compat" / "specs" / "transactions" / "history_sh_600036_20170209_last10.json"
FINANCE_SMOKE_SPEC_PATH = ROOT / "compat" / "specs" / "finance" / "sz_000001.json"
XDXR_SMOKE_SPEC_PATH = ROOT / "compat" / "specs" / "xdxr" / "sh_600036.json"
SPEC_ROOT = ROOT / "compat" / "specs"
CORPUS_ROOT = ROOT / "compat" / "corpus"
ARTIFACTS_DIR = ROOT / "compat" / "artifacts"
DOCKERFILE = ROOT / "compat" / "docker" / "legacy-baseline.Dockerfile"
LEGACY_IMAGE = "mootdx-legacy-baseline:py311"
CONTAINER_SPEC_ROOT = Path("/workspace/compat/specs")
CONTAINER_CORPUS_ROOT = Path("/workspace/compat/corpus")
CONTAINER_ARTIFACTS_DIR = Path("/workspace/compat/artifacts")
NEXT_INSTALL = "."
LEGACY_INSTALL = ".[legacy]"

nox.options.default_venv_backend = "uv|virtualenv"
nox.options.sessions = ["next_only_install", "unit", "compat_replay"]


def _docker_available() -> bool:
    return shutil.which("docker") is not None


@nox.session(python=["3.13"])
def next_only_install(session: nox.Session) -> None:
    session.install("-e", NEXT_INSTALL, "pytest~=9.0.3")
    session.run(
        "pytest",
        "tests/core_engine/test_next_only_runtime.py",
        "tests/core_engine/test_next_local_readers.py",
        "tests/core_engine/test_report_file_protocol.py",
        "-q",
    )


@nox.session(python=["3.13"])
def unit(session: nox.Session) -> None:
    session.install("-e", LEGACY_INSTALL, "freezegun~=1.5.5", "pytest~=9.0.3", "pytest-cov~=7.1.0")
    session.run("pytest", "tests/utils/test_utils.py", "tests/cache/test_file.py", "tests/core_engine", "tests/compat", "-q")


@nox.session(python=["3.13"])
def compat_replay(session: nox.Session) -> None:
    session.install("-e", LEGACY_INSTALL)
    session.run(
        "python",
        "-m",
        "compat.runners.run_api_capture",
        "replay-corpus",
        "--spec-root",
        str(SPEC_ROOT),
        "--corpus-root",
        str(CORPUS_ROOT),
        "--artifacts-dir",
        str(ARTIFACTS_DIR),
        "--runtime",
        "legacy",
        "--backend-name",
        "compat-legacy-replay",
    )
    session.run(
        "python",
        "-m",
        "compat.runners.run_api_capture",
        "replay-corpus",
        "--spec-root",
        str(SPEC_ROOT),
        "--corpus-root",
        str(CORPUS_ROOT),
        "--artifacts-dir",
        str(ARTIFACTS_DIR),
        "--runtime",
        "next",
        "--backend-name",
        "compat-next-replay",
    )


@nox.session(python=["3.13"])
def baseline_capture(session: nox.Session) -> None:
    if not _docker_available():
        session.skip("docker is required for the legacy baseline image")

    session.run(
        "docker",
        "build",
        "-f",
        str(DOCKERFILE),
        "-t",
        LEGACY_IMAGE,
        ".",
        external=True,
    )
    session.run(
        "docker",
        "run",
        "--rm",
        "-v",
        f"{ROOT}:/workspace",
        "-w",
        "/workspace",
        LEGACY_IMAGE,
        "capture-corpus",
        "--backend-name",
        "mootdx-legacy",
        "--spec-root",
        str(CONTAINER_SPEC_ROOT),
        "--corpus-root",
        str(CONTAINER_CORPUS_ROOT),
        "--artifacts-dir",
        str(CONTAINER_ARTIFACTS_DIR),
        external=True,
    )


@nox.session(python=["3.13"])
def compat_live_smoke(session: nox.Session) -> None:
    session.install("-e", LEGACY_INSTALL, "pytest~=9.0.3")
    session.run(
        "python",
        "-m",
        "compat.runners.run_api_capture",
        "capture-live-artifact",
        "--runtime",
        "next",
        "--backend-name",
        "mootdx-next",
        "--spec",
        str(SMOKE_SPEC_PATH),
        "--output",
        str(ARTIFACTS_DIR / "next-smoke.json"),
    )
    session.run(
        "python",
        "-m",
        "compat.runners.run_api_capture",
        "capture-live-artifact",
        "--runtime",
        "legacy",
        "--backend-name",
        "mootdx-legacy-local",
        "--spec",
        str(SMOKE_SPEC_PATH),
        "--output",
        str(ARTIFACTS_DIR / "legacy-smoke.json"),
    )
    session.run(
        "python",
        "-m",
        "compat.runners.compare_artifacts",
        "--left",
        str(ARTIFACTS_DIR / "next-smoke.json"),
        "--right",
        str(ARTIFACTS_DIR / "legacy-smoke.json"),
    )
    session.run(
        "python",
        "-m",
        "compat.runners.run_api_capture",
        "compare-live-decode",
        "--spec",
        str(QUOTES_SMOKE_SPEC_PATH),
        "--left-output",
        str(ARTIFACTS_DIR / "quotes-legacy-raw.json"),
        "--right-output",
        str(ARTIFACTS_DIR / "quotes-next-raw.json"),
    )
    session.run(
        "python",
        "-m",
        "compat.runners.run_api_capture",
        "compare-live-decode",
        "--spec",
        str(BARS_SMOKE_SPEC_PATH),
        "--left-output",
        str(ARTIFACTS_DIR / "bars-legacy-raw.json"),
        "--right-output",
        str(ARTIFACTS_DIR / "bars-next-raw.json"),
    )
    session.run(
        "python",
        "-m",
        "compat.runners.run_api_capture",
        "compare-live-decode",
        "--spec",
        str(MINUTES_SMOKE_SPEC_PATH),
        "--left-output",
        str(ARTIFACTS_DIR / "minutes-legacy-raw.json"),
        "--right-output",
        str(ARTIFACTS_DIR / "minutes-next-raw.json"),
    )
    session.run(
        "python",
        "-m",
        "compat.runners.run_api_capture",
        "compare-live-decode",
        "--spec",
        str(TRANSACTIONS_SMOKE_SPEC_PATH),
        "--left-output",
        str(ARTIFACTS_DIR / "transactions-legacy-raw.json"),
        "--right-output",
        str(ARTIFACTS_DIR / "transactions-next-raw.json"),
    )
    session.run(
        "python",
        "-m",
        "compat.runners.run_api_capture",
        "compare-live-decode",
        "--spec",
        str(FINANCE_SMOKE_SPEC_PATH),
        "--left-output",
        str(ARTIFACTS_DIR / "finance-legacy-raw.json"),
        "--right-output",
        str(ARTIFACTS_DIR / "finance-next-raw.json"),
    )
    session.run(
        "python",
        "-m",
        "compat.runners.run_api_capture",
        "compare-live-decode",
        "--spec",
        str(XDXR_SMOKE_SPEC_PATH),
        "--left-output",
        str(ARTIFACTS_DIR / "xdxr-legacy-raw.json"),
        "--right-output",
        str(ARTIFACTS_DIR / "xdxr-next-raw.json"),
    )
    session.run("pytest", "tests/core_engine/test_next_transaction_live_smoke.py", "-q")
    session.run("pytest", "tests/core_engine/test_quotes_compat_live_smoke.py", "-q")


@nox.session(python=["3.13"])
def transport_live_smoke(session: nox.Session) -> None:
    session.install("-e", NEXT_INSTALL, "pytest~=9.0.3")
    session.run("pytest", "tests/core_engine/test_transport_live_smoke.py", "-q")


@nox.session(python=["3.13"])
def scheduler_live_smoke(session: nox.Session) -> None:
    session.install("-e", NEXT_INSTALL, "pytest~=9.0.3")
    session.run("pytest", "tests/core_engine/test_scheduler_live_smoke.py", "-q")


@nox.session(python=["3.13"])
def quotes_live_smoke(session: nox.Session) -> None:
    session.install("-e", NEXT_INSTALL, "pytest~=9.0.3")
    session.run("pytest", "tests/core_engine/test_next_quotes_live_smoke.py", "-q")


@nox.session(python=["3.13"])
def history_live_smoke(session: nox.Session) -> None:
    session.install("-e", NEXT_INSTALL, "pytest~=9.0.3")
    session.run("pytest", "tests/core_engine/test_next_history_live_smoke.py", "-q")


@nox.session(python=["3.13"])
def transaction_live_smoke(session: nox.Session) -> None:
    session.install("-e", LEGACY_INSTALL, "pytest~=9.0.3")
    session.run("pytest", "tests/core_engine/test_next_transaction_live_smoke.py", "-q")


@nox.session(python=["3.13"])
def info_live_smoke(session: nox.Session) -> None:
    session.install("-e", NEXT_INSTALL, "pytest~=9.0.3")
    session.run("pytest", "tests/core_engine/test_next_info_live_smoke.py", "-q")


@nox.session(python=["3.13"])
def compat_nightly(session: nox.Session) -> None:
    session.notify("compat_replay")
    session.notify("compat_live_smoke")
    session.notify("history_live_smoke")
    session.notify("transaction_live_smoke")
    session.notify("info_live_smoke")
    session.notify("server_live_smoke")
    session.notify("financial_live_smoke")


@nox.session(python=["3.13"])
def server_live_smoke(session: nox.Session) -> None:
    session.install("-e", NEXT_INSTALL, "pytest~=9.0.3")
    session.run("pytest", "tests/core_engine/test_next_server_live_smoke.py", "-q")


@nox.session(python=["3.13"])
def financial_live_smoke(session: nox.Session) -> None:
    session.install("-e", NEXT_INSTALL, "pytest~=9.0.3")
    session.run("pytest", "tests/core_engine/test_next_financial_live_smoke.py", "-q")


@nox.session(python=["3.13"])
def next_matrix(session: nox.Session) -> None:
    session.install("-e", NEXT_INSTALL, "pytest~=9.0.3")
    session.run(
        "pytest",
        "tests/core_engine/test_next_api_matrix.py",
        "tests/core_engine/test_next_adapter_matrix.py",
        "tests/core_engine/test_report_file_client.py",
        "tests/compat/test_matrix.py",
        "-q",
    )


@nox.session(python=["3.13"])
def next_live_matrix(session: nox.Session) -> None:
    session.install("-e", NEXT_INSTALL, "pytest~=9.0.3")
    session.run(
        "pytest",
        "tests/core_engine/test_next_full_live_matrix.py",
        "-q",
        env={"MOOTDX_RUN_LIVE_MATRIX": "1"},
    )
