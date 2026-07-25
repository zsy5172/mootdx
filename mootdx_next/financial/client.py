from __future__ import annotations

import asyncio
import hashlib
import os
import re
import tempfile
from collections.abc import Callable
from dataclasses import asdict
from dataclasses import dataclass
from pathlib import Path

import httpx

from mootdx_next.api.report_files import ReportFileClient
from mootdx_next.constants import FINANCIAL_BASE_URL
from mootdx_next.constants import FINANCIAL_CATALOG_FILE
from mootdx_next.errors import FinancialCatalogError
from mootdx_next.errors import FinancialDownloadError
from mootdx_next.errors import FinancialIntegrityError
from mootdx_next.financial.parser import FinancialReader
from mootdx_next.models import ServerEndpoint

CATALOG_LINE = re.compile(r"^(?P<filename>gpcw\d{8}\.zip),(?P<md5>[0-9a-fA-F]{32}),(?P<size>\d+)$")
ProgressHook = Callable[[int, int], object]
ReportClientFactory = Callable[[ServerEndpoint], ReportFileClient]


@dataclass(frozen=True, slots=True)
class FinancialFile:
    filename: str
    hash: str
    filesize: int

    def as_dict(self) -> dict[str, str | int]:
        return asdict(self)


def parse_catalog(content: bytes | str) -> tuple[FinancialFile, ...]:
    try:
        text = content.decode("utf-8-sig") if isinstance(content, bytes) else str(content)
    except UnicodeDecodeError as exc:
        raise FinancialCatalogError("financial catalog is not UTF-8") from exc

    files: list[FinancialFile] = []
    seen: set[str] = set()
    for number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        matched = CATALOG_LINE.fullmatch(line)
        if matched is None:
            raise FinancialCatalogError(f"invalid financial catalog line {number}")
        filename = matched.group("filename")
        if filename in seen:
            raise FinancialCatalogError(f"duplicate financial catalog filename: {filename}")
        seen.add(filename)
        files.append(
            FinancialFile(
                filename=filename,
                hash=matched.group("md5").lower(),
                filesize=int(matched.group("size")),
            )
        )
    if not files:
        raise FinancialCatalogError("financial catalog is empty")
    return tuple(files)


class FinancialFileClient:
    def __init__(
        self,
        *,
        base_url: str = FINANCIAL_BASE_URL,
        timeout: float = 15.0,
        http_client: httpx.Client | None = None,
        report_servers: list[ServerEndpoint] | tuple[ServerEndpoint, ...] = (),
        report_client_factory: ReportClientFactory = ReportFileClient,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = float(timeout)
        self._owns_http_client = http_client is None
        self._http = http_client or httpx.Client(timeout=self.timeout, follow_redirects=True)
        self._report_servers = tuple(report_servers)
        self._report_client_factory = report_client_factory
        self._catalog: tuple[FinancialFile, ...] | None = None

    def close(self) -> None:
        if self._owns_http_client:
            self._http.close()

    def __enter__(self) -> "FinancialFileClient":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()

    def catalog(self, refresh: bool = False) -> tuple[FinancialFile, ...]:
        if self._catalog is not None and not refresh:
            return self._catalog
        content = self._fetch_catalog_bytes()
        self._catalog = parse_catalog(content)
        return self._catalog

    def files(self, refresh: bool = False) -> list[dict[str, str | int]]:
        return [item.as_dict() for item in self.catalog(refresh=refresh)]

    def fetch(
        self,
        downdir: str | Path = ".",
        filename: str | None = None,
        *,
        overwrite: bool = False,
        report_hook: ProgressHook | None = None,
    ) -> Path | list[Path]:
        catalog = self.catalog()
        if filename is None:
            return [
                self._fetch_entry(item, Path(downdir), overwrite=overwrite, report_hook=report_hook)
                for item in catalog
            ]
        entry = self._entry(filename, catalog)
        return self._fetch_entry(entry, Path(downdir), overwrite=overwrite, report_hook=report_hook)

    def parse(
        self,
        downdir: str | Path = ".",
        filename: str | None = None,
        *,
        header: str = "zh",
    ):
        if not filename:
            raise FinancialCatalogError("filename cannot be blank")
        path = Path(downdir) / self._safe_filename(filename)
        if not path.is_file():
            path = self.fetch(downdir=downdir, filename=filename)
        return FinancialReader.read(path, header=header)

    def fetch_and_parse(
        self,
        filename: str,
        *,
        downdir: str | Path = ".",
        header: str = "zh",
        overwrite: bool = False,
    ):
        path = self.fetch(downdir=downdir, filename=filename, overwrite=overwrite)
        return FinancialReader.read(path, header=header)

    def _fetch_catalog_bytes(self) -> bytes:
        url = f"{self.base_url}/{FINANCIAL_CATALOG_FILE}"
        try:
            response = self._http.get(url)
            response.raise_for_status()
            return response.content
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code < 500:
                raise FinancialDownloadError(f"financial catalog HTTP {exc.response.status_code}") from exc
            http_error: Exception = exc
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            http_error = exc

        errors = [f"https: {type(http_error).__name__}: {http_error}"]
        for endpoint in self._report_servers:
            client = self._report_client_factory(endpoint)
            try:
                return bytes(client.fetch_file(f"tdxfin/{FINANCIAL_CATALOG_FILE}"))
            except Exception as exc:
                errors.append(f"{endpoint.host}:{endpoint.port}: {type(exc).__name__}: {exc}")
            finally:
                client.close()
        raise FinancialDownloadError("all financial catalog sources failed: " + "; ".join(errors))

    def _fetch_entry(
        self,
        entry: FinancialFile,
        directory: Path,
        *,
        overwrite: bool,
        report_hook: ProgressHook | None,
    ) -> Path:
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / entry.filename
        if target.is_file() and not overwrite:
            self._verify_path(target, entry)
            return target

        try:
            return self._fetch_entry_https(entry, target, report_hook)
        except FinancialIntegrityError:
            raise
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code < 500:
                raise FinancialDownloadError(f"financial file HTTP {exc.response.status_code}: {entry.filename}") from exc
            primary_error: Exception = exc
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            primary_error = exc

        errors = [f"https: {type(primary_error).__name__}: {primary_error}"]
        for endpoint in self._report_servers:
            client = self._report_client_factory(endpoint)
            try:
                content = bytes(
                    client.fetch_file(
                        f"tdxfin/{entry.filename}",
                        filesize=entry.filesize,
                        reporthook=report_hook,
                    )
                )
                self._verify_bytes(content, entry)
                self._atomic_write(target, content)
                return target
            except Exception as exc:
                errors.append(f"{endpoint.host}:{endpoint.port}: {type(exc).__name__}: {exc}")
            finally:
                client.close()
        raise FinancialDownloadError(f"all sources failed for {entry.filename}: " + "; ".join(errors))

    def _fetch_entry_https(
        self,
        entry: FinancialFile,
        target: Path,
        report_hook: ProgressHook | None,
    ) -> Path:
        url = f"{self.base_url}/{entry.filename}"
        digest = hashlib.md5()
        downloaded = 0
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                prefix=f".{entry.filename}.",
                suffix=".tmp",
                dir=target.parent,
                delete=False,
            ) as output:
                temporary = Path(output.name)
                with self._http.stream("GET", url) as response:
                    response.raise_for_status()
                    for chunk in response.iter_bytes():
                        if not chunk:
                            continue
                        output.write(chunk)
                        digest.update(chunk)
                        downloaded += len(chunk)
                        if report_hook is not None:
                            report_hook(downloaded, entry.filesize)

            self._verify(downloaded, digest.hexdigest(), entry)
            os.replace(temporary, target)
            return target
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()

    @staticmethod
    def _safe_filename(filename: str) -> str:
        normalized = str(filename).strip()
        if Path(normalized).name != normalized or not CATALOG_LINE.match(
            f"{normalized},{'0' * 32},0"
        ):
            raise FinancialCatalogError(f"invalid financial filename: {filename}")
        return normalized

    def _entry(self, filename: str, catalog: tuple[FinancialFile, ...]) -> FinancialFile:
        normalized = self._safe_filename(filename)
        try:
            return next(item for item in catalog if item.filename == normalized)
        except StopIteration as exc:
            raise FinancialCatalogError(f"financial file is not present in catalog: {normalized}") from exc

    @staticmethod
    def _verify(size: int, digest: str, entry: FinancialFile) -> None:
        if size != entry.filesize:
            raise FinancialIntegrityError(
                f"size mismatch for {entry.filename}: expected {entry.filesize}, got {size}"
            )
        if digest.lower() != entry.hash:
            raise FinancialIntegrityError(
                f"MD5 mismatch for {entry.filename}: expected {entry.hash}, got {digest.lower()}"
            )

    @classmethod
    def _verify_bytes(cls, content: bytes, entry: FinancialFile) -> None:
        cls._verify(len(content), hashlib.md5(content).hexdigest(), entry)

    @classmethod
    def _verify_path(cls, path: Path, entry: FinancialFile) -> None:
        digest = hashlib.md5()
        size = 0
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
                size += len(chunk)
        cls._verify(size, digest.hexdigest(), entry)

    @staticmethod
    def _atomic_write(target: Path, content: bytes) -> None:
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                prefix=f".{target.name}.",
                suffix=".tmp",
                dir=target.parent,
                delete=False,
            ) as output:
                temporary = Path(output.name)
                output.write(content)
            os.replace(temporary, target)
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()


class AsyncFinancialFileClient:
    def __init__(
        self,
        *,
        base_url: str = FINANCIAL_BASE_URL,
        timeout: float = 15.0,
        http_client: httpx.AsyncClient | None = None,
        report_servers: list[ServerEndpoint] | tuple[ServerEndpoint, ...] = (),
        report_client_factory: ReportClientFactory = ReportFileClient,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = float(timeout)
        self._owns_http_client = http_client is None
        self._http = http_client or httpx.AsyncClient(timeout=self.timeout, follow_redirects=True)
        self._report_servers = tuple(report_servers)
        self._report_client_factory = report_client_factory
        self._catalog: tuple[FinancialFile, ...] | None = None

    async def close(self) -> None:
        if self._owns_http_client:
            await self._http.aclose()

    async def __aenter__(self) -> "AsyncFinancialFileClient":
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        await self.close()

    async def catalog(self, refresh: bool = False) -> tuple[FinancialFile, ...]:
        if self._catalog is not None and not refresh:
            return self._catalog
        content = await self._fetch_catalog_bytes()
        self._catalog = parse_catalog(content)
        return self._catalog

    async def files(self, refresh: bool = False) -> list[dict[str, str | int]]:
        return [item.as_dict() for item in await self.catalog(refresh=refresh)]

    async def fetch(
        self,
        downdir: str | Path = ".",
        filename: str | None = None,
        *,
        overwrite: bool = False,
        report_hook: ProgressHook | None = None,
    ) -> Path | list[Path]:
        catalog = await self.catalog()
        if filename is None:
            results = []
            for item in catalog:
                results.append(
                    await self._fetch_entry(item, Path(downdir), overwrite=overwrite, report_hook=report_hook)
                )
            return results
        normalized = FinancialFileClient._safe_filename(filename)
        try:
            entry = next(item for item in catalog if item.filename == normalized)
        except StopIteration as exc:
            raise FinancialCatalogError(f"financial file is not present in catalog: {normalized}") from exc
        return await self._fetch_entry(entry, Path(downdir), overwrite=overwrite, report_hook=report_hook)

    async def parse(
        self,
        downdir: str | Path = ".",
        filename: str | None = None,
        *,
        header: str = "zh",
    ):
        if not filename:
            raise FinancialCatalogError("filename cannot be blank")
        safe = FinancialFileClient._safe_filename(filename)
        path = Path(downdir) / safe
        if not path.is_file():
            path = await self.fetch(downdir=downdir, filename=filename)
        return await asyncio.to_thread(FinancialReader.read, path, header)

    async def fetch_and_parse(
        self,
        filename: str,
        *,
        downdir: str | Path = ".",
        header: str = "zh",
        overwrite: bool = False,
    ):
        path = await self.fetch(downdir=downdir, filename=filename, overwrite=overwrite)
        return await asyncio.to_thread(FinancialReader.read, path, header)

    async def _fetch_catalog_bytes(self) -> bytes:
        url = f"{self.base_url}/{FINANCIAL_CATALOG_FILE}"
        try:
            response = await self._http.get(url)
            response.raise_for_status()
            return response.content
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code < 500:
                raise FinancialDownloadError(f"financial catalog HTTP {exc.response.status_code}") from exc
            http_error: Exception = exc
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            http_error = exc
        return await self._report_fallback(
            FINANCIAL_CATALOG_FILE,
            filesize=0,
            primary_error=http_error,
            report_hook=None,
        )

    async def _fetch_entry(
        self,
        entry: FinancialFile,
        directory: Path,
        *,
        overwrite: bool,
        report_hook: ProgressHook | None,
    ) -> Path:
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / entry.filename
        if target.is_file() and not overwrite:
            await asyncio.to_thread(FinancialFileClient._verify_path, target, entry)
            return target

        try:
            response = await self._http.get(f"{self.base_url}/{entry.filename}")
            response.raise_for_status()
            content = response.content
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code < 500:
                raise FinancialDownloadError(f"financial file HTTP {exc.response.status_code}: {entry.filename}") from exc
            content = await self._report_fallback(
                entry.filename,
                filesize=entry.filesize,
                primary_error=exc,
                report_hook=report_hook,
            )
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            content = await self._report_fallback(
                entry.filename,
                filesize=entry.filesize,
                primary_error=exc,
                report_hook=report_hook,
            )

        FinancialFileClient._verify_bytes(content, entry)
        await asyncio.to_thread(FinancialFileClient._atomic_write, target, content)
        if report_hook is not None:
            report_hook(len(content), entry.filesize)
        return target

    async def _report_fallback(
        self,
        filename: str,
        *,
        filesize: int,
        primary_error: Exception,
        report_hook: ProgressHook | None,
    ) -> bytes:
        errors = [f"https: {type(primary_error).__name__}: {primary_error}"]
        for endpoint in self._report_servers:
            try:
                return await asyncio.to_thread(
                    self._fetch_report_file,
                    endpoint,
                    filename,
                    filesize,
                    report_hook,
                )
            except Exception as exc:
                errors.append(f"{endpoint.host}:{endpoint.port}: {type(exc).__name__}: {exc}")
        raise FinancialDownloadError(f"all sources failed for {filename}: " + "; ".join(errors))

    def _fetch_report_file(
        self,
        endpoint: ServerEndpoint,
        filename: str,
        filesize: int,
        report_hook: ProgressHook | None,
    ) -> bytes:
        client = self._report_client_factory(endpoint)
        try:
            return bytes(
                client.fetch_file(
                    f"tdxfin/{filename}",
                    filesize=filesize,
                    reporthook=report_hook,
                )
            )
        finally:
            client.close()
