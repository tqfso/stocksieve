"""本文件约束领域、应用、适配器的依赖方向，防止分包后重新混入HTTP或外部服务实现。"""

import ast
import importlib
import pkgutil
from pathlib import Path

import stocksieve

PACKAGE = Path(stocksieve.__file__).parent


def imported_modules(path: Path) -> set[str]:
    """静态读取导入关系，不执行业务或外部服务。"""
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
        elif isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
    return names


def test_domain_rules_do_not_import_application_storage_or_external_frameworks() -> None:
    forbidden = ('aiohttp', 'httpx', 'sqlalchemy', 'pydantic', 'deepagents', 'langchain', 'stocksieve.interfaces', 'stocksieve.infrastructure', 'stocksieve.bootstrap')
    for path in PACKAGE.glob('modules/*/domain/*.py'):
        for module in imported_modules(path):
            assert not module.startswith(forbidden), (str(path), module)
            assert '.application' not in module and '.infrastructure' not in module, (str(path), module)
            if module.startswith('stocksieve.shared.'):
                assert module in {'stocksieve.shared.errors', 'stocksieve.shared.market_types', 'stocksieve.shared.time'}, (str(path), module)


def test_application_uses_ports_and_shared_storage_not_http_or_provider_implementations() -> None:
    for path in PACKAGE.glob('modules/*/application/*.py'):
        for module in imported_modules(path):
            assert not module.startswith(('stocksieve.interfaces', 'stocksieve.bootstrap', 'aiohttp', 'httpx', 'baostock', 'deepagents', 'langchain')), (str(path), module)
            assert not (module.startswith('stocksieve.modules.') and '.infrastructure.' in module), (str(path), module)
    for path in (PACKAGE / 'shared').glob('*.py'):
        assert not any(module.startswith(('stocksieve.modules', 'stocksieve.interfaces', 'stocksieve.bootstrap')) for module in imported_modules(path)), str(path)


def test_package_imports_resource_paths_and_stable_cli_entry() -> None:
    from stocksieve.cli import main
    from stocksieve.shared.paths import BACKEND_ROOT
    assert callable(main)
    assert BACKEND_ROOT == Path(__file__).resolve().parents[1]
    assert (BACKEND_ROOT / 'sql/init.sql').is_file()
    assert (BACKEND_ROOT / 'skills/stock-analysis/SKILL.md').is_file()
    assert {path.name for path in PACKAGE.glob('*.py')} == {'__init__.py', 'cli.py'}
    for module in pkgutil.walk_packages(stocksieve.__path__, stocksieve.__name__ + '.'):
        importlib.import_module(module.name)
