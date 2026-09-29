"""仅在本机监听的短剧检索与下载 Web 服务。"""

from __future__ import annotations

import json
import os
import secrets
import threading
import webbrowser
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory
from werkzeug.exceptions import HTTPException

from catalog import CatalogError, get_series, search_series
from jobs import JobError, JobManager
from runtime import ASSET_ROOT, DATA_DIR, DEFAULT_DOWNLOAD_DIR, media_tool
from video_api import AppVideoClient, VideoError


ROOT = ASSET_ROOT
DATA = DATA_DIR
CONFIG_FILE = DATA / "config.json"
CONFIG_LOCK = threading.RLock()

app = Flask(__name__, static_folder=None)
app.config["TRUSTED_HOSTS"] = ["127.0.0.1", "localhost"]


def read_config() -> dict:
    """首次使用时生成持久设备标识，环境变量可覆盖本机配置。"""
    with CONFIG_LOCK:
        try:
            config = json.loads(CONFIG_FILE.read_text(encoding="utf-8")) if CONFIG_FILE.exists() else {}
        except (OSError, ValueError) as exc:
            raise JobError(f"读取配置失败：{exc}") from exc
        if not isinstance(config, dict):
            raise JobError("配置文件格式无效")
        device_id = str(config.get("device_id") or "")
        install_id = str(config.get("install_id") or "")
        env_device = os.getenv("HONGGUO_DEVICE_ID")
        env_install = os.getenv("HONGGUO_INSTALL_ID")
        generated = False
        if not device_id and not env_device:
            device_id = str(10**15 + secrets.randbelow(9 * 10**15))
            generated = True
        if not install_id and not env_install:
            install_id = str(10**15 + secrets.randbelow(9 * 10**15))
            generated = True
        if generated:
            config.update({"device_id": device_id, "install_id": install_id, "device_source": "generated"})
            save_config(config)
        return {
            "device_id": env_device or device_id,
            "install_id": env_install or install_id,
            "download_dir": os.getenv("HONGGUO_DOWNLOAD_DIR") or str(config.get("download_dir") or DEFAULT_DOWNLOAD_DIR),
            "device_source": "environment" if env_device or env_install else str(config.get("device_source") or "manual"),
        }


def save_config(config: dict) -> None:
    """原子写入用户配置，并限制文件权限。"""
    with CONFIG_LOCK:
        DATA.mkdir(parents=True, exist_ok=True)
        temporary = CONFIG_FILE.with_suffix(".tmp")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(config, stream, ensure_ascii=False, indent=2)
            os.chmod(temporary, 0o600)
            temporary.replace(CONFIG_FILE)
        finally:
            temporary.unlink(missing_ok=True)


manager = JobManager(DATA / "jobs.json", read_config)


@app.errorhandler(CatalogError)
@app.errorhandler(VideoError)
@app.errorhandler(JobError)
def known_error(exc):
    return jsonify({"error": str(exc)}), 400


@app.errorhandler(Exception)
def unexpected_error(exc):
    if isinstance(exc, HTTPException):
        return jsonify({"error": exc.description}), exc.code
    app.logger.exception("请求处理失败")
    return jsonify({"error": "服务处理失败，请查看终端日志"}), 500


@app.route("/")
def index():
    return send_from_directory(ROOT / "static", "index.html")


@app.route("/static/<path:filename>")
def static_file(filename):
    return send_from_directory(ROOT / "static", filename)


@app.route("/api/health")
def health():
    return jsonify({"ok": True, "ffmpeg": media_tool("ffmpeg"), "ffprobe": media_tool("ffprobe")})


@app.route("/api/search")
def search():
    return jsonify({"items": search_series(request.args.get("q", ""))})


@app.route("/api/series/<series_id>")
def series(series_id):
    return jsonify(get_series(series_id))


@app.route("/api/config", methods=["GET", "POST"])
def config():
    current = read_config()
    if request.method == "POST":
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            raise JobError("请提交 JSON 配置")
        folder = str(data.get("download_dir") or current["download_dir"]).strip()
        download_dir = Path(folder).expanduser()
        if not download_dir.is_absolute():
            raise JobError("下载目录必须是绝对路径")
        with CONFIG_LOCK:
            stored = json.loads(CONFIG_FILE.read_text(encoding="utf-8")) if CONFIG_FILE.exists() else {}
            if not isinstance(stored, dict):
                raise JobError("配置文件格式无效")
            stored["download_dir"] = str(download_dir)
            save_config(stored)
        current = read_config()
    return jsonify({
        "configured": bool(current["device_id"] and current["install_id"]),
        "download_dir": current["download_dir"],
        "ffmpeg_ready": bool(media_tool("ffmpeg") and media_tool("ffprobe")),
    })


@app.route("/api/qualities", methods=["POST"])
def qualities():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise VideoError("请提交单集 video_id")
    config = read_config()
    client = AppVideoClient(config["device_id"], config["install_id"])
    video_id = str(data.get("video_id") or "")
    return jsonify({"video_id": video_id, "items": [item.public() for item in client.qualities(video_id)]})


@app.route("/api/jobs", methods=["GET", "POST"])
def jobs():
    if request.method == "GET":
        return jsonify({"items": manager.list_jobs()[:10]})
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or not isinstance(data.get("episodes"), list):
        raise JobError("请选择需要下载的集数")
    numbers = data["episodes"]
    if any(type(number) is not int for number in numbers):
        raise JobError("集数必须是整数")
    config = read_config()
    AppVideoClient(config["device_id"], config["install_id"])
    series_data = get_series(str(data.get("series_id") or ""))
    job = manager.create(series_data, numbers, str(data.get("preference") or "max"))
    return jsonify(job), 201


@app.route("/api/jobs/<job_id>")
def job(job_id):
    return jsonify(manager.get(job_id))


@app.route("/api/jobs/<job_id>/pause", methods=["POST"])
def pause(job_id):
    return jsonify(manager.pause(job_id))


@app.route("/api/jobs/<job_id>/resume", methods=["POST"])
def resume(job_id):
    return jsonify(manager.resume(job_id))


if __name__ == "__main__":
    port = int(os.getenv("HONGGUO_PORT", "8765"))
    url = f"http://127.0.0.1:{port}"
    threading.Timer(1, lambda: webbrowser.open(url)).start()
    print(f"红果下载工具：{url}")
    app.run(host="127.0.0.1", port=port, debug=False, use_reloader=False, threaded=True)
