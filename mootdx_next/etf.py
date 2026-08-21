from __future__ import annotations

import json
from collections.abc import Callable
from collections.abc import Mapping
from datetime import date
from datetime import datetime
from typing import Protocol

import httpx

from mootdx_next.errors import EtfPcfDecodeError
from mootdx_next.errors import EtfPcfDownloadError
from mootdx_next.params import normalize_date

ETF_PCF_URL = "http://www.tdx.com.cn/fastapi/api/quantload/downdata"
ETF_PCF_TIMEOUT_SECONDS = 30.0
ETF_PCF_MAX_RESPONSE_BYTES = 4 * 1024 * 1024


class EtfPcfProvider(Protocol):
    def load(
        self,
        code: str,
        trading_date: str | int | date | datetime,
    ) -> list[dict[str, object]]: ...


HttpClientFactory = Callable[[], httpx.Client]


def normalize_etf_code(code: str) -> str:
    """Normalize ``510300.SH``/``159919.SZ`` to the six-digit TDX code."""

    if not isinstance(code, str) or not code.strip():
        raise ValueError("ETF code cannot be blank")
    value = code.strip().upper()
    if "." in value:
        value, suffix = value.rsplit(".", 1)
        if suffix not in {"SH", "SZ"}:
            raise ValueError("ETF code suffix must be SH or SZ")
    if len(value) != 6 or not value.isdigit():
        raise ValueError("ETF code must contain six digits")
    return value


def normalize_etf_date(value: str | int | date | datetime) -> str:
    if isinstance(value, datetime):
        value = value.date()
    if isinstance(value, date):
        return value.strftime("%Y%m%d")
    return normalize_date(value)


def decode_etf_pcf(payload: bytes | str) -> list[dict[str, object]]:
    """Decode the JSON document returned by TDX's ETF download endpoint.

    The endpoint returns a one-row ETF summary (cash component, creation unit,
    NAV date, etc.), not the constituent basket.  An empty list is a valid
    response for dates for which TDX has no file.
    """

    if isinstance(payload, bytes):
        try:
            text = payload.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise EtfPcfDecodeError("ETF PCF response is not UTF-8 JSON") from exc
    elif isinstance(payload, str):
        text = payload
    else:  # pragma: no cover - guarded by the public type contract
        raise TypeError("payload must be bytes or str")

    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise EtfPcfDecodeError("ETF PCF response is not valid JSON") from exc

    if isinstance(value, Mapping):
        error_code = value.get("ErrorCode")
        error_info = value.get("ErrorInfo")
        if error_code is not None or error_info is not None:
            if error_code == -1006 and str(error_info) == "空数据":
                return []
            raise EtfPcfDecodeError(f"TDX ETF PCF error: {error_code!r} {error_info!r}")
        raise EtfPcfDecodeError("ETF PCF response object is missing ErrorCode/ErrorInfo")

    if not isinstance(value, list):
        raise EtfPcfDecodeError("ETF PCF response root must be a JSON array")

    rows: list[dict[str, object]] = []
    for index, row in enumerate(value):
        if not isinstance(row, Mapping):
            raise EtfPcfDecodeError(f"ETF PCF row {index} is not a JSON object")
        rows.append(dict(row))
    return rows


class EtfPcfHttpProvider:
    """Fetch ETF PCF summaries directly from the endpoint used by TdxW.exe."""

    def __init__(
        self,
        *,
        url: str = ETF_PCF_URL,
        timeout: float = ETF_PCF_TIMEOUT_SECONDS,
        max_bytes: int = ETF_PCF_MAX_RESPONSE_BYTES,
        client_factory: HttpClientFactory | None = None,
    ) -> None:
        if timeout <= 0:
            raise ValueError("timeout must be greater than zero")
        if max_bytes <= 0:
            raise ValueError("max_bytes must be greater than zero")
        self._url = str(url)
        self._timeout = float(timeout)
        self._max_bytes = int(max_bytes)
        self._client_factory = client_factory or (lambda: httpx.Client(follow_redirects=False))

    def load(
        self,
        code: str,
        trading_date: str | int | date | datetime,
    ) -> list[dict[str, object]]:
        normalized_code = normalize_etf_code(code)
        normalized_date = normalize_etf_date(trading_date)
        try:
            with self._client_factory() as client:
                response = client.get(
                    self._url,
                    params={"type": "etf", "code": normalized_code, "rq": normalized_date},
                    headers={"User-Agent": "mootdx"},
                    timeout=self._timeout,
                )
                response.raise_for_status()
        except httpx.HTTPError as exc:
            raise EtfPcfDownloadError(f"ETF PCF request failed: {exc}") from exc
        if len(response.content) > self._max_bytes:
            raise EtfPcfDownloadError(f"ETF PCF response exceeds {self._max_bytes} bytes")

        rows = decode_etf_pcf(response.content)
        for row in rows:
            row.setdefault("code", normalized_code)
            row.setdefault("date", normalized_date)
            row.setdefault("source", "tdx_quantload_etf")
        return rows


default_etf_pcf_provider = EtfPcfHttpProvider()


def download_etf_pcf(
    code: str,
    trading_date: str | int | date | datetime,
    *,
    provider: EtfPcfProvider | None = None,
) -> list[dict[str, object]]:
    """Download an ETF PCF summary without starting TdxW.exe or using 17709."""

    return (provider or default_etf_pcf_provider).load(code, trading_date)
