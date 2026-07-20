from __future__ import annotations

import pandas as pd
import pytest

from mootdx.localfiles import StdLCMinBarReader
from mootdx.localfiles import StdMinBarReader
from mootdx.parse import BaseParse
from mootdx.reader import Reader
from mootdx.tools.customize import Customize

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


@pytest.mark.parametrize("reader_class", [StdMinBarReader, StdLCMinBarReader])
def test_std_minute_readers_return_empty_frame_for_empty_file(tmp_path, reader_class) -> None:
    path = tmp_path / "empty.lc1"
    path.touch()

    result = reader_class().get_df(path)

    assert result.empty
    assert list(result.columns) == ["open", "high", "low", "close", "amount", "volume"]
    assert isinstance(result.index, pd.DatetimeIndex)


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
