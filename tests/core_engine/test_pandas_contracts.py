from __future__ import annotations

import pandas as pd
from pandas.testing import assert_frame_equal

from mootdx.utils import to_data
from mootdx_next import finance_to_frame
from mootdx_next import quotes_to_frame
from mootdx_next import stocks_to_frame


def test_to_data_empty_returns_empty_dataframe() -> None:
    result = to_data(None)

    assert isinstance(result, pd.DataFrame)
    assert result.empty is True


def test_to_data_uses_datetime_and_date_as_index() -> None:
    datetime_rows = [{"datetime": "2026-04-12 09:31:00", "price": 10.0}]
    date_rows = [{"date": "2026-04-12", "value": 1}]

    datetime_df = to_data(datetime_rows)
    date_df = to_data(date_rows)

    assert str(datetime_df.index.dtype).startswith("datetime64")
    assert str(date_df.index.dtype).startswith("datetime64")


def test_quotes_to_frame_adds_volume_alias_without_changing_records() -> None:
    rows = [{"code": "600036", "price": 10.0, "vol": 12}]

    result = quotes_to_frame(rows)

    assert list(result.columns) == ["code", "price", "vol", "volume"]
    assert result.loc[0, "volume"] == 12


def test_stocks_to_frame_preserves_column_order() -> None:
    rows = [{"code": "600036", "name": "招商银行", "volunit": 100}]

    result = stocks_to_frame(rows)
    expected = pd.DataFrame(rows)

    assert_frame_equal(result, expected)


def test_finance_to_frame_returns_single_row_dataframe() -> None:
    result = finance_to_frame({"code": "000001", "liutongguben": 100.0})

    assert list(result.columns) == ["code", "liutongguben"]
    assert result.to_dict(orient="records") == [{"code": "000001", "liutongguben": 100.0}]
