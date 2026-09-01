from __future__ import annotations

import json
import os
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .schema import Listing


def telegram_send(chat_id: str, text: str) -> bool:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    if not token or not chat_id:
        return False
    data = urlencode({"chat_id": chat_id, "text": text, "disable_web_page_preview": "false"}).encode()
    req = Request(f"https://api.telegram.org/bot{token}/sendMessage", data=data, method="POST")
    with urlopen(req, timeout=15) as response:
        payload = json.loads(response.read().decode("utf-8"))
        return bool(payload.get("ok"))


def listing_message(listing: Listing, *, event: str = "Новый подходящий автомобиль") -> str:
    features = [name for name, evidence in listing.evidence.items() if evidence.get("value") is True]
    missing = ", ".join(listing.missing) if listing.missing else "нет"
    price = f"{listing.price:,.0f} {listing.currency or ''}".replace(",", " ") if listing.price else "не подтверждена"
    return (
        f"🚘 {event}\n{listing.title}\n"
        f"Цена: {price}\nГод: {listing.year or 'не подтверждён'} | Пробег: {listing.mileage_km if listing.mileage_km is not None else 'не подтверждён'} км\n"
        f"Подтверждено: {', '.join(features) if features else 'нет'}\n"
        f"Требует проверки: {missing}\n{listing.url}"
    )
