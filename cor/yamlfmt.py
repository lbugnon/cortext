"""YAML output tweaks applied to every frontmatter write.

Imported for its side effect from `cor/__init__.py`, so it is in force before
any of the ~20 `frontmatter.dump()` call sites run.
"""

import yaml

# python-frontmatter dumps with its own SafeDumper; registering against
# yaml.SafeDumper would not take effect.
from frontmatter.default_handlers import SafeDumper


def _represent_none(dumper, _value):
    """Write unset optional fields as `due:` rather than `due: null`.

    The stock templates ship empty keys (`priority:`, `due:`) as a reminder
    that the field exists. YAML parses those as None, and the default
    representer writes them back as the literal `null`, so every project file
    picked up `priority: null` / `due: null` noise the first time any command
    rewrote its frontmatter.

    Round-tripping is unaffected: an empty value still parses back to None.
    """
    return dumper.represent_scalar("tag:yaml.org,2002:null", "")


SafeDumper.add_representer(type(None), _represent_none)
