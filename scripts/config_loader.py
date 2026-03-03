"""
config_loader.py - Shared configuration loading for all pipeline scripts

Provides:
  - PROJECT_ROOT: Path to the project root directory
  - load_config(): Load config.yaml with data/ path fallback
"""

import sys
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def load_config() -> dict:
    """Load configuration from data/config.yaml, falling back to config.yaml.

    Search order:
      1. data/config.yaml   (operational config inside the data mount)
      2. config.yaml        (legacy location / config.yaml.example copy)

    A warning is printed to stderr when using the fallback so that
    developers notice they should copy config.yaml.example to
    data/config.yaml.
    """
    data_config = PROJECT_ROOT / "data" / "config.yaml"
    if data_config.exists():
        with open(data_config) as f:
            return yaml.safe_load(f)

    root_config = PROJECT_ROOT / "config.yaml"
    if root_config.exists():
        print(
            "Warning: using config.yaml at project root; "
            "copy config.yaml.example to data/config.yaml for the standard layout",
            file=sys.stderr,
        )
        with open(root_config) as f:
            return yaml.safe_load(f)

    # Last resort: try config.yaml.example so things work out-of-the-box
    example_config = PROJECT_ROOT / "config.yaml.example"
    if example_config.exists():
        print(
            "Warning: using config.yaml.example; "
            "copy it to data/config.yaml and customise",
            file=sys.stderr,
        )
        with open(example_config) as f:
            return yaml.safe_load(f)

    raise FileNotFoundError(
        "No config.yaml found. Copy config.yaml.example to data/config.yaml"
    )
