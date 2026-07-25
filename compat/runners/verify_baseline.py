from __future__ import annotations

import argparse
import importlib.metadata
from pathlib import Path


EXPECTED = {
    "mootdx": "0.11.7",
    "tdxpy": "0.2.7",
}


def verify() -> dict[str, str]:
    import mootdx
    import tdxpy

    actual = {
        "mootdx": importlib.metadata.version("mootdx"),
        "tdxpy": importlib.metadata.version("tdxpy"),
        "mootdx_path": str(Path(mootdx.__file__).resolve()),
        "tdxpy_path": str(Path(tdxpy.__file__).resolve()),
    }
    for package, expected in EXPECTED.items():
        if actual[package] != expected:
            raise RuntimeError(f"baseline requires {package}=={expected}, got {actual[package]}")

    for package in EXPECTED:
        path = Path(actual[f"{package}_path"])
        if Path("/workspace") in path.parents:
            raise RuntimeError(f"baseline imported workspace package instead of pinned wheel: {path}")
    return actual


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.parse_args()
    actual = verify()
    print(
        "baseline verified: "
        f"mootdx=={actual['mootdx']} ({actual['mootdx_path']}), "
        f"tdxpy=={actual['tdxpy']} ({actual['tdxpy_path']})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
