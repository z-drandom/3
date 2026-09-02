"""中文全文检索：jieba 分词 + 内存倒排索引 + 关键词高亮片段。"""
from __future__ import annotations

import re
import threading
from typing import Iterable

from .indexer import Doc, Index, escape

try:
    import jieba
    HAS_JIEBA = True
except ImportError:                 # 内网装不上 jieba 时退化为二元切分，检索仍可用
    jieba = None
    HAS_JIEBA = False
    print("[search] 未安装 jieba，已退化为二元（bigram）切分模式", flush=True)

# 字段权重：命中标题的价值远高于命中正文
W_TITLE, W_TAG, W_CATEGORY, W_OWNER, W_BODY = 12.0, 8.0, 5.0, 4.0, 1.0
SNIPPET_RADIUS = 60          # 摘要片段：命中位置左右各取多少字
MAX_SNIPPETS = 3             # 每篇最多给几段摘要
MAX_SPAN = 240               # 单段摘要最长字数，避免整篇糊成一坨


def tokenize(text: str) -> list[str]:
    """中文用 jieba 搜索引擎模式切分，英文数字按单词切，统一小写。"""
    if not text:
        return []
    low = text.lower()
    tokens = set()
    if HAS_JIEBA:
        for tok in jieba.cut_for_search(low):
            tok = tok.strip()
            if len(tok) >= 2 or (len(tok) == 1 and "一" <= tok <= "鿿"):
                tokens.add(tok)
    else:
        for run in re.findall(r"[\u4e00-\u9fff]+", low):      # 连续汉字串
            tokens.add(run) if len(run) <= 2 else None
            for i in range(len(run) - 1):
                tokens.add(run[i:i + 2])                        # 二元组
    for word in re.findall(r"[a-z0-9][a-z0-9_\-.]{1,}", text.lower()):
        tokens.add(word)
    return sorted(tokens)


class SearchIndex:
    """倒排索引：token -> 文档路径集合。每次目录重扫后整体重建。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._inv: dict[str, set[str]] = {}
        self.version = -1

    def build(self, docs: Iterable[Doc]) -> None:
        inv: dict[str, set[str]] = {}
        for doc in docs:
            blob = " ".join([doc.title, doc.category, doc.owner,
                             " ".join(doc.tags), doc.plain])
            for tok in tokenize(blob):
                inv.setdefault(tok, set()).add(doc.path)
        with self._lock:
            self._inv = inv

    def candidates(self, tokens: list[str]) -> set[str]:
        """取所有查询词命中的文档并集（宽召回，排序阶段再收紧）。"""
        with self._lock:
            hit: set[str] = set()
            for tok in tokens:
                hit |= self._inv.get(tok, set())
            return hit


search_index = SearchIndex()


def _score_field(text: str, tokens: list[str]) -> float:
    """统计一组词在某字段里的出现次数之和。"""
    low = text.lower()
    return float(sum(low.count(tok) for tok in tokens))


def _highlight(text: str, tokens: list[str]) -> str:
    """先转义再用 <mark> 包裹命中词，避免 XSS。"""
    out = escape(text)
    for tok in sorted(set(tokens), key=len, reverse=True):
        if not tok:
            continue
        out = re.sub(f"({re.escape(escape(tok))})", r"<mark>\1</mark>", out, flags=re.I)
    return out


def make_snippets(plain: str, tokens: list[str]) -> list[str]:
    """按命中位置切出若干带高亮的上下文片段。"""
    low = plain.lower()
    positions: list[int] = []
    for tok in tokens:
        start = 0
        while len(positions) < 30:
            i = low.find(tok, start)
            if i < 0:
                break
            positions.append(i)
            start = i + max(1, len(tok))
    if not positions:
        return [escape(plain[:150]) + ("…" if len(plain) > 150 else "")]

    positions.sort()
    spans: list[tuple[int, int]] = []
    for pos in positions:
        s, e = max(0, pos - SNIPPET_RADIUS), min(len(plain), pos + SNIPPET_RADIUS)
        if spans and s <= spans[-1][1] and (max(spans[-1][1], e) - spans[-1][0]) <= MAX_SPAN:
            spans[-1] = (spans[-1][0], max(spans[-1][1], e))   # 相邻片段合并，但不超过 MAX_SPAN
        else:
            spans.append((s, e))
        if len(spans) >= MAX_SNIPPETS:
            break
    return [("…" if s > 0 else "") + _highlight(plain[s:e], tokens) +
            ("…" if e < len(plain) else "") for s, e in spans]


def search(idx: Index, query: str, *, tag: str = "", category: str = "",
           status: str = "", limit: int = 50) -> list[dict]:
    """全文检索主入口。query 为空时退化成“按条件筛选 + 按修改时间排序”。"""
    query = (query or "").strip()
    tokens = tokenize(query)
    # 短查询（如单个英文词/编号）保证原串也参与匹配
    if query and query.lower() not in tokens:
        tokens.append(query.lower())

    if query:
        paths = search_index.candidates(tokens)
        docs = [d for p in paths if (d := idx.get(p))]
        # 倒排未命中时兜底做一次子串扫描，保证“搜得到就一定搜得到”
        if not docs:
            docs = [d for d in idx.all()
                    if query.lower() in (d.title + d.plain + " ".join(d.tags)).lower()]
    else:
        docs = idx.all()

    results = []
    for doc in docs:
        if tag and tag not in doc.tags:
            continue
        if category and doc.category != category and not doc.path.startswith(category + "/"):
            continue
        if status and doc.status != status:
            continue

        if query:
            score = (
                _score_field(doc.title, tokens) * W_TITLE
                + _score_field(" ".join(doc.tags), tokens) * W_TAG
                + _score_field(doc.category, tokens) * W_CATEGORY
                + _score_field(doc.owner, tokens) * W_OWNER
                + _score_field(doc.plain, tokens) * W_BODY
            )
            if score <= 0:
                continue
            if doc.status == "废止":
                score *= 0.3               # 废止文件仍可检索，但排在后面
            snippets = make_snippets(doc.plain, tokens)
        else:
            score, snippets = 0.0, [escape(doc.plain[:150])]

        item = doc.to_brief()
        item["score"] = round(score, 2)
        item["snippets"] = snippets
        item["title_html"] = _highlight(doc.title, tokens) if query else escape(doc.title)
        results.append(item)

    if query:
        results.sort(key=lambda r: (-r["score"], r["title"]))
    else:
        results.sort(key=lambda r: -r["mtime"])
    return results[:limit]
