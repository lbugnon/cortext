"""Vault configuration and path resolution.

This module reads and writes a YAML config at ~/.config/cor/config.yaml
to store the active vault and other user preferences. 
"""

import os
from pathlib import Path

import yaml

from .exceptions import ConfigError


# The file whose presence makes a directory a vault. Referenced only via
# is_vault_initialized().
VAULT_MARKER = "backlog.md"

def _config_dir() -> Path:
    """Return the configuration directory (respects XDG_CONFIG_HOME)."""
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        base = Path(xdg)
    else:
        base = Path.home() / ".config"
    return base / "cor"


def _config_file() -> Path:
    """Return the path to the config file."""
    return _config_dir() / "config.yaml"


def load_config() -> dict:
    """Load config from the config file, returning an empty dict if missing."""
    cfg = _config_file()
    if not cfg.exists():
        return {}
    try:
        return yaml.safe_load(cfg.read_text()) or {}
    except yaml.YAMLError:
        return {}


def save_config(config: dict) -> None:
    """Save config to the config file, creating directories as needed.
    
    Sets file permissions to 0o600 (owner read/write only) for security.
    """
    cfg_dir = _config_dir()
    cfg_dir.mkdir(parents=True, exist_ok=True)
    cfg_file = _config_file()
    cfg_file.write_text(yaml.dump(config, default_flow_style=False))
    # Restrict permissions: owner read/write only
    os.chmod(cfg_file, 0o600)


def _find_vault_from_cwd() -> Path | None:
    """Walk up from cwd looking for a directory containing the vault marker."""
    current = Path.cwd()
    for ancestor in [current, *current.parents]:
        if is_vault_initialized(ancestor):
            return ancestor
    return None


#: Resolution sources returned by resolve_vault(), in precedence order.
VAULT_SOURCE_CWD = "cwd"
VAULT_SOURCE_ENV = "env"
VAULT_SOURCE_CONFIG = "config"

#: Human-readable labels for each source, for `cor status` and friends.
VAULT_SOURCE_LABELS = {
    VAULT_SOURCE_CWD: "found from cwd",
    VAULT_SOURCE_ENV: "COR_VAULT",
    VAULT_SOURCE_CONFIG: "config",
}


def resolve_vault() -> tuple[Path, str]:
    """Resolve the active vault, reporting which rule produced it.

    Precedence:
    1. Nearest ancestor of cwd containing backlog.md (vault marker).
    2. COR_VAULT environment variable.
    3. The default vault in ~/.config/cor/config.yaml.

    Enables multiple vaults on one machine: cd into a vault and `cor`
    operates on it. The config file remains the fallback when neither cwd
    nor env points at a vault - but that fallback is a *guess*, which is
    why the source is returned alongside the path. Callers that are about
    to write should refuse to act on VAULT_SOURCE_CONFIG without
    confirming; see cor.utils.require_init.

    Returns:
        (vault path, one of VAULT_SOURCE_*).

    Raises:
        ConfigError: if none of the three rules resolves.
    """
    discovered = _find_vault_from_cwd()
    if discovered is not None:
        return discovered, VAULT_SOURCE_CWD

    env_vault = os.environ.get("COR_VAULT")
    if env_vault:
        return Path(env_vault), VAULT_SOURCE_ENV

    default = get_default_vault_path()
    if default is None:
        raise ConfigError(
            "Vault path not configured. Run 'cor init' from a vault directory, "
            "set COR_VAULT, or run 'cor vault add <name> /path/to/notes'."
        )
    return default, VAULT_SOURCE_CONFIG


def get_vault_path() -> Path:
    """Resolve the active vault path. See resolve_vault() for precedence."""
    return resolve_vault()[0]


def vault_source_label(source: str) -> str:
    """Human-readable description of a VAULT_SOURCE_* value."""
    return VAULT_SOURCE_LABELS.get(source, "unknown")


def set_vault_path(path: Path) -> None:
    """Save vault path to config file, registering it if it is new.

    Keeps the legacy `vault` key and the named registry in step: the path
    becomes the default vault and, if not already registered under some
    name, is added under its directory name.
    """
    resolved = path.resolve()
    config = load_config()
    config["vault"] = str(resolved)

    vaults = _read_vaults(config)
    existing = next((n for n, p in vaults.items() if p == resolved), None)
    if existing is None:
        existing = _unique_vault_name(resolved.name, vaults)
        vaults[existing] = resolved

    config["vaults"] = {n: str(p) for n, p in vaults.items()}
    config["default"] = existing
    save_config(config)


# --- Named vault registry ---
#
# Multiple vaults on one machine need short names: the shell prompt has to
# say *which* vault in a couple of characters, and `:vault personal` is only
# possible if vaults are named. The registry lives in config.yaml as:
#
#   vaults:
#     work: /home/user/notes/work
#     personal: /home/user/notes/personal
#   default: work
#   vault: /home/user/notes/work    # legacy mirror of the default
#
# The legacy single `vault:` key keeps working; it is read as a one-entry
# registry so existing configs need no migration.


def _read_vaults(config: dict) -> dict[str, Path]:
    """Extract the vault registry from an already-loaded config dict."""
    raw = config.get("vaults")
    if isinstance(raw, dict) and raw:
        return {str(name): Path(path).expanduser() for name, path in raw.items()}

    # Back-compat: a config predating the registry has only `vault`. Present
    # it as a single named entry rather than rewriting the file behind the
    # user's back - `cor vault add` is what migrates.
    legacy = config.get("vault")
    if legacy:
        path = Path(legacy).expanduser()
        return {path.name: path}

    return {}


def _unique_vault_name(preferred: str, vaults: dict[str, Path]) -> str:
    """Return `preferred`, suffixed with a counter if already taken."""
    if preferred not in vaults:
        return preferred
    n = 2
    while f"{preferred}{n}" in vaults:
        n += 1
    return f"{preferred}{n}"


def get_vaults() -> dict[str, Path]:
    """Return the registered vaults as name -> path."""
    return _read_vaults(load_config())


def add_vault(name: str, path: Path) -> None:
    """Register a vault under `name`, making it the default if it is the first."""
    config = load_config()
    vaults = _read_vaults(config)
    vaults[name] = path.resolve()
    config["vaults"] = {n: str(p) for n, p in vaults.items()}
    if not config.get("default"):
        config["default"] = name
        config["vault"] = str(vaults[name])
    save_config(config)


def remove_vault(name: str) -> None:
    """Unregister a vault. Does not touch the vault directory itself."""
    config = load_config()
    vaults = _read_vaults(config)
    if name not in vaults:
        raise ConfigError(f"No vault named '{name}'. Known: {_known_vaults_hint(vaults)}")
    del vaults[name]
    config["vaults"] = {n: str(p) for n, p in vaults.items()}

    if config.get("default") == name:
        # Promote an arbitrary survivor rather than leaving a dangling default.
        replacement = next(iter(vaults), None)
        if replacement is None:
            config.pop("default", None)
            config.pop("vault", None)
        else:
            config["default"] = replacement
            config["vault"] = str(vaults[replacement])
    save_config(config)


def get_default_vault() -> str | None:
    """Return the name of the default vault, or None if none is registered."""
    config = load_config()
    vaults = _read_vaults(config)
    if not vaults:
        return None

    default = config.get("default")
    if default in vaults:
        return default

    # A stale or missing `default` should not make the whole registry
    # unusable; fall back to whichever vault the legacy key points at.
    legacy = config.get("vault")
    if legacy:
        legacy_path = Path(legacy).expanduser().resolve()
        for name, path in vaults.items():
            if path.resolve() == legacy_path:
                return name
    return next(iter(vaults))


def get_default_vault_path() -> Path | None:
    """Return the path of the default vault, or None if none is registered."""
    name = get_default_vault()
    if name is None:
        return None
    return get_vaults()[name]


def set_default_vault(name: str) -> None:
    """Make `name` the vault used when cwd and COR_VAULT say nothing."""
    config = load_config()
    vaults = _read_vaults(config)
    if name not in vaults:
        raise ConfigError(f"No vault named '{name}'. Known: {_known_vaults_hint(vaults)}")
    config["vaults"] = {n: str(p) for n, p in vaults.items()}
    config["default"] = name
    config["vault"] = str(vaults[name])
    save_config(config)


def vault_name_for(path: Path) -> str:
    """Return the registered name for `path`, or its directory name.

    A vault discovered by walking up from cwd need not be registered, but
    the shell prompt still needs something short to display.
    """
    resolved = path.expanduser().resolve()
    for name, candidate in get_vaults().items():
        if candidate.expanduser().resolve() == resolved:
            return name
    return resolved.name


def _known_vaults_hint(vaults: dict[str, Path] | None = None) -> str:
    """Render the registered vault names for use in an error message."""
    if vaults is None:
        vaults = get_vaults()
    if not vaults:
        return "none registered (use 'cor vault add <name> <path>')"
    return ", ".join(sorted(vaults))


def is_vault_initialized(vault_path: Path | None = None) -> bool:
    """Check whether a directory is an initialized vault.

    The presence of VAULT_MARKER is what makes a directory a vault, for both
    resolution rule 1 and every "is this usable?" check. This is the only
    place that fact is spelled out.
    """
    vault = vault_path or get_vault_path()
    return (vault / VAULT_MARKER).exists()


def get_verbosity() -> int:
    """Get verbosity level from config (default: 1)."""
    config = load_config()
    return config.get("verbosity", 1)


def set_verbosity(level: int) -> None:
    """Save verbosity level to config file (0-3)."""
    if not 0 <= level <= 3:
        raise ConfigError("Verbosity level must be between 0 and 3")
    config = load_config()
    config["verbosity"] = level
    save_config(config)


def config_file() -> Path:
    """Return the current config file path."""
    return _config_file()


def get_remote_inbox() -> str | None:
    """Get Telegram bot token for remote inbox.

    Returns token from config file or TELEGRAM_BOT_TOKEN env var, or None if not configured.
    """
    # Check environment variable first
    env_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if env_token:
        return env_token

    # Fall back to config file
    config = load_config()
    return config.get("remote_inbox")


def set_remote_inbox(bot_token: str) -> None:
    """Save Telegram bot token to config file."""
    config = load_config()
    config["remote_inbox"] = bot_token
    save_config(config)


# --- Per-vault state ---
#
# Focus is a property of a vault, not of the machine. Kept global, focusing
# a project in one vault would skew fuzzy matching and completion in every
# other vault, where that project does not even exist.


def _current_vault_key() -> str:
    """Identify the active vault for keying per-vault state."""
    try:
        return vault_name_for(get_vault_path())
    except ConfigError:
        return ""


def get_focused_project() -> str | None:
    """Get the focused project for the active vault, or None."""
    config = load_config()
    key = _current_vault_key()
    state = config.get("vault_state") or {}
    if key and isinstance(state, dict) and key in state:
        return (state[key] or {}).get("focus")

    # Back-compat: a config written before focus was per-vault has a single
    # top-level key. Honour it rather than silently dropping the user's focus.
    return config.get("focused_project")


def set_focused_project(project: str) -> None:
    """Set the focused project for the active vault."""
    config = load_config()
    key = _current_vault_key()
    state = config.get("vault_state")
    if not isinstance(state, dict):
        state = {}
    entry = state.get(key) if isinstance(state.get(key), dict) else {}
    entry["focus"] = project
    state[key] = entry
    config["vault_state"] = state
    # The legacy global key would otherwise shadow nothing but still confuse
    # anyone reading the file; retire it as soon as we write per-vault state.
    config.pop("focused_project", None)
    save_config(config)


def clear_focused_project() -> None:
    """Clear the focused project for the active vault."""
    config = load_config()
    key = _current_vault_key()
    changed = False

    state = config.get("vault_state")
    if isinstance(state, dict) and isinstance(state.get(key), dict):
        if state[key].pop("focus", None) is not None:
            changed = True
        if not state[key]:
            del state[key]
        config["vault_state"] = state

    if config.pop("focused_project", None) is not None:
        changed = True

    if changed:
        save_config(config)


# Default timezone is UTC
default_timezone = "UTC"


def get_timezone() -> str:
    """Get the timezone from config (default: UTC).
    
    Returns timezone string like 'America/Argentina/Buenos_Aires' or 'UTC'.
    """
    config = load_config()
    return config.get("timezone", default_timezone)


def set_timezone(timezone: str) -> None:
    """Set the timezone in config.
    
    Args:
        timezone: Timezone string like 'America/Argentina/Buenos_Aires'
    """
    config = load_config()
    config["timezone"] = timezone
    save_config(config)
