"""Tests for orchestrate.py - pipeline orchestration."""

import json
import os
import subprocess as real_subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from orchestrate import check_prerequisites, main, run_step


class TestCheckPrerequisites:
    """Tests for prerequisite checking."""

    @patch("subprocess.run")
    @patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"})
    def test_all_pass_claude_ollama(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)

        with patch.dict("sys.modules", {"ollama": MagicMock()}):
            issues = check_prerequisites("claude", "ollama")

        assert issues == []

    @patch("subprocess.run")
    @patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test"})
    def test_ollama_not_running(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        mock_ollama = MagicMock()
        mock_ollama.list.side_effect = Exception("connection refused")

        with patch.dict("sys.modules", {"ollama": mock_ollama}):
            issues = check_prerequisites("claude", "ollama")

        assert any("Ollama" in i for i in issues)

    @patch("subprocess.run")
    def test_missing_api_key(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        env = os.environ.copy()
        env.pop("ANTHROPIC_API_KEY", None)

        with patch.dict(os.environ, env, clear=True):
            issues = check_prerequisites("claude", "ollama")

        assert any("ANTHROPIC_API_KEY" in i for i in issues)

    @patch("subprocess.run")
    @patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"})
    def test_local_server_not_running(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        mock_requests = MagicMock()
        mock_requests.get.side_effect = Exception("connection refused")

        with patch.dict("sys.modules", {"requests": mock_requests}):
            issues = check_prerequisites("local", "local")

        assert any("Local LLM server" in i for i in issues)

    @patch("subprocess.run", side_effect=FileNotFoundError("age not found"))
    @patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"})
    def test_age_not_found_no_issue(self, mock_run):
        """age is optional - not finding it should not add an issue."""
        with patch.dict("sys.modules", {"ollama": MagicMock()}):
            issues = check_prerequisites("claude", "ollama")

        # age is optional, so no issue about it
        assert not any("age" in i.lower() for i in issues)

    @patch("subprocess.run")
    @patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"})
    def test_no_pdf_library(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)

        original_import = __builtins__.__import__ if hasattr(__builtins__, '__import__') else __import__

        def mock_import(name, *args, **kwargs):
            if name == "fitz":
                raise ImportError("No module named 'fitz'")
            if name == "fillpdf":
                raise ImportError("No module named 'fillpdf'")
            return original_import(name, *args, **kwargs)

        with patch.dict("sys.modules", {"ollama": MagicMock()}):
            with patch("builtins.__import__", side_effect=mock_import):
                issues = check_prerequisites("claude", "ollama")

        assert any("PDF" in i for i in issues)


class TestRunStep:
    """Tests for run_step subprocess wrapper."""

    @patch("subprocess.run")
    def test_success(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        assert run_step("test", ["echo", "hi"]) is True

    @patch("subprocess.run")
    def test_failure_returncode(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1)
        assert run_step("test", ["false"]) is False

    @patch("subprocess.run")
    def test_exception(self, mock_run):
        mock_run.side_effect = Exception("boom")
        assert run_step("test", ["bad"]) is False

    @patch("subprocess.run")
    def test_env_merged(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        run_step("test", ["cmd"], env={"EXTRA": "1"})

        call_kwargs = mock_run.call_args
        env_passed = call_kwargs.kwargs.get("env") or call_kwargs[1].get("env")
        assert "EXTRA" in env_passed
        assert env_passed["EXTRA"] == "1"


class TestOrchestrateMain:
    """Tests for the main CLI entry point."""

    @patch("orchestrate.load_config")
    @patch("orchestrate.check_prerequisites", return_value=["Ollama not running"])
    def test_prereqs_fail_exits(self, mock_prereqs, mock_config, test_config):
        mock_config.return_value = test_config
        runner = CliRunner()
        result = runner.invoke(main, ["--year", "2025", "--non-interactive"])
        assert result.exit_code != 0
        assert "Prerequisites not met" in result.output

    @patch("orchestrate.load_config")
    @patch("orchestrate.check_prerequisites", return_value=[])
    def test_missing_sources_dir(self, mock_prereqs, mock_config, test_config):
        mock_config.return_value = test_config
        runner = CliRunner()
        result = runner.invoke(main, ["--year", "9999", "--non-interactive"])
        assert result.exit_code != 0
        assert "Sources directory not found" in result.output

    @patch("orchestrate.load_config")
    @patch("orchestrate.check_prerequisites", return_value=[])
    def test_missing_passphrase_non_interactive(self, mock_prereqs, mock_config, tmp_path, test_config):
        mock_config.return_value = test_config
        sources = tmp_path / "data" / "raw" / "2025" / "sources"
        sources.mkdir(parents=True)
        (sources / "w2.pdf").write_bytes(b"%PDF")

        test_config["paths"]["raw_documents"] = str(tmp_path / "data" / "raw")

        runner = CliRunner()
        env = os.environ.copy()
        env.pop("VAULT_PASSPHRASE", None)
        result = runner.invoke(main, ["--year", "2025", "--non-interactive"], env=env)
        assert result.exit_code != 0
        assert "VAULT_PASSPHRASE" in result.output

    @patch("orchestrate.run_step", return_value=True)
    @patch("orchestrate.load_config")
    @patch("orchestrate.check_prerequisites", return_value=[])
    def test_full_pipeline(self, mock_prereqs, mock_config, mock_run_step, tmp_path, test_config):
        sources = tmp_path / "data" / "raw" / "2025" / "sources"
        sources.mkdir(parents=True)
        (sources / "w2.pdf").write_bytes(b"%PDF")

        test_config["paths"]["raw_documents"] = str(tmp_path / "data" / "raw")
        test_config["paths"]["extracted_data"] = str(tmp_path / "data" / "extracted")
        test_config["paths"]["sanitized_data"] = str(tmp_path / "data" / "sanitized")
        test_config["paths"]["vault"] = str(tmp_path / "data" / "vault")
        test_config["paths"]["instructions"] = str(tmp_path / "data" / "instructions")
        test_config["paths"]["output"] = str(tmp_path / "data" / "output")
        test_config["paths"]["blank_forms"] = str(tmp_path / "templates")
        mock_config.return_value = test_config

        runner = CliRunner()
        result = runner.invoke(
            main,
            ["--year", "2025", "--non-interactive"],
            env={**os.environ, "VAULT_PASSPHRASE": "testpass"},
        )
        assert result.exit_code == 0
        assert mock_run_step.call_count >= 3

    @patch("orchestrate.run_step", return_value=True)
    @patch("orchestrate.load_config")
    @patch("orchestrate.check_prerequisites", return_value=[])
    def test_skip_flags(self, mock_prereqs, mock_config, mock_run_step, tmp_path, test_config):
        sources = tmp_path / "data" / "raw" / "2025" / "sources"
        sources.mkdir(parents=True)
        (sources / "w2.pdf").write_bytes(b"%PDF")

        test_config["paths"]["raw_documents"] = str(tmp_path / "data" / "raw")
        test_config["paths"]["extracted_data"] = str(tmp_path / "data" / "extracted")
        test_config["paths"]["sanitized_data"] = str(tmp_path / "data" / "sanitized")
        test_config["paths"]["vault"] = str(tmp_path / "data" / "vault")
        test_config["paths"]["instructions"] = str(tmp_path / "data" / "instructions")
        test_config["paths"]["output"] = str(tmp_path / "data" / "output")
        test_config["paths"]["blank_forms"] = str(tmp_path / "templates")
        mock_config.return_value = test_config

        inst_dir = tmp_path / "data" / "instructions"
        inst_dir.mkdir(parents=True)
        (inst_dir / "2025.json").write_text('{"forms_needed": []}')

        runner = CliRunner()
        result = runner.invoke(
            main,
            ["--year", "2025", "--non-interactive", "--skip-extract", "--skip-sanitize", "--skip-process"],
            env={**os.environ, "VAULT_PASSPHRASE": "testpass"},
        )
        assert mock_run_step.call_count == 1
        call_args = mock_run_step.call_args[0]
        assert "Assemble" in call_args[0]
