from __future__ import annotations

import json
import os
from urllib.request import Request, urlopen

from .schema import Listing


class VisionVerifier:
    """Confirm missing vehicle features from dealer photos without inferring from trim/model."""

    endpoint = "https://api.openai.com/v1/responses"

    def __init__(self, api_key: str | None = None, model: str | None = None):
        self.api_key = api_key or os.getenv("OPENAI_API_KEY", "")
        self.model = model or os.getenv("OPENAI_VISION_MODEL", "gpt-5.6-luna")
        self.detail = os.getenv("OPENAI_VISION_DETAIL", "high").casefold()
        if self.detail not in {"low", "high", "auto"}:
            self.detail = "high"
        try:
            self.max_images = max(1, min(8, int(os.getenv("OPENAI_VISION_MAX_IMAGES", "4"))))
        except ValueError:
            self.max_images = 4
        try:
            self.min_confidence = min(1.0, max(0.5, float(os.getenv("OPENAI_VISION_MIN_CONFIDENCE", "0.85"))))
        except ValueError:
            self.min_confidence = 0.85

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    @staticmethod
    def _output_text(payload: dict) -> str:
        for item in payload.get("output", []):
            if item.get("type") != "message":
                continue
            for content in item.get("content", []):
                if content.get("type") == "output_text" and content.get("text"):
                    return str(content["text"])
        return ""

    def verify(self, listing: Listing, features: list[str]) -> tuple[dict[str, dict[str, object]], list[str]]:
        wanted = [feature for feature in features if feature]
        images = [url for url in listing.image_urls if url.startswith(("http://", "https://"))][: self.max_images]
        if not wanted:
            return {}, []
        if not self.configured:
            return {}, ["OPENAI_API_KEY is not configured; photo-only evidence remains candidate"]
        if not images:
            return {}, ["no dealer photos available for vision verification"]

        result_schema = {
            "type": "object",
            "properties": {
                "results": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "feature": {"type": "string", "enum": wanted},
                            "status": {"type": "string", "enum": ["confirmed", "not_visible", "contradicted"]},
                            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                            "evidence": {"type": "string"},
                            "image_index": {"type": ["integer", "null"]},
                        },
                        "required": ["feature", "status", "confidence", "evidence", "image_index"],
                        "additionalProperties": False,
                    },
                }
            },
            "required": ["results"],
            "additionalProperties": False,
        }
        content: list[dict[str, object]] = [{
            "type": "input_text",
            "text": (
                "Inspect only the supplied dealer-listing photos. For each requested vehicle feature, "
                "confirm it only when it is directly visible. Never infer a feature from make, model, trim, "
                "year, badges, or typical equipment. Use contradicted only when a photo clearly shows the "
                "relevant area and the feature is visibly absent; otherwise use not_visible. Image indexes "
                "are zero-based in the order supplied. Requested features: " + ", ".join(wanted)
            ),
        }]
        for image in images:
            content.append({"type": "input_image", "image_url": image, "detail": self.detail})
        request_payload = {
            "model": self.model,
            "store": False,
            "reasoning": {"effort": "low"},
            "input": [{"role": "user", "content": content}],
            "text": {
                "verbosity": "low",
                "format": {
                    "type": "json_schema",
                    "name": "vehicle_feature_evidence",
                    "strict": True,
                    "schema": result_schema,
                },
            },
            "max_output_tokens": 800,
        }
        request = Request(
            self.endpoint,
            data=json.dumps(request_payload).encode("utf-8"),
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=45) as response:
                payload = json.loads(response.read().decode("utf-8", errors="replace"))
        except Exception as exc:
            return {}, [f"vision request failed: {type(exc).__name__}: {exc}"]
        raw = self._output_text(payload)
        if not raw:
            return {}, ["vision response contained no output text"]
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {}, ["vision response was not valid structured JSON"]

        confirmed: dict[str, dict[str, object]] = {}
        warnings: list[str] = []
        for item in parsed.get("results", []):
            feature = str(item.get("feature") or "")
            status = str(item.get("status") or "")
            try:
                confidence = float(item.get("confidence", 0))
            except (TypeError, ValueError):
                confidence = 0
            if feature not in wanted:
                continue
            if status == "confirmed" and confidence >= self.min_confidence:
                index = item.get("image_index")
                evidence = str(item.get("evidence") or "visually confirmed")[:180]
                confirmed[feature] = {
                    "value": True,
                    "source_text": f"vision image {index}: {evidence}",
                    "confidence": confidence,
                    "source": "vision",
                }
            elif status == "contradicted" and confidence >= self.min_confidence:
                warnings.append(f"vision contradiction for {feature}: {item.get('evidence') or 'feature not visible'}")
        return confirmed, warnings


def apply_vision_confirmations(listing: Listing, confirmations: dict[str, dict[str, object]]) -> Listing:
    if listing.status == "irrelevant" or not confirmations:
        return listing
    for feature, evidence in confirmations.items():
        if feature in listing.evidence and evidence.get("value") is True:
            listing.evidence[feature] = evidence
    listing.missing = [name for name in listing.missing if name not in confirmations]
    if not listing.missing:
        listing.status = "relevant"
    return listing
