from __future__ import annotations

import argparse
import json
import os
import time
from urllib.parse import urlencode
from urllib.request import urlopen

from .fx import FxProvider, normalize_listing_price
from .notify import listing_message, telegram_send
from .providers import BrightDataSerpProvider
from .schema import SearchProfile
from .store import Store
from .vision import VisionVerifier, apply_vision_confirmations


def run_search(
    store: Store,
    search_id: str,
    profile: SearchProfile,
    chat_id: str | None = None,
    *,
    provider: BrightDataSerpProvider | None = None,
    vision: VisionVerifier | None = None,
) -> dict:
    run_id, skip_reason = store.start_run(search_id)
    if not run_id:
        return {"skipped": True, "reason": skip_reason or "run unavailable"}

    provider = provider or BrightDataSerpProvider()
    vision = vision or VisionVerifier()
    new_relevant = 0
    price_drops = 0
    vision_verified = 0
    vision_candidates = 0
    vision_disabled_reported = False
    vision_budget_blocked = False

    try:
        listings, warnings = provider.search(profile)
        fx_snapshot = None
        if profile.max_price and profile.price_currency:
            try:
                fx_snapshot = FxProvider().get()
            except Exception as exc:
                warnings.append(f"FX: {type(exc).__name__}: {exc}")

        for listing in listings:
            if listing.status == "candidate" and listing.image_urls:
                missing_features = [
                    name for name in listing.missing if name in profile.required_features
                ]
                if missing_features and not vision.configured and not vision_disabled_reported:
                    warnings.append(vision.disabled_reason + "; photo-only evidence remains candidate")
                    vision_disabled_reported = True
                elif (
                    missing_features
                    and not vision_budget_blocked
                    and vision_candidates < vision.max_candidates_per_run
                ):
                    fingerprint = listing.fingerprint or listing.url
                    signature = vision.signature(listing, missing_features)
                    if store.vision_check_due(search_id, fingerprint, signature):
                        outcome = vision.verify(
                            listing,
                            missing_features,
                            store=store,
                            search_id=search_id,
                            run_id=run_id,
                        )
                        warnings.extend(
                            f"{listing.source}: {warning}" for warning in outcome.warnings
                        )
                        if not outcome.attempted and any(
                            warning.startswith("OpenAI daily budget reached")
                            for warning in outcome.warnings
                        ):
                            vision_budget_blocked = True
                        if outcome.attempted:
                            vision_candidates += 1
                            store.record_vision_check(
                                search_id, fingerprint, signature, outcome.status
                            )
                        if outcome.confirmations:
                            apply_vision_confirmations(listing, outcome.confirmations)
                            vision_verified += len(outcome.confirmations)

            if fx_snapshot is not None:
                normalize_listing_price(listing, profile, fx_snapshot)

            is_new, old_price = store.save_listing(search_id, listing)
            if listing.status != "relevant":
                continue
            effective_price = listing.export_price or listing.net_price or listing.price
            if is_new:
                new_relevant += 1
                if chat_id:
                    telegram_send(chat_id, listing_message(listing))
            elif old_price and effective_price and effective_price < old_price:
                price_drops += 1
                if chat_id:
                    telegram_send(
                        chat_id,
                        listing_message(
                            listing,
                            event=f"Цена снижена: {old_price:,.0f} → {effective_price:,.0f}",
                        ),
                    )

        store.finish_run(
            search_id,
            run_id,
            profile.interval_minutes,
            status="succeeded",
            found=len(listings),
            new_relevant=new_relevant,
            price_drops=price_drops,
            vision_candidates=vision_candidates,
        )
        return {
            "run_id": run_id,
            "found": len(listings),
            "new_relevant": new_relevant,
            "price_drops": price_drops,
            "vision_candidates": vision_candidates,
            "vision_confirmations": vision_verified,
            "warnings": warnings,
        }
    except Exception as exc:
        store.finish_run(
            search_id,
            run_id,
            max(profile.interval_minutes, 60),
            status="failed",
            vision_candidates=vision_candidates,
            error_type=type(exc).__name__,
        )
        raise


def poll_telegram_bindings(store: Store) -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    if not token:
        return
    offset_file = store.path.with_suffix(".telegram-offset")
    try:
        offset = int(offset_file.read_text().strip()) if offset_file.exists() else 0
    except ValueError:
        offset = 0
    query = urlencode({"timeout": 1, "offset": offset})
    try:
        payload = json.loads(urlopen(f"https://api.telegram.org/bot{token}/getUpdates?{query}", timeout=5).read().decode())
    except Exception:
        return
    for update in payload.get("result", []):
        offset = max(offset, int(update["update_id"]) + 1)
        message = update.get("message") or {}
        text = str(message.get("text") or "").strip()
        chat_id = str((message.get("chat") or {}).get("id") or "")
        if text.lower().startswith("/bind ") and chat_id:
            code = text.split(maxsplit=1)[1].strip().upper()
            search_id = store.bind_telegram(code, chat_id)
            if search_id:
                telegram_send(chat_id, f"✅ Уведомления подключены к поиску {search_id}")
    offset_file.write_text(str(offset))


def worker_loop(db_path: str, once: bool = False) -> None:
    store = Store(db_path)
    while True:
        poll_telegram_bindings(store)
        for row in store.due_searches():
            profile = SearchProfile.from_dict(json.loads(row["profile_json"]))
            try:
                run_search(store, row["id"], profile, row["telegram_chat_id"])
            except Exception as exc:
                print(f"search {row['id']} failed: {type(exc).__name__}: {exc}", flush=True)
        if once:
            return
        time.sleep(30)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=os.getenv("SEARCH_DB", "data/searches.db"))
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    worker_loop(args.db, args.once)


if __name__ == "__main__":
    main()
