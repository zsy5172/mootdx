from __future__ import annotations

import struct
from collections import OrderedDict
from pathlib import Path

import pandas as pd

_MINUTE_COLUMNS = [
    "date",
    "year",
    "month",
    "day",
    "hour",
    "minute",
    "open",
    "high",
    "low",
    "close",
    "amount",
    "volume",
]
_MINUTE_DATA_COLUMNS = ["open", "high", "low", "close", "amount", "volume"]


class LocalFileNotFoundError(FileNotFoundError):
    pass


class _BinaryReader:
    @staticmethod
    def unpack_records(fmt: str, data: bytes):
        record = struct.Struct(fmt)
        return (record.unpack_from(data, offset) for offset in range(0, len(data), record.size))

    @staticmethod
    def parse_date(num: int) -> tuple[int, int, int]:
        month = (num % 2048) // 100
        year = num // 2048 + 2004
        day = (num % 2048) % 100
        return year, month, day

    @staticmethod
    def parse_time(num: int) -> tuple[int, int]:
        return num // 60, num % 60


class StdDailyBarReader(_BinaryReader):
    security_exchange = ["sz", "sh"]
    security_type = [
        "SH_A_STOCK",
        "SH_B_STOCK",
        "SH_STAR_STOCK",
        "SH_INDEX",
        "SH_FUND",
        "SH_BOND",
        "SZ_A_STOCK",
        "SZ_B_STOCK",
        "SZ_INDEX",
        "SZ_FUND",
        "SZ_BOND",
    ]
    security_coefficient = {
        "SH_A_STOCK": [0.01, 0.01],
        "SH_B_STOCK": [0.001, 0.01],
        "SH_STAR_STOCK": [0.01, 0.01],
        "SH_INDEX": [0.01, 1.0],
        "SH_FUND": [0.001, 1.0],
        "SH_BOND": [0.001, 1.0],
        "SZ_A_STOCK": [0.01, 0.01],
        "SZ_B_STOCK": [0.01, 0.01],
        "SZ_INDEX": [0.01, 1.0],
        "SZ_FUND": [0.001, 0.01],
        "SZ_BOND": [0.001, 0.01],
    }

    def parse_data_by_file(self, filename: str | Path):
        path = Path(filename)
        if not path.is_file():
            raise LocalFileNotFoundError(f"no tdx kline data, please check path {path}")

        return self.unpack_records("<IIIIIfII", path.read_bytes())

    def get_df(self, filename: str | Path) -> pd.DataFrame:
        path = Path(filename)
        security_type = self.get_security_type(path)
        if security_type not in self.security_type:
            raise NotImplementedError("unknown security type")

        coefficient = self.security_coefficient[security_type]
        data = [self._convert_row(row, coefficient) for row in self.parse_data_by_file(path)]
        df = pd.DataFrame(data=data, columns=["date", "open", "high", "low", "close", "amount", "volume"])
        df.index = pd.to_datetime(df.date, errors="coerce")
        return df[["open", "high", "low", "close", "amount", "volume"]]

    def get_security_type(self, filename: str | Path) -> str:
        basename = Path(filename).stem.lower()
        exchange = basename[:2]
        code_head = basename[2:4]

        if exchange == self.security_exchange[0]:
            if code_head in ["00", "30"]:
                return "SZ_A_STOCK"
            if code_head in ["20"]:
                return "SZ_B_STOCK"
            if code_head in ["39"]:
                return "SZ_INDEX"
            if code_head in ["15", "16", "18"]:
                return "SZ_FUND"
            if code_head in ["10", "11", "12", "13", "14"]:
                return "SZ_BOND"

        if exchange == self.security_exchange[1]:
            if code_head in ["60"]:
                return "SH_A_STOCK"
            if code_head in ["68"]:
                return "SH_STAR_STOCK"
            if code_head in ["90"]:
                return "SH_B_STOCK"
            if code_head in ["00", "88", "99"]:
                return "SH_INDEX"
            if code_head in ["50", "51", "58"]:
                return "SH_FUND"
            if code_head in ["01", "02", "10", "11", "12", "13", "14", "15", "16", "17", "18", "19", "20"]:
                return "SH_BOND"

        raise NotImplementedError("unknown security exchange")

    @staticmethod
    def _convert_row(row, coefficient: list[float]) -> tuple[str, float, float, float, float, float, float]:
        t_date = str(row[0])
        datestr = f"{t_date[:4]}-{t_date[4:6]}-{t_date[6:]}"
        return (
            datestr,
            row[1] * coefficient[0],
            row[2] * coefficient[0],
            row[3] * coefficient[0],
            row[4] * coefficient[0],
            row[5],
            row[6] * coefficient[1],
        )


class StdMinBarReader(_BinaryReader):
    def parse_data_by_file(self, filename: str | Path):
        path = Path(filename)
        if not path.is_file():
            raise LocalFileNotFoundError(f"no tdx kline data, please check path {path}")

        return self.unpack_records("<HHIIIIfII", path.read_bytes())

    def get_df(self, filename: str | Path) -> pd.DataFrame:
        rows = []
        for row in self.parse_data_by_file(filename):
            year, month, day = self.parse_date(row[0])
            hour, minute = self.parse_time(row[1])
            rows.append(
                OrderedDict(
                    [
                        ("date", f"{year:04d}-{month:02d}-{day:02d} {hour:02d}:{minute:02d}"),
                        ("year", year),
                        ("month", month),
                        ("day", day),
                        ("hour", hour),
                        ("minute", minute),
                        ("open", row[2] / 100),
                        ("high", row[3] / 100),
                        ("low", row[4] / 100),
                        ("close", row[5] / 100),
                        ("amount", row[6]),
                        ("volume", row[7]),
                    ]
                )
            )

        df = pd.DataFrame(data=rows, columns=_MINUTE_COLUMNS)
        df.index = pd.to_datetime(df["date"])
        return df[_MINUTE_DATA_COLUMNS]


class StdLCMinBarReader(_BinaryReader):
    def parse_data_by_file(self, filename: str | Path):
        path = Path(filename)
        if not path.is_file():
            raise LocalFileNotFoundError(f"no tdx kline data, please check path {path}")

        return self.unpack_records("<HHfffffII", path.read_bytes())

    def get_df(self, filename: str | Path) -> pd.DataFrame:
        rows = []
        for row in self.parse_data_by_file(filename):
            year, month, day = self.parse_date(row[0])
            hour, minute = self.parse_time(row[1])
            rows.append(
                OrderedDict(
                    [
                        ("date", f"{year:04d}-{month:02d}-{day:02d} {hour:02d}:{minute:02d}"),
                        ("year", year),
                        ("month", month),
                        ("day", day),
                        ("hour", hour),
                        ("minute", minute),
                        ("open", row[2]),
                        ("high", row[3]),
                        ("low", row[4]),
                        ("close", row[5]),
                        ("amount", row[6]),
                        ("volume", row[7]),
                    ]
                )
            )

        df = pd.DataFrame(data=rows, columns=_MINUTE_COLUMNS)
        df.index = pd.to_datetime(df["date"])
        return df[_MINUTE_DATA_COLUMNS]


class ExtBarReader(_BinaryReader):
    def parse_data_by_file(self, filename: str | Path):
        path = Path(filename)
        if not path.is_file():
            raise LocalFileNotFoundError(f"no tdx kline data, please check path {path}")

        return self.unpack_records("<IffffIIf", path.read_bytes())

    def get_df(self, filename: str | Path) -> pd.DataFrame:
        columns = ["date", "open", "high", "low", "close", "amount", "volume", "jiesuan", "hk_stock_amount"]
        data = [self._convert_row(row) for row in self.parse_data_by_file(filename)]
        df = pd.DataFrame(data=data, columns=columns)
        df.index = pd.to_datetime(df.date)
        return df[["open", "high", "low", "close", "amount", "volume", "jiesuan", "hk_stock_amount"]]

    @staticmethod
    def _convert_row(row):
        t_date = str(row[0])
        datestr = f"{t_date[:4]}-{t_date[4:6]}-{t_date[6:]}"
        (hk_stock_amount,) = struct.unpack("<f", struct.pack("<I", row[5]))
        return (datestr, row[1], row[2], row[3], row[4], row[5], row[6], row[7], hk_stock_amount)


class BlockReader:
    type_flat = 0
    type_group = 1

    def get_df(self, name: str | Path | bytes | bytearray, result_type: int = type_flat) -> pd.DataFrame:
        return pd.DataFrame(self.get_data(name, result_type))

    @staticmethod
    def get_data(name: str | Path | bytes | bytearray, result_type: int = type_flat):
        result = []
        data = name if isinstance(name, (bytes, bytearray)) else Path(name).read_bytes()

        pos = 384
        (num,) = struct.unpack("<H", data[pos:pos + 2])
        pos += 2

        for _ in range(num):
            block_name_raw = data[pos:pos + 9]
            pos += 9

            block_name = block_name_raw.decode("gbk", "ignore").rstrip("\x00")
            stock_count, block_type = struct.unpack("<HH", data[pos:pos + 4])
            pos += 4

            block_stock_begin = pos
            codes: list[str] = []
            for code_index in range(stock_count):
                one_code = data[pos:pos + 7].decode("utf-8", "ignore").rstrip("\x00")
                pos += 7
                if result_type == BlockReader.type_flat:
                    result.append(
                        OrderedDict(
                            [
                                ("blockname", block_name),
                                ("block_type", block_type),
                                ("code_index", code_index),
                                ("code", one_code),
                            ]
                        )
                    )
                else:
                    codes.append(one_code)

            if result_type == BlockReader.type_group:
                result.append(
                    OrderedDict(
                        [
                            ("blockname", block_name),
                            ("block_type", block_type),
                            ("stock_count", stock_count),
                            ("code_list", ",".join(codes)),
                        ]
                    )
                )

            pos = block_stock_begin + 2800

        return result


class CustomerBlockReader:
    def get_df(self, name: str | Path, result_type: int = BlockReader.type_flat) -> pd.DataFrame:
        return pd.DataFrame(self.get_data(name, result_type))

    @staticmethod
    def get_data(name: str | Path, result_type: int = BlockReader.type_flat):
        root = Path(name)
        if not root.is_dir():
            raise Exception("not a directory")

        block_file = root / "blocknew.cfg"
        if not block_file.exists():
            raise Exception("file not exists")

        block_data = block_file.read_bytes()
        pos = 0
        result = []

        while pos < len(block_data):
            block_name = block_data[pos:pos + 50].decode("gbk", "ignore").rstrip("\x00").split("\x00")[0]
            block_type = block_data[pos + 50:pos + 120].decode("gbk", "ignore").rstrip("\x00").split("\x00")[0]
            pos += 120

            blk_file = root / f"{block_type}.blk"
            if not blk_file.exists():
                raise Exception("file not exists")

            codes = blk_file.read_text().splitlines()
            if result_type == BlockReader.type_flat:
                for index, code in enumerate(codes):
                    if code:
                        result.append(
                            OrderedDict(
                                [
                                    ("blockname", block_name),
                                    ("block_type", block_type),
                                    ("code_index", index),
                                    ("code", code[1:]),
                                ]
                            )
                        )
            else:
                cc = [code[1:] for code in codes if code]
                result.append(
                    OrderedDict(
                        [
                            ("blockname", block_name),
                            ("block_type", block_type),
                            ("stock_count", len(cc)),
                            ("code_list", ",".join(cc)),
                        ]
                    )
                )

        return result
