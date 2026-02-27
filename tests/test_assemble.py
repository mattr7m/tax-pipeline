"""Tests for assemble.py — token rehydration and template finding."""

import base64
import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from assemble import (
    assemble_forms,
    decrypt_vault,
    fill_pdf_form,
    find_template,
    generate_review_document,
    get_pdf_form_fields,
    main,
    rehydrate_data,
)


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


class TestDecryptVaultAssemble:
    """Tests for vault decryption in assemble.py (mocked)."""

    @patch("assemble.subprocess.run")
    def test_age_success(self, mock_run, vault_data):
        mock_run.return_value = MagicMock(
            stdout=json.dumps(vault_data).encode()
        )
        result = decrypt_vault(Path("fake.age"), "pass")
        assert result == vault_data

    @patch("assemble.subprocess.run", side_effect=FileNotFoundError)
    def test_base64_fallback(self, mock_run, tmp_path, vault_data):
        vault_file = tmp_path / "test.age"
        encoded = base64.b64encode(json.dumps(vault_data).encode()).decode()
        vault_file.write_text(encoded)

        result = decrypt_vault(vault_file, "pass")
        assert result == vault_data

    @patch("assemble.subprocess.run")
    def test_error_raises(self, mock_run):
        mock_run.side_effect = subprocess.CalledProcessError(
            1, "age", stderr=b"bad"
        )
        with pytest.raises(subprocess.CalledProcessError):
            decrypt_vault(Path("fake.age"), "pass")


class TestGetPdfFormFields:
    """Tests for PDF form field reading."""

    def test_fillpdf_backend(self):
        import assemble
        mock_fillpdfs = MagicMock()
        mock_fillpdfs.get_form_fields.return_value = {"f1_01": ""}
        with patch.object(assemble, "HAS_FILLPDF", True), \
             patch.object(assemble, "HAS_PYMUPDF", False), \
             patch.object(assemble, "fillpdfs", mock_fillpdfs, create=True):
            result = get_pdf_form_fields(Path("fake.pdf"))
        assert result == {"f1_01": ""}

    @patch("assemble.HAS_FILLPDF", False)
    @patch("assemble.HAS_PYMUPDF", True)
    @patch("assemble.fitz")
    def test_pymupdf_backend(self, mock_fitz):
        mock_widget = MagicMock()
        mock_widget.field_name = "f1_01"
        mock_widget.field_value = ""
        mock_page = MagicMock()
        mock_page.widgets.return_value = [mock_widget]
        mock_doc = MagicMock()
        mock_doc.__iter__ = lambda self: iter([mock_page])
        mock_fitz.open.return_value = mock_doc

        result = get_pdf_form_fields(Path("fake.pdf"))
        assert "f1_01" in result

    @patch("assemble.HAS_FILLPDF", False)
    @patch("assemble.HAS_PYMUPDF", False)
    def test_no_backend(self):
        result = get_pdf_form_fields(Path("fake.pdf"))
        assert result == {}


class TestFillPdfForm:
    """Tests for PDF form filling."""

    def test_fillpdf_success(self, tmp_path):
        import assemble
        mock_fillpdfs = MagicMock()
        with patch.object(assemble, "HAS_FILLPDF", True), \
             patch.object(assemble, "HAS_PYMUPDF", False), \
             patch.object(assemble, "fillpdfs", mock_fillpdfs, create=True):
            result = fill_pdf_form(
                Path("template.pdf"), tmp_path / "out.pdf",
                {"f1_01": "Jane"}, flatten=False
            )
        assert result is True
        mock_fillpdfs.write_fillable_pdf.assert_called_once()

    def test_fillpdf_exception_returns_false(self, tmp_path):
        import assemble
        mock_fillpdfs = MagicMock()
        mock_fillpdfs.write_fillable_pdf.side_effect = Exception("pdf error")
        with patch.object(assemble, "HAS_FILLPDF", True), \
             patch.object(assemble, "HAS_PYMUPDF", False), \
             patch.object(assemble, "fillpdfs", mock_fillpdfs, create=True):
            result = fill_pdf_form(
                Path("template.pdf"), tmp_path / "out.pdf", {"f1_01": "Jane"}
            )
        assert result is False

    @patch("assemble.HAS_FILLPDF", False)
    @patch("assemble.HAS_PYMUPDF", True)
    @patch("assemble.fitz")
    def test_pymupdf_success(self, mock_fitz, tmp_path):
        mock_widget = MagicMock()
        mock_widget.field_name = "f1_01"
        mock_page = MagicMock()
        mock_page.widgets.return_value = [mock_widget]
        mock_doc = MagicMock()
        mock_doc.__iter__ = lambda self: iter([mock_page])
        mock_fitz.open.return_value = mock_doc

        result = fill_pdf_form(
            Path("template.pdf"), tmp_path / "out.pdf",
            {"f1_01": "Jane"}, flatten=False
        )
        assert result is True
        mock_doc.save.assert_called_once()

    @patch("assemble.HAS_FILLPDF", False)
    @patch("assemble.HAS_PYMUPDF", False)
    def test_no_library_returns_false(self, tmp_path):
        result = fill_pdf_form(
            Path("template.pdf"), tmp_path / "out.pdf", {"f1_01": "Jane"}
        )
        assert result is False


class TestAssembleForms:
    """Tests for form assembly orchestration."""

    @patch("assemble.fill_pdf_form", return_value=True)
    @patch("assemble.find_template", return_value=Path("fake.pdf"))
    def test_assembles_all(self, mock_find, mock_fill, tmp_path, instructions_data):
        output_dir = tmp_path / "output"
        output_dir.mkdir()
        result = assemble_forms(instructions_data, tmp_path, output_dir)
        assert len(result) == 1  # One form (1040)
        assert mock_fill.called

    @patch("assemble.fill_pdf_form")
    @patch("assemble.find_template", return_value=None)
    def test_template_not_found_skipped(self, mock_find, mock_fill, tmp_path, instructions_data):
        output_dir = tmp_path / "output"
        output_dir.mkdir()
        result = assemble_forms(instructions_data, tmp_path, output_dir)
        assert len(result) == 0
        mock_fill.assert_not_called()

    @patch("assemble.fill_pdf_form", return_value=False)
    @patch("assemble.find_template", return_value=Path("fake.pdf"))
    def test_fill_failure_excluded(self, mock_find, mock_fill, tmp_path, instructions_data):
        output_dir = tmp_path / "output"
        output_dir.mkdir()
        result = assemble_forms(instructions_data, tmp_path, output_dir)
        assert len(result) == 0


class TestGenerateReviewDocument:
    """Tests for REVIEW.md generation."""

    def test_creates_file(self, tmp_path, instructions_data):
        output = tmp_path / "REVIEW.md"
        generate_review_document(instructions_data, output)
        assert output.exists()

    def test_includes_summary(self, tmp_path, instructions_data):
        output = tmp_path / "REVIEW.md"
        generate_review_document(instructions_data, output)
        content = output.read_text()
        assert "75,850.00" in content
        assert "15,000.00" in content

    def test_includes_warnings(self, tmp_path, instructions_data):
        output = tmp_path / "REVIEW.md"
        generate_review_document(instructions_data, output)
        content = output.read_text()
        assert "Verify standard deduction" in content

    def test_empty_instructions(self, tmp_path):
        output = tmp_path / "REVIEW.md"
        generate_review_document({}, output)
        assert output.exists()
        content = output.read_text()
        assert "Tax Return Review Document" in content


class TestAssembleMain:
    """Tests for the assemble.py CLI entry point."""

    @patch("assemble.load_config")
    @patch("assemble.decrypt_vault", side_effect=Exception("bad passphrase"))
    def test_decrypt_failure_exits(self, mock_decrypt, mock_config, tmp_path, test_config, instructions_data):
        mock_config.return_value = test_config
        inst_file = tmp_path / "instructions.json"
        inst_file.write_text(json.dumps(instructions_data))
        vault_file = tmp_path / "vault.age"
        vault_file.write_text("fake")
        templates = tmp_path / "templates"
        templates.mkdir()

        runner = CliRunner()
        result = runner.invoke(main, [
            "--instructions", str(inst_file),
            "--vault", str(vault_file),
            "--templates", str(templates),
            "--output", str(tmp_path / "output"),
            "--passphrase", "bad",
        ])
        assert result.exit_code != 0

    @patch("assemble.generate_review_document")
    @patch("assemble.assemble_forms", return_value=[Path("1040.pdf")])
    @patch("assemble.decrypt_vault")
    @patch("assemble.load_config")
    def test_full_assembly_success(
        self, mock_config, mock_decrypt, mock_assemble, mock_review,
        tmp_path, test_config, instructions_data, vault_data
    ):
        mock_config.return_value = test_config
        mock_decrypt.return_value = vault_data

        inst_file = tmp_path / "instructions.json"
        inst_file.write_text(json.dumps(instructions_data))
        vault_file = tmp_path / "vault.age"
        vault_file.write_text("fake")
        templates = tmp_path / "templates"
        templates.mkdir()

        runner = CliRunner()
        result = runner.invoke(main, [
            "--instructions", str(inst_file),
            "--vault", str(vault_file),
            "--templates", str(templates),
            "--output", str(tmp_path / "output"),
            "--passphrase", "testpass",
        ])
        assert result.exit_code == 0
        assert mock_assemble.called
        assert mock_review.called
