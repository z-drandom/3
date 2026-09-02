"""内存索引：扫描 docs 目录，解析 YAML frontmatter，渲染 Markdown。

设计要点：
1. 磁盘是唯一数据源，索引只是内存缓存，删文件 -> 重扫 -> 条目消失；
2. 每篇知识 = 一个 .md 文件；与其同名的文件夹 = 该篇的附件目录；
3. 整库重扫代价很低（几千篇 < 1 秒），因此文件变化时直接全量重建，逻辑最简单可靠。
"""
from __future__ import annotations

import html
import re
import threading
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import quote

import markdown as md_lib
import yaml

from .config import settings

# frontmatter 匹配：文件开头的 --- ... --- 块
_FM_RE = re.compile(r"^---\s*\r?\n(.*?)\r?\n---\s*\r?\n?", re.S)
# 渲染后重写相对路径用：src="..." / href="..."
_ATTR_RE = re.compile(r'(<(?:img|a|source|video|embed)\b[^>]*?\b(?:src|href)=")([^"]+)(")', re.I)
# 扫描时忽略的目录名与文件名
_IGNORE_DIRS = {".trash", ".git", ".svn", "__pycache__", ".obsidian", "node_modules"}
_IGNORE_FILE_PREFIX = ("~$", ".~", "._")

STATUS_ACTIVE = "生效"
STATUS_VOID = "废止"


def _to_list(value: Any) -> list[str]:
    """tags 允许写成 YAML 列表，也允许写成 "危化品, 储罐" 这种逗号串。"""
    if value is None:
        return []
    if isinstance(value, str):
        return [t.strip() for t in re.split(r"[,，;；/]", value) if t.strip()]
    if isinstance(value, (list, tuple, set)):
        return [str(t).strip() for t in value if str(t).strip()]
    return [str(value).strip()]


def _to_date(value: Any) -> str:
    """把 date / datetime / 字符串统一成 YYYY-MM-DD；解析不了就原样返回。"""
    if value in (None, ""):
        return ""
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value).strip()
    m = re.match(r"^(\d{4})[-/年.](\d{1,2})[-/月.](\d{1,2})", text)
    if m:
        return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    return text


def days_until(iso_date: str) -> int | None:
    """返回距今天的天数（负数=已过期）；日期非法返回 None。"""
    try:
        return (date.fromisoformat(iso_date) - date.today()).days
    except (ValueError, TypeError):
        return None


def split_frontmatter(text: str) -> tuple[dict, str]:
    """拆出 frontmatter 字典与正文；没有 frontmatter 时返回空字典。"""
    m = _FM_RE.match(text)
    if not m:
        return {}, text
    try:
        data = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError:
        data = {}
    if not isinstance(data, dict):
        data = {}
    return data, text[m.end():]


def dump_frontmatter(meta: dict, body: str) -> str:
    """把元数据 + 正文组装回带 frontmatter 的 Markdown 文本。"""
    fm = yaml.safe_dump(meta, allow_unicode=True, sort_keys=False, default_flow_style=False)
    return f"---\n{fm}---\n\n{body.lstrip()}"


def strip_markdown(text: str) -> str:
    """粗暴去掉 Markdown 标记，得到用于分词与摘要的纯文本。"""
    text = re.sub(r"```.*?```", " ", text, flags=re.S)          # 代码块
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", text)            # 图片
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)         # 链接保留文字
    text = re.sub(r"<[^>]+>", " ", text)                          # 内嵌 HTML
    text = re.sub(r"[#>*_`|~\-]{1,}", " ", text)                  # 标记符号
    return re.sub(r"\s+", " ", text).strip()


@dataclass
class Doc:
    """一篇知识 = 一个 .md 文件的内存视图。"""
    path: str                    # 相对 docs_root 的 posix 路径，同时充当唯一 ID
    abs_path: Path
    title: str
    category: str
    tags: list[str]
    owner: str
    effective_date: str
    review_date: str
    status: str
    body: str                    # Markdown 正文（不含 frontmatter）
    plain: str                   # 纯文本，用于检索与摘要
    extra: dict = field(default_factory=dict)   # frontmatter 里的其它自定义字段
    mtime: float = 0.0
    size: int = 0

    @property
    def dir(self) -> str:
        """所在目录（相对路径）。"""
        return str(Path(self.path).parent.as_posix()).lstrip(".")

    @property
    def attach_dir(self) -> Path:
        """同名附件目录（可能不存在）。"""
        return self.abs_path.with_suffix("")

    def attachments(self) -> list[dict]:
        """列出同名文件夹里的附件。"""
        d = self.attach_dir
        if not d.is_dir():
            return []
        out = []
        for f in sorted(d.rglob("*")):
            if f.is_file() and not f.name.startswith(_IGNORE_FILE_PREFIX):
                out.append({
                    "name": f.relative_to(d).as_posix(),
                    "path": f.relative_to(settings.docs_root).as_posix(),
                    "size": f.stat().st_size,
                })
        return out

    def review_days(self) -> int | None:
        """距复审到期的天数。"""
        return days_until(self.review_date)

    def to_brief(self) -> dict:
        """列表用的精简结构。"""
        return {
            "path": self.path, "title": self.title, "category": self.category,
            "tags": self.tags, "owner": self.owner,
            "effective_date": self.effective_date, "review_date": self.review_date,
            "status": self.status, "review_days": self.review_days(),
            "mtime": self.mtime,
        }


def _rewrite_links(html_text: str, doc_dir: str) -> str:
    """把正文里的相对链接（图片/附件）改写成 /api/asset/... 接口地址。"""
    def repl(m: re.Match) -> str:
        head, url, tail = m.group(1), m.group(2), m.group(3)
        if re.match(r"^(https?:|mailto:|tel:|data:|/|#)", url, re.I):
            return m.group(0)      # 绝对地址与锚点不动
        rel = f"{doc_dir}/{url}" if doc_dir else url
        rel = re.sub(r"/{2,}", "/", rel)
        return f"{head}/api/asset/{quote(rel)}{tail}"

    return _ATTR_RE.sub(repl, html_text)


def render_markdown(body: str, doc_dir: str) -> str:
    """渲染 Markdown 为 HTML，开启表格、围栏代码块等扩展。"""
    renderer = md_lib.Markdown(
        extensions=["tables", "fenced_code", "codehilite", "toc",
                    "attr_list", "sane_lists", "nl2br", "footnotes", "admonition"],
        extension_configs={"codehilite": {"noclasses": False, "guess_lang": False}},
        output_format="html",
    )
    return _rewrite_links(renderer.convert(body), doc_dir)


class Index:
    """全库内存索引，线程安全（读多写少，用一把读写锁足够）。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.docs: dict[str, Doc] = {}
        self.version = 0                 # 每次重建 +1，前端可据此判断是否需要刷新
        self.built_at = ""
        self.errors: list[dict] = []     # 解析失败的文件，前端管理视图可见

    # ---------- 构建 ----------
    def _iter_md_files(self) -> Iterable[Path]:
        """遍历根目录下所有 .md，跳过隐藏目录与临时文件。"""
        root = settings.docs_root
        for p in root.rglob("*"):
            if p.is_dir():
                continue
            if p.suffix.lower() not in (".md", ".markdown"):
                continue
            parts = p.relative_to(root).parts
            if any(seg in _IGNORE_DIRS or seg.startswith(".") for seg in parts[:-1]):
                continue
            if p.name.startswith(_IGNORE_FILE_PREFIX) or p.name.startswith("."):
                continue
            yield p

    def _parse(self, path: Path) -> Doc:
        """读取单个 .md 并解析成 Doc。"""
        root = settings.docs_root
        rel = path.relative_to(root).as_posix()
        raw = path.read_text(encoding="utf-8", errors="replace")
        meta, body = split_frontmatter(raw)
        stat = path.stat()

        known = {"title", "category", "tags", "owner", "effective_date", "review_date", "status"}
        # 分类缺省取一级目录名，符合“按目录分类”的约定
        default_category = rel.split("/")[0] if "/" in rel else "未分类"
        status = str(meta.get("status", STATUS_ACTIVE)).strip() or STATUS_ACTIVE

        return Doc(
            path=rel,
            abs_path=path,
            title=str(meta.get("title") or path.stem).strip(),
            category=str(meta.get("category") or default_category).strip(),
            tags=_to_list(meta.get("tags")),
            owner=str(meta.get("owner") or "").strip(),
            effective_date=_to_date(meta.get("effective_date")),
            review_date=_to_date(meta.get("review_date")),
            status=status,
            body=body,
            plain=strip_markdown(body),
            extra={k: v for k, v in meta.items() if k not in known},
            mtime=stat.st_mtime,
            size=stat.st_size,
        )

    def rebuild(self) -> int:
        """全量重扫目录，返回文档数。"""
        docs: dict[str, Doc] = {}
        errors: list[dict] = []
        settings.docs_root.mkdir(parents=True, exist_ok=True)
        for path in self._iter_md_files():
            try:
                doc = self._parse(path)
                docs[doc.path] = doc
            except Exception as exc:      # 单篇解析失败不能拖垮全库
                errors.append({
                    "path": path.relative_to(settings.docs_root).as_posix(),
                    "error": f"{type(exc).__name__}: {exc}",
                })
        with self._lock:
            self.docs = docs
            self.errors = errors
            self.version += 1
            self.built_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        return len(docs)

    # ---------- 查询 ----------
    def get(self, rel_path: str) -> Doc | None:
        with self._lock:
            return self.docs.get(rel_path)

    def all(self) -> list[Doc]:
        with self._lock:
            return list(self.docs.values())

    def tree(self) -> list[dict]:
        """构建左侧目录树：目录节点 + 文档叶子节点。"""
        root: dict[str, Any] = {"name": "", "path": "", "dirs": {}, "docs": []}
        for doc in sorted(self.all(), key=lambda d: (d.path.lower())):
            node = root
            segs = doc.path.split("/")[:-1]
            acc = []
            for seg in segs:
                acc.append(seg)
                node = node["dirs"].setdefault(
                    seg, {"name": seg, "path": "/".join(acc), "dirs": {}, "docs": []}
                )
            node["docs"].append(doc.to_brief())

        def convert(node: dict) -> dict:
            children = [convert(v) for _, v in sorted(node["dirs"].items())]
            count = len(node["docs"]) + sum(c["count"] for c in children)
            return {"name": node["name"], "path": node["path"],
                    "children": children, "docs": node["docs"], "count": count}

        return [convert(v) for _, v in sorted(root["dirs"].items())] + \
               ([{"name": "（根目录）", "path": "", "children": [], "docs": root["docs"],
                  "count": len(root["docs"])}] if root["docs"] else [])

    def tags(self) -> list[dict]:
        """标签及其文档数，按数量倒序。"""
        counter: dict[str, int] = {}
        for doc in self.all():
            for t in doc.tags:
                counter[t] = counter.get(t, 0) + 1
        return [{"tag": k, "count": v}
                for k, v in sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))]

    def due_reviews(self, days: int | None = None) -> list[dict]:
        """到期提醒：review_date 距今 < days 天（含已过期）的生效文档。"""
        limit = settings.review_warn_days if days is None else days
        out = []
        for doc in self.all():
            if doc.status == STATUS_VOID:
                continue
            d = doc.review_days()
            if d is None or d >= limit:
                continue
            brief = doc.to_brief()
            brief["level"] = "overdue" if d < 0 else ("urgent" if d <= 7 else "warn")
            out.append(brief)
        out.sort(key=lambda b: (b["review_days"] if b["review_days"] is not None else 9999))
        return out

    def stats(self) -> dict:
        docs = self.all()
        return {
            "total": len(docs),
            "active": sum(1 for d in docs if d.status != STATUS_VOID),
            "void": sum(1 for d in docs if d.status == STATUS_VOID),
            "categories": len({d.category for d in docs}),
            "tags": len(self.tags()),
            "due": len(self.due_reviews()),
            "version": self.version,
            "built_at": self.built_at,
            "errors": len(self.errors),
            "root": str(settings.docs_root),
        }


index = Index()


def escape(text: str) -> str:
    """HTML 转义，摘要拼接时用。"""
    return html.escape(text, quote=False)
