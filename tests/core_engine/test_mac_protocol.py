from __future__ import annotations

import struct

import pytest

from mootdx_next.mac import (
    MacBoardSortColumn,
    MacField,
    MacFieldPreset,
    MacFieldSelection,
)
from mootdx_next.models import RequestContext, ResponseEnvelope, ServerEndpoint
from mootdx_next.protocol.mac import MacExProtocol, MacProtocol, build_mac_request
from mootdx_next.transport.constants import MAC_EX_LOGIN_PAYLOAD
from mootdx_next.transport.mac import MacExSocketTransport, MacSocketTransport
from tests.core_engine.test_transport_sync import (
    FakeSocket,
    SocketFactory,
    _pack_header,
)


def test_mac_request_header_and_message_id() -> None:
    payload = build_mac_request(0x122B, b"abc")
    assert payload[:10] == struct.pack("<BIBHH", 0x1C, 0, 1, 5, 5)
    assert payload[10:12] == struct.pack("<H", 0x122B)
    assert payload[12:] == b"abc"


def test_mac_ex_uses_extended_head_flag() -> None:
    payload = MacExProtocol().encode("mac_ex_quotes", symbols=[(31, "00700")])
    assert payload[0] == 0x01
    assert struct.unpack_from("<H", payload, 10)[0] == 0x122B


def test_mac_transport_skips_standard_setup() -> None:
    body = b"ok"
    socket = FakeSocket(recv_chunks=[_pack_header(len(body), len(body)), body])
    transport = MacSocketTransport(socket_factory=SocketFactory([socket]))

    envelope = transport.send(
        RequestContext(api="mac_quotes"),
        b"mac-request",
        ServerEndpoint(host="127.0.0.1", port=7709),
    )

    assert socket.sent_payloads == [b"mac-request"]
    assert envelope.body == body


def test_mac_ex_transport_logs_in_once_before_first_request() -> None:
    login_body = b"ok"
    data_body = b"quote"
    socket = FakeSocket(
        recv_chunks=[
            _pack_header(len(login_body), len(login_body)),
            login_body,
            _pack_header(len(data_body), len(data_body)),
            data_body,
            _pack_header(len(data_body), len(data_body)),
            data_body,
        ]
    )
    transport = MacExSocketTransport(socket_factory=SocketFactory([socket]))
    server = ServerEndpoint(host="127.0.0.1", port=7727)

    transport.send(RequestContext(api="mac_quotes"), b"first", server)
    transport.send(RequestContext(api="mac_quotes"), b"second", server)

    assert socket.sent_payloads == [MAC_EX_LOGIN_PAYLOAD, b"first", b"second"]
    assert transport.metrics.sent_requests == 2


def test_mac_bitmap_supports_high_bit_fields() -> None:
    bitmap = MacFieldSelection(MacField.BID_ASK_DIFF, MacField.MAIN_NET_5M_AMOUNT).bitmap()
    assert len(bitmap) == 20
    assert int.from_bytes(bitmap, "little") & (1 << int(MacField.BID_ASK_DIFF))


def test_mac_quotes_decode_dynamic_fields() -> None:
    fields = [MacField.PRE_CLOSE, MacField.MAIN_NET_AMOUNT, MacField.BID_ASK_DIFF]
    bitmap = MacFieldSelection(*fields).bitmap()
    row = struct.pack("<H22s44s", 1, b"600519\x00" + b"\x00" * 15, "贵州茅台".encode("gbk") + b"\x00" * 31)
    row += struct.pack("<f", 1600.0)
    row += struct.pack("<f", 123456.0)
    row += struct.pack("<i", -12)
    body = bitmap + struct.pack("<IH", 1, 1) + row
    decoded = MacProtocol().decode("mac_quotes", ResponseEnvelope(body=body))
    assert decoded[0]["market"] == 1
    assert decoded[0]["code"] == "600519"
    assert decoded[0]["pre_close"] == 1600.0
    assert decoded[0]["main_net_amount"] == 123456.0
    assert decoded[0]["bid_ask_diff"] == -12


def test_mac_quotes_decode_all_high_bit_field_shapes() -> None:
    fields = [
        MacField.BID3_PRICE,
        MacField.BID3_VOLUME,
        MacField.BID_ASK_DIFF,
        MacField.CHANGE_AT_1430,
    ]
    bitmap = MacFieldSelection(*fields).bitmap()
    row = struct.pack("<H22s44s", 1, b"600519\x00" + b"\x00" * 15, b"\x00" * 44)
    row += struct.pack("<fIif", 1342.9, 123, -4, 0.5)
    decoded = MacProtocol().decode(
        "mac_quotes",
        ResponseEnvelope(body=bitmap + struct.pack("<IH", 1, 1) + row),
    )
    assert decoded[0]["bid3_price"] == pytest.approx(1342.9)
    assert decoded[0]["bid3_volume"] == 123
    assert decoded[0]["bid_ask_diff"] == -4
    assert decoded[0]["change_at_1430"] == pytest.approx(0.5)


def test_mac_quotes_decode_live_float_bid_ask_diff() -> None:
    fields = [MacField.BID_ASK_DIFF]
    bitmap = MacFieldSelection(*fields).bitmap()
    row = struct.pack("<H22s44s", 1, b"600519\x00" + b"\x00" * 15, b"\x00" * 44)
    row += struct.pack("<f", 43.0)
    decoded = MacProtocol().decode(
        "mac_quotes",
        ResponseEnvelope(body=bitmap + struct.pack("<IH", 1, 1) + row),
    )
    assert decoded[0]["bid_ask_diff"] == pytest.approx(43.0)


def test_mac_common_fields_are_stable() -> None:
    selection = MacFieldSelection(MacFieldPreset.COMMON)
    assert MacField.PRE_CLOSE in selection
    assert MacField.AMOUNT in selection


def test_mac_command_message_ids_are_stable() -> None:
    protocol = MacProtocol()
    cases = (
        ("mac_quotes", {"symbols": ["sh600519"]}, 0x122B),
        ("mac_quotes_list", {"board_code": 6}, 0x122C),
        ("mac_board_members", {"board_code": 20686}, 0x122C),
        ("mac_board_list", {}, 0x1231),
        ("mac_belong_board", {"symbol": "sh600519"}, 0x1218),
        ("mac_capital_flow", {"symbol": "sh600519"}, 0x1218),
        ("mac_symbol_info", {"symbol": "sh600519"}, 0x122A),
        ("mac_bars", {"symbol": "sh600519"}, 0x122E),
        ("mac_tick_chart", {"symbol": "sh600519"}, 0x122D),
        ("mac_tick_charts", {"symbol": "sh600519"}, 0x123E),
        ("mac_chart_sampling", {"symbol": "sh600519"}, 0x254D),
        ("mac_transactions", {"symbol": "sh600519"}, 0x122F),
        ("mac_auction", {"symbol": "sh600519"}, 0x123D),
        ("mac_unusual", {"market": 1}, 0x1237),
        ("mac_server_info", {}, 0x120F),
        ("mac_kline_offset", {}, 0x124A),
        ("mac_file_meta", {"filename": "block.dat"}, 0x1215),
        ("mac_file_chunk", {"filename": "block.dat"}, 0x1217),
        ("mac_goods_list", {"market": 31}, 0x2562),
    )
    for api, kwargs, expected in cases:
        payload = protocol.encode(api, **kwargs)
        assert struct.unpack_from("<H", payload, 10)[0] == expected


def test_mac_board_list_encodes_requested_sort_column() -> None:
    payload = MacProtocol().encode(
        "mac_board_list", sort_column=MacBoardSortColumn.CHANGE_10D
    )

    assert payload[16] == MacBoardSortColumn.CHANGE_10D


def test_mac_board_list_sort_values_follow_requested_column() -> None:
    row = struct.pack(
        "<H6s16s44sfffH6s16s44sfff",
        1,
        b"880001",
        b"",
        "行业板块".encode("gbk"),
        1234.5,
        6.25,
        1200.0,
        1,
        b"600519",
        b"",
        "贵州茅台".encode("gbk"),
        1500.0,
        4.5,
        1490.0,
    )
    decoded = MacProtocol().decode(
        "mac_board_list", ResponseEnvelope(body=struct.pack("<HH", 2, 1) + row)
    )

    assert decoded[0]["sort_value"] == pytest.approx(6.25)
    assert decoded[0]["symbol_sort_value"] == pytest.approx(4.5)
    assert "rise_speed" not in decoded[0]
    assert "symbol_rise_speed" not in decoded[0]


def test_mac_binary_decoders_cover_market_data_commands() -> None:
    protocol = MacProtocol()

    bars_body = b"\x00" * 24 + struct.pack("<HBHI", 1, 0, 1, 0)
    bars_body += struct.pack("<II7f", 20260813, 0, 1.0, 2.0, 0.5, 1.5, 100.0, 200.0, 300.0)
    bars = protocol.decode("mac_bars", ResponseEnvelope(body=bars_body))
    assert bars[0]["close"] == pytest.approx(1.5)

    tick_body = b"\x00" * 33 + struct.pack("<H", 1)
    tick_body += struct.pack("<HffIf", 570, 1.5, 1.4, 200, 0.1)
    ticks = protocol.decode("mac_tick_chart", ResponseEnvelope(body=tick_body))
    assert ticks[0]["vol"] == 200

    transaction_body = b"\x00" * 29 + struct.pack("<H", 1) + b"\x00" * 8
    transaction_body += struct.pack("<IfIIH", 34200, 1.5, 100, 2, 0)
    transactions = protocol.decode("mac_transactions", ResponseEnvelope(body=transaction_body))
    assert transactions[0]["trade_count"] == 2

    auction_body = b"\x00" * 24 + struct.pack("<I", 1) + b"\x00" * 8
    auction_body += struct.pack("<IfIi", 34200, 1.5, 100, -10)
    auction = protocol.decode("mac_auction", ResponseEnvelope(body=auction_body))
    assert auction[0]["unmatched"] == -10

    unusual_row = struct.pack("<H6sBBBHH", 1, b"600519", 0, 0x0A, 0, 7, 0)
    unusual_row += struct.pack("<BffI", 1, 0.02, 0.0, 0)
    unusual_row += b"\x00" + struct.pack("<BH", 9, 2530)
    unusual = protocol.decode(
        "mac_unusual",
        ResponseEnvelope(body=struct.pack("<H", 1) + unusual_row + ",贵州茅台".encode("gbk")),
    )
    assert unusual[0]["description"] == "单笔冲涨"
    assert unusual[0]["name"] == "贵州茅台"

    limit_row = struct.pack("<H6sBBBHH", 1, b"600519", 0, 0x14, 0, 7, 0)
    limit_row += struct.pack("<BBff3x", 1, 2, 1335.0, 100.0)
    limit_row += b"\x00" + struct.pack("<BH", 9, 2530)
    limit = protocol.decode(
        "mac_unusual",
        ResponseEnvelope(body=struct.pack("<H", 1) + limit_row + ",贵州茅台".encode("gbk")),
    )
    assert limit[0]["description"] == "封跌停板"
    assert limit[0]["value"] == "1335.00/100.00"

    server_body = b"\x00" * 22 + struct.pack("<I", 20260813) + b"\x00" * 4
    server_body += struct.pack("<8H", 570, 690, 780, 900, 0, 0, 0, 0)
    server_body += struct.pack("<8H", 1260, 1380, 0, 0, 0, 0, 0, 0)
    server_body += b"\x00" + struct.pack("<I", 20260812) + b"\x00" * 20
    server_info = protocol.decode("mac_server_info", ResponseEnvelope(body=server_body))
    assert server_info[0]["today"] == "2026-08-13"
    assert server_info[0]["sessions_1"][:2] == [
        {"open": "9:30", "close": "11:30"},
        {"open": "13:00", "close": "15:00"},
    ]
    assert server_info[0]["sessions_2"][0] == {
        "open": "21:00",
        "close": "23:00",
    }
    assert server_info[0]["market_param_1"] == 0
    assert server_info[0]["market_param_2"] == 0

    file_meta = protocol.decode(
        "mac_file_meta",
        ResponseEnvelope(body=struct.pack("<IIb", 3, 7, 1) + b"hash".ljust(32, b"\x00")),
    )
    assert file_meta["size"] == 7
    assert protocol.decode("mac_file_chunk", ResponseEnvelope(body=b"\x00" * 8 + b"chunk")) == b"chunk"

    goods_row = struct.pack("<H23sHIBfffHH", 31, b"00700\x00" + b"\x00" * 18, 1, 2, 1, 1.0, 2.0, 3.0, 4, 5)
    goods = protocol.decode("mac_goods_list", ResponseEnvelope(body=struct.pack("<H", 1) + goods_row))
    assert goods[0]["name"] == "00700"


def test_mac_client_pages_lists_bars_and_transactions_without_truncation(monkeypatch) -> None:
    from mootdx_next import SyncClient

    client = SyncClient(max_retries=0)
    calls: list[tuple[str, int, int]] = []

    def fake_request(api: str, **kwargs):
        start = int(kwargs["start"])
        count = int(kwargs["count"])
        calls.append((api, start, count))
        return [{"offset": start + index} for index in range(count)]

    monkeypatch.setattr(client, "_mac_request", fake_request)

    quotes = client.mac_quotes_list(count=120)
    assert [row["offset"] for row in quotes] == list(range(120))
    assert calls == [("mac_quotes_list", 0, 80), ("mac_quotes_list", 80, 40)]

    calls.clear()
    bars = client.mac_bars("sh600519", count=900)
    assert bars[0]["offset"] == 700
    assert bars[-1]["offset"] == 699
    assert calls == [("mac_bars", 0, 700), ("mac_bars", 700, 200)]

    calls.clear()
    transactions = client.mac_transactions("sh600519", count=2000)
    assert [row["offset"] for row in transactions] == list(range(2000))
    assert calls == [
        ("mac_transactions", 0, 1000),
        ("mac_transactions", 1000, 1000),
    ]

    calls.clear()
    auction = client.mac_auction("sh600519", count=501)
    assert [row["offset"] for row in auction] == list(range(501))
    assert calls == [("mac_auction", 0, 500), ("mac_auction", 500, 1)]

    calls.clear()
    unusual = client.mac_unusual(1, count=601)
    assert [row["offset"] for row in unusual] == list(range(601))
    assert calls == [("mac_unusual", 0, 600), ("mac_unusual", 600, 1)]


def test_mac_tick_charts_assigns_partial_latest_day_before_history() -> None:
    protocol = MacProtocol()
    tick_fmt = struct.Struct("<HffHH")
    tail_fmt = struct.Struct("<44sBHf5x2I5ffIf12s2fI")
    body = bytearray(struct.pack("<H22s", 1, b"600519" + b"\x00" * 16))
    body += struct.pack("<5I", 20260813, 20260812, 0, 0, 0)
    body += struct.pack("<5f", 1343.0, 1346.5, 0.0, 0.0, 0.0)
    body += struct.pack("<HBHH", 2, 1, 4, 5)
    for index in range(5):
        body += tick_fmt.pack(570 + index, 100.0 + index, 100.0, index + 1, 0)
    body += tail_fmt.pack("贵州茅台".encode("gbk") + b"\x00" * 36, 2, 1, 100.0, 20260813, 570, 1343.0, 1343.0, 1343.0, 1343.0, 1343.0, 0.0, 5, 500.0, b"\x00" * 12, 0.0, 1343.0, 0)
    decoded = protocol.decode("mac_tick_charts", ResponseEnvelope(body=bytes(body)), days=2)
    assert len(decoded) == 5
    assert decoded[0]["date"] == "20260813"
    assert decoded[1]["date"] == "20260812"
