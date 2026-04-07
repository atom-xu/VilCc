"""FastAPI service for subtitle fetching."""
import asyncio
import io
from typing import List, Optional
from fastapi import FastAPI, HTTPException, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from fetcher import (
    fetch_subtitles,
    fetch_batch_subtitles,
    fetch_channel_videos,
    create_zip_export,
    generate_txt_content,
    safe_filename,
    search_videos
)

app = FastAPI(title="Subtitle Fetcher API")


class SubtitleRequest(BaseModel):
    url: str
    use_asr: bool = Field(default=True, description="是否使用ASR兜底（仅B站无字幕视频）")


class SubtitleResponse(BaseModel):
    title: str
    platform: str
    duration: float
    language: str
    source: str  # "cc" | "asr" | "ai_subtitle"
    subtitles: str


class ErrorResponse(BaseModel):
    error: str
    message: str


# Batch API models
class BatchSubtitleRequest(BaseModel):
    urls: List[str] = Field(..., description="视频URL列表，最多20条")
    concurrency: int = Field(default=3, ge=1, le=5, description="并发数，默认3，最大5")
    use_asr: bool = Field(default=True, description="是否使用ASR兜底（仅B站无字幕视频）")


class BatchResultItem(BaseModel):
    url: str
    status: str
    title: Optional[str] = None
    platform: Optional[str] = None
    duration: Optional[float] = None
    language: Optional[str] = None
    source: Optional[str] = None  # "cc" | "asr" | "ai_subtitle"
    subtitles: Optional[str] = None
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
    use_asr: bool = Field(default=True, description="是否使用ASR兜底（仅B站无字幕视频）")


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
    format: str = Field(default="zip", description="导出格式：zip、json、txt")
    return_audio: bool = Field(default=False, description="是否返回音频，默认否")
    concurrency: int = Field(default=3, ge=1, le=5, description="并发数")
    use_asr: bool = Field(default=True, description="是否使用ASR兜底")


class BatchExportRequest(BaseModel):
    urls: List[str] = Field(..., description="视频URL列表，最多20条")
    format: str = Field(default="zip", description="导出格式：zip、json、txt")
    return_audio: bool = Field(default=False, description="是否返回音频，默认否")
    concurrency: int = Field(default=3, ge=1, le=5, description="并发数")
    use_asr: bool = Field(default=True, description="是否使用ASR兜底")


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


@app.post("/subtitles", response_model=SubtitleResponse)
def get_subtitles(request: SubtitleRequest):
    """
    Fetch subtitles from video URL.

    Supports YouTube and Bilibili videos. For Bilibili videos without CC subtitles,
    will use DashScope ASR if use_asr=True and DASHSCOPE_API_KEY is set.
    """
    try:
        result = fetch_subtitles(request.url, use_asr=request.use_asr)
        return result
    except ValueError as e:
        error_msg = str(e)
        if error_msg.startswith("no_subtitles"):
            raise HTTPException(
                status_code=422,
                detail={"error": "no_subtitles", "message": "该视频没有可用字幕"}
            )
        elif error_msg.startswith("fetch_failed"):
            raise HTTPException(
                status_code=500,
                detail={"error": "fetch_failed", "message": "无法获取视频信息，请检查URL"}
            )
        elif error_msg.startswith("asr_failed"):
            raise HTTPException(
                status_code=500,
                detail={"error": "asr_failed", "message": f"ASR识别失败: {error_msg[10:]}"}
            )
        else:
            raise HTTPException(
                status_code=500,
                detail={"error": "unknown", "message": error_msg}
            )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail={"error": "fetch_failed", "message": f"无法获取视频信息，请检查URL: {str(e)}"}
        )


@app.post("/subtitles/batch")
async def get_subtitles_batch(request: BatchSubtitleRequest):
    """
    Fetch subtitles for multiple URLs concurrently.

    Supports up to 20 URLs. Individual failures don't affect overall result.
    For Bilibili videos without CC subtitles, will use DashScope ASR if use_asr=True.
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

    result = await fetch_batch_subtitles(unique_urls, request.concurrency, request.use_asr)
    return result


@app.post("/subtitles/channel")
async def get_channel_subtitles(request: ChannelSubtitleRequest):
    """
    Fetch subtitles from a YouTube channel or Bilibili UP主 page.

    Gets recent videos from the channel and fetches their subtitles.
    For Bilibili videos without CC subtitles, will use DashScope ASR if use_asr=True.
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
    batch_result = await fetch_batch_subtitles(video_urls, request.concurrency, request.use_asr)

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
        request.use_asr
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
        request.use_asr
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

    else:  # zip (default)
        zip_bytes = create_zip_export(results)
        return StreamingResponse(
            io.BytesIO(zip_bytes),
            media_type="application/zip",
            headers={"Content-Disposition": f"attachment; filename={safe_name}_subtitles.zip"}
        )


# ==================== Search Endpoint ====================

@app.post("/search", response_model=SearchResponse)
def search_videos_endpoint(request: SearchRequest):
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

    try:
        results = search_videos(request.query, request.platform, request.limit)
        return {
            "query": request.query,
            "platform": request.platform,
            "total": len(results),
            "results": results
        }
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
    use_asr: bool = Field(default=True, description="是否使用ASR兜底")
    auto_start: bool = Field(default=True, description="是否立即开始执行")


class CreateBatchTaskRequest(BaseModel):
    urls: List[str] = Field(..., description="视频URL列表，最多100条")
    batch_size: int = Field(default=5, ge=1, le=10, description="每批处理数量")
    concurrency: int = Field(default=3, ge=1, le=5, description="并发数")
    use_asr: bool = Field(default=True, description="是否使用ASR兜底")
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
        # 先获取视频列表
        from batch_executor import _fetch_video_list
        video_urls = await _fetch_video_list(request.channel_url, limit=request.limit)

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
            use_asr=request.use_asr
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
        use_asr=request.use_asr
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
