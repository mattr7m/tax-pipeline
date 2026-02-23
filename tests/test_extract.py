"""Tests for extract.py — document detection, JSON parsing, prompt building, summaries."""

import json

import pytest

from extract import (
    build_extraction_prompt,
    detect_document_type,
    parse_json_response,
    update_summary,
)


class TestDetectDocumentType:
    """Test keyword-based document type detection."""

    def test_detect_w2(self, test_config):
        text = "Form W-2 Wage and Tax Statement 2025"
        doc_type, confidence = detect_document_type(text, test_config)
        assert doc_type == "w2"
        assert confidence > 0

    def test_detect_1099_int(self, test_config):
        text = "1099-INT Interest Income for tax year 2025"
        doc_type, confidence = detect_document_type(text, test_config)
        assert doc_type == "1099_int"
        assert confidence > 0

    def test_detect_1099_div(self, test_config):
        text = "1099-DIV Dividends and Distributions"
        doc_type, confidence = detect_document_type(text, test_config)
        assert doc_type == "1099_div"
        assert confidence > 0

    def test_detect_1098(self, test_config):
        text = "1098 Mortgage Interest Statement from your lender"
        doc_type, confidence = detect_document_type(text, test_config)
        assert doc_type == "1098"
        assert confidence > 0

    def test_detect_1040(self, test_config):
        text = "Form 1040 U.S. Individual Income Tax Return"
        doc_type, confidence = detect_document_type(text, test_config)
        assert doc_type == "1040"
        assert confidence > 0

    def test_unknown_document(self, test_config):
        text = "This is a random document with no tax keywords"
        doc_type, confidence = detect_document_type(text, test_config)
        assert confidence == 0
        assert doc_type == "unknown"

    def test_case_insensitive(self, test_config):
        text = "FORM W-2 WAGE AND TAX STATEMENT"
        doc_type, confidence = detect_document_type(text, test_config)
        assert doc_type == "w2"

    def test_highest_confidence_wins(self, test_config):
        """When multiple types match, the one with highest confidence wins."""
        # All W-2 keywords present
        text = "Wage and Tax Statement W-2 Form W-2"
        doc_type, confidence = detect_document_type(text, test_config)
        assert doc_type == "w2"
        assert confidence == 1.0

    def test_empty_text(self, test_config):
        doc_type, confidence = detect_document_type("", test_config)
        assert confidence == 0

    def test_empty_config(self):
        doc_type, confidence = detect_document_type("some text", {})
        assert doc_type == "unknown"
        assert confidence == 0.0


class TestParseJsonResponse:
    """Test JSON extraction from LLM responses."""

    def test_clean_json(self, llm_response_clean):
        result = parse_json_response(llm_response_clean, "w2", "original")
        assert result["document_type"] == "w2"
        assert result["wages"] == 75000

    def test_markdown_json_block(self, llm_response_markdown):
        result = parse_json_response(llm_response_markdown, "w2", "original")
        assert result["document_type"] == "w2"
        assert result["wages"] == 75000

    def test_generic_code_block(self, llm_response_generic_block):
        result = parse_json_response(llm_response_generic_block, "w2", "original")
        assert result["document_type"] == "w2"
        assert result["wages"] == 75000

    def test_malformed_response(self, llm_response_malformed):
        result = parse_json_response(llm_response_malformed, "w2", "original text")
        assert "extraction_error" in result or "raw_text" in result
        assert result["document_type"] == "w2"

    def test_json_with_trailing_whitespace(self):
        response = '  {"key": "value"}  \n\n'
        result = parse_json_response(response, "test", "orig")
        assert result["key"] == "value"

    def test_multiple_code_blocks_picks_json(self):
        response = """Some text
```python
x = 1
```
```json
{"document_type": "w2", "wages": 50000}
```
More text"""
        result = parse_json_response(response, "w2", "orig")
        assert result["document_type"] == "w2"
        assert result["wages"] == 50000


class TestBuildExtractionPrompt:
    """Test prompt construction."""

    def test_includes_doc_type(self, test_config):
        prompt = build_extraction_prompt("some text", "w2", test_config)
        assert "W2" in prompt

    def test_includes_expected_fields(self, test_config):
        prompt = build_extraction_prompt("some text", "w2", test_config)
        assert "wages" in prompt
        assert "federal_withheld" in prompt

    def test_includes_document_text(self, test_config):
        prompt = build_extraction_prompt("my document content", "w2", test_config)
        assert "my document content" in prompt

    def test_truncates_long_text(self, test_config):
        long_text = "x" * 20000
        prompt = build_extraction_prompt(long_text, "w2", test_config)
        # Should not include the full 20000 chars
        assert len(prompt) < 20000

    def test_prior_year_context_included(self, test_config):
        prior = {"tax_year": 2024, "income": 70000}
        prompt = build_extraction_prompt("text", "w2", test_config, prior)
        assert "2024" in prompt
        assert "70000" in prompt

    def test_no_prior_year(self, test_config):
        prompt = build_extraction_prompt("text", "w2", test_config, None)
        assert "Prior year" not in prompt

    def test_unknown_doc_type(self, test_config):
        prompt = build_extraction_prompt("text", "unknown", test_config)
        assert "UNKNOWN" in prompt


class TestUpdateSummary:
    """Test running summary accumulation."""

    def test_w2_wages(self):
        summary = {"income": {}, "deductions": {}, "withholding": {}}
        doc_data = {"wages": 75000, "federal_withheld": 11250}
        update_summary(summary, doc_data, "w2")
        assert summary["income"]["wages"] == 75000
        assert summary["withholding"]["federal"] == 11250

    def test_w2_accumulates(self):
        summary = {"income": {"wages": 50000}, "deductions": {}, "withholding": {"federal": 7500}}
        doc_data = {"wages": 25000, "federal_withheld": 3750}
        update_summary(summary, doc_data, "w2")
        assert summary["income"]["wages"] == 75000
        assert summary["withholding"]["federal"] == 11250

    def test_w2_alt_field_names(self):
        summary = {"income": {}, "deductions": {}, "withholding": {}}
        doc_data = {"wages_tips_compensation": 75000, "federal_income_tax_withheld": 11250}
        update_summary(summary, doc_data, "w2")
        assert summary["income"]["wages"] == 75000
        assert summary["withholding"]["federal"] == 11250

    def test_1099_int(self):
        summary = {"income": {}, "deductions": {}, "withholding": {}}
        doc_data = {"interest_income": 850}
        update_summary(summary, doc_data, "1099_int")
        assert summary["income"]["interest"] == 850

    def test_1099_div(self):
        summary = {"income": {}, "deductions": {}, "withholding": {}}
        doc_data = {"ordinary_dividends": 1200, "qualified_dividends": 500}
        update_summary(summary, doc_data, "1099_div")
        assert summary["income"]["dividends"] == 1200
        assert summary["income"]["qualified_dividends"] == 500

    def test_1098(self):
        summary = {"income": {}, "deductions": {}, "withholding": {}}
        doc_data = {"mortgage_interest": 12500, "property_taxes": 4200}
        update_summary(summary, doc_data, "1098")
        assert summary["deductions"]["mortgage_interest"] == 12500
        assert summary["deductions"]["property_taxes"] == 4200

    def test_invalid_value_ignored(self):
        summary = {"income": {}, "deductions": {}, "withholding": {}}
        doc_data = {"wages": "not-a-number"}
        update_summary(summary, doc_data, "w2")
        assert "wages" not in summary["income"]

    def test_missing_fields_no_error(self):
        summary = {"income": {}, "deductions": {}, "withholding": {}}
        doc_data = {"some_other_field": 100}
        update_summary(summary, doc_data, "w2")
        # Should not crash, summary unchanged
        assert summary["income"] == {}

    def test_unknown_doc_type_no_error(self):
        summary = {"income": {}, "deductions": {}, "withholding": {}}
        doc_data = {"wages": 75000}
        update_summary(summary, doc_data, "unknown")
        assert summary["income"] == {}
