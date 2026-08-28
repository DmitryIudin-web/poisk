from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

from .models import Evidence


@dataclass(frozen=True)
class EvidenceRule:
    positive_groups: tuple[tuple[str, ...], ...]
    negative_patterns: tuple[str, ...] = ()


@dataclass(frozen=True)
class TargetProfile:
    target_id: str
    display_name: str
    year: int
    max_mileage_km: int
    required_evidence: tuple[str, ...]
    allowed_regions: tuple[str, ...]
    lhd_required_regions: tuple[str, ...]
    price_drop_thresholds: Mapping[str, int]
    evidence_rules: Mapping[str, EvidenceRule]


def match_evidence(text: str, rule: EvidenceRule) -> Evidence:
    for pattern in rule.negative_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return Evidence(False, match.group(0)[:120])
    for group in rule.positive_groups:
        matches = [re.search(pattern, text, re.IGNORECASE) for pattern in group]
        if matches and all(matches):
            return Evidence(True, " | ".join(match.group(0)[:60] for match in matches if match))
    return Evidence(None, None)


def load_target_profile(path: str | Path) -> TargetProfile:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    required = tuple(str(value) for value in payload.get("required_evidence", ()))
    if not required:
        raise ValueError("target profile requires at least one evidence field")

    rules: dict[str, EvidenceRule] = {}
    for name, raw in payload.get("evidence_rules", {}).items():
        positive = tuple(
            tuple(str(pattern) for pattern in group)
            for group in raw.get("positive_groups", ())
        )
        negative = tuple(str(pattern) for pattern in raw.get("negative_patterns", ()))
        if str(name) in required and not any(
            group and any(pattern.strip() for pattern in group) for group in positive
        ):
            raise ValueError(f"required evidence rule has no positive patterns: {name}")
        for pattern in (*negative, *(item for group in positive for item in group)):
            try:
                re.compile(pattern, re.IGNORECASE)
            except re.error as exc:
                raise ValueError(f"invalid evidence regex: {pattern}") from exc
        flat_positive = {pattern for group in positive for pattern in group}
        if flat_positive.intersection(negative):
            raise ValueError(f"contradictory evidence rule: {name}")
        rules[str(name)] = EvidenceRule(positive, negative)

    unknown = set(required) - set(rules)
    if unknown:
        raise ValueError(f"required evidence has no rule: {sorted(unknown)}")

    thresholds = {
        str(code).upper(): int(amount)
        for code, amount in payload.get("price_drop_thresholds", {}).items()
    }
    supported_currencies = {"RUB", "EUR", "GEL", "KGS", "KZT", "USD"}
    if set(thresholds) - supported_currencies:
        raise ValueError("unsupported price-threshold currency")
    if any(amount <= 0 for amount in thresholds.values()):
        raise ValueError("price thresholds must be positive")

    profile = TargetProfile(
        target_id=str(payload["target_id"]),
        display_name=str(payload["display_name"]),
        year=int(payload["year"]),
        max_mileage_km=int(payload["max_mileage_km"]),
        required_evidence=required,
        allowed_regions=tuple(str(value) for value in payload.get("allowed_regions", ())),
        lhd_required_regions=tuple(
            str(value) for value in payload.get("lhd_required_regions", ())
        ),
        price_drop_thresholds=MappingProxyType(thresholds),
        evidence_rules=MappingProxyType(rules),
    )
    if not profile.target_id or profile.max_mileage_km < 0:
        raise ValueError("invalid target identity or mileage boundary")
    return profile
