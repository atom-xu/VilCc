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
import time
import random
import webbrowser
from typing import Dict, Any, List, Optional, Callable
import yt_dlp

# Try to import bilibili_api, handle if not available
try:
    from bilibili_api import user
    BILIBILI_API_AVAILABLE = True
except ImportError:
    BILIBILI_API_AVAILABLE = False

# Cookie file path
COOKIE_FILE_PATH = os.path.join(os.path.dirname(__file__), "cookies.txt")
ENV_FILE_PATH = os.path.join(os.path.dirname(__file__), ".env")

# Language priority: zh-Hans -> zh -> zh-CN -> en -> any first
LANG_PRIORITY = ["zh-Hans", "zh", "zh-CN", "en"]

# Format priority: json3 -> vtt -> srt
FORMAT_PRIORITY = ["json3", "vtt", "srt"]


def _load_env_file(path: str) -> Dict[str, str]:
    """Load key-value pairs from .env file."""
    env = {}
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    k, v = line.split("=", 1)
                    env[k.strip()] = v.strip()
    return env


def _save_env_file(path: str, env: Dict[str, str]):
    """Save key-value pairs to .env file, preserving comments if possible."""
    lines = []
    existing_keys = set()

    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                stripped = line.strip()
                if not stripped or stripped.startswith("#"):
                    lines.append(line.rstrip("\n"))
                    continue
                if "=" in stripped:
                    k = stripped.split("=", 1)[0].strip()
                    if k in env:
                        lines.append(f"{k}={env[k]}")
                        existing_keys.add(k)
                    else:
                        lines.append(line.rstrip("\n"))

    for k, v in env.items():
        if k not in existing_keys:
            lines.append(f"{k}={v}")

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def extract_bili_cookies_from_browser(browser: str = "chrome") -> Dict[str, str]:
    """
    Extract Bilibili cookies (SESSDATA, bili_jct, buvid3) from browser using yt-dlp.
    Returns a dict of cookie names to values.
    """
    ydl_opts = {
        "cookiesfrombrowser": (browser, None, None, None),
        "quiet": True,
        "no_warnings": True,
    }
    cookies = {}
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        cookiejar = getattr(ydl, "cookiejar", None)
        if cookiejar:
            for cookie in cookiejar:
                if "bilibili.com" in cookie.domain and cookie.name in (
                    "SESSDATA", "bili_jct", "buvid3", "DedeUserID"
                ):
                    cookies[cookie.name] = cookie.value
    return cookies


def write_cookies_txt(cookies: Dict[str, str], path: str):
    """Write Netscape-format cookies.txt for yt-dlp."""
    lines = ["# Netscape HTTP Cookie File"]
    # Default expiry ~1 year from now
    expiry = str(int(time.time()) + 365 * 24 * 3600)
    for name, value in cookies.items():
        lines.append(f".bilibili.com\tTRUE\t/\tFALSE\t{expiry}\t{name}\t{value}")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


# Initialize Bilibili SESSDATA from environment or .env file
BILIBILI_SESSDATA = os.getenv("BILIBILI_SESSDATA", "")
if not BILIBILI_SESSDATA and os.path.exists(ENV_FILE_PATH):
    _env = _load_env_file(ENV_FILE_PATH)
    BILIBILI_SESSDATA = _env.get("BILIBILI_SESSDATA", "")
    if BILIBILI_SESSDATA:
        os.environ["BILIBILI_SESSDATA"] = BILIBILI_SESSDATA


def has_bilibili_cookies() -> bool:
    """Check if we already have Bilibili cookies available."""
    return bool(BILIBILI_SESSDATA) or os.path.exists(COOKIE_FILE_PATH)


def ensure_bilibili_cookies(interactive: bool = True) -> bool:
    """
    Ensure Bilibili cookies are available.
    If missing and interactive=True, open browser, wait for user login,
    then auto-extract cookies from Chrome and persist them.
    Returns True if cookies are available.
    """
    global BILIBILI_SESSDATA

    # Already have cookies
    if has_bilibili_cookies():
        # Also sync SESSDATA from cookies.txt if env is empty
        if not BILIBILI_SESSDATA and os.path.exists(COOKIE_FILE_PATH):
            with open(COOKIE_FILE_PATH, "r", encoding="utf-8") as f:
                for line in f:
                    parts = line.strip().split("\t")
                    if len(parts) >= 7 and parts[5] == "SESSDATA":
                        BILIBILI_SESSDATA = parts[6]
                        os.environ["BILIBILI_SESSDATA"] = BILIBILI_SESSDATA
                        break
        return True

    if not interactive:
        print("[Cookie] 未找到 B 站登录凭证，非交互模式跳过自动获取")
        return False

    print("\n" + "=" * 50)
    print("未检测到 B 站登录凭证（cookies.txt 或 BILIBILI_SESSDATA）")
    print("正在打开 Bilibili 登录页面，请登录后返回终端按回车继续...")
    print("=" * 50)

    try:
        webbrowser.open("https://www.bilibili.com")
    except Exception as e:
        print(f"打开浏览器失败: {e}")
        print("请手动访问 https://www.bilibili.com 登录")

    try:
        input("登录完成后请按回车键继续...")
    except EOFError:
        print("[Cookie] 无法读取输入，跳过自动获取")
        return False

    print("[Cookie] 正在从 Chrome 提取 B 站 Cookie...")
    cookies = extract_bili_cookies_from_browser("chrome")

    if not cookies.get("SESSDATA"):
        # Try Safari fallback on macOS
        print("[Cookie] Chrome 中未找到 SESSDATA，尝试 Safari...")
        cookies = extract_bili_cookies_from_browser("safari")

    if not cookies.get("SESSDATA"):
        print("[Cookie] 仍未找到 SESSDATA，请确认已登录 B 站")
        print("或者手动设置环境变量: export BILIBILI_SESSDATA=xxx")
        return False

    # Persist cookies.txt
    write_cookies_txt(cookies, COOKIE_FILE_PATH)
    print(f"[Cookie] 已保存 cookies.txt -> {COOKIE_FILE_PATH}")

    # Persist .env
    sessdata = cookies.get("SESSDATA", "")
    BILIBILI_SESSDATA = sessdata
    os.environ["BILIBILI_SESSDATA"] = sessdata
    env = _load_env_file(ENV_FILE_PATH)
    env["BILIBILI_SESSDATA"] = sessdata
    _save_env_file(ENV_FILE_PATH, env)
    print(f"[Cookie] 已写入 .env: BILIBILI_SESSDATA={sessdata[:20]}...")
    print("=" * 50 + "\n")
    return True


def is_ratelimit_error(exc: Exception) -> bool:
    """Detect Bilibili/YouTube rate-limit or security-control errors."""
    msg = str(exc).lower()
    keywords = [
        "412", "429", "352", "too many requests", "security control",
        "风控", "precondition failed", "unavailable", "temporarily",
        "blocked", "rejected", "limit"
    ]
    return any(k in msg for k in keywords)


def retry_with_backoff(
    func: Callable,
    max_retries: int = 3,
    base_delay: float = 2.0,
    max_delay: float = 60.0,
    jitter: bool = True,
    on_retry: Optional[Callable[[int, Exception, float], None]] = None
):
    """
    Execute *func* with exponential backoff on rate-limit errors.
    Re-raises the last exception if all retries are exhausted.
    """
    last_exc = None
    for attempt in range(max_retries + 1):
        try:
            return func()
        except Exception as e:
            last_exc = e
            if attempt >= max_retries or not is_ratelimit_error(e):
                raise
            delay = min(base_delay * (2 ** attempt), max_delay)
            if jitter:
                delay = delay * (0.7 + random.random() * 0.6)
            if on_retry:
                on_retry(attempt + 1, e, delay)
            time.sleep(delay)
    raise last_exc  # pragma: no cover


async def async_retry_with_backoff(
    coro_func: Callable,
    max_retries: int = 3,
    base_delay: float = 2.0,
    max_delay: float = 60.0,
    jitter: bool = True,
    on_retry: Optional[Callable[[int, Exception, float], None]] = None
):
    """Async version of retry_with_backoff."""
    last_exc = None
    for attempt in range(max_retries + 1):
        try:
            return await coro_func()
        except Exception as e:
            last_exc = e
            if attempt >= max_retries or not is_ratelimit_error(e):
                raise
            delay = min(base_delay * (2 ** attempt), max_delay)
            if jitter:
                delay = delay * (0.7 + random.random() * 0.6)
            if on_retry:
                on_retry(attempt + 1, e, delay)
            await asyncio.sleep(delay)
    raise last_exc  # pragma: no cover


def transcribe_audio(audio_path: str) -> Optional[str]:
    """
    Transcribe audio to text using DashScope ASR (paraformer-v1).
    Returns the transcribed text, or None if ASR is unavailable/fails.
    """
    api_key = os.getenv("DASHSCOPE_API_KEY", "")
    if not api_key or api_key == "your_key_here":
        return None

    try:
        import dashscope
        from dashscope.audio.asr import Recognition
    except ImportError:
        return None

    dashscope.api_key = api_key

    # Determine format from file extension
    ext = os.path.splitext(audio_path)[1].lower().lstrip(".")
    if not ext:
        ext = "m4a"

    try:
        result = Recognition.call(
            model="paraformer-v1",
            audio=audio_path,
            sample_rate=16000,
            format=ext,
        )
        if result.status_code == 200:
            text = getattr(result.output, "text", "")
            if not text and isinstance(result.output, dict):
                text = result.output.get("text", "")
            return text.strip() if text else None
        else:
            print(f"[ASR] DashScope error {result.status_code}: {getattr(result, 'message', 'unknown')}")
            return None
    except Exception as e:
        print(f"[ASR] Exception: {e}")
        return None


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
        "socket_timeout": 30,
        "retries": 2,
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


def _parse_timestamp(ts: str) -> float:
    """Parse VTT/SRT timestamp string to seconds (float)."""
    ts = ts.strip().replace(",", ".")
    parts = ts.split(":")
    try:
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
        elif len(parts) == 2:
            return int(parts[0]) * 60 + float(parts[1])
        return float(ts)
    except Exception:
        return 0.0


def parse_vtt(vtt_text: str) -> tuple:
    """
    Parse VTT format.
    Returns (plain_text: str, segments: list[{start, end, text}]).
    """
    lines = vtt_text.strip().split("\n")
    text_parts = []
    segments = []
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if "-->" in line:
            # Timestamp line: "00:00:00.500 --> 00:00:02.300 align:start position:0%"
            arrow_parts = line.split("-->")
            start = _parse_timestamp(arrow_parts[0])
            end = _parse_timestamp(arrow_parts[1].split()[0])  # ignore position metadata
            i += 1
            chunk = []
            while i < len(lines) and lines[i].strip():
                t = lines[i].strip()
                t = re.sub(r"</?c[.\w]*>", "", t)
                t = re.sub(r"<\d{2}:\d{2}:\d{2}\.\d{3}>", "", t)
                t = t.strip()
                if t:
                    chunk.append(t)
                i += 1
            if chunk:
                text = " ".join(chunk)
                text_parts.append(text)
                segments.append({"start": round(start, 3), "end": round(end, 3), "text": text})
        else:
            i += 1
    return "\n".join(text_parts), segments


def parse_srt(srt_text: str) -> tuple:
    """
    Parse SRT format.
    Returns (plain_text: str, segments: list[{start, end, text}]).
    """
    lines = srt_text.strip().split("\n")
    text_parts = []
    segments = []
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if not line or line.isdigit():
            i += 1
            continue
        if "-->" in line:
            arrow_parts = line.split("-->")
            start = _parse_timestamp(arrow_parts[0])
            end = _parse_timestamp(arrow_parts[1])
            i += 1
            chunk = []
            while i < len(lines) and lines[i].strip():
                chunk.append(lines[i].strip())
                i += 1
            if chunk:
                text = " ".join(chunk)
                text_parts.append(text)
                segments.append({"start": round(start, 3), "end": round(end, 3), "text": text})
        else:
            i += 1
    return "\n".join(text_parts), segments


def parse_json3(json_text: str) -> tuple:
    """
    Parse YouTube JSON3 format.
    Returns (plain_text: str, segments: list[{start, end, text}]).
    """
    try:
        data = json.loads(json_text)
        events = data.get("events", [])
        text_parts = []
        segments = []
        for event in events:
            if "segs" in event:
                text = "".join(seg.get("utf8", "") for seg in event["segs"])
                text = text.strip()
                if text:
                    text_parts.append(text)
                    start_ms = event.get("tStartMs", 0)
                    dur_ms = event.get("dDurationMs", 0)
                    segments.append({
                        "start": round(start_ms / 1000, 3),
                        "end": round((start_ms + dur_ms) / 1000, 3),
                        "text": text,
                    })
        return "\n".join(text_parts), segments
    except Exception:
        return "", []


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


def _extract_youtube_id(url: str) -> Optional[str]:
    """Extract YouTube video ID from URL."""
    for pattern in [
        r'(?:youtube\.com/watch\?(?:.*&)?v=|youtu\.be/|youtube\.com/shorts/)([a-zA-Z0-9_-]{11})',
        r'youtube\.com/embed/([a-zA-Z0-9_-]{11})',
    ]:
        m = re.search(pattern, url)
        if m:
            return m.group(1)
    return None


def _extract_bvid(url: str) -> Optional[str]:
    """Extract Bilibili BV/AV ID from URL. Returns None for b23.tv short URLs."""
    m = re.search(r'bilibili\.com/video/(BV[a-zA-Z0-9]+)', url)
    if m:
        return m.group(1)
    m = re.search(r'bilibili\.com/video/av(\d+)', url, re.IGNORECASE)
    if m:
        return f"av{m.group(1)}"
    return None


def _fetch_youtube_fast(url: str) -> Optional[Dict[str, Any]]:
    """
    Fast path: fetch YouTube transcript via youtube-transcript-api (v1.x).
    Skips yt-dlp entirely — roughly 5-10x faster, far less likely to hit rate limits.
    Returns None on any failure so the caller can fall back to yt-dlp.
    """
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
    except ImportError:
        return None

    video_id = _extract_youtube_id(url)
    if not video_id:
        return None

    try:
        api = YouTubeTranscriptApi()
        transcript_list = api.list(video_id)
        transcript = None

        for find_method in ("find_manually_created_transcript", "find_generated_transcript"):
            if transcript:
                break
            for lang in LANG_PRIORITY:
                try:
                    transcript = getattr(transcript_list, find_method)([lang])
                    break
                except Exception:
                    continue

        if not transcript:
            transcript = next(iter(transcript_list), None)
        if not transcript:
            return None

        fetched = transcript.fetch()
        if not fetched:
            return None

        segments = []
        text_parts = []
        for s in fetched:
            t = s.text.strip()
            if t:
                text_parts.append(t)
                segments.append({
                    "start": round(s.start, 3),
                    "end": round(s.start + s.duration, 3),
                    "text": t,
                })
        if not text_parts:
            return None

        duration = 0
        if segments:
            duration = segments[-1]["end"]

        # Title via oEmbed — no API key, no rate limit
        title = video_id
        try:
            import urllib.parse as _up
            oembed_url = f"https://www.youtube.com/oembed?url={_up.quote(url)}&format=json"
            _ctx = ssl.create_default_context()
            _ctx.check_hostname = False
            _ctx.verify_mode = ssl.CERT_NONE
            req = urllib.request.Request(oembed_url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=10, context=_ctx) as resp:
                title = json.loads(resp.read()).get("title", video_id)
        except Exception:
            pass

        return {
            "title": title,
            "platform": "youtube",
            "duration": duration,
            "upload_date": "",  # oEmbed doesn't expose publish date; yt-dlp fallback will fill it
            "subtitles": "\n".join(text_parts),
            "segments": segments,
            "language": fetched.language_code,
            "source": "cc",
            "audio_base64": None,
        }

    except Exception:
        return None


def _fetch_bilibili_fast(url: str) -> Optional[Dict[str, Any]]:
    """
    Fast path: fetch Bilibili subtitle via direct API (no yt-dlp).
    2 API calls instead of yt-dlp's full page extraction — much faster and lower risk.
    Returns None on any failure so the caller can fall back to yt-dlp.
    """
    bvid = _extract_bvid(url)
    if not bvid:
        return None

    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Referer": "https://www.bilibili.com",
    }
    if BILIBILI_SESSDATA:
        headers["Cookie"] = f"SESSDATA={BILIBILI_SESSDATA}"

    _ctx = ssl.create_default_context()
    _ctx.check_hostname = False
    _ctx.verify_mode = ssl.CERT_NONE

    try:
        # Step 1: video info → title, duration, cid
        if bvid.lower().startswith("av"):
            view_url = f"https://api.bilibili.com/x/web-interface/view?aid={bvid[2:]}"
        else:
            view_url = f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}"

        req = urllib.request.Request(view_url, headers=headers)
        with urllib.request.urlopen(req, timeout=10, context=_ctx) as resp:
            view_data = json.loads(resp.read())

        if view_data.get("code") != 0:
            return None

        info = view_data["data"]
        title = info.get("title", "")
        duration = info.get("duration", 0)
        cid = info.get("cid", 0)
        # pubdate is Unix timestamp → convert to YYYYMMDD string for consistency
        pubdate_ts = info.get("pubdate", 0)
        upload_date = ""
        if pubdate_ts:
            import datetime
            upload_date = datetime.datetime.fromtimestamp(pubdate_ts).strftime("%Y%m%d")
        if not cid:
            return None

        # Step 2: player API → subtitle list
        player_url = f"https://api.bilibili.com/x/player/v2?bvid={bvid}&cid={cid}"
        req = urllib.request.Request(player_url, headers=headers)
        with urllib.request.urlopen(req, timeout=10, context=_ctx) as resp:
            player_data = json.loads(resp.read())

        if player_data.get("code") != 0:
            return None

        subtitle_list = player_data["data"].get("subtitle", {}).get("subtitles", [])
        if not subtitle_list:
            return None  # no CC — let yt-dlp handle audio/ASR fallback

        subtitle = None
        for lang_key in ["zh-Hans", "zh-CN", "zh"]:
            for s in subtitle_list:
                if lang_key in s.get("lan", ""):
                    subtitle = s
                    break
            if subtitle:
                break
        if not subtitle:
            subtitle = subtitle_list[0]

        # Step 3: download subtitle JSON
        subtitle_url = subtitle["subtitle_url"]
        if subtitle_url.startswith("//"):
            subtitle_url = "https:" + subtitle_url

        req = urllib.request.Request(subtitle_url, headers=headers)
        with urllib.request.urlopen(req, timeout=15, context=_ctx) as resp:
            content = json.loads(resp.read())

        body = content.get("body", [])
        segments = []
        text_parts = []
        for item in body:
            t = item.get("content", "").strip()
            if t:
                text_parts.append(t)
                segments.append({
                    "start": round(float(item.get("from", 0)), 3),
                    "end": round(float(item.get("to", 0)), 3),
                    "text": t,
                })
        if not text_parts:
            return None

        return {
            "title": title,
            "platform": "bilibili",
            "duration": duration,
            "upload_date": upload_date,
            "subtitles": "\n".join(text_parts),
            "segments": segments,
            "language": subtitle.get("lan", "zh"),
            "source": "cc",
            "audio_base64": None,
        }

    except Exception:
        return None


def _fetch_cc_only_ytdlp(url: str) -> Optional[Dict[str, Any]]:
    """
    yt-dlp CC-only fetch — no audio download, no ASR.
    Uses short timeouts and 1 retry to fail fast in a race context.
    Always returns a dict (subtitles=None when no CC found, so caller gets title/duration).
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
        "socket_timeout": 20,
        "retries": 1,
    }

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
    except Exception:
        return None

    title = info.get("title", "")
    duration = info.get("duration", 0)
    upload_date = info.get("upload_date", "")  # YYYYMMDD from yt-dlp
    subtitles = info.get("subtitles", {})
    automatic_captions = info.get("automatic_captions", {})
    lang, _, subtitle_data = select_language(subtitles, automatic_captions)

    # Bilibili: also try AI subtitle via subprocess
    if lang is None and platform == "bilibili":
        try:
            import subprocess, tempfile as tf
            temp_dir = tf.mkdtemp(prefix="subtitle_")
            output_base = os.path.join(temp_dir, "subtitle")
            cmd = ["yt-dlp", "--cookies-from-browser", "chrome", "--skip-download",
                   "--write-subs", "--sub-langs", "ai-zh", "--sub-format", "srt",
                   "--output", output_base, url]
            subprocess.run(cmd, capture_output=True, text=True, timeout=25)
            srt_file = f"{output_base}.ai-zh.srt"
            if os.path.exists(srt_file):
                with open(srt_file, "r", encoding="utf-8") as f:
                    text, segs = parse_srt(f.read())
                try:
                    os.remove(srt_file)
                    os.rmdir(temp_dir)
                except Exception:
                    pass
                if text.strip():
                    return {"title": title, "platform": platform, "duration": duration,
                            "upload_date": upload_date, "subtitles": text, "segments": segs, "language": "zh", "source": "cc", "audio_base64": None}
        except Exception:
            pass

    _meta = {"title": title, "platform": platform, "duration": duration, "upload_date": upload_date}

    if lang is None:
        return {**_meta, "subtitles": None, "segments": [], "language": None, "source": "none", "audio_base64": None}

    fmt, subtitle_url = select_format(subtitle_data)
    if not fmt:
        return {**_meta, "subtitles": None, "segments": [], "language": None, "source": "none", "audio_base64": None}

    try:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        req = urllib.request.Request(subtitle_url)
        if platform == "bilibili" and BILIBILI_SESSDATA:
            req.add_header("Cookie", f"SESSDATA={BILIBILI_SESSDATA}")
        with urllib.request.urlopen(req, timeout=15, context=ctx) as resp:
            content = resp.read().decode("utf-8")
    except Exception:
        return {**_meta, "subtitles": None, "segments": [], "language": None, "source": "none", "audio_base64": None}

    if fmt == "json3":
        text, segs = parse_json3(content)
    elif fmt == "vtt":
        text, segs = parse_vtt(content)
    elif fmt == "srt":
        text, segs = parse_srt(content)
    else:
        text, segs = content, []

    if not text.strip():
        return {**_meta, "subtitles": None, "segments": [], "language": None, "source": "none", "audio_base64": None}

    return {**_meta, "subtitles": text, "segments": segs, "language": lang, "source": "cc", "audio_base64": None}


async def fetch_subtitles_racing(url: str, return_audio: bool = True, use_asr: bool = False) -> Dict[str, Any]:
    """
    Race fast path (platform-native API) vs yt-dlp simultaneously.
    Whoever gets CC subtitles first wins; the other is cancelled immediately.

    This eliminates the "stuck waiting" problem: if fast path hangs,
    yt-dlp is already running and will return a result — and vice versa.
    Fallback to audio/ASR only runs after both CC paths have given up.
    """
    platform = detect_platform(url)
    if platform == "bilibili":
        ensure_bilibili_cookies(interactive=False)

    loop = asyncio.get_event_loop()

    async def run_fast():
        try:
            if platform not in ("youtube", "bilibili"):
                return None
            fn = _fetch_youtube_fast if platform == "youtube" else _fetch_bilibili_fast
            return await asyncio.wait_for(
                loop.run_in_executor(None, fn, url),
                timeout=6.0
            )
        except Exception:
            return None

    async def run_ytdlp():
        # 0.3s head start for fast path; if fast path is already done by then, no wasted work
        await asyncio.sleep(0.3)
        try:
            return await asyncio.wait_for(
                loop.run_in_executor(None, _fetch_cc_only_ytdlp, url),
                timeout=40.0
            )
        except Exception:
            return None

    fast_task = asyncio.create_task(run_fast())
    ytdlp_task = asyncio.create_task(run_ytdlp())

    cc_result = None
    metadata_hint = None  # title/duration from yt-dlp even when no CC found
    pending = {fast_task, ytdlp_task}

    try:
        while pending and cc_result is None:
            done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                try:
                    r = task.result()
                    if r and r.get("subtitles"):
                        cc_result = r
                        break
                    elif r and r.get("title") and not metadata_hint:
                        metadata_hint = r  # save for audio/ASR fallback metadata
                except Exception:
                    pass
    finally:
        for t in (fast_task, ytdlp_task):
            if not t.done():
                t.cancel()

    if cc_result:
        return cc_result

    # Neither found CC subtitles — audio / ASR / none fallback
    meta_title = (metadata_hint or {}).get("title", "")
    meta_duration = (metadata_hint or {}).get("duration", 0)

    if use_asr:
        try:
            audio_path = await asyncio.wait_for(
                loop.run_in_executor(None, download_audio, url),
                timeout=120.0
            )
            asr_text = transcribe_audio(audio_path)
            try:
                os.remove(audio_path)
                os.rmdir(os.path.dirname(audio_path))
            except Exception:
                pass
            if asr_text:
                return {"title": meta_title, "platform": platform, "duration": meta_duration,
                        "subtitles": asr_text, "language": "zh", "source": "asr", "audio_base64": None}
        except Exception:
            pass

    if not return_audio:
        return {"title": meta_title, "platform": platform, "duration": meta_duration,
                "subtitles": None, "language": None, "source": "none", "audio_base64": None}

    try:
        audio_path = await asyncio.wait_for(
            loop.run_in_executor(None, download_audio, url),
            timeout=120.0
        )
        with open(audio_path, "rb") as f:
            audio_b64 = base64.b64encode(f.read()).decode("utf-8")
        try:
            os.remove(audio_path)
            os.rmdir(os.path.dirname(audio_path))
        except Exception:
            pass
        return {"title": meta_title, "platform": platform, "duration": meta_duration,
                "subtitles": None, "language": None, "source": "audio", "audio_base64": audio_b64}
    except Exception:
        pass

    raise ValueError("fetch_failed: unable to fetch subtitles or audio")


def fetch_subtitles(url: str, return_audio: bool = True, use_asr: bool = False) -> Dict[str, Any]:
    """
    Fetch subtitles from video URL.

    Args:
        url: Video URL (YouTube or Bilibili)
        return_audio: Whether to return audio for videos without subtitles
        use_asr: Whether to use DashScope ASR when no subtitles exist

    Returns:
        Dict with title, platform, duration, subtitles, language, source, audio_base64

    Raises:
        ValueError: no_subtitles if no subtitles available and ASR disabled/failed
        ValueError: fetch_failed if network error or invalid URL
    """
    platform = detect_platform(url)

    if platform == "bilibili":
        ensure_bilibili_cookies(interactive=False)

    # Fast path: try lightweight direct API before yt-dlp
    try:
        if platform == "youtube":
            _fast = _fetch_youtube_fast(url)
        elif platform == "bilibili":
            _fast = _fetch_bilibili_fast(url)
        else:
            _fast = None
        if _fast and _fast.get("subtitles"):
            return _fast
    except Exception:
        pass

    def _fetch():
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
            # REQUIRED: without socket_timeout, yt-dlp hangs indefinitely on slow servers.
            # All yt-dlp YoutubeDL option dicts in this file must include these two keys.
            "socket_timeout": 30,
            "retries": 2,
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
        upload_date = info.get("upload_date", "")  # YYYYMMDD from yt-dlp

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

        # No subtitles found - handle ASR or audio return
        if lang is None:
            # Try ASR first if enabled
            if use_asr:
                try:
                    audio_path = download_audio(url)
                    asr_text = transcribe_audio(audio_path)
                    # Cleanup
                    try:
                        os.remove(audio_path)
                        os.rmdir(os.path.dirname(audio_path))
                    except:
                        pass

                    if asr_text:
                        return {
                            "title": title,
                            "platform": platform,
                            "duration": duration,
                            "subtitles": asr_text,
                            "language": "zh",
                            "source": "asr",
                            "audio_base64": None,
                        }
                except Exception as e:
                    pass

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
            text, segs = parse_json3(content)
        elif fmt == "vtt":
            text, segs = parse_vtt(content)
        elif fmt == "srt":
            text, segs = parse_srt(content)
        else:
            text, segs = content, []

        if not text.strip():
            raise ValueError("no_subtitles")

        return {
            "title": title,
            "platform": platform,
            "duration": duration,
            "upload_date": upload_date,
            "subtitles": text,
            "segments": segs,
            "language": lang,
            "source": "cc",
            "audio_base64": None,
        }

    return retry_with_backoff(
        _fetch,
        max_retries=3,
        base_delay=2.0,
        on_retry=lambda attempt, exc, delay: print(
            f"[B站风控重试] {url} 第{attempt}次重试，等待{delay:.1f}s: {exc}"
        )
    )


def fetch_subtitles_single(url: str, return_audio: bool = True, use_asr: bool = False) -> Dict[str, Any]:
    """
    Fetch subtitles for a single URL, returns unified format with status.
    """
    try:
        result = fetch_subtitles(url, return_audio=return_audio, use_asr=use_asr)
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
        try:
            return await asyncio.wait_for(
                loop.run_in_executor(None, fetch_subtitles_single, url, return_audio),
                timeout=90.0
            )
        except asyncio.TimeoutError:
            return {
                "url": url,
                "status": "failed",
                "error": "timeout",
                "message": "请求超时，请稍后重试"
            }


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
    """Fetch video list from Bilibili user with retry on rate-limit."""
    if not BILIBILI_API_AVAILABLE:
        return []

    u = user.User(uid)
    videos = []
    pn = 1
    ps = min(limit, 30)
    total_pages = None

    # PATTERN: retry is placed INSIDE the loop, around a single page call.
    # DO NOT wrap the entire while-loop with async_retry_with_backoff — if the
    # outer wrapper retries on a rate-limit error it restarts from page 1,
    # multiplying requests and triggering more rate limits.
    while len(videos) < limit:
        resp = None
        for attempt in range(4):
            try:
                resp = await u.get_videos(pn=pn, ps=ps)
                break
            except Exception as e:
                if attempt >= 3 or not is_ratelimit_error(e):
                    break
                delay = 3.0 * (2 ** attempt) * (0.8 + random.random() * 0.4)
                print(f"[B站列表风控重试] UID:{uid} 第{pn}页 第{attempt+1}次重试，等待{delay:.1f}s")
                await asyncio.sleep(delay)

        if not resp or "list" not in resp or "vlist" not in resp.get("list", {}):
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

        # GOTCHA: Bilibili API returns page.count (total items), NOT page.pagecount.
        # page_info.get("pagecount") is always missing → defaults to 1 → only 1 page fetched.
        # Always compute total_pages = ceil(count / ps) manually.
        if total_pages is None:
            page_info = resp.get("page", {})
            total_count = page_info.get("count", 0)
            total_pages = (total_count + ps - 1) // ps if total_count > 0 else 1
            print(f"[B站视频列表] UID:{uid} 共 {total_count} 条视频，{total_pages} 页")

        if pn >= total_pages:
            break
        pn += 1
        await asyncio.sleep(1.0)  # conservative inter-page delay to avoid rate limiting

    return videos[:limit]


async def fetch_channel_videos(channel_url: str, limit: int = 20) -> Dict[str, Any]:
    """Fetch video list from a channel/UP主 page."""
    platform = detect_platform(channel_url)

    if platform == "bilibili":
        ensure_bilibili_cookies(interactive=False)

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
        "socket_timeout": 30,
        "retries": 2,
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
        "socket_timeout": 30,
        "retries": 2,
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
