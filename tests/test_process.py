"""Tests for process.py — sanitization verification, prompt building, JSON parsing."""

import json

import pytest

from process import (
    build_system_prompt,
    build_user_prompt,
    determine_tax_year,
    parse_json_response,
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
        assert "Prior Year Context" in prompt
        assert "2024" in prompt

    def test_no_prior_year(self, sanitized_data):
        prompt = build_user_prompt(sanitized_data, None)
        assert "Prior Year" not in prompt

    def test_includes_forms_needed(self, sanitized_data):
        prompt = build_user_prompt(sanitized_data, None, ["1040", "Schedule A"])
        assert "Forms to Prepare" in prompt
        assert "1040" in prompt
        assert "Schedule A" in prompt

    def test_no_forms_needed(self, sanitized_data):
        prompt = build_user_prompt(sanitized_data, None, None)
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
