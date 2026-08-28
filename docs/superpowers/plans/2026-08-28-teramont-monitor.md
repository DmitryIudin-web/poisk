# Volkswagen Teramont Pro Monitor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a dependency-free Python monitor that checks five public marketplaces hourly, classifies evidence conservatively, persists durable history, and sends only approved Telegram events.

**Architecture:** Source adapters discover marketplace listing URLs and feed detail-page text into a deterministic normalizer. Pure qualification and event functions compare each successful scan with JSON state; a two-stage GitHub Actions flow persists pending events on a dedicated branch before delivering them. Source failures are isolated and recorded as `source_gap`.

**Tech Stack:** Python 3.12 standard library, `unittest`, GitHub Actions, JSON/JSONL, Telegram Bot HTTP API.

**Spec:** `docs/superpowers/specs/2026-08-28-teramont-monitor-design.md`

## Global Constraints

- Do not bypass CAPTCHAs, authentication, rate limits, or marketplace access controls.
- Missing evidence is `unknown`, never `false`.
- A listing is relevant only when all eight hard requirements are positively confirmed.
- Only `new_relevant`, `price_drop`, `became_available`, `critical_confirmation`, and `removed_or_sold` may reach Telegram.
- A listing absent from a failed source scan is not missing; absence requires two successful scans.
- Runtime dependencies are limited to the Python 3.12 standard library.
- Telegram credentials are read only from `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`.
- No secrets, cookies, raw HTML, phone numbers, or seller contact details are stored.

## File map

- `pyproject.toml`: package metadata, Python floor, and unittest-friendly layout.
- `config/sources.json`: five source definitions, URL patterns, request pacing, and location defaults.
- `src/teramont_monitor/models.py`: evidence, listing, scan, event, and state dataclasses.
- `src/teramont_monitor/normalize.py`: deterministic extraction and region/price normalization.
- `src/teramont_monitor/qualify.py`: hard-requirement evaluation.
- `src/teramont_monitor/identity.py`: VIN/listing-ID/URL identity.
- `src/teramont_monitor/events.py`: state transition and event rules.
- `src/teramont_monitor/storage.py`: atomic JSON and append-only JSONL persistence.
- `src/teramont_monitor/html.py`: standard-library HTML link/text/JSON-LD extraction.
- `src/teramont_monitor/sources.py`: source configuration, polite HTTP fetch, source adapters, and source gaps.
- `src/teramont_monitor/telegram.py`: safe event rendering and Telegram delivery.
- `src/teramont_monitor/cli.py`: `collect`, `notify`, and `smoke` orchestration.
- `src/teramont_monitor/__main__.py`: module entry point.
- `.github/workflows/monitor.yml`: tests, hourly collection, state-branch commits, and delivery.
- `tests/__init__.py`: test package bootstrap for the `src` layout.
- `tests/`: focused unit, adapter, workflow, and integration tests with minimized fixtures.
- `README.md`: exact local and GitHub setup, operations, and troubleshooting.

---

### Task 1: Domain model, normalization, and strict qualification

**Files:**
- Create: `pyproject.toml`
- Create: `src/teramont_monitor/__init__.py`
- Create: `src/teramont_monitor/models.py`
- Create: `src/teramont_monitor/normalize.py`
- Create: `src/teramont_monitor/qualify.py`
- Create: `tests/__init__.py`
- Test: `tests/test_normalize.py`
- Test: `tests/test_qualify.py`

**Interfaces:**
- Produces: `Evidence(value: bool | None, source_text: str | None)`, `Listing`, `normalize_listing(source, url, listing_id, text, metadata) -> Listing`, and `qualify(listing) -> tuple[str, tuple[str, ...]]`.

- [ ] **Step 1: Write failing normalization tests**

```python
def test_normalizes_explicit_black_black_dcc_stock_and_cash_price():
    listing = normalize_listing(
        "drom", "https://auto.drom.ru/1.html", "1",
        "Volkswagen Teramont Pro 2026 Peak. Цвет кузова: черный. "
        "Цвет салона: черный. DCC. Пробег 23 км. В наличии. "
        "Цена за наличные 5 999 000 ₽. VIN: WVGZZZCA1RC123456. "
        "ЭПТС действующий. Коммерческий утильсбор уплачен.", {},
    )
    assert listing.cash_price == 5_999_000
    assert listing.exterior_black.value is True
    assert listing.interior_black.value is True
    assert listing.dcc.value is True
    assert listing.in_stock.value is True
```

- [ ] **Step 2: Run normalization tests and verify missing-module failure**

Run: `python -m unittest tests.test_normalize -v`
Expected: FAIL because `teramont_monitor` does not exist.

- [ ] **Step 3: Implement dataclasses and conservative extraction**

Implement three-state `Evidence`, JSON-safe dataclasses, explicit label-based black exterior/interior patterns, DCC markers, mileage/year/VIN extraction, unconditional versus conditional RUB price fields, EPTS/recycling-fee evidence, and region mapping (`russia`, `bishkek`, `eaeu_other`, `unknown`).

- [ ] **Step 4: Run normalization tests**

Run: `python -m unittest tests.test_normalize -v`
Expected: PASS.

- [ ] **Step 5: Write failing qualification tests**

```python
def test_unknown_dcc_keeps_offer_candidate():
    listing = matching_listing(dcc=Evidence(None, None))
    status, missing = qualify(listing)
    assert status == "candidate"
    assert "dcc" in missing

def test_all_eight_requirements_make_offer_relevant():
    assert qualify(matching_listing()) == ("relevant", ())
```

- [ ] **Step 6: Run qualification tests and verify expected failure**

Run: `python -m unittest tests.test_qualify -v`
Expected: FAIL because `qualify` is absent.

- [ ] **Step 7: Implement the eight-requirement qualification table**

The function checks model, year, exterior, interior, trim/equivalent top trim, DCC, mileage, and physical stock. It returns every missing/negative requirement without inventing evidence.

- [ ] **Step 8: Run Task 1 tests and commit**

Run: `python -m unittest tests.test_normalize tests.test_qualify -v`
Expected: PASS.

Commit: `git add pyproject.toml src/teramont_monitor tests/__init__.py tests/test_normalize.py tests/test_qualify.py && git commit -m "feat: add strict Teramont qualification"`

---

### Task 2: Identity, state transitions, and durable storage

**Files:**
- Create: `src/teramont_monitor/identity.py`
- Create: `src/teramont_monitor/events.py`
- Create: `src/teramont_monitor/storage.py`
- Test: `tests/test_identity.py`
- Test: `tests/test_events.py`
- Test: `tests/test_storage.py`

**Interfaces:**
- Consumes: `Listing`, `Evidence`, and `qualify` from Task 1.
- Produces: `listing_key(listing) -> str`, `vehicle_key(listing) -> str`, `apply_scan(state, source_results, observed_at) -> tuple[MonitorState, list[Event], list[dict]]`, `load_state(path)`, `save_state(path, state)`, and `append_history(path, records)`.

- [ ] **Step 1: Write and fail identity tests**

```python
def test_listing_identity_prefers_source_listing_id_but_vehicle_groups_by_vin():
    a = listing(source="drom", listing_id="1", vin="WVGZZZCA1RC123456")
    b = listing(source="autoru", listing_id="2", vin="WVGZZZCA1RC123456")
    assert listing_key(a) != listing_key(b)
    assert vehicle_key(a) == vehicle_key(b)
```

Run: `python -m unittest tests.test_identity -v`
Expected: FAIL because identity functions are absent.

- [ ] **Step 2: Implement stable identity and pass identity tests**

Normalize VIN to uppercase; use `source:id`, then canonical URL SHA-256 for listing identity; use VIN for cross-seller vehicle grouping and fall back to listing identity.

Run: `python -m unittest tests.test_identity -v`
Expected: PASS.

- [ ] **Step 3: Write and fail event transition tests**

```python
def test_price_drop_boundary_is_inclusive():
    old = state_with(relevant_listing(cash_price=6_000_000))
    new = scan_with(relevant_listing(cash_price=5_950_000))
    _, events, _ = apply_scan(old, new, "2026-08-28T12:00:00Z")
    assert [event.kind for event in events] == ["price_drop"]

def test_failed_source_does_not_increment_misses():
    old = state_with(relevant_listing(), misses=1)
    next_state, events, _ = apply_scan(old, failed_scan("drom"), NOW)
    assert next_state.listings[KEY].misses == 1
    assert events == []
```

Also cover new relevant, became available, critical confirmation, explicit sold, two successful absences, reappearance, price increase, and unchanged deduplication.

Run: `python -m unittest tests.test_events -v`
Expected: FAIL because transition logic is absent.

- [ ] **Step 4: Implement minimal transition engine and event IDs**

Event IDs are SHA-256 hashes of kind, listing key, and new evidence value. Previously seen or already pending IDs are suppressed. Source gaps generate history records, not Telegram events.

- [ ] **Step 5: Write and fail storage tests**

```python
def test_save_state_is_atomic_and_round_trips():
    with TemporaryDirectory() as directory:
        path = Path(directory) / "state.json"
        save_state(path, sample_state())
        assert load_state(path) == sample_state()
        assert not path.with_suffix(".json.tmp").exists()
```

Run: `python -m unittest tests.test_storage -v`
Expected: FAIL because storage functions are absent.

- [ ] **Step 6: Implement atomic JSON and append-only JSONL**

Write UTF-8 JSON to a sibling temporary file and replace atomically. Append one compact JSON object per history line. Never serialize raw HTML or credentials.

- [ ] **Step 7: Run Task 2 tests and commit**

Run: `python -m unittest tests.test_identity tests.test_events tests.test_storage -v`
Expected: PASS.

Commit: `git add src/teramont_monitor/identity.py src/teramont_monitor/events.py src/teramont_monitor/storage.py tests/test_identity.py tests/test_events.py tests/test_storage.py && git commit -m "feat: add monitor state and event history"`

---

### Task 3: Five isolated marketplace sources

**Files:**
- Create: `config/sources.json`
- Create: `src/teramont_monitor/html.py`
- Create: `src/teramont_monitor/sources.py`
- Create: `tests/fixtures/autoru.html`
- Create: `tests/fixtures/drom.html`
- Create: `tests/fixtures/avito.html`
- Create: `tests/fixtures/mashina.html`
- Create: `tests/fixtures/kolesa.html`
- Test: `tests/test_html.py`
- Test: `tests/test_sources.py`

**Interfaces:**
- Consumes: `normalize_listing` from Task 1.
- Produces: `SourceConfig`, `SourceResult(source, ok, listings, gap)`, `extract_links(html, config)`, `extract_detail_text(html)`, `scan_source(config, fetcher)`, and `scan_all(configs, fetcher)`.

- [ ] **Step 1: Write and fail HTML extraction tests**

```python
def test_extracts_only_allowed_listing_links_and_canonicalizes_them():
    links = extract_links(fixture("drom.html"), drom_config())
    assert links == [("460325385", "https://auto.drom.ru/moscow/volkswagen/teramont/460325385.html")]
```

Fixtures are minimized hand-authored fragments containing stable attributes or URL shapes, not copied full marketplace pages.

Run: `python -m unittest tests.test_html -v`
Expected: FAIL because HTML extraction is absent.

- [ ] **Step 2: Implement safe HTML parsing**

Use `html.parser.HTMLParser` to collect anchor URLs/text, meta descriptions, visible text, and `application/ld+json`. Reject non-HTTP URLs and hosts outside each source allowlist. Remove fragments and tracking query parameters.

- [ ] **Step 3: Write and fail source adapter tests**

```python
def test_blocked_source_returns_gap_not_empty_market():
    result = scan_source(drom_config(), fake_fetch("<title>Captcha</title>"))
    assert result.ok is False
    assert result.gap.code == "blocked"

def test_one_source_failure_does_not_discard_other_sources():
    results = scan_all(configs(), routed_fetcher(drom_error=True))
    assert results["drom"].ok is False
    assert results["mashina"].ok is True
```

Also test 403, 429, timeout, unexpected empty discovery, detail-page failure isolation, maximum detail-page cap, and polite delay injection.

Run: `python -m unittest tests.test_sources -v`
Expected: FAIL because source scanning is absent.

- [ ] **Step 4: Implement source-specific discovery configuration**

Configure these live search URLs and listing URL patterns:

```json
{
  "autoru": "https://auto.ru/cars/volkswagen/teramont/all/?year_from=2026&year_to=2026",
  "drom": "https://auto.drom.ru/volkswagen/teramont/?minyear=2026&maxyear=2026",
  "avito": "https://www.avito.ru/rossiya/avtomobili?q=volkswagen%20teramont%20pro%202026",
  "mashina": "https://m.mashina.kg/search/?q=Volkswagen%20Teramont%20Pro",
  "kolesa": "https://kolesa.kz/cars/volkswagen/teramont/"
}
```

Search pages are requested once per source. Only discovered links whose card text contains Teramont/Терамонт are fetched, up to the configured cap. A normal empty result requires a source-specific empty marker; otherwise it is `unexpected_empty`.

- [ ] **Step 5: Run Task 3 tests and commit**

Run: `python -m unittest tests.test_html tests.test_sources -v`
Expected: PASS.

Commit: `git add config/sources.json src/teramont_monitor/html.py src/teramont_monitor/sources.py tests/fixtures tests/test_html.py tests/test_sources.py && git commit -m "feat: add isolated marketplace scanners"`

---

### Task 4: Telegram queue and command-line orchestration

**Files:**
- Create: `src/teramont_monitor/telegram.py`
- Create: `src/teramont_monitor/cli.py`
- Create: `src/teramont_monitor/__main__.py`
- Test: `tests/test_telegram.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: source scan, transition, and storage interfaces from Tasks 1–3.
- Produces: `format_events(events) -> str`, `send_pending(state_dir, token, chat_id, transport) -> int`, `collect(config_path, state_dir, fetcher) -> RunSummary`, and `main(argv=None) -> int`.

- [ ] **Step 1: Write and fail Telegram tests**

```python
def test_formatter_contains_stable_event_id_and_no_credentials():
    text = format_events([sample_event("price_drop")])
    assert "price_drop" in text
    assert "event:" in text
    assert "bot-token" not in text
```

Cover HTML escaping, Russia/EAEU section labels, original-currency display, and the five-kind allowlist.

Run: `python -m unittest tests.test_telegram -v`
Expected: FAIL because Telegram functions are absent.

- [ ] **Step 2: Implement formatting and pending delivery**

Use Telegram `sendMessage` over `urllib.request` with HTML parse mode. Reject any event kind outside the allowlist. On success, remove delivered IDs from `pending-events.json`; on failure, leave the queue unchanged and return non-zero.

- [ ] **Step 3: Write and fail CLI integration tests**

```python
def test_collect_persists_state_history_and_pending_events():
    with TemporaryDirectory() as directory:
        state_dir = Path(directory)
        summary = collect(CONFIG, state_dir, fixture_fetcher())
        assert summary.successful_sources == 5
        assert (state_dir / "state.json").exists()
        assert (state_dir / "history.jsonl").exists()
        assert load_pending(state_dir / "pending-events.json")
```

Also test zero successful sources returning exit code 2, dry-run not mutating files, notify without secrets returning an actionable error, and smoke never notifying.

Run: `python -m unittest tests.test_cli -v`
Expected: FAIL because orchestration is absent.

- [ ] **Step 4: Implement `collect`, `notify`, and `smoke` commands**

`collect` scans and persists; `notify` sends the durable queue; `smoke --dry-run` prints source statuses and never changes state. Human-readable output contains counts and gap codes only, not raw pages.

- [ ] **Step 5: Run Task 4 tests and commit**

Run: `python -m unittest tests.test_telegram tests.test_cli -v`
Expected: PASS.

Commit: `git add src/teramont_monitor/telegram.py src/teramont_monitor/cli.py src/teramont_monitor/__main__.py tests/test_telegram.py tests/test_cli.py && git commit -m "feat: add durable Telegram delivery"`

---

### Task 5: Hourly workflow, documentation, and acceptance verification

**Files:**
- Create: `.github/workflows/monitor.yml`
- Create: `README.md`
- Create: `.gitignore`
- Test: `tests/test_workflow.py`

**Interfaces:**
- Consumes: `python -m teramont_monitor collect`, `notify`, and `smoke` from Task 4.
- Produces: an hourly and manually runnable GitHub Actions workflow plus operator documentation.

- [ ] **Step 1: Write and fail workflow contract tests**

```python
def test_workflow_has_hourly_schedule_manual_run_and_exact_secrets():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "17 * * * *" in text
    assert "workflow_dispatch:" in text
    assert "secrets.TELEGRAM_BOT_TOKEN" in text
    assert "secrets.TELEGRAM_CHAT_ID" in text
    assert "contents: write" in text
```

Also assert one concurrency group, Python 3.12, tests before collection, separate pre-notify and post-notify state commits, and no literal Telegram token pattern.

Run: `python -m unittest tests.test_workflow -v`
Expected: FAIL because the workflow is absent.

- [ ] **Step 2: Implement the state-branch workflow**

The workflow checks out code, runs the full test suite, prepares an existing or orphan `monitor-state` worktree, runs `collect`, commits/pushes pending state, runs `notify`, commits/pushes delivery state, and uploads sanitized diagnostics only on failure. Git identity is `github-actions[bot]`.

- [ ] **Step 3: Write the exact README setup**

Document repository Actions permissions (`Read and write permissions`), secret creation, first manual run, `monitor-state`, local tests, live smoke, event rules, state schema, source gaps, GitHub scheduling limitations, and safe rollback. State that a local commit is not a deployed monitor and that the workflow becomes active only after code reaches the default branch.

- [ ] **Step 4: Run live smoke without persistence or Telegram**

Run: `python -m teramont_monitor smoke --config config/sources.json --dry-run`
Expected: each source reports `ok` with a discovered count or a specific `source_gap`; no state files are created.

- [ ] **Step 5: Run complete deterministic verification**

Run: `python -m unittest discover -s tests -t . -v`
Expected: all tests PASS with zero failures/errors.

Run: `python -m compileall -q src tests`
Expected: exit 0.

Run: `git diff --check`
Expected: exit 0.

- [ ] **Step 6: Security and impact review**

Verify no secret values, cookies, raw marketplace pages, phone numbers, or seller contacts are tracked; all source failures remain isolated; only the five approved event kinds can be sent; no migration is required because the repository was empty.

- [ ] **Step 7: Commit final integration**

Commit: `git add .github/workflows/monitor.yml README.md .gitignore tests/test_workflow.py && git commit -m "ci: run Teramont monitor hourly"`

- [ ] **Step 8: Final readback**

Run: `git status --short --branch && git log --oneline --decorate -6`
Expected: clean `codex/teramont-monitor` branch with the design, implementation checkpoints, and no push performed.
