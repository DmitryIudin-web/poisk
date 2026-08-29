# Multi-Vehicle Monitor and Range Rover D350 — Design

Date: 2026-08-28
Status: user-approved design, pending written-spec review

## Problem

The repository currently monitors one hard-coded target: Volkswagen Teramont Pro
2026. Add a second target without copying the scanner, event engine, state
handling, workflow, or Telegram integration:

- full-size Range Rover L460 D350 Autobiography;
- model year 2026;
- black exterior and black interior;
- factory Rear Seat Entertainment with two passenger screens;
- mileage from 0 through 1,000 km inclusive;
- physically in stock;
- located in Russia, Kyrgyzstan, Georgia, or Europe;
- left-hand drive when the listing is in Europe.

The existing Teramont monitor must continue using its current requirements,
including the 1,000 km mileage ceiling, without resetting its history or sending
old offers again.

## Goals

- Replace vehicle-specific qualification wiring with a small target-profile
  boundary shared by Teramont and Range Rover.
- Keep discovery, normalization, history, event generation, and Telegram
  delivery common to both targets.
- Search Russia, Kyrgyzstan, Georgia, and Europe independently for Range Rover.
- Retain original currencies and apply user-approved price-drop thresholds per
  currency.
- Preserve explicit `unknown` evidence and source-specific `source_gap` records.
- Add no new credentials and expose no Telegram secret in code, configuration,
  logs, state, or documentation.

## Non-goals

- Bypassing CAPTCHAs, login requirements, rate limits, robots restrictions, or
  marketplace access controls.
- Contacting sellers, reserving a vehicle, negotiating, or purchasing.
- Treating Autobiography trim as proof of Rear Seat Entertainment.
- Inferring two rear screens from generic phrases such as “full options”.
- Converting EUR, GEL, KGS, or other currencies to an alleged all-inclusive
  Russian price.
- Treating a listing date, registration date, or seller claim as a verified VIN,
  EPTS, or recycling-fee fact when the source does not explicitly state it.
- Reorganizing unrelated package boundaries or rewriting existing adapters.

## Alternatives considered

### 1. Shared engine with target profiles — selected

Keep one source scanner, canonical listing model, event engine, state store,
workflow, and Telegram notifier. Put vehicle-specific model, trim, equipment,
market, and price-threshold rules behind target profiles.

Advantages: no duplicated operational logic, consistent failure handling, easy
regression testing, and a clear route for future vehicle targets. Cost: a small
backward-compatible generalization of current vehicle-specific functions.

### 2. Copy the Teramont package for Range Rover

Advantages: low initial design effort. Disadvantages: duplicate fixes, duplicate
workflow/state behavior, divergent alert semantics, and twice the maintenance.
Rejected because it conflicts with the requirement not to duplicate existing
functionality.

### 3. Add Range Rover branches inside existing hard-coded functions

Advantages: fewer new files initially. Disadvantages: increasingly fragile
conditionals, difficult fixtures, and vehicle rules leaking into shared parsing.
Rejected because it is less maintainable than a narrow profile boundary.

## Target profiles

Introduce an immutable `TargetProfile` loaded from checked configuration and
validated before scanning. It contains only target-specific policy:

- stable `target_id` and display name;
- positive and negative model evidence;
- year and mileage limits;
- exterior/interior color requirements;
- trim evidence;
- required-equipment positive and negative evidence;
- region and steering-side policy;
- price-drop thresholds by currency.

The shared code remains responsible for requests, extraction, tri-state evidence,
identity, state comparison, history, events, and delivery.

The two initial profile IDs are:

- `teramont-pro-2026`;
- `range-rover-l460-d350-autobiography-2026`.

Profile validation fails before network access for an unknown currency, empty
identity rule, contradictory positive/negative pattern, or invalid mileage/price
threshold.

## Range Rover qualification

A Range Rover listing is `relevant` only when every hard requirement is
positively supported by the current listing data.

### Exact vehicle

- Accept the full-size Range Rover L460 in SWB or LWB form.
- Require `D350` or unambiguous equivalent evidence: 3.0 diesel and approximately
  257–258 kW / 349–351 hp.
- Require `Autobiography` trim.
- Reject Range Rover Sport, Velar, Evoque, and other Land Rover models even if
  `D350` and `Autobiography` appear in their text.

The engine equivalence rule requires the fuel, displacement, and power evidence
together; a bare “3.0 diesel” is not sufficient.

### Year, colors, mileage, and availability

- Require model year 2026. A first-registration value alone is retained but does
  not replace explicit model-year evidence when the source distinguishes them.
- Require black exterior and black interior from structured fields or explicit
  text. Mixed interior descriptions qualify only when black is the primary cabin
  color and the source does not describe a conflicting primary color.
- Require mileage between 0 and 1,000 km inclusive.
- Require physical stock at the seller or named sale location. “In transit”,
  “on order”, “available to order”, “arriving”, and equivalent phrases do not
  qualify.

### Factory passenger screens

Require explicit factory Rear Seat Entertainment evidence describing two rear
passenger displays. Positive evidence may include structured equipment or clear
market-language terms such as:

- `Rear Seat Entertainment`, `RSE`, or `two rear screens`;
- `Fond-TV`, `Fond Entertainment`, or an equivalent German description;
- Russian phrases explicitly naming two rear passenger monitors;
- an official option/equipment label that maps unambiguously to the factory
  two-screen system.

Reject explicit negatives including `No Rear Seat Entertainment`,
`Entfall Multimediasystem im Fond`, aftermarket tablets, removable headrest
screens, or text stating that rear multimedia was deleted. Do not require an
exact screen size because official descriptions vary by market/model-year; the
factory two-screen system is the requirement.

### Steering side

Listings classified as `europe` require positive left-hand-drive evidence from a
structured field, VIN/equipment decode supplied by the source, or explicit text.
Right-hand drive is rejected. Unknown steering side remains a candidate and
cannot produce a relevant alert. This additional steering requirement applies to
Europe only.

### Candidate behavior

A plausible offer with one or more unconfirmed hard requirements is stored as a
`candidate`. Missing evidence is `unknown`, never `false`. A candidate cannot
produce `new_relevant`; it may produce `critical_confirmation` only when new
evidence makes it fully relevant or confirms one of the separately tracked
commercial fields under the existing event policy.

## Markets and sources

Range Rover sources are grouped by the vehicle's physical location, not the
seller's corporate address:

- `russia`: Auto.ru, Drom, and Avito;
- `kyrgyzstan`: Mashina.kg;
- `georgia`: MyAuto, the public Jaguar Land Rover Georgia stock, and AutoBridge;
- `europe`: mobile.de and AutoScout24;
- `unknown`: location cannot be established and the listing remains a candidate.

Each configured source is attempted independently. A blocked page, CAPTCHA,
403/429, malformed response, unexpected empty page, or detail-page exhaustion is
recorded as a source-specific `source_gap`. It is never reported as zero market
inventory and never increments removal counters.

Source adapters may add conservative URL patterns and market-language keywords,
but must reuse the existing shared JSON-LD/HTML extraction path wherever its
contract is sufficient. New custom parsing is limited to evidence that the
shared extractor cannot represent and is covered by a minimized fixture.

## Price and commercial evidence

Store amounts in the source currency. Add first-class `EUR` and `GEL` support
while preserving current `RUB`, `KGS`, and `KZT` behavior.

Range Rover price-drop alerts use the approved thresholds:

| Currency | Minimum drop |
|---|---:|
| RUB | 100,000 RUB |
| EUR | 1,000 EUR |
| GEL | 3,000 GEL |
| KGS | 100,000 KGS |

An unsupported currency is retained in observations but produces no price-drop
event until a threshold is explicitly configured. Cross-currency comparisons are
never performed.

`cash_price` continues to mean a price available without credit, trade-in,
insurance, financing, or other conditional discount. A conditional amount is
stored separately with its qualifier. For Russian listings, VIN, EPTS, and
commercial recycling-fee status are captured only from explicit source evidence.

## Identity and deduplication

Add `target_id` to the canonical observation and event identity. Listing identity
within a target remains:

1. normalized VIN when present for vehicle grouping;
2. marketplace plus marketplace listing ID for the offer key;
3. canonical listing URL hash as fallback.

The same URL cannot collide across targets. Multiple offers sharing a VIN remain
separate seller offers under a shared vehicle identity, preserving price and
location differences. A target transition from candidate to relevant creates one
stable event even when another source has already observed the same vehicle; the
existing cross-seller policy remains otherwise unchanged.

## Meaningful events

Telegram remains limited to the five approved categories:

- `new_relevant`;
- `price_drop` using the target/currency threshold;
- `became_available`;
- `critical_confirmation`;
- `removed_or_sold`.

Every message includes the target name, market, source currency, source URL, and
stable event ID. It includes VIN, EPTS, recycling-fee status, steering side, and
Rear Seat Entertainment evidence only when known.

A listing is removed/sold only after an explicit source status or absence from
two consecutive successful complete scans of that source. Partial or failed
scans do not count. Reappearance, price increases, unchanged observations, and
candidate-only discovery remain in history without Telegram delivery.

## Durable state and backward compatibility

Keep the existing `monitor-state` branch and concurrency group. Do not migrate or
rename the existing Teramont root files during this feature:

- root `state.json`, `history.jsonl`, and `pending-events.json` remain the legacy
  Teramont state paths;
- Range Rover state is stored under `range-rover-d350/` using the same three-file
  contract.

This asymmetric layout is intentional: it avoids resetting the live Teramont
monitor and prevents replaying old offers. Shared state APIs accept an explicit
target state directory, while the Teramont invocation continues to default to the
legacy root.

Each target commits its state only after a valid scan result. Pending Telegram
events remain retryable. No secrets, raw HTML, seller phone numbers, email
addresses, or contact details are stored.

## Workflow

Extend the existing hourly workflow instead of adding a second scheduler. The
single serialized job runs both targets sequentially with target-specific state
directories. Sequential execution avoids concurrent pushes to `monitor-state`.

Failure isolation rules:

- a Range Rover source failure does not discard Teramont results;
- a Teramont failure does not discard Range Rover results;
- each target persists its source gaps and completed observations independently;
- the job reports failure after persistence when a target has no successful
  source, without manufacturing market events.

Telegram continues to read only `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` from
GitHub Secrets/environment variables. No new secrets or GitHub permission changes
are required.

## Implementation boundaries

The minimal implementation should:

1. add a profile model/loader and make qualification profile-driven;
2. add `target_id`, market, steering-side, and rear-entertainment evidence to the
   canonical representation without breaking old serialized Teramont state;
3. add the Range Rover profile and source configuration;
4. add EUR/GEL parsing and per-profile currency thresholds;
5. parameterize CLI state paths and message target labels;
6. run both profiles sequentially in the existing workflow;
7. update README and tests.

Do not rename the Python package solely for aesthetics. Existing imports and CLI
commands may remain as compatibility entry points while target-neutral functions
are introduced behind them.

## Error handling and edge cases

- Empty or null evidence remains `unknown`.
- Very long listing text is bounded by existing extraction limits before pattern
  matching.
- Model negative patterns are evaluated before positive equivalence rules.
- A power value without diesel/displacement evidence cannot prove D350.
- `1,000 km` qualifies; `1,001 km` does not.
- A zero-kilometre car may still fail physical-stock evidence.
- Autobiography without factory rear-screen evidence remains a candidate.
- A two-screen aftermarket system is rejected when aftermarket evidence exists;
  otherwise ambiguous screens remain unknown.
- A European listing without steering-side evidence remains a candidate even if
  every other field matches.
- Currency changes on the same offer do not generate a cross-currency drop.
- Duplicate runs, Telegram retries, and a push retry reuse stable event IDs.
- A target with zero configured sources fails validation.
- One target cannot write or consume another target's pending events.
- A source returning only previously known cards is still a successful scan when
  its completeness contract is satisfied.

## Testing

Tests remain offline and use synthetic/minimized fixtures. Add coverage for:

- target-profile loading and invalid-profile rejection;
- unchanged Teramont qualification and the exact 1,000 km boundary;
- Range Rover L460 positive cases in Russian, English, and German;
- rejection of Sport, Velar, Evoque, wrong engine, wrong year, wrong colors,
  mileage above 1,000 km, and non-stock status;
- positive factory RSE and explicit factory-option labels;
- negative/deleted RSE and aftermarket-tablet cases;
- European LHD, RHD, and unknown steering-side behavior;
- RUB/EUR/GEL/KGS thresholds immediately below, at, and above each boundary;
- no cross-currency price comparison;
- target-aware identity, deduplication, and event formatting;
- legacy Teramont root-state read/write compatibility;
- Range Rover subdirectory isolation;
- two-successful-scan removal behavior under partial/failed source scans;
- source adapters using representative, sanitized fixtures;
- workflow/config validation and secret-name checks.

Run the complete existing test suite plus the new tests. A manual live smoke may
report source gaps but must not write state or send Telegram.

## Acceptance criteria

1. Teramont continues to qualify the same listings and reuse its existing state.
2. Range Rover accepts only fully confirmed L460 D350 Autobiography 2026 matches
   with black exterior/interior, factory two-screen RSE, mileage at most 1,000 km,
   and physical stock.
3. European matches additionally require confirmed LHD.
4. Russia, Kyrgyzstan, Georgia, and Europe are stored and reported separately.
5. All configured sources fail independently and unavailable sources produce
   `source_gap`, not a false zero result or false removal.
6. VIN, unconditional price, EPTS, and commercial recycling-fee evidence are
   retained only when explicitly present.
7. Price alerts use the approved per-currency thresholds without conversion.
8. Duplicate observations and retries do not create duplicate events.
9. Telegram sends only the five approved event categories and identifies the
   monitored vehicle and market.
10. GitHub Actions runs both targets hourly through the existing serialized
    workflow and reads Telegram credentials only from the two existing Secrets.
11. README contains exact setup, criteria, market, state-layout, dry-run, and
    troubleshooting instructions.
12. The full automated test suite passes before implementation is reported
    complete.

## Security and risk review

- No credential, cookie, raw page, contact detail, or personal data is committed.
- Public pages are read conservatively; access controls are not bypassed.
- Listing claims remain source claims and are not represented as independently
  verified facts.
- Marketplace markup can change. Parser failures must surface as `source_gap`.
- The broadest regression risk is resetting legacy state; the unchanged root
  layout and compatibility tests are mandatory controls.
- The broadest operational risk is concurrent state pushes; one sequential,
  serialized workflow is the control.

## Rollback

Revert the implementation commit(s) and disable the extended workflow if needed.
The legacy Teramont root state remains readable by the pre-feature code. The
`range-rover-d350/` state directory may be left intact for audit/history; deleting
it is a separate destructive action and is not part of rollback.

## Recommendation

Implement the shared-engine/profile approach with the deliberately asymmetric
state layout. It directly satisfies the second-vehicle requirement, avoids
duplicating operational logic, and protects the already-running Teramont history.
