"""Subtitle fetcher core logic using yt-dlp."""
import os
import re
import json
import uuid
import asyncio
import tempfile
import urllib.request
import ssl
from typing import Dict, Any, List
import yt_dlp

# Try to import dashscope, handle if not available
try:
    import dashscope
    DASHSCOPE_AVAILABLE = True
except ImportError:
    DASHSCOPE_AVAILABLE = False

# Try to import bilibili_api, handle if not available
try:
    from bilibili_api import user
    BILIBILI_API_AVAILABLE = True
except ImportError:
    BILIBILI_API_AVAILABLE = False

# Initialize DashScope API key if available
if DASHSCOPE_AVAILABLE:
    dashscope.api_key = os.getenv("DASHSCOPE_API_KEY", "")

# Bilibili SESSDATA for cookie-based authentication
BILIBILI_SESSDATA = os.getenv("BILIBILI_SESSDATA", "")

# Cookie file path
COOKIE_FILE_PATH = os.path.join(os.path.dirname(__file__), "cookies.txt")

# Language priority: zh-Hans -> zh -> zh-CN -> en -> any first
LANG_PRIORITY = ["zh-Hans", "zh", "zh-CN", "en"]

# Format priority: json3 -> vtt -> srt
FORMAT_PRIORITY = ["json3", "vtt", "srt"]


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
        # Remove inline tags like <c.bg_transparent>
        line = re.sub(r"</?c[.\w]*>", "", line)
        result.append(line)

    return "\n".join(result)


def parse_srt(srt_text: str) -> str:
    """Parse SRT format, remove timestamps and merge lines."""
    lines = srt_text.strip().split("\n")
    result = []
    skip_patterns = [
        r"^\d+$",  # Sequence numbers
        r"^\d{2}:\d{2}:\d{2}",  # Timestamps
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
    """Parse YouTube json3 format."""
    try:
        data = json.loads(json_text)
        events = data.get("events", [])
        result = []
        for event in events:
            if "segs" in event:
                text_parts = [seg.get("utf8", "") for seg in event["segs"]]
                text = "".join(text_parts).strip()
                if text:
                    result.append(text)
        return "\n".join(result)
    except json.JSONDecodeError:
        return ""


def select_language(subtitles: Dict[str, Any], automatic_captions: Dict[str, Any]) -> tuple:
    """
    Select best available language based on priority.
    Returns (language_code, is_automatic, subtitle_data).
    """
    # First check manual subtitles
    for lang in LANG_PRIORITY:
        if lang in subtitles:
            return lang, False, subtitles[lang]

    # Then check automatic captions
    for lang in LANG_PRIORITY:
        if lang in automatic_captions:
            return lang, True, automatic_captions[lang]

    # Fallback: any manual subtitle
    if subtitles:
        lang = list(subtitles.keys())[0]
        return lang, False, subtitles[lang]

    # Fallback: any automatic caption
    if automatic_captions:
        lang = list(automatic_captions.keys())[0]
        return lang, True, automatic_captions[lang]

    return None, None, None


def select_format(subtitle_data: list) -> tuple:
    """
    Select best available format based on priority.
    Returns (format_name, format_url).
    """
    formats_dict = {item.get("ext", ""): item.get("url", "") for item in subtitle_data}

    for fmt in FORMAT_PRIORITY:
        if fmt in formats_dict:
            return fmt, formats_dict[fmt]

    # Fallback to first available
    if formats_dict:
        fmt = list(formats_dict.keys())[0]
        return fmt, formats_dict[fmt]

    return None, None


def download_audio(url: str) -> str:
    """
    Download audio from video URL using yt-dlp.

    Args:
        url: Video URL

    Returns:
        Local audio file path

    Raises:
        ValueError: download_failed if download fails
    """
    platform = detect_platform(url)

    # Create temp directory with uuid
    temp_dir = tempfile.mkdtemp(prefix="subtitle_fetcher_")
    audio_path = os.path.join(temp_dir, f"{uuid.uuid4().hex}.m4a")

    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "format": "bestaudio[ext=m4a]/bestaudio",
        "outtmpl": audio_path,
        "nocheckcertificate": True,
        "cookiesfrombrowser": None,
    }

    # Add Bilibili cookie if available
    if platform == "bilibili" and os.path.exists(COOKIE_FILE_PATH):
        ydl_opts["cookiefile"] = COOKIE_FILE_PATH

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
    except Exception as e:
        # Clean up temp dir on failure
        try:
            os.remove(audio_path)
            os.rmdir(temp_dir)
        except:
            pass
        raise ValueError(f"download_failed: {str(e)}")

    # Check if file exists
    if not os.path.exists(audio_path):
        try:
            os.rmdir(temp_dir)
        except:
            pass
        raise ValueError("download_failed: Audio file not created")

    return audio_path


def transcribe_audio(file_path: str) -> str:
    """
    Transcribe audio file using DashScope ASR.

    Args:
        file_path: Local audio file path

    Returns:
        Transcribed text

    Raises:
        ValueError: asr_failed if transcription fails
    """
    if not DASHSCOPE_AVAILABLE:
        raise ValueError("asr_failed: DashScope not installed")

    if not dashscope.api_key:
        raise ValueError("asr_failed: DASHSCOPE_API_KEY not set")

    try:
        # Use paraformer-v2 for better Chinese recognition
        from dashscope.audio.asr import Recognition

        recognition = Recognition(
            model="paraformer-v2",
            format="m4a",
            sample_rate=16000,
        )

        result = recognition.call(file_path)

        # Parse result
        if result.status_code == 200:
            sentences = result.output.get("sentences", [])
            text_parts = []
            for sentence in sentences:
                text = sentence.get("text", "").strip()
                if text:
                    text_parts.append(text)
            return "\n".join(text_parts)
        else:
            raise ValueError(f"asr_failed: {result.message}")

    except Exception as e:
        raise ValueError(f"asr_failed: {str(e)}")


def fetch_subtitles(url: str, use_asr: bool = True) -> Dict[str, Any]:
    """
    Fetch subtitles from video URL.

    Args:
        url: Video URL (YouTube or Bilibili)
        use_asr: Whether to use ASR as fallback for Bilibili videos (default True)

    Returns:
        Dict with title, platform, duration, subtitles, language, source

    Raises:
        ValueError: no_subtitles if no subtitles available and ASR disabled/failed
        ValueError: fetch_failed if network error or invalid URL
        ValueError: asr_failed if ASR fails
    """
    platform = detect_platform(url)

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
        # Disable subtitle options for Bilibili to avoid 412 errors
        ydl_opts["writesubtitles"] = False
        ydl_opts["writeautomaticsub"] = False
        if os.path.exists(COOKIE_FILE_PATH):
            ydl_opts["cookiefile"] = COOKIE_FILE_PATH
        else:
            # Try to use browser cookies on macOS
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

    # If no CC subtitles, try Bilibili AI subtitle or ASR
    if lang is None:
        # For Bilibili, try to download AI subtitle first
        if platform == "bilibili":
            try:
                import tempfile
                import subprocess

                # Create temp directory for subtitle download
                temp_dir = tempfile.mkdtemp(prefix="subtitle_")
                output_base = os.path.join(temp_dir, "subtitle")

                # Use yt-dlp to download AI subtitle
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

                # Find and read the downloaded subtitle file
                srt_file = f"{output_base}.ai-zh.srt"
                if os.path.exists(srt_file):
                    with open(srt_file, 'r', encoding='utf-8') as f:
                        srt_content = f.read()

                    # Parse SRT to plain text
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

                    # Clean up
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
                            "source": "ai_subtitle",
                        }
            except Exception as e:
                # AI subtitle download failed, continue to ASR
                pass

        # Try ASR if AI subtitle not available
        if use_asr and platform == "bilibili" and DASHSCOPE_AVAILABLE:
            audio_path = None
            try:
                # Download audio
                audio_path = download_audio(url)
                # Transcribe
                text = transcribe_audio(audio_path)

                if text.strip():
                    return {
                        "title": title,
                        "platform": platform,
                        "duration": duration,
                        "subtitles": text,
                        "language": "zh",  # ASR mainly for Chinese Bilibili
                        "source": "asr",
                    }
                else:
                    raise ValueError("asr_failed: Empty transcription result")
            except ValueError:
                raise
            except Exception as e:
                raise ValueError(f"asr_failed: {str(e)}")
            finally:
                # Always clean up audio file
                if audio_path and os.path.exists(audio_path):
                    try:
                        os.remove(audio_path)
                        # Try to remove temp dir
                        temp_dir = os.path.dirname(audio_path)
                        os.rmdir(temp_dir)
                    except:
                        pass
        else:
            raise ValueError("no_subtitles")

    # Select best format
    fmt, subtitle_url = select_format(subtitle_data)

    if fmt is None:
        raise ValueError("no_subtitles")

    # Download and parse subtitle content
    try:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

        # Create request with headers
        req = urllib.request.Request(subtitle_url)
        # Add Bilibili cookie if available
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
    }


def fetch_subtitles_single(url: str, use_asr: bool = True) -> Dict[str, Any]:
    """
    Fetch subtitles for a single URL, returns unified format with status.

    Args:
        url: Video URL
        use_asr: Whether to use ASR as fallback

    Returns:
        Dict with url, status (success/failed), and either subtitle data or error info
    """
    try:
        result = fetch_subtitles(url, use_asr=use_asr)
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
        elif error_msg.startswith("asr_failed"):
            return {
                "url": url,
                "status": "failed",
                "error": "asr_failed",
                "message": f"ASR识别失败: {error_msg[10:]}"
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


async def fetch_subtitles_async(url: str, semaphore: asyncio.Semaphore, use_asr: bool = True) -> Dict[str, Any]:
    """Async wrapper for fetch_subtitles_single with semaphore control."""
    async with semaphore:
        # Run blocking operations in thread pool
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, fetch_subtitles_single, url, use_asr)


async def fetch_batch_subtitles(urls: List[str], concurrency: int = 3, use_asr: bool = True) -> Dict[str, Any]:
    """
    Fetch subtitles for multiple URLs concurrently.

    Args:
        urls: List of video URLs
        concurrency: Max concurrent requests (default 3, max 5)
        use_asr: Whether to use ASR as fallback for Bilibili

    Returns:
        Dict with total, success, failed, and results list
    """
    semaphore = asyncio.Semaphore(concurrency)
    tasks = [fetch_subtitles_async(url, semaphore, use_asr) for url in urls]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    # Process results, convert exceptions to failed items
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
    """
    Fetch video list from Bilibili user using bilibili-api-python.

    Args:
        uid: Bilibili user ID (from space.bilibili.com/{uid})
        limit: Max videos to fetch

    Returns:
        List of video URLs
    """
    if not BILIBILI_API_AVAILABLE:
        return []

    try:
        u = user.User(uid)
        # Get video list (paginated, fetch pages up to limit)
        # pn = page number, ps = page size
        videos = []
        pn = 1  # page number starts from 1
        ps = min(limit, 30)  # max 30 per request

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

            # Check if there's more pages
            page_info = resp.get("page", {})
            if pn >= page_info.get("pagecount", 1):
                break
            pn += 1

        return videos[:limit]
    except Exception as e:
        return []


async def fetch_channel_videos(channel_url: str, limit: int = 20) -> Dict[str, Any]:
    """
    Fetch video list from a channel/UP主 page.

    Args:
        channel_url: YouTube channel URL (@handle or /channel/UCxxx) or Bilibili space URL
        limit: Max videos to fetch (default 20, max 50)

    Returns:
        Dict with channel info and video URLs list, or error dict
    """
    platform = detect_platform(channel_url)

    # Check if it's a channel/space URL
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

    # For Bilibili, use bilibili-api-python instead of yt-dlp
    if platform == "bilibili":
        # Extract uid from URL: space.bilibili.com/{uid}
        import re
        uid_match = re.search(r'space\.bilibili\.com/(\d+)', channel_url)
        if not uid_match:
            return {
                "error": "invalid_channel_url",
                "message": "无法从URL中提取B站用户ID"
            }
        uid = int(uid_match.group(1))

        # Get video list using bilibili-api
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

    # YouTube: use yt-dlp
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

    # Extract video URLs from entries
    entries = info.get("entries", [])

    # Check if we got tabs instead of videos (for @handle URLs)
    # If first entry has webpage_url ending in /videos, we got tabs not videos
    if entries and isinstance(entries[0], dict):
        first_entry = entries[0]
        webpage_url = first_entry.get("webpage_url", "")
        if webpage_url and any(webpage_url.endswith(suffix) for suffix in ["/videos", "/streams", "/shorts"]):
            # We got tabs, need to fetch /videos tab directly
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
            # Try to get the video URL
            url = entry.get("url") or entry.get("webpage_url") or entry.get("id")
            if not url:
                continue

            # Skip non-video pages
            if "/videos" in str(url) or "/streams" in str(url) or "/shorts" in str(url):
                continue

            # Ensure full URL
            if platform == "youtube":
                if not url.startswith("http"):
                    url = f"https://www.youtube.com/watch?v={url}"
                elif "youtube.com/" in url and "watch?v=" not in url:
                    # Skip channel pages, playlists, etc.
                    if "/channel/" in url or "/c/" in url or "/@" in url or "/playlist" in url:
                        continue
            elif platform == "bilibili":
                if not url.startswith("http"):
                    url = f"https://www.bilibili.com/video/{url}"
                elif "bilibili.com" in url and "/video/" not in url:
                    # Skip space pages, etc.
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


# ==================== Export Utilities ====================

def format_duration(seconds: int) -> str:
    """
    Format seconds to human readable duration.

    Args:
        seconds: Duration in seconds

    Returns:
        Formatted string like "14分23秒" or "1小时3分12秒"
    """
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
    """
    Convert title to safe filename.

    Args:
        title: Original video title

    Returns:
        Safe filename with illegal chars removed and length limited
    """
    if not title:
        return "untitled"

    # Remove/replace illegal filename characters
    illegal_chars = r'[\/:*?"<>|]'
    safe = re.sub(illegal_chars, '_', title)

    # Remove leading/trailing whitespace and dots
    safe = safe.strip('. ')

    # Limit length to 80 characters
    if len(safe) > 80:
        safe = safe[:80].strip('. ')

    # Ensure not empty
    if not safe:
        safe = "untitled"

    return safe


def generate_md_content(item: Dict[str, Any], index: int) -> str:
    """
    Generate markdown content for a single video.

    Args:
        item: Video result dict
        index: Video index (for ordering)

    Returns:
        Markdown formatted content
    """
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

    # Map source to Chinese
    source_map = {
        "cc": "CC字幕",
        "asr": "语音识别"
    }
    source_cn = source_map.get(source, source)

    return f"""# {title}

- 平台：{platform}
- 时长：{format_duration(duration)}
- 字幕来源：{source_cn}
- 语言：{language}
- URL：{url}

---

{subtitles}
"""


def generate_txt_content(results: List[Dict[str, Any]]) -> str:
    """
    Generate combined txt content for all videos.

    Args:
        results: List of video result dicts

    Returns:
        Combined txt content
    """
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
                "asr": "语音识别"
            }
            source_cn = source_map.get(source, source)

            section = f"""========================================
【{i}】{title}
URL：{url}
时长：{format_duration(duration)}
来源：{source_cn}
========================================

{subtitles}
"""

        sections.append(section)

    return "\n\n\n".join(sections)


def create_zip_export(results: List[Dict[str, Any]], base_name: str = "subtitles") -> bytes:
    """
    Create ZIP archive with markdown files for each video.

    Args:
        results: List of video result dicts
        base_name: Base name for the zip file

    Returns:
        ZIP file bytes
    """
    import io
    import zipfile

    zip_buffer = io.BytesIO()

    with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zipf:
        for i, item in enumerate(results, 1):
            # Generate filename
            title = item.get("title", f"video_{i}")
            safe_title = safe_filename(title)
            filename = f"{i:03d}_{safe_title}.md"

            # Generate content
            content = generate_md_content(item, i)

            # Write to zip
            zipf.writestr(filename, content.encode('utf-8'))

    zip_buffer.seek(0)
    return zip_buffer.getvalue()


# ==================== Search Functions ====================

def search_videos(query: str, platform: str = "youtube", limit: int = 10) -> List[Dict[str, Any]]:
    """
    Search videos on YouTube or Bilibili.

    Args:
        query: Search query string
        platform: "youtube", "bilibili", or "all"
        limit: Max results per platform (default 10, max 20)

    Returns:
        List of video info dicts
    """
    results = []

    if platform in ["youtube", "all"]:
        youtube_results = _search_platform(query, "youtube", limit)
        results.extend(youtube_results)

    if platform in ["bilibili", "all"]:
        bilibili_results = _search_platform(query, "bilibili", limit)
        results.extend(bilibili_results)

    return results


def _search_platform(query: str, platform: str, limit: int) -> List[Dict[str, Any]]:
    """
    Internal function to search on a specific platform.

    Args:
        query: Search query
        platform: "youtube" or "bilibili"
        limit: Max results

    Returns:
        List of video info dicts
    """
    # Build search query prefix
    if platform == "youtube":
        search_prefix = f"ytsearch{limit}:"
    else:  # bilibili
        search_prefix = f"bilisearch{limit}:"

    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "extract_flat": False,  # Get full info
        "nocheckcertificate": True,
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(f"{search_prefix}{query}", download=False)
    except Exception as e:
        # Return empty list on search failure
        return []

    if not info:
        return []

    # Extract entries
    entries = info.get("entries", [])
    results = []

    for entry in entries:
        if not entry:
            continue

        # Extract video info
        title = entry.get("title", "")
        url = entry.get("webpage_url") or entry.get("url", "")
        duration = entry.get("duration")
        view_count = entry.get("view_count")
        upload_date = entry.get("upload_date")
        channel = entry.get("channel") or entry.get("uploader", "")

        # Format upload_date from YYYYMMDD to YYYY-MM-DD
        if upload_date and len(str(upload_date)) == 8:
            try:
                upload_date = f"{str(upload_date)[:4]}-{str(upload_date)[4:6]}-{str(upload_date)[6:8]}"
            except:
                pass

        # Ensure full URL
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
