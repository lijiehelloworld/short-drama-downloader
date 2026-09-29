"""验证剧目 ID、画质选择与任务输入的关键边界。"""

import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import catalog
import runtime
import server
from jobs import JobError, JobManager
from video_api import AppVideoClient, Quality, VideoError, choose_quality


SERIES_ID = "7688558312157088793"
VIDEO_IDS = ["7688560096204311576", "7688560020216089624"]


def page(key, value):
    """构造官网在 HTML 中嵌入的数据片段。"""
    return f"<script>_ROUTER_DATA = {json.dumps({'loaderData': {key: value}})};</script>"


class CatalogTests(unittest.TestCase):
    def test_search_exact_title_with_book_marks(self):
        html = page("search_(keyword)/page", {
            "searchList": [
                {"video_data": {"series_id": "7688558312157088794", "series_title": "满院亲戚全是上古大妖第三季", "episode_cnt": 100}},
                {"video_data": {"series_id": SERIES_ID, "series_title": "满院亲戚全是上古大妖第四季", "episode_cnt": 155}},
            ]
        })
        with patch("catalog.fetch_html", return_value=html) as fetch:
            results = catalog.search_series("《满院亲戚全是上古大妖第四季》")
        self.assertEqual([item["series_id"] for item in results], [SERIES_ID])
        self.assertIn("/search/", fetch.call_args.args[0])

    def test_detail_returns_episode_ids_not_series_id(self):
        html = page("detail_page", {"seriesDetail": {
            "series_id": SERIES_ID, "series_name": "满院亲戚全是上古大妖第四季",
            "episode_cnt": 155, "accessible_episode_cnt": 3, "vid_list": VIDEO_IDS,
        }})
        with patch("catalog.fetch_html", return_value=html):
            detail = catalog.get_series(SERIES_ID)
        self.assertEqual(detail["episodes"], [
            {"number": 1, "video_id": VIDEO_IDS[0]},
            {"number": 2, "video_id": VIDEO_IDS[1]},
        ])
        self.assertEqual(detail["accessible_web_count"], 3)

    def test_series_id_search_uses_detail_without_name_search(self):
        html = page("detail_page", {"seriesDetail": {
            "series_id": SERIES_ID, "series_name": "满院亲戚全是上古大妖第四季",
            "episode_cnt": 155, "vid_list": VIDEO_IDS,
        }})
        with patch("catalog.fetch_html", return_value=html) as fetch:
            results = catalog.search_series(SERIES_ID)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["series_id"], SERIES_ID)
        self.assertEqual(results[0]["episode_count"], 155)
        self.assertIn("/detail?series_id=", fetch.call_args.args[0])


class QualityTests(unittest.TestCase):
    def test_vertical_quality_uses_short_edge_and_never_downgrades(self):
        options = [
            Quality("720", 720, 1280, 900, "720", "https://example.com/720.mp4", None),
            Quality("1080", 1080, 1920, 1600, "1080", "https://example.com/1080.mp4", None),
        ]
        self.assertEqual(choose_quality(options, "edge:1080").key, "1080")
        self.assertEqual(choose_quality(options, "max").key, "1080")
        with self.assertRaisesRegex(VideoError, "没有短边 1440"):
            choose_quality(options, "edge:1440")

    def test_client_lists_all_returned_qualities(self):
        client = AppVideoClient("123456", "654321")
        client._sign = Mock(return_value=("https://example.com/model", {}, b"{}"))
        post = Mock()
        post.json.return_value = {"data": {VIDEO_IDS[0]: {"video_model": {"fallback_api": "https://example.com/detail"}}}}
        get = Mock()
        get.json.return_value = {"video_info": {"data": {"video_list": {
            "normal": {"main_url": "https://example.com/720.mp4", "vwidth": 720, "vheight": 1280, "bitrate": 800},
            "high": {"main_url": "https://example.com/1080.mp4", "vwidth": 1080, "vheight": 1920, "bitrate": 1500},
        }}}}
        client.session.post = Mock(return_value=post)
        client.session.get = Mock(return_value=get)
        self.assertEqual([item.short_edge for item in client.qualities(VIDEO_IDS[0])], [1080, 720])

    def test_malformed_model_response_is_actionable_error(self):
        client = AppVideoClient("123456", "654321")
        client._sign = Mock(return_value=("https://example.com/model", {}, b"{}"))
        response = Mock()
        response.json.return_value = []
        client.session.post = Mock(return_value=response)
        with self.assertRaisesRegex(VideoError, "没有返回 data"):
            client.qualities(VIDEO_IDS[0])

    def test_signing_accepts_string_query(self):
        client = AppVideoClient("1234567890123456", "6543210987654321")
        url, headers, body = client._sign(
            "https://example.com/video?iid=6543210987654321&device_id=1234567890123456",
            {"mixed_video_id_map": {"1004": [VIDEO_IDS[0]]}},
        )
        self.assertIn("device_id=1234567890123456", url)
        self.assertIn("x-medusa", headers)
        self.assertIn(VIDEO_IDS[0].encode(), body)


class ConfigTests(unittest.TestCase):
    def test_first_read_creates_stable_private_identifiers(self):
        with tempfile.TemporaryDirectory() as directory:
            data = Path(directory)
            config_file = data / "config.json"
            with patch.object(server, "DATA", data), patch.object(server, "CONFIG_FILE", config_file), patch.dict(
                "os.environ", {"HONGGUO_DEVICE_ID": "", "HONGGUO_INSTALL_ID": "", "HONGGUO_DOWNLOAD_DIR": ""}
            ):
                first = server.read_config()
                second = server.read_config()
            self.assertEqual(first["device_id"], second["device_id"])
            self.assertEqual(first["install_id"], second["install_id"])
            self.assertRegex(first["device_id"], r"^[0-9]{16}$")
            self.assertRegex(first["install_id"], r"^[0-9]{16}$")
            self.assertEqual(first["device_source"], "generated")
            if os.name != "nt":
                self.assertEqual(stat.S_IMODE(config_file.stat().st_mode), 0o600)

    def test_config_api_hides_identifiers_and_only_changes_download_dir(self):
        with tempfile.TemporaryDirectory() as directory:
            data = Path(directory)
            config_file = data / "config.json"
            with patch.object(server, "DATA", data), patch.object(server, "CONFIG_FILE", config_file), patch.dict(
                "os.environ", {"HONGGUO_DEVICE_ID": "", "HONGGUO_INSTALL_ID": "", "HONGGUO_DOWNLOAD_DIR": ""}
            ):
                with server.app.test_client() as client:
                    initial = client.get("/api/config").get_json()
                    stored_before = json.loads(config_file.read_text(encoding="utf-8"))
                    response = client.post("/api/config", json={
                        "download_dir": str(data / "videos"), "device_id": "0000000000000000",
                    })
                    stored_after = json.loads(config_file.read_text(encoding="utf-8"))
            self.assertEqual(response.status_code, 200)
            self.assertTrue(initial["configured"])
            self.assertNotIn("device_id", initial)
            self.assertNotIn("install_id", response.get_json())
            self.assertEqual(stored_after["device_id"], stored_before["device_id"])
            self.assertEqual(stored_after["install_id"], stored_before["install_id"])
            self.assertEqual(stored_after["download_dir"], str(data / "videos"))


class JobTests(unittest.TestCase):
    def test_job_rejects_missing_episode_id(self):
        series = {"series_id": SERIES_ID, "title": "测试剧", "episodes": [{"number": 1, "video_id": VIDEO_IDS[0]}]}
        with tempfile.TemporaryDirectory() as directory:
            manager = JobManager(Path(directory) / "jobs.json", lambda: {})
            with self.assertRaisesRegex(JobError, "没有对应的 video_id"):
                manager.create(series, [2], "max")
            self.assertEqual(manager.list_jobs(), [])


class RuntimeTests(unittest.TestCase):
    def test_bundled_ffmpeg_takes_precedence_over_path(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tool = root / "ffmpeg" / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg")
            tool.parent.mkdir()
            tool.touch()
            with patch.object(runtime, "ASSET_ROOT", root), patch("runtime.shutil.which", return_value="other-ffmpeg"):
                self.assertEqual(runtime.media_tool("ffmpeg"), str(tool))


if __name__ == "__main__":
    unittest.main()
