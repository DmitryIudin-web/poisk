from __future__ import annotations

import hashlib
from urllib.parse import urlsplit, urlunsplit

from .models import Listing


def canonical_url(url: str) -> str:
    parts = urlsplit(url.strip())
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, "", ""))


def listing_key(listing: Listing) -> str:
    if listing.listing_id:
        return f"{listing.source}:{listing.listing_id}"
    digest = hashlib.sha256(canonical_url(listing.url).encode("utf-8")).hexdigest()[:24]
    return f"{listing.source}:url:{digest}"


def vehicle_key(listing: Listing) -> str:
    if listing.vin:
        return f"vin:{listing.vin.strip().upper()}"
    return listing_key(listing)
