# Universal Vehicle Search — без Vercel

Публичный конструктор мониторингов автомобилей поверх evidence-gated методологии `poisk`.

## Что делает

1. Спрашивает марку и модель.
2. Уточняет только недостающие параметры: комплектацию, кузов/базу, год, пробег, состояние, рынки, цвета, обязательные опции, бюджет и требование export/ex-VAT.
3. Сохраняет каждый пользовательский поиск отдельно.
4. Worker регулярно запускает поиск по выбранным рынкам.
5. Объявление становится `relevant` только когда обязательные параметры подтверждены. Отсутствие доказательства = `candidate`.
6. Дедупликация идёт по VIN, а при его отсутствии — по каноническому URL и заголовку.
7. Новые релевантные объявления и снижения фактической net/export цены могут уходить в Telegram.

## Архитектура

- `FastAPI` — публичный web/API;
- `SQLite WAL` — поиски, результаты и история MVP;
- отдельный `worker` — постоянный мониторинг;
- `Bright Data SERP API` — общий discovery-слой;
- site-aware adapters — глубокий разбор конкретных карточек;
- OpenAI Responses API — опциональная vision-проверка обязательных опций на фото;
- ExchangeRate-API open endpoint — ежедневная FX-нормализация с сохранением исходной цены;
- Caddy — единственная публичная точка входа, automatic HTTPS при наличии домена;
- Docker Compose — развёртывание на обычном VPS, без Vercel.

FX feed: https://www.exchangerate-api.com/ — open access endpoint. В публичной версии приложения необходимо оставить дискретную атрибуцию `Rates By Exchange Rate API` согласно условиям их open-access режима.

## Рынки

Европа, ОАЭ, Грузия, Россия, Китай, Корея, Япония, США.

### Специализированные источники

- `DubiCars`: export status, GCC/US/Canadian spec, Dubai/Sharjah location, gross/net/export markers, фото.
- `mobile.de`: JSON-LD, VIN, пробег, ESV/Long, gross/net/export/T1, VAT markers, фото.
- `AutoScout24`: JSON-LD + VAT/export/spec normalization поверх карточки.
- `MyAuto.ge`: HTML + резервный структурированный запрос `api2.myauto.ge/ka/products/{id}`; API fallback может вернуть пробег, цену, валюту, город, модель и фото даже когда основной DOM JS-only.

## Evidence gating

Для обязательной опции приоритет доказательства:

1. явный текст карточки;
2. structured data / JSON API;
3. vision по фотографиям;
4. нет доказательства → `candidate`.

Vision не подтверждает опцию по названию комплектации. Только по тому, что реально видно на дилерских фото. Визуальное противоречие само по себе не удаляет автомобиль автоматически, а создаёт warning для проверки человеком.

## Цена: gross / net / export / FX

Хранятся раздельно:

- опубликованная цена;
- `brutto/gross`;
- `netto/net/ex-VAT`;
- `export price / Exportpreis / T1`;
- нормализованная цена в валюте пользовательского лимита.

Исходная цена никогда не заменяется FX-конвертацией. Нормализованная цена используется для сравнения рынков и фильтра. Price-drop событие считается по исходной цене карточки, а не по движению курса.

`MwSt. ausweisbar` / `VAT deductible` не считается автоматической гарантией export VAT 0%.

## Запуск

```bash
cp .env.example .env
# BRIGHTDATA_API_KEY=...
# OPENAI_API_KEY=...       # опционально
# TELEGRAM_BOT_TOKEN=...  # опционально

# для домена: PUBLIC_HOST=cars.example.com
# без домена: PUBLIC_HOST=http://localhost

docker compose up -d --build
```

С доменом Caddy автоматически получает TLS-сертификат. FastAPI-порт `8000` наружу не публикуется.

Проверка:

```bash
curl https://cars.example.com/health
```

или без домена:

```bash
curl http://SERVER_IP/health
```

## Публичная защита

По умолчанию:

```env
PUBLIC_RATE_LIMIT_PER_MINUTE=60
CREATE_SEARCH_LIMIT_PER_HOUR=12
```

Caddy является единственной публичной точкой входа; FastAPI доверяет proxy headers только в Docker production-схеме.

## Vision

```env
OPENAI_VISION_MODEL=gpt-5.6-luna
OPENAI_VISION_DETAIL=high
OPENAI_VISION_MAX_IMAGES=4
OPENAI_VISION_MIN_CONFIDENCE=0.85
```

Без `OPENAI_API_KEY` сервис продолжает работу, а photo-only доказательства остаются `candidate`.

## Telegram

После создания поиска интерфейс выдаёт:

```text
/bind A1B2C3
```

Пользователь отправляет команду боту, после чего worker привязывает Telegram chat к конкретному поиску. В уведомлении показываются исходная export/net цена, нормализованная цена в валюте фильтра, VAT/spec/location и недостающие доказательства.

## Проверки

CI покрывает:

- adaptive wizard;
- trim/body/base вопросы;
- evidence-gating;
- vision promotion;
- year/price guard;
- DubiCars export/spec;
- mobile.de nearest gross/net/export price;
- AutoScout VAT/export;
- MyAuto product API fallback;
- cross-currency FX filter;
- rate limiter;
- дедупликацию;
- FastAPI import smoke.

CI: `.github/workflows/test-universal.yml`.

## Следующий этап

1. Добавить административную статистику: source gaps, расход SERP/vision, candidate → relevant.
2. Добавить прямые source-adapters для AutoScout/MyAuto discovery, где это устойчиво и легально, сохранив SERP fallback.
3. Добавить пользовательские сортировки/сравнение нескольких машин в одной таблице.
4. Перейти на PostgreSQL только после появления реальной многопользовательской нагрузки.
