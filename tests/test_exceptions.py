from mootdx._optional import missing_legacy_dependency
from mootdx.exceptions import MootdxException
from mootdx.exceptions import MootdxModuleNotFoundError
from mootdx.exceptions import MootdxValidationException


def test_public_exception_hierarchy_and_messages() -> None:
    validation = MootdxValidationException('证券代码错误')
    missing = MootdxModuleNotFoundError('缺少可选依赖')

    assert isinstance(validation, MootdxException)
    assert isinstance(validation, ValueError)
    assert str(validation) == '证券代码错误'
    assert repr(validation) == '<MootdxValidationException: 证券代码错误>'

    assert isinstance(missing, MootdxException)
    assert isinstance(missing, ModuleNotFoundError)
    assert str(missing) == '缺少可选依赖'


def test_base_exception_preserves_metadata() -> None:
    response = object()
    error = MootdxException('请求失败', provider='tdx', response=response, data={'retry': 1})

    assert str(error) == '请求失败'
    assert error.provider == 'tdx'
    assert error.response is response
    assert error.data == {'retry': 1}


def test_missing_legacy_dependency_keeps_actionable_message() -> None:
    error = missing_legacy_dependency('旧版行情')

    assert isinstance(error, MootdxModuleNotFoundError)
    assert str(error) == '旧版行情 依赖 tdxpy, 请先安装 `mootdx[legacy]`'
