from __future__ import annotations

import math
import struct
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas

from compat.common import build_artifact
from compat.common import load_json
from compat.common import normalize_value
from compat.common import write_json
from compat.matrix import iter_cases
from mootdx.quotes import Quotes
from mootdx.quotes import count_weekdays
from mootdx.utils import get_frequency
from mootdx.utils import get_stock_market
from mootdx.utils import get_stock_markets
from mootdx.utils import to_data
from mootdx_next import bars_to_frame
from mootdx_next import block_to_frame
from mootdx_next import f10_categories_to_frame
from mootdx_next import finance_to_frame
from mootdx_next import minutes_to_frame
from mootdx_next import ServerEndpoint
from mootdx_next import StdQuoteProtocol
from mootdx_next import SyncClient
from mootdx_next import quotes_to_frame
from mootdx_next import stocks_to_frame
from mootdx_next import transaction_to_frame
from mootdx_next import transactions_to_frame
from mootdx_next import xdxr_to_frame
from tdxpy.parser.std.get_block_info import GetBlockInfo
from tdxpy.parser.std.get_block_info import GetBlockInfoMeta
from tdxpy.parser.std.get_company_info_category import GetCompanyInfoCategory
from tdxpy.parser.std.get_company_info_content import GetCompanyInfoContent
from tdxpy.parser.std.get_finance_info import GetFinanceInfo
from tdxpy.parser.std.get_index_bars import GetIndexBarsCmd
from tdxpy.parser.std.get_security_count import GetSecurityCountCmd
from tdxpy.parser.std.get_history_minute_time_data import GetHistoryMinuteTimeData
from tdxpy.parser.std.get_history_transaction_data import GetHistoryTransactionData
from tdxpy.parser.std.get_security_list import GetSecurityList
from tdxpy.parser.std.get_security_bars import GetSecurityBarsCmd
from tdxpy.parser.std.get_security_quotes import GetSecurityQuotesCmd
from tdxpy.parser.std.get_transaction_data import GetTransactionData
from tdxpy.parser.std.get_xdxr_info import GetXdXrInfo

RSP_HEADER_LEN = 0x10
DEFAULT_CAPTURE_SERVER = ("110.41.174.169", 7709)


@dataclass(slots=True)
class StepCapture:
    step_id: str
    parser: str
    kwargs: dict[str, Any]
    request: bytes
    response_body: bytes
    parsed: Any


def load_spec(path: str | Path) -> dict[str, Any]:
    spec = load_json(path)
    if "case_id" not in spec or "api" not in spec or "comparator" not in spec:
        raise ValueError(f"invalid compat spec: {path}")
    return spec


def list_spec_paths(spec_root: str | Path) -> list[Path]:
    root = Path(spec_root)
    return sorted(path for path in root.rglob("*.json"))


def generated_spec_paths(spec_root: str | Path) -> list[Path]:
    root = Path(spec_root)
    return sorted(root / case.api / f"{case.case_id}.json" for case in iter_cases())


def corpus_case_dir(corpus_root: str | Path, spec: dict[str, Any]) -> Path:
    return Path(corpus_root) / spec["api"] / spec["case_id"]


def manifest_path(corpus_root: str | Path, spec: dict[str, Any]) -> Path:
    return corpus_case_dir(corpus_root, spec) / "manifest.json"


def expected_path(corpus_root: str | Path, spec: dict[str, Any]) -> Path:
    return corpus_case_dir(corpus_root, spec) / "expected.json"


def build_live_artifact(spec: dict[str, Any], runtime: str = "legacy") -> dict[str, Any]:
    client = None
    try:
        client = _build_client(spec, runtime=runtime)
        result = _normalize_next_result_for_artifact(spec, _capture_live_result(client, spec), runtime=runtime)
        return build_artifact(
            case_id=spec["case_id"],
            api=spec["api"],
            comparator=spec["comparator"],
            result=result,
        )
    except Exception as exc:
        return build_artifact(
            case_id=spec["case_id"],
            api=spec["api"],
            comparator=spec["comparator"],
            error=exc,
        )
    finally:
        if client is not None:
            _close_client(client)


def build_live_decode_artifact_pair(spec: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    client = _build_client(spec, runtime="legacy")
    try:
        steps, legacy_result = _capture_case(client, spec)
        next_result = _result_from_captured_steps_next(spec, steps)
        return (
            build_artifact(
                case_id=spec["case_id"],
                api=spec["api"],
                comparator=spec["comparator"],
                result=legacy_result,
            ),
            build_artifact(
                case_id=spec["case_id"],
                api=spec["api"],
                comparator=spec["comparator"],
                result=next_result,
            ),
        )
    finally:
        _close_client(client)


def capture_corpus_case(spec: dict[str, Any], corpus_root: str | Path) -> dict[str, Any]:
    client = _build_client(spec)
    case_dir = corpus_case_dir(corpus_root, spec)
    steps_dir = case_dir / "steps"
    steps_dir.mkdir(parents=True, exist_ok=True)

    try:
        steps, result = _capture_case(client, spec)
        manifest_steps: list[dict[str, Any]] = []
        for step in steps:
            step_dir = steps_dir / step.step_id
            step_dir.mkdir(parents=True, exist_ok=True)
            request_path = step_dir / "request.bin"
            response_path = step_dir / "response.body.bin"
            request_path.write_bytes(step.request)
            response_path.write_bytes(step.response_body)
            manifest_steps.append(
                {
                    "id": step.step_id,
                    "parser": step.parser,
                    "kwargs": normalize_value(step.kwargs),
                    "request_path": str(request_path.relative_to(case_dir)),
                    "response_body_path": str(response_path.relative_to(case_dir)),
                }
            )

        manifest = {
            "case_id": spec["case_id"],
            "api": spec["api"],
            "comparator": spec["comparator"],
            "steps": manifest_steps,
            "expected_path": "expected.json",
        }
        artifact = build_artifact(
            case_id=spec["case_id"],
            api=spec["api"],
            comparator=spec["comparator"],
            result=result,
        )
        write_json(case_dir / "manifest.json", manifest)
        write_json(case_dir / "expected.json", artifact)
        return artifact
    finally:
        _close_client(client)


def replay_case(spec: dict[str, Any], corpus_root: str | Path, runtime: str = "legacy") -> dict[str, Any]:
    case_dir = corpus_case_dir(corpus_root, spec)
    manifest = load_json(case_dir / "manifest.json")
    if runtime == "next":
        result = _replay_case_next(spec, case_dir, manifest)
        return build_artifact(
            case_id=spec["case_id"],
            api=spec["api"],
            comparator=spec["comparator"],
            result=result,
        )

    parsed_steps = []
    for step in manifest["steps"]:
        response_body = (case_dir / step["response_body_path"]).read_bytes()
        parser = _make_parser(step["parser"])
        _prime_parser_for_replay(parser, step)
        parsed_steps.append(
            {
                "id": step["id"],
                "parser": step["parser"],
                "kwargs": step["kwargs"],
                "parsed": parser.parseResponse(response_body),
            }
        )

    result = _aggregate_case(spec, parsed_steps)
    return build_artifact(
        case_id=spec["case_id"],
        api=spec["api"],
        comparator=spec["comparator"],
        result=result,
    )


def _build_client(spec: dict[str, Any], runtime: str = "legacy") -> Any:
    if runtime == "next":
        return _build_next_client(spec)
    if runtime != "legacy":
        raise ValueError(f"unsupported runtime: {runtime}")

    client_spec = spec["client"]
    if client_spec.get("kind", "quotes") != "quotes":
        raise ValueError(f"unsupported client kind: {client_spec.get('kind')}")

    factory_kwargs = dict(client_spec.get("factory", {}))
    factory_kwargs.setdefault("engine", "legacy")
    factory_kwargs.setdefault("server", list(DEFAULT_CAPTURE_SERVER))
    return Quotes.factory(**factory_kwargs)


def _build_next_client(spec: dict[str, Any]) -> SyncClient:
    if spec["api"] not in {
        "stock_count",
        "stocks",
        "quotes",
        "bars",
        "minutes",
        "minute",
        "transaction",
        "transactions",
        "finance",
        "xdxr",
        "f10_categories",
        "f10_content",
        "index_bars",
        "block",
    }:
        raise ValueError(f"unsupported api for next runtime: {spec['api']}")

    client_spec = spec["client"]
    server = tuple(client_spec.get("factory", {}).get("server", DEFAULT_CAPTURE_SERVER))
    endpoint = ServerEndpoint(host=str(server[0]), port=int(server[1]), label=f"{spec['case_id']}-server")
    return SyncClient(servers=[endpoint])


def _close_client(client: Any) -> None:
    if hasattr(client, "connection_pool"):
        try:
            client.connection_pool.close_all()
        except Exception:
            pass

    if hasattr(client, "close"):
        try:
            client.close()
            return
        except Exception:
            pass

    if hasattr(client, "client") and hasattr(client.client, "close"):
        try:
            client.client.close()
        except Exception:
            pass


def _execute_command(cmd) -> tuple[bytes, bytes, Any]:
    cmd.setup()
    if not cmd.send_pkg:
        raise ValueError("command send_pkg not ready")

    request = bytes(cmd.send_pkg)
    descended = cmd.client.send(request)
    if descended != len(request):
        raise RuntimeError("failed to send full request")

    header = cmd.client.recv(RSP_HEADER_LEN)
    if len(header) != RSP_HEADER_LEN:
        raise RuntimeError(f"invalid response header length: {len(header)}")

    _, _, _, zip_size, unzip_size = struct.unpack("<IIIHH", header)

    body = bytearray()
    while len(body) < zip_size:
        chunk = cmd.client.recv(zip_size - len(body))
        if not chunk:
            break
        body.extend(chunk)

    if len(body) != zip_size:
        raise RuntimeError(f"incomplete response body: expected {zip_size}, got {len(body)}")

    decoded = bytes(body)
    if zip_size != unzip_size:
        decoded = zlib.decompress(decoded)

    parsed = cmd.parseResponse(decoded)
    return request, decoded, parsed


def _capture_case(client: Any, spec: dict[str, Any]) -> tuple[list[StepCapture], Any]:
    api_client = client.client
    socket_client = api_client.client
    api = spec["api"]
    if api == "stock_count":
        market = int(spec["call"]["kwargs"]["market"])
        cmd = GetSecurityCountCmd(socket_client, lock=api_client.lock)
        cmd.setParams(market)
        request, response_body, parsed = _execute_command(cmd)
        step = StepCapture(
            step_id="01_stock_count",
            parser="GetSecurityCountCmd",
            kwargs={"market": market},
            request=request,
            response_body=response_body,
            parsed=parsed,
        )
        return [step], parsed

    if api == "stocks":
        market = int(spec["call"]["kwargs"]["market"])
        stock_count_cmd = GetSecurityCountCmd(socket_client, lock=api_client.lock)
        stock_count_cmd.setParams(market)
        request, response_body, parsed_count = _execute_command(stock_count_cmd)
        steps = [
            StepCapture(
                step_id="01_stock_count",
                parser="GetSecurityCountCmd",
                kwargs={"market": market},
                request=request,
                response_body=response_body,
                parsed=parsed_count,
            )
        ]

        stocks = None
        step_index = 2
        for start in range(0, parsed_count, 1000):
            list_cmd = GetSecurityList(socket_client, lock=api_client.lock)
            list_cmd.setParams(market, start)
            request, response_body, parsed_page = _execute_command(list_cmd)
            step_id = f"{step_index:02d}_list_{start:05d}"
            steps.append(
                StepCapture(
                    step_id=step_id,
                    parser="GetSecurityList",
                    kwargs={"market": market, "start": start},
                    request=request,
                    response_body=response_body,
                    parsed=parsed_page,
                )
            )
            page_df = to_data(parsed_page)
            stocks = pandas.concat([stocks, page_df], ignore_index=True) if start > 1 else page_df
            step_index += 1

        return steps, stocks

    if api == "quotes":
        symbol = spec["call"]["kwargs"]["symbol"]
        symbols = [symbol] if isinstance(symbol, str) else list(symbol)
        all_stock = get_stock_markets(symbols)
        cmd = GetSecurityQuotesCmd(socket_client, lock=api_client.lock)
        cmd.setParams(all_stock)
        request, response_body, parsed = _execute_command(cmd)
        step = StepCapture(
            step_id="01_quotes",
            parser="GetSecurityQuotesCmd",
            kwargs={"all_stock": [[market, code] for market, code in all_stock]},
            request=request,
            response_body=response_body,
            parsed=parsed,
        )
        return [step], to_data(parsed, symbol=all_stock, client=client)

    if api == "bars":
        kwargs = spec["call"]["kwargs"]
        symbol = str(kwargs["symbol"])
        frequency = get_frequency(kwargs["frequency"])
        start = int(kwargs.get("start", 0))
        offset = int(kwargs.get("offset", 800))
        market = int(get_stock_market(symbol))
        cmd = GetSecurityBarsCmd(socket_client, lock=api_client.lock)
        cmd.setParams(frequency, market, symbol, start, offset)
        request, response_body, parsed = _execute_command(cmd)
        step = StepCapture(
            step_id="01_bars",
            parser="GetSecurityBarsCmd",
            kwargs={
                "symbol": symbol,
                "market": market,
                "frequency": frequency,
                "start": start,
                "offset": offset,
            },
            request=request,
            response_body=response_body,
            parsed=parsed,
        )
        return [step], to_data(parsed, symbol=symbol, client=client)

    if api in {"index_bars", "index"}:
        kwargs = spec["call"]["kwargs"]
        symbol = str(kwargs["symbol"])
        frequency = get_frequency(kwargs["frequency"])
        start = int(kwargs.get("start", 0))
        offset = min(int(kwargs.get("offset", 800)), 800)
        market = _get_index_market(symbol, kwargs.get("market"))
        cmd = GetIndexBarsCmd(socket_client, lock=api_client.lock)
        cmd.setParams(frequency, market, symbol, start, offset)
        request, response_body, parsed = _execute_command(cmd)
        step = StepCapture(
            step_id="01_index_bars",
            parser="GetIndexBarsCmd",
            kwargs={
                "symbol": symbol,
                "market": market,
                "frequency": frequency,
                "start": start,
                "offset": offset,
            },
            request=request,
            response_body=response_body,
            parsed=parsed,
        )
        return [step], to_data(parsed, symbol=symbol, client=client)

    if api == "minutes":
        kwargs = spec["call"]["kwargs"]
        symbol = str(kwargs["symbol"])
        date = kwargs["date"]
        market_tag = str(kwargs.get("market", "")).lower()
        if market_tag == "sz":
            market = 0
        elif market_tag == "sh":
            market = 1
        else:
            market = int(get_stock_market(symbol))
        cmd = GetHistoryMinuteTimeData(socket_client, lock=api_client.lock)
        cmd.setParams(market, symbol, date)
        request, response_body, parsed = _execute_command(cmd)
        step = StepCapture(
            step_id="01_minutes",
            parser="GetHistoryMinuteTimeData",
            kwargs={"symbol": symbol, "market": market, "date": int(date)},
            request=request,
            response_body=response_body,
            parsed=parsed,
        )
        return [step], to_data(parsed, symbol=symbol, client=client)

    if api == "transaction":
        kwargs = spec["call"]["kwargs"]
        symbol = str(kwargs["symbol"])
        start = int(kwargs.get("start", 0))
        offset = int(kwargs.get("offset", 800))
        market = int(get_stock_market(symbol))
        cmd = GetTransactionData(socket_client, lock=api_client.lock)
        cmd.setParams(market, symbol, start, offset)
        request, response_body, parsed = _execute_command(cmd)
        step = StepCapture(
            step_id="01_transaction",
            parser="GetTransactionData",
            kwargs={"symbol": symbol, "market": market, "start": start, "offset": offset},
            request=request,
            response_body=response_body,
            parsed=parsed,
        )
        return [step], to_data(parsed, symbol=symbol, client=client)

    if api == "transactions":
        kwargs = spec["call"]["kwargs"]
        symbol = str(kwargs["symbol"])
        start = int(kwargs.get("start", 0))
        offset = int(kwargs.get("offset", 800))
        date = int(kwargs["date"])
        market = int(get_stock_market(symbol))
        cmd = GetHistoryTransactionData(socket_client, lock=api_client.lock)
        cmd.setParams(market, symbol, start, offset, date)
        request, response_body, parsed = _execute_command(cmd)
        step = StepCapture(
            step_id="01_transactions",
            parser="GetHistoryTransactionData",
            kwargs={"symbol": symbol, "market": market, "start": start, "offset": offset, "date": date},
            request=request,
            response_body=response_body,
            parsed=parsed,
        )
        return [step], to_data(parsed, symbol=symbol, client=client)

    if api == "finance":
        symbol = str(spec["call"]["kwargs"]["symbol"])
        market = int(get_stock_market(symbol))
        cmd = GetFinanceInfo(socket_client, lock=api_client.lock)
        cmd.setParams(market, symbol)
        request, response_body, parsed = _execute_command(cmd)
        step = StepCapture(
            step_id="01_finance",
            parser="GetFinanceInfo",
            kwargs={"symbol": symbol, "market": market},
            request=request,
            response_body=response_body,
            parsed=parsed,
        )
        return [step], to_data(parsed, symbol=symbol, client=client)

    if api == "xdxr":
        symbol = str(spec["call"]["kwargs"]["symbol"])
        market = int(get_stock_market(symbol))
        cmd = GetXdXrInfo(socket_client, lock=api_client.lock)
        cmd.setParams(market, symbol)
        request, response_body, parsed = _execute_command(cmd)
        step = StepCapture(
            step_id="01_xdxr",
            parser="GetXdXrInfo",
            kwargs={"symbol": symbol, "market": market},
            request=request,
            response_body=response_body,
            parsed=parsed,
        )
        return [step], to_data(parsed, symbol=symbol, client=client)

    if api == "f10_categories":
        symbol = str(spec["call"]["kwargs"]["symbol"])
        market = int(get_stock_market(symbol))
        cmd = GetCompanyInfoCategory(socket_client, lock=api_client.lock)
        cmd.setParams(market, symbol)
        request, response_body, parsed = _execute_command(cmd)
        step = StepCapture(
            step_id="01_f10_categories",
            parser="GetCompanyInfoCategory",
            kwargs={"symbol": symbol, "market": market},
            request=request,
            response_body=response_body,
            parsed=parsed,
        )
        return [step], to_data(parsed, symbol=symbol, client=client)

    if api == "f10_content":
        symbol = str(spec["call"]["kwargs"]["symbol"])
        name = str(spec["call"]["kwargs"]["name"])
        market = int(get_stock_market(symbol))
        categories_cmd = GetCompanyInfoCategory(socket_client, lock=api_client.lock)
        categories_cmd.setParams(market, symbol)
        categories_request, categories_body, categories = _execute_command(categories_cmd)
        matched = next((item for item in categories if item["name"] == name), None)
        if matched is None:
            raise ValueError(f"unable to capture f10 content for {symbol}: unknown category {name}")
        content_cmd = GetCompanyInfoContent(socket_client, lock=api_client.lock)
        content_cmd.setParams(market, symbol, matched["filename"], matched["start"], matched["length"])
        content_request, content_body, content = _execute_command(content_cmd)
        return (
            [
                StepCapture(
                    step_id="01_f10_categories",
                    parser="GetCompanyInfoCategory",
                    kwargs={"symbol": symbol, "market": market},
                    request=categories_request,
                    response_body=categories_body,
                    parsed=categories,
                ),
                StepCapture(
                    step_id="02_f10_content",
                    parser="GetCompanyInfoContent",
                    kwargs={
                        "symbol": symbol,
                        "market": market,
                        "name": name,
                        "filename": matched["filename"],
                        "start": matched["start"],
                        "length": matched["length"],
                    },
                    request=content_request,
                    response_body=content_body,
                    parsed=content,
                ),
            ],
            content,
        )

    if api == "block":
        block_file = str(spec["call"]["kwargs"].get("tofile", "block.dat"))
        meta_cmd = GetBlockInfoMeta(socket_client, lock=api_client.lock)
        meta_cmd.setParams(block_file)
        meta_request, meta_body, meta = _execute_command(meta_cmd)
        steps = [
            StepCapture(
                step_id="01_block_info_meta",
                parser="GetBlockInfoMeta",
                kwargs={"block_file": block_file},
                request=meta_request,
                response_body=meta_body,
                parsed=meta,
            )
        ]

        size = int(meta["size"])
        chunk_size = 0x7530
        content = bytearray()
        for chunk_index, start in enumerate(range(0, size, chunk_size), start=2):
            piece_cmd = GetBlockInfo(socket_client, lock=api_client.lock)
            piece_cmd.setParams(block_file, start, size)
            request, response_body, parsed = _execute_command(piece_cmd)
            steps.append(
                StepCapture(
                    step_id=f"{chunk_index:02d}_block_piece_{start:05d}",
                    parser="GetBlockInfo",
                    kwargs={"block_file": block_file, "start": start, "size": size},
                    request=request,
                    response_body=response_body,
                    parsed=parsed,
                )
            )
            content.extend(parsed)

        return steps, pandas.DataFrame(data=_parse_block_content(bytes(content)))

    if api in {"get_k_data", "k", "ohlc"}:
        kwargs = spec["call"]["kwargs"]
        if api == "get_k_data":
            code = str(kwargs["code"])
            start_date = pandas.to_datetime(kwargs["start_date"])
            end_date = pandas.to_datetime(kwargs["end_date"])
        else:
            code = str(kwargs["symbol"])
            start_date = pandas.to_datetime(kwargs["begin"])
            end_date = pandas.to_datetime(kwargs["end"])

        if end_date <= start_date:
            return [], pandas.DataFrame()

        today = pandas.to_datetime(pandas.Timestamp.now().date())
        market = int(get_stock_market(code))
        workday_count = count_weekdays(start_date, end_date)
        if workday_count <= 0:
            return [], pandas.DataFrame()

        offset_end = max((end_date - today).days, 0)
        chunk_size = 800
        page_num = math.ceil(workday_count / chunk_size)
        steps: list[StepCapture] = []
        frames: list[pandas.DataFrame] = []
        for page_index in range(page_num):
            offset = offset_end + page_index * chunk_size
            count = min(chunk_size, workday_count - page_index * chunk_size)
            cmd = GetSecurityBarsCmd(socket_client, lock=api_client.lock)
            cmd.setParams(9, market, code, offset, count)
            request, response_body, parsed = _execute_command(cmd)
            steps.append(
                StepCapture(
                    step_id=f"{page_index + 1:02d}_bars_{offset:05d}",
                    parser="GetSecurityBarsCmd",
                    kwargs={"symbol": code, "market": market, "frequency": 9, "start": offset, "offset": count},
                    request=request,
                    response_body=response_body,
                    parsed=parsed,
                )
            )
            frame = to_data(parsed, symbol=code, client=client)
            if not frame.empty:
                frames.append(frame)

        return steps, _aggregate_k_data_frames(frames, code, start_date, end_date)

    raise ValueError(f"unsupported api for capture: {api}")


def _capture_live_result(client: Any, spec: dict[str, Any]) -> Any:
    api = spec["api"]
    kwargs = spec["call"]["kwargs"]
    return getattr(client, api)(**kwargs)


def _replay_case_next(spec: dict[str, Any], case_dir: Path, manifest: dict[str, Any]) -> Any:
    steps = [
        StepCapture(
            step_id=step["id"],
            parser=step["parser"],
            kwargs=step["kwargs"],
            request=(case_dir / step["request_path"]).read_bytes(),
            response_body=(case_dir / step["response_body_path"]).read_bytes(),
            parsed=None,
        )
        for step in manifest["steps"]
    ]
    return _result_from_captured_steps_next(spec, steps)


def _make_parser(name: str):
    if name == "GetSecurityCountCmd":
        return GetSecurityCountCmd(None)
    if name == "GetSecurityList":
        return GetSecurityList(None)
    if name == "GetSecurityQuotesCmd":
        return GetSecurityQuotesCmd(None)
    if name == "GetSecurityBarsCmd":
        return GetSecurityBarsCmd(None)
    if name == "GetIndexBarsCmd":
        return GetIndexBarsCmd(None)
    if name == "GetBlockInfoMeta":
        return GetBlockInfoMeta(None)
    if name == "GetBlockInfo":
        return GetBlockInfo(None)
    if name == "GetHistoryMinuteTimeData":
        return GetHistoryMinuteTimeData(None)
    if name == "GetTransactionData":
        return GetTransactionData(None)
    if name == "GetHistoryTransactionData":
        return GetHistoryTransactionData(None)
    if name == "GetFinanceInfo":
        return GetFinanceInfo(None)
    if name == "GetXdXrInfo":
        return GetXdXrInfo(None)
    if name == "GetCompanyInfoCategory":
        return GetCompanyInfoCategory(None)
    if name == "GetCompanyInfoContent":
        return GetCompanyInfoContent(None)
    raise ValueError(f"unsupported parser for replay: {name}")


def _prime_parser_for_replay(parser: Any, step: dict[str, Any]) -> None:
    name = step["parser"]
    kwargs = step["kwargs"]

    if name == "GetSecurityBarsCmd":
        parser.setParams(
            int(kwargs["frequency"]),
            int(kwargs["market"]),
            str(kwargs["symbol"]),
            int(kwargs["start"]),
            int(kwargs["offset"]),
        )
    elif name == "GetIndexBarsCmd":
        parser.setParams(
            int(kwargs["frequency"]),
            int(kwargs["market"]),
            str(kwargs["symbol"]),
            int(kwargs["start"]),
            int(kwargs["offset"]),
        )
    elif name == "GetBlockInfoMeta":
        parser.setParams(str(kwargs["block_file"]))
    elif name == "GetBlockInfo":
        parser.setParams(str(kwargs["block_file"]), int(kwargs["start"]), int(kwargs["size"]))
    elif name == "GetHistoryMinuteTimeData":
        parser.setParams(
            int(kwargs["market"]),
            str(kwargs["symbol"]),
            int(kwargs["date"]),
        )
    elif name == "GetTransactionData":
        parser.setParams(
            int(kwargs["market"]),
            str(kwargs["symbol"]),
            int(kwargs["start"]),
            int(kwargs["offset"]),
        )
    elif name == "GetHistoryTransactionData":
        parser.setParams(
            int(kwargs["market"]),
            str(kwargs["symbol"]),
            int(kwargs["start"]),
            int(kwargs["offset"]),
            int(kwargs["date"]),
        )
    elif name == "GetFinanceInfo":
        parser.setParams(int(kwargs["market"]), str(kwargs["symbol"]))
    elif name == "GetXdXrInfo":
        parser.setParams(int(kwargs["market"]), str(kwargs["symbol"]))
    elif name == "GetCompanyInfoCategory":
        parser.setParams(int(kwargs["market"]), str(kwargs["symbol"]))
    elif name == "GetCompanyInfoContent":
        parser.setParams(
            int(kwargs["market"]),
            str(kwargs["symbol"]),
            str(kwargs["filename"]),
            int(kwargs["start"]),
            int(kwargs["length"]),
        )


def _aggregate_case(spec: dict[str, Any], steps: list[dict[str, Any]]) -> Any:
    api = spec["api"]
    if api == "stock_count":
        return steps[0]["parsed"]

    if api == "stocks":
        counts = steps[0]["parsed"]
        stocks = None
        if counts > 0:
            for step in steps[1:]:
                start = int(step["kwargs"]["start"])
                page_df = to_data(step["parsed"])
                stocks = pandas.concat([stocks, page_df], ignore_index=True) if start > 1 else page_df
        return stocks

    if api == "quotes":
        symbols = spec["call"]["kwargs"]["symbol"]
        symbols = [symbols] if isinstance(symbols, str) else list(symbols)
        all_stock = get_stock_markets(symbols)
        return to_data(steps[0]["parsed"], symbol=all_stock)

    if api == "bars":
        symbol = spec["call"]["kwargs"]["symbol"]
        return to_data(steps[0]["parsed"], symbol=symbol)

    if api in {"index_bars", "index"}:
        symbol = spec["call"]["kwargs"]["symbol"]
        return to_data(steps[0]["parsed"], symbol=symbol)

    if api == "minutes":
        symbol = spec["call"]["kwargs"]["symbol"]
        return to_data(steps[0]["parsed"], symbol=symbol)

    if api == "transaction":
        symbol = spec["call"]["kwargs"]["symbol"]
        return to_data(steps[0]["parsed"], symbol=symbol)

    if api == "transactions":
        symbol = spec["call"]["kwargs"]["symbol"]
        return to_data(steps[0]["parsed"], symbol=symbol)

    if api == "finance":
        symbol = spec["call"]["kwargs"]["symbol"]
        return to_data(steps[0]["parsed"], symbol=symbol)

    if api == "xdxr":
        symbol = spec["call"]["kwargs"]["symbol"]
        return to_data(steps[0]["parsed"], symbol=symbol)

    if api == "f10_categories":
        symbol = spec["call"]["kwargs"]["symbol"]
        return to_data(steps[0]["parsed"], symbol=symbol)

    if api == "f10_content":
        return steps[1]["parsed"]

    if api == "block":
        content = bytearray()
        for step in steps[1:]:
            content.extend(step["parsed"])
        return pandas.DataFrame(data=_parse_block_content(bytes(content)))

    if api in {"get_k_data", "k", "ohlc"}:
        if api == "get_k_data":
            code = str(spec["call"]["kwargs"]["code"])
            start_date = pandas.to_datetime(spec["call"]["kwargs"]["start_date"])
            end_date = pandas.to_datetime(spec["call"]["kwargs"]["end_date"])
        else:
            code = str(spec["call"]["kwargs"]["symbol"])
            start_date = pandas.to_datetime(spec["call"]["kwargs"]["begin"])
            end_date = pandas.to_datetime(spec["call"]["kwargs"]["end"])

        frames: list[pandas.DataFrame] = []
        for step in steps:
            frame = to_data(step["parsed"], symbol=code)
            if not frame.empty:
                frames.append(frame)
        return _aggregate_k_data_frames(frames, code, start_date, end_date)

    raise ValueError(f"unsupported api for replay aggregation: {api}")


def _result_from_captured_steps_next(spec: dict[str, Any], steps: list[StepCapture]) -> Any:
    protocol = StdQuoteProtocol()
    api = spec["api"]

    if api == "stock_count":
        return protocol.decode_stock_count(steps[0].response_body)

    if api == "stocks":
        count = protocol.decode_stock_count(steps[0].response_body)
        if count <= 0:
            return stocks_to_frame([])

        rows: list[dict[str, object]] = []
        for step in steps[1:]:
            rows.extend(protocol.decode_stock_list_page(step.response_body))
        return stocks_to_frame(rows)

    if api == "quotes":
        rows = protocol.decode_quotes(steps[0].response_body)
        return quotes_to_frame(rows)

    if api == "bars":
        step = steps[0]
        rows = protocol.decode_bars(step.response_body, int(step.kwargs["frequency"]))
        return bars_to_frame(rows)

    if api in {"index_bars", "index"}:
        step = steps[0]
        rows = protocol.decode_index_bars(step.response_body, int(step.kwargs["frequency"]))
        return bars_to_frame(rows)

    if api == "minutes":
        step = steps[0]
        rows = protocol.decode_minutes(step.response_body, int(step.kwargs["market"]), str(step.kwargs["symbol"]))
        return minutes_to_frame(rows)

    if api == "transaction":
        rows = protocol.decode_transaction(steps[0].response_body)
        return transaction_to_frame(rows)

    if api == "transactions":
        rows = protocol.decode_history_transactions(steps[0].response_body)
        return transactions_to_frame(rows)

    if api == "finance":
        row = protocol.decode_finance(steps[0].response_body)
        return finance_to_frame(row)

    if api == "xdxr":
        rows = protocol.decode_xdxr(steps[0].response_body)
        return xdxr_to_frame(rows)

    if api == "f10_categories":
        rows = protocol.decode_f10_categories(steps[0].response_body)
        return f10_categories_to_frame(rows)

    if api == "f10_content":
        return protocol.decode_f10_content(steps[1].response_body)

    if api == "block":
        content = bytearray()
        for step in steps[1:]:
            content.extend(protocol.decode_block_info(step.response_body))
        return block_to_frame(_parse_block_content(bytes(content)))

    if api in {"get_k_data", "k", "ohlc"}:
        if api == "get_k_data":
            code = str(spec["call"]["kwargs"]["code"])
            start_date = pandas.to_datetime(spec["call"]["kwargs"]["start_date"])
            end_date = pandas.to_datetime(spec["call"]["kwargs"]["end_date"])
        else:
            code = str(spec["call"]["kwargs"]["symbol"])
            start_date = pandas.to_datetime(spec["call"]["kwargs"]["begin"])
            end_date = pandas.to_datetime(spec["call"]["kwargs"]["end"])

        frames = []
        for step in steps:
            rows = protocol.decode_bars(step.response_body, 9)
            if rows:
                frames.append(bars_to_frame(rows))
        return _aggregate_k_data_frames(frames, code, start_date, end_date)

    raise ValueError(f"unsupported api for next replay: {api}")


def _normalize_next_result_for_artifact(spec: dict[str, Any], result: Any, runtime: str) -> Any:
    if runtime != "next":
        return result

    if spec["api"] == "stocks":
        return stocks_to_frame(result)
    if spec["api"] == "quotes":
        return quotes_to_frame(result)
    if spec["api"] in {"bars", "index_bars"}:
        return bars_to_frame(result)
    if spec["api"] in {"minutes", "minute"}:
        return minutes_to_frame(result)
    if spec["api"] == "transaction":
        return transaction_to_frame(result)
    if spec["api"] == "transactions":
        return transactions_to_frame(result)
    if spec["api"] == "finance":
        return finance_to_frame(result)
    if spec["api"] == "xdxr":
        return xdxr_to_frame(result)
    if spec["api"] == "f10_categories":
        return f10_categories_to_frame(result)
    if spec["api"] == "block":
        return block_to_frame(result)
    return result


def _get_index_market(symbol: str, market: Any = None) -> int:
    if market is not None:
        return int(market)
    return 1 if symbol[:2] in ["00", "88", "99"] else 0


def _parse_block_content(data: bytes) -> list[dict[str, object]]:
    if len(data) < 386:
        return []

    pos = 384
    block_count = struct.unpack("<H", data[pos : pos + 2])[0]
    pos += 2
    rows: list[dict[str, object]] = []
    for _ in range(block_count):
        raw_name = data[pos : pos + 9]
        block_name = raw_name.split(b"\x00", 1)[0].decode("gbk", errors="ignore").strip()
        pos += 9
        stock_count, block_type = struct.unpack("<HH", data[pos : pos + 4])
        pos += 4
        stock_bytes = data[pos : pos + 2800]
        pos += 2800
        for index in range(stock_count):
            code = stock_bytes[index * 7 : index * 7 + 7].split(b"\x00", 1)[0].decode("ascii", errors="ignore")
            if not code:
                continue
            rows.append(
                {
                    "blockname": block_name,
                    "block_type": block_type,
                    "code_index": index,
                    "code": code,
                }
            )
    return rows


def _aggregate_k_data_frames(
    frames: list[pandas.DataFrame],
    code: str,
    start_date: pandas.Timestamp,
    end_date: pandas.Timestamp,
) -> pandas.DataFrame:
    if not frames:
        return pandas.DataFrame()

    data = pandas.concat(frames, ignore_index=True)
    data["date"] = pandas.to_datetime(data["datetime"].astype(str).str[:10])
    data["code"] = str(code)
    data.drop(columns=["year", "month", "day", "hour", "minute", "datetime"], inplace=True)
    data.set_index("date", inplace=True)
    return data.loc[(data.index >= start_date) & (data.index <= end_date)].sort_index()
