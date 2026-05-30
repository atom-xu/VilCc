"""FastAPI service for subtitle fetching."""
import asyncio
import io
import os
import sys
import time
from collections import deque
from datetime import datetime, timezone
from typing import List, Optional
from fastapi import FastAPI, HTTPException, Response
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from fetcher import (
    fetch_subtitles,
    fetch_subtitles_racing,
    fetch_batch_subtitles,
    fetch_channel_videos,
    create_zip_export,
    generate_txt_content,
    safe_filename,
    search_videos,
    has_bilibili_cookies,
)

app = FastAPI(title="VilCC API")

# ── Activity log ─────────────────────────────────────────────────────────────
# Keeps the last 50 entries; each entry: {time, url, title, status, source}
_activity_log: deque = deque(maxlen=50)
_start_time: float = time.monotonic()


class SubtitleRequest(BaseModel):
    url: str
    return_audio: bool = Field(default=True, description="无字幕时是否返回音频base64，默认true")


class SubtitleResponse(BaseModel):
    title: str
    platform: str
    duration: float
    upload_date: Optional[str] = None  # YYYYMMDD
    language: Optional[str] = None
    source: str  # "cc" | "audio" | "none"
    subtitles: Optional[str] = None
    segments: Optional[List] = None  # [{start, end, text}] with timestamps
    audio_base64: Optional[str] = None


class ErrorResponse(BaseModel):
    error: str
    message: str


# Batch API models
class BatchSubtitleRequest(BaseModel):
    urls: List[str] = Field(..., description="视频URL列表，最多20条")
    concurrency: int = Field(default=3, ge=1, le=5, description="并发数，默认3，最大5")
    return_audio: bool = Field(default=True, description="无字幕时是否返回音频base64，默认true")


class BatchResultItem(BaseModel):
    url: str
    status: str
    title: Optional[str] = None
    platform: Optional[str] = None
    duration: Optional[float] = None
    language: Optional[str] = None
    source: Optional[str] = None  # "cc" | "audio" | "none"
    subtitles: Optional[str] = None
    audio_base64: Optional[str] = None
    error: Optional[str] = None
    message: Optional[str] = None


class BatchSubtitleResponse(BaseModel):
    total: int
    success: int
    failed: int
    results: List[BatchResultItem]


# Channel API models
class ChannelSubtitleRequest(BaseModel):
    channel_url: str = Field(..., description="频道主页URL，支持 YouTube @handle 和 B站 space.bilibili.com/uid")
    limit: int = Field(default=20, ge=1, le=50, description="获取最近N条视频，默认20，最大50")
    concurrency: int = Field(default=3, ge=1, le=5, description="并发数，默认3")
    return_audio: bool = Field(default=True, description="无字幕时是否返回音频base64，默认true")


class ChannelSubtitleResponse(BaseModel):
    channel: str
    platform: str
    total_videos: int
    success: int
    failed: int
    results: List[BatchResultItem]


# Export API models
class ChannelExportRequest(BaseModel):
    channel_url: str = Field(..., description="频道主页URL")
    limit: int = Field(default=20, ge=1, le=50, description="获取最近N条视频")
    format: str = Field(default="zip", description="导出格式：zip、json、txt、md")
    return_audio: bool = Field(default=False, description="是否返回音频base64，默认否")
    concurrency: int = Field(default=3, ge=1, le=5, description="并发数")


class BatchExportRequest(BaseModel):
    urls: List[str] = Field(..., description="视频URL列表，最多20条")
    format: str = Field(default="zip", description="导出格式：zip、json、txt、md")
    return_audio: bool = Field(default=False, description="是否返回音频base64，默认否")
    concurrency: int = Field(default=3, ge=1, le=5, description="并发数")


# Search API models
class SearchRequest(BaseModel):
    query: str = Field(..., description="搜索关键词")
    platform: str = Field(default="youtube", description="搜索平台：youtube、bilibili、all")
    limit: int = Field(default=10, ge=1, le=20, description="返回结果数量，默认10，最大20")


class SearchResultItem(BaseModel):
    title: str
    url: str
    platform: str
    duration: Optional[float] = None
    view_count: Optional[int] = None
    upload_date: Optional[str] = None
    channel: Optional[str] = None


class SearchResponse(BaseModel):
    query: str
    platform: str
    total: int
    results: List[SearchResultItem]


@app.get("/health")
def health_check():
    """Health check endpoint."""
    return {"status": "ok"}


# ── Dashboard routes ──────────────────────────────────────────────────────────

@app.get("/", include_in_schema=False)
def dashboard():
    """Serve the management dashboard."""
    return FileResponse(os.path.join(os.path.dirname(__file__), "static", "index.html"))


@app.get("/api/status")
def api_status():
    """Return server status summary."""
    uptime_seconds = int(time.monotonic() - _start_time)
    hours, remainder = divmod(uptime_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    uptime_str = f"{hours}h {minutes}m {seconds}s"
    return {
        "uptime": uptime_str,
        "uptime_seconds": uptime_seconds,
        "bilibili_auth": has_bilibili_cookies(),
        "recent_count": len(_activity_log),
        "server_version": "1.0.0",
    }


@app.get("/api/activity")
def api_activity():
    """Return last 20 activity log entries (newest first)."""
    entries = list(_activity_log)
    return {"entries": list(reversed(entries))[:20]}


import json as _json

_SERVER_NAME = "vilcc"
_MCP_ENTRY_KEY = "mcpServers"

# Config paths and display names for each supported AI client
_CLIENTS = {
    "claude-desktop": {
        "label": "Claude Desktop",
        "config": "~/Library/Application Support/Claude/claude_desktop_config.json",
        "install_check": "~/Library/Application Support/Claude",
        "restart": "重启 Claude Desktop",
        "deep_link": None,
    },
    "claude-code": {
        "label": "Claude Code",
        "config": "~/.claude/settings.json",
        "install_check": "~/.claude",
        "restart": "重新加载 Claude Code",
        "deep_link": None,
    },
    "cursor": {
        "label": "Cursor",
        "config": "~/.cursor/mcp.json",
        "install_check": "~/.cursor",
        "restart": "重启 Cursor",
        "deep_link": "cursor://anysphere.cursor-deeplink/mcp/install",
    },
    "vscode": {
        "label": "VS Code",
        "config": "~/Library/Application Support/Code/User/mcp.json",
        "install_check": "~/Library/Application Support/Code",
        "restart": "重启 VS Code",
        "deep_link": "vscode://vscode.mcp/install",
    },
    "windsurf": {
        "label": "Windsurf",
        "config": "~/.codeium/windsurf/mcp_config.json",
        "install_check": "~/.codeium/windsurf",
        "restart": "重启 Windsurf",
        "deep_link": None,
    },
    "trae": {
        # Trae IDE (ByteDance) — also covers 豆包 MarsCode which merged into Trae
        "label": "Trae / MarsCode",
        "config": "~/Library/Application Support/Trae/User/mcp.json",
        "install_check": "~/Library/Application Support/Trae",
        "restart": "重启 Trae",
        "deep_link": None,
    },
    "lingma": {
        # 通义灵码 has no config file — MCP is configured through the plugin UI only
        "label": "通义灵码",
        "config": None,
        "install_check": "~/.lingma",
        "ui_only": True,
        "ui_hint": "头像 → 个人设置 → MCP服务 → 粘贴 JSON",
        "restart": "",
        "deep_link": None,
    },
    "openclaw": {
        # OpenClaw uses ~/.openclaw/openclaw.json with nested mcp.servers (not mcpServers)
        "label": "OpenClaw",
        "config": "~/.openclaw/openclaw.json",
        "install_check": "~/.openclaw",
        "config_key": ["mcp", "servers"],
        "restart": "openclaw gateway restart",
        "deep_link": None,
    },
}


def _mcp_entry():
    return {
        "command": sys.executable,
        "args": [os.path.join(os.path.dirname(os.path.abspath(__file__)), "mcp_server.py")],
    }


def _read_config(path: str) -> dict:
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return _json.load(f)
        except Exception:
            pass
    return {}


def _write_config(path: str, data: dict):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        _json.dump(data, f, indent=2, ensure_ascii=False)


def _is_configured(config: dict, config_key: list = None) -> bool:
    if config_key is None:
        config_key = [_MCP_ENTRY_KEY]
    current = config
    for key in config_key[:-1]:
        if not isinstance(current, dict):
            return False
        current = current.get(key, {})
    servers = current.get(config_key[-1]) if isinstance(current, dict) else {}
    servers = servers or {}
    # Also check legacy "servers" key for standard-format clients
    if not servers and config_key == [_MCP_ENTRY_KEY]:
        servers = config.get("servers") or {}
    return _SERVER_NAME in servers


@app.get("/api/mcp-config")
def api_mcp_config():
    """Return MCP server config for deep link generation."""
    entry = _mcp_entry()
    return {"command": entry["command"], "args": entry["args"], "name": _SERVER_NAME}


@app.get("/api/client-status")
def api_client_status():
    """Detect which AI clients are installed and whether MCP is configured in each."""
    results = []
    for client_id, info in _CLIENTS.items():
        ui_only = info.get("ui_only", False)
        config_path_raw = info.get("config")
        config_path = os.path.expanduser(config_path_raw) if config_path_raw else None
        fallback_check = os.path.dirname(config_path) if config_path else ""
        check_path = os.path.expanduser(info.get("install_check", fallback_check))
        installed = bool(
            (check_path and (os.path.isdir(check_path) or os.path.exists(check_path)))
            or (config_path and os.path.exists(config_path))
        )
        if ui_only:
            configured = False
        else:
            config = _read_config(config_path) if (installed and config_path) else {}
            config_key = info.get("config_key", [_MCP_ENTRY_KEY])
            configured = _is_configured(config, config_key) if installed else False
        results.append({
            "id": client_id,
            "label": info["label"],
            "installed": installed,
            "configured": configured,
            "ui_only": ui_only,
            "ui_hint": info.get("ui_hint", ""),
            "restart_hint": info.get("restart", ""),
            "deep_link": info.get("deep_link"),
        })
    return {"clients": results}


@app.post("/api/connect/{client_id}")
def connect_client(client_id: str):
    """Write MCP config for the specified AI client."""
    if client_id not in _CLIENTS:
        raise HTTPException(status_code=404, detail="Unknown client")

    info = _CLIENTS[client_id]

    if info.get("ui_only"):
        raise HTTPException(
            status_code=422,
            detail={"message": f"请手动配置：{info.get('ui_hint', '')}", "ui_only": True},
        )

    config_path = os.path.expanduser(info["config"])
    config = _read_config(config_path)
    config_key = info.get("config_key", [_MCP_ENTRY_KEY])

    # Navigate / create nested path (e.g. ["mcp", "servers"] for OpenClaw)
    current = config
    for key in config_key[:-1]:
        current.setdefault(key, {})
        current = current[key]
    current.setdefault(config_key[-1], {})
    current[config_key[-1]][_SERVER_NAME] = _mcp_entry()

    _write_config(config_path, config)

    return {
        "ok": True,
        "config_path": config_path,
        "message": f"已写入配置，请{info['restart']}",
    }


# ── Mount static files (must come after explicit routes) ─────────────────────
_static_dir = os.path.join(os.path.dirname(__file__), "static")
app.mount("/static", StaticFiles(directory=_static_dir), name="static")


# ── Subtitle endpoint ─────────────────────────────────────────────────────────

@app.post("/subtitles", response_model=SubtitleResponse)
async def get_subtitles(request: SubtitleRequest):
    """
    Fetch subtitles from video URL.

    Supports YouTube and Bilibili videos.
    - Has CC subtitles: source="cc", subtitles=text
    - No subtitles + return_audio=True: source="audio", audio_base64=data
    - No subtitles + return_audio=False: source="none"

    Uses racing strategy: fast path (platform API) and yt-dlp run simultaneously.
    First valid CC result wins; if both fail, falls back to audio/ASR.
    """
    _log_time = datetime.now(timezone.utc).strftime("%H:%M:%S")
    _log_url = request.url
    try:
        result = await asyncio.wait_for(
            fetch_subtitles_racing(request.url, request.return_audio),
            timeout=90.0
        )
        _activity_log.append({
            "time": _log_time,
            "url": _log_url,
            "title": result.get("title", ""),
            "status": "ok",
            "source": result.get("source", ""),
        })
        return result
    except asyncio.TimeoutError:
        _activity_log.append({"time": _log_time, "url": _log_url, "title": "", "status": "error", "source": "timeout"})
        raise HTTPException(
            status_code=504,
            detail={"error": "timeout", "message": "请求超时，请稍后重试"}
        )
    except ValueError as e:
        error_msg = str(e)
        _activity_log.append({"time": _log_time, "url": _log_url, "title": "", "status": "error", "source": error_msg[:30]})
        if "no_subtitles" in error_msg:
            raise HTTPException(
                status_code=422,
                detail={"error": "no_subtitles", "message": "该视频没有可用字幕"}
            )
        elif "fetch_failed" in error_msg:
            raise HTTPException(
                status_code=500,
                detail={"error": "fetch_failed", "message": "无法获取视频信息，请检查URL"}
            )
        else:
            raise HTTPException(
                status_code=500,
                detail={"error": "unknown", "message": error_msg}
            )
    except Exception as e:
        _activity_log.append({"time": _log_time, "url": _log_url, "title": "", "status": "error", "source": "exception"})
        raise HTTPException(
            status_code=500,
            detail={"error": "fetch_failed", "message": f"无法获取视频信息，请检查URL: {str(e)}"}
        )


@app.post("/subtitles/batch")
async def get_subtitles_batch(request: BatchSubtitleRequest):
    """
    Fetch subtitles for multiple URLs concurrently.

    Supports up to 20 URLs. Individual failures don't affect overall result.
    - Has CC subtitles: source="cc", subtitles=text
    - No subtitles + return_audio=True: source="audio", audio_base64=data
    - No subtitles + return_audio=False: source="none"
    """
    if len(request.urls) > 20:
        raise HTTPException(
            status_code=422,
            detail={"error": "too_many_urls", "message": "URL数量不能超过20条"}
        )

    # Remove duplicates while preserving order
    seen = set()
    unique_urls = []
    for url in request.urls:
        if url not in seen:
            seen.add(url)
            unique_urls.append(url)

    result = await fetch_batch_subtitles(unique_urls, request.concurrency, request.return_audio)
    return result


@app.post("/subtitles/channel")
async def get_channel_subtitles(request: ChannelSubtitleRequest):
    """
    Fetch subtitles from a YouTube channel or Bilibili UP主 page.

    Gets recent videos from the channel and fetches their subtitles.
    - Has CC subtitles: source="cc", subtitles=text
    - No subtitles + return_audio=True: source="audio", audio_base64=data
    - No subtitles + return_audio=False: source="none"
    """
    # First, fetch video list from channel
    channel_info = await fetch_channel_videos(request.channel_url, request.limit)

    if "error" in channel_info:
        error_code = channel_info["error"]
        raise HTTPException(
            status_code=422 if error_code in ["channel_not_supported", "invalid_channel_url"] else 500,
            detail=channel_info
        )

    video_urls = channel_info.get("video_urls", [])
    if not video_urls:
        return {
            "channel": channel_info.get("channel", ""),
            "platform": channel_info.get("platform", ""),
            "total_videos": 0,
            "success": 0,
            "failed": 0,
            "results": []
        }

    # Fetch subtitles for all videos
    batch_result = await fetch_batch_subtitles(video_urls, request.concurrency, request.return_audio)

    return {
        "channel": channel_info.get("channel", ""),
        "platform": channel_info.get("platform", ""),
        "total_videos": len(video_urls),
        "success": batch_result["success"],
        "failed": batch_result["failed"],
        "results": batch_result["results"]
    }


# ==================== Export Endpoints ====================

@app.post("/subtitles/batch/export")
async def export_batch_subtitles(request: BatchExportRequest):
    """
    Export batch subtitles in zip, json, or txt format.
    """
    if len(request.urls) > 20:
        raise HTTPException(
            status_code=422,
            detail={"error": "too_many_urls", "message": "URL数量不能超过20条"}
        )

    # Fetch subtitles
    batch_result = await fetch_batch_subtitles(
        request.urls,
        request.concurrency,
        request.return_audio
    )

    results = batch_result["results"]

    if request.format == "json":
        # Return JSON directly
        return Response(
            content=__import__('json').dumps(batch_result, ensure_ascii=False, indent=2),
            media_type="application/json",
            headers={"Content-Disposition": "attachment; filename=subtitles.json"}
        )

    elif request.format == "txt":
        # Return combined txt
        txt_content = generate_txt_content(results)
        return Response(
            content=txt_content.encode('utf-8'),
            media_type="text/plain; charset=utf-8",
            headers={"Content-Disposition": "attachment; filename=subtitles.txt"}
        )

    elif request.format == "md":
        # Return combined markdown
        from fetcher import generate_md_content
        sections = []
        for i, item in enumerate(results, 1):
            sections.append(generate_md_content(item, i))
        md_content = "\n\n---\n\n".join(sections)
        return Response(
            content=md_content.encode('utf-8'),
            media_type="text/markdown; charset=utf-8",
            headers={"Content-Disposition": "attachment; filename=subtitles.md"}
        )

    else:  # zip (default)
        # Create ZIP with markdown files
        zip_bytes = create_zip_export(results)
        return StreamingResponse(
            io.BytesIO(zip_bytes),
            media_type="application/zip",
            headers={"Content-Disposition": "attachment; filename=subtitles.zip"}
        )


@app.post("/subtitles/channel/export")
async def export_channel_subtitles(request: ChannelExportRequest):
    """
    Export channel subtitles in zip, json, or txt format.
    """
    # First, fetch video list from channel
    channel_info = await fetch_channel_videos(request.channel_url, request.limit)

    if "error" in channel_info:
        error_code = channel_info["error"]
        raise HTTPException(
            status_code=422 if error_code in ["channel_not_supported", "invalid_channel_url"] else 500,
            detail=channel_info
        )

    video_urls = channel_info.get("video_urls", [])
    if not video_urls:
        # Return empty export
        if request.format == "json":
            empty_result = {
                "channel": channel_info.get("channel", ""),
                "platform": channel_info.get("platform", ""),
                "total": 0,
                "success": 0,
                "failed": 0,
                "results": []
            }
            return Response(
                content=__import__('json').dumps(empty_result, ensure_ascii=False, indent=2),
                media_type="application/json",
                headers={"Content-Disposition": "attachment; filename=subtitles.json"}
            )
        elif request.format == "txt":
            return Response(
                content=f"# {channel_info.get('channel', 'Channel')}\n\n未找到视频。".encode('utf-8'),
                media_type="text/plain; charset=utf-8",
                headers={"Content-Disposition": "attachment; filename=subtitles.txt"}
            )
        else:
            return Response(
                content=b"",
                media_type="application/zip",
                headers={"Content-Disposition": "attachment; filename=subtitles.zip"}
            )

    # Fetch subtitles for all videos
    batch_result = await fetch_batch_subtitles(
        video_urls,
        request.concurrency,
        request.return_audio
    )

    results = batch_result["results"]
    channel_name = channel_info.get("channel", "channel")
    safe_name = safe_filename(channel_name)

    if request.format == "json":
        full_result = {
            "channel": channel_name,
            "platform": channel_info.get("platform", ""),
            "total": len(video_urls),
            "success": batch_result["success"],
            "failed": batch_result["failed"],
            "results": results
        }
        return Response(
            content=__import__('json').dumps(full_result, ensure_ascii=False, indent=2),
            media_type="application/json",
            headers={"Content-Disposition": f"attachment; filename={safe_name}_subtitles.json"}
        )

    elif request.format == "txt":
        txt_content = f"# {channel_name} - 字幕导出\n\n"
        txt_content += f"平台：{channel_info.get('platform', '')}\n"
        txt_content += f"视频数：{len(video_urls)}\n"
        txt_content += f"成功：{batch_result['success']} | 失败：{batch_result['failed']}\n\n"
        txt_content += "=" * 50 + "\n\n"
        txt_content += generate_txt_content(results)

        return Response(
            content=txt_content.encode('utf-8'),
            media_type="text/plain; charset=utf-8",
            headers={"Content-Disposition": f"attachment; filename={safe_name}_subtitles.txt"}
        )

    elif request.format == "md":
        from fetcher import generate_md_content
        sections = []
        for i, item in enumerate(results, 1):
            sections.append(generate_md_content(item, i))
        md_content = f"# {channel_name} - 字幕导出\n\n"
        md_content += f"**平台：**{channel_info.get('platform', '')}  \n"
        md_content += f"**视频数：**{len(video_urls)}  \n"
        md_content += f"**成功：**{batch_result['success']} | **失败：**{batch_result['failed']}\n\n"
        md_content += "---\n\n"
        md_content += "\n\n---\n\n".join(sections)
        return Response(
            content=md_content.encode('utf-8'),
            media_type="text/markdown; charset=utf-8",
            headers={"Content-Disposition": f"attachment; filename={safe_name}_subtitles.md"}
        )

    else:  # zip (default)
        zip_bytes = create_zip_export(results)
        return StreamingResponse(
            io.BytesIO(zip_bytes),
            media_type="application/zip",
            headers={"Content-Disposition": f"attachment; filename={safe_name}_subtitles.zip"}
        )


# ==================== Search Endpoint ====================

@app.post("/search", response_model=SearchResponse)
async def search_videos_endpoint(request: SearchRequest):
    """
    Search videos on YouTube or Bilibili.

    Returns video list with title, URL, duration, view count, etc.
    Can be used with /subtitles/batch to get subtitles.
    """
    if request.limit > 20:
        raise HTTPException(
            status_code=422,
            detail={"error": "limit_too_large", "message": "limit不能超过20"}
        )

    import asyncio
    loop = asyncio.get_event_loop()
    try:
        results = await asyncio.wait_for(
            loop.run_in_executor(None, search_videos, request.query, request.platform, request.limit),
            timeout=60.0
        )
        return {
            "query": request.query,
            "platform": request.platform,
            "total": len(results),
            "results": results
        }
    except asyncio.TimeoutError:
        raise HTTPException(
            status_code=504,
            detail={"error": "timeout", "message": "搜索超时，请稍后重试"}
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail={"error": "search_failed", "message": f"搜索失败: {str(e)}"}
        )


# ==================== Batch Executor Endpoints ====================

from batch_executor import (
    create_task, get_task, list_tasks, delete_task,
    start_task, pause_task, resume_task, get_task_results,
    task_to_dict, TaskStatus
)
from pydantic import BaseModel, Field
from typing import Literal


class CreateChannelTaskRequest(BaseModel):
    channel_url: str = Field(..., description="频道主页URL")
    limit: int = Field(default=50, ge=1, le=100, description="最大获取视频数")
    batch_size: int = Field(default=5, ge=1, le=10, description="每批处理数量")
    concurrency: int = Field(default=3, ge=1, le=5, description="并发数")
    return_audio: bool = Field(default=True, description="无字幕时是否返回音频base64")
    use_asr: bool = Field(default=False, description="无字幕时是否使用DashScope ASR转文字（需配置DASHSCOPE_API_KEY）")
    batch_delay: float = Field(default=2.0, ge=0.0, le=120.0, description="批次间基础延迟（秒），建议B站任务设为5-10")
    auto_start: bool = Field(default=True, description="是否立即开始执行")


class CreateBatchTaskRequest(BaseModel):
    urls: List[str] = Field(..., description="视频URL列表，最多100条")
    batch_size: int = Field(default=5, ge=1, le=10, description="每批处理数量")
    concurrency: int = Field(default=3, ge=1, le=5, description="并发数")
    return_audio: bool = Field(default=True, description="无字幕时是否返回音频base64")
    use_asr: bool = Field(default=False, description="无字幕时是否使用DashScope ASR转文字（需配置DASHSCOPE_API_KEY）")
    batch_delay: float = Field(default=2.0, ge=0.0, le=120.0, description="批次间基础延迟（秒），建议B站任务设为5-10")
    auto_start: bool = Field(default=True, description="是否立即开始执行")


class TaskResponse(BaseModel):
    task_id: str
    task_type: str
    status: str
    total_videos: int
    processed_videos: int
    success_count: int
    failed_count: int
    progress_percent: float
    current_batch: int
    total_batches: int
    created_at: str
    started_at: Optional[str]
    completed_at: Optional[str]
    error_message: Optional[str]


@app.post("/batch/tasks/channel", response_model=TaskResponse)
async def create_channel_task_endpoint(request: CreateChannelTaskRequest):
    """
    创建频道批量任务 - 自动获取频道所有视频并提取字幕

    流程：
    1. 获取频道视频列表
    2. 分批获取字幕
    3. 可随时查询进度
    4. 完成后导出结果
    """
    try:
        # 先获取视频列表（优先使用 bilibili_api 以规避 yt-dlp 的 B 站风控）
        from fetcher import fetch_channel_videos
        channel_info = await fetch_channel_videos(request.channel_url, limit=request.limit)

        if "error" in channel_info:
            raise HTTPException(
                status_code=422,
                detail={"error": channel_info["error"], "message": channel_info.get("message", "无法获取频道视频列表")}
            )

        video_urls = channel_info.get("video_urls", [])
        if not video_urls:
            raise HTTPException(
                status_code=422,
                detail={"error": "no_videos", "message": "频道中没有找到视频"}
            )

        # 创建任务
        task = create_task(
            task_type="channel",
            channel_url=request.channel_url,
            video_urls=video_urls,
            batch_size=request.batch_size,
            concurrency=request.concurrency,
            return_audio=request.return_audio,
            use_asr=request.use_asr,
            batch_delay=request.batch_delay
        )

        # 自动开始
        if request.auto_start:
            await start_task(task.task_id)

        return task_to_dict(task)

    except ValueError as e:
        raise HTTPException(status_code=422, detail={"error": "invalid_url", "message": str(e)})
    except Exception as e:
        raise HTTPException(status_code=500, detail={"error": "create_failed", "message": str(e)})


@app.post("/batch/tasks/urls", response_model=TaskResponse)
async def create_batch_task_endpoint(request: CreateBatchTaskRequest):
    """
    创建URL列表批量任务 - 对指定URL列表分批提取字幕
    """
    if len(request.urls) > 100:
        raise HTTPException(
            status_code=422,
            detail={"error": "too_many_urls", "message": "URL数量不能超过100条"}
        )

    # 去重
    seen = set()
    unique_urls = []
    for url in request.urls:
        if url not in seen:
            seen.add(url)
            unique_urls.append(url)

    # 创建任务
    task = create_task(
        task_type="batch_urls",
        video_urls=unique_urls,
        batch_size=request.batch_size,
        concurrency=request.concurrency,
        return_audio=request.return_audio,
        use_asr=request.use_asr,
        batch_delay=request.batch_delay
    )

    # 自动开始
    if request.auto_start:
        await start_task(task.task_id)

    return task_to_dict(task)


@app.get("/batch/tasks")
def list_tasks_endpoint(status: Optional[str] = None):
    """
    列出所有任务，可按状态过滤

    状态：pending, running, paused, completed, failed
    """
    tasks = list_tasks(status)
    return {
        "total": len(tasks),
        "tasks": [task_to_dict(t) for t in tasks]
    }


@app.get("/batch/tasks/{task_id}", response_model=TaskResponse)
def get_task_endpoint(task_id: str):
    """获取任务详情和进度"""
    task = get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail={"error": "not_found", "message": "任务不存在"})
    return task_to_dict(task)


@app.post("/batch/tasks/{task_id}/start")
async def start_task_endpoint(task_id: str):
    """手动启动任务"""
    task = get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail={"error": "not_found", "message": "任务不存在"})

    if task.status != TaskStatus.PENDING:
        raise HTTPException(status_code=422, detail={"error": "invalid_state", "message": "任务不是待执行状态"})

    success = await start_task(task_id)
    if not success:
        raise HTTPException(status_code=500, detail={"error": "start_failed", "message": "启动任务失败"})

    return {"message": "任务已启动", "task_id": task_id}


@app.post("/batch/tasks/{task_id}/pause")
async def pause_task_endpoint(task_id: str):
    """暂停正在执行的任务"""
    task = get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail={"error": "not_found", "message": "任务不存在"})

    success = await pause_task(task_id)
    if not success:
        raise HTTPException(status_code=422, detail={"error": "pause_failed", "message": "只能暂停运行中的任务"})

    return {"message": "任务已暂停", "task_id": task_id}


@app.post("/batch/tasks/{task_id}/resume")
async def resume_task_endpoint(task_id: str):
    """恢复暂停的任务"""
    task = get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail={"error": "not_found", "message": "任务不存在"})

    success = await resume_task(task_id)
    if not success:
        raise HTTPException(status_code=422, detail={"error": "resume_failed", "message": "只能恢复暂停的任务"})

    return {"message": "任务已恢复", "task_id": task_id}


@app.delete("/batch/tasks/{task_id}")
def delete_task_endpoint(task_id: str):
    """删除任务"""
    task = get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail={"error": "not_found", "message": "任务不存在"})

    delete_task(task_id)
    return {"message": "任务已删除", "task_id": task_id}


@app.get("/batch/tasks/{task_id}/results")
def get_task_results_endpoint(
    task_id: str,
    format: Literal["json", "txt", "md"] = "json"
):
    """
    获取任务结果

    format:
    - json: JSON格式完整数据
    - txt: 纯文本格式
    - md: Markdown格式
    """
    task = get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail={"error": "not_found", "message": "任务不存在"})

    if task.status not in [TaskStatus.COMPLETED, TaskStatus.PAUSED]:
        raise HTTPException(
            status_code=422,
            detail={"error": "not_ready", "message": "任务尚未完成"}
        )

    result = get_task_results(task_id, format)
    if not result:
        raise HTTPException(status_code=500, detail={"error": "export_failed", "message": "导出失败"})

    return Response(
        content=result["content"].encode('utf-8'),
        media_type=result["content_type"],
        headers={"Content-Disposition": f"attachment; filename={result['filename']}"}
    )


@app.get("/batch/tasks/{task_id}/progress")
def get_task_progress_endpoint(task_id: str):
    """
    获取任务实时进度（SSE流式，可选）
    """
    task = get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail={"error": "not_found", "message": "任务不存在"})

    return {
        "task_id": task_id,
        "status": task.status.value,
        "progress": {
            "total": task.total_videos,
            "processed": task.processed_videos,
            "success": task.success_count,
            "failed": task.failed_count,
            "percent": round(task.processed_videos / task.total_videos * 100, 1) if task.total_videos > 0 else 0,
            "current_batch": task.current_batch,
            "total_batches": task.total_batches
        }
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8765)
