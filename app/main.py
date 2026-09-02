"""FastAPI 入口：只读接口对所有人开放，/api/admin/* 需要口令。"""
from __future__ import annotations

import mimetypes
import secrets
import shutil
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote, unquote

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import oplog
from .config import settings
from .indexer import (STATUS_ACTIVE, dump_frontmatter, index, render_markdown,
                      split_frontmatter)
from .safepath import UnsafePathError, safe_join
from .search import search as do_search
from .watcher import rebuild_all, start_watcher

STATIC_DIR = Path(__file__).resolve().parents[1] / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动：建目录 -> 首次全量扫描 -> 启动监听与回收站清理线程。"""
    settings.ensure_dirs()
    n = rebuild_all()
    print(f"[启动] 知识库根目录 {settings.docs_root}，已索引 {n} 篇文档", flush=True)
    if not settings.admin_token:
        print("[警告] 未设置 EHS_ADMIN_TOKEN，管理视图已禁用（站点为纯只读）", flush=True)
    observer = start_watcher()
    oplog.start_purge_thread()
    try:
        yield
    finally:
        observer.stop()
        observer.join(timeout=3)


app = FastAPI(title="EHS 知识库", version="1.0.0", lifespan=lifespan)


# ---------------- 权限 ----------------
def require_admin(x_admin_token: str = Header(default="")) -> str:
    """校验管理口令；返回管理员标识（用于操作日志）。"""
    if not settings.admin_token:
        raise HTTPException(status_code=403, detail="服务端未配置管理口令，管理视图不可用")
    if not x_admin_token or not secrets.compare_digest(x_admin_token, settings.admin_token):
        raise HTTPException(status_code=401, detail="口令错误")
    return "admin"


def operator_of(x_admin_user: str = Header(default="")) -> str:
    """操作人：管理视图里可自填姓名，便于日志追溯。

    HTTP 头只能承载 latin-1，中文姓名前端会做百分号编码；
    这里先解码，若仍是被 latin-1 误读的 UTF-8 字节再修复一次。
    """
    name = unquote(x_admin_user or "")
    if not name:
        return "admin"
    try:
        name = name.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        pass                      # 已经是正常的 Unicode 字符串
    return name.strip()[:40] or "admin"


@app.exception_handler(UnsafePathError)
async def _unsafe_path_handler(request, exc: UnsafePathError):
    return JSONResponse(status_code=400, content={"detail": str(exc)})


# ---------------- 只读接口 ----------------
@app.get("/api/stats")
def api_stats():
    """站点概览：文档数、分类数、到期数、索引版本。"""
    return index.stats()


@app.get("/api/tree")
def api_tree():
    """左侧目录树。"""
    return {"tree": index.tree(), "version": index.version}


@app.get("/api/tags")
def api_tags():
    """全部标签及计数。"""
    return {"tags": index.tags()}


@app.get("/api/docs")
def api_docs(category: str = "", tag: str = "", status: str = "", limit: int = 500):
    """按条件列出文档（不带关键词）。"""
    return {"results": do_search(index, "", tag=tag, category=category,
                                 status=status, limit=limit)}


@app.get("/api/search")
def api_search(q: str = "", category: str = "", tag: str = "",
               status: str = "", limit: int = 50):
    """全文搜索：返回带 <mark> 高亮的片段。"""
    results = do_search(index, q, tag=tag, category=category, status=status, limit=limit)
    return {"query": q, "count": len(results), "results": results}


@app.get("/api/reviews")
def api_reviews(days: int | None = None):
    """到期提醒面板数据：review_date 距今小于阈值的文档。"""
    return {"days": days or settings.review_warn_days, "results": index.due_reviews(days)}


@app.get("/api/doc")
def api_doc(path: str):
    """读取单篇文档：元数据 + 渲染后的 HTML + 附件清单。"""
    doc = index.get(path)
    if doc is None:
        raise HTTPException(status_code=404, detail="文档不存在（可能刚被删除）")
    data = doc.to_brief()
    data.update({
        "html": render_markdown(doc.body, doc.dir),
        "markdown": doc.body,
        "extra": doc.extra,
        "attachments": doc.attachments(),
        "size": doc.size,
    })
    return data


@app.get("/api/asset/{rel_path:path}")
def api_asset(rel_path: str, download: int = 0):
    """按相对路径提供附件/图片；路径经过越界校验。"""
    target = safe_join(settings.docs_root, rel_path)
    if not target.is_file():
        raise HTTPException(status_code=404, detail="附件不存在")
    media, _ = mimetypes.guess_type(target.name)
    disposition = "attachment" if download else "inline"
    # HTTP 头只能是 latin-1，中文文件名必须按 RFC 5987 做百分号编码
    encoded = quote(target.name, safe="")
    return FileResponse(
        target, media_type=media or "application/octet-stream",
        headers={"Content-Disposition": f"{disposition}; filename*=UTF-8''{encoded}"},
    )


# ---------------- 管理接口 ----------------
class LoginBody(BaseModel):
    password: str = ""


@app.post("/api/admin/login")
def api_login(body: LoginBody):
    """校验口令。前端把口令存在 sessionStorage，后续请求带在请求头里。"""
    if not settings.admin_token:
        raise HTTPException(status_code=403, detail="服务端未配置 EHS_ADMIN_TOKEN")
    if not secrets.compare_digest(body.password or "", settings.admin_token):
        raise HTTPException(status_code=401, detail="口令错误")
    return {"ok": True}


class SaveBody(BaseModel):
    """新建或保存一篇文档（frontmatter + 正文）。"""
    path: str
    title: str = ""
    category: str = ""
    tags: list[str] = Field(default_factory=list)
    owner: str = ""
    effective_date: str = ""
    review_date: str = ""
    status: str = STATUS_ACTIVE
    body: str | None = None          # 为 None 表示只改 frontmatter，正文保持不变
    create: bool = False


@app.post("/api/admin/save")
def api_save(body: SaveBody, _: str = Depends(require_admin),
             operator: str = Depends(operator_of)):
    """写回磁盘：编辑 frontmatter（可含正文），或新建一篇 .md。"""
    target = safe_join(settings.docs_root, body.path)
    if target.suffix.lower() not in (".md", ".markdown"):
        raise HTTPException(status_code=400, detail="只能保存 .md 文件")

    if body.create:
        if target.exists():
            raise HTTPException(status_code=409, detail="同名文件已存在")
        target.parent.mkdir(parents=True, exist_ok=True)
        old_meta, old_body = {}, ""
    else:
        if not target.is_file():
            raise HTTPException(status_code=404, detail="文件不存在")
        old_meta, old_body = split_frontmatter(
            target.read_text(encoding="utf-8", errors="replace"))

    meta = {
        "title": body.title or target.stem,
        "category": body.category or target.relative_to(settings.docs_root).parts[0],
        "tags": body.tags,
        "owner": body.owner,
        "effective_date": body.effective_date,
        "review_date": body.review_date,
        "status": body.status or STATUS_ACTIVE,
    }
    # 保留 frontmatter 里的自定义字段（doc_no、version 等），编辑时不能丢
    for key, value in old_meta.items():
        if key not in meta:
            meta[key] = value
    new_body = old_body if body.body is None else body.body
    # 先写临时文件再原子替换，避免写一半被 watchdog 读到半截内容
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(dump_frontmatter(meta, new_body), encoding="utf-8", newline="\n")
    tmp.replace(target)

    action = "CREATE" if body.create else "EDIT"
    oplog.log(action, body.path, operator, f"status={meta['status']}")
    rebuild_all()
    return {"ok": True, "path": target.relative_to(settings.docs_root).as_posix()}


class PathBody(BaseModel):
    path: str


@app.post("/api/admin/delete")
def api_delete(body: PathBody, _: str = Depends(require_admin),
               operator: str = Depends(operator_of)):
    """删除 = 移入 .trash + 写日志；.md 会连同同名附件目录一起移走。"""
    target = safe_join(settings.docs_root, body.path)
    if target == settings.docs_root:
        raise HTTPException(status_code=400, detail="不能删除根目录")
    if not target.exists():
        raise HTTPException(status_code=404, detail="目标不存在")
    if settings.trash_dir in target.parents or target == settings.trash_dir:
        raise HTTPException(status_code=400, detail="回收站内的内容不在此处操作")

    moved = [oplog.move_to_trash(target, operator)]
    oplog.log("DELETE", body.path, operator, f"移入 .trash/{moved[0]}")

    if target.suffix.lower() in (".md", ".markdown"):
        attach = target.with_suffix("")       # 同名附件目录
        if attach.is_dir():
            entry = oplog.move_to_trash(attach, operator)
            moved.append(entry)
            oplog.log("DELETE", attach.relative_to(settings.docs_root).as_posix(),
                      operator, f"随正文一并移入 .trash/{entry}")
    rebuild_all()
    return {"ok": True, "trash": moved}


@app.post("/api/admin/restore")
def api_restore(body: PathBody, _: str = Depends(require_admin),
                operator: str = Depends(operator_of)):
    """从回收站还原（path 为 /api/admin/trash 返回的 entry 字段）。"""
    try:
        restored = oplog.restore_from_trash(body.path, operator)
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    rebuild_all()
    return {"ok": True, "path": restored}


@app.post("/api/admin/mkdir")
def api_mkdir(body: PathBody, _: str = Depends(require_admin),
              operator: str = Depends(operator_of)):
    """新建分类目录。"""
    target = safe_join(settings.docs_root, body.path)
    target.mkdir(parents=True, exist_ok=True)
    oplog.log("MKDIR", body.path, operator, "")
    rebuild_all()
    return {"ok": True}


@app.post("/api/admin/upload")
async def api_upload(dir: str = Form(""), files: list[UploadFile] = File(...),
                     _: str = Depends(require_admin),
                     operator: str = Depends(operator_of)):
    """上传文件到指定目录：.md 即新增知识条目，其它类型即附件。"""
    dest_dir = safe_join(settings.docs_root, dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    saved = []
    for up in files:
        name = Path(up.filename or "unnamed").name          # 丢掉客户端路径
        suffix = Path(name).suffix.lower()
        if suffix not in settings.allowed_suffixes:
            raise HTTPException(status_code=400, detail=f"不允许的文件类型：{suffix}")
        target = dest_dir / name
        if target.exists():           # 同名自动加序号，不覆盖既有文件
            stem, i = target.stem, 1
            while target.exists():
                target = dest_dir / f"{stem}({i}){suffix}"
                i += 1
        size = 0
        with target.open("wb") as fp:
            while chunk := await up.read(1024 * 1024):
                size += len(chunk)
                if size > settings.max_upload_bytes:
                    fp.close()
                    target.unlink(missing_ok=True)
                    raise HTTPException(status_code=413, detail="文件超过上传大小上限")
                fp.write(chunk)
        rel = target.relative_to(settings.docs_root).as_posix()
        saved.append(rel)
        oplog.log("UPLOAD", rel, operator, f"{size} 字节")
    rebuild_all()
    return {"ok": True, "saved": saved}


@app.get("/api/admin/log")
def api_log(limit: int = 300, _: str = Depends(require_admin)):
    """查看操作日志（倒序）。"""
    return {"rows": oplog.read_log(limit)}


@app.get("/api/admin/trash")
def api_trash(limit: int = 200, _: str = Depends(require_admin)):
    """查看回收站内容与保留策略。"""
    return {"retain_days": settings.trash_retain_days, "rows": oplog.list_trash(limit)}


@app.get("/api/admin/errors")
def api_errors(_: str = Depends(require_admin)):
    """列出解析失败的 Markdown（如 frontmatter 写坏了）。"""
    return {"errors": index.errors}


@app.post("/api/admin/reindex")
def api_reindex(_: str = Depends(require_admin), operator: str = Depends(operator_of)):
    """手动强制重扫（正常情况下 watchdog 已自动处理）。"""
    n = rebuild_all()
    oplog.log("REINDEX", "-", operator, f"{n} 篇")
    return {"ok": True, "total": n}


# ---------------- 前端静态资源 ----------------
@app.get("/", response_class=HTMLResponse)
def home():
    """单页应用入口。"""
    return (STATIC_DIR / "index.html").read_text(encoding="utf-8")


@app.get("/healthz")
def healthz():
    """给 systemd / 监控用的健康检查。"""
    return {"ok": True, "docs": len(index.docs), "version": index.version}


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


def main() -> None:
    """python -m app.main 直接启动（nssm/开发调试用）。"""
    import uvicorn
    uvicorn.run("app.main:app", host=settings.host, port=settings.port,
                workers=1, log_level="info")


if __name__ == "__main__":
    main()
