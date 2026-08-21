"""MAC 协议 TCP transport。"""

from __future__ import annotations

from mootdx_next.transport.constants import MAC_EX_LOGIN_PAYLOAD
from mootdx_next.transport.socket_transport import SyncSocketTransport


class MacSocketTransport(SyncSocketTransport):
    """A 股 MAC 连接：不发送标准 TDX setup。"""

    def __init__(self, *args, mac_ex: bool = False, **kwargs):
        # MAC sessions must not inherit the standard setup sequence.  Popping
        # also makes an injected factory safe when it forwards setup_payloads.
        kwargs.pop("setup_payloads", None)
        super().__init__(*args, setup_payloads=(), **kwargs)
        self.mac_ex = bool(mac_ex)

    def _perform_setup(self) -> None:
        if self.mac_ex:
            self._exchange_payload(MAC_EX_LOGIN_PAYLOAD, count_request=False)


class MacExSocketTransport(MacSocketTransport):
    def __init__(self, *args, **kwargs):
        kwargs["mac_ex"] = True
        super().__init__(*args, **kwargs)


__all__ = ["MacExSocketTransport", "MacSocketTransport"]
