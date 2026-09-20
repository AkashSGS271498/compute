"""
File-safety utilities for job workspaces.

These helpers are intentionally strict: they prefer to reject a
filename and return an error rather than silently allow a path that
could escape the workspace.
"""

import os
import re
from typing import Optional


# Characters that are never valid inside a simple filename.
_UNSAFE_RE = re.compile(r'[/\\:\*\?"<>\|]')


def validate_filename(name: str) -> Optional[str]:
    """
    Validate and normalise a bare filename (not a path).

    Rules
    -----
    - Must be a non-empty string.
    - Must not contain path separators (/ \\), null bytes, or shell
      metacharacters.
    - Must not be a dotfile-only name such as '.' or '..'.
    - Must not start with a dash (prevents flag injection via CLI).
    - Length <= 255 characters (POSIX limit).

    Returns
    -------
    The validated filename (unchanged) on success, or ``None`` if the
    name is rejected.
    """
    if not isinstance(name, str) or not name:
        return None

    # Null byte or non-printable ASCII
    if "\x00" in name or any(ord(c) < 32 for c in name):
        return None

    # Slash / backslash / other shell-unsafe characters
    if _UNSAFE_RE.search(name):
        return None

    # Only dots (e.g. '.' or '..')
    stripped = name.strip(".")
    if not stripped:
        return None

    # Leading dash could be interpreted as a CLI flag
    if name.startswith("-"):
        return None

    # POSIX filename length limit
    if len(name) > 255:
        return None

    # The basename must equal the name itself (no directory component)
    if os.path.basename(name) != name:
        return None

    return name


def safe_join(base_dir: str, filename: str) -> Optional[str]:
    """
    Join *base_dir* and *filename* and verify the result stays inside
    *base_dir*.

    Returns the resolved absolute path on success, or ``None`` if the
    path would escape the base directory.
    """
    if validate_filename(filename) is None:
        return None

    base = os.path.realpath(base_dir)
    candidate = os.path.realpath(os.path.join(base, filename))

    # Must be the base dir itself or a direct child
    if candidate == base or candidate.startswith(base + os.sep):
        return candidate

    return None
