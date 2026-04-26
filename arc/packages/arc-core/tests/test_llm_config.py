"""
Tests for arc.core.llm.LLMConfig + the resolve_llm() precedence resolver.

LLMConfig is the platform-default + per-manifest provider spec; resolve_llm
implements the precedence stack:

    explicit (with_llm)  >  manifest.llm  >  platform default  >  None

These tests cover the dataclass round-trips and the resolver. They do NOT
exercise the actual provider clients — those live in arc-connectors and
have their own test file.
"""

from __future__ import annotations

import sys
from typing import Any
from unittest.mock import MagicMock

import pytest

from arc.core import LLMClient, LLMConfig, resolve_llm


# ── LLMConfig dataclass ─────────────────────────────────────────────────────


class TestLLMConfigDataclass:
    def test_default_is_empty(self):
        cfg = LLMConfig()
        assert cfg.is_empty()
        assert cfg.build_client() is None

    def test_to_dict_omits_empty_fields(self):
        # Even if you set provider, fields that match defaults stay out.
        cfg = LLMConfig(provider="bedrock")
        d = cfg.to_dict()
        assert d == {"provider": "bedrock"}
        # max_retries==3 is the default; it shouldn't appear
        assert "max_retries" not in d

    def test_to_dict_includes_overridden_max_retries(self):
        cfg = LLMConfig(provider="bedrock", max_retries=5)
        d = cfg.to_dict()
        assert d["max_retries"] == 5

    def test_to_dict_includes_litellm_specifics(self):
        cfg = LLMConfig(
            provider="litellm",
            model="anthropic/claude-3-5-sonnet-20241022",
            fallback_models=["openai/gpt-4o-mini"],
            api_base="https://my-litellm.example.com",
        )
        d = cfg.to_dict()
        assert d["provider"] == "litellm"
        assert d["model"] == "anthropic/claude-3-5-sonnet-20241022"
        assert d["fallback_models"] == ["openai/gpt-4o-mini"]
        assert d["api_base"] == "https://my-litellm.example.com"

    def test_from_dict_round_trips(self):
        original = LLMConfig(
            provider="litellm",
            model="openai/gpt-4o",
            fallback_models=["anthropic/claude-3-5-sonnet"],
            max_retries=5,
        )
        rebuilt = LLMConfig.from_dict(original.to_dict())
        assert rebuilt == original

    def test_from_dict_ignores_unknown_keys(self):
        # Forward-compat: a manifest could carry future fields we don't
        # know about yet. We should ignore them, not crash.
        cfg = LLMConfig.from_dict({
            "provider": "bedrock",
            "model": "anthropic.claude-3-5-sonnet-20241022-v2:0",
            "future_field": "future_value",
        })
        assert cfg.provider == "bedrock"
        assert cfg.model    == "anthropic.claude-3-5-sonnet-20241022-v2:0"

    def test_from_dict_lowercases_provider(self):
        cfg = LLMConfig.from_dict({"provider": "BEDROCK"})
        assert cfg.provider == "bedrock"


# ── from_env ────────────────────────────────────────────────────────────────


class TestFromEnv:
    def test_unset_returns_empty(self, monkeypatch: pytest.MonkeyPatch):
        # Ensure no LLM vars are set
        for var in [
            "ARC_LLM_PROVIDER", "ARC_LLM_MODEL", "ARC_LLM_REGION",
            "ARC_LLM_FALLBACK_MODELS", "ARC_LLM_API_BASE", "ARC_LLM_MAX_RETRIES",
            "AWS_REGION",
        ]:
            monkeypatch.delenv(var, raising=False)

        cfg = LLMConfig.from_env()
        assert cfg.is_empty()
        assert cfg.build_client() is None

    def test_bedrock_provider_from_env(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("ARC_LLM_PROVIDER", "bedrock")
        monkeypatch.setenv("ARC_LLM_MODEL", "anthropic.claude-3-5-sonnet-20241022-v2:0")
        monkeypatch.setenv("ARC_LLM_REGION", "us-west-2")

        cfg = LLMConfig.from_env()
        assert cfg.provider == "bedrock"
        assert cfg.model    == "anthropic.claude-3-5-sonnet-20241022-v2:0"
        assert cfg.region   == "us-west-2"

    def test_aws_region_fallback(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("ARC_LLM_PROVIDER", "bedrock")
        monkeypatch.delenv("ARC_LLM_REGION", raising=False)
        monkeypatch.setenv("AWS_REGION", "eu-west-1")

        cfg = LLMConfig.from_env()
        assert cfg.region == "eu-west-1"

    def test_litellm_with_fallbacks(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("ARC_LLM_PROVIDER", "litellm")
        monkeypatch.setenv("ARC_LLM_MODEL", "anthropic/claude-3-5-sonnet-20241022")
        monkeypatch.setenv(
            "ARC_LLM_FALLBACK_MODELS",
            "openai/gpt-4o-mini, anthropic/claude-3-haiku-20240307",
        )
        monkeypatch.setenv("ARC_LLM_API_BASE", "https://litellm.internal/v1")

        cfg = LLMConfig.from_env()
        assert cfg.provider == "litellm"
        assert cfg.fallback_models == [
            "openai/gpt-4o-mini",
            "anthropic/claude-3-haiku-20240307",
        ]
        assert cfg.api_base == "https://litellm.internal/v1"


# ── build_client ────────────────────────────────────────────────────────────


class TestBuildClient:
    def test_empty_returns_none(self):
        assert LLMConfig().build_client() is None

    def test_bedrock_constructs_a_client(self):
        # No actual boto3 call happens at construction — the client lazy-
        # inits its boto3 handle on first invocation.
        cfg = LLMConfig(
            provider="bedrock",
            model="anthropic.claude-3-5-sonnet-20241022-v2:0",
            region="us-east-1",
        )
        client = cfg.build_client()
        assert client is not None
        assert isinstance(client, LLMClient)
        assert client.model_id == "anthropic.claude-3-5-sonnet-20241022-v2:0"
        assert client.region   == "us-east-1"

    def test_litellm_requires_model(self):
        cfg = LLMConfig(provider="litellm")  # model is empty
        with pytest.raises(ValueError, match="model"):
            cfg.build_client()

    def test_litellm_constructs_with_fallbacks(self, monkeypatch: pytest.MonkeyPatch):
        # Don't try to import litellm itself — just verify the client class
        # is constructed with the right kwargs. We stub litellm so the lazy
        # import succeeds.
        monkeypatch.setitem(sys.modules, "litellm", MagicMock())
        cfg = LLMConfig(
            provider="litellm",
            model="anthropic/claude-3-5-sonnet-20241022",
            fallback_models=["openai/gpt-4o-mini"],
            api_base="https://my-proxy.example",
        )
        client = cfg.build_client()
        assert client is not None
        assert isinstance(client, LLMClient)
        # Verify the fields wired through — these are LiteLLMClient attrs
        assert client.model            == "anthropic/claude-3-5-sonnet-20241022"
        assert client.fallback_models  == ["openai/gpt-4o-mini"]
        assert client.api_base         == "https://my-proxy.example"

    def test_unknown_provider_raises(self):
        cfg = LLMConfig(provider="acme-corp")
        with pytest.raises(ValueError, match="Unknown LLM provider"):
            cfg.build_client()


# ── resolve_llm — the precedence stack ──────────────────────────────────────


class _FakeLLM:
    """A stand-in LLMClient that satisfies the structural protocol."""
    label: str

    def __init__(self, label: str) -> None:
        self.label = label

    async def generate(self, **_kw: Any) -> str: return self.label
    async def generate_json(self, **_kw: Any) -> dict: return {"label": self.label}


class TestResolveLLM:
    def test_explicit_wins_over_manifest_and_default(self):
        explicit = _FakeLLM("explicit")
        manifest_cfg = LLMConfig(provider="bedrock")
        platform_cfg = LLMConfig(provider="bedrock")

        result = resolve_llm(
            explicit=explicit,
            manifest_config=manifest_cfg,
            platform_default=platform_cfg,
        )
        assert result is explicit

    def test_manifest_wins_over_platform_default(self):
        manifest_cfg = LLMConfig(
            provider="bedrock",
            model="anthropic.claude-3-5-sonnet-20241022-v2:0",
        )
        platform_cfg = LLMConfig(
            provider="bedrock",
            model="anthropic.claude-3-haiku-20240307-v1:0",
        )

        result = resolve_llm(
            explicit=None,
            manifest_config=manifest_cfg,
            platform_default=platform_cfg,
        )
        assert result is not None
        # Manifest wins — the model id is the manifest's, not the platform's.
        assert result.model_id == "anthropic.claude-3-5-sonnet-20241022-v2:0"

    def test_platform_default_used_when_manifest_omits(self):
        platform_cfg = LLMConfig(
            provider="bedrock",
            model="anthropic.claude-3-5-sonnet-20241022-v2:0",
        )

        result = resolve_llm(
            explicit=None,
            manifest_config=None,
            platform_default=platform_cfg,
        )
        assert result is not None
        assert result.model_id == "anthropic.claude-3-5-sonnet-20241022-v2:0"

    def test_empty_manifest_falls_through_to_platform_default(self):
        # An empty LLMConfig in the manifest (e.g., provider="" but the key
        # exists) should not block the platform default.
        empty_manifest = LLMConfig()
        platform_cfg = LLMConfig(
            provider="bedrock",
            model="anthropic.claude-3-5-sonnet-20241022-v2:0",
        )

        result = resolve_llm(
            explicit=None,
            manifest_config=empty_manifest,
            platform_default=platform_cfg,
        )
        assert result is not None
        assert result.model_id == "anthropic.claude-3-5-sonnet-20241022-v2:0"

    def test_all_none_returns_none(self):
        assert resolve_llm() is None

    def test_empty_platform_default_returns_none(self):
        assert resolve_llm(platform_default=LLMConfig()) is None
