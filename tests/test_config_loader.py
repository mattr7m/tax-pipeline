"""Tests for config_loader.py — config resolution and fallback logic."""

from pathlib import Path
from unittest.mock import patch

import pytest

from config_loader import load_config, PROJECT_ROOT


class TestProjectRoot:
    """Test PROJECT_ROOT resolution."""

    def test_is_absolute(self):
        assert PROJECT_ROOT.is_absolute()

    def test_points_to_repo(self):
        assert (PROJECT_ROOT / "scripts" / "config_loader.py").exists()


class TestLoadConfig:
    """Test config file resolution order."""

    def test_loads_data_config_first(self, tmp_path):
        """data/config.yaml is preferred over root config.yaml."""
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        (data_dir / "config.yaml").write_text("paths:\n  output: data/output\n")
        (tmp_path / "config.yaml").write_text("paths:\n  output: OLD\n")

        with patch("config_loader.PROJECT_ROOT", tmp_path):
            cfg = load_config()
        assert cfg["paths"]["output"] == "data/output"

    def test_falls_back_to_root_config(self, tmp_path):
        """When data/config.yaml is absent, root config.yaml is used."""
        (tmp_path / "config.yaml").write_text("paths:\n  output: root-output\n")

        with patch("config_loader.PROJECT_ROOT", tmp_path):
            cfg = load_config()
        assert cfg["paths"]["output"] == "root-output"

    def test_falls_back_to_example(self, tmp_path):
        """When both config.yaml files are absent, config.yaml.example is used."""
        (tmp_path / "config.yaml.example").write_text("paths:\n  output: example-output\n")

        with patch("config_loader.PROJECT_ROOT", tmp_path):
            cfg = load_config()
        assert cfg["paths"]["output"] == "example-output"

    def test_raises_when_no_config(self, tmp_path):
        """FileNotFoundError when no config file exists at all."""
        with patch("config_loader.PROJECT_ROOT", tmp_path):
            with pytest.raises(FileNotFoundError):
                load_config()
