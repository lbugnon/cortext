"""Link regex patterns for Cor.

Centralizes the regex patterns used for link manipulation, which were
previously scattered across maintenance.py and other modules.

This module also used to hold a `LinkManager` class and a `Link` dataclass.
Nothing in production ever called a LinkManager method - `sync/runner.py`
constructed one and then used only the patterns below, doing its own link
rewriting - and its `validate_links` duplicated the live implementation in
`sync/runner.py`. Both were removed rather than leaving two implementations of
link handling, one of them unreachable.
"""

import re

class LinkPatterns:
    """Centralized regex patterns for link manipulation.

    All 35+ patterns from maintenance.py consolidated here.
    """

    # Basic link pattern: [text](target)
    LINK = re.compile(r'\[([^\]]+)\]\(([^)]+)\)')

    # Generic capture group for link manipulation: ([text](), target, ())
    LINK_CAPTURE = re.compile(r'(\[[^\]]+\]\()([^)]+)(\))')

    # Archive link patterns
    ARCHIVE_LINK = re.compile(r'(\[[^\]]+\]\()archive/([^)]+)(\))')
    ARCHIVE_LINK_WITH_TEXT = re.compile(r'\[([^\]]+)\]\(archive/([^)]+)\)')

    # Parent prefix patterns (../)
    PARENT_PREFIX_LINK = re.compile(r'(\[[^\]]+\]\()\.\.\/([^)]+)(\))')

    # Task entry patterns (for task lists in parents)
    # Matches: - [x] [Title](target) or - [x] [Title](archive/target)
    TASK_ENTRY = re.compile(
        r'^(- \[([x .o~/])\] \[[^\]]+\]\((?:archive/)?([^\)]+)\))$',
        re.MULTILINE
    )

    # Task entry with capture groups for modification
    TASK_ENTRY_DETAILED = re.compile(
        r"(- )\[[x .o~/]\]( \[[^\]]+\]\()(archive/)?([^\)]+)(\))",
        re.MULTILINE
    )

    # Backlink patterns (parent references)
    BACKLINK = re.compile(r'\[<([^\]]+)\]\(([^)]+)\)')
    BACKLINK_WITH_ARCHIVE = re.compile(r'\[<([^\]]+)\]\((?:archive/)?([^)]+)\)')

    # External link prefixes
    EXTERNAL_PREFIXES = ('http://', 'https://', '#', 'mailto:')
