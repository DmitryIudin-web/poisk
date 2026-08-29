from __future__ import annotations

import html as html_module
import json
import re
from html.parser import HTMLParser
from typing import Any, Protocol
from urllib.parse import urljoin, urlsplit

from .identity import canonical_url


class LinkConfig(Protocol):
    search_url: str
    allowed_hosts: tuple[str, ...]
    listing_pattern: str
    url_terms: tuple[str, ...]


_URL_CANDIDATE = re.compile(r"(?:https?:)?//[^\s\"'<>\\]+|/[^\s\"'<>\\]+", re.IGNORECASE)


class _PageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[str] = []
        self.visible: list[str] = []
        self.meta: list[str] = []
        self.meta_title: str | None = None
        self.meta_image: str | None = None
        self.jsonld: list[str] = []
        self.primary_visible: list[str] = []
        self._primary_depth = 0
        self._ignored_depth = 0
        self._jsonld_depth = 0
        self._jsonld_buffer: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag in {"main", "article"}:
            self._primary_depth += 1
        if tag == "a" and attributes.get("href"):
            self.links.append(attributes["href"] or "")
        if tag == "meta" and attributes.get("content"):
            meta_name = (attributes.get("name") or attributes.get("property") or "").casefold()
            if meta_name == "og:title":
                self.meta_title = attributes["content"] or ""
            elif meta_name in {"og:image", "twitter:image", "twitter:image:src"}:
                self.meta_image = self.meta_image or attributes["content"] or None
            elif meta_name in {"description", "og:description"}:
                self.meta.append(attributes["content"] or "")
        if tag == "script" and (attributes.get("type") or "").casefold() == "application/ld+json":
            self._jsonld_depth = 1
            self._jsonld_buffer = []
            return
        if tag in {"script", "style", "noscript", "template"}:
            self._ignored_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self._jsonld_depth:
            self.jsonld.append("".join(self._jsonld_buffer))
            self._jsonld_depth = 0
            self._jsonld_buffer = []
            return
        if tag in {"script", "style", "noscript", "template"} and self._ignored_depth:
            self._ignored_depth -= 1
        if tag in {"main", "article"} and self._primary_depth:
            self._primary_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._jsonld_depth:
            self._jsonld_buffer.append(data)
        elif not self._ignored_depth and data.strip():
            self.visible.append(data.strip())
            if self._primary_depth:
                self.primary_visible.append(data.strip())


def _host_allowed(host: str, allowed: tuple[str, ...]) -> bool:
    host = host.casefold().split(":", 1)[0]
    return any(host == item.casefold() for item in allowed)


def extract_links(page_html: str, config: LinkConfig) -> list[tuple[str, str]]:
    parser = _PageParser()
    parser.feed(page_html)
    parser.close()
    raw_candidates = list(parser.links)
    raw_candidates.extend(match.group(0) for match in _URL_CANDIDATE.finditer(html_module.unescape(page_html)))
    pattern = re.compile(config.listing_pattern, re.IGNORECASE)
    found: list[tuple[str, str]] = []
    seen: set[str] = set()
    for raw in raw_candidates:
        decoded = html_module.unescape(raw).replace(r"\/", "/").replace(r"\u002F", "/")
        try:
            candidate = urljoin(config.search_url, decoded)
            absolute = canonical_url(candidate)
        except ValueError:
            continue
        if not _host_allowed(urlsplit(absolute).netloc, config.allowed_hosts):
            continue
        lowered = absolute.casefold()
        if any(term.casefold() not in lowered for term in config.url_terms):
            continue
        match = pattern.search(absolute)
        if not match and (query := urlsplit(candidate).query):
            match = pattern.search(f"{absolute}?{query}")
            if match:
                absolute = match.group(0)
        if not match or absolute in seen:
            continue
        listing_id = match.groupdict().get("id") or match.group(match.lastindex or 0)
        if not listing_id:
            continue
        seen.add(absolute)
        found.append((listing_id, absolute))
    return found


def _walk_json(value: Any, strings: list[str], metadata: dict[str, Any]) -> None:
    if isinstance(value, list):
        for item in value:
            _walk_json(item, strings, metadata)
        return
    if not isinstance(value, dict):
        if isinstance(value, str) and value.strip():
            strings.append(value.strip())
        return
    name = value.get("name") or value.get("headline")
    if name and "title" not in metadata:
        metadata["title"] = str(name)
    price = value.get("price")
    if isinstance(price, (int, float)) and not isinstance(price, bool):
        metadata.setdefault("price", int(price))
    currency = value.get("priceCurrency")
    if currency:
        metadata.setdefault("price_currency", str(currency))
    locality = value.get("addressLocality")
    if locality:
        metadata.setdefault("location", str(locality))
    image = value.get("image")
    if "image_url" not in metadata:
        if isinstance(image, str) and image.strip():
            metadata["image_url"] = image.strip()
        elif isinstance(image, list):
            candidate = next((item.strip() for item in image if isinstance(item, str) and item.strip()), None)
            if candidate:
                metadata["image_url"] = candidate
        elif isinstance(image, dict):
            candidate = image.get("url") or image.get("contentUrl")
            if isinstance(candidate, str) and candidate.strip():
                metadata["image_url"] = candidate.strip()
    for item in value.values():
        _walk_json(item, strings, metadata)


def extract_detail(page_html: str) -> tuple[str, dict[str, Any]]:
    parser = _PageParser()
    parser.feed(page_html)
    parser.close()
    metadata: dict[str, Any] = {}
    if parser.meta_title:
        metadata["title"] = parser.meta_title
    if parser.meta_image:
        metadata["image_url"] = parser.meta_image
    structured_strings: list[str] = []
    for block in parser.jsonld:
        try:
            _walk_json(json.loads(block), structured_strings, metadata)
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
    visible = parser.primary_visible or parser.visible
    strings = [*parser.meta, *visible]
    if not parser.primary_visible:
        strings.extend(structured_strings)
    text = re.sub(r"\s+", " ", " | ".join(strings)).strip()
    return text, metadata
