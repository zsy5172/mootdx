from __future__ import annotations

import struct

import pandas as pd
import pytest

from mootdx_next import BaseParse
from mootdx_next import Customize
from mootdx_next import ExtBarReader
from mootdx_next import LocalFileFormatError
from mootdx_next import Reader
from mootdx_next import StdDailyBarReader
from mootdx_next import StdLCMinBarReader
from mootdx_next import StdMinBarReader

FIXTURE_DIR = "tests/fixtures"


def test_std_reader_daily_and_minute_work_without_legacy_reader() -> None:
    reader = Reader.factory(market="std", tdxdir=FIXTURE_DIR)

    daily = reader.daily(symbol="127021")
    assert not daily.empty

    minute = reader.minute(symbol="688001", suffix="1")
    assert not minute.empty

    fzline = reader.minute(symbol="688001", suffix="5")
    assert not fzline.empty


def test_ext_reader_local_files_work_without_legacy_reader() -> None:
    reader = Reader.factory(market="ext", tdxdir=FIXTURE_DIR)

    daily = reader.daily(symbol="4#CF7D0LAO")
    assert not daily.empty

    minute = reader.minute(symbol="4#CF7D0LAO")
    assert minute is None


def test_ext_daily_reader_preserves_float_amount_and_integer_volume(tmp_path) -> None:
    path = tmp_path / "4#CF7D0LAO.day"
    path.write_bytes(
        struct.pack(
            "<IfffffIf",
            20230911,
            102.5,
            105.0,
            101.0,
            104.0,
            186871758848.0,
            105358016,
            103.5,
        )
    )

    frame = ExtBarReader().get_df(path)

    assert list(frame.columns) == [
        "open",
        "high",
        "low",
        "close",
        "amount",
        "volume",
        "jiesuan",
    ]
    assert frame.loc[pd.Timestamp("2023-09-11"), "amount"] == pytest.approx(
        186871758848.0
    )
    assert frame.loc[pd.Timestamp("2023-09-11"), "volume"] == 105358016
    assert frame.loc[pd.Timestamp("2023-09-11"), "jiesuan"] == pytest.approx(103.5)


def test_reader_accepts_tdx_root_or_vipdoc_path() -> None:
    root_reader = Reader.factory(market="std", tdxdir=FIXTURE_DIR)
    vipdoc_reader = Reader.factory(market="std", tdxdir=f"{FIXTURE_DIR}/vipdoc")

    pd.testing.assert_frame_equal(
        root_reader.daily(symbol="127021"),
        vipdoc_reader.daily(symbol="127021"),
    )


def test_bj_daily_reader_resolves_920_path_and_coefficients(tmp_path) -> None:
    daily_dir = tmp_path / "vipdoc" / "bj" / "lday"
    daily_dir.mkdir(parents=True)
    daily_file = daily_dir / "bj920001.day"
    daily_file.write_bytes(
        struct.pack(
            "<IIIIIfII",
            20260727,
            1000,
            1100,
            900,
            1050,
            12345.0,
            2000,
            0,
        )
    )

    reader = Reader.factory(market="std", tdxdir=tmp_path)
    result = reader.daily(symbol="920001")

    assert reader.find_path("920001", subdir="lday", suffix="day") == daily_file
    assert result.loc[pd.Timestamp("2026-07-27"), "open"] == pytest.approx(10.0)
    assert result.loc[pd.Timestamp("2026-07-27"), "close"] == pytest.approx(10.5)
    assert result.loc[pd.Timestamp("2026-07-27"), "volume"] == pytest.approx(20.0)


def test_bj_daily_reader_distinguishes_stock_and_index_types() -> None:
    reader = StdDailyBarReader()

    assert reader.get_security_type("bj920001.day") == "BJ_A_STOCK"
    assert reader.get_security_type("bj899001.day") == "BJ_INDEX"


@pytest.mark.parametrize("reader_class", [StdMinBarReader, StdLCMinBarReader])
def test_std_minute_readers_return_empty_frame_for_empty_file(tmp_path, reader_class) -> None:
    path = tmp_path / "empty.lc1"
    path.touch()

    result = reader_class().get_df(path)

    assert result.empty
    assert list(result.columns) == ["open", "high", "low", "close", "amount", "volume"]
    assert isinstance(result.index, pd.DatetimeIndex)


@pytest.mark.parametrize(
    ("reader", "filename"),
    [
        (StdDailyBarReader(), "sh600036.day"),
        (StdMinBarReader(), "sh600036.1"),
        (StdLCMinBarReader(), "sh600036.lc1"),
    ],
)
def test_local_binary_readers_reject_truncated_records(tmp_path, reader, filename) -> None:
    path = tmp_path / filename
    path.write_bytes(b"\x00")

    with pytest.raises(LocalFileFormatError, match="truncated binary record"):
        reader.get_df(path)


def test_block_parse_and_custom_block_reader_work_without_legacy_reader(tmp_path) -> None:
    reader = Reader.factory(market="std", tdxdir=FIXTURE_DIR)
    parse = BaseParse(tdxdir=FIXTURE_DIR)

    block_df = reader.block(symbol="block_gn.dat")
    assert not block_df.empty

    cfg_df = parse.cfg("T0002/hq_cache/tdxhy.cfg")
    assert not cfg_df.empty

    incon = reader.block(symbol="incon.dat")
    assert incon

    workdir = tmp_path / "tdx"
    blocknew = workdir / "T0002" / "blocknew"
    blocknew.mkdir(parents=True)
    custom = Customize(tdxdir=str(workdir))

    assert custom.create(name="龙虎榜", symbol=["600036", "600016"], blk_file="block_a")
    assert custom.search(name="龙虎榜")
    assert custom.update(name="龙虎榜", symbol=["600000"])
    assert custom.remove(name="龙虎榜") is not None

    assert custom.search(name="龙虎榜") is None
