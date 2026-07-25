from __future__ import annotations

import io
import struct
import zipfile
from pathlib import Path
from pathlib import PurePosixPath

import pandas as pd

from mootdx_next.errors import FinancialFileFormatError
from mootdx_next.errors import UnsafeArchiveError
from mootdx_next.financial.columns import COLUMNS

HEADER = struct.Struct("<1hI1H3L")
STOCK_ITEM = struct.Struct("<6s1c1L")


class FinancialReader:
    """Read TDX gpcw ZIP or DAT files without extracting archives to disk."""

    @classmethod
    def read(cls, filename: str | Path, header: str = "zh") -> pd.DataFrame:
        path = cls._resolve_path(filename)
        payload = path.read_bytes()
        if path.suffix.lower() == ".zip":
            payload = cls._read_zip(payload)
        elif path.suffix.lower() != ".dat":
            raise FinancialFileFormatError(f"unsupported financial file type: {path.suffix}")
        return cls.to_frame(cls.parse_bytes(payload), header=header)

    @classmethod
    def to_data(cls, filename: str | Path, **kwargs) -> pd.DataFrame:
        return cls.read(filename, header=kwargs.get("header", "zh"))

    @staticmethod
    def parse_bytes(payload: bytes) -> list[tuple[object, ...]]:
        if len(payload) < HEADER.size:
            raise FinancialFileFormatError("financial DAT header is truncated")

        try:
            header = HEADER.unpack_from(payload)
        except struct.error as exc:
            raise FinancialFileFormatError("cannot decode financial DAT header") from exc

        report_date = int(header[1])
        stock_count = int(header[2])
        report_size = int(header[4])
        if report_size <= 0 or report_size % 4:
            raise FinancialFileFormatError(f"invalid financial report size: {report_size}")

        fields = report_size // 4
        report = struct.Struct(f"<{fields}f")
        table_end = HEADER.size + stock_count * STOCK_ITEM.size
        if table_end > len(payload):
            raise FinancialFileFormatError("financial stock table is truncated")

        rows: list[tuple[object, ...]] = []
        for index in range(stock_count):
            item_offset = HEADER.size + index * STOCK_ITEM.size
            try:
                code_raw, _, data_offset = STOCK_ITEM.unpack_from(payload, item_offset)
            except struct.error as exc:
                raise FinancialFileFormatError(f"financial stock item {index} is truncated") from exc

            data_offset = int(data_offset)
            if data_offset < table_end or data_offset + report.size > len(payload):
                raise FinancialFileFormatError(f"financial report offset is invalid for stock item {index}")
            try:
                values = report.unpack_from(payload, data_offset)
            except struct.error as exc:
                raise FinancialFileFormatError(f"financial report is truncated for stock item {index}") from exc

            try:
                code = code_raw.rstrip(b"\x00").decode("ascii")
            except UnicodeDecodeError as exc:
                raise FinancialFileFormatError(f"financial stock code {index} is not ASCII") from exc
            if not code:
                raise FinancialFileFormatError(f"financial stock code {index} is blank")
            rows.append((code, report_date, *values))
        return rows

    @staticmethod
    def to_frame(data: list[tuple[object, ...]], header: str = "zh") -> pd.DataFrame:
        if not data:
            return pd.DataFrame()

        width = len(data[0])
        if width < 2 or any(len(row) != width for row in data):
            raise FinancialFileFormatError("financial rows have inconsistent field counts")

        columns = ["code", "report_date", *(f"col{index}" for index in range(1, width - 1))]
        frame = pd.DataFrame.from_records(data, columns=columns)
        frame.set_index("code", inplace=True)
        if header == "zh":
            labels = list(COLUMNS)
            if len(labels) < len(frame.columns):
                labels.extend(frame.columns[len(labels):])
            frame.columns = labels[: len(frame.columns)]
        return frame

    @staticmethod
    def _resolve_path(filename: str | Path) -> Path:
        path = Path(filename)
        if path.is_file():
            return path
        if not path.suffix:
            for suffix in (".zip", ".dat"):
                candidate = path.with_suffix(suffix)
                if candidate.is_file():
                    return candidate
        raise FileNotFoundError(path)

    @staticmethod
    def _read_zip(payload: bytes) -> bytes:
        try:
            archive = zipfile.ZipFile(io.BytesIO(payload))
        except (OSError, zipfile.BadZipFile) as exc:
            raise FinancialFileFormatError("invalid financial ZIP archive") from exc

        with archive:
            dat_members = []
            for info in archive.infolist():
                member = PurePosixPath(info.filename.replace("\\", "/"))
                if member.is_absolute() or ".." in member.parts:
                    raise UnsafeArchiveError(f"unsafe financial ZIP member: {info.filename}")
                if not info.is_dir() and member.suffix.lower() == ".dat":
                    dat_members.append(info)

            if len(dat_members) != 1:
                raise FinancialFileFormatError(
                    f"financial ZIP must contain exactly one DAT file, found {len(dat_members)}"
                )
            try:
                return archive.read(dat_members[0])
            except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
                raise FinancialFileFormatError("cannot read financial DAT from ZIP") from exc
