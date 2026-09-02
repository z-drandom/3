"""路径安全工具：所有对外暴露的路径参数都必须过这里，防止 ../ 越权。"""
from pathlib import Path, PurePosixPath


class UnsafePathError(ValueError):
    """路径越界或非法时抛出。"""


def norm_rel(rel: str) -> str:
    """把用户传来的相对路径规范成 posix 风格，去掉盘符、前导斜杠与 . / .. 段。"""
    rel = (rel or "").replace("\\", "/").strip()
    parts = []
    for seg in PurePosixPath(rel).parts:
        if seg in ("", ".", "/"):
            continue
        if seg == "..":
            raise UnsafePathError("路径中不允许出现 ..")
        if ":" in seg:  # 例如 C: 之类的盘符
            raise UnsafePathError("路径中不允许出现盘符")
        parts.append(seg)
    return "/".join(parts)


def safe_join(root: Path, rel: str) -> Path:
    """把相对路径安全地拼到 root 下，越界直接报错。"""
    target = (root / norm_rel(rel)).resolve()
    root = root.resolve()
    if target != root and root not in target.parents:
        raise UnsafePathError("目标路径超出知识库根目录")
    return target


def rel_of(root: Path, path: Path) -> str:
    """把绝对路径转回相对根目录的 posix 相对路径。"""
    return path.resolve().relative_to(root.resolve()).as_posix()
