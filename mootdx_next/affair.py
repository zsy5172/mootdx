from __future__ import annotations

from pathlib import Path

from mootdx_next.financial import FinancialFileClient
from mootdx_next.financial import FinancialReader


class Affair:
    """Compatibility-shaped facade backed entirely by next financial services."""

    @staticmethod
    def files():
        with FinancialFileClient() as client:
            return client.files()

    @staticmethod
    def fetch(downdir: str | Path = ".", filename: str | None = None):
        with FinancialFileClient() as client:
            return client.fetch(downdir=downdir, filename=filename)

    @staticmethod
    def parse(downdir: str | Path = ".", filename: str | None = None, **kwargs):
        if not filename:
            return None
        path = Path(downdir) / filename
        if not path.is_file():
            path = Affair.fetch(downdir=downdir, filename=filename)
        return FinancialReader.read(path, header=kwargs.get("header", "zh"))
