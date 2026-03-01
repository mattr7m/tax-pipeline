"""
pathguard.py - Filesystem containment for the tax processing pipeline

Ensures all file operations stay within the project root directory.
Catches accidental misconfiguration (e.g., --output /tmp/somewhere)
and path traversal (e.g., ../../other-year/data).

Works on any filesystem including 9p virtio mounts — pure Python,
no system-level sandbox needed.
"""

from pathlib import Path
from typing import Union


def safe_resolve(project_root: Path, requested: Union[str, Path]) -> Path:
    """Resolve a path and verify it stays within project_root.

    If *requested* is relative it is resolved against *project_root*.
    Absolute paths are used as-is.  In both cases the final resolved
    path must fall within *project_root* after symlink resolution.

    Returns:
        The resolved, canonicalized Path.

    Raises:
        ValueError: If the resolved path escapes project_root.
    """
    resolved = (project_root / requested).resolve()
    root_resolved = project_root.resolve()
    try:
        resolved.relative_to(root_resolved)
    except ValueError:
        raise ValueError(
            f"Path escapes project root: {requested!s} "
            f"(resolved to {resolved}, root is {root_resolved})"
        ) from None
    return resolved
