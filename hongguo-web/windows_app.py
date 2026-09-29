"""Windows 单文件程序入口：本机服务配合系统 WebView2 窗口。"""

from __future__ import annotations

import ctypes
import subprocess
import sys
import threading

from werkzeug.serving import make_server

from runtime import media_tool
from server import app


def self_test() -> None:
    """构建后检查静态资源与随包媒体工具是否可用。"""
    with app.test_client() as client:
        for path in ("/", "/static/app.js", "/api/health"):
            response = client.get(path)
            if response.status_code != 200:
                raise RuntimeError(f"内置服务检查失败：{path} ({response.status_code})")
    for name in ("ffmpeg", "ffprobe"):
        executable = media_tool(name)
        if not executable:
            raise RuntimeError(f"安装包缺少 {name}")
        result = subprocess.run([executable, "-version"], capture_output=True, timeout=15, check=False)
        if result.returncode != 0:
            raise RuntimeError(f"安装包中的 {name} 无法运行")


def show_error(message: str) -> None:
    """无控制台运行时向用户显示启动错误。"""
    ctypes.windll.user32.MessageBoxW(None, message, "红果短剧下载", 0x10)


def acquire_mutex() -> int:
    """限制为单实例，避免两个窗口同时修改同一任务文件。"""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.argtypes = (ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p)
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    handle = kernel32.CreateMutexW(None, False, "Local\\HongguoDownloader")
    if not handle:
        raise OSError(ctypes.get_last_error(), "无法创建程序互斥锁")
    if ctypes.get_last_error() == 183:
        close_mutex(handle)
        raise RuntimeError("红果短剧下载已经打开。")
    return handle


def close_mutex(handle: int) -> None:
    """关闭 Windows 互斥锁句柄。"""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CloseHandle.argtypes = (ctypes.c_void_p,)
    kernel32.CloseHandle.restype = ctypes.c_bool
    kernel32.CloseHandle(handle)


def run_window() -> None:
    """窗口关闭后停止本机 HTTP 服务。"""
    import webview

    http = make_server("127.0.0.1", 0, app, threaded=True)
    worker = threading.Thread(target=http.serve_forever, daemon=True)
    worker.start()
    try:
        webview.create_window("红果短剧下载", f"http://127.0.0.1:{http.server_port}", width=1180, height=800)
        webview.start(gui="edgechromium")
    finally:
        http.shutdown()
        worker.join(timeout=5)
        http.server_close()


def main() -> int:
    if "--self-test" in sys.argv:
        self_test()
        return 0
    if sys.platform != "win32":
        raise RuntimeError("此入口仅用于 Windows；macOS 请运行 start.command。")
    handle = None
    try:
        handle = acquire_mutex()
        run_window()
        return 0
    except Exception as exc:
        show_error(f"程序无法启动：{exc}")
        return 1
    finally:
        if handle:
            close_mutex(handle)


if __name__ == "__main__":
    raise SystemExit(main())
