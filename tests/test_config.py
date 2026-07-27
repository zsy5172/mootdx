from __future__ import annotations

import json
from importlib import import_module

import pytest

from mootdx import config
from mootdx.consts import CONFIG


@pytest.fixture(autouse=True)
def restore_config_state():
    original_settings = config.clone()
    original_conf = config.CONF
    original_loaded = config._loaded
    config.settings.clear()
    config.settings.update(json.loads(json.dumps(CONFIG)))
    config._loaded = False
    try:
        yield
    finally:
        config.settings.clear()
        config.settings.update(original_settings)
        config.CONF = original_conf
        config._loaded = original_loaded


def test_setup_creates_local_defaults_without_network(tmp_path, monkeypatch) -> None:
    config_file = tmp_path / 'config.json'
    config.CONF = config_file
    config._loaded = False
    server_module = import_module('mootdx.server')
    monkeypatch.setattr(
        server_module,
        'bestip',
        lambda *args, **kwargs: pytest.fail('config setup must not probe the network'),
    )

    assert config.setup()

    assert json.loads(config_file.read_text(encoding='utf-8')) == json.loads(json.dumps(CONFIG))
    assert config.clone() == CONFIG


def test_setup_keeps_invalid_file_and_uses_memory_defaults(tmp_path) -> None:
    config_file = tmp_path / 'config.json'
    config_file.write_text('{invalid', encoding='utf-8')
    config.CONF = config_file
    config._loaded = False

    assert config.setup()

    assert config_file.read_text(encoding='utf-8') == '{invalid'
    assert config.clone() == CONFIG


def test_setup_deep_merges_partial_config_and_preserves_falsey_values(tmp_path) -> None:
    config_file = tmp_path / 'config.json'
    config_file.write_text(
        json.dumps({'BESTIP': {'HQ': ['127.0.0.1', 7709]}, 'TDXDIR': ''}),
        encoding='utf-8',
    )
    config.CONF = config_file
    config._loaded = False

    config.setup()

    assert config.get('BESTIP.HQ') == ['127.0.0.1', 7709]
    assert config.get('BESTIP.EX') is None
    assert config.get('TDXDIR', 'fallback') == ''
    assert config.get('UNKNOWN', 'fallback') == 'fallback'


def test_dotted_set_and_update_preserve_sibling_settings() -> None:
    config.set('BESTIP.HQ', ('127.0.0.1', 7709))
    config.update({'BESTIP': {'EX': ('127.0.0.2', 7720)}})

    assert config.get('BESTIP.HQ') == ('127.0.0.1', 7709)
    assert config.get('BESTIP.EX') == ('127.0.0.2', 7720)
    assert config.get('BESTIP.GP') is None

    snapshot = config.clone()
    snapshot['BESTIP']['HQ'] = None
    assert config.get('BESTIP.HQ') == ('127.0.0.1', 7709)


def test_setup_is_idempotent_until_forced(tmp_path) -> None:
    config_file = tmp_path / 'config.json'
    config_file.write_text(json.dumps({'TDXDIR': 'first'}), encoding='utf-8')
    config.CONF = config_file
    config._loaded = False

    config.setup()
    config_file.write_text(json.dumps({'TDXDIR': 'second'}), encoding='utf-8')

    config.setup()
    assert config.get('TDXDIR') == 'first'

    config.setup(force=True)
    assert config.get('TDXDIR') == 'second'
