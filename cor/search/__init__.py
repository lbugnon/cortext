"""Search and completion functionality for Cor.

This package contains:
- fuzzy.py: Fuzzy matching
- completion.py: Consolidated shell completion logic
- content.py: Full-text content search via ripgrep
"""

from .fuzzy import (
    fuzzy_match,
    resolve_file_fuzzy,
    resolve_task_fuzzy,
    resolve_files,
    get_file_path,
    get_task_file_stems,
)
from .content import (
    search_content,
    parse_search_query,
    filter_matches,
    SearchMatch,
)

__all__ = [
    "fuzzy_match",
    "resolve_file_fuzzy",
    "resolve_task_fuzzy",
    "resolve_files",
    "get_file_path",
    "get_task_file_stems",
    "search_content",
    "parse_search_query",
    "filter_matches",
    "SearchMatch",
]
