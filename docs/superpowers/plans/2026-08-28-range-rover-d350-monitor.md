# Range Rover D350 Multi-Vehicle Monitor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend the live Teramont monitor into a profile-driven two-vehicle monitor and add hourly Range Rover L460 D350 Autobiography 2026 discovery for Russia, Kyrgyzstan, Georgia, and Europe without resetting Teramont state.

**Architecture:** Keep the existing Python package, scanner, state store, event engine, workflow, and Telegram delivery. Add a validated `TargetProfile`, pass it through normalization/qualification/events, keep legacy Teramont state at the `monitor-state` root, and store Range Rover state under `range-rover-d350/`. Run the two targets sequentially in one serialized GitHub Actions job.

**Tech Stack:** Python 3.12 standard library, `unittest`, JSON configuration, GitHub Actions, Telegram Bot API.

**Spec:** `docs/superpowers/specs/2026-08-28-range-rover-d350-monitor-design.md`

## Global Constraints

- Accept only full-size Range Rover L460 D350 Autobiography, never Sport, Velar, or Evoque.
- Require model year 2026, black exterior, black interior, 0–1,000 km inclusive, physical stock, and factory two-screen Rear Seat Entertainment.
- Require confirmed left-hand drive for European Range Rover listings.
- Accept both SWB and LWB.
- Store source currencies without conversion.
- Range Rover price-drop thresholds are exactly RUB 100,000; EUR 1,000; GEL 3,000; KGS 100,000.
- Preserve the existing Teramont 1,000 km boundary, 50,000 RUB threshold, root state paths, event history, and command compatibility.
- Keep only the five existing Telegram event categories.
- Missing evidence is `unknown`; an unavailable source is `source_gap`, never zero inventory.
- Do not bypass access controls or store raw HTML, credentials, cookies, phone numbers, email addresses, or seller contact details.
- Read Telegram credentials only from `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`.
- Add no third-party runtime dependency.

---

## File map

**Create:**

- `src/teramont_monitor/profiles.py` — immutable evidence rules, target profiles, JSON validation.
- `config/targets/teramont-pro-2026.json` — current Teramont policy, including the 50,000 RUB threshold.
- `config/targets/range-rover-l460-d350-autobiography-2026.json` — Range Rover qualification and per-currency thresholds.
- `config/range-rover-sources.json` — Russia, Kyrgyzstan, Georgia, and Europe source definitions.
- `tests/test_profiles.py` — profile validation and exact policy contracts.
- `tests/fixtures/range_rover_sources.html` — sanitized links for every new source pattern.
- `tests/fixtures/range_rover_detail_ru.html` — positive Russian Range Rover evidence.
- `tests/fixtures/range_rover_detail_en.html` — positive English/LHD/RSE evidence.
- `tests/fixtures/range_rover_detail_de_negative.html` — deleted-RSE and RHD negative evidence.

**Modify:**

- `src/teramont_monitor/models.py` — backward-compatible target and Range Rover evidence fields.
- `src/teramont_monitor/normalize.py` — profile rules, EUR/GEL, target market, RSE, LHD.
- `src/teramont_monitor/qualify.py` — profile-driven hard requirements.
- `src/teramont_monitor/identity.py` — target-aware keys while preserving Teramont keys.
- `src/teramont_monitor/events.py` — profile thresholds and target-aware transitions.
- `src/teramont_monitor/sources.py` — source market plus profile propagation.
- `src/teramont_monitor/cli.py` — `--target` and profile propagation.
- `src/teramont_monitor/telegram.py` — vehicle/market labels, currencies, RSE/LHD.
- `.github/workflows/monitor.yml` — sequential two-target collection and delivery.
- `README.md` — two monitors, exact criteria, state layout, sources, and commands.
- `tests/test_normalize.py`, `tests/test_qualify.py`, `tests/test_identity.py`, `tests/test_events.py`, `tests/test_sources.py`, `tests/test_cli.py`, `tests/test_telegram.py`, `tests/test_workflow.py` — regression and new-target contracts.

---

### Task 1: Add validated target profiles

**Files:**

- Create: `src/teramont_monitor/profiles.py`
- Create: `config/targets/teramont-pro-2026.json`
- Create: `config/targets/range-rover-l460-d350-autobiography-2026.json`
- Create: `tests/test_profiles.py`

**Interfaces:**

- Produces: `EvidenceRule(positive_groups, negative_patterns)`.
- Produces: `TargetProfile(target_id, display_name, year, max_mileage_km, required_evidence, lhd_required_regions, price_drop_thresholds, evidence_rules)`.
- Produces: `load_target_profile(path: str | Path) -> TargetProfile`.
- Produces: `match_evidence(text: str, rule: EvidenceRule) -> Evidence`.

- [ ] **Step 1: Write failing profile contract tests**

```python
# tests/test_profiles.py
import json
import tempfile
import unittest
from pathlib import Path

from teramont_monitor.profiles import load_target_profile, match_evidence

ROOT = Path(__file__).resolve().parents[1]


class TargetProfileTests(unittest.TestCase):
    def test_checked_profiles_have_exact_ids_and_thresholds(self) -> None:
        teramont = load_target_profile(ROOT / "config/targets/teramont-pro-2026.json")
        range_rover = load_target_profile(
            ROOT / "config/targets/range-rover-l460-d350-autobiography-2026.json"
        )
        self.assertEqual(teramont.target_id, "teramont-pro-2026")
        self.assertEqual(teramont.price_drop_thresholds, {"RUB": 50_000})
        self.assertEqual(range_rover.max_mileage_km, 1_000)
        self.assertEqual(range_rover.lhd_required_regions, ("europe",))
        self.assertEqual(
            range_rover.price_drop_thresholds,
            {"RUB": 100_000, "EUR": 1_000, "GEL": 3_000, "KGS": 100_000},
        )

    def test_negative_pattern_wins_before_positive_group(self) -> None:
        profile = load_target_profile(
            ROOT / "config/targets/range-rover-l460-d350-autobiography-2026.json"
        )
        evidence = match_evidence(
            "Range Rover Sport D350 Autobiography", profile.evidence_rules["model_match"]
        )
        self.assertIs(evidence.value, False)

    def test_loader_rejects_invalid_regex_and_empty_required_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "invalid.json"
            path.write_text(json.dumps({
                "target_id": "bad", "display_name": "Bad", "year": 2026,
                "max_mileage_km": 1000, "required_evidence": [],
                "lhd_required_regions": [], "price_drop_thresholds": {"RUB": 1},
                "evidence_rules": {"model_match": {
                    "positive_groups": [["["]], "negative_patterns": []
                }},
            }), encoding="utf-8")
            with self.assertRaises(ValueError):
                load_target_profile(path)
```

Add separate invalid payload subtests for an empty `target_id`, an unsupported
threshold currency `ABC`, a non-positive threshold, and the same regex appearing
in both a rule's positive and negative sets.

- [ ] **Step 2: Run the new tests and verify RED**

Run:

```powershell
$env:PYTHONPATH = "src"
python -m unittest tests.test_profiles -v
```

Expected: import failure because `teramont_monitor.profiles` does not exist.

- [ ] **Step 3: Implement immutable rules and strict loader**

```python
# src/teramont_monitor/profiles.py
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

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
    lhd_required_regions: tuple[str, ...]
    price_drop_thresholds: dict[str, int]
    evidence_rules: dict[str, EvidenceRule]


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
        positive = tuple(tuple(str(pattern) for pattern in group) for group in raw.get("positive_groups", ()))
        negative = tuple(str(pattern) for pattern in raw.get("negative_patterns", ()))
        for pattern in (*negative, *(item for group in positive for item in group)):
            re.compile(pattern, re.IGNORECASE)
        flat_positive = {pattern for group in positive for pattern in group}
        if flat_positive.intersection(negative):
            raise ValueError(f"contradictory evidence rule: {name}")
        rules[str(name)] = EvidenceRule(positive, negative)
    unknown = set(required) - set(rules)
    if unknown:
        raise ValueError(f"required evidence has no rule: {sorted(unknown)}")
    thresholds = {str(code).upper(): int(amount) for code, amount in payload.get("price_drop_thresholds", {}).items()}
    supported_currencies = {"RUB", "EUR", "GEL", "KGS", "KZT", "USD"}
    if set(thresholds) - supported_currencies:
        raise ValueError("unsupported price-threshold currency")
    if any(amount <= 0 for amount in thresholds.values()):
        raise ValueError("price thresholds must be positive")
    profile = TargetProfile(
        target_id=str(payload["target_id"]), display_name=str(payload["display_name"]),
        year=int(payload["year"]), max_mileage_km=int(payload["max_mileage_km"]),
        required_evidence=required,
        lhd_required_regions=tuple(str(value) for value in payload.get("lhd_required_regions", ())),
        price_drop_thresholds=thresholds, evidence_rules=rules,
    )
    if not profile.target_id or profile.max_mileage_km < 0:
        raise ValueError("invalid target identity or mileage boundary")
    return profile
```

Write the Teramont profile with this exact policy payload:

```json
{
  "target_id": "teramont-pro-2026",
  "display_name": "Volkswagen Teramont Pro 2026",
  "year": 2026,
  "max_mileage_km": 1000,
  "required_evidence": ["model_match", "top_trim", "dcc"],
  "lhd_required_regions": [],
  "price_drop_thresholds": {"RUB": 50000},
  "evidence_rules": {
    "model_match": {
      "positive_groups": [["(?:volkswagen\\s+)?teramont\\s*pro|терамонт\\s*про|途昂\\s*pro"]],
      "negative_patterns": ["teramont(?!\\s*pro)|терамонт(?!\\s*про)"]
    },
    "top_trim": {
      "positive_groups": [["\\bpeak\\b"], ["\\bsummit\\b"], ["максимальная комплектация|топовая комплектация|top trim|maximum trim"]],
      "negative_patterns": []
    },
    "dcc": {
      "positive_groups": [["\\bdcc\\b|adaptive chassis control|адаптивная подвеска|адаптивное шасси"]],
      "negative_patterns": ["без dcc|no dcc"]
    }
  }
}
```

Write the Range Rover profile with this exact policy payload:

```json
{
  "target_id": "range-rover-l460-d350-autobiography-2026",
  "display_name": "Range Rover L460 D350 Autobiography 2026",
  "year": 2026,
  "max_mileage_km": 1000,
  "required_evidence": ["model_match", "powertrain_match", "top_trim", "rear_seat_entertainment"],
  "lhd_required_regions": ["europe"],
  "price_drop_thresholds": {"RUB": 100000, "EUR": 1000, "GEL": 3000, "KGS": 100000},
  "evidence_rules": {
    "model_match": {
      "positive_groups": [["\\b(?:land\\s+rover\\s+)?range\\s+rover\\b|\\bl460\\b"]],
      "negative_patterns": ["range\\s+rover\\s+sport", "range\\s+rover\\s+velar", "range\\s+rover\\s+evoque"]
    },
    "powertrain_match": {
      "positive_groups": [
        ["\\bd350\\b"],
        ["\\b3[.,]0\\b", "diesel|дизел|дизель", "(?:257|258)\\s*kw|(?:349|350|351)\\s*(?:hp|л\\.?\\s*с\\.?)"]
      ],
      "negative_patterns": ["\\bp(?:400|440|460|510|530|550|615)e?\\b"]
    },
    "top_trim": {
      "positive_groups": [["\\bautobiography\\b|автобиограф"]],
      "negative_patterns": []
    },
    "rear_seat_entertainment": {
      "positive_groups": [["rear seat entertainment|\\brse\\b|fond[- ]tv|fond entertainment|multimediasystem im fond|задн(?:ие|их) мультимедийн(?:ые|ых) экран|монитор(?:ы|а) для задних пассажиров"]],
      "negative_patterns": ["no rear seat entertainment|entfall multimediasystem im fond|без задних монитор|aftermarket (?:tablet|screen)|нештатн(?:ый|ые) (?:планшет|монитор)"]
    },
    "steering_left": {
      "positive_groups": [["left[- ]hand drive|\\blhd\\b|left steering|steering\\s*:?\\s*left|левый руль|руль слева|linkslenker"]],
      "negative_patterns": ["right[- ]hand drive|\\brhd\\b|right steering|steering\\s*:?\\s*right|правый руль|руль справа|rechtslenker"]
    }
  }
}
```

- [ ] **Step 4: Run profile tests and the existing suite**

Run:

```powershell
python -m unittest tests.test_profiles -v
python -m unittest discover -s tests -t . -v
```

Expected: all profile tests pass; all existing 80 tests remain green.

- [ ] **Step 5: Commit the profile boundary**

```powershell
git add -- src/teramont_monitor/profiles.py config/targets/teramont-pro-2026.json config/targets/range-rover-l460-d350-autobiography-2026.json tests/test_profiles.py
git commit -m "feat: add validated vehicle target profiles"
```

---

### Task 2: Make canonical listings profile-aware and backward-compatible

**Files:**

- Modify: `src/teramont_monitor/models.py:18-61`
- Modify: `src/teramont_monitor/normalize.py:10-237`
- Create: `tests/fixtures/range_rover_detail_ru.html`
- Create: `tests/fixtures/range_rover_detail_en.html`
- Create: `tests/fixtures/range_rover_detail_de_negative.html`
- Modify: `tests/test_normalize.py`

**Interfaces:**

- Consumes: `TargetProfile` and `match_evidence` from Task 1.
- Produces: `Listing.target_id`, `target_name`, `powertrain_match`, `rear_seat_entertainment`, and `steering_left`.
- Produces: `normalize_listing(source, url, listing_id, text, metadata, profile, market=None) -> Listing`.
- Preserves: `Listing.from_dict(old_state)` defaults missing target fields to Teramont.

- [ ] **Step 1: Add failing normalization and legacy-state tests**

Add tests that load the two checked profiles and assert:

```python
range_rover = normalize_listing(
    "autobridge", "https://autobridge.ge/en/listings/range-rover-abc123", "abc123",
    "Range Rover L460 D350 2026 Autobiography. Exterior: Santorini Black. "
    "Interior: Ebony Black. Mileage 19 km. In stock. Left hand drive. "
    "Factory Rear Seat Entertainment with two rear screens.",
    {"price": 167_000, "price_currency": "EUR", "location": "Tbilisi"},
    RANGE_ROVER_PROFILE, market="georgia",
)
self.assertEqual(range_rover.target_id, "range-rover-l460-d350-autobiography-2026")
self.assertIs(range_rover.powertrain_match.value, True)
self.assertIs(range_rover.rear_seat_entertainment.value, True)
self.assertIs(range_rover.steering_left.value, True)
self.assertEqual(range_rover.price_currency, "EUR")
self.assertEqual(range_rover.region, "georgia")

negative = normalize_listing(
    "mobile", "https://suchen.mobile.de/fahrzeuge/details.html?id=1", "1",
    "Range Rover Sport D350 Autobiography 2026. Schwarz. "
    "Entfall Multimediasystem im Fond. Rechtslenker. 10 km. Sofort verfügbar.",
    {}, RANGE_ROVER_PROFILE, market="europe",
)
self.assertIs(negative.model_match.value, False)
self.assertIs(negative.rear_seat_entertainment.value, False)
self.assertIs(negative.steering_left.value, False)

legacy = Listing.from_dict(matching_listing().to_dict() | {
    "target_id": None,
})
self.assertEqual(legacy.target_id, "teramont-pro-2026")
```

Also assert GEL symbols/codes normalize to `GEL`, EUR symbols/codes normalize to
`EUR`, USD symbols/codes remain `USD` without conversion, contact data is stripped
from new titles, explicit sold wins over in-stock, `Model Year 2026` is preferred
over a different first-registration year, a first-registration year alone leaves
the model year unknown, `Ebony` is black, and `Light Cloud/ebony` is not treated as
a primarily black interior. The three minimized fixtures contain no
phone/email/contact payload.

- [ ] **Step 2: Run normalization tests and verify RED**

Run:

```powershell
python -m unittest tests.test_normalize -v
```

Expected: failures for the new signature, fields, currencies, and markets.

- [ ] **Step 3: Extend `Listing` with safe defaults**

Add these defaulted fields after the existing serialized fields:

```python
target_id: str = "teramont-pro-2026"
target_name: str = "Volkswagen Teramont Pro 2026"
powertrain_match: Evidence = field(default_factory=lambda: Evidence(None, None))
rear_seat_entertainment: Evidence = field(default_factory=lambda: Evidence(None, None))
steering_left: Evidence = field(default_factory=lambda: Evidence(None, None))
```

In `Listing.from_dict`, normalize a missing/null `target_id` and `target_name` to
the legacy Teramont values and add the three new evidence names to the existing
`Evidence.from_dict` conversion loop.

- [ ] **Step 4: Generalize normalization minimally**

Change the signature to:

```python
def normalize_listing(
    source: str,
    url: str,
    listing_id: str | None,
    text: str,
    metadata: Mapping[str, Any] | None,
    profile: TargetProfile,
    market: str | None = None,
) -> Listing:
```

Use `match_evidence(clean_text, profile.evidence_rules[name])` for configured
evidence fields. Keep existing color, mileage, stock, sold, VIN, EPTS, recycling
fee, and contact-redaction functions. Extend money tokens and `_currency` with:

```python
if "eur" in lowered or "€" in value or "euro" in lowered:
    return "EUR"
if "gel" in lowered or "₾" in value or "lari" in lowered:
    return "GEL"
if "usd" in lowered or "$" in value or "dollar" in lowered:
    return "USD"
```

Pass `market` as the region when it is one of `russia`, `kyrgyzstan`, `georgia`,
or `europe`; otherwise retain the legacy inference path. Populate all new fields
and use `profile.display_name` as the safe fallback title. Extend the money regex
with `€|eur|euro|₾|gel|lari|\$|usd|dollar`; never map an explicit unknown code to
RUB.

Add a deterministic year helper:

```python
def _extract_model_year(text: str, metadata: Mapping[str, Any]) -> int | None:
    structured = metadata.get("model_year")
    if isinstance(structured, (int, float)) and not isinstance(structured, bool):
        return int(structured)
    explicit = re.search(r"(?:model year|модельный год|год выпуска|year)\D{0,12}(20\d{2})", text, re.IGNORECASE)
    if explicit:
        return int(explicit.group(1))
    if re.search(r"first registration|первая регистрация", text, re.IGNORECASE):
        return None
    fallback = re.search(r"\b(20\d{2})\b", text)
    return int(fallback.group(1)) if fallback else None
```

Extend black aliases with `ebony`, but evaluate only the first slash-separated
interior color as primary. After sold evidence is computed, force
`in_stock = Evidence(False, sold.source_text)` when `sold.value is True`.

- [ ] **Step 5: Run focused tests and all legacy normalization/qualification tests**

Run:

```powershell
python -m unittest tests.test_profiles tests.test_normalize tests.test_qualify -v
```

Expected: normalization tests pass. Qualification may still use its old policy,
but all existing Teramont tests remain green after their helper passes the
Teramont profile to `normalize_listing`.

- [ ] **Step 6: Commit the canonical model and normalizer**

```powershell
git add -- src/teramont_monitor/models.py src/teramont_monitor/normalize.py tests/test_normalize.py tests/test_qualify.py tests/fixtures/range_rover_detail_ru.html tests/fixtures/range_rover_detail_en.html tests/fixtures/range_rover_detail_de_negative.html
git commit -m "feat: normalize profile-specific vehicle evidence"
```

---

### Task 3: Drive qualification from the selected profile

**Files:**

- Modify: `src/teramont_monitor/qualify.py:1-23`
- Modify: `tests/test_qualify.py`

**Interfaces:**

- Consumes: `Listing` from Task 2 and `TargetProfile` from Task 1.
- Produces: `qualify(listing: Listing, profile: TargetProfile) -> tuple[str, tuple[str, ...]]`.
- Contract: false hard evidence yields `irrelevant`; unknown yields `candidate`; all true yields `relevant`.

- [ ] **Step 1: Add failing Range Rover qualification matrix**

Create a `matching_range_rover(**changes)` helper and subtests for:

```python
self.assertEqual(qualify(matching_range_rover(), RANGE_ROVER_PROFILE), ("relevant", ()))
self.assertEqual(qualify(matching_range_rover(mileage_km=1_000), RANGE_ROVER_PROFILE), ("relevant", ()))
self.assertEqual(qualify(matching_range_rover(mileage_km=1_001), RANGE_ROVER_PROFILE)[0], "irrelevant")
self.assertEqual(qualify(replace(matching_range_rover(), rear_seat_entertainment=Evidence(None)), RANGE_ROVER_PROFILE), ("candidate", ("rear_seat_entertainment",)))
self.assertEqual(qualify(replace(matching_range_rover(), rear_seat_entertainment=Evidence(False)), RANGE_ROVER_PROFILE)[0], "irrelevant")
self.assertEqual(qualify(replace(matching_range_rover(), region="europe", steering_left=Evidence(None)), RANGE_ROVER_PROFILE), ("candidate", ("steering_left",)))
self.assertEqual(qualify(replace(matching_range_rover(), region="europe", steering_left=Evidence(False)), RANGE_ROVER_PROFILE)[0], "irrelevant")
self.assertEqual(qualify(replace(matching_range_rover(), region="georgia", steering_left=Evidence(None)), RANGE_ROVER_PROFILE), ("relevant", ()))
```

Retain explicit tests for the Teramont eight requirements and exact 1,000 km
boundary using `TERAMONT_PROFILE`.

- [ ] **Step 2: Run qualification tests and verify RED**

Run: `python -m unittest tests.test_qualify -v`

Expected: Range Rover and profile-argument tests fail against the hard-coded
Teramont function.

- [ ] **Step 3: Implement profile-driven checks**

```python
def qualify(listing: Listing, profile: TargetProfile) -> tuple[str, tuple[str, ...]]:
    checks: list[tuple[str, bool | None]] = [
        (name, getattr(listing, name).value) for name in profile.required_evidence
    ]
    checks.extend((
        ("year", None if listing.year is None else listing.year == profile.year),
        ("exterior_black", listing.exterior_black.value),
        ("interior_black", listing.interior_black.value),
        ("mileage", None if listing.mileage_km is None else 0 <= listing.mileage_km <= profile.max_mileage_km),
        ("in_stock", listing.in_stock.value),
    ))
    if listing.region in profile.lhd_required_regions:
        checks.append(("steering_left", listing.steering_left.value))
    failed = tuple(name for name, value in checks if value is False)
    if failed:
        return "irrelevant", failed
    unknown = tuple(name for name, value in checks if value is None)
    return ("candidate", unknown) if unknown else ("relevant", ())
```

- [ ] **Step 4: Run qualification and normalization suites**

Run: `python -m unittest tests.test_qualify tests.test_normalize -v`

Expected: all tests pass.

- [ ] **Step 5: Commit qualification**

```powershell
git add -- src/teramont_monitor/qualify.py tests/test_qualify.py
git commit -m "feat: qualify listings by target profile"
```

---

### Task 4: Configure and scan Range Rover markets

**Files:**

- Create: `config/range-rover-sources.json`
- Create: `tests/fixtures/range_rover_sources.html`
- Modify: `src/teramont_monitor/sources.py:23-177`
- Modify: `tests/test_sources.py`
- Modify: `tests/test_html.py`

**Interfaces:**

- Consumes: `TargetProfile` and the new `normalize_listing` signature.
- Produces: `SourceConfig.market: str | None`.
- Produces: `scan_source(config, profile, *, fetcher=None, sleeper=time.sleep) -> SourceResult`.
- Produces: `scan_all(configs, profile, *, fetcher=None, sleeper=time.sleep) -> dict[str, SourceResult]`.

- [ ] **Step 1: Write failing source-config and link-contract tests**

Assert exactly nine Range Rover sources and markets:

```python
configs = load_source_configs(ROOT / "config/range-rover-sources.json")
self.assertEqual(
    [(item.name, item.market) for item in configs],
    [
        ("autoru", "russia"), ("drom", "russia"), ("avito", "russia"),
        ("mashina", "kyrgyzstan"), ("myauto", "georgia"),
        ("landrover_georgia", "georgia"), ("autobridge", "georgia"),
        ("mobile_de", "europe"), ("autoscout24", "europe"),
    ],
)
```

For every config, run `extract_links` against `range_rover_sources.html` and
assert one allowed listing link with a stable ID. Add a scanner test proving that
the profile and `market` reach normalization and that one blocked detail remains
a warning rather than discarding another successful listing.

- [ ] **Step 2: Run focused tests and verify RED**

Run: `python -m unittest tests.test_sources tests.test_html -v`

Expected: missing config/fixture and missing `market`/profile arguments.

- [ ] **Step 3: Add exact source configuration**

Use these search roots and link contracts:

| Source | Search root | Exact `listing_pattern` |
|---|---|---|
| Auto.ru | `https://auto.ru/cars/land_rover/range_rover/all/?year_from=2026&year_to=2026` | `https?://(?:www\\.)?auto\\.ru/cars/(?:new|used)/(?:group|sale)/land_rover/range_rover/(?:[^\\s\\\"'<>]+/)*(?P<id>\\d+-[0-9a-f]+)` |
| Drom | `https://auto.drom.ru/land_rover/range_rover/?minyear=2026&maxyear=2026` | `https?://auto\\.drom\\.ru/(?:[^/]+/)?land_rover/range_rover/(?P<id>\\d+)\\.html` |
| Avito | `https://www.avito.ru/all/avtomobili?q=range%20rover%20d350%20autobiography%202026` | `https?://(?:www\\.|m\\.)?avito\\.ru/[^/]+/avtomobili/[^?#\\\"']+_(?P<id>\\d{6,})` |
| Mashina.kg | `https://m.mashina.kg/catalog/passenger?q=Range%20Rover%20D350%20Autobiography` | `https?://m\\.mashina\\.kg/details/land-rover-range-rover-(?P<id>[0-9a-f]{24})` |
| MyAuto | `https://www.myauto.ge/en/main` | `https?://(?:www\\.)?myauto\\.ge/(?:en|ka|ru)/pr/(?P<id>\\d+)/[^\\s\\\"'<>]+` |
| Land Rover Georgia | `https://www.landrover-georgia.com/shop/en/approved/range-rover/` | `https?://www\\.landrover-georgia\\.com/shop/en/approved/range-rover/range-rover/(?P<id>[a-z0-9-]+)` |
| AutoBridge | `https://autobridge.ge/en/listings?make=land-rover&model=range-rover&yearFrom=2026&yearTo=2026` | `https?://autobridge\\.ge/en/listings/land-rover-range-rover-2026-(?P<id>[0-9a-f]{8})` |
| mobile.de | `https://suchen.mobile.de/auto/land-rover-range-rover-tageszulassung.html` | `https?://suchen\\.mobile\\.de/fahrzeuge/details\\.html\\?id=(?P<id>\\d+)` |
| AutoScout24 | `https://www.autoscout24.com/lst/land-rover/range-rover/ve_range-rover-d350?fregfrom=2026&fregto=2026&kmto=1000` | `https?://(?:www\\.)?autoscout24\\.com/offers/[^\\s\\\"'<>]+-(?P<id>[0-9a-f]{8}-[0-9a-f-]{27})` |

Use the existing conservative timeout, detail cap, delay, host allowlist, empty
markers, blocked markers, and no authentication. Qualification, not the search
URL, remains the final correctness gate.

- [ ] **Step 4: Propagate profile and market through the scanner**

Add `market: str | None = None` to `SourceConfig`, load it from JSON, and replace
the detail normalization call with:

```python
listings.append(normalize_listing(
    config.name, url, listing_id, detail_text, metadata, profile, market=config.market
))
```

Require `profile` in `scan_source` and `scan_all`. Update all current scanner
tests to load the Teramont profile explicitly. Do not create a new transport or
source class when the shared extractor passes the new fixtures.

- [ ] **Step 5: Run scanner, extractor, and profile tests**

Run:

```powershell
python -m unittest tests.test_sources tests.test_html tests.test_profiles -v
```

Expected: all tests pass; no live network request occurs.

- [ ] **Step 6: Commit source support**

```powershell
git add -- config/range-rover-sources.json src/teramont_monitor/sources.py tests/test_sources.py tests/test_html.py tests/fixtures/range_rover_sources.html
git commit -m "feat: scan Range Rover markets"
```

---

### Task 5: Make identity and events target/currency-aware

**Files:**

- Modify: `src/teramont_monitor/identity.py:15-25`
- Modify: `src/teramont_monitor/events.py:19-335`
- Modify: `tests/test_identity.py`
- Modify: `tests/test_events.py`
- Modify: `tests/test_storage.py`

**Interfaces:**

- Consumes: `TargetProfile` and profile-aware `qualify`.
- Produces: legacy-preserving `listing_key`/`vehicle_key`.
- Produces: `apply_scan(state, source_results, observed_at, profile, *, known_event_ids=None)`.
- Event `detail` for price drops contains both `drop` and `currency`.

- [ ] **Step 1: Write failing identity compatibility tests**

```python
legacy = matching_listing()
self.assertEqual(listing_key(legacy), "drom:123")
self.assertEqual(vehicle_key(replace(legacy, vin="WVGZZZ1T0LW000001")), "vin:WVGZZZ1T0LW000001")

rr = matching_range_rover(source="drom", listing_id="123", vin="SALGA2BK0RA000001")
self.assertEqual(
    listing_key(rr),
    "range-rover-l460-d350-autobiography-2026:drom:123",
)
self.assertEqual(
    vehicle_key(rr),
    "range-rover-l460-d350-autobiography-2026:vin:SALGA2BK0RA000001",
)
```

- [ ] **Step 2: Write failing price-threshold and transition tests**

For each RR currency, assert below boundary is silent and exact boundary emits
one `price_drop`. Assert different previous/current currencies are silent. Assert
the event detail currency equals the listing currency. Re-run existing tests for
candidate confirmation, physical availability, stable event IDs, two successful
misses, failed/partial scans, and duplicate VIN offers with the explicit Teramont
profile.

- [ ] **Step 3: Run event/identity tests and verify RED**

Run: `python -m unittest tests.test_identity tests.test_events tests.test_storage -v`

Expected: target-aware key and per-currency threshold tests fail.

- [ ] **Step 4: Preserve Teramont keys and prefix other targets**

```python
LEGACY_TARGET_ID = "teramont-pro-2026"


def _prefix(listing: Listing) -> str:
    return "" if listing.target_id == LEGACY_TARGET_ID else f"{listing.target_id}:"


def listing_key(listing: Listing) -> str:
    prefix = _prefix(listing)
    if listing.listing_id:
        return f"{prefix}{listing.source}:{listing.listing_id}"
    digest = hashlib.sha256(canonical_url(listing.url).encode("utf-8")).hexdigest()[:24]
    return f"{prefix}{listing.source}:url:{digest}"
```

Apply the same prefix rule before `vin:` in `vehicle_key`.

- [ ] **Step 5: Pass the profile through events and apply thresholds**

Make `profile` a positional argument immediately after `observed_at`. Replace
every `qualify(listing)` call with `qualify(listing, profile)`. Replace the
hard-coded RUB block with:

```python
currency = current.price_currency
threshold = profile.price_drop_thresholds.get(currency or "")
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
    emit(_make_event(
        "price_drop", key, observed_at, current,
        {"drop": drop, "currency": currency, "transition_anchor": previous_state.last_seen_at},
        {"cash_price": previous.cash_price, "price_currency": previous.price_currency},
    ))
```

Do not change the five event kinds, two-complete-scan removal rule, partial-scan
protection, pending-event deduplication, or commercial-confirmation fields.

- [ ] **Step 6: Run focused and storage compatibility tests**

Run:

```powershell
python -m unittest tests.test_identity tests.test_events tests.test_storage -v
```

Expected: all tests pass, including a serialized pre-feature Teramont state loaded
and saved without changing its listing keys.

- [ ] **Step 7: Commit identity and event policy**

```powershell
git add -- src/teramont_monitor/identity.py src/teramont_monitor/events.py tests/test_identity.py tests/test_events.py tests/test_storage.py
git commit -m "feat: isolate target events and currency thresholds"
```

---

### Task 6: Parameterize CLI state and distinguish Telegram messages

**Files:**

- Modify: `src/teramont_monitor/cli.py:19-130`
- Modify: `src/teramont_monitor/telegram.py:22-91`
- Modify: `tests/test_cli.py`
- Modify: `tests/test_telegram.py`

**Interfaces:**

- Consumes: `load_target_profile`, profile-aware `scan_all`, and `apply_scan`.
- Produces: `collect(config_path, state_dir, *, target_path=..., fetcher=None, dry_run=False, observed_at=None)`.
- Produces: `smoke(config_path, *, target_path=..., fetcher=None)`.
- CLI adds `--target`, defaulting to `config/targets/teramont-pro-2026.json`.
- Telegram formats target, market, price-drop currency, RSE, and steering evidence.

- [ ] **Step 1: Add failing CLI profile/state-isolation tests**

Use two temporary state directories. Collect the existing Teramont fixtures into
the root and Range Rover fixtures into `range-rover-d350/`. Assert:

```python
self.assertTrue((root / "state.json").exists())
self.assertTrue((root / "range-rover-d350/state.json").exists())
self.assertTrue(all(
    item.listing.target_id == "teramont-pro-2026"
    for item in load_state(root / "state.json").listings.values()
))
self.assertTrue(all(
    item.listing.target_id == "range-rover-l460-d350-autobiography-2026"
    for item in load_state(root / "range-rover-d350/state.json").listings.values()
))
```

Also assert `main(["collect", ..., "--target", rr_profile])` returns the summary
exit code and that legacy CLI invocation without `--target` still selects
Teramont.

- [ ] **Step 2: Add failing Telegram formatting tests**

Assert a Range Rover EUR event contains:

- `Range Rover L460 D350 Autobiography 2026`;
- `Европа`;
- `1 000 €` for an exact threshold drop;
- known factory RSE and left-hand-drive evidence;
- the stable event ID;
- no bot token, chat ID, email, or phone.

Add labels `Кыргызстан`, `Грузия`, and `Европа`; keep existing Teramont region
labels.

- [ ] **Step 3: Run CLI/Telegram tests and verify RED**

Run: `python -m unittest tests.test_cli tests.test_telegram -v`

Expected: failures for missing target option/profile propagation and target-aware
formatting.

- [ ] **Step 4: Load one profile per collect/smoke operation**

```python
DEFAULT_TARGET = "config/targets/teramont-pro-2026.json"


def collect(config_path, state_dir, *, target_path=DEFAULT_TARGET, fetcher=None, dry_run=False, observed_at=None):
    profile = load_target_profile(target_path)
    results = scan_all(load_source_configs(config_path), profile, fetcher=fetcher)
    # Existing load/history/pending flow remains unchanged.
    next_state, events, history = apply_scan(
        state, results, observed_at or _now(), profile, known_event_ids=known
    )
```

Add `--target` to `collect` and `smoke`; leave `notify --state-dir` unchanged
because pending events contain their target identity.

- [ ] **Step 5: Format target and source currency correctly**

Extend `_money` with `EUR: €`, `GEL: ₾`, and `USD: $`. For price drops call:

```python
drop = _money(event.detail.get("drop"), event.detail.get("currency") or listing.price_currency)
```

Add the escaped `listing.target_name` and region label before price fields. Show
`Factory RSE: confirmed` and `Left-hand drive: confirmed` only for known true
evidence; do not print unknown as false.

- [ ] **Step 6: Run focused tests and full regression**

Run:

```powershell
python -m unittest tests.test_cli tests.test_telegram -v
python -m unittest discover -s tests -t . -v
```

Expected: all tests pass.

- [ ] **Step 7: Commit CLI and Telegram changes**

```powershell
git add -- src/teramont_monitor/cli.py src/teramont_monitor/telegram.py tests/test_cli.py tests/test_telegram.py
git commit -m "feat: run and notify multiple vehicle targets"
```

---

### Task 7: Run both targets hourly and document operation

**Files:**

- Modify: `.github/workflows/monitor.yml:1-103`
- Modify: `tests/test_workflow.py`
- Modify: `README.md`

**Interfaces:**

- Consumes: CLI `collect --target` and target-bearing pending events.
- Produces: one serialized hourly job, legacy root state, Range Rover subdirectory state.
- Preserves: exact secret names and `contents: write` least privilege.

- [ ] **Step 1: Write failing workflow contract tests**

Assert the workflow:

```python
self.assertIn("config/targets/teramont-pro-2026.json", text)
self.assertIn("config/targets/range-rover-l460-d350-autobiography-2026.json", text)
self.assertIn("config/range-rover-sources.json", text)
self.assertIn('$STATE_DIR/range-rover-d350', text)
self.assertEqual(text.count("TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}"), 1)
self.assertEqual(text.count("TELEGRAM_CHAT_ID: ${{ secrets.TELEGRAM_CHAT_ID }}"), 1)
```

Also assert:

- schedule remains `17 * * * *` and `workflow_dispatch` remains;
- tests run before either collection;
- both collections finish before the first state push;
- both target notifications happen after observation persistence;
- notification state is pushed before the final combined failure step;
- no matrix or parallel state writer exists;
- only `contents: write` is granted;
- failure artifact includes only the two JSON summaries.

- [ ] **Step 2: Run workflow tests and verify RED**

Run: `python -m unittest tests.test_workflow -v`

Expected: missing second target/state path and wrong failure ordering.

- [ ] **Step 3: Extend the single sequential workflow**

Use one state worktree and create the RR directory:

```bash
mkdir -p "$STATE_DIR/range-rover-d350" "$SUMMARY_DIR"
touch "$STATE_DIR/history.jsonl" "$STATE_DIR/range-rover-d350/history.jsonl"
```

In one collection step, run Teramont first and Range Rover second with `set +e`,
capture `PIPESTATUS[0]` for each, and write both values to `$GITHUB_OUTPUT`. Persist
the complete state worktree once after both scans. Notify both directories
sequentially in one step using the existing two secret environment variables.
Persist delivery state once. Move the combined failure check to the end:

```bash
if [ "${{ steps.collect.outputs.teramont_status }}" != "0" ] || \
   [ "${{ steps.collect.outputs.range_rover_status }}" != "0" ]; then
  exit 2
fi
```

This ordering sends already-persisted events from a healthy target even when the
other target has only source gaps. Keep `cancel-in-progress: false`; rename the
workflow/concurrency/artifact labels to generic vehicle-monitor names.

- [ ] **Step 4: Rewrite README around the two-target contract**

Document exactly:

- both vehicle criteria and the two 1,000 km limits;
- RR SWB/LWB acceptance, L460-only exclusions, factory two-screen RSE, and Europe LHD;
- nine RR sources grouped by Russia/Kyrgyzstan/Georgia/Europe;
- original-currency storage and all five price thresholds (Teramont RUB 50,000 plus four RR thresholds);
- candidate/unknown/source-gap behavior;
- root Teramont state versus `range-rover-d350/` state;
- the same two GitHub Secrets and no additional secret;
- hourly run at minute 17 UTC and GitHub best-effort scheduling;
- exact local test and dry-run commands:

```powershell
$env:PYTHONPATH = "src"
python -m unittest discover -s tests -t . -v
python -m teramont_monitor smoke --config config/sources.json --target config/targets/teramont-pro-2026.json --dry-run
python -m teramont_monitor smoke --config config/range-rover-sources.json --target config/targets/range-rover-l460-d350-autobiography-2026.json --dry-run
```

State that smoke is read-only, a local commit does not activate scheduling, and
blocked live sources are reported as `source_gap`.

- [ ] **Step 5: Run workflow tests and full suite**

Run:

```powershell
python -m unittest tests.test_workflow -v
python -m unittest discover -s tests -t . -v
git diff --check
```

Expected: all tests pass and `git diff --check` prints nothing.

- [ ] **Step 6: Commit workflow and documentation**

```powershell
git add -- .github/workflows/monitor.yml tests/test_workflow.py README.md
git commit -m "feat: schedule Teramont and Range Rover monitors"
```

---

### Task 8: Perform final regression, security review, and live read-only smoke

**Files:**

- Review: all files listed in the file map.
- Modify only if a verification failure identifies a requirement-scoped defect.

**Interfaces:**

- Verifies the complete spec and the production activation boundary.

- [ ] **Step 1: Run the complete deterministic test suite from a clean process**

Run:

```powershell
$env:PYTHONPATH = "src"
python -m unittest discover -s tests -t . -v
```

Expected: every test passes with zero failures/errors.

- [ ] **Step 2: Run both read-only live smokes**

Run:

```powershell
python -m teramont_monitor smoke --config config/sources.json --target config/targets/teramont-pro-2026.json --dry-run
python -m teramont_monitor smoke --config config/range-rover-sources.json --target config/targets/range-rover-l460-d350-autobiography-2026.json --dry-run
```

Expected: each source reports either an honest successful result or a named
`source_gap`; neither command writes state or sends Telegram. Live source gaps do
not fail deterministic acceptance if the gap is explicit and sanitized.

- [ ] **Step 3: Verify secrets and sensitive-data exclusions**

Run:

```powershell
rg -n "TELEGRAM_BOT_TOKEN|TELEGRAM_CHAT_ID" .github README.md src tests config
rg -n "bot[0-9]{6,}:|@[A-Za-z0-9_]{5,}|\+?[0-9][0-9 ()-]{8,}" src tests config .github
```

Expected: only environment/secret names and synthetic test values appear; no
real token, chat ID, phone, email, seller contact, or raw listing page is stored.

- [ ] **Step 4: Verify backward compatibility and exact scope**

Run:

```powershell
git diff origin/main --stat
git diff origin/main --check
git status --short --branch
```

Confirm manually from the diff that the legacy root state paths remain
`state.json`, `history.jsonl`, and `pending-events.json`; only RR uses
`range-rover-d350/`; only five event kinds exist; no unrelated refactor or
dependency was added.

- [ ] **Step 5: Commit any scoped verification fix and re-run Step 1**

If verification required a code correction, stage only the affected task files,
commit with `fix: <specific verified defect>`, and repeat the full deterministic
suite. If no correction was required, create no empty commit.

- [ ] **Step 6: Prepare the completion handoff without pushing**

Report changed files, test count/result, per-source live smoke outcomes,
Security/Risk Review, commit list, and remaining `source_gap` items. Do not push,
open a PR, merge, or activate the workflow until the user gives a separate
explicit command.

---

## Plan self-review

- **Spec coverage:** Tasks 1–7 cover every acceptance criterion: profiles,
  normalization, exact RR qualification, source markets, currency thresholds,
  identity/events, legacy state, Telegram, workflow, and README. Task 8 covers
  regression, live source gaps, security, and activation boundaries.
- **Alternatives:** The plan implements the selected shared-engine/profile design;
  it does not duplicate the package or add hard-coded RR branches to events.
- **Edge cases:** Explicit negatives, LHD unknown/RHD, 1,000/1,001 km, currency
  changes, source failures, partial scans, duplicate retries, old state, and
  concurrent state pushes have tests or workflow contracts.
- **Type consistency:** `TargetProfile` is created in Task 1, passed to
  `normalize_listing`, `scan_all`, `qualify`, and `apply_scan`, and loaded once by
  `collect`/`smoke`. `Listing` carries target evidence into events and Telegram.
- **No speculative scope:** The existing package name, transport, storage format,
  HTML extractor, event taxonomy, secrets, and dependencies are retained.
