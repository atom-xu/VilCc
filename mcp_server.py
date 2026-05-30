"""VilCC MCP server — exposes subtitle fetching tools via the Model Context Protocol."""
import asyncio
import json
import sys
from typing import Any

from mcp.server.fastmcp import FastMCP
import fetcher


def _strip_audio(result: Any) -> Any:
    """Remove audio_base64 field from any result dict or list of dicts."""
    if isinstance(result, dict):
        return {k: v for k, v in result.items() if k != "audio_base64"}
    if isinstance(result, list):
        return [_strip_audio(item) for item in result]
    return result


# ── startup: ensure Bilibili cookies are loaded (non-interactive) ──────────
fetcher.ensure_bilibili_cookies(interactive=False)

# ── create server ───────────────────────────────────────────────────────────
mcp = FastMCP("vilcc")


# ── Tool 1: search_videos ───────────────────────────────────────────────────
@mcp.tool()
def search_videos(
    query: str,
    platform: str = "bilibili",
    limit: int = 10,
) -> str:
    """Search for videos on YouTube or Bilibili.

    Args:
        query: Search keywords.
        platform: Target platform — "youtube", "bilibili", or "all". Defaults to "bilibili".
        limit: Maximum number of results to return (1-20). Defaults to 10.

    Returns:
        JSON string with a list of video results containing title, url,
        platform, duration, view_count, upload_date, and channel.
    """
    results = fetcher.search_videos(query, platform=platform, limit=limit)
    cleaned = _strip_audio(results)
    return json.dumps(cleaned, ensure_ascii=False, indent=2)


# ── Tool 2: fetch_subtitle ──────────────────────────────────────────────────
@mcp.tool()
async def fetch_subtitle(url: str) -> str:
    """Fetch subtitles for a single video URL.

    Uses a racing strategy: the platform-native API and yt-dlp run in parallel
    and the first successful CC result wins.  Falls back gracefully when no
    subtitles are available.

    Args:
        url: Full video URL (YouTube or Bilibili).

    Returns:
        JSON string with keys: title, platform, duration, upload_date,
        language, source ("cc" | "none"), subtitles (text or null),
        segments (list of {start, end, text} or null).
        audio_base64 is always stripped from the response.
    """
    result = await fetcher.fetch_subtitles_racing(url, return_audio=False)
    cleaned = _strip_audio(result)
    return json.dumps(cleaned, ensure_ascii=False, indent=2)


# ── Tool 3: list_channel_videos ─────────────────────────────────────────────
@mcp.tool()
async def list_channel_videos(
    channel_url: str,
    limit: int = 20,
) -> str:
    """List videos from a YouTube channel or Bilibili UP-主 space.

    Supported URL formats:
    - YouTube: https://www.youtube.com/@handle  or  /channel/<id>
    - Bilibili: https://space.bilibili.com/<uid>

    Args:
        channel_url: Channel / space URL.
        limit: Maximum number of videos to return (1-50). Defaults to 20.

    Returns:
        JSON string with keys: channel, platform, total_videos, video_urls.
    """
    result = await fetcher.fetch_channel_videos(channel_url, limit=limit)
    cleaned = _strip_audio(result)
    return json.dumps(cleaned, ensure_ascii=False, indent=2)


# ── Tool 4: batch_fetch_subtitles ───────────────────────────────────────────
@mcp.tool()
async def batch_fetch_subtitles(urls: list[str]) -> str:
    """Fetch subtitles for multiple video URLs concurrently.

    Processes up to 20 URLs with concurrency=3. Individual failures are
    reported per-item and do not abort the entire batch.

    Args:
        urls: List of video URLs (YouTube and/or Bilibili).

    Returns:
        JSON string with keys: total, success, failed, results (list of
        per-video subtitle objects). audio_base64 is stripped from all items.
    """
    result = await fetcher.fetch_batch_subtitles(
        urls,
        concurrency=3,
        return_audio=False,
    )
    cleaned = _strip_audio(result)
    # Also strip from nested results list
    if isinstance(cleaned, dict) and "results" in cleaned:
        cleaned["results"] = [_strip_audio(r) for r in cleaned["results"]]
    return json.dumps(cleaned, ensure_ascii=False, indent=2)


# ── entry point ─────────────────────────────────────────────────────────────
def main():
    """Run the MCP server over stdio."""
    mcp.run()


if __name__ == "__main__":
    main()
