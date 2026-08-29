from __future__ import annotations

import html
import json
from collections.abc import Callable, Iterable
from pathlib import Path
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from .models import Event, PriceDigest, RankedOffer
from .storage import load_pending, load_price_digest, load_state, save_pending, save_state


ALLOWED_EVENT_KINDS = {
    "new_relevant": "Новый релевантный лот",
    "price_drop": "Снижение цены",
    "became_available": "Появился в наличии",
    "critical_confirmation": "Подтверждён критичный параметр",
    "removed_or_sold": "Снят или продан",
}

REGION_LABELS = {
    "russia": "Россия",
    "bishkek": "Бишкек",
    "eaeu_other": "Другой рынок ЕАЭС",
    "kyrgyzstan": "Кыргызстан",
    "georgia": "Грузия",
    "europe": "Европа",
    "unknown": "Регион не подтверждён",
}


class TelegramError(RuntimeError):
    pass


Transport = Callable[[str, str, str], None]
PhotoTransport = Callable[[str, str, str, str], None]

MISSING_LABELS = {
    "model": "модель",
    "year": "год",
    "exterior_black": "чёрный кузов",
    "interior_black": "чёрный салон",
    "top_trim": "максимальная комплектация",
    "dcc": "DCC",
    "mileage": "пробег",
    "in_stock": "физическое наличие",
    "powertrain_match": "D350",
    "rear_seat_entertainment": "заводские задние экраны",
    "region": "регион",
    "steering_left": "левый руль",
    "cash_price": "цена за наличные",
}


def _money(value: int | None, currency: str | None) -> str | None:
    if value is None:
        return None
    suffix = {
        "RUB": "₽",
        "KGS": "сом",
        "KZT": "₸",
        "EUR": "€",
        "GEL": "₾",
        "USD": "$",
    }.get(currency or "", currency or "")
    return f"{value:,}".replace(",", " ") + (f" {suffix}" if suffix else "")


def _format_event(event: Event) -> str:
    if event.kind not in ALLOWED_EVENT_KINDS:
        raise ValueError(f"Telegram event kind is not approved: {event.kind}")
    listing = event.listing
    lines = [
        f"<b>{html.escape(ALLOWED_EVENT_KINDS[event.kind])}</b>",
        html.escape(listing.target_name),
        html.escape(listing.title),
        f"Рынок: {html.escape(REGION_LABELS.get(listing.region, REGION_LABELS['unknown']))}",
    ]
    price = _money(listing.cash_price, listing.price_currency)
    if price:
        lines.append(f"Цена без условий: <b>{html.escape(price)}</b>")
    elif listing.advertised_price is not None:
        advertised = _money(listing.advertised_price, listing.price_currency)
        lines.append(f"Заявленная цена: {html.escape(advertised or '')} ({html.escape(listing.price_qualifier or 'условия не подтверждены')})")
    if event.kind == "price_drop":
        drop = _money(event.detail.get("drop"), event.detail.get("currency") or listing.price_currency)
        if drop:
            lines.append(f"Снижение: <b>{html.escape(drop)}</b>")
    if event.kind == "critical_confirmation":
        confirmed = ", ".join(str(value) for value in event.detail.get("confirmed", ()))
        if confirmed:
            lines.append(f"Подтверждено: {html.escape(confirmed)}")
    if listing.rear_seat_entertainment.value is True:
        lines.append("Factory RSE: confirmed")
    if listing.steering_left.value is True:
        lines.append("Left-hand drive: confirmed")
    if listing.vin:
        lines.append(f"VIN: <code>{html.escape(listing.vin)}</code>")
    if listing.epts_status:
        lines.append(f"ЭПТС: {html.escape(listing.epts_status)}")
    if listing.commercial_recycling_fee_status:
        lines.append(f"Коммерческий утильсбор: {html.escape(listing.commercial_recycling_fee_status)}")
    lines.extend(
        [
            f'<a href="{html.escape(listing.url, quote=True)}">Открыть объявление</a>',
            f"<code>event: {html.escape(event.id)}</code>",
        ]
    )
    return "\n".join(lines)


def format_events(events: Iterable[Event]) -> str:
    grouped: dict[str, list[Event]] = {}
    for event in events:
        if event.kind not in ALLOWED_EVENT_KINDS:
            raise ValueError(f"Telegram event kind is not approved: {event.kind}")
        grouped.setdefault(event.listing.region, []).append(event)
    sections: list[str] = []
    for region, items in grouped.items():
        sections.append(f"<b>{html.escape(REGION_LABELS.get(region, REGION_LABELS['unknown']))}</b>")
        sections.extend(_format_event(item) for item in items)
    return "\n\n".join(sections)


def _evidence_label(value: bool | None, confirmed: str, missing: str) -> str:
    if value is True:
        return confirmed
    if value is False:
        return "не соответствует"
    return missing


def _format_ranked_offer(index: int, offer: RankedOffer) -> str:
    listing = offer.listing
    strict = (
        offer.status == "relevant"
        and listing.in_stock.value is True
        and listing.cash_price is not None
        and listing.price_currency == "RUB"
    )
    lines = [
        f'{index}. <b>{html.escape(listing.title)}</b>',
        f"Статус: {'подтверждённое предложение' if strict else 'требует проверки'}",
        f"Город: {html.escape(listing.location or 'не указан')}",
        f"Регион: {html.escape(REGION_LABELS.get(listing.region, REGION_LABELS['unknown']))}",
        f"Год: {listing.year if listing.year is not None else 'не подтверждён'}",
        f"Пробег: {listing.mileage_km:,} км".replace(",", " ") if listing.mileage_km is not None else "Пробег: не подтверждён",
        f"Наличие: {_evidence_label(listing.in_stock.value, 'подтверждено', 'не подтверждено')}",
        f"Кузов: {_evidence_label(listing.exterior_black.value, 'чёрный', 'цвет не подтверждён')}",
        f"Салон: {_evidence_label(listing.interior_black.value, 'чёрный', 'цвет не подтверждён')}",
    ]
    if listing.target_id == "teramont-pro-2026":
        lines.extend(
            (
                f"Комплектация Peak/Summit: {_evidence_label(listing.top_trim.value, 'подтверждена', 'не подтверждена')}",
                f"DCC: {_evidence_label(listing.dcc.value, 'подтверждена', 'не подтверждена')}",
            )
        )
    else:
        lines.extend(
            (
                f"Комплектация Autobiography: {_evidence_label(listing.top_trim.value, 'подтверждена', 'не подтверждена')}",
                f"Двигатель D350: {_evidence_label(listing.powertrain_match.value, 'подтверждён', 'не подтверждён')}",
                f"Заводские мониторы для пассажиров: {_evidence_label(listing.rear_seat_entertainment.value, 'подтверждены', 'не подтверждены')}",
            )
        )
    cash = _money(listing.cash_price, listing.price_currency)
    advertised = _money(listing.advertised_price, listing.price_currency)
    if cash:
        lines.append(f"Цена за наличные: <b>{html.escape(cash)}</b>")
    elif advertised:
        lines.append(f"Цена из объявления: <b>{html.escape(advertised)}</b> — условия оплаты не подтверждены")
    else:
        lines.append("Цена: не указана")
    lines.append(f"Источник: {html.escape(listing.source)}")
    if listing.vin:
        lines.append(f"VIN: <code>{html.escape(listing.vin)}</code>")
    if listing.epts_status:
        lines.append(f"ЭПТС: {html.escape(listing.epts_status)}")
    if listing.commercial_recycling_fee_status:
        lines.append(f"Коммерческий утильсбор: {html.escape(listing.commercial_recycling_fee_status)}")
    if offer.missing:
        missing = ", ".join(MISSING_LABELS.get(value, value) for value in offer.missing)
        lines.append(f"не подтверждено: {html.escape(missing)}")
    if not listing.image_url:
        lines.append("Фото: доступно по ссылке на объявление")
    lines.append(f'<a href="{html.escape(listing.url, quote=True)}">Открыть объявление</a>')
    return "\n".join(lines)


def format_price_digest(digest: PriceDigest) -> str:
    lines = [
        f"<b>Три минимальные цены — {html.escape(digest.target_name)}</b>",
        "Россия · физически в наличии · цена без кредита и trade-in",
        f"Источники: доступно {digest.successful_sources}, недоступно {digest.failed_sources}",
        "",
        "<b>Подтверждённые предложения</b>",
    ]
    if digest.confirmed:
        lines.extend(_format_ranked_offer(index, offer) for index, offer in enumerate(digest.confirmed, 1))
    else:
        lines.append("Подтверждённых предложений нет")
    if len(digest.confirmed) < 3:
        lines.extend(("", "<b>Кандидаты</b>"))
        if digest.candidates:
            lines.extend(_format_ranked_offer(index, offer) for index, offer in enumerate(digest.candidates, 1))
        else:
            lines.append("Кандидатов с ценой из объявления нет")
    return "\n\n".join(part for part in lines if part != "")


def _telegram_transport(token: str, chat_id: str, text: str) -> None:
    endpoint = f"https://api.telegram.org/bot{quote(token, safe=':')}/sendMessage"
    body = urlencode({"chat_id": chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": "true"}).encode("utf-8")
    request = Request(endpoint, data=body, headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urlopen(request, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception as error:
        raise TelegramError(f"Telegram delivery failed: {type(error).__name__}") from error
    if not payload.get("ok"):
        raise TelegramError("Telegram API rejected the message")


def _telegram_photo_transport(token: str, chat_id: str, photo: str, caption: str) -> None:
    endpoint = f"https://api.telegram.org/bot{quote(token, safe=':')}/sendPhoto"
    body = urlencode({"chat_id": chat_id, "photo": photo, "caption": caption, "parse_mode": "HTML"}).encode("utf-8")
    request = Request(endpoint, data=body, headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urlopen(request, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception as error:
        raise TelegramError(f"Telegram photo delivery failed: {type(error).__name__}") from error
    if not payload.get("ok"):
        raise TelegramError("Telegram API rejected the photo")


def send_pending(
    state_dir: str | Path,
    token: str,
    chat_id: str,
    *,
    transport: Transport = _telegram_transport,
) -> int:
    if not token or not chat_id:
        raise TelegramError("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are required")
    root = Path(state_dir)
    state_path = root / "state.json"
    pending_path = root / "pending-events.json"
    state = load_state(state_path)
    pending = load_pending(pending_path)
    pending = [event for event in pending if event.id not in state.emitted_event_ids]
    save_pending(pending_path, pending)
    delivered = 0
    while pending:
        event = pending[0]
        transport(token, chat_id, format_events([event]))
        state.emitted_event_ids.add(event.id)
        save_state(state_path, state)
        pending = pending[1:]
        save_pending(pending_path, pending)
        delivered += 1
    return delivered


def send_price_digest(
    state_dir: str | Path,
    token: str,
    chat_id: str,
    *,
    transport: Transport = _telegram_transport,
    photo_transport: PhotoTransport = _telegram_photo_transport,
) -> int:
    if not token or not chat_id:
        raise TelegramError("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are required")
    try:
        digest = load_price_digest(Path(state_dir) / "price-digest.json")
    except (FileNotFoundError, KeyError, TypeError, ValueError) as error:
        raise TelegramError("Fresh price digest is unavailable") from error
    offers = (*digest.confirmed, *digest.candidates)
    if not offers:
        transport(token, chat_id, format_price_digest(digest))
        return 1
    delivered = 0
    for index, offer in enumerate(offers, 1):
        card = "\n".join(
            (
                f"<b>{html.escape(digest.target_name)}</b>",
                f"Источники: доступно {digest.successful_sources}, недоступно {digest.failed_sources}",
                _format_ranked_offer(index, offer),
            )
        )
        if offer.listing.image_url:
            try:
                photo_transport(token, chat_id, offer.listing.image_url, card)
            except TelegramError:
                transport(token, chat_id, card + "\nФото: доступно по ссылке на объявление")
        else:
            transport(token, chat_id, card)
        delivered += 1
    return delivered
