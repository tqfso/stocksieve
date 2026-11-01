"""本文件向渠道传递操作标识和进度回调，不暴露数据库任务对象。"""

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Callable, Iterator

_progress: ContextVar[Callable[[str, int, bool], None] | None] = ContextVar('source_progress', default=None)


_operation: ContextVar[str | None] = ContextVar('source_operation', default=None)


@contextmanager
def progress_sink(callback: Callable[[str, int, bool], None], *, operation_id: str | None = None) -> Iterator[None]:
    """跨异步任务及取数线程传递进度回调，不把数据库任务对象放入渠道接口。"""
    token = _progress.set(callback)
    operation = _operation.set(operation_id)
    try:
        yield
    finally:
        _progress.reset(token)
        _operation.reset(operation)


def current_operation_id() -> str | None:
    """提供不透明操作标识供分页缓存恢复，不向渠道暴露数据库任务对象。"""
    return _operation.get()


def report_progress(message: str, progress: int, advanced: bool = False) -> None:
    callback = _progress.get()
    if callback:
        callback(message, progress, advanced)
