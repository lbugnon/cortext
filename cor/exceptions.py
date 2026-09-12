"""Custom exception hierarchy for Cor.

This module defines all custom exceptions used throughout the codebase.
CLI commands catch these and format them nicely for the user.
"""


class CorError(Exception):
    """Base exception for all Cor errors."""
    code = "cor_error"
    pass


class ValidationError(CorError):
    """Data validation failed (invalid metadata, malformed files, etc.)."""
    code = "validation_error"
    pass


class NotFoundError(CorError):
    """Required file, note, or resource not found."""
    code = "not_found"
    pass


class ConfigError(CorError):
    """Configuration error (missing or invalid config values)."""
    code = "configuration_error"
    pass


class NotInitializedError(CorError):
    """Vault not initialized (no backlog.md found)."""
    code = "not_initialized"
    pass


class AlreadyExistsError(CorError):
    """Resource already exists (file, project, etc.)."""
    code = "already_exists"
    pass


class ExternalServiceError(CorError):
    """Error communicating with external service (API call failed)."""
    code = "external_service_error"
    pass


class ConflictError(CorError):
    """A concurrent write or stale revision prevents a safe mutation."""
    code = "conflict"
    pass
