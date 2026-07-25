class MootdxNextError(Exception):
    """Base exception for the next-generation core."""


class TransportError(MootdxNextError):
    """Base exception for transport-layer failures."""


class TransportConnectionError(TransportError):
    """Raised when a socket or connection cannot be established."""


class TransportTimeoutError(TransportError):
    """Raised when a transport operation exceeds its timeout."""


class EmptyResponseError(TransportError):
    """Raised when the server returns no response body."""


class InvalidResponseHeaderError(TransportError):
    """Raised when a response header is truncated or malformed."""


class PayloadDecompressionError(TransportError):
    """Raised when a compressed payload cannot be decompressed cleanly."""


class ProtocolError(MootdxNextError):
    """Base exception for protocol encoding and decoding failures."""


class ProtocolDecodeError(ProtocolError):
    """Raised when a response cannot be decoded into a result object."""


class SchedulerError(MootdxNextError):
    """Base exception for scheduling and routing failures."""


class PoolExhaustedError(SchedulerError):
    """Raised when a connection pool cannot lease more transports for a server."""


class NoHealthyServerError(SchedulerError):
    """Raised when no server can be selected for a request."""


class UnsupportedMarketError(SchedulerError):
    """Raised when a market or endpoint is unsupported by the runtime."""


class InvalidSymbolError(MootdxNextError):
    """Raised when a symbol or symbol collection cannot be normalized."""


class InvalidFrequencyError(MootdxNextError):
    """Raised when a frequency cannot be normalized to a supported K-line type."""


class InvalidDateError(MootdxNextError):
    """Raised when a date cannot be normalized to YYYYMMDD."""


class OutsideTradingSessionError(MootdxNextError):
    """Raised when a real-time API is called outside the supported trading session."""


class UnknownF10CategoryError(MootdxNextError):
    """Raised when an F10 content lookup cannot find the requested category."""


class AdjustmentError(MootdxNextError):
    """Raised when price adjustment data cannot be calculated safely."""
