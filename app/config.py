"""全局配置：全部来自环境变量，无配置文件、无数据库。"""
import os
from pathlib import Path


def _env_int(name: str, default: int) -> int:
    """读取整数型环境变量，非法值回退默认值。"""
    try:
        return int(os.environ.get(name, "").strip() or default)
    except ValueError:
        return default


class Settings:
    def __init__(self) -> None:
        # 知识库根目录：唯一的数据源，删文件即删条目
        self.docs_root = Path(
            os.environ.get("EHS_DOCS_ROOT") or (Path(__file__).resolve().parents[1] / "docs")
        ).expanduser().resolve()

        # 回收站与操作日志，均放在根目录下的隐藏目录，扫描时会被跳过
        self.trash_dir = Path(
            os.environ.get("EHS_TRASH_DIR") or (self.docs_root / ".trash")
        ).expanduser().resolve()
        self.log_file = Path(
            os.environ.get("EHS_LOG_FILE") or (self.trash_dir / "operation.log")
        ).expanduser().resolve()

        # 管理口令：为空则管理视图整体关闭（纯只读站点）
        self.admin_token = os.environ.get("EHS_ADMIN_TOKEN", "").strip()

        self.host = os.environ.get("EHS_HOST", "0.0.0.0")
        self.port = _env_int("EHS_PORT", 8080)

        # 回收站保留天数；0 = 永不清理
        self.trash_retain_days = _env_int("EHS_TRASH_RETAIN_DAYS", 90)
        # 到期提醒阈值（天）
        self.review_warn_days = _env_int("EHS_REVIEW_WARN_DAYS", 30)

        # 允许上传的附件后缀（.md 正文与常见办公附件）
        self.allowed_suffixes = {
            ".md", ".markdown",
            ".pdf", ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".svg",
            ".xls", ".xlsx", ".xlsm", ".csv", ".doc", ".docx", ".ppt", ".pptx",
            ".txt", ".zip", ".dwg", ".mp4",
        }
        # 单文件上传上限（字节），默认 100 MB
        self.max_upload_bytes = _env_int("EHS_MAX_UPLOAD_MB", 100) * 1024 * 1024

    def ensure_dirs(self) -> None:
        """确保根目录、回收站、日志文件存在。"""
        self.docs_root.mkdir(parents=True, exist_ok=True)
        self.trash_dir.mkdir(parents=True, exist_ok=True)
        self.log_file.parent.mkdir(parents=True, exist_ok=True)
        if not self.log_file.exists():
            self.log_file.write_text(
                "# 时间\t操作\t目标\t操作人\t备注\n", encoding="utf-8"
            )


settings = Settings()
