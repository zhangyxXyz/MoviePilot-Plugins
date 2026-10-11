"""Sorting preserves task identity and rejects stale/partial order payloads."""
import importlib.util
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location('brushflow_order_models', ROOT / 'plugins.v3/brushflow/models.py')
models = importlib.util.module_from_spec(spec)
spec.loader.exec_module(models)


def test_reorder_preserves_configuration_objects_and_edit_position():
    tasks = {'a': object(), 'b': object(), 'c': object()}
    ordered = models.reorder_tasks(tasks, ['c', 'a', 'b'])
    assert list(ordered) == ['c', 'a', 'b']
    assert all(ordered[k] is tasks[k] for k in tasks)
    ordered['a'] = object()
    assert list(ordered) == ['c', 'a', 'b']
    assert list(tasks) == ['a', 'b', 'c']


@pytest.mark.parametrize('ids', [['a'], ['a','a'], ['a','x'], ['a','b','c']])
def test_stale_missing_duplicate_and_unknown_ids_rejected(ids):
    with pytest.raises(ValueError):
        models.reorder_tasks({'a': object(), 'b': object()}, ids)


def test_hr_bridge_always_selects_guarded_delete_path():
    import ast
    from types import SimpleNamespace
    module=ast.parse((ROOT/'plugins.v3/brushflow/__init__.py').read_text())
    cls=next(n for n in module.body if isinstance(n,ast.ClassDef) and n.name=='BrushFlow')
    method=next(n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name=='_guarded_delete_enabled')
    ns={}
    exec(compile(ast.Module(body=[method],type_ignores=[]),'<guard selector>','exec'),ns)
    plugin=SimpleNamespace(_global_hr_guard_enabled=True)
    assert ns['_guarded_delete_enabled'](plugin) is True
