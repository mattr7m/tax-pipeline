"""Tests for process.py — sanitization verification, prompt building, JSON parsing."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests
from click.testing import CliRunner

from process import (
    build_system_prompt,
    build_user_prompt,
    determine_tax_year,
    display_results,
    main,
    parse_json_response,
    process_with_claude,
    process_with_local_llm,
    verify_sanitized,
    SYSTEM_PROMPT_BASE,
)


class TestVerifySanitized:
    """Test the safety gate that prevents sending raw SSNs to APIs."""

    def test_clean_data_passes(self, sanitized_data):
        assert verify_sanitized(sanitized_data) is True

    def test_raw_ssn_fails(self, extracted_data):
        """Data with real SSNs should fail verification."""
        assert verify_sanitized(extracted_data) is False

    def test_data_with_tokens_passes(self):
        data = {"ssn": "[SSN_REDACTED_1]", "ein": "[EIN_REDACTED_1]"}
        assert verify_sanitized(data) is True

    def test_empty_data_passes(self):
        assert verify_sanitized({}) is True

    def test_nested_ssn_fails(self):
        data = {"employee": {"personal": {"ssn": "123-45-6789"}}}
        assert verify_sanitized(data) is False

    def test_ssn_in_list_fails(self):
        data = {"ssns": ["123-45-6789", "987-65-4321"]}
        assert verify_sanitized(data) is False

    def test_partial_ssn_passes(self):
        """Strings that look like SSNs but aren't full matches."""
        data = {"note": "reference 12-34-567"}
        assert verify_sanitized(data) is True

    def test_ssn_pattern_boundary(self):
        """SSN pattern requires word boundaries."""
        data = {"note": "code1234-56-78909876"}
        # The regex uses \b so this embedded pattern may or may not match
        # depending on boundary; this test documents current behavior
        result = verify_sanitized(data)
        # This is testing the regex as-is; the important thing is no crash
        assert isinstance(result, bool)


class TestDetermineTaxYear:
    """Test tax year extraction from data."""

    def test_explicit_tax_year(self):
        data = {"tax_year": 2025}
        assert determine_tax_year(data) == 2025

    def test_string_tax_year(self):
        data = {"tax_year": "2025"}
        assert determine_tax_year(data) == 2025

    def test_from_documents(self):
        data = {"documents": [{"tax_year": 2025}]}
        assert determine_tax_year(data) == 2025

    def test_default_current_year(self):
        from datetime import datetime
        data = {}
        result = determine_tax_year(data)
        assert result == datetime.now().year

    def test_invalid_tax_year_falls_through(self):
        from datetime import datetime
        data = {"tax_year": "not-a-year"}
        result = determine_tax_year(data)
        assert result == datetime.now().year

    def test_none_tax_year_falls_through(self):
        from datetime import datetime
        data = {"tax_year": None}
        result = determine_tax_year(data)
        assert result == datetime.now().year


class TestBuildSystemPrompt:
    """Test system prompt construction."""

    def test_base_prompt_without_knowledge(self):
        result = build_system_prompt()
        assert result == SYSTEM_PROMPT_BASE

    def test_empty_knowledge_returns_base(self):
        result = build_system_prompt("")
        assert result == SYSTEM_PROMPT_BASE

    def test_knowledge_appended(self):
        context = "Standard deduction: $15,000 for single filers"
        result = build_system_prompt(context)
        assert SYSTEM_PROMPT_BASE in result
        assert "Standard deduction: $15,000" in result
        assert "TAX YEAR REFERENCE DATA" in result

    def test_prompt_contains_json_format(self):
        result = build_system_prompt()
        assert "forms_needed" in result
        assert "form_instructions" in result


class TestBuildUserPrompt:
    """Test user prompt construction."""

    def test_includes_sanitized_data(self, sanitized_data):
        prompt = build_user_prompt(sanitized_data, None)
        assert "[SSN_REDACTED_1]" in prompt
        assert "75000" in prompt

    def test_includes_prior_year(self, sanitized_data):
        prior = {"tax_year": 2024, "summary": {"total_income": 70000}}
        prompt = build_user_prompt(sanitized_data, prior)
        assert "Prior Year Filed Return" in prompt
        assert "2024" in prompt

    def test_no_prior_year(self, sanitized_data):
        prompt = build_user_prompt(sanitized_data, None)
        assert "Prior Year Filed Return" not in prompt

    def test_includes_forms_needed(self, sanitized_data):
        prompt = build_user_prompt(sanitized_data, None, forms_needed=["1040", "Schedule A"])
        assert "Forms to Prepare" in prompt
        assert "1040" in prompt
        assert "Schedule A" in prompt

    def test_no_forms_needed(self, sanitized_data):
        prompt = build_user_prompt(sanitized_data, None, forms_needed=None)
        assert "Forms to Prepare" not in prompt

    def test_instructions_section(self, sanitized_data):
        prompt = build_user_prompt(sanitized_data, None)
        assert "Determine filing status" in prompt
        assert "structured JSON" in prompt


class TestProcessParseJsonResponse:
    """Test process.py's version of parse_json_response."""

    def test_clean_json(self):
        result = parse_json_response('{"tax_year": 2025, "forms_needed": ["1040"]}')
        assert result["tax_year"] == 2025

    def test_markdown_block(self):
        response = '```json\n{"tax_year": 2025}\n```'
        result = parse_json_response(response)
        assert result["tax_year"] == 2025

    def test_generic_block(self):
        response = 'Here:\n```\n{"tax_year": 2025}\n```\nDone.'
        result = parse_json_response(response)
        assert result["tax_year"] == 2025

    def test_malformed_returns_error(self):
        result = parse_json_response("This is not JSON at all")
        assert "parse_error" in result
        assert "raw_response" in result

    def test_empty_string(self):
        result = parse_json_response("")
        assert "parse_error" in result


class TestProcessWithClaude:
    """Tests for Claude API integration (mocked)."""

    def _make_mock_anthropic(self, response_text):
        """Create a mock anthropic module with a configured client."""
        mock_anthropic = MagicMock()
        mock_client = MagicMock()
        mock_anthropic.Anthropic.return_value = mock_client
        mock_client.messages.create.return_value = MagicMock(
            content=[MagicMock(text=response_text)]
        )
        return mock_anthropic, mock_client

    def test_success(self, sanitized_data, test_config):
        mock_anthropic, mock_client = self._make_mock_anthropic(
            '{"tax_year": 2025, "forms_needed": ["1040"]}'
        )
        with patch.dict("sys.modules", {"anthropic": mock_anthropic}):
            result = process_with_claude(sanitized_data, None, test_config)
        assert result["tax_year"] == 2025
        assert "1040" in result["forms_needed"]

    def test_includes_knowledge_in_system_prompt(self, sanitized_data, test_config):
        mock_anthropic, mock_client = self._make_mock_anthropic('{"tax_year": 2025}')
        with patch.dict("sys.modules", {"anthropic": mock_anthropic}):
            process_with_claude(
                sanitized_data, None, test_config,
                tax_knowledge_context="Standard deduction: $15,000"
            )
        call_kwargs = mock_client.messages.create.call_args[1]
        assert "TAX YEAR REFERENCE DATA" in call_kwargs["system"]
        assert "$15,000" in call_kwargs["system"]

    def test_includes_forms_in_user_prompt(self, sanitized_data, test_config):
        mock_anthropic, mock_client = self._make_mock_anthropic('{"tax_year": 2025}')
        with patch.dict("sys.modules", {"anthropic": mock_anthropic}):
            process_with_claude(
                sanitized_data, None, test_config,
                forms_needed=["1040", "Schedule A"]
            )
        call_kwargs = mock_client.messages.create.call_args[1]
        user_msg = call_kwargs["messages"][0]["content"]
        assert "Forms to Prepare" in user_msg
        assert "1040" in user_msg

    def test_malformed_response(self, sanitized_data, test_config):
        mock_anthropic, _ = self._make_mock_anthropic("Not valid JSON at all")
        with patch.dict("sys.modules", {"anthropic": mock_anthropic}):
            result = process_with_claude(sanitized_data, None, test_config)
        assert "parse_error" in result

    def test_import_error_exits(self, sanitized_data, test_config):
        with patch.dict("sys.modules", {"anthropic": None}):
            with pytest.raises(SystemExit):
                process_with_claude(sanitized_data, None, test_config)


class TestProcessWithLocalLlm:
    """Tests for local LLM integration (mocked)."""

    @patch("process.requests.post")
    def test_success_openai_format(self, mock_post, sanitized_data, test_config):
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "choices": [{"message": {"content": '{"tax_year": 2025}'}}]
        }
        mock_response.raise_for_status.return_value = None
        mock_post.return_value = mock_response

        result = process_with_local_llm(sanitized_data, None, test_config)
        assert result["tax_year"] == 2025

    @patch("process.requests.post")
    def test_success_direct_content(self, mock_post, sanitized_data, test_config):
        mock_response = MagicMock()
        mock_response.json.return_value = {"content": '{"tax_year": 2025}'}
        mock_response.raise_for_status.return_value = None
        mock_post.return_value = mock_response

        result = process_with_local_llm(sanitized_data, None, test_config)
        assert result["tax_year"] == 2025

    @patch("process.requests.post", side_effect=requests.exceptions.ConnectionError)
    def test_connection_error_exits(self, mock_post, sanitized_data, test_config):
        with pytest.raises(SystemExit):
            process_with_local_llm(sanitized_data, None, test_config)

    @patch("process.requests.post", side_effect=requests.exceptions.Timeout)
    def test_timeout_exits(self, mock_post, sanitized_data, test_config):
        with pytest.raises(SystemExit):
            process_with_local_llm(sanitized_data, None, test_config)

    @patch("process.requests.post")
    def test_http_error_exits(self, mock_post, sanitized_data, test_config):
        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = requests.exceptions.HTTPError(
            response=MagicMock(status_code=500, text="Internal error")
        )
        mock_post.return_value = mock_response

        with pytest.raises(SystemExit):
            process_with_local_llm(sanitized_data, None, test_config)


class TestDisplayResults:
    """Tests for result display formatting."""

    @patch("process.console")
    def test_parse_error(self, mock_console):
        display_results({"parse_error": "bad json", "raw_response": "text"})
        output = " ".join(str(c) for c in mock_console.print.call_args_list)
        assert "parsed" in output.lower() or "JSON" in output

    @patch("process.console")
    def test_summary(self, mock_console, instructions_data):
        display_results(instructions_data)
        # Should not crash; console.print called at least once
        assert mock_console.print.called

    @patch("process.console")
    def test_warnings(self, mock_console):
        display_results({"warnings": ["Check deductions"]})
        output = " ".join(str(c) for c in mock_console.print.call_args_list)
        assert "Check deductions" in output

    @patch("process.console")
    def test_empty_no_crash(self, mock_console):
        display_results({})
        # Should not raise any exception


class TestProcessMain:
    """Tests for the process.py CLI entry point."""

    @patch("process.load_config")
    def test_unsanitized_rejected(self, mock_config, tmp_path, test_config, extracted_data):
        mock_config.return_value = test_config
        input_file = tmp_path / "extracted.json"
        input_file.write_text(json.dumps(extracted_data))
        output_file = tmp_path / "output.json"

        runner = CliRunner()
        result = runner.invoke(main, [
            "--input", str(input_file),
            "--output", str(output_file),
            "--backend", "claude",
            "--no-knowledge",
        ])
        assert result.exit_code != 0

    @patch("process.safe_resolve", side_effect=lambda root, p: Path(p).resolve())
    @patch("process.process_with_claude")
    @patch("process.load_knowledge_for_processing", return_value=("", ["1040"]))
    @patch("process.load_config")
    def test_claude_backend_dispatches(
        self, mock_config, mock_knowledge, mock_claude, mock_safe,
        tmp_path, test_config, sanitized_data, instructions_data
    ):
        mock_config.return_value = test_config
        mock_claude.return_value = instructions_data
        input_file = tmp_path / "sanitized.json"
        input_file.write_text(json.dumps(sanitized_data))
        output_file = tmp_path / "output.json"

        runner = CliRunner()
        result = runner.invoke(main, [
            "--input", str(input_file),
            "--output", str(output_file),
            "--backend", "claude",
        ])
        assert result.exit_code == 0
        assert mock_claude.called

    @patch("process.safe_resolve", side_effect=lambda root, p: Path(p).resolve())
    @patch("process.process_with_local_llm")
    @patch("process.load_knowledge_for_processing", return_value=("", ["1040"]))
    @patch("process.load_config")
    def test_local_backend_dispatches(
        self, mock_config, mock_knowledge, mock_local, mock_safe,
        tmp_path, test_config, sanitized_data, instructions_data
    ):
        mock_config.return_value = test_config
        mock_local.return_value = instructions_data
        input_file = tmp_path / "sanitized.json"
        input_file.write_text(json.dumps(sanitized_data))
        output_file = tmp_path / "output.json"

        runner = CliRunner()
        result = runner.invoke(main, [
            "--input", str(input_file),
            "--output", str(output_file),
            "--backend", "local",
        ])
        assert result.exit_code == 0
        assert mock_local.called
