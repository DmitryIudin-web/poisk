from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class Evidence:
    value: bool | None
    source_text: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "Evidence":
        data = data or {}
        return cls(data.get("value"), data.get("source_text"))


@dataclass(frozen=True)
class Listing:
    source: str
    url: str
    listing_id: str | None
    title: str
    model_match: Evidence
    year: int | None
    exterior_black: Evidence
    interior_black: Evidence
    top_trim: Evidence
    dcc: Evidence
    mileage_km: int | None
    is_new: Evidence
    in_stock: Evidence
    sold: Evidence
    cash_price: int | None = None
    advertised_price: int | None = None
    price_currency: str | None = None
    price_qualifier: str | None = None
    vin: str | None = None
    region: str = "unknown"
    source_market: str = "unknown"
    location: str | None = None
    epts_status: str | None = None
    commercial_recycling_fee_status: str | None = None
    target_id: str = "teramont-pro-2026"
    target_name: str = "Volkswagen Teramont Pro 2026"
    powertrain_match: Evidence = field(default_factory=lambda: Evidence(None, None))
    rear_seat_entertainment: Evidence = field(default_factory=lambda: Evidence(None, None))
    steering_left: Evidence = field(default_factory=lambda: Evidence(None, None))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Listing":
        values = dict(data)
        values["target_id"] = values.get("target_id") or "teramont-pro-2026"
        values["target_name"] = values.get("target_name") or "Volkswagen Teramont Pro 2026"
        for name in (
            "model_match",
            "exterior_black",
            "interior_black",
            "top_trim",
            "dcc",
            "is_new",
            "in_stock",
            "sold",
            "powertrain_match",
            "rear_seat_entertainment",
            "steering_left",
        ):
            values[name] = Evidence.from_dict(values.get(name))
        return cls(**values)


@dataclass(frozen=True)
class SourceGap:
    source: str
    code: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class SourceResult:
    source: str
    ok: bool
    listings: tuple[Listing, ...] = ()
    gap: SourceGap | None = None
    search_url: str | None = None
    complete: bool = True
    warnings: tuple[SourceGap, ...] = ()


@dataclass(frozen=True)
class RankedOffer:
    listing: Listing
    status: str
    missing: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "listing": self.listing.to_dict(),
            "status": self.status,
            "missing": list(self.missing),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RankedOffer":
        return cls(
            listing=Listing.from_dict(data["listing"]),
            status=str(data["status"]),
            missing=tuple(str(value) for value in data.get("missing", ())),
        )


@dataclass(frozen=True)
class PriceDigest:
    target_id: str
    target_name: str
    observed_at: str
    successful_sources: int
    failed_sources: int
    confirmed: tuple[RankedOffer, ...] = ()
    candidates: tuple[RankedOffer, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": 1,
            "target_id": self.target_id,
            "target_name": self.target_name,
            "observed_at": self.observed_at,
            "successful_sources": self.successful_sources,
            "failed_sources": self.failed_sources,
            "confirmed": [item.to_dict() for item in self.confirmed],
            "candidates": [item.to_dict() for item in self.candidates],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PriceDigest":
        return cls(
            target_id=str(data["target_id"]),
            target_name=str(data["target_name"]),
            observed_at=str(data["observed_at"]),
            successful_sources=int(data.get("successful_sources", 0)),
            failed_sources=int(data.get("failed_sources", 0)),
            confirmed=tuple(RankedOffer.from_dict(item) for item in data.get("confirmed", ())),
            candidates=tuple(RankedOffer.from_dict(item) for item in data.get("candidates", ())),
        )


@dataclass(frozen=True)
class Event:
    id: str
    kind: str
    listing_key: str
    occurred_at: str
    listing: Listing
    previous: dict[str, Any] = field(default_factory=dict)
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Event":
        values = dict(data)
        values["listing"] = Listing.from_dict(values["listing"])
        return cls(**values)


@dataclass
class ListingState:
    listing: Listing
    status: str
    missing: tuple[str, ...]
    misses: int = 0
    last_seen_at: str | None = None
    removed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "listing": self.listing.to_dict(),
            "status": self.status,
            "missing": list(self.missing),
            "misses": self.misses,
            "last_seen_at": self.last_seen_at,
            "removed": self.removed,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ListingState":
        return cls(
            listing=Listing.from_dict(data["listing"]),
            status=data["status"],
            missing=tuple(data.get("missing", ())),
            misses=int(data.get("misses", 0)),
            last_seen_at=data.get("last_seen_at"),
            removed=bool(data.get("removed", False)),
        )


@dataclass
class MonitorState:
    listings: dict[str, ListingState] = field(default_factory=dict)
    emitted_event_ids: set[str] = field(default_factory=set)

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": 1,
            "listings": {key: value.to_dict() for key, value in self.listings.items()},
            "emitted_event_ids": sorted(self.emitted_event_ids),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "MonitorState":
        data = data or {}
        return cls(
            listings={
                key: ListingState.from_dict(value)
                for key, value in (data.get("listings") or {}).items()
            },
            emitted_event_ids=set(data.get("emitted_event_ids") or ()),
        )
