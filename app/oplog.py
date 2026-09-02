"""回收站与操作日志：删除只“移动 + 记账”，不做物理删除。"""
from __future__ import annotations

import shutil
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

from .config import settings
from .safepath import norm_rel

_lock = threading.Lock()


def _now() -> str:
    """本地时间字符串，日志与回收站目录名共用。"""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def log(action: str, target: str, operator: str, note: str = "") -> None:
    """向 operation.log 追加一行 TSV：时间 / 操作 / 目标 / 操作人 / 备注。"""
    settings.ensure_dirs()
    line = "\t".join(
        x.replace("\t", " ").replace("\n", " ")
        for x in (_now(), action, target, operator or "unknown", note)
    )
    with _lock, settings.log_file.open("a", encoding="utf-8") as fp:
        fp.write(line + "\n")


def read_log(limit: int = 300) -> list[dict]:
    """读取最近若干条操作日志（倒序）。"""
    if not settings.log_file.exists():
        return []
    lines = settings.log_file.read_text(encoding="utf-8", errors="replace").splitlines()
    rows = []
    for raw in lines:
        if not raw.strip() or raw.startswith("#"):
            continue
        cols = raw.split("\t")
        cols += [""] * (5 - len(cols))
        rows.append({
            "time": cols[0], "action": cols[1], "target": cols[2],
            "operator": cols[3], "note": cols[4],
        })
    rows.reverse()
    return rows[:limit]


def move_to_trash(path: Path, operator: str) -> str:
    """把文件/目录移入 .trash/<时间戳-随机码>/<原相对路径>，返回回收站相对位置。"""
    settings.ensure_dirs()
    root = settings.docs_root
    rel = path.resolve().relative_to(root).as_posix()
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
    dest = settings.trash_dir / stamp / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(path), str(dest))
    return f"{stamp}/{rel}"


def restore_from_trash(entry_rel: str, operator: str) -> str:
    """从回收站还原：entry_rel 形如 20260902-101500-a1b2/危化品/x.md。"""
    entry_rel = norm_rel(entry_rel)
    src = (settings.trash_dir / entry_rel).resolve()
    if settings.trash_dir not in src.parents:
        raise ValueError("回收站路径非法")
    if not src.exists():
        raise FileNotFoundError("回收站中不存在该条目")
    # 去掉首段时间戳目录，即为原始相对路径
    parts = entry_rel.split("/", 1)
    if len(parts) != 2:
        raise ValueError("回收站条目格式不正确")
    dest = (settings.docs_root / parts[1]).resolve()
    if dest.exists():
        raise FileExistsError("原位置已存在同名文件，请先处理")
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dest))
    log("RESTORE", parts[1], operator, f"来源 .trash/{entry_rel}")
    return parts[1]


def list_trash(limit: int = 200) -> list[dict]:
    """列出回收站中的批次条目（按时间倒序）。"""
    if not settings.trash_dir.exists():
        return []
    out = []
    for batch in sorted(settings.trash_dir.iterdir(), reverse=True):
        if not batch.is_dir():
            continue  # 跳过 operation.log 等文件
        for item in batch.rglob("*"):
            if item.is_file():
                out.append({
                    "entry": f"{batch.name}/{item.relative_to(batch).as_posix()}",
                    "original": item.relative_to(batch).as_posix(),
                    "deleted_at": datetime.fromtimestamp(batch.stat().st_mtime)
                                          .strftime("%Y-%m-%d %H:%M:%S"),
                    "size": item.stat().st_size,
                })
        if len(out) >= limit:
            break
    return out[:limit]


def purge_expired() -> int:
    """清理超过保留期的回收站批次；retain_days=0 时不清理。返回清理批次数。"""
    days = settings.trash_retain_days
    if days <= 0 or not settings.trash_dir.exists():
        return 0
    deadline = time.time() - days * 86400
    removed = 0
    for batch in settings.trash_dir.iterdir():
        if not batch.is_dir():
            continue
        if batch.stat().st_mtime < deadline:
            shutil.rmtree(batch, ignore_errors=True)
            log("PURGE", f".trash/{batch.name}", "system", f"超过保留期 {days} 天")
            removed += 1
    return removed


def start_purge_thread() -> None:
    """后台线程：启动时清一次，之后每 12 小时清一次过期回收站。"""
    def loop() -> None:
        while True:
            try:
                purge_expired()
            except Exception as exc:  # 清理失败不影响主服务
                log("PURGE_ERROR", ".trash", "system", str(exc))
            time.sleep(12 * 3600)

    threading.Thread(target=loop, name="trash-purge", daemon=True).start()
