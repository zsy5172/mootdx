from __future__ import annotations

from mootdx.quotes import StdQuotes


class RecordingBarsClient:
    def __init__(self) -> None:
        self.calls: list[tuple[object, ...]] = []

    def get_security_bars(self, *args):
        self.calls.append(args)
        return [{'datetime': '2026-07-27 00:00:00', 'close': 1.0}]


def test_legacy_bars_sends_bare_code_for_explicit_market_prefix() -> None:
    quotes = object.__new__(StdQuotes)
    quotes.client = RecordingBarsClient()

    result = quotes.bars('SH.600000', offset=1)

    assert not result.empty
    assert quotes.client.calls == [(9, 1, '600000', 0, 1)]
