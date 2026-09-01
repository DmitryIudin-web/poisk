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


def _money(value: float | None, currency: str | None) -> str:
    if value is None:
        return "не подтверждена"
    return f"{value:,.0f} {currency or ''}".replace(",", " ").strip()


def listing_message(listing: Listing, *, event: str = "Новый подходящий автомобиль") -> str:
    features = [name for name, evidence in listing.evidence.items() if evidence.get("value") is True]
    missing = ", ".join(listing.missing) if listing.missing else "нет"
    effective = listing.export_price or listing.net_price or listing.price
    price_label = "Export" if listing.export_price else ("Net" if listing.net_price else "Цена")
    details = []
    if listing.location:
        details.append(listing.location)
    if listing.body_variant:
        details.append(listing.body_variant)
    if listing.regional_spec:
        details.append(f"{listing.regional_spec}-spec")
    if listing.vat_status:
        details.append(listing.vat_status)
    if listing.export_status is True:
        details.append("экспорт разрешён")
    if listing.export_vat is True:
        details.append("ex-VAT/export подтверждён")
    meta = " | ".join(details) if details else "доп. данные не опубликованы"
    normalized = ""
    if listing.normalized_price is not None and listing.normalized_currency:
        normalized = f"\nВ валюте фильтра: {_money(listing.normalized_price, listing.normalized_currency)}"
        if listing.fx_updated_at:
            normalized += f" (FX: {listing.fx_updated_at})"
    return (
        f"🚘 {event}\n{listing.title}\n"
        f"{price_label}: {_money(effective, listing.currency)}{normalized}\n"
        f"Год: {listing.year or 'не подтверждён'} | Пробег: {listing.mileage_km if listing.mileage_km is not None else 'не подтверждён'} км\n"
        f"{meta}\n"
        f"Подтверждено: {', '.join(features) if features else 'нет'}\n"
        f"Требует проверки: {missing}\n{listing.url}"
    )
