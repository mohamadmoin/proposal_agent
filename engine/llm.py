"""One client for every model call, on the OpenAI SDK (OpenAI, Gemini's OpenAI-compatible endpoint, or a fake).

Each call sends knowledge/persona.md as the system message, retries what is worth retrying, and is reported to the
store (step, model, messages, response, tokens, latency, status) so every sentence can be traced to its call.
"""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from engine import prompts
from engine.knowledge import Knowledge

log = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)
R = TypeVar("R")
Recorder = Callable[[dict[str, Any]], None]

PROVIDERS = {
    "openai": {"base_url": None, "small": "gpt-5.4-mini", "strong": "gpt-5.4-mini"},
    "gemini": {
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "small": "gemini-3.1-flash-lite",
        "strong": "gemini-3.8-flash",
    },
    "fake": {"base_url": None, "small": "fake-small", "strong": "fake-strong"},
}


MAX_PROVIDER_WAIT = 45.0  # seconds; a longer requested wait fails the call instead of stalling the job


class LLMError(RuntimeError):
    """A model call failed after its retries."""


class RetryableError(LLMError):
    """Rate limit, server error or timeout: worth two more tries. `delay` = seconds the provider asked us to wait."""

    def __init__(self, message: str, delay: float = 0.0):
        super().__init__(message)
        self.delay = delay


def describe_error(exc: Exception) -> str:
    """A short, user-facing reason for a warning; the full error stays in the call log (trace)."""
    text = f"{type(exc).__name__}: {exc}"
    if "429" in text or "RateLimit" in text or "RESOURCE_EXHAUSTED" in text:
        return "the model provider's rate limit or quota was reached"
    if "Timeout" in text or "timed out" in text:
        return "the service did not answer in time"
    if "APIConnection" in text or "ConnectError" in text:
        return "the service could not be reached"
    first = str(exc).splitlines()[0] if str(exc) else type(exc).__name__
    return first[:120]


def retry_delay(exc: Exception) -> float:
    """The wait a rate-limited provider asks for: a Retry-After header, or "retry in 24.6s" in Gemini's message."""
    import re

    response = getattr(exc, "response", None)
    header = response.headers.get("retry-after") if response is not None else None
    if header and header.replace(".", "", 1).isdigit():
        return float(header)
    match = re.search(r"retry in ([0-9.]+)\s*s", str(exc))
    return float(match.group(1)) if match else 0.0


class BadOutputError(LLMError):
    """A refusal or an answer that does not fit the schema: worth one more try."""


@dataclass(frozen=True)
class LLMConfig:
    """Model settings, all from LLM_* environment variables."""

    provider: str = "openai"
    base_url: str | None = None
    api_key: str = ""
    model_small: str = PROVIDERS["openai"]["small"]
    model_strong: str = PROVIDERS["openai"]["strong"]
    temperature: float | None = None
    effort_small: str | None = "none"
    effort_strong: str | None = "low"
    timeout: float = 90.0
    max_parallel: int = 4
    price_input_per_m: float | None = None
    price_output_per_m: float | None = None

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> LLMConfig:
        env = os.environ if env is None else env
        provider = (env.get("LLM_PROVIDER") or "openai").strip().lower()
        if provider not in PROVIDERS:
            raise LLMError(f"unknown LLM_PROVIDER {provider!r}; use openai, gemini or fake")
        defaults = PROVIDERS[provider]
        return cls(
            provider=provider,
            base_url=(env.get("LLM_BASE_URL") or "").strip() or defaults["base_url"],
            api_key=(env.get("LLM_API_KEY") or "").strip(),
            model_small=(env.get("LLM_MODEL") or "").strip() or defaults["small"],
            model_strong=(env.get("LLM_MODEL_STRONG") or "").strip() or defaults["strong"],
            temperature=optional_float(env.get("LLM_TEMPERATURE")),
            effort_small=effort(env, "LLM_REASONING_EFFORT", "none"),
            effort_strong=effort(env, "LLM_REASONING_EFFORT_STRONG", "low"),
            timeout=float(env.get("LLM_TIMEOUT") or 90),
            max_parallel=max(1, int(env.get("LLM_MAX_PARALLEL") or 4)),
            price_input_per_m=optional_float(env.get("LLM_PRICE_INPUT_PER_M")),
            price_output_per_m=optional_float(env.get("LLM_PRICE_OUTPUT_PER_M")),
        )

    def model(self, tier: str) -> str:
        return self.model_strong if tier == "strong" else self.model_small

    def effort(self, tier: str) -> str | None:
        return self.effort_strong if tier == "strong" else self.effort_small

    def cost(self, prompt_tokens: int, completion_tokens: int) -> float | None:
        """US dollars, only when both prices are configured."""
        if self.price_input_per_m is None or self.price_output_per_m is None:
            return None
        return (prompt_tokens * self.price_input_per_m + completion_tokens * self.price_output_per_m) / 1_000_000


def optional_float(value: str | None) -> float | None:
    value = (value or "").strip()
    return float(value) if value else None


def effort(env: dict[str, str], name: str, default: str) -> str | None:
    """Unset → the default; set but empty → None (do not send reasoning_effort)."""
    if name not in env:
        return default
    return env[name].strip() or None


@dataclass
class Completion:
    """What a backend returns for one call."""

    text: str
    parsed: BaseModel | None
    prompt_tokens: int
    completion_tokens: int
    model: str


class OpenAIBackend:
    """The real backend: the openai SDK pointed at OpenAI, Gemini or LLM_BASE_URL."""

    def __init__(self, config: LLMConfig):
        import openai

        self.openai = openai
        self.config = config
        if not config.api_key:
            raise LLMError("LLM_API_KEY is not set (use LLM_PROVIDER=fake for the offline demo)")
        self.client = openai.OpenAI(
            api_key=config.api_key, base_url=config.base_url, timeout=config.timeout, max_retries=0
        )

    def options(self, tier: str) -> dict[str, Any]:
        """Reasoning models reject temperature, so it is only sent when configured."""
        options: dict[str, Any] = {}
        if self.config.temperature is not None:
            options["temperature"] = self.config.temperature
        level = self.config.effort(tier)
        if level:
            options["reasoning_effort"] = level
        return options

    def call(
        self, messages: list[dict[str, str]], tier: str, schema: type[BaseModel] | None, step: str = ""
    ) -> Completion:
        model = self.config.model(tier)
        try:
            if schema is None:
                response = self.client.chat.completions.create(model=model, messages=messages, **self.options(tier))
                message = response.choices[0].message
                if getattr(message, "refusal", None):
                    raise BadOutputError(f"refusal: {message.refusal}")
                text, parsed = message.content or "", None
            else:
                response = self.client.chat.completions.parse(
                    model=model, messages=messages, response_format=schema, **self.options(tier)
                )
                message = response.choices[0].message
                if getattr(message, "refusal", None):
                    raise BadOutputError(f"refusal: {message.refusal}")
                if message.parsed is None:
                    raise BadOutputError("the answer could not be parsed")
                text, parsed = message.content or "", message.parsed
        except self.openai.RateLimitError as exc:
            raise RetryableError(f"{type(exc).__name__}: {exc}", delay=retry_delay(exc)) from exc
        except self.openai.InternalServerError as exc:
            raise RetryableError(f"{type(exc).__name__}: {exc}") from exc
        except (self.openai.APITimeoutError, self.openai.APIConnectionError) as exc:
            raise RetryableError(f"{type(exc).__name__}: {exc}") from exc
        except (ValidationError, self.openai.LengthFinishReasonError, self.openai.ContentFilterFinishReasonError) as e:
            raise BadOutputError(f"{type(e).__name__}: {e}") from e
        usage = response.usage
        return Completion(
            text=text,
            parsed=parsed,
            prompt_tokens=getattr(usage, "prompt_tokens", 0) or 0,
            completion_tokens=getattr(usage, "completion_tokens", 0) or 0,
            model=getattr(response, "model", model) or model,
        )


class LLM:
    """The engine's model client. `complete_json` returns a Pydantic object, `complete_text` a string."""

    def __init__(self, config: LLMConfig, knowledge: Knowledge, backend: Any = None, recorder: Recorder | None = None):
        self.config = config
        self.knowledge = knowledge
        self.backend = backend or make_backend(config)
        self.recorder = recorder
        self.retry_sleep = 1.0

    def with_recorder(self, recorder: Recorder | None) -> LLM:
        """A copy that reports its calls to `recorder` (one per proposal run)."""
        clone = LLM(self.config, self.knowledge, self.backend, recorder)
        clone.retry_sleep = self.retry_sleep
        return clone

    def with_config(self, **changes: Any) -> LLM:
        clone = LLM(replace(self.config, **changes), self.knowledge, self.backend, self.recorder)
        clone.retry_sleep = self.retry_sleep
        return clone

    # -- prompts --

    def prose_prompt(self, name: str, **values: object) -> str:
        """A prompt for a step that writes proposal text: the step's file plus knowledge/style_rules.md."""
        return (
            prompts.render(name, **values) + "\n\n<writing_rules>\n" + self.knowledge.style_rules + "\n</writing_rules>"
        )

    # -- calls --

    def complete_json(self, step: str, schema: type[T], user: str, tier: str = "small") -> T:
        result = self.call(step, user, tier, schema)
        return result.parsed  # type: ignore[return-value]

    def complete_text(self, step: str, user: str, tier: str = "small") -> str:
        return self.call(step, user, tier, None).text.strip()

    def call(self, step: str, user: str, tier: str, schema: type[BaseModel] | None) -> Completion:
        """Two retries on rate limits, server errors and timeouts; one retry on a refusal or invalid output."""
        messages = [{"role": "system", "content": self.knowledge.persona}, {"role": "user", "content": user}]
        transient_left, bad_output_left = 2, 1
        attempt = 0
        while True:
            attempt += 1
            started = time.monotonic()
            try:
                completion = self.backend.call(messages, tier, schema, step=step)
            except RetryableError as exc:
                self.record(step, tier, messages, "", 0, 0, started, "error", str(exc), attempt)
                if tier == "strong" and self.config.model("small") != self.config.model("strong"):
                    # A strong model that times out or is overloaded would stall the whole job for minutes: the
                    # small model writes this step instead, and the user is told (UsageMeter → warning).
                    self.record(step, "small", messages, "", 0, 0, time.monotonic(), "fallback",
                                f"strong model failed ({exc}); the small model was used", attempt)
                    return self.call(step, user, "small", schema)
                if transient_left <= 0:
                    raise LLMError(f"{step}: {exc}") from exc
                transient_left -= 1
                # free tiers answer 429 with "retry in 24s": waiting that long once beats failing the step
                time.sleep(max(self.retry_sleep * attempt, min(exc.delay, MAX_PROVIDER_WAIT) if self.retry_sleep else 0))
                continue
            except BadOutputError as exc:
                self.record(step, tier, messages, "", 0, 0, started, "error", str(exc), attempt)
                if bad_output_left <= 0:
                    raise LLMError(f"{step}: {exc}") from exc
                bad_output_left -= 1
                continue
            self.record(
                step,
                tier,
                messages,
                completion.text,
                completion.prompt_tokens,
                completion.completion_tokens,
                started,
                "ok",
                "",
                attempt,
                completion.model,
            )
            return completion

    def record(
        self,
        step: str,
        tier: str,
        messages: list[dict[str, str]],
        response: str,
        prompt_tokens: int,
        completion_tokens: int,
        started: float,
        status: str,
        error: str,
        attempt: int,
        model: str | None = None,
    ) -> None:
        if not self.recorder:
            return
        self.recorder(
            {
                "step": step,
                "tier": tier,
                "model": model or self.config.model(tier),
                "messages": messages,
                "response": response,
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "latency_ms": int((time.monotonic() - started) * 1000),
                "status": status,
                "error": error,
                "attempt": attempt,
                "cost_usd": self.config.cost(prompt_tokens, completion_tokens),
            }
        )

    # -- parallelism --

    def map(self, fn: Callable[[Any], R], items: Iterable[Any]) -> list[R | Exception]:
        """Run fn over items in a thread pool of LLM_MAX_PARALLEL; keep the order; return exceptions in place."""
        items = list(items)
        if not items:
            return []

        def safe(item: Any) -> R | Exception:
            try:
                return fn(item)
            except Exception as exc:  # one failed paper must not sink the others
                log.warning("parallel step failed: %s", exc)
                return exc

        with ThreadPoolExecutor(max_workers=min(self.config.max_parallel, len(items))) as pool:
            return list(pool.map(safe, items))


def make_backend(config: LLMConfig) -> Any:
    if config.provider == "fake":
        from engine.fake_llm import FakeBackend

        return FakeBackend(config)
    return OpenAIBackend(config)


def make_llm(knowledge: Knowledge, env: dict[str, str] | None = None) -> LLM:
    """The configured client (LLM_PROVIDER=openai | gemini | fake)."""
    return LLM(LLMConfig.from_env(env), knowledge)
