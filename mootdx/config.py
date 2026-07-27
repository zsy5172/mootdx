from __future__ import annotations

import copy
import json
import threading
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from mootdx.consts import CONFIG
from mootdx.logger import logger
from mootdx.utils import get_config_path

__all__ = ['clone', 'get', 'has', 'path', 'set', 'settings', 'setup', 'update']

BASE = Path(__file__).resolve().parent.parent
CONF = Path(get_config_path('config.json'))

settings: dict[str, Any] = copy.deepcopy(CONFIG)
_loaded = False
_lock = threading.RLock()


def _merge(target: dict[str, Any], source: Mapping[str, Any]) -> None:
    for key, value in source.items():
        if isinstance(value, Mapping) and isinstance(target.get(key), dict):
            _merge(target[key], value)
        else:
            target[key] = copy.deepcopy(value)


def _replace_settings(options: Mapping[str, Any] | None = None) -> None:
    settings.clear()
    settings.update(copy.deepcopy(CONFIG))
    if options is not None:
        _merge(settings, options)


def _write_default(config_file: Path) -> None:
    config_file.parent.mkdir(parents=True, exist_ok=True)
    temporary = config_file.with_suffix(f'{config_file.suffix}.tmp')
    temporary.write_text(
        json.dumps(CONFIG, indent=2, ensure_ascii=False) + '\n',
        encoding='utf-8',
    )
    temporary.replace(config_file)


def setup(*, force: bool = False) -> bool:
    """Load local configuration without performing implicit network discovery."""

    global _loaded
    with _lock:
        if _loaded and not force:
            return True

        config_file = Path(CONF)
        options: Mapping[str, Any] | None = None
        try:
            decoded = json.loads(config_file.read_text(encoding='utf-8'))
            if not isinstance(decoded, Mapping):
                raise ValueError('配置根节点必须是 JSON 对象')
            options = decoded
        except FileNotFoundError:
            logger.info('初始化配置文件: %s', config_file)
            _write_default(config_file)
        except (json.JSONDecodeError, OSError, ValueError) as exc:
            logger.warning('忽略无效配置文件 %s: %s', config_file, exc)

        _replace_settings(options)
        _loaded = True
        return True


def has(key: str, value: object) -> bool:
    container = get(key)
    try:
        return value in container
    except TypeError:
        return False


def set(key: str, value: Any) -> None:  # noqa: A001
    with _lock:
        parts = key.split('.')
        target = settings
        for part in parts[:-1]:
            current = target.get(part)
            if not isinstance(current, dict):
                current = {}
                target[part] = current
            target = current

        leaf = parts[-1]
        if isinstance(value, Mapping) and isinstance(target.get(leaf), dict):
            _merge(target[leaf], value)
        else:
            target[leaf] = copy.deepcopy(value)


def get(key: str, default: Any = None) -> Any:
    current: Any = settings
    for part in key.split('.'):
        if not isinstance(current, Mapping) or part not in current:
            return default
        current = current[part]
    return current


def path(key: str, value: str | Path | None = None) -> Path:
    configured = Path(get(key, '') or '')
    result = configured if configured.is_absolute() else BASE / configured
    return result / value if value is not None else result


def clone() -> dict[str, Any]:
    with _lock:
        return copy.deepcopy(settings)


def update(options: Mapping[str, Any]) -> None:
    with _lock:
        _merge(settings, options)
