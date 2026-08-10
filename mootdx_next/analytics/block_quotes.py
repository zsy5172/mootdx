from __future__ import annotations

import math
from collections.abc import Iterable
from collections.abc import Mapping
from statistics import median

import pandas as pd

Records = pd.DataFrame | Iterable[Mapping[str, object]]

BLOCK_QUOTE_COLUMNS = [
    "block_code",
    "block_name",
    "block_category",
    "block_category_name",
    "block_taxonomy",
    "member_count",
    "quoted_count",
    "coverage_ratio",
    "amount_count",
    "amount",
    "volume_count",
    "volume",
    "weighted_change_pct",
    "median_change_pct",
    "up_count",
    "flat_count",
    "down_count",
    "up_ratio",
    "limit_price_count",
    "limit_up_count",
    "limit_down_count",
]


def aggregate_block_quotes(
    quotes: Records,
    members: Records,
    blocks: Records | None = None,
) -> pd.DataFrame:
    """Aggregate caller-provided security quotes by caller-provided blocks.

    The function performs no network requests, security-universe filtering,
    ranking or trading-policy checks.  Passing a 09:25 quote snapshot produces
    call-auction block statistics; passing a continuous-session snapshot
    produces statistics for that later observation.
    """

    quote_by_security: dict[tuple[int, str], Mapping[str, object]] = {}
    for quote in _records(quotes):
        key = _security_key(quote, "quote")
        if key in quote_by_security:
            raise ValueError(f"duplicate quote for market={key[0]} code={key[1]}")
        quote_by_security[key] = quote

    groups: dict[tuple[str, str, str, str], dict[str, object]] = {}
    if blocks is not None:
        for block in _records(blocks):
            key, metadata = _block_metadata(block, member=False)
            groups.setdefault(key, {**metadata, "members": []})

    for member in _records(members):
        key, metadata = _block_metadata(member, member=True)
        group = groups.setdefault(key, {**metadata, "members": []})
        group_members = group["members"]
        if not isinstance(group_members, list):
            raise RuntimeError("invalid block aggregation state")
        security = _security_key(member, "block member")
        if security not in group_members:
            group_members.append(security)

    output: list[dict[str, object]] = []
    for group in groups.values():
        securities = group.pop("members")
        if not isinstance(securities, list):
            raise RuntimeError("invalid block aggregation state")

        changes: list[float] = []
        weighted_changes: list[tuple[float, float]] = []
        amounts: list[float] = []
        volumes: list[float] = []
        up_count = 0
        flat_count = 0
        down_count = 0
        limit_price_count = 0
        limit_up_count = 0
        limit_down_count = 0

        for security in securities:
            quote = quote_by_security.get(security)
            if quote is None:
                continue
            price = _optional_number(quote.get("price"))
            previous_close = _optional_number(
                quote.get("last_close")
                if quote.get("last_close") is not None
                else quote.get("pre_close")
            )
            if (
                price is None
                or price <= 0
                or previous_close is None
                or previous_close <= 0
            ):
                continue

            change_pct = (price / previous_close - 1.0) * 100.0
            changes.append(change_pct)
            if change_pct > 1e-12:
                up_count += 1
            elif change_pct < -1e-12:
                down_count += 1
            else:
                flat_count += 1

            amount = _optional_non_negative(quote.get("amount"), "quote amount")
            if amount is not None:
                amounts.append(amount)
                if amount > 0:
                    weighted_changes.append((change_pct, amount))

            volume_value = _first(quote, "volume", "vol", "volume_lots")
            volume = _optional_non_negative(volume_value, "quote volume")
            if volume is not None:
                volumes.append(volume)

            limit_up = _optional_number(quote.get("limit_up"))
            limit_down = _optional_number(quote.get("limit_down"))
            if limit_up is not None and limit_up > 0:
                limit_price_count += 1
                if _price_equal(price, limit_up):
                    limit_up_count += 1
            if limit_down is not None and limit_down > 0:
                if limit_up is None or limit_up <= 0:
                    limit_price_count += 1
                if _price_equal(price, limit_down):
                    limit_down_count += 1

        member_count = len(securities)
        quoted_count = len(changes)
        total_weight = sum(weight for _, weight in weighted_changes)
        output.append(
            {
                **group,
                "member_count": member_count,
                "quoted_count": quoted_count,
                "coverage_ratio": quoted_count / member_count if member_count else None,
                "amount_count": len(amounts),
                "amount": sum(amounts),
                "volume_count": len(volumes),
                "volume": sum(volumes),
                "weighted_change_pct": (
                    sum(change * weight for change, weight in weighted_changes)
                    / total_weight
                    if total_weight
                    else None
                ),
                "median_change_pct": median(changes) if changes else None,
                "up_count": up_count,
                "flat_count": flat_count,
                "down_count": down_count,
                "up_ratio": up_count / quoted_count if quoted_count else None,
                "limit_price_count": limit_price_count,
                "limit_up_count": limit_up_count,
                "limit_down_count": limit_down_count,
            }
        )

    return pd.DataFrame.from_records(output, columns=BLOCK_QUOTE_COLUMNS)


def _records(values: Records) -> list[Mapping[str, object]]:
    if isinstance(values, pd.DataFrame):
        return list(values.to_dict(orient="records"))
    rows = list(values)
    if any(not isinstance(row, Mapping) for row in rows):
        raise TypeError("records must contain mappings")
    return rows


def _security_key(row: Mapping[str, object], label: str) -> tuple[int, str]:
    market_value = row.get("market")
    code_value = row.get("code")
    if isinstance(market_value, bool):
        raise ValueError(f"{label} has an invalid market")
    try:
        market = int(market_value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} has an invalid market") from exc
    code = str(code_value or "").strip()
    if not code.isdigit() or len(code) > 6:
        raise ValueError(f"{label} has an invalid code")
    return market, code.zfill(6)


def _block_metadata(
    row: Mapping[str, object],
    *,
    member: bool,
) -> tuple[tuple[str, str, str, str], dict[str, object]]:
    prefix = "block_" if member else ""
    code = str(row.get(f"{prefix}code") or "").strip()
    name = str(row.get(f"{prefix}name") or "").strip()
    category = str(row.get(f"{prefix}category") or "").strip()
    category_name = str(row.get(f"{prefix}category_name") or "").strip()
    taxonomy = str(row.get(f"{prefix}taxonomy") or "").strip()
    if not name:
        raise ValueError("block metadata requires a name")
    if not category:
        raise ValueError(f"block {name!r} requires a category")
    key = (category, taxonomy, code, name)
    return key, {
        "block_code": code,
        "block_name": name,
        "block_category": category,
        "block_category_name": category_name,
        "block_taxonomy": taxonomy or None,
    }


def _first(row: Mapping[str, object], *keys: str) -> object:
    for key in keys:
        value = row.get(key)
        if value is not None:
            return value
    return None


def _optional_number(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _optional_non_negative(value: object, label: str) -> float | None:
    result = _optional_number(value)
    if result is None:
        return None
    if result < 0:
        raise ValueError(f"{label} cannot be negative")
    return result


def _price_equal(left: float, right: float) -> bool:
    return math.isclose(left, right, rel_tol=1e-9, abs_tol=1e-8)


__all__ = ["aggregate_block_quotes"]
