"""The agent's models, built from the environment.

- lead:   the researcher. Makes the hard decisions (design, primary outcome, sample-size parameters) and writes the
          sections with the whole proposal in view. Default gemini-3.8-flash, thinking "low" (AGENT_LEAD_THINKING).
- worker: bulk work whose output code can check (screening, data extraction, one-study paragraphs). Default
          gemini-3.1-flash-lite, thinking "low".
Each has an optional OpenAI fallback (gpt-5.4-mini through the Responses API, as in dose_calculator's platform agent)
when OPENAI_API_KEY is set. Gemini is reached through GoogleModel (native API), never the OpenAI-compatible endpoint,
because that path drops Gemini's thought signatures in multi-turn tool calls.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass

from pydantic_ai.exceptions import ModelAPIError, UsageLimitExceeded
from pydantic_ai.models import Model
from pydantic_ai.models.fallback import FallbackModel

log = logging.getLogger(__name__)

DEFAULTS = {
    "lead": ("gemini-3.8-flash", "low"),  # S3 gate (AGT-20): low judged 3.93 vs medium 3.64 on 4 titles, cheaper
    "worker": ("gemini-3.1-flash-lite", "low"),
    "fallback": ("gpt-5.4-mini", "low"),
}
# "minimal" is rejected by gemini-3.8-flash with HTTP 400 (dose_calculator AGENT_MODEL_LATENCY note, 2026-10-21)
THINKING_LEVELS = {"low", "medium", "high"}


class ModelConfigError(RuntimeError):
    """The agent cannot be built (missing key or bad setting)."""


@dataclass(frozen=True)
class AgentModels:
    lead: Model
    worker: Model
    names: dict[str, str]
    critic: Model | None = None  # the consistency critic; None = the lead's model


def google_key(env: Mapping[str, str]) -> str:
    """The Gemini key: AGENT_GOOGLE_API_KEY, then GEMINI_TOKEN, then LLM_API_KEY (when LLM_PROVIDER=gemini), then
    GOOGLE_API_KEY."""
    if env.get("AGENT_GOOGLE_API_KEY"):
        return env["AGENT_GOOGLE_API_KEY"]
    if env.get("GEMINI_TOKEN"):
        return env["GEMINI_TOKEN"]
    if (env.get("LLM_PROVIDER") or "").strip().lower() == "gemini" and env.get("LLM_API_KEY"):
        return env["LLM_API_KEY"]
    return env.get("GOOGLE_API_KEY", "")


def _thinking(role: str, env: Mapping[str, str]) -> str:
    level = (env.get(f"AGENT_{role.upper()}_THINKING") or DEFAULTS[role][1]).strip().lower()
    if level not in THINKING_LEVELS:
        raise ModelConfigError(f"AGENT_{role.upper()}_THINKING must be one of {sorted(THINKING_LEVELS)}, not {level!r}")
    return level


def _google(name: str, thinking: str, key: str, env: Mapping[str, str], max_tokens: int) -> Model:
    from google.genai.types import HttpRetryOptions
    from pydantic_ai.models.google import GoogleModel
    from pydantic_ai.providers.google import GoogleProvider

    base = (env.get("GEMINI_BASE_URL") or "").strip().rstrip("/") or None
    # retries timeouts, connection errors, 429 and 5xx with backoff, so a short network blip does not end a run (S3)
    retry = HttpRetryOptions(attempts=4, initial_delay=2.0, max_delay=30.0)
    provider = (GoogleProvider(api_key=key, base_url=base, retry_options=retry) if base
                else GoogleProvider(api_key=key, retry_options=retry))
    return GoogleModel(name, provider=provider,
                       settings={"max_tokens": max_tokens, "google_thinking_config": {"thinking_level": thinking}})


def _openai(name: str, effort: str, key: str, max_tokens: int) -> Model:
    from pydantic_ai.models.openai import OpenAIResponsesModel
    from pydantic_ai.providers.openai import OpenAIProvider

    # the Responses API: gpt-5.x chat/completions rejects tools together with reasoning_effort
    return OpenAIResponsesModel(name, provider=OpenAIProvider(api_key=key),
                                settings={"max_tokens": max_tokens, "openai_reasoning_effort": effort})


def should_fall_back(exc: Exception) -> bool:
    """Fall back on provider/API failures, never on our own budget limits; always say so in the log."""
    if isinstance(exc, UsageLimitExceeded):
        return False
    if isinstance(exc, ModelAPIError):
        log.warning("AGENT_MODEL_FALLBACK %s: %s", type(exc).__name__, str(exc)[:200])
        return True
    return False


def build_models(env: Mapping[str, str] | None = None) -> AgentModels:
    env = os.environ if env is None else env
    key = google_key(env)
    if not key:
        raise ModelConfigError("no Gemini key: set AGENT_GOOGLE_API_KEY, GEMINI_TOKEN, LLM_API_KEY or GOOGLE_API_KEY")
    openai_key = env.get("OPENAI_API_KEY", "")
    fb_name = env.get("AGENT_FALLBACK_MODEL") or DEFAULTS["fallback"][0]
    names: dict[str, str] = {}
    built: dict[str, Model] = {}
    for role, max_tokens in (("lead", 32_000), ("worker", 8_000)):
        name = env.get(f"AGENT_{role.upper()}_MODEL") or DEFAULTS[role][0]
        thinking = _thinking(role, env)
        primary = _google(name, thinking, key, env, max_tokens)
        names[role] = name
        names[f"{role}_thinking"] = thinking
        if openai_key and fb_name:
            backup = _openai(fb_name, "low" if role == "worker" else "medium", openai_key, max_tokens)
            built[role] = FallbackModel(primary, backup, fallback_on=should_fall_back)
            names[f"{role}_fallback"] = fb_name
        else:
            built[role] = primary
    return AgentModels(lead=built["lead"], worker=built["worker"], names=names)
