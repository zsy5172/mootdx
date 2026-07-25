from pathlib import Path

from mootdx_next.financial import FinancialReader
from mootdx_next.financial import parse_catalog

from .base import BaseFinancial


class FinancialList(BaseFinancial):
    """Legacy catalog parser backed by the next financial implementation."""

    def content(self, report_hook=None, downdir=None, proxies=None, chunk_size=1024 * 50, *args, **kwargs):
        raise self.unsupported_gp()

    def parse(self, download_file, *args, **kwargs):
        with download_file:
            content = download_file.read()
        if not content:
            return None
        return [item.as_dict() for item in parse_catalog(content)]


class Financial(BaseFinancial):
    """Legacy raw-file facade backed by the next in-memory parser."""

    def content(self, report_hook=None, downdir=None, proxies=None, chunk_size=51200, *args, **kwargs):
        raise self.unsupported_gp()

    def parse(self, download_file, *args, **kwargs):
        file_type = Path(download_file.name).suffix
        with download_file:
            payload = download_file.read()
        return FinancialReader.parse_payload(payload, file_type=file_type)

    @staticmethod
    def to_df(data, header="zh"):
        return FinancialReader.to_frame(data, header=header)
