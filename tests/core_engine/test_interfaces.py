import inspect

import pytest

from mootdx_next.interfaces import AbstractProtocol
from mootdx_next.interfaces import AbstractScheduler
from mootdx_next.interfaces import AbstractTransport


def test_abstract_transport_cannot_be_instantiated() -> None:
    with pytest.raises(TypeError):
        AbstractTransport()


def test_abstract_transport_method_signatures() -> None:
    connect_signature = inspect.signature(AbstractTransport.connect)
    assert list(connect_signature.parameters) == ["self", "server", "timeout_ms"]

    close_signature = inspect.signature(AbstractTransport.close)
    assert list(close_signature.parameters) == ["self"]

    is_connected_signature = inspect.signature(AbstractTransport.is_connected)
    assert list(is_connected_signature.parameters) == ["self"]

    send_signature = inspect.signature(AbstractTransport.send)
    assert list(send_signature.parameters) == ["self", "context", "payload", "server"]


def test_abstract_protocol_cannot_be_instantiated() -> None:
    with pytest.raises(TypeError):
        AbstractProtocol()


def test_abstract_scheduler_cannot_be_instantiated() -> None:
    with pytest.raises(TypeError):
        AbstractScheduler()
