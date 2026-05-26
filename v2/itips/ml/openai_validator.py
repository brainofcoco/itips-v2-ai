"""OpenAI vision validator — second-opinion layer for ambiguous alerts.

Sits between the local fallbacks (FaceEngine / BehaviorEngine / sensor
dispatch) and the AlertEngine. For each alert in its confidence band,
sends the snapshot + context to a vision model, parses a structured
JSON verdict, and lets the caller suppress / decorate / re-route.

Optional like the rest of `itips/ml/` — `openai` is lazy-imported, the
validator is a no-op when ITIPS_OPENAI_ENABLED=false or the key is
missing, and the baseline pipeline works unchanged.
"""

from __future__ import annotations

import base64
import collections
import json
import logging
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    import numpy as np

logger = logging.getLogger(__name__)


class OpenAIValidatorUnavailable(RuntimeError):
    """Raised when `openai` isn't installed; caller falls back to local-only."""


# Categories that re-route the original alert regardless of the source.
ESCALATE_CATEGORIES = {"fire", "smoke", "weapon"}

# High-confidence threshold for full suppression of false_positive verdicts.
# Below this we downgrade-and-decorate; above, we suppress.
SUPPRESS_CONFIDENCE = 0.85


@dataclass
class ValidationResult:
    scenario: str
    verdict: str        # real | false_positive | inconclusive
    category: str       # human | worker | animal | vehicle | environmental | fire | smoke | weapon | unclear
    confidence: float
    summary: str
    model: str
    tokens_used: int = 0
    cached: bool = False
    raw: dict = field(default_factory=dict)

    @property
    def is_real(self) -> bool:
        return self.verdict == "real"

    @property
    def is_false_positive(self) -> bool:
        return self.verdict == "false_positive"

    @property
    def should_suppress(self) -> bool:
        """Suppress only on high-confidence FP — keep ambiguous in the
        operator's view rather than risk silencing a real attack."""
        return self.is_false_positive and self.confidence >= SUPPRESS_CONFIDENCE

    @property
    def should_escalate(self) -> bool:
        return self.category in ESCALATE_CATEGORIES


class OpenAIValidator:
    """Confidence-banded, cooldown-debounced, quota-capped LLM validator."""

    def __init__(
        self,
        *,
        api_key: Optional[str],
        prompts_path: Path,
        default_model: str = "gpt-4o-mini",
        enabled: bool = True,
        max_tokens_per_hour: int = 100_000,
        max_image_edge_px: int = 768,
        timeout_s: float = 30.0,
    ) -> None:
        self._api_key = api_key
        self._default_model = default_model
        self._enabled = bool(enabled)
        self._max_tokens_per_hour = int(max_tokens_per_hour)
        self._max_image_edge_px = int(max_image_edge_px)
        self._timeout_s = float(timeout_s)
        self._prompts = _load_prompts(prompts_path)
        self._client = None
        self._init_lock = threading.Lock()
        # Per-(scenario, key) cooldown cache: key → (expires_at, last_result).
        self._cooldown: dict[tuple[str, Any], tuple[float, ValidationResult]] = {}
        self._cooldown_lock = threading.Lock()
        # Rolling 1-hour token usage (deque of (ts, tokens)).
        self._token_log: collections.deque = collections.deque()
        self._token_lock = threading.Lock()
        # Audit ring buffer for the dashboard.
        self._recent: collections.deque = collections.deque(maxlen=100)

    # ─── state ────────────────────────────────────────────────────────

    def is_enabled(self) -> bool:
        return self._enabled and bool(self._api_key)

    def is_configured(self) -> bool:
        return bool(self._api_key)

    @property
    def default_model(self) -> str:
        return self._default_model

    @property
    def scenarios(self) -> list[str]:
        return sorted(self._prompts.keys())

    def hourly_token_usage(self) -> tuple[int, int]:
        return self._prune_and_sum_tokens(), self._max_tokens_per_hour

    def recent(self, limit: int = 20) -> list[dict]:
        with self._cooldown_lock:
            items = list(self._recent)
        return list(reversed(items[-limit:]))

    # ─── gating ───────────────────────────────────────────────────────

    def should_validate(
        self,
        scenario: str,
        *,
        local_confidence: float = 0.0,
        cooldown_key: Any = None,
    ) -> bool:
        if not self.is_enabled():
            return False
        prompt = self._prompts.get(scenario)
        if prompt is None:
            return False
        # Confidence band — outside it, trust the local result.
        lo, hi = prompt.get("confidence_band", (0.0, 1.0))
        if not (lo <= local_confidence <= hi):
            return False
        # Quota — never call if we've blown the hourly cap.
        used, cap = self.hourly_token_usage()
        if used >= cap:
            return False
        # Cooldown — within the window, we'll serve the cached result
        # from validate(), so allow this through.
        return True

    # ─── main entry ───────────────────────────────────────────────────

    def validate(
        self,
        scenario: str,
        frame: "np.ndarray",
        context: dict,
        *,
        cooldown_key: Any = None,
    ) -> Optional[ValidationResult]:
        """Returns None on hard failure (no key, no client, network error).
        Cached result returned within the per-scenario cooldown window."""
        if not self.is_enabled():
            return None
        prompt = self._prompts.get(scenario)
        if prompt is None:
            logger.warning("OpenAIValidator: unknown scenario %r", scenario)
            return None

        # Serve from cooldown cache if hot.
        cache_key = (scenario, cooldown_key) if cooldown_key is not None else None
        if cache_key is not None:
            cached = self._read_cooldown(cache_key)
            if cached is not None:
                cached.cached = True
                self._record(cached)
                return cached

        # Quota check after cooldown (so cached hits don't count against quota).
        used, cap = self.hourly_token_usage()
        if used >= cap:
            logger.warning("OpenAIValidator: hourly cap reached (%d/%d)", used, cap)
            return None

        try:
            client = self._ensure_client()
        except OpenAIValidatorUnavailable as exc:
            logger.warning("OpenAIValidator: %s", exc)
            return None

        # Build the request.
        try:
            user_msg = prompt["user_template"].format(**_safe_format_ctx(context))
        except KeyError as exc:
            logger.warning("OpenAIValidator: scenario %s missing key %s in context",
                           scenario, exc)
            return None

        image_b64 = _encode_image(frame, max_edge=self._max_image_edge_px)
        if image_b64 is None:
            logger.info("OpenAIValidator: frame encoding failed, skipping scenario=%s",
                        scenario)
            return None

        model = prompt.get("model") or self._default_model
        try:
            response = client.chat.completions.create(
                model=model,
                response_format={"type": "json_object"},
                temperature=0.1,
                timeout=self._timeout_s,
                messages=[
                    {"role": "system", "content": prompt["system"]},
                    {"role": "user", "content": [
                        {"type": "text", "text": user_msg},
                        {"type": "image_url",
                         "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}},
                    ]},
                ],
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("OpenAIValidator: API call failed scenario=%s: %s",
                           scenario, exc)
            return None

        result = _parse_response(response, scenario=scenario, model=model)
        if result is None:
            return None

        self._record_tokens(result.tokens_used)
        if cache_key is not None:
            self._write_cooldown(cache_key, result, ttl_s=prompt.get("cooldown_s", 30))
        self._record(result)
        logger.info("OpenAIValidator scenario=%s verdict=%s category=%s conf=%.2f tokens=%d",
                    scenario, result.verdict, result.category,
                    result.confidence, result.tokens_used)
        return result

    # ─── internals ────────────────────────────────────────────────────

    def _ensure_client(self):
        if self._client is not None:
            return self._client
        with self._init_lock:
            if self._client is not None:
                return self._client
            try:
                from openai import OpenAI  # type: ignore
            except ImportError as exc:
                raise OpenAIValidatorUnavailable(
                    "openai is not installed. `pip install itips-ai[ml]`."
                ) from exc
            self._client = OpenAI(api_key=self._api_key, timeout=self._timeout_s)
            return self._client

    def _read_cooldown(self, key) -> Optional[ValidationResult]:
        now = time.monotonic()
        with self._cooldown_lock:
            entry = self._cooldown.get(key)
            if entry is None:
                return None
            expires_at, result = entry
            if now >= expires_at:
                self._cooldown.pop(key, None)
                return None
        return result

    def _write_cooldown(self, key, result: ValidationResult, *, ttl_s: float) -> None:
        with self._cooldown_lock:
            self._cooldown[key] = (time.monotonic() + float(ttl_s), result)

    def _record_tokens(self, tokens: int) -> None:
        if tokens <= 0:
            return
        with self._token_lock:
            self._token_log.append((time.time(), int(tokens)))

    def _prune_and_sum_tokens(self) -> int:
        cutoff = time.time() - 3600.0
        with self._token_lock:
            while self._token_log and self._token_log[0][0] < cutoff:
                self._token_log.popleft()
            return sum(t for _, t in self._token_log)

    def _record(self, result: ValidationResult) -> None:
        with self._cooldown_lock:
            self._recent.append({
                "ts": time.time(),
                "scenario": result.scenario,
                "verdict": result.verdict,
                "category": result.category,
                "confidence": result.confidence,
                "summary": result.summary,
                "model": result.model,
                "tokens_used": result.tokens_used,
                "cached": result.cached,
            })

    # ─── service shim ─────────────────────────────────────────────────

    def start(self) -> None:
        return

    def stop(self) -> None:
        return


# ─── helpers ─────────────────────────────────────────────────────────


def _load_prompts(path: Path) -> dict[str, dict]:
    if not path or not Path(path).exists():
        logger.warning("OpenAIValidator: prompts file %s missing", path)
        return {}
    try:
        import yaml
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    except Exception:
        logger.exception("OpenAIValidator: prompts %s unreadable", path)
        return {}
    scenarios = raw.get("scenarios") or {}
    if not isinstance(scenarios, dict):
        return {}
    return scenarios


def _safe_format_ctx(context: dict) -> dict:
    """str.format chokes on missing keys; supply blank defaults for the
    ones our prompt templates reference."""
    defaults = {
        "camera_id": context.get("camera_id", "?"),
        "zone_id": context.get("zone_id", "?"),
        "zone_name": context.get("zone_name", ""),
        "preset_name": context.get("preset_name", ""),
        "sensor_type": context.get("sensor_type", ""),
        "class_name": context.get("class_name", ""),
        "confidence": float(context.get("confidence", 0.0) or 0.0),
        "direction": context.get("direction", ""),
        "dwell_seconds": float(context.get("dwell_seconds", 0.0) or 0.0),
        "events_inside": int(context.get("events_inside", 0) or 0),
    }
    defaults.update({k: v for k, v in context.items() if v is not None})
    return defaults


def _encode_image(frame: "np.ndarray", *, max_edge: int) -> Optional[str]:
    """Resize to longest-edge `max_edge` px, encode JPEG, base64."""
    try:
        import cv2
        h, w = frame.shape[:2]
        longest = max(h, w)
        if longest > max_edge:
            scale = max_edge / float(longest)
            new_size = (int(w * scale), int(h * scale))
            frame = cv2.resize(frame, new_size, interpolation=cv2.INTER_AREA)
        ok, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
        if not ok:
            return None
        return base64.b64encode(buf.tobytes()).decode("ascii")
    except Exception:
        logger.exception("OpenAIValidator: image encode failed")
        return None


_ALLOWED_VERDICTS = {"real", "false_positive", "inconclusive"}
_ALLOWED_CATEGORIES = {
    "human", "worker", "animal", "vehicle",
    "environmental", "fire", "smoke", "weapon", "unclear",
}


def _parse_response(response, *, scenario: str, model: str) -> Optional[ValidationResult]:
    try:
        content = response.choices[0].message.content or "{}"
        data = json.loads(content)
    except (json.JSONDecodeError, AttributeError, IndexError) as exc:
        logger.warning("OpenAIValidator: bad response for %s: %s", scenario, exc)
        return None
    verdict = str(data.get("verdict", "inconclusive")).lower()
    category = str(data.get("category", "unclear")).lower()
    if verdict not in _ALLOWED_VERDICTS:
        verdict = "inconclusive"
    if category not in _ALLOWED_CATEGORIES:
        category = "unclear"
    try:
        confidence = float(data.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = max(0.0, min(1.0, confidence))
    summary = str(data.get("summary", "")).strip()
    tokens = int(getattr(response, "usage", None).total_tokens) if getattr(response, "usage", None) else 0
    return ValidationResult(
        scenario=scenario,
        verdict=verdict,
        category=category,
        confidence=confidence,
        summary=summary,
        model=model,
        tokens_used=tokens,
        raw=data,
    )
