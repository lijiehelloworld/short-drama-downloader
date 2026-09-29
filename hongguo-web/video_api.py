"""调用已有项目使用的移动端视频接口，并在本机保存选定画质。"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qsl, quote, urlsplit

import requests
from Crypto.Cipher import AES
from flurl.core import core_sixgod
from runtime import media_tool


USER_AGENT = (
    "com.phoenix.read/71332 (Linux; U; Android 16; zh_CN; 25053RT47C; "
    "Build/BP2A.250605.031.A3; Cronet/TTNetVersion:04657795 2026-01-23 "
    "QuicVersion:c67e9834 2025-09-08)"
)
VIDEO_MODEL_URL = (
    "https://api5-normal-sinfonlineb.fqnovel.com/novel/player/multi_video_model/v1/"
    "?iid={install_id}&device_id={device_id}&ac=wifi&channel=update_64&aid=8662"
    "&app_name=novelread&version_code=71332&version_name=7.1.3.32"
    "&device_platform=android&os=android&ssmix=a&device_type=25053RT47C"
    "&device_brand=Redmi&language=zh&os_api=36&os_version=16"
    "&manifest_version_code=71332&resolution=1280*2772&dpi=520"
    "&update_version_code=71332&host_abi=arm64-v8a&dragon_device_type=phone"
    "&pv_player=71332&compliance_status=0&need_personal_recommend=1"
    "&player_so_load=1&is_android_pad_screen=0"
)
SPADE_CONSTANTS = bytes.fromhex(
    "4d d4 c2 e6 b8 31 62 09 0e 52 b3 c7 a6 73 3b a4 "
    "1c b2 46 2b 82 9a b5 8a 19 6b 39 db 57 17 75 24 "
    "f4 9b af 7f 08 e8 d6 8d 26 a7 2e 37 c1 a9 5a 2f "
    "1f 05 a5 18 92 ae f2 94 97 32 b6 2a 38 aa dd 58"
)


class VideoError(RuntimeError):
    """视频解析、画质匹配或保存失败。"""


@dataclass(frozen=True)
class Quality:
    key: str
    width: int
    height: int
    bitrate: int
    label: str
    url: str
    content_key: bytes | None

    @property
    def short_edge(self) -> int:
        return min(self.width, self.height)

    def public(self) -> dict:
        """返回界面可见字段，不泄露下载地址或解密密钥。"""
        return {
            "key": self.key,
            "width": self.width,
            "height": self.height,
            "short_edge": self.short_edge,
            "bitrate": self.bitrate,
            "label": self.label,
        }


def _int(value: object) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _b64(value: str) -> bytes:
    value = value.strip()
    value += "=" * (-len(value) % 4)
    try:
        return base64.b64decode(value)
    except (binascii.Error, ValueError):
        return base64.urlsafe_b64decode(value)


def derive_content_key(spade_a: str) -> bytes:
    """沿用参考项目的 spade_a 内容密钥推导。"""
    raw = _b64(spade_a)
    if len(raw) < 3:
        raise VideoError("spade_a 数据过短")
    length = len(raw) - (raw[0] ^ raw[1] ^ raw[2]) + 47
    if length <= 0 or length > len(raw) * 2:
        raise VideoError("spade_a 长度无效")
    length = min(length, len(raw) - 1)
    if length < 33:
        raise VideoError("spade_a 缺少完整密钥")
    data = bytearray(raw[1:1 + length])
    odd, even = 85, 246
    for index in range(length):
        if index & 1:
            previous, odd = odd, data[index]
        else:
            previous, even = even, data[index]
        data[index] = (-21 - index.bit_count() + (previous ^ data[index])) & 0xFF
    try:
        key = binascii.unhexlify(bytes(data[1:33]).decode("ascii"))
    except (UnicodeDecodeError, binascii.Error) as exc:
        raise VideoError("spade_a 内容密钥格式无效") from exc
    if len(key) != 16:
        raise VideoError("内容密钥长度无效")
    return key


def decrypt_spade_url(value: str, key_seed: bytes) -> str:
    """沿用参考项目的加密播放地址解码。"""
    raw = _b64(value)
    if len(raw) < 5 or raw[0] != 0xA8 or raw[2:4] != b"\x01\x00":
        raise VideoError("播放地址格式无效")
    ciphertext = raw[4:]
    ciphertext = ciphertext[:len(ciphertext) // 16 * 16]
    digest = hashlib.sha512(hashlib.sha512(key_seed).digest() + SPADE_CONSTANTS).digest()
    plain = AES.new(digest[:16], AES.MODE_CBC, iv=digest[16:32]).decrypt(ciphertext)
    if plain:
        padding = plain[-1]
        if 1 <= padding <= 16 and plain[-padding:] == bytes([padding]) * padding:
            plain = plain[:-padding]
    return plain.rstrip(b"\x00").decode("utf-8")


def _http_url(value: str, label: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise VideoError(f"{label}不是有效 HTTP 地址")
    if parsed.hostname in {"localhost", "127.0.0.1", "::1"}:
        raise VideoError(f"{label}不能指向本机")
    return value


def choose_quality(qualities: list[Quality], preference: str) -> Quality:
    """按视频短边选画质，避免把竖屏 1080×1920 误称为 1920p。"""
    if not qualities:
        raise VideoError("客户端接口没有返回可下载画质")
    if preference == "max":
        candidates = qualities
    elif re.fullmatch(r"edge:\d{2,4}", preference):
        edge = int(preference.split(":", 1)[1])
        candidates = [item for item in qualities if item.short_edge == edge]
        if not candidates:
            available = ", ".join(str(edge) for edge in sorted({item.short_edge for item in qualities}))
            raise VideoError(f"本集没有短边 {edge} 的画质；实际提供：{available}")
    else:
        raise VideoError("画质选择无效")
    return max(candidates, key=lambda item: (item.short_edge, item.bitrate))


class AppVideoClient:
    """使用用户提供的设备标识解析单集视频与画质。"""

    def __init__(self, device_id: str, install_id: str):
        if not device_id.strip() or not install_id.strip():
            raise VideoError("请先在设置中填写 device_id 和 install_id")
        self.device_id = device_id.strip()
        self.install_id = install_id.strip()
        self.session = requests.Session()

    def _sign(self, url: str, payload: dict) -> tuple[str, dict, bytes]:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        parsed = urlsplit(url)
        headers = {
            "User-Agent": USER_AGENT,
            "Accept": "application/json; charset=utf-8,application/x-protobuf",
            "Content-Type": "application/json; charset=UTF-8",
            "x-xs-from-web": "0",
            "x-ss-req-ticket": str(int(time.time() * 1000)),
            "x-tt-request-tag": "t=0;n=0",
            "sdk-version": "2",
            "passport-sdk-version": "50561",
            "x-vc-bdturing-sdk-version": "3.7.2.cn",
        }
        device = {
            "device_id": self.device_id,
            "iid": self.install_id,
            "install_id": self.install_id,
            "device_brand": "Redmi",
            "device_model": "25053RT47C",
            "device_type": "25053RT47C",
            "device_manufacturer": "Xiaomi",
            "os_version": "16",
            "version_name": "7.1.3.32",
            "ua": USER_AGENT,
        }
        signed_headers, signed_url = core_sixgod(
            surl=f"{parsed.scheme}://{parsed.netloc}{parsed.path}",
            params=dict(parse_qsl(parsed.query, keep_blank_values=True)),
            data=payload,
            devices=device,
            header=headers,
            log=False,
        )
        return signed_url, signed_headers, body

    def qualities(self, video_id: str) -> list[Quality]:
        """读取某一集实际提供的所有画质，不在此阶段下载视频。"""
        if not re.fullmatch(r"\d{10,22}", video_id):
            raise VideoError("单集 video_id 格式不正确")
        url = VIDEO_MODEL_URL.format(
            install_id=quote(self.install_id, safe=""),
            device_id=quote(self.device_id, safe=""),
        )
        payload = {
            "biz_param": {
                "detail_page_version": 0,
                "device_level": 3,
                "disable_digg_stat": False,
                "need_all_video_definition": True,
                "need_mp4_align": False,
                "use_os_player": False,
                "use_server_dns": False,
                "video_platform": 1024,
            },
            "mixed_video_id_map": {"1004": [video_id]},
        }
        signed_url, headers, body = self._sign(url, payload)
        try:
            response = self.session.post(signed_url, headers=headers, data=body, timeout=30)
            response.raise_for_status()
            result = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise VideoError(f"客户端视频接口请求失败：{exc}") from exc
        data = result.get("data") if isinstance(result, dict) else None
        if not isinstance(data, dict):
            raise VideoError("客户端视频接口没有返回 data")
        entry = data.get(video_id)
        if entry is None and len(data) == 1:
            entry = next(iter(data.values()))
        if isinstance(entry, list) and entry:
            entry = entry[0]
        if not isinstance(entry, dict):
            raise VideoError("客户端视频接口没有返回该集的视频数据")
        model = entry.get("video_model")
        if isinstance(model, str):
            try:
                model = json.loads(model)
            except ValueError as exc:
                raise VideoError("video_model 格式无效") from exc
        if not isinstance(model, dict):
            raise VideoError("客户端视频接口未提供 video_model")
        fallback = model.get("fallback_api")
        if isinstance(fallback, list):
            fallback = fallback[0] if fallback else ""
        if isinstance(fallback, dict):
            fallback = fallback.get("fallback_api")
        if isinstance(fallback, str) and fallback.startswith("{"):
            try:
                decoded = json.loads(fallback)
                fallback = decoded.get("fallback_api") if isinstance(decoded, dict) else fallback
            except ValueError:
                pass
        fallback = _http_url(str(fallback or ""), "视频详情地址")
        try:
            response = self.session.get(fallback, headers={"User-Agent": USER_AGENT}, timeout=30)
            response.raise_for_status()
            info = response.json()["video_info"]["data"]
        except (requests.RequestException, ValueError, KeyError, TypeError) as exc:
            raise VideoError(f"视频详情请求失败：{exc}") from exc
        if not isinstance(info, dict) or not isinstance(info.get("video_list"), dict):
            raise VideoError("视频详情没有提供画质列表")
        seed = _b64(str(info.get("key_seed") or "")) if info.get("key_seed") else b""
        qualities = []
        for key, item in info["video_list"].items():
            if not isinstance(item, dict):
                continue
            raw_url = str(item.get("main_url") or item.get("play_addr") or "")
            if not raw_url:
                continue
            if raw_url.startswith(("http://", "https://")):
                video_url = raw_url
            elif seed:
                video_url = decrypt_spade_url(raw_url, seed)
            else:
                raise VideoError("加密播放地址缺少 key_seed")
            video_url = _http_url(video_url, "视频地址")
            spade_a = str(item.get("spade_a") or "")
            width = _int(item.get("vwidth") or item.get("width"))
            height = _int(item.get("vheight") or item.get("height"))
            label = str(item.get("quality") or item.get("definition") or item.get("gear_name") or "").strip()
            if width and height:
                label = f"{label} · {width}×{height}" if label else f"{width}×{height}"
            qualities.append(Quality(
                key=str(key), width=width, height=height,
                bitrate=_int(item.get("bitrate")), label=label or str(key),
                url=video_url, content_key=derive_content_key(spade_a) if spade_a else None,
            ))
        if not qualities:
            raise VideoError("本集没有可用播放地址")
        return sorted(qualities, key=lambda item: (item.short_edge, item.bitrate), reverse=True)

    def download(self, video_id: str, preference: str, destination: Path) -> dict:
        """下载单集，校验成品分辨率后才标记完成。"""
        quality = choose_quality(self.qualities(video_id), preference)
        ffmpeg = media_tool("ffmpeg")
        ffprobe = media_tool("ffprobe")
        if not ffmpeg or not ffprobe:
            raise VideoError("未找到 ffmpeg/ffprobe，请先安装 FFmpeg")
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            raise VideoError(f"目标文件已存在：{destination.name}")
        partial = destination.with_name(destination.stem + ".partial.mp4")
        if partial.exists():
            partial.unlink()
        command = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y"]
        if quality.content_key:
            command += ["-decryption_key", quality.content_key.hex()]
        command += ["-i", quality.url, "-c", "copy", "-movflags", "+faststart", str(partial)]
        try:
            flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            result = subprocess.run(command, capture_output=True, text=True, timeout=1800, check=False, creationflags=flags)
            if result.returncode != 0:
                raise VideoError(f"FFmpeg 保存失败：{result.stderr[-1200:].strip()}")
            probe = subprocess.run(
                [ffprobe, "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height", "-of", "json", str(partial)],
                capture_output=True, text=True, timeout=30, check=True, creationflags=flags,
            )
            stream = json.loads(probe.stdout)["streams"][0]
            width, height = _int(stream.get("width")), _int(stream.get("height"))
            if not width or not height:
                raise VideoError("成品文件没有有效视频画面")
            if quality.short_edge and min(width, height) != quality.short_edge:
                raise VideoError(f"成品画质 {width}×{height} 与所选 {quality.width}×{quality.height} 不一致")
            partial.replace(destination)
            return {"file": str(destination), "width": width, "height": height, "quality": quality.label}
        except (subprocess.TimeoutExpired, subprocess.CalledProcessError, ValueError, KeyError, IndexError) as exc:
            raise VideoError(f"视频保存或校验失败：{exc}") from exc
        finally:
            partial.unlink(missing_ok=True)
