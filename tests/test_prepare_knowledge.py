"""Tests for prepare_knowledge.py — page chunking, document creation, section identification."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests
from click.testing import CliRunner

from prepare_knowledge import (
    chunk_pages,
    create_final_document,
    extract_chunk_with_claude,
    extract_chunk_with_local_llm,
    extract_text_from_pdf,
    identify_sections,
    main,
)


class TestChunkPages:
    """Test combining pages into LLM-sized chunks."""

    def test_single_chunk(self):
        pages = [{"page": 1, "text": "Page one"}, {"page": 2, "text": "Page two"}]
        chunks = chunk_pages(pages, chunk_size=5)
        assert len(chunks) == 1
        assert "[Page 1]" in chunks[0]
        assert "[Page 2]" in chunks[0]

    def test_multiple_chunks(self):
        pages = [{"page": i, "text": f"Page {i}"} for i in range(1, 11)]
        chunks = chunk_pages(pages, chunk_size=3)
        assert len(chunks) == 4  # 3+3+3+1

    def test_exact_division(self):
        pages = [{"page": i, "text": f"Page {i}"} for i in range(1, 7)]
        chunks = chunk_pages(pages, chunk_size=3)
        assert len(chunks) == 2

    def test_empty_pages(self):
        assert chunk_pages([], chunk_size=5) == []

    def test_single_page(self):
        pages = [{"page": 1, "text": "Only page"}]
        chunks = chunk_pages(pages, chunk_size=10)
        assert len(chunks) == 1
        assert "Only page" in chunks[0]

    def test_chunk_size_one(self):
        pages = [{"page": 1, "text": "A"}, {"page": 2, "text": "B"}]
        chunks = chunk_pages(pages, chunk_size=1)
        assert len(chunks) == 2

    def test_page_numbers_preserved(self):
        pages = [{"page": 5, "text": "Content"}, {"page": 6, "text": "More"}]
        chunks = chunk_pages(pages, chunk_size=10)
        assert "[Page 5]" in chunks[0]
        assert "[Page 6]" in chunks[0]


class TestCreateFinalDocument:
    """Test combining extracted sections into final markdown."""

    def test_includes_header(self):
        doc = create_final_document(["Section 1"], "1040", 2025, Path("test.pdf"))
        assert "Form 1040" in doc
        assert "Tax Year 2025" in doc

    def test_includes_source(self):
        doc = create_final_document(["Section 1"], "1040", 2025, Path("i1040gi.pdf"))
        assert "i1040gi.pdf" in doc

    def test_includes_sections(self):
        sections = ["Section one content", "Section two content"]
        doc = create_final_document(sections, "1040", 2025, Path("test.pdf"))
        assert "Section one content" in doc
        assert "Section two content" in doc

    def test_sections_separated(self):
        sections = ["First", "Second"]
        doc = create_final_document(sections, "1040", 2025, Path("test.pdf"))
        assert "---" in doc

    def test_includes_footer(self):
        doc = create_final_document(["Sec"], "1040", 2025, Path("test.pdf"))
        assert "References" in doc
        assert "Disclaimer" in doc

    def test_single_section(self):
        doc = create_final_document(["Only section"], "1040", 2025, Path("test.pdf"))
        assert "Only section" in doc

    def test_empty_sections(self):
        doc = create_final_document([], "1040", 2025, Path("test.pdf"))
        assert "Form 1040" in doc


class TestIdentifySections:
    """Test section identification from page content."""

    def test_filing_requirements(self):
        pages = [{"page": 1, "text": "Filing Requirements: Do You Have To File?"}]
        sections = identify_sections(pages)
        assert "filing_requirements" in sections

    def test_income_section(self):
        pages = [
            {"page": 1, "text": "Introduction"},
            {"page": 5, "text": "Income: Line 1 Wages and salary"},
        ]
        sections = identify_sections(pages)
        assert "income" in sections

    def test_multiple_sections(self):
        pages = [
            {"page": 1, "text": "Filing Requirements info"},
            {"page": 3, "text": "Income section starts here with Line 1"},
            {"page": 10, "text": "Deductions and Standard Deduction"},
        ]
        sections = identify_sections(pages)
        assert len(sections) >= 2

    def test_empty_pages(self):
        assert identify_sections([]) == {}

    def test_no_matching_sections(self):
        pages = [{"page": 1, "text": "Random unrelated content"}]
        sections = identify_sections(pages)
        # May or may not match depending on keywords; shouldn't crash
        assert isinstance(sections, dict)

    def test_page_ranges(self):
        pages = [
            {"page": 1, "text": "Filing Requirements here"},
            {"page": 2, "text": "More filing requirements"},
            {"page": 3, "text": "Income section Line 1"},
            {"page": 4, "text": "More income"},
        ]
        sections = identify_sections(pages)
        if "filing_requirements" in sections:
            start, end = sections["filing_requirements"]
            assert start >= 1


class TestExtractTextFromPdfKnowledge:
    """Tests for PDF text extraction via pdfplumber (mocked)."""

    def test_extracts_pages(self):
        mock_page1 = MagicMock()
        mock_page1.extract_text.return_value = "Line 1: Wages\nEnter your wages here"
        mock_page2 = MagicMock()
        mock_page2.extract_text.return_value = "Line 2b: Taxable Interest"
        mock_pdf = MagicMock()
        mock_pdf.pages = [mock_page1, mock_page2]
        mock_pdf.__enter__ = lambda self: self
        mock_pdf.__exit__ = MagicMock(return_value=False)

        mock_pdfplumber = MagicMock()
        mock_pdfplumber.open.return_value = mock_pdf

        with patch.dict("sys.modules", {"pdfplumber": mock_pdfplumber}):
            result = extract_text_from_pdf(Path("fake.pdf"))

        assert len(result) == 2
        assert result[0]["page"] == 1
        assert "Wages" in result[0]["text"]

    def test_max_pages_limit(self):
        pages = [MagicMock() for _ in range(10)]
        for i, p in enumerate(pages):
            p.extract_text.return_value = f"Content for page {i+1} with enough text"
        mock_pdf = MagicMock()
        mock_pdf.pages = pages
        mock_pdf.__enter__ = lambda self: self
        mock_pdf.__exit__ = MagicMock(return_value=False)

        mock_pdfplumber = MagicMock()
        mock_pdfplumber.open.return_value = mock_pdf

        with patch.dict("sys.modules", {"pdfplumber": mock_pdfplumber}):
            result = extract_text_from_pdf(Path("fake.pdf"), max_pages=3)

        assert len(result) <= 3

    def test_empty_pages_skipped(self):
        mock_page1 = MagicMock()
        mock_page1.extract_text.return_value = "Real content here"
        mock_page2 = MagicMock()
        mock_page2.extract_text.return_value = ""  # Empty
        mock_pdf = MagicMock()
        mock_pdf.pages = [mock_page1, mock_page2]
        mock_pdf.__enter__ = lambda self: self
        mock_pdf.__exit__ = MagicMock(return_value=False)

        mock_pdfplumber = MagicMock()
        mock_pdfplumber.open.return_value = mock_pdf

        with patch.dict("sys.modules", {"pdfplumber": mock_pdfplumber}):
            result = extract_text_from_pdf(Path("fake.pdf"))

        assert len(result) == 1


class TestExtractChunkWithClaude:
    """Tests for Claude API chunk extraction (mocked)."""

    def test_success(self, test_config):
        mock_anthropic = MagicMock()
        mock_client = MagicMock()
        mock_anthropic.Anthropic.return_value = mock_client
        mock_client.messages.create.return_value = MagicMock(
            content=[MagicMock(text="## Line 1: Wages\nEnter wages from W-2")]
        )

        with patch.dict("sys.modules", {"anthropic": mock_anthropic}):
            result = extract_chunk_with_claude("chunk text", 1, 3, "1040", test_config)
        assert "Line 1" in result
        assert "Wages" in result

    def test_passes_correct_params(self, test_config):
        mock_anthropic = MagicMock()
        mock_client = MagicMock()
        mock_anthropic.Anthropic.return_value = mock_client
        mock_client.messages.create.return_value = MagicMock(
            content=[MagicMock(text="result")]
        )

        with patch.dict("sys.modules", {"anthropic": mock_anthropic}):
            extract_chunk_with_claude("text", 2, 5, "1040", test_config)

        call_kwargs = mock_client.messages.create.call_args[1]
        user_msg = call_kwargs["messages"][0]["content"]
        assert "chunk 2 of 5" in user_msg
        assert "Form 1040" in user_msg

    def test_import_error_exits(self, test_config):
        with patch.dict("sys.modules", {"anthropic": None}):
            with pytest.raises(SystemExit):
                extract_chunk_with_claude("text", 1, 1, "1040", test_config)


class TestExtractChunkWithLocalLlm:
    """Tests for local LLM chunk extraction (mocked)."""

    @patch("prepare_knowledge.requests.post")
    def test_success(self, mock_post, test_config):
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "choices": [{"message": {"content": "## Filing Requirements\nDetails..."}}]
        }
        mock_response.raise_for_status.return_value = None
        mock_post.return_value = mock_response

        result = extract_chunk_with_local_llm("text", 1, 3, "1040", test_config)
        assert "Filing Requirements" in result

    @patch("prepare_knowledge.requests.post", side_effect=requests.exceptions.ConnectionError)
    def test_connection_error_exits(self, mock_post, test_config):
        with pytest.raises(SystemExit):
            extract_chunk_with_local_llm("text", 1, 1, "1040", test_config)

    @patch("prepare_knowledge.requests.post", side_effect=requests.exceptions.Timeout)
    def test_timeout_returns_placeholder(self, mock_post, test_config):
        result = extract_chunk_with_local_llm("text", 2, 5, "1040", test_config)
        assert "timed out" in result.lower()


class TestPrepareKnowledgeMain:
    """Tests for the prepare_knowledge.py CLI entry point."""

    @patch("prepare_knowledge.extract_chunk_with_claude", return_value="## Section\nContent")
    @patch("prepare_knowledge.extract_text_from_pdf")
    @patch("prepare_knowledge.load_config")
    def test_full_pipeline(self, mock_config, mock_extract_pdf, mock_chunk_claude, tmp_path, test_config):
        mock_config.return_value = test_config
        mock_extract_pdf.return_value = [
            {"page": 1, "text": "Filing requirements content"}
        ]

        pdf_file = tmp_path / "test.pdf"
        pdf_file.write_bytes(b"%PDF-1.4")
        output_file = tmp_path / "output.md"

        runner = CliRunner()
        result = runner.invoke(main, [
            "--pdf", str(pdf_file),
            "--form", "1040",
            "--year", "2025",
            "--output", str(output_file),
        ])
        assert result.exit_code == 0
        assert output_file.exists()
        content = output_file.read_text()
        assert "Form 1040" in content

    @patch("prepare_knowledge.extract_chunk_with_local_llm", return_value="## Section")
    @patch("prepare_knowledge.extract_chunk_with_claude")
    @patch("prepare_knowledge.extract_text_from_pdf")
    @patch("prepare_knowledge.load_config")
    def test_local_backend_dispatches(
        self, mock_config, mock_extract_pdf, mock_claude, mock_local,
        tmp_path, test_config
    ):
        mock_config.return_value = test_config
        mock_extract_pdf.return_value = [{"page": 1, "text": "Content"}]

        pdf_file = tmp_path / "test.pdf"
        pdf_file.write_bytes(b"%PDF-1.4")

        runner = CliRunner()
        result = runner.invoke(main, [
            "--pdf", str(pdf_file),
            "--form", "1040",
            "--year", "2025",
            "--backend", "local",
            "--output", str(tmp_path / "out.md"),
        ])
        assert result.exit_code == 0
        assert mock_local.called
        mock_claude.assert_not_called()

    @patch("prepare_knowledge.extract_chunk_with_claude", return_value="## Section")
    @patch("prepare_knowledge.extract_text_from_pdf")
    @patch("prepare_knowledge.load_config")
    def test_max_pages_option(self, mock_config, mock_extract_pdf, mock_chunk, tmp_path, test_config):
        mock_config.return_value = test_config
        mock_extract_pdf.return_value = [{"page": 1, "text": "Content"}]

        pdf_file = tmp_path / "test.pdf"
        pdf_file.write_bytes(b"%PDF-1.4")

        runner = CliRunner()
        runner.invoke(main, [
            "--pdf", str(pdf_file),
            "--form", "1040",
            "--year", "2025",
            "--max-pages", "5",
            "--output", str(tmp_path / "out.md"),
        ])
        mock_extract_pdf.assert_called_once_with(pdf_file, 5)
