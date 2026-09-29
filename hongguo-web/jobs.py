"""顺序下载任务与可恢复的本地状态。"""

from __future__ import annotations

import json
import re
import threading
import uuid
from datetime import datetime
from pathlib import Path

from video_api import AppVideoClient, VideoError


def safe_title(title: str) -> str:
    """只保留适合 macOS 文件夹名称的剧名。"""
    return re.sub(r"[\\/:*?\"<>|\x00-\x1f]", "_", title).strip(" .")[:80] or "未命名短剧"


class JobError(RuntimeError):
    """任务创建或状态切换不合法。"""


class JobManager:
    """单工作线程执行任务；每集结果立即写入 JSON。"""

    def __init__(self, store: Path, config_reader):
        self.store = store
        self.config_reader = config_reader
        self.lock = threading.RLock()
        self.jobs: dict[str, dict] = {}
        self.active_id: str | None = None
        self.pause_requested = False
        self._load()

    def _load(self) -> None:
        if not self.store.exists():
            return
        try:
            data = json.loads(self.store.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("任务文件不是对象")
            for job_id, job in data.items():
                if not isinstance(job, dict) or not isinstance(job.get("episodes"), list):
                    continue
                if job.get("status") in {"queued", "running"}:
                    job["status"] = "interrupted"
                for episode in job["episodes"]:
                    if episode.get("status") == "running":
                        episode["status"] = "pending"
                self.jobs[job_id] = job
        except (OSError, ValueError, TypeError) as exc:
            raise JobError(f"读取任务文件失败：{exc}") from exc

    def _save(self) -> None:
        self.store.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.store.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.jobs, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.store)

    def list_jobs(self) -> list[dict]:
        with self.lock:
            return [json.loads(json.dumps(job)) for job in reversed(list(self.jobs.values()))]

    def get(self, job_id: str) -> dict:
        with self.lock:
            job = self.jobs.get(job_id)
            if job is None:
                raise JobError("任务不存在")
            return json.loads(json.dumps(job))

    def create(self, series: dict, numbers: list[int], preference: str) -> dict:
        if not numbers or len(numbers) > 500:
            raise JobError("请选择 1 到 500 集")
        if preference != "max" and not re.fullmatch(r"edge:\d{2,4}", preference):
            raise JobError("画质选择无效")
        episode_map = {item["number"]: item["video_id"] for item in series["episodes"]}
        unique = sorted(set(numbers))
        if any(number not in episode_map for number in unique):
            raise JobError("所选集数没有对应的 video_id")
        with self.lock:
            if self.active_id is not None:
                raise JobError("已有下载任务运行，请等其完成或暂停")
            job_id = uuid.uuid4().hex[:12]
            job = {
                "id": job_id,
                "series_id": series["series_id"],
                "title": series["title"],
                "preference": preference,
                "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                "status": "queued",
                "episodes": [
                    {"number": number, "video_id": episode_map[number], "status": "pending", "message": "等待下载", "file": ""}
                    for number in unique
                ],
            }
            self.jobs[job_id] = job
            self._save()
            self._start(job_id)
            return json.loads(json.dumps(job))

    def _start(self, job_id: str) -> None:
        self.active_id = job_id
        self.pause_requested = False
        thread = threading.Thread(target=self._work, args=(job_id,), daemon=True)
        thread.start()

    def pause(self, job_id: str) -> dict:
        with self.lock:
            if self.active_id != job_id:
                raise JobError("此任务当前没有运行")
            self.pause_requested = True
            self.jobs[job_id]["status"] = "pausing"
            self._save()
            return self.get(job_id)

    def resume(self, job_id: str) -> dict:
        with self.lock:
            if self.active_id is not None:
                raise JobError("已有下载任务运行")
            job = self.jobs.get(job_id)
            if job is None:
                raise JobError("任务不存在")
            if job["status"] not in {"paused", "interrupted", "completed_with_errors"}:
                raise JobError("当前任务不能恢复")
            for episode in job["episodes"]:
                if episode["status"] == "error":
                    episode["status"] = "pending"
                    episode["message"] = "等待重试"
            job["status"] = "queued"
            self._save()
            self._start(job_id)
            return self.get(job_id)

    def _work(self, job_id: str) -> None:
        try:
            config = self.config_reader()
            client = AppVideoClient(config.get("device_id", ""), config.get("install_id", ""))
            base = Path(config["download_dir"]).expanduser()
            with self.lock:
                job = self.jobs[job_id]
                job["status"] = "running"
                self._save()
            for episode in job["episodes"]:
                with self.lock:
                    if self.pause_requested:
                        job["status"] = "paused"
                        self._save()
                        return
                    if episode["status"] == "done":
                        continue
                    episode["status"] = "running"
                    episode["message"] = "正在解析画质并保存视频"
                    self._save()
                destination = base / safe_title(job["title"]) / f"第{episode['number']:03d}集.mp4"
                try:
                    result = client.download(episode["video_id"], job["preference"], destination)
                except (VideoError, OSError) as exc:
                    with self.lock:
                        episode["status"] = "error"
                        episode["message"] = str(exc)
                        self._save()
                else:
                    with self.lock:
                        episode["status"] = "done"
                        episode["message"] = f"完成：{result['width']}×{result['height']}"
                        episode["file"] = result["file"]
                        self._save()
            with self.lock:
                failed = any(item["status"] == "error" for item in job["episodes"])
                job["status"] = "completed_with_errors" if failed else "completed"
                self._save()
        except Exception as exc:
            with self.lock:
                job = self.jobs[job_id]
                job["status"] = "interrupted"
                job["message"] = str(exc)
                self._save()
        finally:
            with self.lock:
                self.active_id = None
                self.pause_requested = False
