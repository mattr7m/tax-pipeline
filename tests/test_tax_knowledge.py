"""Tests for tax_knowledge.py — knowledge loading, form detection, context building."""

import json

import pytest

from tax_knowledge import TaxKnowledgeBase, load_knowledge_for_processing


class TestTaxKnowledgeBaseAvailability:
    """Test knowledge base existence checks."""

    def test_available(self, tax_knowledge_dir):
        kb = TaxKnowledgeBase(2025, tax_knowledge_dir)
        assert kb.is_available() is True

    def test_not_available(self, tmp_path):
        kb = TaxKnowledgeBase(9999, tmp_path)
        assert kb.is_available() is False


class TestLoadTaxTables:
    """Test tax table loading."""

    def test_load(self, tax_knowledge_dir):
        kb = TaxKnowledgeBase(2025, tax_knowledge_dir)
        tables = kb.load_tax_tables()
        assert tables["standard_deductions"]["single"] == 15000
        assert tables["standard_deductions"]["married_filing_jointly"] == 30000

    def test_caching(self, tax_knowledge_dir):
        kb = TaxKnowledgeBase(2025, tax_knowledge_dir)
        t1 = kb.load_tax_tables()
        t2 = kb.load_tax_tables()
        assert t1 is t2  # Same object (cached)

    def test_missing_returns_empty(self, tmp_path):
        year_dir = tmp_path / "2025"
        year_dir.mkdir()
        kb = TaxKnowledgeBase(2025, tmp_path)
        assert kb.load_tax_tables() == {}


class TestLoadFormMapping:
    """Test form field mapping loading."""

    def test_load_1040(self, tax_knowledge_dir):
        kb = TaxKnowledgeBase(2025, tax_knowledge_dir)
        mapping = kb.load_form_mapping("1040")
        assert mapping["form_id"] == "1040"
        assert "field_mappings" in mapping

    def test_load_schedule_a(self, tax_knowledge_dir):
        kb = TaxKnowledgeBase(2025, tax_knowledge_dir)
        mapping = kb.load_form_mapping("schedule_a")
        assert mapping["form_id"] == "schedule_a"

    def test_caching(self, tax_knowledge_dir):
        kb = TaxKnowledgeBase(2025, tax_knowledge_dir)
        m1 = kb.load_form_mapping("1040")
        m2 = kb.load_form_mapping("1040")
        assert m1 is m2

    def test_missing_form(self, tax_knowledge_dir):
        kb = TaxKnowledgeBase(2025, tax_knowledge_dir)
        assert kb.load_form_mapping("schedule_z") == {}


class TestLoadFormInstructions:
    """Test form instructions loading."""

    def test_load(self, tax_knowledge_dir):
        kb = TaxKnowledgeBase(2025, tax_knowledge_dir)
        instructions = kb.load_form_instructions("1040")
        assert "Line 1a" in instructions

    def test_missing_returns_empty(self, tax_knowledge_dir):
        kb = TaxKnowledgeBase(2025, tax_knowledge_dir)
        assert kb.load_form_instructions("schedule_z") == ""


class TestLoadRulesSummary:
    """Test rules summary loading."""

    def test_load(self, tax_knowledge_dir):
        kb = TaxKnowledgeBase(2025, tax_knowledge_dir)
        rules = kb.load_rules_summary()
        assert "Filing Requirements" in rules
        assert "$15,000" in rules

    def test_caching(self, tax_knowledge_dir):
        kb = TaxKnowledgeBase(2025, tax_knowledge_dir)
        r1 = kb.load_rules_summary()
        r2 = kb.load_rules_summary()
        assert r1 is r2


class TestGetFormsNeeded:
    """Test form detection heuristics."""

    def test_always_includes_1040(self, tax_knowledge_dir):
        kb = TaxKnowledgeBase(2025, tax_knowledge_dir)
        forms = kb.get_forms_needed({"documents": [], "summary": {}})
        assert "1040" in forms

    def test_schedule_a_from_deductions(self, tax_knowledge_dir):
        kb = TaxKnowledgeBase(2025, tax_knowledge_dir)
        data = {
            "documents": [],
            "summary": {
                "deductions": {
                    "mortgage_interest": 12500,
                    "property_taxes": 4200,
                }
            },
        }
        forms = kb.get_forms_needed(data)
        assert "1040" in forms
        assert "schedule_a" in forms

    def test_no_schedule_a_low_deductions(self, tax_knowledge_dir):
        kb = TaxKnowledgeBase(2025, tax_knowledge_dir)
        data = {
            "documents": [],
            "summary": {
                "deductions": {
                    "mortgage_interest": 1000,
                    "property_taxes": 500,
                }
            },
        }
        forms = kb.get_forms_needed(data)
        assert "schedule_a" not in forms

    def test_schedule_b_high_interest(self, tax_knowledge_dir):
        kb = TaxKnowledgeBase(2025, tax_knowledge_dir)
        data = {
            "documents": [{"document_type": "1099_int"}],
            "summary": {
                "income": {"interest": 2000},
                "deductions": {},
            },
        }
        forms = kb.get_forms_needed(data)
        assert "schedule_b" in forms

    def test_no_schedule_b_low_interest(self, tax_knowledge_dir):
        kb = TaxKnowledgeBase(2025, tax_knowledge_dir)
        data = {
            "documents": [{"document_type": "1099_int"}],
            "summary": {
                "income": {"interest": 500},
                "deductions": {},
            },
        }
        forms = kb.get_forms_needed(data)
        assert "schedule_b" not in forms


class TestBuildContextForForms:
    """Test context string generation."""

    def test_includes_tax_tables(self, tax_knowledge_dir):
        kb = TaxKnowledgeBase(2025, tax_knowledge_dir)
        context = kb.build_context_for_forms(["1040"])
        assert "Standard Deductions" in context
        assert "$15,000" in context

    def test_includes_field_mappings(self, tax_knowledge_dir):
        kb = TaxKnowledgeBase(2025, tax_knowledge_dir)
        context = kb.build_context_for_forms(["1040"])
        assert "f1_25" in context

    def test_includes_instructions(self, tax_knowledge_dir):
        kb = TaxKnowledgeBase(2025, tax_knowledge_dir)
        context = kb.build_context_for_forms(["1040"], include_instructions=True)
        assert "Line 1a" in context

    def test_excludes_instructions(self, tax_knowledge_dir):
        kb = TaxKnowledgeBase(2025, tax_knowledge_dir)
        context = kb.build_context_for_forms(["1040"], include_instructions=False)
        # Field mappings and tables should still be there
        assert "f1_25" in context

    def test_multiple_forms(self, tax_knowledge_dir):
        kb = TaxKnowledgeBase(2025, tax_knowledge_dir)
        context = kb.build_context_for_forms(["1040", "schedule_a"])
        assert "1040" in context
        assert "Schedule A" in context


class TestCompactTaxTables:
    """Test tax table formatting."""

    def test_formats_standard_deductions(self, tax_knowledge_dir):
        kb = TaxKnowledgeBase(2025, tax_knowledge_dir)
        tables = kb.load_tax_tables()
        result = kb._compact_tax_tables(tables)
        assert "Single: $15,000" in result
        assert "Married Filing Jointly: $30,000" in result

    def test_empty_tables(self, tax_knowledge_dir):
        kb = TaxKnowledgeBase(2025, tax_knowledge_dir)
        result = kb._compact_tax_tables({})
        assert result == ""


class TestFormatFieldMapping:
    """Test field mapping formatting."""

    def test_basic_format(self, tax_knowledge_dir):
        kb = TaxKnowledgeBase(2025, tax_knowledge_dir)
        mapping = kb.load_form_mapping("1040")
        result = kb._format_field_mapping(mapping)
        assert "f1_25" in result
        assert "1a" in result


class TestLoadKnowledgeForProcessing:
    """Test the convenience wrapper."""

    def test_returns_context_and_forms(self, tax_knowledge_dir, extracted_data):
        context, forms = load_knowledge_for_processing(
            2025, extracted_data, tax_knowledge_dir
        )
        assert len(context) > 0
        assert "1040" in forms

    def test_unavailable_year(self, tmp_path):
        context, forms = load_knowledge_for_processing(
            9999, {}, tmp_path
        )
        assert context == ""
        assert forms == ["1040"]
