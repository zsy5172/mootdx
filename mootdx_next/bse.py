from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from collections.abc import Iterable
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

import httpx

from mootdx_next.errors import BseResponseError
from mootdx_next.symbols import BSE_STOCK_PREFIX

BSE_CODES_URL = "https://www.bse.cn/nqhqController/nqhq_en.do"
BSE_CACHE_TTL_SECONDS = 10 * 60
BSE_REQUEST_TIMEOUT_SECONDS = 10.0
BSE_MAX_PAGES = 200
BSE_MAX_RESPONSE_BYTES = 8 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class BseSecurity:
    code: str
    name: str
    date: str
    pre_close: float
    open: float
    high: float
    low: float
    price: float
    volume: int
    amount: float

    def to_stock_dict(self) -> dict[str, object]:
        """Return the standard ``stocks()`` row shape plus verified BSE fields."""

        return {
            "market": 2,
            "code": self.code,
            "name": self.name,
            "volunit": 100,
            "decimal_point": 2,
            "pre_close": self.pre_close,
            "date": self.date,
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "price": self.price,
            "volume": self.volume,
            "amount": self.amount,
            "source": "bse",
            "source_kind": "bse_market_snapshot",
        }


BseSnapshot = tuple[BseSecurity, ...]


class BseProvider(Protocol):
    def load(self) -> Iterable[BseSecurity]: ...


HttpClientFactory = Callable[[], httpx.Client]


class BseHttpProvider:
    """Load the current BSE security directory from the exchange website.

    A new HTTP client is created for each complete snapshot load.  The client
    is never shared across worker threads; sharing and refresh coordination
    belong to :class:`BseRegistry`.
    """

    def __init__(
        self,
        *,
        url: str = BSE_CODES_URL,
        timeout: float = BSE_REQUEST_TIMEOUT_SECONDS,
        max_pages: int = BSE_MAX_PAGES,
        client_factory: HttpClientFactory | None = None,
        time_fn: Callable[[], float] = time.time,
    ) -> None:
        if timeout <= 0:
            raise ValueError("timeout must be greater than zero")
        if max_pages <= 0:
            raise ValueError("max_pages must be greater than zero")
        self._url = str(url)
        self._timeout = float(timeout)
        self._max_pages = int(max_pages)
        self._time_fn = time_fn
        self._client_factory = client_factory or self._new_client

    def load(self) -> BseSnapshot:
        rows: list[BseSecurity] = []
        expected_total: int | None = None
        expected_pages: int | None = None
        seen: set[str] = set()

        with self._client_factory() as client:
            for page in range(self._max_pages):
                callback = f"jQuery_mootdx_{int(self._time_fn() * 1000)}_{page}"
                try:
                    response = client.post(
                        self._url,
                        params={"callback": callback},
                        data={
                            "page": str(page),
                            "type_en": '["B"]',
                            "sortfield": "hqzqdm",
                            "sorttype": "asc",
                            "xxfcbj_en": "[2]",
                            "zqdm": "",
                        },
                        headers={
                            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                            "User-Agent": (
                                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0 Safari/537.36"
                            ),
                            "X-Requested-With": "XMLHttpRequest",
                        },
                        timeout=self._timeout,
                    )
                    response.raise_for_status()
                except httpx.HTTPError as exc:
                    raise BseResponseError(f"BSE directory request failed on page {page}: {exc}") from exc
                if len(response.content) > BSE_MAX_RESPONSE_BYTES:
                    raise BseResponseError(
                        f"BSE directory page {page} exceeds {BSE_MAX_RESPONSE_BYTES} bytes"
                    )

                page_data = self._decode_page(response.text, callback, page)
                total = self._required_int(page_data, "totalElements", page, minimum=0)
                total_pages = self._required_int(page_data, "totalPages", page, minimum=0)
                last_page = page_data.get("lastPage")
                content = page_data.get("content")
                if not isinstance(last_page, bool):
                    raise BseResponseError(f"BSE directory page {page} has invalid lastPage")
                if not isinstance(content, list):
                    raise BseResponseError(f"BSE directory page {page} has invalid content")

                if expected_total is None:
                    expected_total = total
                    expected_pages = total_pages
                elif total != expected_total or total_pages != expected_pages:
                    raise BseResponseError("BSE directory pagination metadata changed during refresh")

                for index, raw in enumerate(content):
                    security = self._decode_security(raw, page, index)
                    if security.code in seen:
                        raise BseResponseError(f"BSE directory contains duplicate code {security.code}")
                    seen.add(security.code)
                    rows.append(security)

                if last_page:
                    break
                if not content:
                    raise BseResponseError(f"BSE directory page {page} is empty before lastPage")
            else:
                raise BseResponseError(f"BSE directory exceeds max_pages={self._max_pages}")

        if expected_total is None:
            raise BseResponseError("BSE directory returned no pages")
        if len(rows) != expected_total:
            raise BseResponseError(
                f"BSE directory expected {expected_total} securities, decoded {len(rows)}"
            )
        return tuple(rows)

    def _new_client(self) -> httpx.Client:
        return httpx.Client(follow_redirects=False)

    @staticmethod
    def _decode_page(text: str, callback: str, page: int) -> Mapping[str, object]:
        stripped = text.strip()
        prefix = f"{callback}("
        if not stripped.startswith(prefix) or not stripped.endswith(")"):
            raise BseResponseError(f"BSE directory page {page} is not valid JSONP")
        payload = stripped[len(prefix) : -1]
        try:
            decoded = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise BseResponseError(f"BSE directory page {page} contains invalid JSON") from exc
        if not isinstance(decoded, list) or len(decoded) != 1 or not isinstance(decoded[0], Mapping):
            raise BseResponseError(f"BSE directory page {page} has an invalid root schema")
        return decoded[0]

    @classmethod
    def _decode_security(cls, raw: object, page: int, index: int) -> BseSecurity:
        if not isinstance(raw, Mapping):
            raise BseResponseError(f"BSE directory page {page} row {index} is not an object")
        code = raw.get("hqzqdm")
        name = raw.get("hqzqjc")
        date_value = raw.get("hqjsrq")
        if not isinstance(code, str) or len(code) != 6 or not code.isdigit():
            raise BseResponseError(f"BSE directory page {page} row {index} has invalid code")
        if not code.startswith(BSE_STOCK_PREFIX):
            raise BseResponseError(
                f"BSE directory page {page} row {index} has non-920 listing code"
            )
        if not isinstance(name, str) or not name.strip():
            raise BseResponseError(f"BSE directory page {page} row {index} has invalid name")
        if not isinstance(date_value, str) or len(date_value) != 8 or not date_value.isdigit():
            raise BseResponseError(f"BSE directory page {page} row {index} has invalid date")
        return BseSecurity(
            code=code,
            name=name.strip(),
            date=date_value,
            pre_close=cls._required_float(raw, "hqzrsp", page, index),
            open=cls._required_float(raw, "hqjrkp", page, index),
            high=cls._required_float(raw, "hqzgcj", page, index),
            low=cls._required_float(raw, "hqzdcj", page, index),
            price=cls._required_float(raw, "hqzjcj", page, index),
            volume=cls._required_row_int(raw, "hqcjsl", page, index),
            amount=cls._required_float(raw, "hqcjje", page, index),
        )

    @staticmethod
    def _required_int(
        data: Mapping[str, object],
        key: str,
        page: int,
        *,
        minimum: int,
    ) -> int:
        value = data.get(key)
        if isinstance(value, bool):
            raise BseResponseError(f"BSE directory page {page} has invalid {key}")
        try:
            result = int(value)  # type: ignore[arg-type]
        except (TypeError, ValueError) as exc:
            raise BseResponseError(f"BSE directory page {page} has invalid {key}") from exc
        if result < minimum:
            raise BseResponseError(f"BSE directory page {page} has invalid {key}")
        return result

    @staticmethod
    def _required_row_int(data: Mapping[str, object], key: str, page: int, index: int) -> int:
        value = data.get(key)
        if isinstance(value, bool):
            raise BseResponseError(f"BSE directory page {page} row {index} has invalid {key}")
        try:
            result = int(value)  # type: ignore[arg-type]
        except (TypeError, ValueError) as exc:
            raise BseResponseError(
                f"BSE directory page {page} row {index} has invalid {key}"
            ) from exc
        if result < 0:
            raise BseResponseError(f"BSE directory page {page} row {index} has invalid {key}")
        return result

    @staticmethod
    def _required_float(data: Mapping[str, object], key: str, page: int, index: int) -> float:
        value = data.get(key)
        if isinstance(value, bool):
            raise BseResponseError(f"BSE directory page {page} row {index} has invalid {key}")
        try:
            result = float(value)  # type: ignore[arg-type]
        except (TypeError, ValueError) as exc:
            raise BseResponseError(
                f"BSE directory page {page} row {index} has invalid {key}"
            ) from exc
        if result != result or result in {float("inf"), float("-inf")}:
            raise BseResponseError(f"BSE directory page {page} row {index} has invalid {key}")
        return result


class BseRegistry:
    """Thread-safe, process-level cache of an immutable BSE directory snapshot."""

    def __init__(
        self,
        provider: BseProvider,
        *,
        ttl_seconds: float = BSE_CACHE_TTL_SECONDS,
        time_fn: Callable[[], float] = time.monotonic,
    ) -> None:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be greater than zero")
        self._provider = provider
        self._ttl_seconds = float(ttl_seconds)
        self._time_fn = time_fn
        self._condition = threading.Condition()
        self._snapshot: BseSnapshot = ()
        self._expires_at = 0.0
        self._loaded = False
        self._refreshing = False

    @property
    def ttl_seconds(self) -> float:
        return self._ttl_seconds

    def get(self, *, refresh: bool = False) -> BseSnapshot:
        with self._condition:
            if self._refreshing:
                self._condition.wait_for(lambda: not self._refreshing)
                if self._loaded:
                    return self._snapshot
            if not refresh and self._loaded and self._time_fn() < self._expires_at:
                return self._snapshot
            self._refreshing = True

        try:
            snapshot = self._normalize(self._provider.load())
        except BaseException:
            with self._condition:
                self._refreshing = False
                self._condition.notify_all()
            raise

        with self._condition:
            self._snapshot = snapshot
            self._expires_at = self._time_fn() + self._ttl_seconds
            self._loaded = True
            self._refreshing = False
            self._condition.notify_all()
            return snapshot

    def refresh(self) -> BseSnapshot:
        return self.get(refresh=True)

    def invalidate(self) -> None:
        with self._condition:
            self._expires_at = 0.0

    def snapshot(self) -> BseSnapshot:
        with self._condition:
            return self._snapshot

    @staticmethod
    def _normalize(values: Iterable[BseSecurity]) -> BseSnapshot:
        snapshot = tuple(values)
        seen: set[str] = set()
        for item in snapshot:
            if not isinstance(item, BseSecurity):
                raise TypeError("BSE provider must return BseSecurity instances")
            if item.code in seen:
                raise BseResponseError(f"BSE provider returned duplicate code {item.code}")
            seen.add(item.code)
        return tuple(sorted(snapshot, key=lambda item: item.code))


bse_registry = BseRegistry(BseHttpProvider())


def get_bse_securities() -> BseSnapshot:
    return bse_registry.get()


def refresh_bse_securities() -> BseSnapshot:
    return bse_registry.refresh()


def invalidate_bse_securities() -> None:
    bse_registry.invalidate()


def bse_snapshot() -> BseSnapshot:
    return bse_registry.snapshot()
