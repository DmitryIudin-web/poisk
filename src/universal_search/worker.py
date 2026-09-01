from __future__ import annotations

import argparse
import json
import os
import time
from urllib.parse import urlencode
from urllib.request import urlopen

from .notify import listing_message, telegram_send
from .providers import BrightDataSerpProvider
from .schema import SearchProfile
from .store import Store
from .vision import VisionVerifier, apply_vision_confirmations


def run_search(store: Store, search_id: str, profile: SearchProfile, chat_id: str | None = None) -> dict:
    provider = BrightDataSerpProvider()
    vision = VisionVerifier()
    listings, warnings = provider.search(profile)
    new_relevant = 0
    price_drops = 0
    vision_verified = 0

    for listing in listings:
        if listing.status == "candidate" and listing.image_urls:
            missing_features = [name for name in listing.missing if name in profile.required_features]
            if missing_features and vision.configured:
                confirmations, vision_warnings = vision.verify(listing, missing_features)
                warnings.extend(f"{listing.source}: {warning}" for warning in vision_warnings)
                if confirmations:
                    apply_vision_confirmations(listing, confirmations)
                    vision_verified += len(confirmations)

        is_new, old_price = store.save_listing(search_id, listing)
        if listing.status != "relevant":
            continue
        effective_price = listing.export_price or listing.net_price or listing.price
        if is_new:
            new_relevant += 1
            if chat_id:
                telegram_send(chat_id, listing_message(listing))
        elif old_price and effective_price and listing.price and listing.price < old_price:
            price_drops += 1
            if chat_id:
                telegram_send(chat_id, listing_message(listing, event=f"Цена снижена: {old_price:,.0f} → {listing.price:,.0f}"))

    store.mark_run(search_id, profile.interval_minutes)
    return {
        "found": len(listings),
        "new_relevant": new_relevant,
        "price_drops": price_drops,
        "vision_confirmations": vision_verified,
        "warnings": warnings,
    }


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
                store.mark_run(row["id"], max(profile.interval_minutes, 60))
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
