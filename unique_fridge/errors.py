class FridgeError(Exception):
    """Base exception for the library."""


class ProtocolError(FridgeError):
    """Malformed, unexpected, or incomplete protocol data."""


class ValidationError(FridgeError):
    """A requested operation violates a characterized protocol invariant."""


class CommunicationError(FridgeError):
    """BLE discovery, connection, timeout, or response failure."""


class UnsupportedStateError(ValidationError):
    """An undefined/reserved protocol value was requested or received."""
