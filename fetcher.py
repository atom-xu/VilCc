"""Subtitle fetcher core logic using yt-dlp."""
import os
import re
import json
import uuid
import base64
import asyncio
import tempfile
import urllib.request
import ssl
from typing import Dict, Any, List, Optional
import yt_dlp

# Try to import bilibili_api, handle if not available
try:
    from bilibili_api import user
    BILIBILI_API_AVAILABLE = True
except ImportError:
    BILIBILI_API_AVAILABLE = False

# Bilibili SESSDATA for cookie-based authentication
BILIBILI_SESSDATA = os.getenv("BILIBILI_SESSDATA", "")

# Cookie file path
COOKIE_FILE_PATH = os.path.join(os.path.dirname(__file__), "cookies.txt")

# Language priority: zh-Hans -> zh -> zh-CN -> en -> any first
LANG_PRIORITY = ["zh-Hans", "zh", "zh-CN", "en"]

# Format priority: json3 -> vtt -> srt
FORMAT_PRIORITY = ["json3", "vtt", "srt"]


def download_audio(url: str) -> str:
    """
    Download audio from video URL.

    Args:
        url: Video URL

    Returns:
        Path to downloaded audio file
    """
    temp_dir = tempfile.mkdtemp(prefix="audio_")
    output_base = os.path.join(temp_dir, str(uuid.uuid4()))

    ydl_opts = {
        "format": "bestaudio[ext=m4a]/bestaudio",
        "outtmpl": output_base + ".%(ext)s",
        "quiet": True,
        "no_warnings": True,
        "nocheckcertificate": True,
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])

        # Find the downloaded file
        for f in os.listdir(temp_dir):
            if f.startswith(os.path.basename(output_base)):
                return os.path.join(temp_dir, f)

        raise ValueError("Audio download failed: file not found")
    except Exception as e:
        # Cleanup on failure
        try:
            for f in os.listdir(temp_dir):
                os.remove(os.path.join(temp_dir, f))
            os.rmdir(temp_dir)
        except:
            pass
        raise ValueError(f"Audio download failed: {str(e)}")


def detect_platform(url: str) -> str:
    """Detect video platform from URL."""
    if "bilibili.com" in url or "b23.tv" in url:
        return "bilibili"
    if "youtube.com" in url or "youtu.be" in url:
        return "youtube"
    return "unknown"


def parse_vtt(vtt_text: str) -> str:
    """Parse VTT format, remove timestamps and merge lines."""
    lines = vtt_text.strip().split("\n")
    result = []
    skip_patterns = [
        r"^WEBVTT",
        r"^Kind:",
        r"^Language:",
        r"^\d{2}:\d{2}:\d{2}",
        r"^\s*$",
        r"^<c[.\w]*>",
        r"</c>$",
    ]

    for line in lines:
        line = line.strip()
        if not line:
            continue
        should_skip = False
        for pattern in skip_patterns:
            if re.match(pattern, line):
                should_skip = True
                break
        if should_skip:
            continue
        line = re.sub(r"</?c[.\w]*>", "", line)
        result.append(line)

    return "\n".join(result)


def parse_srt(srt_text: str) -> str:
    """Parse SRT format, remove timestamps and merge lines."""
    lines = srt_text.strip().split("\n")
    result = []
    skip_patterns = [
        r"^\d+$",
        r"^\d{2}:\d{2}:\d{2}",
        r"^\s*$",
    ]

    for line in lines:
        line = line.strip()
        if not line:
            continue
        should_skip = False
        for pattern in skip_patterns:
            if re.match(pattern, line):
                should_skip = True
                break
        if should_skip:
            continue
        result.append(line)

    return "\n".join(result)


def parse_json3(json_text: str) -> str:
    """Parse YouTube JSON3 format."""
    try:
        data = json.loads(json_text)
        events = data.get("events", [])
        result = []
        for event in events:
            if "segs" in event:
                text = "".join(seg.get("utf8", "") for seg in event["segs"])
                if text.strip():
                    result.append(text.strip())
        return "\n".join(result)
    except:
        return ""


def select_language(subtitles: Dict, automatic_captions: Dict) -> tuple:
    """Select best language based on priority."""
    # First check manual subtitles
    for lang in LANG_PRIORITY:
        if lang in subtitles:
            return lang, False, subtitles[lang]

    # Then check automatic captions
    for lang in LANG_PRIORITY:
        if lang in automatic_captions:
            return lang, True, automatic_captions[lang]

    # Fallback to any available language
    if subtitles:
        lang = list(subtitles.keys())[0]
        return lang, False, subtitles[lang]
    if automatic_captions:
        lang = list(automatic_captions.keys())[0]
        return lang, True, automatic_captions[lang]

    return None, False, None


def select_format(subtitle_data: List) -> tuple:
    """Select best format based on priority."""
    if not subtitle_data:
        return None, None

    for fmt in FORMAT_PRIORITY:
        for item in subtitle_data:
            if item.get("ext") == fmt:
                return fmt, item.get("url")

    # Fallback to first available
    first = subtitle_data[0]
    return first.get("ext"), first.get("url")


def fetch_subtitles(url: str, return_audio: bool = True) -> Dict[str, Any]:
    """
    Fetch subtitles from video URL.

    Args:
        url: Video URL (YouTube or Bilibili)
        return_audio: Whether to return audio for videos without subtitles

    Returns:
        Dict with title, platform, duration, subtitles, language, source, audio_base64

    Raises:
        ValueError: no_subtitles if no subtitles available and ASR disabled/failed
        ValueError: fetch_failed if network error or invalid URL
    """
    platform = detect_platform(url)
    audio_path = None

    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "writesubtitles": True,
        "writeautomaticsub": True,
        "subtitleslangs": ["all"],
        "nocheckcertificate": True,
        "cookiesfrombrowser": None,
    }

    # Add Bilibili cookie if available
    if platform == "bilibili":
        ydl_opts["writesubtitles"] = False
        ydl_opts["writeautomaticsub"] = False
        if os.path.exists(COOKIE_FILE_PATH):
            ydl_opts["cookiefile"] = COOKIE_FILE_PATH
        else:
            ydl_opts["cookiesfrombrowser"] = ("chrome", None, None, None)

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as e:
        raise ValueError(f"fetch_failed: {str(e)}")

    title = info.get("title", "")
    duration = info.get("duration", 0)

    subtitles = info.get("subtitles", {})
    automatic_captions = info.get("automatic_captions", {})

    # Select best language
    lang, is_auto, subtitle_data = select_language(subtitles, automatic_captions)

    # If no CC subtitles, try Bilibili AI subtitle
    if lang is None and platform == "bilibili":
        try:
            import tempfile as tf
            import subprocess

            temp_dir = tf.mkdtemp(prefix="subtitle_")
            output_base = os.path.join(temp_dir, "subtitle")

            cmd = [
                "yt-dlp",
                "--cookies-from-browser", "chrome",
                "--skip-download",
                "--write-subs",
                "--sub-langs", "ai-zh",
                "--sub-format", "srt",
                "--output", output_base,
                url
            ]

            result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)

            srt_file = f"{output_base}.ai-zh.srt"
            if os.path.exists(srt_file):
                with open(srt_file, 'r', encoding='utf-8') as f:
                    srt_content = f.read()

                lines = srt_content.strip().split('\n')
                text_lines = []
                i = 0
                while i < len(lines):
                    line = lines[i].strip()
                    if line.isdigit():
                        i += 1
                        if i < len(lines) and '-->' in lines[i]:
                            i += 1
                        text_parts = []
                        while i < len(lines) and lines[i].strip():
                            text_parts.append(lines[i].strip())
                            i += 1
                        if text_parts:
                            text_lines.append(' '.join(text_parts))
                    else:
                        i += 1

                text = '\n'.join(text_lines)

                try:
                    os.remove(srt_file)
                    os.rmdir(temp_dir)
                except:
                    pass

                if text.strip():
                    return {
                        "title": title,
                        "platform": platform,
                        "duration": duration,
                        "subtitles": text,
                        "language": "zh",
                        "source": "cc",
                        "audio_base64": None,
                    }
        except Exception as e:
            pass

    # No subtitles found - handle audio return
    if lang is None:
        if not return_audio:
            return {
                "title": title,
                "platform": platform,
                "duration": duration,
                "subtitles": None,
                "language": None,
                "source": "none",
                "audio_base64": None,
            }

        # Download audio and return base64
        try:
            audio_path = download_audio(url)
            with open(audio_path, 'rb') as f:
                audio_base64 = base64.b64encode(f.read()).decode('utf-8')

            # Cleanup
            try:
                os.remove(audio_path)
                os.rmdir(os.path.dirname(audio_path))
            except:
                pass

            return {
                "title": title,
                "platform": platform,
                "duration": duration,
                "subtitles": None,
                "language": None,
                "source": "audio",
                "audio_base64": audio_base64,
            }
        except Exception as e:
            # Audio download failed, return none
            return {
                "title": title,
                "platform": platform,
                "duration": duration,
                "subtitles": None,
                "language": None,
                "source": "none",
                "audio_base64": None,
            }

    # Select best format
    fmt, subtitle_url = select_format(subtitle_data)

    if fmt is None:
        raise ValueError("no_subtitles")

    # Download and parse subtitle content
    try:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

        req = urllib.request.Request(subtitle_url)
        if platform == "bilibili" and BILIBILI_SESSDATA:
            req.add_header("Cookie", f"SESSDATA={BILIBILI_SESSDATA}")

        with urllib.request.urlopen(req, timeout=30, context=ctx) as response:
            content = response.read().decode("utf-8")
    except Exception as e:
        raise ValueError(f"fetch_failed: {str(e)}")

    # Parse based on format
    if fmt == "json3":
        text = parse_json3(content)
    elif fmt == "vtt":
        text = parse_vtt(content)
    elif fmt == "srt":
        text = parse_srt(content)
    else:
        text = content

    if not text.strip():
        raise ValueError("no_subtitles")

    return {
        "title": title,
        "platform": platform,
        "duration": duration,
        "subtitles": text,
        "language": lang,
        "source": "cc",
        "audio_base64": None,
    }


def fetch_subtitles_single(url: str, return_audio: bool = True) -> Dict[str, Any]:
    """
    Fetch subtitles for a single URL, returns unified format with status.
    """
    try:
        result = fetch_subtitles(url, return_audio=return_audio)
        return {
            "url": url,
            "status": "success",
            **result
        }
    except ValueError as e:
        error_msg = str(e)
        if error_msg.startswith("no_subtitles"):
            return {
                "url": url,
                "status": "failed",
                "error": "no_subtitles",
                "message": "该视频没有可用字幕"
            }
        elif error_msg.startswith("fetch_failed"):
            return {
                "url": url,
                "status": "failed",
                "error": "fetch_failed",
                "message": "无法获取视频信息，请检查URL"
            }
        else:
            return {
                "url": url,
                "status": "failed",
                "error": "unknown",
                "message": error_msg
            }
    except Exception as e:
        return {
            "url": url,
            "status": "failed",
            "error": "fetch_failed",
            "message": f"无法获取视频信息，请检查URL: {str(e)}"
        }


async def fetch_subtitles_async(url: str, semaphore: asyncio.Semaphore, return_audio: bool = True) -> Dict[str, Any]:
    """Async wrapper for fetch_subtitles_single with semaphore control."""
    async with semaphore:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, fetch_subtitles_single, url, return_audio)


async def fetch_batch_subtitles(urls: List[str], concurrency: int = 3, return_audio: bool = True) -> Dict[str, Any]:
    """
    Fetch subtitles for multiple URLs concurrently.
    """
    semaphore = asyncio.Semaphore(concurrency)
    tasks = [fetch_subtitles_async(url, semaphore, return_audio) for url in urls]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    processed_results = []
    for url, result in zip(urls, results):
        if isinstance(result, Exception):
            processed_results.append({
                "url": url,
                "status": "failed",
                "error": "fetch_failed",
                "message": f"请求异常: {str(result)}"
            })
        else:
            processed_results.append(result)

    success_count = sum(1 for r in processed_results if r.get("status") == "success")
    failed_count = len(processed_results) - success_count

    return {
        "total": len(urls),
        "success": success_count,
        "failed": failed_count,
        "results": processed_results
    }


async def get_bilibili_video_list(uid: str, limit: int = 20) -> List[str]:
    """Fetch video list from Bilibili user."""
    if not BILIBILI_API_AVAILABLE:
        return []

    try:
        u = user.User(uid)
        videos = []
        pn = 1
        ps = min(limit, 30)

        while len(videos) < limit:
            resp = await u.get_videos(pn=pn, ps=ps)
            if not resp or "list" not in resp or "vlist" not in resp["list"]:
                break

            vlist = resp["list"]["vlist"]
            if not vlist:
                break

            for v in vlist:
                bvid = v.get("bvid")
                if bvid:
                    videos.append(f"https://www.bilibili.com/video/{bvid}")
                if len(videos) >= limit:
                    break

            page_info = resp.get("page", {})
            if pn >= page_info.get("pagecount", 1):
                break
            pn += 1

        return videos[:limit]
    except Exception as e:
        return []


async def fetch_channel_videos(channel_url: str, limit: int = 20) -> Dict[str, Any]:
    """Fetch video list from a channel/UP主 page."""
    platform = detect_platform(channel_url)

    is_channel_url = False
    if platform == "youtube":
        if "/@" in channel_url or "/channel/" in channel_url:
            is_channel_url = True
    elif platform == "bilibili":
        if "space.bilibili.com" in channel_url:
            is_channel_url = True

    if not is_channel_url:
        return {
            "error": "invalid_channel_url",
            "message": "不支持的频道URL格式"
        }

    if platform == "bilibili":
        import re
        uid_match = re.search(r'space\.bilibili\.com/(\d+)', channel_url)
        if not uid_match:
            return {
                "error": "invalid_channel_url",
                "message": "无法从URL中提取B站用户ID"
            }
        uid = int(uid_match.group(1))

        video_urls = await get_bilibili_video_list(uid, limit)

        if not video_urls:
            return {
                "error": "fetch_failed",
                "message": "无法获取B站视频列表"
            }

        return {
            "channel": f"UID{uid}",
            "platform": platform,
            "total_videos": len(video_urls),
            "video_urls": video_urls
        }

    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "extract_flat": True,
        "playlistend": limit,
        "nocheckcertificate": True,
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(channel_url, download=False)
    except Exception as e:
        return {
            "error": "fetch_failed",
            "message": f"无法获取频道信息: {str(e)}"
        }

    if not info:
        return {
            "error": "channel_not_found",
            "message": "无法获取频道信息"
        }

    entries = info.get("entries", [])

    if entries and isinstance(entries[0], dict):
        first_entry = entries[0]
        webpage_url = first_entry.get("webpage_url", "")
        if webpage_url and any(webpage_url.endswith(suffix) for suffix in ["/videos", "/streams", "/shorts"]):
            videos_url = channel_url.rstrip("/") + "/videos"
            try:
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(videos_url, download=False)
                    entries = info.get("entries", [])
            except Exception as e:
                return {
                    "error": "fetch_failed",
                    "message": f"无法获取频道视频: {str(e)}"
                }

    video_urls = []

    for entry in entries:
        if entry and isinstance(entry, dict):
            url = entry.get("url") or entry.get("webpage_url") or entry.get("id")
            if not url:
                continue

            if "/videos" in str(url) or "/streams" in str(url) or "/shorts" in str(url):
                continue

            if platform == "youtube":
                if not url.startswith("http"):
                    url = f"https://www.youtube.com/watch?v={url}"
                elif "youtube.com/" in url and "watch?v=" not in url:
                    if "/channel/" in url or "/c/" in url or "/@" in url or "/playlist" in url:
                        continue
            elif platform == "bilibili":
                if not url.startswith("http"):
                    url = f"https://www.bilibili.com/video/{url}"
                elif "bilibili.com" in url and "/video/" not in url:
                    if "/space/" in url:
                        continue

            video_urls.append(url)

    channel_name = info.get("title", "")
    if not channel_name and info.get("channel"):
        channel_name = info["channel"]

    return {
        "channel": channel_name,
        "platform": platform,
        "total_videos": len(video_urls),
        "video_urls": video_urls[:limit]
    }


def format_duration(seconds: int) -> str:
    """Format seconds to human readable duration."""
    if not seconds or seconds < 0:
        return "0秒"

    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60

    if hours > 0:
        return f"{hours}小时{minutes}分{secs}秒"
    elif minutes > 0:
        return f"{minutes}分{secs}秒"
    else:
        return f"{secs}秒"


def safe_filename(title: str) -> str:
    """Convert title to safe filename."""
    if not title:
        return "untitled"

    illegal_chars = r'[\/:*?"<>|]'
    safe = re.sub(illegal_chars, '_', title)
    safe = safe.strip('. ')

    if len(safe) > 80:
        safe = safe[:80].strip('. ')

    if not safe:
        safe = "untitled"

    return safe


def generate_md_content(item: Dict[str, Any], index: int) -> str:
    """Generate markdown content for a single video."""
    url = item.get("url", "")
    status = item.get("status", "failed")

    if status == "failed":
        error = item.get("error", "unknown")
        return f"""# 视频 {index}

- URL：{url}
- 状态：获取失败
- 原因：{error}

---

[获取失败：{error}]
"""

    title = item.get("title", "未知标题")
    platform = item.get("platform", "unknown")
    duration = item.get("duration", 0)
    source = item.get("source", "cc")
    language = item.get("language", "")
    subtitles = item.get("subtitles", "")

    source_map = {
        "cc": "CC字幕",
        "audio": "音频",
        "none": "无字幕"
    }
    source_cn = source_map.get(source, source)

    return f"""# {title}

- 平台：{platform}
- 时长：{format_duration(duration)}
- 字幕来源：{source_cn}
- 语言：{language}
- URL：{url}

---

{subtitles if subtitles else "[无字幕内容]"}
"""


def generate_txt_content(results: List[Dict[str, Any]]) -> str:
    """Generate combined txt content for all videos."""
    sections = []

    for i, item in enumerate(results, 1):
        url = item.get("url", "")
        status = item.get("status", "failed")
        title = item.get("title", "未知标题")

        if status == "failed":
            error = item.get("error", "unknown")
            section = f"""========================================
【{i}】{title}
URL：{url}
状态：获取失败
原因：{error}
========================================

[获取失败：{error}]
"""
        else:
            duration = item.get("duration", 0)
            source = item.get("source", "cc")
            subtitles = item.get("subtitles", "")

            source_map = {
                "cc": "CC字幕",
                "audio": "音频",
                "none": "无字幕"
            }
            source_cn = source_map.get(source, source)

            section = f"""========================================
【{i}】{title}
URL：{url}
时长：{format_duration(duration)}
来源：{source_cn}
========================================

{subtitles if subtitles else "[无字幕内容]"}
"""

        sections.append(section)

    return "\n\n\n".join(sections)


def create_zip_export(results: List[Dict[str, Any]], base_name: str = "subtitles") -> bytes:
    """Create ZIP archive with markdown files for each video."""
    import io
    import zipfile

    zip_buffer = io.BytesIO()

    with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zipf:
        for i, item in enumerate(results, 1):
            title = item.get("title", f"video_{i}")
            safe_title = safe_filename(title)
            filename = f"{i:03d}_{safe_title}.md"

            content = generate_md_content(item, i)

            zipf.writestr(filename, content.encode('utf-8'))

    zip_buffer.seek(0)
    return zip_buffer.getvalue()


def search_videos(query: str, platform: str = "youtube", limit: int = 10) -> List[Dict[str, Any]]:
    """Search videos on YouTube or Bilibili."""
    results = []

    if platform in ["youtube", "all"]:
        youtube_results = _search_platform(query, "youtube", limit)
        results.extend(youtube_results)

    if platform in ["bilibili", "all"]:
        bilibili_results = _search_platform(query, "bilibili", limit)
        results.extend(bilibili_results)

    return results


def _search_platform(query: str, platform: str, limit: int) -> List[Dict[str, Any]]:
    """Internal function to search on a specific platform."""
    if platform == "youtube":
        search_prefix = f"ytsearch{limit}:"
    else:
        search_prefix = f"bilisearch{limit}:"

    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "extract_flat": False,
        "nocheckcertificate": True,
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(f"{search_prefix}{query}", download=False)
    except Exception as e:
        return []

    if not info:
        return []

    entries = info.get("entries", [])
    results = []

    for entry in entries:
        if not entry:
            continue

        title = entry.get("title", "")
        url = entry.get("webpage_url") or entry.get("url", "")
        duration = entry.get("duration")
        view_count = entry.get("view_count")
        upload_date = entry.get("upload_date")
        channel = entry.get("channel") or entry.get("uploader", "")

        if upload_date and len(str(upload_date)) == 8:
            try:
                upload_date = f"{str(upload_date)[:4]}-{str(upload_date)[4:6]}-{str(upload_date)[6:8]}"
            except:
                pass

        if url and not url.startswith("http"):
            if platform == "youtube":
                url = f"https://www.youtube.com/watch?v={url}"
            else:
                url = f"https://www.bilibili.com/video/{url}"

        results.append({
            "title": title,
            "url": url,
            "platform": platform,
            "duration": duration,
            "view_count": view_count,
            "upload_date": upload_date,
            "channel": channel
        })

    return results
