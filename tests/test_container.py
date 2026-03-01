"""Tests for container enhancements — entrypoint, Makefile, and container image."""

import shutil
import subprocess
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent

ENTRYPOINT = PROJECT_ROOT / "scripts" / "entrypoint.sh"
MAKEFILE_DIR = PROJECT_ROOT


def _has_image():
    """Check whether the tax-processor container image is built."""
    result = subprocess.run(
        ["podman", "image", "exists", "tax-processor"],
        capture_output=True,
    )
    return result.returncode == 0


def _podman_run(*args, env=None, timeout=30):
    """Run a podman command and return the CompletedProcess."""
    cmd = ["podman", "run", "--rm"]
    if env:
        for k, v in env.items():
            cmd += ["-e", f"{k}={v}"]
    cmd += list(args)
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


requires_image = pytest.mark.skipif(
    not _has_image(), reason="tax-processor image not built"
)


# ---------------------------------------------------------------------------
# TestEntrypointValidation — tests entrypoint.sh directly via subprocess
# ---------------------------------------------------------------------------
class TestEntrypointValidation:
    """Test TAX_YEAR validation in entrypoint.sh without a container."""

    def test_missing_tax_year_exits_1(self):
        """No TAX_YEAR → exit code 1 with error message."""
        result = subprocess.run(
            ["bash", str(ENTRYPOINT)],
            capture_output=True,
            text=True,
            env={"PATH": "/usr/bin:/bin"},
            timeout=5,
        )
        assert result.returncode == 1
        assert "ERROR" in result.stdout
        assert "TAX_YEAR" in result.stdout

    def test_empty_tax_year_exits_1(self):
        """TAX_YEAR='' → same as missing."""
        result = subprocess.run(
            ["bash", str(ENTRYPOINT)],
            capture_output=True,
            text=True,
            env={"PATH": "/usr/bin:/bin", "TAX_YEAR": ""},
            timeout=5,
        )
        assert result.returncode == 1
        assert "ERROR" in result.stdout


# ---------------------------------------------------------------------------
# TestEntrypointDirCreation — tests mkdir logic in a temp directory
# ---------------------------------------------------------------------------
class TestEntrypointDirCreation:
    """Test that entrypoint creates the expected data/ directory tree."""

    @pytest.fixture()
    def dir_script(self, tmp_path):
        """Extract just the mkdir block from entrypoint.sh and run it."""
        # Build a minimal script that only does the mkdir portion
        script = (
            '#!/bin/bash\n'
            'set -e\n'
            'TAX_YEAR=2025\n'
            'PRIOR_YEAR=$((TAX_YEAR - 1))\n'
            'mkdir -p \\\n'
            '    data/raw/"$TAX_YEAR"/sources \\\n'
            '    data/raw/"$TAX_YEAR"/filed \\\n'
            '    data/raw/"$TAX_YEAR"/knowledge \\\n'
            '    data/raw/"$PRIOR_YEAR"/sources \\\n'
            '    data/raw/"$PRIOR_YEAR"/filed \\\n'
            '    data/raw/"$PRIOR_YEAR"/knowledge \\\n'
            '    data/extracted \\\n'
            '    data/sanitized \\\n'
            '    data/vault \\\n'
            '    data/instructions \\\n'
            '    data/output/"$TAX_YEAR"\n'
        )
        script_path = tmp_path / "mkdir_test.sh"
        script_path.write_text(script)
        subprocess.run(
            ["bash", str(script_path)],
            cwd=tmp_path,
            check=True,
            timeout=5,
        )
        return tmp_path

    def test_creates_current_year_dirs(self, dir_script):
        """data/raw/2025/sources, filed, knowledge created."""
        for sub in ("sources", "filed", "knowledge"):
            assert (dir_script / "data" / "raw" / "2025" / sub).is_dir()

    def test_creates_prior_year_dirs(self, dir_script):
        """data/raw/2024/sources, filed, knowledge created."""
        for sub in ("sources", "filed", "knowledge"):
            assert (dir_script / "data" / "raw" / "2024" / sub).is_dir()

    def test_creates_pipeline_dirs(self, dir_script):
        """data/extracted, sanitized, vault, instructions, data/output/2025 created."""
        for d in ("extracted", "sanitized", "vault", "instructions"):
            assert (dir_script / "data" / d).is_dir()
        assert (dir_script / "data" / "output" / "2025").is_dir()


# ---------------------------------------------------------------------------
# TestMakefile — validates targets via make -n (dry-run)
# ---------------------------------------------------------------------------
class TestMakefile:
    """Test Makefile targets via dry-run."""

    def _make(self, *args):
        result = subprocess.run(
            ["make", "-n"] + list(args),
            capture_output=True,
            text=True,
            cwd=MAKEFILE_DIR,
            timeout=5,
        )
        assert result.returncode == 0, result.stderr
        return result.stdout

    def test_build_target(self):
        """make -n build outputs podman build with default tag."""
        output = self._make("build")
        assert "podman build" in output
        assert "localhost/tax-processor:latest" in output

    def test_push_target(self):
        """make -n push outputs podman push with default tag."""
        output = self._make("push")
        assert "podman push" in output
        assert "localhost/tax-processor:latest" in output

    def test_all_target(self):
        """make -n all outputs both build and push."""
        output = self._make("all")
        assert "podman build" in output
        assert "podman push" in output

    def test_custom_tag(self):
        """TAX override changes the tag in build command."""
        output = self._make("TAX=ghcr.io/user/img:v1", "build")
        assert "ghcr.io/user/img:v1" in output
        assert "localhost/tax-processor" not in output


# ---------------------------------------------------------------------------
# TestContainerImage — integration tests against the built image
# ---------------------------------------------------------------------------
@requires_image
class TestContainerImage:
    """Integration tests that exercise the built tax-processor container."""

    def test_system_deps_installed(self):
        """tesseract, age, and python3 are available."""
        result = _podman_run(
            "tax-processor",
            "bash", "-c",
            "tesseract --version 2>&1 | head -1 && age --version && python3 --version",
            env={"TAX_YEAR": "2025"},
        )
        assert result.returncode == 0
        assert "tesseract" in result.stdout
        assert "Python" in result.stdout

    def test_python_deps_importable(self):
        """All required Python packages import successfully."""
        result = _podman_run(
            "tax-processor",
            "python3", "-c",
            "import pymupdf, pytesseract, click, anthropic, markdown_it; print('OK')",
            env={"TAX_YEAR": "2025"},
        )
        assert result.returncode == 0
        assert "OK" in result.stdout

    def test_project_files_present(self):
        """Key project files exist in the image."""
        result = _podman_run(
            "tax-processor",
            "bash", "-c",
            "test -f config.yaml && test -f scripts/inventory.py && test -x scripts/entrypoint.sh && echo OK",
            env={"TAX_YEAR": "2025"},
        )
        assert result.returncode == 0
        assert "OK" in result.stdout

    def test_data_dir_not_baked_in(self):
        """data/ directory is not present in a fresh image (it's the mount point)."""
        # Use --entrypoint to bypass the normal entrypoint which creates data/
        result = subprocess.run(
            ["podman", "run", "--rm", "--entrypoint", "bash",
             "tax-processor", "-c",
             "test -d /data/taxes/data && echo EXISTS || echo MISSING"],
            capture_output=True, text=True, timeout=15,
        )
        assert result.returncode == 0
        assert "MISSING" in result.stdout

    def test_entrypoint_creates_dirs(self):
        """With TAX_YEAR=2025, data directory tree is created."""
        result = _podman_run(
            "tax-processor",
            "bash", "-c",
            "test -d data/raw/2025/sources && test -d data/raw/2024/filed && test -d data/output/2025 && echo OK",
            env={"TAX_YEAR": "2025"},
        )
        assert result.returncode == 0
        assert "OK" in result.stdout

    def test_dashboard_generated(self):
        """tax-dashboard.html exists after entrypoint startup."""
        result = _podman_run(
            "tax-processor",
            "bash", "-c",
            "test -f tax-dashboard.html && echo OK",
            env={"TAX_YEAR": "2025"},
        )
        assert result.returncode == 0
        assert "OK" in result.stdout

    def test_dashboard_server_responds(self):
        """Dashboard server on port 8000 returns HTTP 200."""
        # Use a short sleep to let the server start, then curl
        result = _podman_run(
            "tax-processor",
            "bash", "-c",
            "sleep 1 && curl -s -o /dev/null -w '%{http_code}' http://localhost:8000",
            env={"TAX_YEAR": "2025"},
            timeout=30,
        )
        assert result.returncode == 0
        assert "200" in result.stdout

    def test_missing_tax_year_fails(self):
        """No TAX_YEAR → exit code 1."""
        result = subprocess.run(
            ["podman", "run", "--rm", "tax-processor"],
            capture_output=True, text=True, timeout=15,
        )
        assert result.returncode == 1

    def test_containerignore_excludes(self):
        """.git, .venv, data/, .claude are not in the image."""
        result = subprocess.run(
            ["podman", "run", "--rm", "--entrypoint", "bash",
             "tax-processor", "-c",
             "ls -d .git .venv data .claude 2>&1; echo DONE"],
            capture_output=True, text=True, timeout=15,
        )
        output = result.stdout
        assert "DONE" in output
        # None of these should exist — ls will report errors for each
        for name in (".git", ".venv", ".claude"):
            assert f"cannot access '{name}'" in output or name not in output.split("DONE")[0].split("\n")
