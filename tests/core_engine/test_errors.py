from mootdx_next.errors import EmptyResponseError
from mootdx_next.errors import InvalidResponseHeaderError
from mootdx_next.errors import MootdxNextError
from mootdx_next.errors import NoHealthyServerError
from mootdx_next.errors import OutsideTradingSessionError
from mootdx_next.errors import PayloadDecompressionError
from mootdx_next.errors import PoolExhaustedError
from mootdx_next.errors import ProtocolDecodeError
from mootdx_next.errors import ProtocolError
from mootdx_next.errors import SchedulerError
from mootdx_next.errors import TransportConnectionError
from mootdx_next.errors import TransportError
from mootdx_next.errors import TransportTimeoutError
from mootdx_next.errors import UnknownF10CategoryError
from mootdx_next.errors import UnsupportedMarketError


def test_transport_error_hierarchy() -> None:
    assert issubclass(TransportError, MootdxNextError)
    assert issubclass(TransportConnectionError, TransportError)
    assert issubclass(TransportTimeoutError, TransportError)
    assert issubclass(EmptyResponseError, TransportError)
    assert issubclass(InvalidResponseHeaderError, TransportError)
    assert issubclass(PayloadDecompressionError, TransportError)


def test_protocol_error_hierarchy() -> None:
    assert issubclass(ProtocolError, MootdxNextError)
    assert issubclass(ProtocolDecodeError, ProtocolError)


def test_scheduler_error_hierarchy() -> None:
    assert issubclass(SchedulerError, MootdxNextError)
    assert issubclass(PoolExhaustedError, SchedulerError)
    assert issubclass(NoHealthyServerError, SchedulerError)
    assert issubclass(UnsupportedMarketError, SchedulerError)


def test_trading_session_error_hierarchy() -> None:
    assert issubclass(OutsideTradingSessionError, MootdxNextError)


def test_unknown_f10_category_error_hierarchy() -> None:
    assert issubclass(UnknownF10CategoryError, MootdxNextError)
