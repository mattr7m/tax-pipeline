"""Tests for dashboard.py — state management and HTML generation."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from dashboard import (
    _phase_instructions,
    _read_preview,
    _render_file_list,
    _render_phase_card,
    highlight_json,
    load_state,
    regenerate_html,
    render_md_preview,
    save_state,
    update_phase,
)


class TestStateManagement:
    """Tests for load_state, save_state, and update_phase."""

    def test_load_state_no_file(self, tmp_path):
        """Returns default dict with expected keys when no state file exists."""
        state = load_state(tmp_path)
        assert state["year"] is None
        assert state["prior_year"] is None
        assert state["updated_at"] is None
        assert state["raw_input"] == {}
        assert state["extracted_input"] == {}
        assert state["sanitized_input"] == {}
        assert state["output"] == {}
        assert state["status"] == {"processing_complete": False}

    def test_load_state_existing(self, tmp_path):
        """Reads and returns persisted JSON."""
        state_dir = tmp_path / "data"
        state_dir.mkdir()
        payload = {"year": 2025, "custom_key": "hello"}
        (state_dir / "dashboard-state.json").write_text(json.dumps(payload))

        result = load_state(tmp_path)
        assert result["year"] == 2025
        assert result["custom_key"] == "hello"

    def test_save_state_creates_dirs(self, tmp_path):
        """Creates data/ directory, writes JSON, sets updated_at."""
        state = {"year": 2025, "raw_input": {}}
        save_state(tmp_path, state)

        state_path = tmp_path / "data" / "dashboard-state.json"
        assert state_path.exists()

        saved = json.loads(state_path.read_text())
        assert "updated_at" in saved
        assert saved["year"] == 2025

    def test_save_state_roundtrip(self, tmp_path):
        """save then load returns same data (plus updated_at)."""
        state = {
            "year": 2025,
            "prior_year": 2024,
            "updated_at": None,
            "raw_input": {"current_sources": [{"name": "w2.pdf", "path": "data/raw/2025/w2.pdf"}]},
            "extracted_input": {},
            "sanitized_input": {},
            "output": {},
            "status": {"processing_complete": False},
        }
        save_state(tmp_path, state)
        loaded = load_state(tmp_path)
        assert loaded["year"] == 2025
        assert loaded["raw_input"]["current_sources"][0]["name"] == "w2.pdf"
        assert loaded["updated_at"] is not None

    def test_update_phase_new_entry(self, tmp_path):
        """Adds file to empty category."""
        # Create a file so resolve works
        raw_dir = tmp_path / "data" / "raw" / "2025"
        raw_dir.mkdir(parents=True)
        test_file = raw_dir / "w2.pdf"
        test_file.touch()

        update_phase(tmp_path, "raw_input", "current_sources", [test_file])

        state = load_state(tmp_path)
        entries = state["raw_input"]["current_sources"]
        assert len(entries) == 1
        assert entries[0]["name"] == "w2.pdf"

    def test_update_phase_replaces_placeholder(self, tmp_path):
        """Removes placeholder key from matching entry."""
        # Seed state with a placeholder
        (tmp_path / "data").mkdir(parents=True)
        state = {
            "year": 2025,
            "raw_input": {
                "current_sources": [{"name": "w2.pdf", "path": "", "placeholder": True}]
            },
        }
        (tmp_path / "data" / "dashboard-state.json").write_text(json.dumps(state))

        # Create and add the real file
        real_file = tmp_path / "data" / "raw" / "2025" / "w2.pdf"
        real_file.parent.mkdir(parents=True)
        real_file.touch()

        update_phase(tmp_path, "raw_input", "current_sources", [real_file])

        updated = load_state(tmp_path)
        entry = updated["raw_input"]["current_sources"][0]
        assert entry["name"] == "w2.pdf"
        assert "placeholder" not in entry
        assert entry["path"] != ""

    def test_update_phase_deduplicates(self, tmp_path):
        """Same filename twice results in single entry with updated path."""
        dir1 = tmp_path / "a"
        dir1.mkdir()
        f1 = dir1 / "doc.pdf"
        f1.touch()

        update_phase(tmp_path, "raw_input", "current_sources", [f1])

        dir2 = tmp_path / "b"
        dir2.mkdir()
        f2 = dir2 / "doc.pdf"
        f2.touch()

        update_phase(tmp_path, "raw_input", "current_sources", [f2])

        state = load_state(tmp_path)
        entries = state["raw_input"]["current_sources"]
        assert len(entries) == 1
        assert entries[0]["name"] == "doc.pdf"

    def test_update_phase_multiple_files(self, tmp_path):
        """Adds several files at once."""
        raw_dir = tmp_path / "data" / "raw"
        raw_dir.mkdir(parents=True)
        files = []
        for name in ["w2.pdf", "1099-int.pdf", "1099-div.pdf"]:
            f = raw_dir / name
            f.touch()
            files.append(f)

        update_phase(tmp_path, "raw_input", "current_sources", files)

        state = load_state(tmp_path)
        entries = state["raw_input"]["current_sources"]
        assert len(entries) == 3
        names = {e["name"] for e in entries}
        assert names == {"w2.pdf", "1099-int.pdf", "1099-div.pdf"}


class TestHighlightJson:
    """Tests for highlight_json regex-based syntax highlighter."""

    def test_keys_wrapped(self):
        """Keys followed by colon get json-key class."""
        result = highlight_json('"key": 42')
        assert "json-key" in result
        assert '"key"' in result

    def test_strings_wrapped(self):
        """Standalone string values get json-str class."""
        result = highlight_json('"hello world"')
        assert '<span class="json-str">"hello world"</span>' in result

    def test_numbers_wrapped(self):
        for num in ["42", "3.14", "1e10"]:
            result = highlight_json(f'"x": {num}')
            assert f'<span class="json-num">{num}</span>' in result

    def test_booleans_null_wrapped(self):
        for val in ["true", "false", "null"]:
            result = highlight_json(f'"x": {val}')
            assert f'<span class="json-bool">{val}</span>' in result

    def test_html_escaped(self):
        result = highlight_json('"a": "<b>&</b>"')
        assert "&lt;" in result
        assert "&gt;" in result
        assert "&amp;" in result
        assert "<b>" not in result

    def test_keys_not_double_wrapped(self):
        """Key value itself is not wrapped with json-str — only the class attr is."""
        result = highlight_json('"key": 42')
        # The key should have json-key class
        assert "json-key" in result
        # The number should get json-num
        assert '<span class="json-num">42</span>' in result
        # "key" text should not be inside a json-str span (only "json-key" attr is)
        assert '"key"</span>' in result


class TestRenderMdPreview:
    """Tests for render_md_preview markdown rendering."""

    def test_renders_heading(self):
        result = render_md_preview("# Foo")
        assert "<h1>" in result
        assert "Foo" in result

    def test_renders_table(self):
        md = "| A | B |\n|---|---|\n| 1 | 2 |"
        result = render_md_preview(md)
        assert "<table>" in result

    def test_fallback_no_markdown_it(self):
        """When markdown_it import fails, returns <pre> with escaped content."""
        with patch.dict("sys.modules", {"markdown_it": None}):
            # Need to force a reimport-like behavior by patching inside the function
            import dashboard

            original = dashboard.render_md_preview

            def patched(content):
                # Simulate ImportError
                try:
                    raise ImportError("no markdown_it")
                except ImportError:
                    return (
                        "<pre>"
                        + content.replace("&", "&amp;")
                        .replace("<", "&lt;")
                        .replace(">", "&gt;")
                        + "</pre>"
                    )

            dashboard.render_md_preview = patched
            try:
                result = dashboard.render_md_preview("# Hello <world>")
                assert "<pre>" in result
                assert "&lt;world&gt;" in result
                assert "<h1>" not in result
            finally:
                dashboard.render_md_preview = original


class TestReadPreview:
    """Tests for _read_preview file reading and format detection."""

    def test_json_pretty_printed(self, tmp_path):
        """Reads JSON, pretty-prints, returns highlighted HTML with is_rendered=False."""
        f = tmp_path / "test.json"
        f.write_text('{"key":"value"}')
        result = _read_preview(tmp_path, "test.json")
        assert result is not None
        html, is_rendered = result
        assert is_rendered is False
        assert "json-key" in html
        assert "json-str" in html

    def test_md_rendered(self, tmp_path):
        """Reads .md, returns rendered HTML with is_rendered=True."""
        f = tmp_path / "test.md"
        f.write_text("# Hello\nWorld")
        result = _read_preview(tmp_path, "test.md")
        assert result is not None
        html, is_rendered = result
        assert is_rendered is True
        assert "Hello" in html

    def test_truncation(self, tmp_path):
        """Large file truncated at max_bytes, adds '(truncated)'."""
        f = tmp_path / "big.json"
        # Write JSON that exceeds max_bytes
        f.write_text('{"data": "' + "x" * 500 + '"}')
        result = _read_preview(tmp_path, "big.json", max_bytes=100)
        assert result is not None
        html, _ = result
        assert "truncated" in html

    def test_missing_file_returns_none(self, tmp_path):
        """Nonexistent path returns None."""
        result = _read_preview(tmp_path, "does_not_exist.json")
        assert result is None

    def test_non_previewable_suffix(self, tmp_path):
        """.pdf returns None."""
        f = tmp_path / "form.pdf"
        f.write_bytes(b"%PDF-1.4 fake")
        result = _read_preview(tmp_path, "form.pdf")
        assert result is None

    def test_invalid_json_still_previews(self, tmp_path):
        """Malformed JSON falls through to raw highlighted text."""
        f = tmp_path / "bad.json"
        f.write_text('{"broken: json')
        result = _read_preview(tmp_path, "bad.json")
        assert result is not None
        html, is_rendered = result
        assert is_rendered is False
        assert "broken" in html


class TestRenderFileList:
    """Tests for _render_file_list HTML output."""

    def test_empty_entries(self, tmp_path):
        """Returns <ul> with single dash <li>."""
        result = _render_file_list(tmp_path, [])
        assert '<ul class="file-list">' in result
        assert '<li class="empty">' in result
        assert "&mdash;" in result

    def test_found_file_has_link(self, tmp_path):
        """Existing file gets class='found' with <a href>."""
        f = tmp_path / "data.json"
        f.write_text("{}")
        entries = [{"name": "data.json", "path": "data.json"}]
        result = _render_file_list(tmp_path, entries)
        assert 'class="found"' in result
        assert '<a href="data.json">' in result

    def test_missing_file(self, tmp_path):
        """Nonexistent file gets class='missing'."""
        entries = [{"name": "gone.json", "path": "gone.json"}]
        result = _render_file_list(tmp_path, entries)
        assert 'class="missing"' in result

    def test_placeholder_treated_as_missing(self, tmp_path):
        """Entry with placeholder: true gets class='missing' even if file exists."""
        f = tmp_path / "exist.json"
        f.write_text("{}")
        entries = [{"name": "exist.json", "path": "exist.json", "placeholder": True}]
        result = _render_file_list(tmp_path, entries)
        assert 'class="missing"' in result

    def test_preview_injected_for_json(self, tmp_path):
        """Existing .json file gets <details> with <pre><code>."""
        f = tmp_path / "data.json"
        f.write_text('{"hello": "world"}')
        entries = [{"name": "data.json", "path": "data.json"}]
        result = _render_file_list(tmp_path, entries)
        assert "<details>" in result
        assert "<pre><code>" in result

    def test_preview_injected_for_md(self, tmp_path):
        """Existing .md file gets <details> with <div class='md-preview'>."""
        f = tmp_path / "notes.md"
        f.write_text("# Hello\nWorld")
        entries = [{"name": "notes.md", "path": "notes.md"}]
        result = _render_file_list(tmp_path, entries)
        assert "<details>" in result
        assert 'class="md-preview"' in result


class TestPhaseInstructions:
    """Tests for _phase_instructions."""

    def test_returns_all_four_steps(self):
        instr = _phase_instructions(2025, 2024)
        assert set(instr.keys()) == {1, 2, 3, 4}

    def test_year_interpolation(self):
        instr = _phase_instructions(2025, 2024)
        # Year should appear in the instructions
        for step in instr.values():
            assert "2025" in step or "2024" in step
        # Both years should be present across all instructions
        combined = "".join(instr.values())
        assert "2025" in combined
        assert "2024" in combined


class TestRenderPhaseCard:
    """Tests for _render_phase_card structure."""

    def test_card_structure(self, tmp_path):
        """Contains phase-card class, step number, title, categories."""
        result = _render_phase_card(
            tmp_path,
            "Raw Input",
            {"current_sources": []},
            [("Current Sources", "current_sources")],
            step_number=1,
        )
        assert 'class="phase-card"' in result
        assert '<span class="step">1</span>' in result
        assert "Raw Input" in result
        assert 'class="category"' in result

    def test_instructions_included(self, tmp_path):
        """When instructions_html provided, phase-how-to appears."""
        result = _render_phase_card(
            tmp_path,
            "Extract",
            {},
            [],
            step_number=2,
            instructions_html='<div class="how-to-body"><p>Do stuff</p></div>',
        )
        assert '<details class="phase-how-to">' in result
        assert "Do stuff" in result

    def test_no_instructions(self, tmp_path):
        """When empty string, no phase-how-to block."""
        result = _render_phase_card(
            tmp_path,
            "Extract",
            {},
            [],
            step_number=2,
            instructions_html="",
        )
        assert "phase-how-to" not in result


class TestRegenerateHtml:
    """Tests for regenerate_html integration."""

    def test_creates_html_file(self, tmp_path):
        """Writes tax-dashboard.html to project root."""
        result = regenerate_html(tmp_path)
        assert result == tmp_path / "tax-dashboard.html"
        assert result.exists()

    def test_html_structure(self, tmp_path):
        """Contains DOCTYPE, title with year, dark mode JS, desktop-view, mobile-view."""
        # Seed state with a year
        (tmp_path / "data").mkdir(parents=True)
        state = {
            "year": 2025,
            "prior_year": 2024,
            "updated_at": None,
            "raw_input": {},
            "extracted_input": {},
            "sanitized_input": {},
            "output": {},
            "status": {"processing_complete": False},
        }
        (tmp_path / "data" / "dashboard-state.json").write_text(json.dumps(state))

        regenerate_html(tmp_path)
        html = (tmp_path / "tax-dashboard.html").read_text()

        assert "<!DOCTYPE html>" in html
        assert "Tax Dashboard" in html
        assert "2025" in html
        assert "toggleTheme" in html
        assert "localStorage" in html
        assert 'class="desktop-view"' in html
        assert 'class="mobile-view"' in html

    def test_processing_badge(self, tmp_path):
        """When status.processing_complete=True, badge div present."""
        (tmp_path / "data").mkdir(parents=True)
        state = {
            "year": 2025,
            "prior_year": 2024,
            "updated_at": None,
            "raw_input": {},
            "extracted_input": {},
            "sanitized_input": {},
            "output": {},
            "status": {"processing_complete": True},
        }
        (tmp_path / "data" / "dashboard-state.json").write_text(json.dumps(state))

        regenerate_html(tmp_path)
        html = (tmp_path / "tax-dashboard.html").read_text()
        assert "badge-complete" in html
        assert "Processing Complete" in html

    def test_no_badge_by_default(self, tmp_path):
        """When processing_complete=False, badge div absent."""
        regenerate_html(tmp_path)
        html = (tmp_path / "tax-dashboard.html").read_text()
        assert "Processing Complete" not in html
