from __future__ import annotations

import asyncio
from pathlib import Path

from mootdx.tools.tdx2csv import batch
from mootdx.tools.tdx2csv import convert
from mootdx.tools.tdx2csv import covert
from mootdx.tools.tdx2csv import txt2csv

FIXTURES = Path('tests/fixtures/export')


def test_txt2csv_writes_requested_output(tmp_path) -> None:
    output = tmp_path / 'nested' / 'converted.csv'

    result = txt2csv(FIXTURES / 'SH#601003.txt', output)

    assert not result.empty
    assert output.is_file()


def test_txt2csv_returns_empty_for_missing_or_invalid_input(tmp_path) -> None:
    invalid = tmp_path / 'invalid.txt'
    invalid.write_bytes(b'\xff')

    assert txt2csv(tmp_path / 'missing.txt').empty
    assert txt2csv(invalid, tmp_path / 'invalid.csv').empty


def test_async_convert_and_compatibility_alias(tmp_path) -> None:
    async def run() -> None:
        converted = await convert(FIXTURES / 'SH#601005.txt', tmp_path / 'converted.csv')
        compatibility = await covert(FIXTURES / 'SH#601006.txt', tmp_path / 'compatibility.csv')

        assert not converted.empty
        assert not compatibility.empty

    asyncio.run(run())

    assert (tmp_path / 'converted.csv').is_file()
    assert (tmp_path / 'compatibility.csv').is_file()


def test_batch_uses_destination_directory(tmp_path) -> None:
    destination = tmp_path / 'output'

    results = batch(FIXTURES, destination)

    source_names = sorted(path.with_suffix('.csv').name for path in FIXTURES.glob('*.txt'))
    output_names = sorted(path.name for path in destination.glob('*.csv'))
    assert len(results) == len(source_names)
    assert output_names == source_names


def test_batch_returns_empty_for_directory_without_exports(tmp_path) -> None:
    assert batch(tmp_path, tmp_path / 'output') == []
