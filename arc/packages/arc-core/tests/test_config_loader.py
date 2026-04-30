"""Tests for arc.core.config_loader.

The loader's contract is small but precise:
  - Loads `.env` from a given path or by walking parents from CWD.
  - Idempotent: re-running doesn't override existing env values.
  - Shell wins: pre-existing os.environ keys are never replaced.
  - Silent no-op when no `.env` file is present (production-safe default).
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from arc.core.config_loader import load_env_file


# ── Explicit-path mode ──────────────────────────────────────────────────────


class TestExplicitPath:
    def test_loads_values_from_given_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        env = tmp_path / ".env"
        env.write_text("ARC_TEST_FOO=hello\nARC_TEST_BAR=42\n")
        monkeypatch.delenv("ARC_TEST_FOO", raising=False)
        monkeypatch.delenv("ARC_TEST_BAR", raising=False)

        result = load_env_file(env)

        assert result == env
        assert os.environ["ARC_TEST_FOO"] == "hello"
        assert os.environ["ARC_TEST_BAR"] == "42"

    def test_returns_none_when_file_missing(self, tmp_path: Path):
        result = load_env_file(tmp_path / "nope.env")
        assert result is None

    def test_explicit_path_skips_parent_walk(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """An explicit path means *only* that path — never walk parents."""
        # Place a .env in tmp_path; pass a different file that doesn't exist.
        (tmp_path / ".env").write_text("ARC_TEST_FROM_PARENT=should-not-be-loaded\n")
        monkeypatch.delenv("ARC_TEST_FROM_PARENT", raising=False)

        result = load_env_file(tmp_path / "different.env")

        assert result is None
        assert "ARC_TEST_FROM_PARENT" not in os.environ


# ── Parent-walk mode (no path → walk up from CWD) ──────────────────────────


class TestParentWalk:
    def test_finds_dotenv_in_cwd(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        env = tmp_path / ".env"
        env.write_text("ARC_TEST_WALK=found\n")
        monkeypatch.delenv("ARC_TEST_WALK", raising=False)
        monkeypatch.chdir(tmp_path)

        result = load_env_file()

        assert result is not None
        assert result.resolve() == env.resolve()
        assert os.environ["ARC_TEST_WALK"] == "found"

    def test_finds_dotenv_in_parent_directory(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        env = tmp_path / ".env"
        env.write_text("ARC_TEST_PARENT=walked-up\n")
        nested = tmp_path / "a" / "b" / "c"
        nested.mkdir(parents=True)
        monkeypatch.delenv("ARC_TEST_PARENT", raising=False)
        monkeypatch.chdir(nested)

        result = load_env_file()

        assert result is not None
        assert result.resolve() == env.resolve()
        assert os.environ["ARC_TEST_PARENT"] == "walked-up"

    def test_returns_none_when_no_dotenv_anywhere(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        nested = tmp_path / "isolated"
        nested.mkdir()
        monkeypatch.chdir(nested)

        # Constrain depth so we don't accidentally find a developer's
        # real .env higher up on their filesystem.
        result = load_env_file(search_parents=2)

        assert result is None

    def test_search_depth_zero_only_checks_cwd(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        (tmp_path / ".env").write_text("ARC_TEST_SHOULD_NOT_LOAD=x\n")
        nested = tmp_path / "deeper"
        nested.mkdir()
        monkeypatch.delenv("ARC_TEST_SHOULD_NOT_LOAD", raising=False)
        monkeypatch.chdir(nested)

        result = load_env_file(search_parents=0)

        assert result is None
        assert "ARC_TEST_SHOULD_NOT_LOAD" not in os.environ


# ── Precedence: shell always wins ──────────────────────────────────────────


class TestPrecedence:
    def test_shell_var_beats_dotenv(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """`.env` says one thing; the shell already says another. Shell wins."""
        (tmp_path / ".env").write_text("ARC_TEST_PRECEDENCE=from-dotenv\n")
        monkeypatch.setenv("ARC_TEST_PRECEDENCE", "from-shell")
        monkeypatch.chdir(tmp_path)

        load_env_file()

        assert os.environ["ARC_TEST_PRECEDENCE"] == "from-shell"

    def test_idempotent_second_call_doesnt_clobber(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """Re-running the loader is safe: a value loaded the first time
        from .env is still there on the second call (and not re-loaded
        if the user has since changed it in os.environ)."""
        (tmp_path / ".env").write_text("ARC_TEST_IDEMPOTENT=v1\n")
        monkeypatch.delenv("ARC_TEST_IDEMPOTENT", raising=False)
        monkeypatch.chdir(tmp_path)

        load_env_file()
        assert os.environ["ARC_TEST_IDEMPOTENT"] == "v1"

        # Caller mutates os.environ after first load (e.g. via shell-export)
        os.environ["ARC_TEST_IDEMPOTENT"] = "v2"

        load_env_file()  # second call
        # Mutation survives — .env doesn't override.
        assert os.environ["ARC_TEST_IDEMPOTENT"] == "v2"


# ── Production safety: missing .env is a silent no-op ─────────────────────


class TestProductionSafety:
    def test_no_dotenv_anywhere_doesnt_raise(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """Production case: no .env file exists. Loader must not raise —
        Lambda / ECS / Bedrock Agents get env from the platform's task
        config layer, not from a filesystem .env."""
        empty = tmp_path / "no-dotenv-here"
        empty.mkdir()
        monkeypatch.chdir(empty)

        # Should be a quiet None, not an exception.
        result = load_env_file(search_parents=2)
        assert result is None
