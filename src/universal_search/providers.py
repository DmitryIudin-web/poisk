from __future__ import annotations

import json
import os
from dataclasses import dataclass
from urllib.parse import quote_plus, urlparse
from urllib.request import Request, urlopen

from .adapters import enrich_detail_page
from .evidence import apply_page_enrichment, parse_listing
from .schema import Listing, SearchProfile

MARKET_DOMAINS: dict[str, tuple[str, ...]] = {
    "Европа": ("mobile.de", "autoscout24.com", "autoscout24.de", "autoscout24.ch", "autobazar.eu", "sauto.cz"),
    "ОАЭ": ("dubicars.com", "dubizzle.com"),
    "Грузия": ("myauto.ge", "autopapa.ge", "auto.ge"),
    "Россия": ("auto.ru", "avito.ru", "drom.ru"),
    "Китай": ("dongchedi.com", "che168.com"),
    "Корея": ("encar.com", "kbchachacha.com"),
    "Япония": ("carsensor.net", "goo-net.com"),
    "США": ("autotrader.com", "cars.com", "cargurus.com"),
}


@dataclass(frozen=True)
class OrganicResult:
    title: str
    url: str
    description: str = ""


def _request_json(url: str, payload: dict, headers: dict[str, str], timeout: int = 30) -> dict:
    req = Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
    with urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8", errors="replace"))


def _request_text(url: str, timeout: int = 20) -> str:
    req = Request(url, headers={
        "User-Agent": "Mozilla/5.0 (compatible; VehicleSearchBot/1.0; +https://github.com/DmitryIudin-web/poisk)",
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "en,de;q=0.8,ru;q=0.6",
    })
    with urlopen(req, timeout=timeout) as response:
        data = response.read(8_000_001)
        if len(data) > 8_000_000:
            raise ValueError("detail page exceeds 8 MB")
        return data.decode(response.headers.get_content_charset() or "utf-8", errors="replace")


class BrightDataSerpProvider:
    """Generic discovery plus site-aware detail parsing. Admin supplies keys; public users do not."""

    endpoint = "https://api.brightdata.com/request"

    def __init__(self, api_key: str | None = None, zone: str | None = None):
        self.api_key = api_key or os.getenv("BRIGHTDATA_API_KEY", "")
        self.zone = zone or os.getenv("BRIGHTDATA_SERP_ZONE", "serp_api1")

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    def _query(self, query: str) -> list[OrganicResult]:
        if not self.configured:
            return []
        search_url = "https://www.google.com/search?q=" + quote_plus(query) + "&num=10&hl=en"
        payload = {"zone": self.zone, "url": search_url, "format": "raw", "data_format": "json"}
        raw = _request_json(
            self.endpoint,
            payload,
            {"Content-Type": "application/json", "Authorization": f"Bearer {self.api_key}"},
        )
        organic = raw.get("organic") or raw.get("organic_results") or raw.get("results") or []
        results: list[OrganicResult] = []
        for item in organic:
            url = item.get("link") or item.get("url")
            if not url:
                continue
            results.append(OrganicResult(str(item.get("title") or ""), str(url), str(item.get("description") or item.get("snippet") or "")))
        return results

    def discover(self, profile: SearchProfile) -> tuple[list[OrganicResult], list[str]]:
        warnings: list[str] = []
        if not self.configured:
            return [], ["BRIGHTDATA_API_KEY is not configured"]
        terms = [profile.make, profile.model, profile.trim]
        terms.extend(profile.required_features[:3])
        if profile.year_from and profile.year_from == profile.year_to:
            terms.append(str(profile.year_from))
        base = " ".join(term for term in terms if term).strip()
        combined: dict[str, OrganicResult] = {}
        for market in profile.markets:
            domains = MARKET_DOMAINS.get(market, ())
            if not domains:
                warnings.append(f"no default domains for market: {market}")
                continue
            for domain in domains:
                query = f'{base} site:{domain}'
                try:
                    for item in self._query(query):
                        host = urlparse(item.url).netloc.casefold().removeprefix("www.")
                        if not (host == domain.casefold() or host.endswith("." + domain.casefold())):
                            continue
                        combined[item.url] = item
                except Exception as exc:
                    warnings.append(f"SERP {domain}: {type(exc).__name__}: {exc}")
        return list(combined.values()), warnings

    def search(self, profile: SearchProfile, *, max_details: int = 30) -> tuple[list[Listing], list[str]]:
        organic, warnings = self.discover(profile)
        listings: list[Listing] = []
        for item in organic[:max_details]:
            host = urlparse(item.url).netloc.casefold().removeprefix("www.")
            enrichment = None
            try:
                raw = _request_text(item.url)
                enrichment = enrich_detail_page(item.url, raw)
                body = enrichment.text
            except Exception as exc:
                body = ""
                warnings.append(f"detail {host}: {type(exc).__name__}: {exc}")
            listing = parse_listing(item.url, host, item.title, body, profile, item.description)
            if enrichment is not None:
                listing = apply_page_enrichment(listing, profile, enrichment)
            listings.append(listing)
        return listings, warnings
