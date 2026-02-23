"""Tests for assemble.py — token rehydration and template finding."""

import json

import pytest

from assemble import find_template, rehydrate_data


class TestRehydrateData:
    """Test replacing redaction tokens with real values from vault."""

    def test_simple_string(self, vault_data):
        data = {"ssn": "[SSN_REDACTED_1]"}
        result = rehydrate_data(data, vault_data)
        assert result["ssn"] == "000-00-1234"

    def test_nested_dict(self, vault_data):
        data = {"employee": {"ssn": "[SSN_REDACTED_1]", "ein": "[EIN_REDACTED_1]"}}
        result = rehydrate_data(data, vault_data)
        assert result["employee"]["ssn"] == "000-00-1234"
        assert result["employee"]["ein"] == "99-8765432"

    def test_list_values(self, vault_data):
        data = {"ssns": ["[SSN_REDACTED_1]"]}
        result = rehydrate_data(data, vault_data)
        assert result["ssns"][0] == "000-00-1234"

    def test_non_string_passthrough(self, vault_data):
        data = {"amount": 75000, "flag": True, "nothing": None}
        result = rehydrate_data(data, vault_data)
        assert result["amount"] == 75000
        assert result["flag"] is True
        assert result["nothing"] is None

    def test_multiple_tokens_in_one_string(self, vault_data):
        data = {"info": "SSN: [SSN_REDACTED_1], EIN: [EIN_REDACTED_1]"}
        result = rehydrate_data(data, vault_data)
        assert "000-00-1234" in result["info"]
        assert "99-8765432" in result["info"]

    def test_empty_vault(self):
        data = {"ssn": "[SSN_REDACTED_1]"}
        result = rehydrate_data(data, {})
        assert result["ssn"] == "[SSN_REDACTED_1]"

    def test_full_instructions(self, instructions_data, vault_data):
        result = rehydrate_data(instructions_data, vault_data)
        result_str = json.dumps(result)
        assert "[SSN_REDACTED_1]" not in result_str
        assert "000-00-1234" in result_str

    def test_deeply_nested(self, vault_data):
        data = {"a": {"b": {"c": {"d": "[EIN_REDACTED_2]"}}}}
        result = rehydrate_data(data, vault_data)
        assert result["a"]["b"]["c"]["d"] == "99-1111111"


class TestFindTemplate:
    """Test template file discovery with various naming patterns."""

    def test_find_f_prefix(self, templates_dir):
        result = find_template("1040", templates_dir)
        assert result is not None
        assert result.name == "f1040.pdf"

    def test_find_hyphenated(self, templates_dir):
        result = find_template("Schedule A", templates_dir)
        assert result is not None
        assert "schedule" in result.name.lower()

    def test_not_found(self, templates_dir):
        result = find_template("schedule_d", templates_dir)
        assert result is None

    def test_case_insensitive(self, templates_dir):
        result = find_template("1040", templates_dir)
        assert result is not None

    def test_empty_directory(self, tmp_path):
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()
        result = find_template("1040", empty_dir)
        assert result is None
