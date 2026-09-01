from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class SearchProfile:
    make: str = ""
    model: str = ""
    trim: str = ""
    year_from: int | None = None
    year_to: int | None = None
    max_mileage_km: int | None = None
    condition: str = "either"
    markets: list[str] = field(default_factory=list)
    colors: list[str] = field(default_factory=list)
    body_variants: list[str] = field(default_factory=list)
    required_features: list[str] = field(default_factory=list)
    excluded_features: list[str] = field(default_factory=list)
    max_price: float | None = None
    price_currency: str | None = None
    export_vat_required: bool = False
    interval_minutes: int = 60
    enabled: bool = True
    answered_fields: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SearchProfile":
        allowed = {field.name for field in cls.__dataclass_fields__.values()}
        return cls(**{key: value for key, value in data.items() if key in allowed})

    def validate(self) -> list[str]:
        errors: list[str] = []
        if not self.make.strip():
            errors.append("make is required")
        if not self.model.strip():
            errors.append("model is required")
        if self.year_from and self.year_to and self.year_from > self.year_to:
            errors.append("year_from cannot exceed year_to")
        if self.max_mileage_km is not None and self.max_mileage_km < 0:
            errors.append("max_mileage_km cannot be negative")
        if not self.markets:
            errors.append("at least one market is required")
        if not 15 <= int(self.interval_minutes) <= 10080:
            errors.append("interval_minutes must be between 15 and 10080")
        if self.max_price is not None and self.max_price <= 0:
            errors.append("max_price must be positive")
        if self.max_price is not None and not self.price_currency:
            errors.append("price_currency is required when max_price is set")
        return errors


@dataclass(frozen=True)
class Question:
    key: str
    text: str
    kind: str
    required: bool = True
    options: tuple[str, ...] = ()
    help_text: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Listing:
    url: str
    source: str
    title: str
    snippet: str = ""
    price: float | None = None
    currency: str | None = None
    gross_price: float | None = None
    net_price: float | None = None
    export_price: float | None = None
    year: int | None = None
    mileage_km: int | None = None
    color: str | None = None
    vin: str | None = None
    location: str | None = None
    body_variant: str | None = None
    regional_spec: str | None = None
    export_status: bool | None = None
    export_vat: bool | None = None
    vat_status: str | None = None
    image_urls: list[str] = field(default_factory=list)
    evidence: dict[str, dict[str, Any]] = field(default_factory=dict)
    status: str = "candidate"
    missing: list[str] = field(default_factory=list)
    fingerprint: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
