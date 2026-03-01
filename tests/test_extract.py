"""Tests for extract.py — document detection, JSON parsing, prompt building, summaries."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests
from click.testing import CliRunner

from extract import (
    build_extraction_prompt,
    check_backend_available,
    detect_document_type,
    extract_text_from_pdf,
    extract_with_local_llm,
    extract_with_ollama,
    main,
    parse_json_response,
    process_directory,
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


class TestExtractTextFromPdf:
    """Tests for PDF text extraction with OCR fallback (mocked)."""

    @patch("extract.fitz")
    def test_text_extraction_no_ocr(self, mock_fitz):
        """When page has enough text, OCR should not be triggered."""
        mock_page = MagicMock()
        mock_page.get_text.return_value = "A" * 100  # More than 50 chars
        mock_doc = MagicMock()
        mock_doc.__iter__ = lambda self: iter([mock_page])
        mock_fitz.open.return_value = mock_doc

        result = extract_text_from_pdf(Path("fake.pdf"))
        assert "A" * 100 in result
        mock_page.get_pixmap.assert_not_called()
        mock_doc.close.assert_called_once()

    @patch("extract.pytesseract")
    @patch("extract.fitz")
    def test_ocr_fallback(self, mock_fitz, mock_pytesseract):
        """Short text triggers OCR."""
        mock_page = MagicMock()
        mock_page.get_text.return_value = "Hi"  # Less than 50 chars
        mock_pix = MagicMock()
        mock_pix.tobytes.return_value = b"\x89PNG\r\n\x1a\n"  # PNG header bytes
        mock_page.get_pixmap.return_value = mock_pix
        mock_doc = MagicMock()
        mock_doc.__iter__ = lambda self: iter([mock_page])
        mock_fitz.open.return_value = mock_doc
        mock_fitz.Matrix.return_value = MagicMock()
        mock_pytesseract.image_to_string.return_value = "OCR extracted text here"

        with patch("extract.Image") as mock_image:
            mock_image.open.return_value = MagicMock()
            result = extract_text_from_pdf(Path("fake.pdf"))

        assert "OCR extracted text" in result

    @patch("extract.fitz")
    def test_multi_page(self, mock_fitz):
        pages = []
        for i in range(3):
            p = MagicMock()
            p.get_text.return_value = f"Content of page {i+1} " + "x" * 50
            pages.append(p)

        mock_doc = MagicMock()
        mock_doc.__iter__ = lambda self: iter(pages)
        mock_fitz.open.return_value = mock_doc

        result = extract_text_from_pdf(Path("fake.pdf"))
        assert "Page 1" in result
        assert "Page 2" in result
        assert "Page 3" in result


class TestExtractWithOllama:
    """Tests for Ollama extraction (mocked)."""

    def test_success(self, test_config):
        mock_ollama = MagicMock()
        mock_ollama.chat.return_value = {
            "message": {"content": '{"document_type": "w2", "wages": 75000}'}
        }

        with patch.dict("sys.modules", {"ollama": mock_ollama}):
            result = extract_with_ollama("doc text", "w2", test_config)

        assert result["document_type"] == "w2"
        assert result["wages"] == 75000

    def test_ollama_exception_propagates(self, test_config):
        mock_ollama = MagicMock()
        mock_ollama.chat.side_effect = Exception("connection refused")

        with patch.dict("sys.modules", {"ollama": mock_ollama}):
            with pytest.raises(Exception, match="connection refused"):
                extract_with_ollama("text", "w2", test_config)


class TestExtractWithLocalLlm:
    """Tests for local LLM extraction (mocked)."""

    @patch("extract.requests.post")
    def test_success(self, mock_post, test_config):
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "choices": [{"message": {"content": '{"document_type": "w2", "wages": 75000}'}}]
        }
        mock_response.raise_for_status.return_value = None
        mock_post.return_value = mock_response

        result = extract_with_local_llm("text", "w2", test_config)
        assert result["document_type"] == "w2"

    @patch("extract.requests.post", side_effect=requests.exceptions.ConnectionError)
    def test_connection_error_exits(self, mock_post, test_config):
        with pytest.raises(SystemExit):
            extract_with_local_llm("text", "w2", test_config)

    @patch("extract.requests.post", side_effect=requests.exceptions.Timeout)
    def test_timeout_exits(self, mock_post, test_config):
        with pytest.raises(SystemExit):
            extract_with_local_llm("text", "w2", test_config)


class TestProcessDirectory:
    """Tests for directory processing (mocked)."""

    @patch("extract.extract_with_ollama")
    @patch("extract.detect_document_type")
    @patch("extract.extract_text_from_pdf")
    def test_processes_all_pdfs(self, mock_extract_pdf, mock_detect, mock_ollama, tmp_path, test_config):
        # Create fake PDF files
        input_dir = tmp_path / "input"
        input_dir.mkdir()
        (input_dir / "w2.pdf").write_bytes(b"%PDF")
        (input_dir / "1099.pdf").write_bytes(b"%PDF")

        mock_extract_pdf.return_value = "W-2 content here with enough text"
        mock_detect.return_value = ("w2", 0.9)
        mock_ollama.return_value = {"document_type": "w2", "wages": 50000, "tax_year": 2025}

        output_file = tmp_path / "output.json"
        result = process_directory(input_dir, output_file, test_config, "ollama")

        assert len(result["documents"]) == 2
        assert output_file.exists()

    @patch("extract.extract_text_from_pdf")
    def test_no_pdfs_empty(self, mock_extract, tmp_path, test_config):
        input_dir = tmp_path / "empty"
        input_dir.mkdir()

        result = process_directory(input_dir, tmp_path / "out.json", test_config, "ollama")
        assert result == {}


class TestCheckBackendAvailable:
    """Tests for backend availability checks."""

    def test_ollama_available(self, test_config):
        mock_ollama = MagicMock()
        with patch.dict("sys.modules", {"ollama": mock_ollama}):
            assert check_backend_available("ollama", test_config) is True

    def test_ollama_unavailable(self, test_config):
        mock_ollama = MagicMock()
        mock_ollama.list.side_effect = Exception("not running")
        with patch.dict("sys.modules", {"ollama": mock_ollama}):
            assert check_backend_available("ollama", test_config) is False

    @patch("extract.requests.get", return_value=MagicMock(status_code=200))
    def test_local_available(self, mock_get, test_config):
        assert check_backend_available("local", test_config) is True

    @patch("extract.requests.get", side_effect=Exception("refused"))
    def test_local_unavailable(self, mock_get, test_config):
        assert check_backend_available("local", test_config) is False


class TestExtractMain:
    """Tests for the extract.py CLI entry point."""

    @patch("extract.safe_resolve", side_effect=lambda root, p: Path(p).resolve())
    @patch("extract.check_backend_available", return_value=False)
    @patch("extract.load_config")
    def test_backend_unavailable_exits(self, mock_config, mock_check, mock_safe, tmp_path, test_config):
        mock_config.return_value = test_config
        input_dir = tmp_path / "input"
        input_dir.mkdir()
        (input_dir / "test.pdf").write_bytes(b"%PDF")

        runner = CliRunner()
        result = runner.invoke(main, [
            "--input", str(input_dir),
            "--output", str(tmp_path / "out.json"),
        ])
        assert result.exit_code != 0
