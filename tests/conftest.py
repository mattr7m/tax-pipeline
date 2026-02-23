"""Shared test fixtures for the tax processor test suite."""

import json
import sys
from pathlib import Path

import pytest
import yaml

# Add scripts/ to path so we can import the modules under test
SCRIPTS_DIR = Path(__file__).parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def test_config():
    """Minimal config dict matching config.yaml structure."""
    with open(FIXTURES_DIR / "config.yaml") as f:
        return yaml.safe_load(f)


@pytest.fixture
def extracted_data():
    """Sample extracted tax data with fake PII (SSNs starting with 000, EINs with 99)."""
    with open(FIXTURES_DIR / "extracted.json") as f:
        return json.load(f)


@pytest.fixture
def sanitized_data():
    """Sample sanitized tax data with redaction tokens."""
    with open(FIXTURES_DIR / "sanitized.json") as f:
        return json.load(f)


@pytest.fixture
def vault_data():
    """Sample vault mapping tokens to fake PII values."""
    with open(FIXTURES_DIR / "vault.json") as f:
        return json.load(f)


@pytest.fixture
def instructions_data():
    """Sample processing instructions with form field mappings."""
    with open(FIXTURES_DIR / "instructions.json") as f:
        return json.load(f)


@pytest.fixture
def llm_response_clean():
    """LLM response that is plain JSON."""
    return (FIXTURES_DIR / "llm_responses" / "clean_json.txt").read_text()


@pytest.fixture
def llm_response_markdown():
    """LLM response with JSON inside ```json``` code block."""
    return (FIXTURES_DIR / "llm_responses" / "markdown_json.txt").read_text()


@pytest.fixture
def llm_response_generic_block():
    """LLM response with JSON inside generic ``` code block."""
    return (FIXTURES_DIR / "llm_responses" / "generic_code_block.txt").read_text()


@pytest.fixture
def llm_response_malformed():
    """LLM response that is not valid JSON."""
    return (FIXTURES_DIR / "llm_responses" / "malformed.txt").read_text()


@pytest.fixture
def tax_knowledge_dir(tmp_path):
    """Create a temporary tax knowledge directory with test data."""
    year_dir = tmp_path / "2025"
    year_dir.mkdir()

    # Minimal tax tables
    tables = {
        "tax_year": 2025,
        "standard_deductions": {
            "single": 15000,
            "married_filing_jointly": 30000,
            "head_of_household": 22500,
        },
        "tax_brackets": {
            "single": [
                {"min": 0, "max": 11925, "rate": 0.10, "base_tax": 0},
                {"min": 11925, "max": 48475, "rate": 0.12, "base_tax": 1192.50},
                {"min": 48475, "max": 103350, "rate": 0.22, "base_tax": 5578.50},
            ]
        },
        "retirement_contributions": {
            "401k_limit": 23500,
            "401k_catch_up_50_plus": 7500,
            "ira_limit": 7000,
            "ira_catch_up_50_plus": 1000,
        },
        "deductions": {
            "salt_cap": 10000,
            "mortgage_interest_debt_limit": 750000,
            "medical_expense_agi_threshold": 0.075,
        },
        "credits": {
            "child_tax_credit": 2000,
            "earned_income_credit_max_3_plus_children": 8046,
        },
    }
    (year_dir / "tax-tables.json").write_text(json.dumps(tables, indent=2))

    # Minimal form 1040 field mapping
    form_1040 = {
        "form_id": "1040",
        "form_name": "U.S. Individual Income Tax Return",
        "tax_year": 2025,
        "field_mappings": {
            "income": {
                "f1_25": {
                    "line": "1a",
                    "type": "currency",
                    "description": "Wages from W-2",
                },
                "f1_36": {
                    "line": "2b",
                    "type": "currency",
                    "description": "Taxable interest",
                },
            }
        },
        "calculation_rules": {
            "line_9": "Sum of lines 1z, 2b, 3b, 4b, 5b, 6b, 7, 8",
            "line_11": "Line 9 minus line 10",
        },
    }
    (year_dir / "form-1040-fields.json").write_text(json.dumps(form_1040, indent=2))

    # Minimal schedule A field mapping
    schedule_a = {
        "form_id": "schedule_a",
        "form_name": "Schedule A - Itemized Deductions",
        "tax_year": 2025,
        "field_mappings": {
            "taxes": {
                "f2_01": {
                    "line": "5a",
                    "type": "currency",
                    "description": "State and local taxes",
                }
            }
        },
        "calculation_rules": {},
    }
    (year_dir / "schedule-a-fields.json").write_text(
        json.dumps(schedule_a, indent=2)
    )

    # Minimal rules summary
    rules = """# Tax Rules Summary - 2025

## Filing Requirements
- Single under 65: file if gross income >= $15,000
- Married filing jointly both under 65: file if gross income >= $30,000

## Standard Deduction
- Single: $15,000
- MFJ: $30,000

## Key Credits
- Child Tax Credit: $2,000 per qualifying child
"""
    (year_dir / "tax-rules-summary.md").write_text(rules)

    # Form instructions
    instructions = """# Form 1040 Instructions

## Line 1a: Wages
Enter the total from all W-2 forms, Box 1.

## Line 2b: Taxable Interest
Enter the total taxable interest from 1099-INT forms.
"""
    (year_dir / "form-1040-instructions.md").write_text(instructions)

    return tmp_path


@pytest.fixture
def templates_dir(tmp_path):
    """Create a temporary templates directory with dummy PDF files."""
    tpl = tmp_path / "templates"
    tpl.mkdir()
    # Create empty files to simulate PDF templates
    (tpl / "f1040.pdf").write_bytes(b"%PDF-1.4 fake")
    (tpl / "schedule-a.pdf").write_bytes(b"%PDF-1.4 fake")
    return tpl
