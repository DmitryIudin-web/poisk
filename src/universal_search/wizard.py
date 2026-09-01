from __future__ import annotations

from .schema import Question, SearchProfile

_MARKETS = ("Европа", "ОАЭ", "Грузия", "Россия", "Китай", "Корея", "Япония", "США")
_CONDITIONS = ("new", "used", "either")


def next_questions(profile: SearchProfile) -> list[Question]:
    """Return a small next-step batch instead of a giant static form."""
    questions: list[Question] = []
    if not profile.make.strip():
        questions.append(Question("make", "Какая марка автомобиля нужна?", "text"))
    if not profile.model.strip():
        questions.append(Question("model", "Какая модель?", "text"))
    if questions:
        return questions[:2]

    if "trim" not in profile.answered_fields:
        questions.append(Question(
            "trim", "Нужна конкретная версия / комплектация?", "text", required=False,
            help_text="Например: Sport Platinum, M60i, Autobiography. Пусто = любая.",
        ))
    if "body_variants" not in profile.answered_fields:
        questions.append(Question(
            "body_variants", "Есть требование к кузову, базе или версии длины?", "multi_text", required=False,
            help_text="Например: ESV; Long; LWB; стандартная база. Пусто = любая.",
        ))
    if questions:
        return questions[:2]

    if profile.year_from is None:
        questions.append(Question("year_from", "С какого года выпуска / модельного года искать?", "number"))
    if profile.max_mileage_km is None:
        questions.append(Question("max_mileage_km", "Какой максимальный пробег допустим, км?", "number"))
    if questions:
        return questions[:2]

    if "condition" not in profile.answered_fields:
        questions.append(Question("condition", "Только новый, с пробегом или без разницы?", "choice", options=_CONDITIONS))
    if not profile.markets:
        questions.append(Question("markets", "На каких рынках искать?", "multi", options=_MARKETS))
    if questions:
        return questions[:2]

    if "colors" not in profile.answered_fields:
        questions.append(Question(
            "colors", "Какие цвета кузова допустимы?", "multi_text", required=False,
            help_text="Можно несколько: чёрный, серый, синий, бордо. Пусто = любой цвет.",
        ))
    if "required_features" not in profile.answered_fields:
        questions.append(Question(
            "required_features", "Какие опции обязательны и должны быть подтверждены?", "multi_text", required=False,
            help_text="Например: панорамная крыша; задние экраны; широкая цифровая торпеда; массаж.",
        ))
    if questions:
        return questions[:2]

    if "max_price" not in profile.answered_fields:
        return [Question(
            "max_price", "Есть верхняя граница цены автомобиля?", "money", required=False,
            help_text="Можно пропустить. Цена нужна для фильтра, а не для расчёта доставки.",
        )]
    if profile.max_price is not None and "price_currency" not in profile.answered_fields:
        return [Question("price_currency", "В какой валюте задан лимит?", "choice", options=("EUR", "USD", "AED", "RUB", "CHF"))]
    if "export_vat_required" not in profile.answered_fields:
        return [Question(
            "export_vat_required", "Обязательно ли подтверждать экспортную / ex-VAT цену?", "choice",
            options=("false", "true"), required=False,
            help_text="true = неподтверждённая export/ex-VAT цена останется кандидатом.",
        )]
    return []


def is_complete(profile: SearchProfile) -> bool:
    return not profile.validate() and not next_questions(profile)
