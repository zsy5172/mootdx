from __future__ import annotations

import pytest

from mootdx.affair import Affair
from mootdx.exceptions import MootdxValidationException
from mootdx.financial.financial import Financial
from mootdx.financial.financial import FinancialList


def test_financial_list_is_explicitly_unsupported() -> None:
    with pytest.raises(MootdxValidationException, match="GP 财务下载线路已经废弃且不再支持"):
        FinancialList().fetch_and_parse()


def test_financial_file_fetch_is_explicitly_unsupported() -> None:
    with pytest.raises(MootdxValidationException, match="GP 财务下载线路已经废弃且不再支持"):
        Financial().fetch_and_parse(filename="gpcw20220331.zip", filesize=0)


def test_affair_files_and_fetch_are_explicitly_unsupported() -> None:
    with pytest.raises(MootdxValidationException, match="GP 财务下载线路已经废弃且不再支持"):
        Affair.files()

    with pytest.raises(MootdxValidationException, match="GP 财务下载线路已经废弃且不再支持"):
        Affair.fetch(downdir=".", filename="gpcw20220331.zip")
