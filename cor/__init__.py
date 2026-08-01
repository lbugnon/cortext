"""Cor - Plain text knowledge management for the terminal."""

__version__ = "0.1.4"

# Registers the YAML representer that keeps unset optional fields written as
# `due:` instead of `due: null`. Imported here so it applies to every
# frontmatter write, whichever module performs it.
from . import yamlfmt as _yamlfmt  # noqa: F401
