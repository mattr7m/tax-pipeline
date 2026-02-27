"""Tests for sanitize.py — the Sanitizer class and related functions.

This is the highest-priority test module because the sanitizer is the
privacy guarantee: a missed regex here leaks real SSNs to cloud APIs.
"""

import base64
import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from sanitize import Sanitizer, decrypt_vault, encrypt_vault


class TestSanitizerTokenGeneration:
    """Test token creation and counter tracking."""

    def test_generate_token_first(self, test_config):
        s = Sanitizer(test_config)
        token = s._generate_token("ssn")
        assert token == "[SSN_REDACTED_1]"

    def test_generate_token_increments(self, test_config):
        s = Sanitizer(test_config)
        t1 = s._generate_token("ssn")
        t2 = s._generate_token("ssn")
        assert t1 == "[SSN_REDACTED_1]"
        assert t2 == "[SSN_REDACTED_2]"

    def test_generate_token_different_types(self, test_config):
        s = Sanitizer(test_config)
        ssn_token = s._generate_token("ssn")
        ein_token = s._generate_token("ein")
        assert ssn_token == "[SSN_REDACTED_1]"
        assert ein_token == "[EIN_REDACTED_1]"


class TestSanitizerSSN:
    """Test SSN detection and replacement."""

    def test_ssn_redacted(self, test_config):
        s = Sanitizer(test_config)
        result = s.sanitize_value("My SSN is 000-00-1234")
        assert "000-00-1234" not in result
        assert "[SSN_REDACTED_1]" in result

    def test_multiple_ssns(self, test_config):
        s = Sanitizer(test_config)
        result = s.sanitize_value("SSN1: 000-00-1234, SSN2: 000-00-5678")
        assert "000-00-1234" not in result
        assert "000-00-5678" not in result
        assert "[SSN_REDACTED_1]" in result
        assert "[SSN_REDACTED_2]" in result

    def test_duplicate_ssn_same_token(self, test_config):
        """The same SSN appearing twice should get the same token."""
        s = Sanitizer(test_config)
        result = s.sanitize_value("SSN: 000-00-1234 and again 000-00-1234")
        assert result.count("[SSN_REDACTED_1]") == 2
        assert "[SSN_REDACTED_2]" not in result

    def test_ssn_vault_stored(self, test_config):
        s = Sanitizer(test_config)
        s.sanitize_value("SSN: 000-00-1234")
        vault = s.get_vault()
        assert "[SSN_REDACTED_1]" in vault
        assert vault["[SSN_REDACTED_1]"] == "000-00-1234"


class TestSanitizerEIN:
    """Test EIN detection and replacement."""

    def test_ein_redacted(self, test_config):
        s = Sanitizer(test_config)
        result = s.sanitize_value("EIN: 99-8765432")
        assert "99-8765432" not in result
        assert "[EIN_REDACTED_" in result

    def test_ein_vault_stored(self, test_config):
        s = Sanitizer(test_config)
        s.sanitize_value("EIN: 99-8765432")
        vault = s.get_vault()
        assert any("99-8765432" == v for v in vault.values())


class TestSanitizerRecursive:
    """Test recursive sanitization of dicts and lists."""

    def test_sanitize_dict(self, test_config):
        s = Sanitizer(test_config)
        data = {"name": "Jane", "ssn": "000-00-1234"}
        result = s.sanitize_value(data)
        assert result["name"] == "Jane"
        assert "000-00-1234" not in result["ssn"]

    def test_sanitize_nested_dict(self, test_config):
        s = Sanitizer(test_config)
        data = {"employee": {"name": "Jane", "ssn": "000-00-1234"}}
        result = s.sanitize_value(data)
        assert "000-00-1234" not in result["employee"]["ssn"]

    def test_sanitize_list(self, test_config):
        s = Sanitizer(test_config)
        data = ["SSN: 000-00-1234", "EIN: 99-8765432"]
        result = s.sanitize_value(data)
        assert "000-00-1234" not in result[0]
        assert "99-8765432" not in result[1]

    def test_sanitize_non_string_passthrough(self, test_config):
        s = Sanitizer(test_config)
        assert s.sanitize_value(42) == 42
        assert s.sanitize_value(3.14) == 3.14
        assert s.sanitize_value(None) is None
        assert s.sanitize_value(True) is True


class TestSanitizerFullDocument:
    """Test sanitizing a full extracted document structure."""

    def test_full_document(self, test_config, extracted_data):
        s = Sanitizer(test_config)
        result = s.sanitize_value(extracted_data)

        # No raw SSNs should remain
        result_str = json.dumps(result)
        assert "000-00-1234" not in result_str

        # No raw EINs should remain
        assert "99-8765432" not in result_str
        assert "99-1111111" not in result_str
        assert "99-2222222" not in result_str

        # Tokens should be present
        assert "[SSN_REDACTED_" in result_str
        assert "[EIN_REDACTED_" in result_str

        # Non-sensitive data preserved
        assert result["documents"][0]["wages"] == 75000.00
        assert result["documents"][0]["employer"]["name"] == "Acme Corp"

    def test_vault_completeness(self, test_config, extracted_data):
        """Every redaction token must have a vault entry."""
        s = Sanitizer(test_config)
        result = s.sanitize_value(extracted_data)
        vault = s.get_vault()

        result_str = json.dumps(result)
        for token in vault:
            assert token in result_str

    def test_summary_counts(self, test_config, extracted_data):
        s = Sanitizer(test_config)
        s.sanitize_value(extracted_data)
        summary = s.get_summary()
        assert summary.get("ssn", 0) >= 1
        assert summary.get("ein", 0) >= 1


class TestSanitizerEdgeCases:
    """Edge cases and boundary conditions."""

    def test_empty_string(self, test_config):
        s = Sanitizer(test_config)
        assert s.sanitize_value("") == ""

    def test_no_sensitive_data(self, test_config):
        s = Sanitizer(test_config)
        result = s.sanitize_value("Just a plain string with no PII")
        assert result == "Just a plain string with no PII"
        assert s.get_vault() == {}

    def test_empty_dict(self, test_config):
        s = Sanitizer(test_config)
        assert s.sanitize_value({}) == {}

    def test_empty_list(self, test_config):
        s = Sanitizer(test_config)
        assert s.sanitize_value([]) == []

    def test_ssn_like_but_not_ssn(self, test_config):
        """Dollar amounts and dates shouldn't trigger SSN pattern."""
        s = Sanitizer(test_config)
        # These should NOT be redacted (no dash in SSN pattern position)
        result = s.sanitize_value("Amount: $75,000.00")
        assert "$75,000.00" in result

    def test_mixed_content_string(self, test_config):
        s = Sanitizer(test_config)
        text = "Employee 000-00-1234 at company 99-8765432 earned $75000"
        result = s.sanitize_value(text)
        assert "000-00-1234" not in result
        assert "99-8765432" not in result
        assert "$75000" in result


class TestEncryptVault:
    """Tests for vault encryption via age CLI."""

    @patch("sanitize.subprocess.run")
    def test_encrypt_with_age(self, mock_run, tmp_path, vault_data):
        output_path = tmp_path / "test.age"
        encrypt_vault(vault_data, output_path, "mypassphrase")

        mock_run.assert_called_once()
        call_args = mock_run.call_args
        assert call_args[0][0] == ["age", "-p", "-o", str(output_path)]
        assert call_args[1]["input"] == json.dumps(vault_data, indent=2).encode()
        assert "AGE_PASSPHRASE" in call_args[1]["env"]
        assert call_args[1]["env"]["AGE_PASSPHRASE"] == "mypassphrase"

    @patch("sanitize.subprocess.run", side_effect=FileNotFoundError("age not found"))
    def test_fallback_base64(self, mock_run, tmp_path, vault_data):
        output_path = tmp_path / "test.age"
        encrypt_vault(vault_data, output_path, "mypassphrase")

        # Should have written a base64-encoded file
        assert output_path.exists()
        decoded = base64.b64decode(output_path.read_text()).decode()
        assert json.loads(decoded) == vault_data

    @patch("sanitize.subprocess.run")
    def test_called_process_error_raises(self, mock_run, tmp_path, vault_data):
        mock_run.side_effect = subprocess.CalledProcessError(
            1, "age", stderr=b"bad passphrase"
        )
        with pytest.raises(subprocess.CalledProcessError):
            encrypt_vault(vault_data, tmp_path / "test.age", "bad")


class TestDecryptVault:
    """Tests for vault decryption via age CLI."""

    @patch("sanitize.subprocess.run")
    def test_decrypt_with_age(self, mock_run, vault_data):
        mock_run.return_value = MagicMock(
            stdout=json.dumps(vault_data).encode()
        )
        result = decrypt_vault(Path("fake.age"), "pass")
        assert result == vault_data

    @patch("sanitize.subprocess.run", side_effect=FileNotFoundError)
    def test_fallback_base64(self, mock_run, tmp_path, vault_data):
        vault_file = tmp_path / "test.age"
        encoded = base64.b64encode(json.dumps(vault_data).encode()).decode()
        vault_file.write_text(encoded)

        result = decrypt_vault(vault_file, "pass")
        assert result == vault_data

    @patch("sanitize.subprocess.run")
    def test_called_process_error_raises(self, mock_run):
        mock_run.side_effect = subprocess.CalledProcessError(
            1, "age", stderr=b"wrong passphrase"
        )
        with pytest.raises(subprocess.CalledProcessError):
            decrypt_vault(Path("fake.age"), "bad")

    @patch("sanitize.subprocess.run", side_effect=FileNotFoundError)
    def test_bad_base64_raises(self, mock_run, tmp_path):
        vault_file = tmp_path / "bad.age"
        vault_file.write_text("not-valid-base64!!!")
        with pytest.raises(Exception):
            decrypt_vault(vault_file, "pass")
