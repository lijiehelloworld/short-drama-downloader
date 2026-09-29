"""读取红果公开网页中的剧目与逐集元数据。"""

from __future__ import annotations

import json
import re
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen


BASE_URL = "https://hongguoduanju.com"
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/126.0 Safari/537.36"
ROUTER_MARKER = "_ROUTER_DATA = "


class CatalogError(RuntimeError):
    """公开网页无法提供预期数据时抛出。"""


def fetch_html(url: str) -> str:
    """只读取固定官网的 HTML，避免把任意地址当作抓取目标。"""
    if not url.startswith(BASE_URL + "/"):
        raise CatalogError("只允许访问红果短剧官网")
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept-Language": "zh-CN,zh;q=0.9"})
    for attempt in range(3):
        try:
            with urlopen(request, timeout=20) as response:
                return response.read(5_000_000).decode("utf-8", errors="replace")
        except HTTPError as exc:
            if exc.code < 500 or attempt == 2:
                raise CatalogError(f"官网请求失败：HTTP {exc.code}") from exc
        except (URLError, TimeoutError) as exc:
            if attempt == 2:
                raise CatalogError(f"官网请求失败：{exc}") from exc
        time.sleep(0.5 * (attempt + 1))
    raise CatalogError("官网请求失败")


def parse_loader(html: str, page_key: str) -> dict:
    """解析服务端注入的 JSON，不执行网页脚本。"""
    offset = html.find(ROUTER_MARKER)
    if offset < 0:
        raise CatalogError("官网页面未包含剧目数据，页面结构可能已变化")
    try:
        router, _ = json.JSONDecoder().raw_decode(html[offset + len(ROUTER_MARKER):])
        page = router["loaderData"][page_key]
    except (ValueError, KeyError, TypeError) as exc:
        raise CatalogError("官网剧目数据格式已变化") from exc
    if not isinstance(page, dict) or page.get("isSuccess") is False:
        raise CatalogError("官网未返回有效剧目数据")
    return page


def search_series(query: str) -> list[dict]:
    """按剧名搜索，或通过 series_id 直接定位剧目。"""
    query = query.strip()
    if query.startswith("《") and query.endswith("》"):
        query = query[1:-1].strip()
    if not query or len(query) > 100:
        raise CatalogError("请输入剧名或 series_id，最多 100 字")
    if re.fullmatch(r"[0-9]+", query):
        detail = get_series(query)
        return [{
            "series_id": detail["series_id"],
            "title": detail["title"],
            "episode_count": detail["episode_count"],
            "cover": "",
            "detail_url": detail["detail_url"],
            "exact": True,
        }]
    page = parse_loader(fetch_html(f"{BASE_URL}/search/{quote(query, safe='')}"), "search_(keyword)/page")
    results = []
    for item in page.get("searchList") or []:
        video = item.get("video_data") if isinstance(item, dict) else None
        if not isinstance(video, dict):
            continue
        title = str(video.get("series_title") or item.get("name") or "").strip()
        series_id = str(video.get("series_id") or "")
        if not title or not re.fullmatch(r"\d{10,22}", series_id):
            continue
        if query not in title and title not in query:
            continue
        results.append({
            "series_id": series_id,
            "title": title,
            "episode_count": int(video.get("episode_cnt") or 0),
            "cover": str(video.get("series_cover") or ""),
            "detail_url": f"{BASE_URL}/detail?series_id={series_id}",
            "exact": title == query,
        })
    results.sort(key=lambda item: (not item["exact"], item["title"]))
    return results[:30]


def get_series(series_id: str) -> dict:
    """获取该剧真正的逐集 video_id；集数不足时不虚构缺失项。"""
    if not re.fullmatch(r"\d{10,22}", series_id):
        raise CatalogError("剧目 ID 格式不正确")
    page = parse_loader(fetch_html(f"{BASE_URL}/detail?series_id={series_id}"), "detail_page")
    detail = page.get("seriesDetail")
    if not isinstance(detail, dict) or str(detail.get("series_id")) != series_id:
        raise CatalogError("官网返回的剧目与请求 ID 不一致")
    vids = detail.get("vid_list") or []
    if not isinstance(vids, list):
        raise CatalogError("官网未提供逐集 ID 列表")
    episodes = [
        {"number": number, "video_id": str(vid)}
        for number, vid in enumerate(vids, 1)
        if re.fullmatch(r"\d{10,22}", str(vid))
    ]
    return {
        "series_id": series_id,
        "title": str(detail.get("series_name") or "").strip(),
        "episode_count": int(detail.get("episode_cnt") or len(vids)),
        "accessible_web_count": int(detail.get("accessible_episode_cnt") or 0),
        "episodes": episodes,
        "detail_url": f"{BASE_URL}/detail?series_id={series_id}",
    }
