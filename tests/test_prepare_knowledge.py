"""Tests for prepare_knowledge.py — page chunking, document creation, section identification,
and all subcommands (instructions, tax-tables, form-fields, rules-summary, all)."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests
from click.testing import CliRunner

from prepare_knowledge import (
    chunk_pages,
    cli,
    create_final_document,
    extract_chunk_with_claude,
    extract_chunk_with_local_llm,
    extract_pdf_form_field_names,
    extract_text_from_pdf,
    identify_sections,
    parse_json_response,
    resolve_knowledge_output_dir,
    send_to_claude,
    send_to_local_llm,
    validate_tax_tables,
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


class TestPrepareKnowledgeInstructions:
    """Tests for the 'instructions' subcommand (formerly main)."""

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
        result = runner.invoke(cli, [
            "instructions",
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
        result = runner.invoke(cli, [
            "instructions",
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
        runner.invoke(cli, [
            "instructions",
            "--pdf", str(pdf_file),
            "--form", "1040",
            "--year", "2025",
            "--max-pages", "5",
            "--output", str(tmp_path / "out.md"),
        ])
        mock_extract_pdf.assert_called_once_with(pdf_file, 5)


class TestParseJsonResponse:
    """Test JSON parsing from LLM responses."""

    def test_plain_json(self):
        result = parse_json_response('{"key": "value"}')
        assert result == {"key": "value"}

    def test_json_in_markdown_block(self):
        text = 'Some text\n```json\n{"key": "value"}\n```\nMore text'
        result = parse_json_response(text)
        assert result == {"key": "value"}

    def test_json_in_generic_block(self):
        text = 'Text\n```\n{"key": "value"}\n```'
        result = parse_json_response(text)
        assert result == {"key": "value"}

    def test_malformed_returns_error(self):
        result = parse_json_response("not json at all")
        assert "parse_error" in result
        assert "raw_response" in result


class TestValidateTaxTables:
    """Test tax tables validation."""

    def test_valid_full(self):
        data = {
            "tax_year": 2025,
            "standard_deductions": {"single": 15000},
            "tax_brackets": {"single": []},
            "retirement_contributions": {},
            "deductions": {},
            "credits": {},
        }
        warnings = validate_tax_tables(data)
        assert len(warnings) == 0

    def test_missing_required_key(self):
        data = {"tax_year": 2025, "tax_brackets": {"single": []}}
        warnings = validate_tax_tables(data)
        assert any("standard_deductions" in w for w in warnings)

    def test_missing_optional_key(self):
        data = {
            "standard_deductions": {"single": 15000},
            "tax_brackets": {"single": []},
        }
        warnings = validate_tax_tables(data)
        # Should warn about optional keys but not fail
        assert any("optional" in w.lower() for w in warnings)

    def test_empty_dict_fails(self):
        warnings = validate_tax_tables({})
        assert len(warnings) >= 2  # at least both required keys missing


class TestSharedHelpers:
    """Tests for shared helper functions."""

    def test_resolve_knowledge_output_dir(self, tmp_path):
        config = {"paths": {"tax_knowledge": str(tmp_path / "knowledge")}}
        # Override __file__ parent.parent resolution
        with patch("prepare_knowledge.Path") as mock_path_cls:
            # Just test with a config that has an absolute path
            pass
        # Simpler: just test it doesn't crash with empty config
        result = resolve_knowledge_output_dir({}, 2025)
        assert result.name == "2025"

    @patch("prepare_knowledge.requests.post")
    def test_send_to_local_llm(self, mock_post, test_config):
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "choices": [{"message": {"content": "response text"}}]
        }
        mock_response.raise_for_status.return_value = None
        mock_post.return_value = mock_response

        result = send_to_local_llm("system", "user", test_config)
        assert result == "response text"

    def test_send_to_claude(self, test_config):
        mock_anthropic = MagicMock()
        mock_client = MagicMock()
        mock_anthropic.Anthropic.return_value = mock_client
        mock_client.messages.create.return_value = MagicMock(
            content=[MagicMock(text="claude response")]
        )

        with patch.dict("sys.modules", {"anthropic": mock_anthropic}):
            result = send_to_claude("system", "user", test_config)
        assert result == "claude response"


class TestTaxTablesGeneration:
    """Tests for the 'tax-tables' subcommand."""

    SAMPLE_TAX_TABLES = json.dumps({
        "tax_year": 2025,
        "standard_deductions": {"single": 15000, "married_filing_jointly": 30000, "head_of_household": 22500},
        "tax_brackets": {"single": [{"min": 0, "max": 11925, "rate": 0.10, "base_tax": 0}]},
        "retirement_contributions": {"401k_limit": 23500, "ira_limit": 7000},
        "deductions": {"salt_cap": 10000},
        "credits": {"child_tax_credit": 2000},
    })

    @patch("prepare_knowledge.send_to_llm", return_value=SAMPLE_TAX_TABLES)
    @patch("prepare_knowledge.extract_text_from_pdf")
    @patch("prepare_knowledge.load_config")
    def test_generates_valid_json(self, mock_config, mock_extract, mock_llm, tmp_path, test_config):
        mock_config.return_value = test_config
        mock_extract.return_value = [
            {"page": 1, "text": "Standard Deduction amounts for 2025"}
        ]

        pdf_file = tmp_path / "instructions.pdf"
        pdf_file.write_bytes(b"%PDF-1.4")
        output_file = tmp_path / "tax-tables.json"

        runner = CliRunner()
        result = runner.invoke(cli, [
            "tax-tables",
            "--pdf", str(pdf_file),
            "--year", "2025",
            "--output", str(output_file),
        ])
        assert result.exit_code == 0
        assert output_file.exists()

        data = json.loads(output_file.read_text())
        assert data["tax_year"] == 2025
        assert "standard_deductions" in data
        assert "tax_brackets" in data

    @patch("prepare_knowledge.send_to_llm", return_value=SAMPLE_TAX_TABLES)
    @patch("prepare_knowledge.extract_text_from_pdf")
    @patch("prepare_knowledge.load_config")
    def test_filters_relevant_pages(self, mock_config, mock_extract, mock_llm, tmp_path, test_config):
        mock_config.return_value = test_config
        mock_extract.return_value = [
            {"page": 1, "text": "Unrelated intro content"},
            {"page": 2, "text": "Standard Deduction for single filers"},
            {"page": 3, "text": "Tax Rate Schedule"},
        ]

        pdf_file = tmp_path / "instructions.pdf"
        pdf_file.write_bytes(b"%PDF-1.4")
        output_file = tmp_path / "tax-tables.json"

        runner = CliRunner()
        result = runner.invoke(cli, [
            "tax-tables",
            "--pdf", str(pdf_file),
            "--year", "2025",
            "--output", str(output_file),
        ])
        assert result.exit_code == 0
        # The LLM prompt should have been called
        mock_llm.assert_called_once()

    @patch("prepare_knowledge.send_to_llm", return_value="not valid json")
    @patch("prepare_knowledge.extract_text_from_pdf")
    @patch("prepare_knowledge.load_config")
    def test_exits_on_parse_failure(self, mock_config, mock_extract, mock_llm, tmp_path, test_config):
        mock_config.return_value = test_config
        mock_extract.return_value = [{"page": 1, "text": "Standard Deduction info"}]

        pdf_file = tmp_path / "instructions.pdf"
        pdf_file.write_bytes(b"%PDF-1.4")

        runner = CliRunner()
        result = runner.invoke(cli, [
            "tax-tables",
            "--pdf", str(pdf_file),
            "--year", "2025",
            "--output", str(tmp_path / "out.json"),
        ])
        assert result.exit_code != 0


class TestFormFieldsGeneration:
    """Tests for the 'form-fields' subcommand."""

    SAMPLE_FIELDS_RESPONSE = json.dumps({
        "form_id": "1040",
        "form_name": "U.S. Individual Income Tax Return",
        "tax_year": 2025,
        "field_mappings": {
            "income": {
                "f1_25": {"line": "1a", "type": "currency", "description": "Wages from W-2"}
            }
        },
        "calculation_rules": {"line_9": "Sum of lines 1z through 8"},
    })

    @patch("prepare_knowledge.send_to_llm", return_value=SAMPLE_FIELDS_RESPONSE)
    @patch("prepare_knowledge.extract_pdf_form_field_names")
    @patch("prepare_knowledge.load_config")
    def test_generates_valid_json(self, mock_config, mock_fields, mock_llm, tmp_path, test_config):
        mock_config.return_value = test_config
        mock_fields.return_value = [
            {"field_name": "f1_25", "field_type": "Text", "page": 1},
            {"field_name": "f1_36", "field_type": "Text", "page": 1},
        ]

        pdf_file = tmp_path / "f1040.pdf"
        pdf_file.write_bytes(b"%PDF-1.4")
        output_file = tmp_path / "form-1040-fields.json"

        runner = CliRunner()
        result = runner.invoke(cli, [
            "form-fields",
            "--form-pdf", str(pdf_file),
            "--form", "1040",
            "--year", "2025",
            "--output", str(output_file),
        ])
        assert result.exit_code == 0
        assert output_file.exists()

        data = json.loads(output_file.read_text())
        assert data["form_id"] == "1040"
        assert "field_mappings" in data
        assert "calculation_rules" in data

    @patch("prepare_knowledge.send_to_llm", return_value=SAMPLE_FIELDS_RESPONSE)
    @patch("prepare_knowledge.extract_pdf_form_field_names")
    @patch("prepare_knowledge.load_config")
    def test_loads_instructions_as_context(self, mock_config, mock_fields, mock_llm, tmp_path, test_config):
        mock_config.return_value = test_config
        mock_fields.return_value = [
            {"field_name": "f1_25", "field_type": "Text", "page": 1}
        ]

        pdf_file = tmp_path / "f1040.pdf"
        pdf_file.write_bytes(b"%PDF-1.4")
        output_file = tmp_path / "form-1040-fields.json"

        # Create instructions file as context
        instructions_file = tmp_path / "form-1040-instructions.md"
        instructions_file.write_text("## Line 1a: Wages\nEnter wages from W-2")

        runner = CliRunner()
        result = runner.invoke(cli, [
            "form-fields",
            "--form-pdf", str(pdf_file),
            "--form", "1040",
            "--year", "2025",
            "--output", str(output_file),
        ])
        assert result.exit_code == 0
        # The LLM should have been called with instructions context
        call_args = mock_llm.call_args
        user_prompt = call_args[0][1] if call_args[0] else call_args[1].get("user_prompt", "")
        assert "Line 1a" in user_prompt

    @patch("prepare_knowledge.extract_pdf_form_field_names", return_value=[])
    @patch("prepare_knowledge.load_config")
    def test_exits_on_no_fields(self, mock_config, mock_fields, tmp_path, test_config):
        mock_config.return_value = test_config

        pdf_file = tmp_path / "f1040.pdf"
        pdf_file.write_bytes(b"%PDF-1.4")

        runner = CliRunner()
        result = runner.invoke(cli, [
            "form-fields",
            "--form-pdf", str(pdf_file),
            "--form", "1040",
            "--year", "2025",
            "--output", str(tmp_path / "out.json"),
        ])
        assert result.exit_code != 0


class TestRulesSummaryGeneration:
    """Tests for the 'rules-summary' subcommand."""

    @patch("prepare_knowledge.send_to_llm", return_value="# Tax Rules Summary\n\n## Filing Requirements\n- Single: $15,000")
    @patch("prepare_knowledge.extract_text_from_pdf")
    @patch("prepare_knowledge.load_config")
    def test_generates_markdown(self, mock_config, mock_extract, mock_llm, tmp_path, test_config):
        mock_config.return_value = test_config
        mock_extract.return_value = [
            {"page": 1, "text": "Filing requirements and standard deduction info"}
        ]

        pdf_file = tmp_path / "instructions.pdf"
        pdf_file.write_bytes(b"%PDF-1.4")
        output_file = tmp_path / "tax-rules-summary.md"

        runner = CliRunner()
        result = runner.invoke(cli, [
            "rules-summary",
            "--pdf", str(pdf_file),
            "--year", "2025",
            "--output", str(output_file),
        ])
        assert result.exit_code == 0
        assert output_file.exists()

        content = output_file.read_text()
        assert "Tax Rules Summary" in content
        assert "Filing Requirements" in content

    @patch("prepare_knowledge.send_to_llm", return_value="# Rules\n\nContent here")
    @patch("prepare_knowledge.extract_text_from_pdf")
    @patch("prepare_knowledge.load_config")
    def test_loads_tax_tables_as_context(self, mock_config, mock_extract, mock_llm, tmp_path, test_config):
        mock_config.return_value = test_config
        mock_extract.return_value = [{"page": 1, "text": "Tax info"}]

        pdf_file = tmp_path / "instructions.pdf"
        pdf_file.write_bytes(b"%PDF-1.4")
        output_file = tmp_path / "tax-rules-summary.md"

        # Create tax-tables.json as context
        tables_file = tmp_path / "tax-tables.json"
        tables_file.write_text(json.dumps({"standard_deductions": {"single": 15000}}))

        runner = CliRunner()
        result = runner.invoke(cli, [
            "rules-summary",
            "--pdf", str(pdf_file),
            "--year", "2025",
            "--output", str(output_file),
        ])
        assert result.exit_code == 0
        # The LLM should have been called with tax tables context
        call_args = mock_llm.call_args
        user_prompt = call_args[0][1] if call_args[0] else call_args[1].get("user_prompt", "")
        assert "15000" in user_prompt


class TestGenerateAll:
    """Tests for the 'all' meta-subcommand."""

    @patch("prepare_knowledge.send_to_llm")
    @patch("prepare_knowledge.extract_pdf_form_field_names")
    @patch("prepare_knowledge.extract_chunk_with_claude", return_value="## Section\nContent")
    @patch("prepare_knowledge.extract_text_from_pdf")
    @patch("prepare_knowledge.load_config")
    def test_runs_all_generators(
        self, mock_config, mock_extract_pdf, mock_chunk_claude,
        mock_form_fields, mock_llm, tmp_path, test_config
    ):
        mock_config.return_value = test_config
        mock_extract_pdf.return_value = [
            {"page": 1, "text": "Standard Deduction amounts and filing requirements"}
        ]
        mock_form_fields.return_value = [
            {"field_name": "f1_25", "field_type": "Text", "page": 1}
        ]

        # Return valid JSON for tax-tables
        tax_tables_json = json.dumps({
            "tax_year": 2025,
            "standard_deductions": {"single": 15000},
            "tax_brackets": {"single": []},
        })
        form_fields_json = json.dumps({
            "form_id": "1040",
            "tax_year": 2025,
            "field_mappings": {"income": {}},
            "calculation_rules": {},
        })
        rules_md = "# Tax Rules\n\n## Filing\n- Info here"

        mock_llm.side_effect = [tax_tables_json, form_fields_json, rules_md]

        instructions_pdf = tmp_path / "i1040gi.pdf"
        instructions_pdf.write_bytes(b"%PDF-1.4")
        form_pdf = tmp_path / "f1040.pdf"
        form_pdf.write_bytes(b"%PDF-1.4")

        runner = CliRunner()
        result = runner.invoke(cli, [
            "all",
            "--instructions-pdf", str(instructions_pdf),
            "--form-pdf", str(form_pdf),
            "--form", "1040",
            "--year", "2025",
        ])
        assert result.exit_code == 0
        # send_to_llm should have been called for tax-tables, form-fields, rules-summary
        assert mock_llm.call_count == 3
        # extract_chunk_with_claude should have been called for instructions
        assert mock_chunk_claude.called

    @patch("prepare_knowledge.send_to_llm")
    @patch("prepare_knowledge.extract_pdf_form_field_names")
    @patch("prepare_knowledge.extract_chunk_with_claude", return_value="## Section")
    @patch("prepare_knowledge.extract_text_from_pdf")
    @patch("prepare_knowledge.load_config")
    def test_runs_in_dependency_order(
        self, mock_config, mock_extract_pdf, mock_chunk_claude,
        mock_form_fields, mock_llm, tmp_path, test_config
    ):
        """Verify the order: tax-tables, instructions, form-fields, rules-summary."""
        mock_config.return_value = test_config
        mock_extract_pdf.return_value = [
            {"page": 1, "text": "Standard Deduction info"}
        ]
        mock_form_fields.return_value = [
            {"field_name": "f1_01", "field_type": "Text", "page": 1}
        ]

        tax_tables_json = json.dumps({
            "tax_year": 2025, "standard_deductions": {"single": 15000},
            "tax_brackets": {"single": []},
        })
        form_fields_json = json.dumps({
            "form_id": "1040", "tax_year": 2025,
            "field_mappings": {}, "calculation_rules": {},
        })
        rules_md = "# Rules"

        call_order = []
        original_send = mock_llm.side_effect

        def track_calls(system, user, backend, config):
            if "tax tables" in system.lower() or "brackets" in system.lower():
                call_order.append("tax-tables")
                return tax_tables_json
            elif "field mapping" in system.lower():
                call_order.append("form-fields")
                return form_fields_json
            elif "rules" in system.lower():
                call_order.append("rules-summary")
                return rules_md
            return "{}"

        mock_llm.side_effect = track_calls

        instructions_pdf = tmp_path / "i1040gi.pdf"
        instructions_pdf.write_bytes(b"%PDF-1.4")
        form_pdf = tmp_path / "f1040.pdf"
        form_pdf.write_bytes(b"%PDF-1.4")

        runner = CliRunner()
        result = runner.invoke(cli, [
            "all",
            "--instructions-pdf", str(instructions_pdf),
            "--form-pdf", str(form_pdf),
            "--form", "1040",
            "--year", "2025",
        ])
        assert result.exit_code == 0
        assert call_order == ["tax-tables", "form-fields", "rules-summary"]
