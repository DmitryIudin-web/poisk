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
    location: str | None = None
    epts_status: str | None = None
    commercial_recycling_fee_status: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Listing":
        values = dict(data)
        for name in (
            "model_match",
            "exterior_black",
            "interior_black",
            "top_trim",
            "dcc",
            "is_new",
            "in_stock",
            "sold",
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
