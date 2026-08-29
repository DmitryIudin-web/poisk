from __future__ import annotations

import hashlib
import json
from typing import Any

from .identity import listing_key, vehicle_key
from .models import Event, Listing, ListingState, MonitorState, SourceResult
from .profiles import TargetProfile
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
    profile: TargetProfile | None = None,
    *,
    known_event_ids: set[str] | None = None,
) -> tuple[MonitorState, list[Event], list[dict[str, Any]]]:
    next_state = MonitorState.from_dict(state.to_dict())
    known = set(state.emitted_event_ids)
    known.update(known_event_ids or ())
    events: list[Event] = []
    history: list[dict[str, Any]] = []
    successful_sources = {name for name, result in source_results.items() if result.ok}
    complete_sources = {
        name for name, result in source_results.items() if result.ok and result.complete
    }
    seen_by_source: dict[str, set[str]] = {name: set() for name in successful_sources}
    incoming_by_key = {
        listing_key(listing): listing
        for result in source_results.values()
        if result.ok
        for listing in result.listings
    }
    active_vehicle_keys: set[str] = set()
    for key, listing_state in next_state.listings.items():
        if listing_state.status != "relevant" or listing_state.removed:
            continue
        incoming = incoming_by_key.get(key)
        if incoming is None:
            active_vehicle_keys.add(vehicle_key(listing_state.listing))
            continue
        incoming_status, _ = qualify(incoming, profile)
        if incoming_status == "relevant" and incoming.sold.value is not True:
            active_vehicle_keys.add(vehicle_key(incoming))

    def emit(event: Event) -> None:
        if event.id not in known:
            events.append(event)
            known.add(event.id)

    for source, result in source_results.items():
        for warning in result.warnings:
            history.append(
                {
                    "type": "source_gap",
                    "observed_at": observed_at,
                    "source": source,
                    "code": warning.code,
                    "message": warning.message,
                    "search_url": result.search_url,
                }
            )
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
            status, missing = qualify(current, profile)
            previous_state = next_state.listings.get(key)
            history.append(
                {
                    "type": "observation",
                    "observed_at": observed_at,
                    "source": source,
                    "listing_key": key,
                    "vehicle_key": vehicle_key(current),
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
                    current_vehicle_key = vehicle_key(current)
                    if current_vehicle_key not in active_vehicle_keys:
                        emit(_make_event("new_relevant", key, observed_at, current, {"status": "relevant"}))
                    active_vehicle_keys.add(current_vehicle_key)
                continue

            previous = previous_state.listing
            was_removed = previous_state.removed
            next_state.listings[key] = ListingState(current, status, missing, 0, observed_at, False)

            if current.sold.value is True:
                next_state.listings[key].removed = True
                if previous_state.status == "relevant" and not was_removed:
                    emit(
                        _make_event(
                            "removed_or_sold",
                            key,
                            observed_at,
                            current,
                            {
                                "reason": "explicit_sold",
                                "transition_anchor": previous_state.last_seen_at,
                            },
                        )
                    )
                continue

            if was_removed and status == "relevant":
                current_vehicle_key = vehicle_key(current)
                if previous_state.status == "relevant":
                    emit(
                        _make_event(
                            "became_available",
                            key,
                            observed_at,
                            current,
                            {
                                "reason": "reappeared",
                                "transition_anchor": previous_state.last_seen_at,
                            },
                        )
                    )
                elif previous_state.status == "candidate":
                    confirmed = sorted(set(previous_state.missing) - set(missing))
                    emit(
                        _make_event(
                            "critical_confirmation",
                            key,
                            observed_at,
                            current,
                            {
                                "confirmed": confirmed,
                                "transition_anchor": previous_state.last_seen_at,
                            },
                        )
                    )
                elif current_vehicle_key not in active_vehicle_keys:
                    emit(
                        _make_event(
                            "new_relevant",
                            key,
                            observed_at,
                            current,
                            {
                                "status": "relevant",
                                "transition_anchor": previous_state.last_seen_at,
                            },
                        )
                    )
                active_vehicle_keys.add(current_vehicle_key)
                continue

            if previous_state.status == "candidate" and status == "relevant":
                confirmed = sorted(set(previous_state.missing) - set(missing))
                emit(
                    _make_event(
                        "critical_confirmation",
                        key,
                        observed_at,
                        current,
                        {
                            "confirmed": confirmed,
                            "transition_anchor": previous_state.last_seen_at,
                        },
                    )
                )
                active_vehicle_keys.add(vehicle_key(current))
                continue

            if previous.in_stock.value is False and current.in_stock.value is True and status == "relevant":
                emit(
                    _make_event(
                        "became_available",
                        key,
                        observed_at,
                        current,
                        {
                            "reason": "stock_confirmed",
                            "transition_anchor": previous_state.last_seen_at,
                        },
                    )
                )
                active_vehicle_keys.add(vehicle_key(current))
                continue

            if previous_state.status == "irrelevant" and status == "relevant":
                current_vehicle_key = vehicle_key(current)
                if current_vehicle_key not in active_vehicle_keys:
                    emit(
                        _make_event(
                            "new_relevant",
                            key,
                            observed_at,
                            current,
                            {
                                "status": "relevant",
                                "transition_anchor": previous_state.last_seen_at,
                            },
                        )
                    )
                active_vehicle_keys.add(current_vehicle_key)
                continue

            currency = current.price_currency
            threshold = (
                profile.price_drop_thresholds.get(currency or "")
                if profile is not None
                else 50_000 if currency == "RUB" else None
            )
            if (
                previous_state.status == "relevant"
                and status == "relevant"
                and threshold is not None
                and previous.cash_price is not None
                and current.cash_price is not None
                and previous.price_currency == currency
                and previous.cash_price - current.cash_price >= threshold
            ):
                drop = previous.cash_price - current.cash_price
                emit(
                    _make_event(
                        "price_drop",
                        key,
                        observed_at,
                        current,
                        {
                            "drop": drop,
                            "currency": currency,
                            "transition_anchor": previous_state.last_seen_at,
                        },
                        {
                            "cash_price": previous.cash_price,
                            "price_currency": previous.price_currency,
                        },
                    )
                )

            confirmed = _new_commercial_confirmations(previous, current)
            if confirmed and status in {"candidate", "relevant"}:
                emit(
                    _make_event(
                        "critical_confirmation",
                        key,
                        observed_at,
                        current,
                        {
                            "confirmed": confirmed,
                            "transition_anchor": previous_state.last_seen_at,
                        },
                    )
                )

    for key, listing_state in list(next_state.listings.items()):
        source = listing_state.listing.source
        if source not in complete_sources or key in seen_by_source[source] or listing_state.removed:
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
                    {
                        "reason": "absent_twice",
                        "transition_anchor": listing_state.last_seen_at,
                    },
                )
            )

    return next_state, events, history
