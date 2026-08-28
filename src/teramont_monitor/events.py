from __future__ import annotations

import hashlib
import json
from typing import Any

from .identity import listing_key
from .models import Event, Listing, ListingState, MonitorState, SourceResult
from .qualify import qualify


_COMMERCIAL_CONFIRMATIONS = (
    "vin",
    "epts_status",
    "commercial_recycling_fee_status",
)


def _event_id(kind: str, key: str, detail: dict[str, Any]) -> str:
    payload = json.dumps([kind, key, detail], ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:20]


def _make_event(
    kind: str,
    key: str,
    observed_at: str,
    listing: Listing,
    detail: dict[str, Any] | None = None,
    previous: dict[str, Any] | None = None,
) -> Event:
    detail = detail or {}
    return Event(
        id=_event_id(kind, key, detail),
        kind=kind,
        listing_key=key,
        occurred_at=observed_at,
        listing=listing,
        previous=previous or {},
        detail=detail,
    )


def _new_commercial_confirmations(previous: Listing, current: Listing) -> list[str]:
    confirmed: list[str] = []
    for field in _COMMERCIAL_CONFIRMATIONS:
        if getattr(previous, field) is None and getattr(current, field) is not None:
            confirmed.append(field)
    return confirmed


def apply_scan(
    state: MonitorState,
    source_results: dict[str, SourceResult],
    observed_at: str,
    *,
    known_event_ids: set[str] | None = None,
) -> tuple[MonitorState, list[Event], list[dict[str, Any]]]:
    next_state = MonitorState.from_dict(state.to_dict())
    known = set(state.emitted_event_ids)
    known.update(known_event_ids or ())
    events: list[Event] = []
    history: list[dict[str, Any]] = []
    successful_sources = {name for name, result in source_results.items() if result.ok}
    seen_by_source: dict[str, set[str]] = {name: set() for name in successful_sources}

    def emit(event: Event) -> None:
        if event.id not in known:
            events.append(event)
            known.add(event.id)

    for source, result in source_results.items():
        if not result.ok:
            gap = result.gap
            history.append(
                {
                    "type": "source_gap",
                    "observed_at": observed_at,
                    "source": source,
                    "code": gap.code if gap else "unknown",
                    "message": gap.message if gap else "source failed without details",
                    "search_url": result.search_url,
                }
            )
            continue

        deduplicated = {listing_key(listing): listing for listing in result.listings}
        for key, current in deduplicated.items():
            seen_by_source[source].add(key)
            status, missing = qualify(current)
            previous_state = next_state.listings.get(key)
            history.append(
                {
                    "type": "observation",
                    "observed_at": observed_at,
                    "source": source,
                    "listing_key": key,
                    "status": status,
                    "missing": list(missing),
                    "cash_price": current.cash_price,
                    "advertised_price": current.advertised_price,
                    "price_currency": current.price_currency,
                    "in_stock": current.in_stock.value,
                    "sold": current.sold.value,
                    "url": current.url,
                }
            )

            if previous_state is None:
                next_state.listings[key] = ListingState(current, status, missing, 0, observed_at, False)
                if status == "relevant" and current.sold.value is not True:
                    emit(_make_event("new_relevant", key, observed_at, current, {"status": "relevant"}))
                continue

            previous = previous_state.listing
            was_removed = previous_state.removed
            next_state.listings[key] = ListingState(current, status, missing, 0, observed_at, False)

            if current.sold.value is True and not was_removed:
                next_state.listings[key].removed = True
                emit(_make_event("removed_or_sold", key, observed_at, current, {"reason": "explicit_sold"}))
                continue

            if was_removed and status == "relevant":
                emit(_make_event("became_available", key, observed_at, current, {"reason": "reappeared"}))
                continue

            if previous_state.status == "candidate" and status == "relevant":
                confirmed = sorted(set(previous_state.missing) - set(missing))
                emit(
                    _make_event(
                        "critical_confirmation",
                        key,
                        observed_at,
                        current,
                        {"confirmed": confirmed},
                    )
                )
                continue

            if previous.in_stock.value is False and current.in_stock.value is True and status == "relevant":
                emit(_make_event("became_available", key, observed_at, current, {"reason": "stock_confirmed"}))

            if (
                previous.cash_price is not None
                and current.cash_price is not None
                and previous.price_currency == "RUB"
                and current.price_currency == "RUB"
            ):
                drop = previous.cash_price - current.cash_price
                if drop >= 50_000:
                    emit(
                        _make_event(
                            "price_drop",
                            key,
                            observed_at,
                            current,
                            {"old_price": previous.cash_price, "new_price": current.cash_price, "drop": drop},
                            {"cash_price": previous.cash_price},
                        )
                    )

            confirmed = _new_commercial_confirmations(previous, current)
            if confirmed and status == "relevant":
                emit(
                    _make_event(
                        "critical_confirmation",
                        key,
                        observed_at,
                        current,
                        {"confirmed": confirmed},
                    )
                )

    for key, listing_state in list(next_state.listings.items()):
        source = listing_state.listing.source
        if source not in successful_sources or key in seen_by_source[source] or listing_state.removed:
            continue
        listing_state.misses += 1
        history.append(
            {
                "type": "absence",
                "observed_at": observed_at,
                "source": source,
                "listing_key": key,
                "misses": listing_state.misses,
            }
        )
        if listing_state.status == "relevant" and listing_state.misses >= 2:
            listing_state.removed = True
            emit(
                _make_event(
                    "removed_or_sold",
                    key,
                    observed_at,
                    listing_state.listing,
                    {"reason": "absent_twice"},
                )
            )

    return next_state, events, history
