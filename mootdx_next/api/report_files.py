from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from typing import Callable

from mootdx_next.models import RequestContext
from mootdx_next.models import ServerEndpoint
from mootdx_next.protocol.report_files import decode_report_file_chunk
from mootdx_next.protocol.report_files import encode_report_file
from mootdx_next.transport.socket_transport import SyncSocketTransport


@dataclass(slots=True)
class ReportFileClient:
    server: ServerEndpoint
    timeout_ms: int = 5000
    transport_factory: Callable[[], SyncSocketTransport] = SyncSocketTransport
    transport: SyncSocketTransport = field(init=False)

    def __post_init__(self) -> None:
        if self.transport_factory is SyncSocketTransport:
            self.transport = SyncSocketTransport(setup_payloads=())
        else:
            self.transport = self.transport_factory()

    def close(self) -> None:
        self.transport.close()

    def fetch_chunk(self, filename: str, offset: int = 0) -> dict[str, bytes | int]:
        context = RequestContext(api="report_file", params={"filename": filename, "offset": offset}, timeout_ms=self.timeout_ms)
        envelope = self.transport.send(context, encode_report_file(filename, offset), self.server)
        return decode_report_file_chunk(envelope.body or b"")

    def fetch_file(
        self,
        filename: str,
        filesize: int = 0,
        reporthook=None,
    ) -> bytearray:
        file_content = bytearray()
        get_zero_length_package_times = 0
        current_downloaded_size = 0

        while current_downloaded_size < filesize or filesize == 0:
            response = self.fetch_chunk(filename, current_downloaded_size)
            chunk_size = int(response["chunksize"])

            if chunk_size > 0:
                chunk_data = bytes(response["chunkdata"])
                current_downloaded_size += chunk_size
                file_content.extend(chunk_data)
                if reporthook is not None:
                    reporthook(current_downloaded_size, filesize)
            else:
                get_zero_length_package_times += 1
                if filesize == 0 or get_zero_length_package_times > 2:
                    break

        return file_content
