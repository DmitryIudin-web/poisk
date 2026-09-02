from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from typing import TYPE_CHECKING
from urllib.request import Request, urlopen

from .schema import Listing

if TYPE_CHECKING:
    from .store import Store


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        value = default
    return max(minimum, min(maximum, value))


def _env_float(name: str, default: float, minimum: float, maximum: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except ValueError:
        value = default
    return max(minimum, min(maximum, value))


def _enabled(name: str, default: bool = False) -> bool:
    fallback = "1" if default else "0"
    return os.getenv(name, fallback).casefold() in {"1", "true", "yes", "on"}


@dataclass
class VisionOutcome:
    confirmations: dict[str, dict[str, object]] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    attempted: bool = False
    status: str = "not_attempted"
    usage_id: str | None = None


class VisionVerifier:
    """Confirm missing features from photos with hard, persistent spend controls."""

    endpoint = "https://api.openai.com/v1/responses"
    _DEFAULT_PRICES = {
        "gpt-5.6-luna": {"input": 0.20, "cached": 0.02, "output": 1.20},
    }

    def __init__(self, api_key: str | None = None, model: str | None = None):
        self.api_key = api_key or os.getenv("OPENAI_API_KEY", "")
        self.enabled = _enabled("OPENAI_VISION_ENABLED", False)
        self.model = model or os.getenv("OPENAI_VISION_MODEL", "gpt-5.6-luna")
        self.detail = os.getenv("OPENAI_VISION_DETAIL", "low").casefold()
        if self.detail != "low":
            self.detail = "low"
        self.max_images = _env_int("OPENAI_VISION_MAX_IMAGES", 2, 1, 2)
        self.max_candidates_per_run = _env_int(
            "OPENAI_VISION_MAX_CANDIDATES_PER_RUN", 2, 1, 2
        )
        self.min_confidence = _env_float("OPENAI_VISION_MIN_CONFIDENCE", 0.85, 0.5, 1.0)
        self.max_output_tokens = _env_int("OPENAI_VISION_MAX_OUTPUT_TOKENS", 400, 100, 800)
        self.reserved_input_tokens = _env_int(
            "OPENAI_VISION_RESERVED_INPUT_TOKENS", 3000, 1000, 30_000
        )
        self.daily_token_limit = _env_int(
            "OPENAI_DAILY_TOKEN_LIMIT", 50_000, 1_000, 10_000_000
        )
        self.daily_cost_limit_usd = _env_float(
            "OPENAI_DAILY_COST_LIMIT_USD", 0.10, 0.001, 10_000.0
        )
        defaults = self._DEFAULT_PRICES.get(self.model, {"input": 1.0, "cached": 1.0, "output": 4.0})
        self.input_cost_per_million = _env_float(
            "OPENAI_INPUT_COST_PER_1M", defaults["input"], 0.0, 10_000.0
        )
        self.cached_cost_per_million = _env_float(
            "OPENAI_CACHED_INPUT_COST_PER_1M", defaults["cached"], 0.0, 10_000.0
        )
        self.output_cost_per_million = _env_float(
            "OPENAI_OUTPUT_COST_PER_1M", defaults["output"], 0.0, 10_000.0
        )

    @property
    def configured(self) -> bool:
        return self.enabled and bool(self.api_key)

    @property
    def disabled_reason(self) -> str:
        if not self.enabled:
            return "OpenAI Vision is disabled by OPENAI_VISION_ENABLED"
        if not self.api_key:
            return "OPENAI_API_KEY is not configured"
        return ""

    def image_urls(self, listing: Listing) -> list[str]:
        return [
            url for url in listing.image_urls if url.startswith(("http://", "https://"))
        ][: self.max_images]

    def signature(self, listing: Listing, features: list[str]) -> str:
        payload = {
            "features": sorted(feature for feature in features if feature),
            "images": self.image_urls(listing),
            "detail": self.detail,
            "model": self.model,
        }
        return hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()

    @staticmethod
    def _output_text(payload: dict) -> str:
        for item in payload.get("output", []):
            if item.get("type") != "message":
                continue
            for content in item.get("content", []):
                if content.get("type") == "output_text" and content.get("text"):
                    return str(content["text"])
        return ""

    @staticmethod
    def _token_usage(payload: dict) -> tuple[int | None, int | None, int]:
        usage = payload.get("usage") or {}
        try:
            input_tokens = int(usage["input_tokens"])
        except (KeyError, TypeError, ValueError):
            input_tokens = None
        try:
            output_tokens = int(usage["output_tokens"])
        except (KeyError, TypeError, ValueError):
            output_tokens = None
        details = usage.get("input_tokens_details") or {}
        try:
            cached_tokens = max(0, int(details.get("cached_tokens") or 0))
        except (TypeError, ValueError):
            cached_tokens = 0
        return input_tokens, output_tokens, cached_tokens

    def estimate_cost(self, input_tokens: int, output_tokens: int, cached_tokens: int = 0) -> float:
        cached = max(0, min(int(input_tokens), int(cached_tokens)))
        uncached = max(0, int(input_tokens) - cached)
        return (
            uncached * self.input_cost_per_million
            + cached * self.cached_cost_per_million
            + max(0, int(output_tokens)) * self.output_cost_per_million
        ) / 1_000_000

    def verify(
        self,
        listing: Listing,
        features: list[str],
        *,
        store: Store | None = None,
        search_id: str = "",
        run_id: str = "",
    ) -> VisionOutcome:
        wanted = [feature for feature in features if feature]
        images = self.image_urls(listing)
        if not wanted:
            return VisionOutcome()
        if not self.configured:
            return VisionOutcome(warnings=[self.disabled_reason + "; photo-only evidence remains candidate"])
        if not images:
            return VisionOutcome(warnings=["no dealer photos available for vision verification"])
        if store is None or not search_id or not run_id:
            return VisionOutcome(
                warnings=["vision blocked because persistent usage accounting is unavailable"]
            )

        reserved_tokens = self.reserved_input_tokens + self.max_output_tokens
        reserved_cost = self.estimate_cost(self.reserved_input_tokens, self.max_output_tokens)
        usage_id, usage = store.reserve_api_call(
            search_id=search_id,
            run_id=run_id,
            model=self.model,
            images=len(images),
            reserved_tokens=reserved_tokens,
            reserved_cost_usd=reserved_cost,
            daily_token_limit=self.daily_token_limit,
            daily_cost_limit_usd=self.daily_cost_limit_usd,
        )
        if not usage_id:
            return VisionOutcome(
                warnings=[
                    "OpenAI daily budget reached; worker blocked the API call "
                    f"({int(usage['budget_tokens'])}/{self.daily_token_limit} tokens, "
                    f"${float(usage['estimated_cost_usd']):.6f}/${self.daily_cost_limit_usd:.2f})"
                ]
            )

        result_schema = {
            "type": "object",
            "properties": {
                "results": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "feature": {"type": "string", "enum": wanted},
                            "status": {
                                "type": "string",
                                "enum": ["confirmed", "not_visible", "contradicted"],
                            },
                            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                            "evidence": {"type": "string"},
                            "image_index": {"type": ["integer", "null"]},
                        },
                        "required": [
                            "feature",
                            "status",
                            "confidence",
                            "evidence",
                            "image_index",
                        ],
                        "additionalProperties": False,
                    },
                }
            },
            "required": ["results"],
            "additionalProperties": False,
        }
        content: list[dict[str, object]] = [
            {
                "type": "input_text",
                "text": (
                    "Inspect only the supplied dealer-listing photos. For each requested vehicle "
                    "feature, confirm it only when it is directly visible. Never infer a feature "
                    "from make, model, trim, year, badges, or typical equipment. Use contradicted "
                    "only when a photo clearly shows the relevant area and the feature is visibly "
                    "absent; otherwise use not_visible. Image indexes are zero-based. Requested "
                    "features: "
                    + ", ".join(wanted)
                ),
            }
        ]
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
            "max_output_tokens": self.max_output_tokens,
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
            store.finalize_api_call(
                usage_id,
                status="failed",
                error_type=type(exc).__name__,
            )
            return VisionOutcome(
                warnings=[f"vision request failed: {type(exc).__name__}: {exc}"],
                attempted=True,
                status="failed",
                usage_id=usage_id,
            )

        input_tokens, output_tokens, cached_tokens = self._token_usage(payload)
        actual_cost = None
        if input_tokens is not None and output_tokens is not None:
            actual_cost = self.estimate_cost(input_tokens, output_tokens, cached_tokens)
        store.finalize_api_call(
            usage_id,
            status="succeeded",
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached_tokens=cached_tokens,
            estimated_cost_usd=actual_cost,
        )

        raw = self._output_text(payload)
        if not raw:
            return VisionOutcome(
                warnings=["vision response contained no output text"],
                attempted=True,
                status="failed",
                usage_id=usage_id,
            )
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return VisionOutcome(
                warnings=["vision response was not valid structured JSON"],
                attempted=True,
                status="failed",
                usage_id=usage_id,
            )

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
                warnings.append(
                    f"vision contradiction for {feature}: "
                    f"{item.get('evidence') or 'feature not visible'}"
                )
        return VisionOutcome(
            confirmations=confirmed,
            warnings=warnings,
            attempted=True,
            status="succeeded",
            usage_id=usage_id,
        )


def apply_vision_confirmations(
    listing: Listing, confirmations: dict[str, dict[str, object]]
) -> Listing:
    if listing.status == "irrelevant" or not confirmations:
        return listing
    for feature, evidence in confirmations.items():
        if feature in listing.evidence and evidence.get("value") is True:
            listing.evidence[feature] = evidence
    listing.missing = [name for name in listing.missing if name not in confirmations]
    if not listing.missing:
        listing.status = "relevant"
    return listing
