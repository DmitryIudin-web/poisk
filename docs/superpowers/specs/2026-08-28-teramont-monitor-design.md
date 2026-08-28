# Volkswagen Teramont Pro Monitor — Design

Date: 2026-08-28

## Problem

The repository is empty. Build a small, maintainable monitor that checks public
vehicle marketplaces every hour for Volkswagen Teramont Pro 2026 offers matching
the user's requirements, keeps durable state and history, and sends Telegram
notifications only for meaningful changes.

## Goals

- Discover offers from Auto.ru, Drom, Avito, Mashina.kg, and Kolesa.kz.
- Separate Russia from Bishkek and other EAEU markets in stored data and alerts.
- Track VIN, unconditional cash price, EPTS, and commercial recycling-fee status
  when a source explicitly provides them.
- Avoid treating missing evidence as a negative fact.
- Survive stateless GitHub Actions runners without an external database.
- Require only Telegram credentials as user-managed secrets.

## Non-goals

- Bypassing CAPTCHAs, authentication, rate limits, or marketplace access controls.
- Contacting sellers, reserving cars, or making purchases.
- Converting non-RUB prices into an alleged all-inclusive Russia price.
- Inferring VIN, EPTS, recycling-fee status, or unconditional price from unrelated
  fields.
- Guaranteed execution at an exact minute; GitHub Actions scheduling is best
  effort.

## Listing qualification

A listing is `relevant` only when all hard requirements are positively supported
by the current source data:

1. model is Volkswagen Teramont Pro;
2. model year is 2026;
3. exterior is black;
4. interior is black;
5. trim is Peak, Summit, or explicitly described as an equivalent top trim;
6. DCC/adaptive chassis control is explicitly present;
7. mileage is between 0 and 1,000 km inclusive;
8. the vehicle is physically in stock, not merely ordered, in transit, or due to
   arrive.

An offer that plausibly matches but has one or more unconfirmed hard requirements
is stored as a `candidate`. It cannot produce a `new_relevant` alert. Missing data
is stored as `unknown`, never converted to `false`.

An equivalent trim requires both an explicit top/maximal-trim statement and DCC.
Marketing text containing only “maximum” is not enough to prove DCC.

## Regions and commercial fields

- `russia`: vehicle physically located in Russia.
- `bishkek`: vehicle physically located in Bishkek.
- `eaeu_other`: another EAEU location.
- `unknown`: location cannot be established.

For Russian listings, `epts_status` and `commercial_recycling_fee_status` are
captured only from explicit source statements. For all regions, `cash_price`
means the price available without credit, trade-in, insurance, or other
conditional discounts. If only a conditional price is shown, `cash_price` is
unknown and the advertised conditional amount is retained separately with its
qualifier. Non-RUB amounts retain their original currency and do not trigger the
50,000 RUB threshold unless a source explicitly supplies a RUB cash price.

## Identity and deduplication

Stable identity is selected in this order:

1. normalized VIN, when present;
2. marketplace plus marketplace listing ID;
3. canonical listing URL hash.

Multiple pages with the same VIN are retained as separate offers under a shared
vehicle key so seller, region, and price differences are not lost.

## Meaningful events

Telegram notifications are limited to:

- `new_relevant`: a listing first becomes fully relevant;
- `price_drop`: RUB cash price decreases by at least 50,000 RUB;
- `became_available`: a tracked listing changes from unavailable/in transit to
  physically in stock and is otherwise relevant;
- `critical_confirmation`: a previously unknown hard requirement, VIN, EPTS, or
  commercial recycling-fee field becomes explicitly confirmed; hard-requirement
  confirmation is alerted when it makes the listing relevant;
- `removed_or_sold`: a previously relevant listing is explicitly marked sold or
  removed, or is absent from two consecutive successful scans of its source.

A failed or blocked source scan never increments the absence counter. Reappearing
unchanged listings and price increases produce no Telegram alert, but remain in
history.

## Architecture

The implementation is a Python 3.12 package with five small boundaries:

1. `sources`: source-specific discovery plus shared JSON-LD/HTML extraction;
2. `normalize`: canonical fields and three-state evidence values;
3. `qualify`: deterministic candidate/relevant classification;
4. `events`: state comparison, absence confirmation, and event generation;
5. `notify`: Telegram formatting and delivery using environment variables.

Source search URLs, conservative request settings, and keywords live in a checked
configuration file. Parsers never execute page scripts. A source response that is
blocked, structurally invalid, or unexpectedly empty is reported as `source_gap`.

## Durable state

GitHub Actions checks out a dedicated orphan branch named `monitor-state` into a
separate directory. The branch contains:

- `state.json`: latest normalized offers and consecutive-miss counters;
- `history.jsonl`: append-only observed changes and source gaps;
- `pending-events.json`: events awaiting successful Telegram delivery.

The workflow serializes runs with a concurrency group. It commits pending state
before Telegram delivery and marks events delivered afterward. A delivery that
succeeds immediately before a Git push failure may be retried; every alert
therefore includes a stable event ID so duplicates are recognizable.

No secrets, cookies, raw HTML, phone numbers, or seller contact details are stored.

## GitHub Actions

The workflow:

- supports `workflow_dispatch`;
- runs hourly at minute 17 UTC to reduce top-of-hour congestion;
- grants only `contents: write` to persist the state branch;
- installs locked Python dependencies;
- runs the monitor with Telegram secrets supplied only as environment variables;
- uploads a short diagnostic artifact on failure without secrets or raw pages.

The first run creates `monitor-state`. The default branch contains code and tests
only; automated state commits never modify it.

## Error handling

- Each source is isolated: one source failure does not discard successful results
  from others.
- HTTP timeouts, 403/429 responses, CAPTCHAs, malformed pages, and unexpected zero
  discovery become source-specific gaps.
- A run with no successful sources fails and sends no market event.
- Telegram errors leave events pending for a later retry.
- Parsing errors retain enough sanitized metadata for diagnosis without storing
  page content.

## Testing

Tests use saved synthetic/minimized fixtures, never live marketplaces. Coverage
includes:

- normalization and three-state evidence;
- all hard qualification requirements;
- VIN/listing-ID/URL identity and cross-seller VIN grouping;
- unconditional versus conditional prices;
- Russia/Bishkek/other EAEU separation;
- every meaningful event and the 50,000 RUB boundary;
- two-successful-scan removal rule and blocked-source protection;
- Telegram formatting without secret leakage;
- source adapters against representative JSON-LD/HTML fixtures;
- workflow/config validation.

A separate manual smoke command checks live sources and reports `source_gap`
without changing the committed state.

## Acceptance criteria

1. A clean checkout can install dependencies, run tests, and execute a dry run.
2. All five configured sources are attempted independently.
3. Only positively confirmed matches become relevant.
4. State survives across scheduled GitHub Actions runs.
5. Duplicate observations do not create duplicate events.
6. Only the five approved event categories reach Telegram.
7. Telegram credentials appear only as GitHub Secrets/environment variables.
8. README documents setup, permissions, first run, dry run, state branch,
   troubleshooting, and exact secret names.
9. No seller contact action, authentication bypass, push, or deployment is part of
   the implementation task.

## Risks and rollback

Marketplace markup and access policies can change without notice. The monitor
must fail visibly per source instead of claiming zero offers. A parser update is
isolated to its source adapter and fixture. Rollback consists of disabling the
workflow and reverting the code commit; deleting `monitor-state` is optional and
must be a separate deliberate action because it removes history.
