"""Tests for prepare_knowledge.py — page chunking, document creation, section identification."""

from pathlib import Path

import pytest

from prepare_knowledge import chunk_pages, create_final_document, identify_sections


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
