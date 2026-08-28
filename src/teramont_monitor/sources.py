from __future__ import annotations

import json
import re
import socket
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .html import extract_detail, extract_links
from .models import SourceGap, SourceResult
from .normalize import normalize_listing


Fetcher = Callable[[str, float], str]
_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_SUSPICIOUS_PAGE_SIZE = 5_000


@dataclass(frozen=True)
class SourceConfig:
    name: str
    search_url: str
    allowed_hosts: tuple[str, ...]
    listing_pattern: str
    url_terms: tuple[str, ...]
    max_details: int
    delay_seconds: float
    timeout_seconds: float
    user_agent: str
    empty_markers: tuple[str, ...]
    blocked_markers: tuple[str, ...]


def load_source_configs(path: str | Path) -> list[SourceConfig]:
    with Path(path).open("r", encoding="utf-8") as stream:
        payload = json.load(stream)
    timeout = float(payload.get("timeout_seconds", 25))
    user_agent = str(payload.get("user_agent") or "TeramontMonitor/0.1")
    configs: list[SourceConfig] = []
    for item in payload.get("sources", []):
        configs.append(
            SourceConfig(
                name=str(item["name"]),
                search_url=str(item["search_url"]),
                allowed_hosts=tuple(item["allowed_hosts"]),
                listing_pattern=str(item["listing_pattern"]),
                url_terms=tuple(item.get("url_terms", ())),
                max_details=int(item.get("max_details", 20)),
                delay_seconds=float(item.get("delay_seconds", 0.25)),
                timeout_seconds=timeout,
                user_agent=user_agent,
                empty_markers=tuple(item.get("empty_markers", ())),
                blocked_markers=tuple(item.get("blocked_markers", ())),
            )
        )
    return configs


def default_fetch(url: str, timeout: float, *, user_agent: str = "TeramontMonitor/0.1") -> str:
    request = Request(url, headers={"User-Agent": user_agent, "Accept": "text/html,application/xhtml+xml"})
    with urlopen(request, timeout=timeout) as response:
        payload = response.read(8_000_001)
        if len(payload) > 8_000_000:
            raise ValueError("response exceeds 8 MB safety limit")
        charset = response.headers.get_content_charset() or "utf-8"
        return payload.decode(charset, errors="replace")


def _gap(config: SourceConfig, code: str, message: str, warnings=()) -> SourceResult:
    return SourceResult(
        config.name,
        False,
        gap=SourceGap(config.name, code, message[:300]),
        search_url=config.search_url,
        complete=False,
        warnings=tuple(warnings),
    )


def _fetch_gap(config: SourceConfig, error: Exception) -> tuple[str, str]:
    if isinstance(error, PermissionError):
        return "blocked", str(error)
    if isinstance(error, HTTPError):
        if error.code == 429:
            return "rate_limited", "HTTP 429"
        if error.code == 403:
            return "blocked", "HTTP 403"
        if error.code == 404:
            return "missing", "HTTP 404"
        return "http_error", f"HTTP {error.code}"
    if isinstance(error, (TimeoutError, socket.timeout)):
        return "timeout", "request timed out"
    if isinstance(error, URLError):
        return "network_error", str(error.reason)
    return "fetch_error", f"{type(error).__name__}: {error}"


def _page_has_marker(page_html: str, markers: tuple[str, ...]) -> bool:
    title_match = _TITLE.search(page_html)
    title = title_match.group(1).casefold() if title_match else ""
    if any(marker.casefold() in title for marker in markers):
        return True
    if len(page_html) < _SUSPICIOUS_PAGE_SIZE:
        lowered = page_html.casefold()
        return any(marker.casefold() in lowered for marker in markers)
    return False


def scan_source(
    config: SourceConfig,
    *,
    fetcher: Fetcher | None = None,
    sleeper: Callable[[float], None] = time.sleep,
) -> SourceResult:
    if fetcher is None:
        fetcher = lambda url, timeout: default_fetch(url, timeout, user_agent=config.user_agent)
    try:
        search_html = fetcher(config.search_url, config.timeout_seconds)
    except Exception as error:  # source boundary intentionally converts transport failures
        code, message = _fetch_gap(config, error)
        return _gap(config, code, message)

    lowered = search_html.casefold()
    if _page_has_marker(search_html, config.blocked_markers):
        return _gap(config, "blocked", "marketplace returned an access-control page")
    if _page_has_marker(search_html, ("too many requests", "error_429", "429 too")):
        return _gap(config, "rate_limited", "marketplace requested a slower rate")

    links = extract_links(search_html, config)
    if not links:
        if any(marker.casefold() in lowered for marker in config.empty_markers):
            return SourceResult(config.name, True, (), search_url=config.search_url, complete=True)
        return _gap(config, "unexpected_empty", "no listing links and no explicit empty-result marker")

    listings = []
    warnings: list[SourceGap] = []
    for index, (listing_id, url) in enumerate(links[: config.max_details]):
        if index and config.delay_seconds:
            sleeper(config.delay_seconds)
        try:
            detail_html = fetcher(url, config.timeout_seconds)
            if _page_has_marker(detail_html, config.blocked_markers):
                raise PermissionError("marketplace returned an access-control detail page")
            detail_text, metadata = extract_detail(detail_html)
            if not detail_text:
                raise ValueError("detail page has no readable content")
            listings.append(normalize_listing(config.name, url, listing_id, detail_text, metadata))
        except Exception as error:  # one broken listing must not discard its source
            code, message = _fetch_gap(config, error)
            warnings.append(SourceGap(config.name, f"detail_{code}", f"{listing_id}: {message}"))

    if not listings:
        return _gap(config, "detail_failed", "all discovered detail pages failed", warnings)
    return SourceResult(
        config.name,
        True,
        tuple(listings),
        search_url=config.search_url,
        complete=len(links) <= config.max_details and not warnings,
        warnings=tuple(warnings),
    )


def scan_all(
    configs: list[SourceConfig],
    *,
    fetcher: Fetcher | None = None,
    sleeper: Callable[[float], None] = time.sleep,
) -> dict[str, SourceResult]:
    return {
        config.name: scan_source(config, fetcher=fetcher, sleeper=sleeper)
        for config in configs
    }
