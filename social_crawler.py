"""VilCC social media crawler — wraps MediaCrawler for multi-platform content."""
import asyncio
import json
import os
import sys
import glob
import shutil
from pathlib import Path
from typing import Dict, List, Any, Optional

MEDIA_CRAWLER_PATH = Path(__file__).parent / "media_crawler"

# Global lock: MediaCrawler uses a global config module, only one crawl at a time
_crawl_lock = asyncio.Lock()

SUPPORTED_PLATFORMS = {
    "xhs":   "小红书",
    "weibo": "微博",
    "dy":    "抖音",
    "ks":    "快手",
    "bili":  "B站",
    "zhihu": "知乎",
    "tieba": "贴吧",
}

# Per-platform cookie storage (in-memory, loaded from env or set via API)
_platform_cookies: Dict[str, str] = {}


def is_available() -> bool:
    return (MEDIA_CRAWLER_PATH / "main.py").exists()


def set_cookies(platform: str, cookie_str: str):
    _platform_cookies[platform] = cookie_str


def get_cookies(platform: str) -> str:
    return _platform_cookies.get(platform, os.environ.get(f"VILCC_{platform.upper()}_COOKIES", ""))


def has_cookies(platform: str) -> bool:
    return bool(get_cookies(platform))


def _setup_path():
    path_str = str(MEDIA_CRAWLER_PATH)
    if path_str not in sys.path:
        sys.path.insert(0, path_str)


def _clear_output(platform: str):
    """Remove stale output files before a crawl."""
    data_dir = MEDIA_CRAWLER_PATH / "data" / platform
    if data_dir.exists():
        for f in data_dir.glob("*.json"):
            f.unlink(missing_ok=True)
        for f in data_dir.glob("*.jsonl"):
            f.unlink(missing_ok=True)


def _read_results(platform: str) -> List[Dict]:
    """Read all JSON/JSONL output files from MediaCrawler's data directory."""
    data_dir = MEDIA_CRAWLER_PATH / "data" / platform
    results = []
    if not data_dir.exists():
        return results
    # Read JSONL files (one JSON object per line)
    for f in sorted(data_dir.glob("*.jsonl")):
        try:
            with open(f, encoding="utf-8") as fp:
                for line in fp:
                    line = line.strip()
                    if line:
                        try:
                            results.append(json.loads(line))
                        except json.JSONDecodeError:
                            pass
        except Exception:
            pass
    # Also read JSON files (list or single object)
    for f in sorted(data_dir.glob("*.json")):
        try:
            with open(f, encoding="utf-8") as fp:
                data = json.load(fp)
                if isinstance(data, list):
                    results.extend(data)
                elif isinstance(data, dict):
                    results.append(data)
        except Exception:
            pass
    return results


async def _run_crawler(platform: str, crawl_type: str, config_overrides: Dict) -> List[Dict]:
    """Configure and run MediaCrawler, return parsed results."""
    _setup_path()
    _clear_output(platform)

    import config as mc_config
    from main import CrawlerFactory

    # Fields to save/restore
    CONFIG_FIELDS = [
        "PLATFORM", "CRAWLER_TYPE", "KEYWORDS", "CRAWLER_MAX_NOTES_COUNT",
        "SAVE_DATA_OPTION", "LOGIN_TYPE", "COOKIES", "MAX_CONCURRENCY_NUM",
        "ENABLE_GET_COMMENTS", "ENABLE_GET_SUB_COMMENTS", "CRAWLER_MAX_SLEEP_SEC",
        "HEADLESS", "ENABLE_CDP_MODE", "START_PAGE",
    ]
    saved = {f: getattr(mc_config, f, None) for f in CONFIG_FIELDS}

    try:
        mc_config.PLATFORM = platform
        mc_config.CRAWLER_TYPE = crawl_type
        mc_config.SAVE_DATA_OPTION = "jsonl"
        mc_config.LOGIN_TYPE = "cookie"
        mc_config.COOKIES = get_cookies(platform)
        mc_config.MAX_CONCURRENCY_NUM = 2
        mc_config.ENABLE_GET_COMMENTS = False
        mc_config.ENABLE_GET_SUB_COMMENTS = False
        mc_config.CRAWLER_MAX_SLEEP_SEC = 2
        mc_config.HEADLESS = True
        mc_config.ENABLE_CDP_MODE = False
        mc_config.START_PAGE = 1
        for k, v in config_overrides.items():
            setattr(mc_config, k, v)

        crawler = CrawlerFactory.create_crawler(platform)
        await crawler.start()
        return _read_results(platform)

    finally:
        for f, v in saved.items():
            if v is not None:
                try:
                    setattr(mc_config, f, v)
                except Exception:
                    pass


async def search(
    platform: str,
    keyword: str,
    limit: int = 20,
) -> Dict[str, Any]:
    """Search for content on a social platform."""
    if not is_available():
        return {
            "error": "not_installed",
            "message": "MediaCrawler 未安装，请运行: git submodule update --init media_crawler && pip install -r media_crawler/requirements.txt && playwright install chromium",
        }
    if platform not in SUPPORTED_PLATFORMS:
        return {"error": "unsupported_platform", "message": f"不支持的平台: {platform}，支持: {list(SUPPORTED_PLATFORMS.keys())}"}

    async with _crawl_lock:
        try:
            items = await _run_crawler(platform, "search", {
                "KEYWORDS": keyword,
                "CRAWLER_MAX_NOTES_COUNT": min(limit, 50),
            })
            return {
                "platform": platform,
                "platform_name": SUPPORTED_PLATFORMS[platform],
                "keyword": keyword,
                "total": len(items),
                "results": items[:limit],
            }
        except Exception as e:
            return {"error": "crawl_failed", "message": str(e)}


async def get_creator(
    platform: str,
    creator_url: str,
    limit: int = 20,
) -> Dict[str, Any]:
    """Get posts from a creator's page."""
    if not is_available():
        return {"error": "not_installed", "message": "MediaCrawler 未安装"}
    if platform not in SUPPORTED_PLATFORMS:
        return {"error": "unsupported_platform", "message": f"不支持的平台: {platform}"}

    creator_list_field = {
        "xhs":   "XHS_CREATOR_ID_LIST",
        "weibo": "WEIBO_CREATOR_ID_LIST",
        "dy":    "DY_SPECIFIED_ID_LIST",
        "bili":  "BILI_CREATOR_ID_LIST",
        "zhihu": "ZHIHU_CREATOR_URL_LIST",
        "tieba": "TIEBA_CREATOR_URL_LIST",
    }
    field = creator_list_field.get(platform)
    if not field:
        return {"error": "unsupported", "message": f"平台 {platform} 暂不支持博主主页爬取"}

    async with _crawl_lock:
        try:
            items = await _run_crawler(platform, "creator", {
                field: [creator_url],
                "CRAWLER_MAX_NOTES_COUNT": min(limit, 50),
            })
            return {
                "platform": platform,
                "platform_name": SUPPORTED_PLATFORMS[platform],
                "creator_url": creator_url,
                "total": len(items),
                "results": items[:limit],
            }
        except Exception as e:
            return {"error": "crawl_failed", "message": str(e)}


def platform_status() -> List[Dict]:
    """Return install + cookie status for all platforms."""
    return [
        {
            "id": pid,
            "name": name,
            "available": is_available(),
            "has_cookies": has_cookies(pid),
        }
        for pid, name in SUPPORTED_PLATFORMS.items()
    ]
