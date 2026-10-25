import pytest
from pydantic import BaseModel

from engine import prompts
from engine.llm import LLM, BadOutputError, Completion, LLMConfig, LLMError, RetryableError


class Answer(BaseModel):
    value: int


class ScriptedBackend:
    """Raises or answers in the order given, and remembers what it was sent."""

    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def call(self, messages, tier, schema, step=""):
        self.calls.append((messages, tier, schema, step))
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def ok(value=1):
    return Completion(text=f'{{"value": {value}}}', parsed=Answer(value=value), prompt_tokens=10,
                      completion_tokens=5, model="m")  # fmt: skip


def make(knowledge, script):
    records = []
    llm = LLM(LLMConfig(provider="openai", api_key="x"), knowledge, ScriptedBackend(script), records.append)
    llm.retry_sleep = 0
    return llm, records


def test_config_defaults_and_providers():
    config = LLMConfig.from_env({})
    assert config.provider == "openai" and config.model_small == "gpt-5.4-mini"
    assert config.temperature is None and config.effort_small == "none" and config.effort_strong == "low"
    gemini = LLMConfig.from_env({"LLM_PROVIDER": "gemini"})
    assert gemini.base_url.startswith("https://generativelanguage.googleapis.com")
    assert (gemini.model_small, gemini.model_strong) == ("gemini-3.1-flash-lite", "gemini-3.8-flash")
    custom = LLMConfig.from_env({"LLM_MODEL": "a", "LLM_MODEL_STRONG": "b", "LLM_REASONING_EFFORT": ""})
    assert (custom.model_small, custom.model_strong, custom.effort_small) == ("a", "b", None)
    assert LLMConfig.from_env({"LLM_TEMPERATURE": "0.2"}).temperature == 0.2


def test_cost_only_with_both_prices():
    assert LLMConfig().cost(1000, 1000) is None
    priced = LLMConfig(price_input_per_m=1.0, price_output_per_m=2.0)
    assert priced.cost(1_000_000, 500_000) == pytest.approx(2.0)


def test_openai_backend_does_not_send_temperature_by_default():
    from engine.llm import OpenAIBackend

    backend = OpenAIBackend(LLMConfig(api_key="x"))
    assert backend.options("small") == {"reasoning_effort": "none"}
    assert backend.options("strong") == {"reasoning_effort": "low"}
    backend = OpenAIBackend(LLMConfig(api_key="x", temperature=0.3, effort_small=None))
    assert backend.options("small") == {"temperature": 0.3}


def test_persona_is_the_system_message_and_calls_are_recorded(knowledge):
    llm, records = make(knowledge, [ok(7)])
    assert llm.complete_json("demo", Answer, "hello").value == 7
    messages = llm.backend.calls[0][0]
    assert messages[0] == {"role": "system", "content": knowledge.persona}
    assert messages[1]["content"] == "hello"
    assert records[0]["step"] == "demo" and records[0]["status"] == "ok" and records[0]["prompt_tokens"] == 10


def test_two_retries_on_transient_errors(knowledge):
    llm, records = make(knowledge, [RetryableError("429"), RetryableError("503"), ok()])
    assert llm.complete_json("s", Answer, "u").value == 1
    assert [r["status"] for r in records] == ["error", "error", "ok"]
    llm, _ = make(knowledge, [RetryableError("1"), RetryableError("2"), RetryableError("3")])
    with pytest.raises(LLMError):
        llm.complete_json("s", Answer, "u")


def test_one_retry_on_bad_output(knowledge):
    llm, _ = make(knowledge, [BadOutputError("refusal"), ok()])
    assert llm.complete_json("s", Answer, "u").value == 1
    llm, _ = make(knowledge, [BadOutputError("refusal"), BadOutputError("again")])
    with pytest.raises(LLMError):
        llm.complete_json("s", Answer, "u")


def test_map_keeps_order_and_isolates_failures(fake_llm):
    def work(x):
        if x == 2:
            raise ValueError("boom")
        return x * 10

    results = fake_llm.map(work, [1, 2, 3])
    assert results[0] == 10 and results[2] == 30 and isinstance(results[1], ValueError)


def test_prose_prompts_append_the_style_rules(fake_llm, knowledge):
    text = fake_llm.prose_prompt("reminder_aims", n_aims=1, n_hypotheses=2, n_questions=3)
    assert text.endswith("</writing_rules>") and knowledge.style_rules.strip()[:30] in text


def test_render_refuses_missing_placeholders():
    with pytest.raises(prompts.PromptError):
        prompts.render("reminder_aims", n_aims=1)


def test_every_prompt_file_loads():
    names = sorted(p.stem for p in prompts.PROMPTS_DIR.glob("*.md"))
    assert "screening" in names and "background_paragraph" in names
    for name in names:
        assert prompts.placeholders(name) or name.startswith("reminder")
