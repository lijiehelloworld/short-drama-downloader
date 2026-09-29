"""定位静态资源、用户数据与可执行媒体工具。"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
ASSET_ROOT = Path(getattr(sys, "_MEIPASS", PROJECT_ROOT))

if os.name == "nt":
    DATA_DIR = Path(os.getenv("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "HongguoDownloader"
    DEFAULT_DOWNLOAD_DIR = Path.home() / "Downloads" / "红果短剧"
else:
    DATA_DIR = PROJECT_ROOT / "data"
    DEFAULT_DOWNLOAD_DIR = PROJECT_ROOT / "downloads"


def media_tool(name: str) -> str | None:
    """优先使用随 Windows 安装包提供的 FFmpeg 工具。"""
    filename = f"{name}.exe" if os.name == "nt" else name
    bundled = ASSET_ROOT / "ffmpeg" / filename
    return str(bundled) if bundled.is_file() else shutil.which(name)
