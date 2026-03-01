"""Tests for pathguard.py — filesystem containment validation."""

import os
from pathlib import Path

import pytest

from pathguard import safe_resolve


@pytest.fixture
def project_root(tmp_path):
    """Use a temporary directory as a mock project root."""
    root = tmp_path / "project"
    root.mkdir()
    (root / "data" / "raw" / "2025").mkdir(parents=True)
    (root / "data" / "extracted").mkdir(parents=True)
    (root / "data" / "output").mkdir(parents=True)
    (root / "scripts").mkdir()
    return root


class TestSafeResolveValid:
    """Paths that should be accepted."""

    def test_relative_subdir(self, project_root):
        result = safe_resolve(project_root, "data/raw/2025")
        assert result == (project_root / "data/raw/2025").resolve()

    def test_relative_file(self, project_root):
        result = safe_resolve(project_root, "data/extracted/2025.json")
        assert result == (project_root / "data/extracted/2025.json").resolve()

    def test_absolute_within_root(self, project_root):
        target = project_root / "data" / "output"
        result = safe_resolve(project_root, target)
        assert result == target.resolve()

    def test_root_itself(self, project_root):
        """The project root itself is a valid path."""
        result = safe_resolve(project_root, ".")
        assert result == project_root.resolve()

    def test_path_object(self, project_root):
        result = safe_resolve(project_root, Path("data"))
        assert result == (project_root / "data").resolve()

    def test_string_path(self, project_root):
        result = safe_resolve(project_root, "scripts")
        assert result == (project_root / "scripts").resolve()

    def test_dotdot_that_stays_inside(self, project_root):
        """data/raw/../extracted should resolve within root."""
        result = safe_resolve(project_root, "data/raw/../extracted")
        assert result == (project_root / "data/extracted").resolve()

    def test_nonexistent_but_valid(self, project_root):
        """Path doesn't need to exist — just needs to be inside root."""
        result = safe_resolve(project_root, "data/future/2030.json")
        assert result == (project_root / "data/future/2030.json").resolve()


class TestSafeResolveRejected:
    """Paths that must be rejected."""

    def test_absolute_outside_root(self, project_root):
        with pytest.raises(ValueError, match="Path escapes project root"):
            safe_resolve(project_root, "/tmp/evil")

    def test_relative_escape_parent(self, project_root):
        with pytest.raises(ValueError, match="Path escapes project root"):
            safe_resolve(project_root, "..")

    def test_relative_escape_deep(self, project_root):
        with pytest.raises(ValueError, match="Path escapes project root"):
            safe_resolve(project_root, "../../other-repo/data")

    def test_sneaky_traversal_via_subdir(self, project_root):
        """data/raw/../../.. escapes through nested traversal."""
        with pytest.raises(ValueError, match="Path escapes project root"):
            safe_resolve(project_root, "data/raw/../../..")

    def test_absolute_sibling_dir(self, project_root):
        sibling = project_root.parent / "other-clone"
        with pytest.raises(ValueError, match="Path escapes project root"):
            safe_resolve(project_root, sibling)

    def test_absolute_root_fs(self, project_root):
        with pytest.raises(ValueError, match="Path escapes project root"):
            safe_resolve(project_root, "/")

    def test_home_directory(self, project_root):
        with pytest.raises(ValueError, match="Path escapes project root"):
            safe_resolve(project_root, Path.home())


class TestSafeResolveSymlinks:
    """Symlinks that point outside should be caught."""

    def test_symlink_escape(self, project_root, tmp_path):
        """A symlink inside the project that points outside should be rejected."""
        outside = tmp_path / "outside"
        outside.mkdir()
        link = project_root / "data" / "sneaky_link"
        link.symlink_to(outside)

        with pytest.raises(ValueError, match="Path escapes project root"):
            safe_resolve(project_root, "data/sneaky_link")

    def test_symlink_internal_ok(self, project_root):
        """A symlink inside the project pointing to another internal path is fine."""
        target = project_root / "data" / "raw"
        link = project_root / "data" / "alias"
        link.symlink_to(target)

        result = safe_resolve(project_root, "data/alias")
        assert result == target.resolve()


class TestSafeResolveErrorMessages:
    """Verify error messages are informative."""

    def test_error_includes_requested_path(self, project_root):
        with pytest.raises(ValueError, match="/tmp/evil"):
            safe_resolve(project_root, "/tmp/evil")

    def test_error_includes_resolved_path(self, project_root):
        with pytest.raises(ValueError, match="resolved to"):
            safe_resolve(project_root, "../../escape")

    def test_error_includes_root(self, project_root):
        with pytest.raises(ValueError, match="root is"):
            safe_resolve(project_root, "/tmp/evil")


class TestSafeResolveEdgeCases:
    """Boundary conditions."""

    def test_empty_string_resolves_to_root(self, project_root):
        """Empty string joined with root gives root."""
        result = safe_resolve(project_root, "")
        assert result == project_root.resolve()

    def test_dot_resolves_to_root(self, project_root):
        result = safe_resolve(project_root, ".")
        assert result == project_root.resolve()

    def test_deeply_nested_valid(self, project_root):
        result = safe_resolve(project_root, "a/b/c/d/e/f/g")
        assert result == (project_root / "a/b/c/d/e/f/g").resolve()

    def test_return_type_is_path(self, project_root):
        result = safe_resolve(project_root, "data")
        assert isinstance(result, Path)

    def test_returned_path_is_absolute(self, project_root):
        result = safe_resolve(project_root, "data")
        assert result.is_absolute()
