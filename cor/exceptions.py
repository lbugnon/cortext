"""Custom exception hierarchy for Cor.

This module defines all custom exceptions used throughout the codebase.
CLI commands catch these and format them nicely for the user.
"""


class CorError(Exception):
    """Base exception for all Cor errors."""
    pass


class ValidationError(CorError):
    """Data validation failed (invalid metadata, malformed files, etc.)."""
    pass


class NotFoundError(CorError):
    """Required file, note, or resource not found."""
    pass


class ConfigError(CorError):
    """Configuration error (missing or invalid config values)."""
    pass


class NotInitializedError(CorError):
    """Vault not initialized (no backlog.md found)."""
    pass


class AlreadyExistsError(CorError):
    """Resource already exists (file, project, etc.)."""
    pass


class SyncError(CorError):
    """Error during sync/maintenance operations."""
    pass


class ExternalServiceError(CorError):
    """Error communicating with external service (API call failed)."""
    pass
