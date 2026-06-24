"""Gateway behaviour: stamping, JSON recovery, prompt registry, rate limiting,
usage attribution, provider selection, and the no-`claude -p`-in-production rule.
"""

from __future__ import annotations

import pathlib

import pytest

from services.llm import prompts
from services.llm.base import (
    API_MODEL_ALIASES,
    LLMError,
    LLMRateLimitError,
    Provider,
    ProviderResponse,
    TokenUsage,
)
from services.llm.gateway import _make_provider, _parse_json
from services.llm.providers.anthropic_api import AnthropicAPIProvider
from services.llm.providers.cli import CLIProvider

from .conftest import RecordingProvider


# --------------------------------------------------------------------------- #
# complete_json: stamping + JSON recovery
# --------------------------------------------------------------------------- #

def test_complete_json_returns_parsed_dict(make_gateway):
    gw = make_gateway(RecordingProvider(text='{"intent": "interested"}'))
    data = gw.complete_json(prompt="response_parser", user="yes!")
    assert data == {"intent": "interested"}


def test_complete_json_with_meta_stamps_prompt_model_and_usage(make_gateway, _isolate_usage_sinks):
    provider = RecordingProvider(text='{"ok": true}', model_version="claude-sonnet-4-6")
    gw = make_gateway(provider)
    result = gw.complete_json(prompt="response_parser", user="hello", with_meta=True)

    assert result.prompt_name == "response_parser"
    assert result.prompt_version == prompts.get("response_parser").version
    assert result.prompt_hash == prompts.get("response_parser").hash
    assert result.model_version == "claude-sonnet-4-6"
    assert result.provider == "anthropic_api"
    assert result.tenant_id == "default"
    assert result.usage.input_tokens == 100
    assert result.usage.output_tokens == 20
    assert len(result.input_hash) == 64  # sha256 hex

    # The same call was recorded to the usage sink exactly once.
    captured = _isolate_usage_sinks
    assert len(captured) == 1
    recorded_result, cost = captured[0]
    assert recorded_result.prompt_name == "response_parser"
    assert cost > 0  # sonnet pricing applied


@pytest.mark.parametrize(
    "raw",
    [
        '{"a": 1}',
        '```json\n{"a": 1}\n```',
        'Here is the result:\n{"a": 1}\nHope that helps!',
        '{"a": 1,}',  # trailing comma
    ],
)
def test_json_recovery_is_robust(raw):
    assert _parse_json(raw) == {"a": 1}


def test_json_recovery_raises_when_no_object():
    with pytest.raises(LLMError):
        _parse_json("no json here at all")


# --------------------------------------------------------------------------- #
# Prompt registry
# --------------------------------------------------------------------------- #

def test_prompt_default_model_is_used(make_gateway):
    # outreach.proceed pins haiku; no explicit model passed.
    provider = RecordingProvider(text='{"subject": "s", "body": "b"}')
    gw = make_gateway(provider, default_model="sonnet")
    gw.complete_json(prompt="outreach.proceed", user="{}")
    assert provider.calls[-1]["model"] == "haiku"


def test_explicit_model_overrides_prompt_default(make_gateway):
    provider = RecordingProvider(text='{"subject": "s", "body": "b"}')
    gw = make_gateway(provider)
    gw.complete_json(prompt="outreach.proceed", user="{}", model="opus")
    assert provider.calls[-1]["model"] == "opus"


def test_prompt_default_max_tokens_is_used(make_gateway):
    provider = RecordingProvider()
    gw = make_gateway(provider)
    gw.complete_json(prompt="jd_generator", user="x")
    # jd_generator template sets default_max_tokens=2000
    assert provider.calls[-1]["max_tokens"] == 2000


def test_unknown_prompt_raises():
    with pytest.raises(KeyError):
        prompts.get("does.not.exist")


def test_adhoc_system_path(make_gateway):
    provider = RecordingProvider()
    gw = make_gateway(provider)
    result = gw.complete(system="be terse", user="hi", with_meta=True)
    assert result.prompt_name == "adhoc"
    assert result.prompt_version == 0
    assert provider.calls[-1]["system"] == "be terse"


def test_call_requires_prompt_or_system(make_gateway):
    gw = make_gateway(RecordingProvider())
    with pytest.raises(LLMError):
        gw.complete(user="hi")


# --------------------------------------------------------------------------- #
# Rate limiting
# --------------------------------------------------------------------------- #

def test_rate_limit_raises_after_cap(make_gateway):
    gw = make_gateway(RecordingProvider(), per_minute=2)
    gw.complete(prompt="response_parser", user="1", tenant_id="acme")
    gw.complete(prompt="response_parser", user="2", tenant_id="acme")
    with pytest.raises(LLMRateLimitError):
        gw.complete(prompt="response_parser", user="3", tenant_id="acme")


def test_rate_limit_is_per_tenant(make_gateway):
    gw = make_gateway(RecordingProvider(), per_minute=1)
    gw.complete(prompt="response_parser", user="1", tenant_id="acme")
    # Different tenant has its own window.
    gw.complete(prompt="response_parser", user="1", tenant_id="globex")
    with pytest.raises(LLMRateLimitError):
        gw.complete(prompt="response_parser", user="2", tenant_id="acme")


# --------------------------------------------------------------------------- #
# Concurrency helper
# --------------------------------------------------------------------------- #

def test_parallel_runs_all_calls(make_gateway):
    gw = make_gateway(RecordingProvider(text='{"n": 1}'))
    results = gw.parallel(
        [lambda i=i: gw.complete_json(prompt="response_parser", user=str(i)) for i in range(5)]
    )
    assert results == [{"n": 1}] * 5


# --------------------------------------------------------------------------- #
# Provider selection (config-only switching)
# --------------------------------------------------------------------------- #

def test_make_provider_api():
    assert isinstance(_make_provider("api"), AnthropicAPIProvider)


def test_make_provider_cli():
    assert isinstance(_make_provider("cli"), CLIProvider)


def test_make_provider_unknown_raises():
    with pytest.raises(LLMError):
        _make_provider("bogus")


def test_alias_resolution_maps_to_current_model_ids():
    p = AnthropicAPIProvider()
    assert p._resolve_model("sonnet") == "claude-sonnet-4-6"
    assert p._resolve_model("opus") == "claude-opus-4-8"
    assert p._resolve_model("haiku") == "claude-haiku-4-5"
    # A full id passes through untouched.
    assert p._resolve_model("claude-opus-4-8") == "claude-opus-4-8"
    assert set(API_MODEL_ALIASES) == {"opus", "sonnet", "haiku"}


# --------------------------------------------------------------------------- #
# Anthropic API provider call shape + usage extraction (stubbed client)
# --------------------------------------------------------------------------- #

class _FakeBlock:
    type = "text"

    def __init__(self, text):
        self.text = text


class _FakeUsage:
    input_tokens = 321
    output_tokens = 54
    cache_read_input_tokens = 0
    cache_creation_input_tokens = 0


class _FakeResponse:
    stop_reason = "end_turn"
    model = "claude-sonnet-4-6"
    content = [_FakeBlock("hello world")]
    usage = _FakeUsage()


class _FakeClient:
    def __init__(self):
        self.captured = {}

    class _Messages:
        def __init__(self, outer):
            self.outer = outer

        def create(self, **kwargs):
            self.outer.captured = kwargs
            return _FakeResponse()

    @property
    def messages(self):
        return _FakeClient._Messages(self)


def test_api_provider_builds_request_and_extracts_usage(monkeypatch):
    provider = AnthropicAPIProvider()
    fake = _FakeClient()
    monkeypatch.setattr(provider, "_get_client", lambda: fake)

    resp = provider.complete("sys", "usr", max_tokens=600, model="sonnet")

    assert resp.text == "hello world"
    assert resp.model_version == "claude-sonnet-4-6"
    assert resp.usage.input_tokens == 321
    assert resp.usage.output_tokens == 54
    # alias resolved, system + messages shaped correctly
    assert fake.captured["model"] == "claude-sonnet-4-6"
    assert fake.captured["system"] == "sys"
    assert fake.captured["max_tokens"] == 600
    assert fake.captured["messages"] == [{"role": "user", "content": "usr"}]


def test_api_provider_unconfigured_is_unavailable(monkeypatch):
    from services import config

    monkeypatch.setattr(config.settings, "anthropic_api_key", "")
    assert AnthropicAPIProvider().available() is False


# --------------------------------------------------------------------------- #
# Engineering Rule 1: no production code path invokes `claude -p`
# --------------------------------------------------------------------------- #

def test_no_claude_dash_p_outside_cli_provider():
    """The headless CLI invocation (a quoted `-p` subprocess arg) must exist
    ONLY in the dev-only CLI provider — never in agents, routers, services, or
    the gateway. (Prose mentions of `claude -p` in comments/docstrings are fine;
    we look for the actual code token, not the words.)"""
    import re

    backend = pathlib.Path(__file__).resolve().parents[1]
    allowed = {backend / "services" / "llm" / "providers" / "cli.py"}
    token = re.compile(r"""["']-p["']""")  # a quoted "-p" / '-p' arg-list element
    offenders = []
    for path in backend.rglob("*.py"):
        parts = set(path.parts)
        if ".venv" in parts or "tests" in parts:
            continue
        if path in allowed:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if token.search(text):
            offenders.append(str(path.relative_to(backend)))
    assert offenders == [], f"headless `-p` invocation found outside the CLI provider: {offenders}"


def test_default_provider_is_api():
    from services import config

    # The shipped default selects the API (API-key auth) provider.
    assert config.Settings.model_fields["llm_provider"].default == "api"
