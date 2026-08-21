class MootdxNextError(Exception):
    """Base exception for the next-generation core."""


class ClientClosedError(MootdxNextError):
    """Raised when a request is attempted after a client was closed."""


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


class ProtocolEncodeError(ProtocolDecodeError):
    """Raised when a request cannot be encoded.

    This remains a subclass of ``ProtocolDecodeError`` for compatibility with
    callers that historically caught that broad protocol exception for both
    request and response failures.
    """


class ValidationError(MootdxNextError):
    """Base exception for invalid caller-supplied domain values."""


class SchedulerError(MootdxNextError):
    """Base exception for scheduling and routing failures."""


class PoolExhaustedError(SchedulerError):
    """Raised when a connection pool cannot lease more transports for a server."""


class NoHealthyServerError(SchedulerError):
    """Raised when no server can be selected for a request."""


class UnsupportedMarketError(ValidationError, SchedulerError):
    """Raised when a market or endpoint is unsupported by the runtime."""


class InvalidSymbolError(ValidationError):
    """Raised when a symbol or symbol collection cannot be normalized."""


class InvalidFrequencyError(ValidationError):
    """Raised when a frequency cannot be normalized to a supported K-line type."""


class InvalidDateError(ValidationError):
    """Raised when a date cannot be normalized to YYYYMMDD."""


class OutsideTradingSessionError(MootdxNextError):
    """Legacy public error retained for compatibility with external clients."""


class UnknownF10CategoryError(MootdxNextError):
    """Raised when an F10 content lookup cannot find the requested category."""


class AdjustmentError(MootdxNextError):
    """Raised when price adjustment data cannot be calculated safely."""


class ConfigFileError(MootdxNextError):
    """Raised when a TDX report/configuration file is missing or malformed."""


class ConfigArchiveError(ConfigFileError):
    """Raised when a TDX configuration archive is invalid or unsafe."""


class GbbqError(MootdxNextError):
    """Base exception for official TDX GBBQ snapshot operations."""


class GbbqDownloadError(GbbqError):
    """Raised when the official GBBQ archive cannot be downloaded safely."""


class GbbqArchiveError(GbbqError):
    """Raised when the official GBBQ ZIP archive is invalid or unsafe."""


class GbbqDecodeError(GbbqError):
    """Raised when encrypted GBBQ records cannot be decoded safely."""


class EtfPcfError(MootdxNextError):
    """Base exception for the official TDX ETF PCF summary endpoint."""


class EtfPcfDownloadError(EtfPcfError):
    """Raised when the ETF PCF response cannot be downloaded."""


class EtfPcfDecodeError(EtfPcfError):
    """Raised when the ETF PCF response has an unexpected schema."""


class BseError(MootdxNextError):
    """Base exception for the Beijing Stock Exchange directory provider."""


class BseResponseError(BseError):
    """Raised when the BSE directory cannot be downloaded or validated."""


class FinancialError(MootdxNextError):
    """Base exception for TDX financial-file operations."""


class FinancialCatalogError(FinancialError):
    """Raised when the financial catalog is missing or malformed."""


class FinancialDownloadError(FinancialError):
    """Raised when every permitted financial download source fails."""


class FinancialIntegrityError(FinancialError):
    """Raised when a downloaded financial file fails size or MD5 validation."""


class FinancialFileFormatError(FinancialError):
    """Raised when a TDX financial DAT or ZIP payload is malformed."""


class UnsafeArchiveError(FinancialFileFormatError):
    """Raised when a financial ZIP contains an unsafe member path."""
