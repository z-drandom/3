"""watchdog 监听：docs 目录任何增删改都触发索引热重载，无需重启服务。"""
from __future__ import annotations

import threading
import time
from pathlib import Path

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from .config import settings
from .indexer import index
from .search import search_index

# 这些后缀/目录的变化不值得重建索引
_WATCH_SUFFIX = {".md", ".markdown"}
_IGNORE_PARTS = {".trash", ".git", "__pycache__"}


def rebuild_all() -> int:
    """重扫目录并重建检索倒排表，返回文档数。"""
    n = index.rebuild()
    search_index.build(index.all())
    search_index.version = index.version
    return n


class _Debouncer:
    """把短时间内的一串文件事件合并成一次重建（默认 0.6 秒静默期）。"""

    def __init__(self, delay: float = 0.6) -> None:
        self.delay = delay
        self._timer: threading.Timer | None = None
        self._lock = threading.Lock()

    def trigger(self) -> None:
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
            self._timer = threading.Timer(self.delay, self._run)
            self._timer.daemon = True
            self._timer.start()

    @staticmethod
    def _run() -> None:
        started = time.time()
        n = rebuild_all()
        print(f"[watcher] 检测到文件变化，索引已热重载：{n} 篇，"
              f"耗时 {time.time() - started:.2f}s", flush=True)


class _Handler(FileSystemEventHandler):
    def __init__(self, debouncer: _Debouncer) -> None:
        self.debouncer = debouncer

    def _relevant(self, path_str: str) -> bool:
        p = Path(path_str)
        if any(part in _IGNORE_PARTS for part in p.parts):
            return False
        if p.name.startswith("~$") or p.name.startswith("."):
            return False
        # 目录事件（新建/删除分类目录）也需要重建；文件只关心 Markdown
        return p.suffix.lower() in _WATCH_SUFFIX or p.suffix == ""

    def on_any_event(self, event: FileSystemEvent) -> None:
        if event.event_type in ("opened", "closed"):
            return
        src = getattr(event, "src_path", "") or ""
        dst = getattr(event, "dest_path", "") or ""
        if self._relevant(src) or (dst and self._relevant(dst)):
            self.debouncer.trigger()


def start_watcher() -> Observer:
    """启动文件监听线程，返回 Observer 以便退出时停止。"""
    settings.docs_root.mkdir(parents=True, exist_ok=True)
    observer = Observer()
    observer.schedule(_Handler(_Debouncer()), str(settings.docs_root), recursive=True)
    observer.daemon = True
    observer.start()
    print(f"[watcher] 已监听目录：{settings.docs_root}", flush=True)
    return observer
