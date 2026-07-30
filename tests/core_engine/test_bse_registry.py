from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import parse_qs

import httpx
import pytest

from mootdx_next.bse import BseHttpProvider
from mootdx_next.bse import BseRegistry
from mootdx_next.bse import BseSecurity
from mootdx_next.errors import BseResponseError


class Clock:
    def __init__(self) -> None:
        self.value = 100.0

    def __call__(self) -> float:
        return self.value


def _row(code: str, name: str) -> dict[str, object]:
    return {
        "hqjsrq": "20260730",
        "hqzqdm": code,
        "hqzqjc": name,
        "hqzrsp": 10.0,
        "hqjrkp": 10.1,
        "hqzgcj": 10.5,
        "hqzdcj": 9.8,
        "hqzjcj": 10.2,
        "hqcjsl": 123400,
        "hqcjje": 1250000.5,
    }


def _jsonp(request: httpx.Request, payload: object) -> httpx.Response:
    callback = request.url.params["callback"]
    return httpx.Response(200, text=f"{callback}({json.dumps(payload, ensure_ascii=False)})")


def test_http_provider_loads_and_validates_all_pages() -> None:
    requested_pages: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        form = parse_qs(request.content.decode("ascii"))
        page = int(form["page"][0])
        requested_pages.append(page)
        content = [_row("920001", "第一页")] if page == 0 else [_row("920002", "第二页")]
        return _jsonp(
            request,
            [
                {
                    "content": content,
                    "totalElements": 2,
                    "totalPages": 2,
                    "lastPage": page == 1,
                }
            ],
        )

    transport = httpx.MockTransport(handler)
    provider = BseHttpProvider(
        client_factory=lambda: httpx.Client(transport=transport),
        time_fn=lambda: 123.0,
    )

    rows = provider.load()

    assert requested_pages == [0, 1]
    assert isinstance(rows, tuple)
    assert [row.code for row in rows] == ["920001", "920002"]
    assert rows[0].volume == 123400
    assert rows[0].amount == 1250000.5


@pytest.mark.parametrize(
    "payload",
    [
        "not jsonp",
        "callback([])",
        "callback([{}])",
    ],
)
def test_http_provider_rejects_malformed_schema(payload: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        callback = request.url.params["callback"]
        return httpx.Response(200, text=payload.replace("callback", callback))

    provider = BseHttpProvider(
        client_factory=lambda: httpx.Client(transport=httpx.MockTransport(handler)),
        max_pages=1,
    )

    with pytest.raises(BseResponseError):
        provider.load()


def test_registry_reuses_immutable_snapshot_refreshes_and_invalidates() -> None:
    clock = Clock()
    calls = 0

    class Provider:
        def load(self):
            nonlocal calls
            calls += 1
            return [_security(f"{920000 + calls:06d}")]

    registry = BseRegistry(Provider(), ttl_seconds=10, time_fn=clock)
    first = registry.get()
    assert isinstance(first, tuple)
    assert registry.get() is first
    assert calls == 1

    clock.value += 10
    second = registry.get()
    assert second is not first
    assert calls == 2

    registry.invalidate()
    assert registry.get()[0].code == "920003"
    assert registry.refresh()[0].code == "920004"


def test_registry_performs_only_one_concurrent_load() -> None:
    started = threading.Event()
    release = threading.Event()
    calls = 0

    class Provider:
        def load(self):
            nonlocal calls
            calls += 1
            started.set()
            assert release.wait(timeout=5)
            return [_security("920001")]

    registry = BseRegistry(Provider())
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit(registry.get) for _ in range(4)]
        assert started.wait(timeout=5)
        release.set()
        snapshots = [future.result(timeout=5) for future in futures]

    assert calls == 1
    assert all(snapshot is snapshots[0] for snapshot in snapshots)


def _security(code: str) -> BseSecurity:
    return BseSecurity(
        code=code,
        name="测试",
        date="20260730",
        pre_close=10.0,
        open=10.0,
        high=10.0,
        low=10.0,
        price=10.0,
        volume=0,
        amount=0.0,
    )
