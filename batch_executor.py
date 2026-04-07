"""
独立分批执行系统 - 支持频道视频批量拉取

功能：
- 创建批量任务（从频道URL获取所有视频并提取字幕）
- 分批次执行，支持断点续传
- 实时查询任务进度
- 任务完成后获取结果
"""

import asyncio
import json
import os
import time
import uuid
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Dict, List, Optional, Any, Callable
from datetime import datetime
import threading

# 任务状态
class TaskStatus(Enum):
    PENDING = "pending"      # 等待执行
    RUNNING = "running"      # 执行中
    PAUSED = "paused"        # 暂停
    COMPLETED = "completed"  # 完成
    FAILED = "failed"        # 失败


@dataclass
class BatchTask:
    """批处理任务"""
    task_id: str
    task_type: str  # "channel" | "batch_urls"
    channel_url: Optional[str]
    video_urls: List[str]
    total_videos: int
    processed_videos: int = 0
    success_count: int = 0
    failed_count: int = 0
    status: TaskStatus = TaskStatus.PENDING
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    error_message: Optional[str] = None
    results: List[Dict] = field(default_factory=list)
    # 执行配置
    batch_size: int = 5  # 每批处理数量
    concurrency: int = 3
    use_asr: bool = True
    # 进度追踪
    current_batch: int = 0
    total_batches: int = 0


# 内存任务存储（生产环境应使用Redis/数据库）
_task_store: Dict[str, BatchTask] = {}
_task_lock = threading.Lock()
_running_tasks: Dict[str, asyncio.Task] = {}


def create_task(
    task_type: str,
    channel_url: Optional[str] = None,
    video_urls: Optional[List[str]] = None,
    batch_size: int = 5,
    concurrency: int = 3,
    use_asr: bool = True
) -> BatchTask:
    """
    创建新的批处理任务

    Args:
        task_type: "channel" 或 "batch_urls"
        channel_url: 频道URL（task_type=channel时必填）
        video_urls: 视频URL列表（task_type=batch_urls时必填）
        batch_size: 每批处理数量
        concurrency: 并发数
        use_asr: 是否使用ASR兜底

    Returns:
        BatchTask 任务对象
    """
    task_id = str(uuid.uuid4())[:8]

    urls = video_urls or []

    task = BatchTask(
        task_id=task_id,
        task_type=task_type,
        channel_url=channel_url,
        video_urls=urls,
        total_videos=len(urls),
        batch_size=batch_size,
        concurrency=concurrency,
        use_asr=use_asr,
        total_batches=(len(urls) + batch_size - 1) // batch_size if urls else 0
    )

    with _task_lock:
        _task_store[task_id] = task

    return task


def get_task(task_id: str) -> Optional[BatchTask]:
    """获取任务信息"""
    return _task_store.get(task_id)


def list_tasks(status: Optional[str] = None) -> List[BatchTask]:
    """列出所有任务，可按状态过滤"""
    tasks = list(_task_store.values())
    if status:
        tasks = [t for t in tasks if t.status.value == status]
    # 按创建时间倒序
    return sorted(tasks, key=lambda x: x.created_at, reverse=True)


def delete_task(task_id: str) -> bool:
    """删除任务"""
    with _task_lock:
        if task_id in _task_store:
            # 如果任务正在运行，先取消
            if task_id in _running_tasks:
                _running_tasks[task_id].cancel()
                del _running_tasks[task_id]
            del _task_store[task_id]
            return True
    return False


def _update_task(task_id: str, **kwargs):
    """更新任务字段"""
    with _task_lock:
        if task_id in _task_store:
            task = _task_store[task_id]
            for key, value in kwargs.items():
                if hasattr(task, key):
                    setattr(task, key, value)


def task_to_dict(task: BatchTask) -> Dict:
    """任务对象转字典"""
    return {
        "task_id": task.task_id,
        "task_type": task.task_type,
        "channel_url": task.channel_url,
        "total_videos": task.total_videos,
        "processed_videos": task.processed_videos,
        "success_count": task.success_count,
        "failed_count": task.failed_count,
        "status": task.status.value,
        "progress_percent": round(task.processed_videos / task.total_videos * 100, 1) if task.total_videos > 0 else 0,
        "current_batch": task.current_batch,
        "total_batches": task.total_batches,
        "created_at": task.created_at,
        "started_at": task.started_at,
        "completed_at": task.completed_at,
        "error_message": task.error_message,
        "config": {
            "batch_size": task.batch_size,
            "concurrency": task.concurrency,
            "use_asr": task.use_asr
        }
    }


# ==================== 执行引擎 ====================

async def _fetch_video_list(channel_url: str, limit: int = 100) -> List[str]:
    """
    获取频道视频列表（使用yt-dlp，不使用bilibili-api）
    """
    import yt_dlp

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

        if not info:
            return []

        entries = info.get("entries", [])
        video_urls = []

        for entry in entries:
            if not entry:
                continue

            url = entry.get("url") or entry.get("webpage_url") or entry.get("id")
            if not url:
                continue

            # 过滤非视频页面
            if "/videos" in str(url) or "/streams" in str(url) or "/shorts" in str(url):
                continue

            # 确保完整URL
            if not url.startswith("http"):
                if "bilibili" in channel_url:
                    url = f"https://www.bilibili.com/video/{url}"
                else:
                    url = f"https://www.youtube.com/watch?v={url}"

            video_urls.append(url)

        return video_urls
    except Exception as e:
        raise ValueError(f"无法获取频道视频列表: {str(e)}")


async def _process_batch(
    task_id: str,
    urls: List[str],
    batch_index: int,
    concurrency: int,
    use_asr: bool
) -> List[Dict]:
    """处理一批视频"""
    from fetcher import fetch_subtitles_single

    semaphore = asyncio.Semaphore(concurrency)

    async def process_one(url: str) -> Dict:
        async with semaphore:
            # 使用线程池执行同步函数，添加90秒超时
            loop = asyncio.get_event_loop()
            try:
                result = await asyncio.wait_for(
                    loop.run_in_executor(
                        None,
                        fetch_subtitles_single,
                        url,
                        use_asr
                    ),
                    timeout=90  # 每个视频最多90秒
                )
                return result
            except asyncio.TimeoutError:
                return {
                    "url": url,
                    "status": "failed",
                    "error": "timeout",
                    "message": "处理超时（视频可能过长或为合集）"
                }

    # 并发处理本批所有URL
    tasks = [process_one(url) for url in urls]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    # 处理结果
    processed = []
    for url, result in zip(urls, results):
        if isinstance(result, Exception):
            processed.append({
                "url": url,
                "status": "failed",
                "error": "exception",
                "message": str(result)
            })
        else:
            processed.append(result)

    # 更新任务进度
    success = sum(1 for r in processed if r.get("status") == "success")
    failed = len(processed) - success

    _update_task(
        task_id,
        processed_videos=_task_store[task_id].processed_videos + len(urls),
        success_count=_task_store[task_id].success_count + success,
        failed_count=_task_store[task_id].failed_count + failed,
        current_batch=batch_index + 1,
        results=_task_store[task_id].results + processed
    )

    return processed


async def _execute_task(task_id: str):
    """执行任务主逻辑"""
    task = get_task(task_id)
    if not task:
        return

    try:
        # 更新状态为运行中
        _update_task(
            task_id,
            status=TaskStatus.RUNNING,
            started_at=datetime.now().isoformat()
        )

        # 1. 如果是频道任务，先获取视频列表
        if task.task_type == "channel" and task.channel_url:
            video_urls = await _fetch_video_list(task.channel_url, limit=100)
            if not video_urls:
                raise ValueError("频道中没有找到视频")

            _update_task(
                task_id,
                video_urls=video_urls,
                total_videos=len(video_urls),
                total_batches=(len(video_urls) + task.batch_size - 1) // task.batch_size
            )
            task = get_task(task_id)  # 刷新任务对象

        # 2. 分批处理
        urls = task.video_urls
        batch_size = task.batch_size

        for i in range(0, len(urls), batch_size):
            # 检查是否被取消
            if task_id not in _task_store or _task_store[task_id].status != TaskStatus.RUNNING:
                return

            batch_urls = urls[i:i + batch_size]
            batch_index = i // batch_size

            await _process_batch(
                task_id,
                batch_urls,
                batch_index,
                task.concurrency,
                task.use_asr
            )

            # 批次间短暂延迟，避免请求过快
            await asyncio.sleep(1)

        # 3. 完成
        _update_task(
            task_id,
            status=TaskStatus.COMPLETED,
            completed_at=datetime.now().isoformat()
        )

    except Exception as e:
        _update_task(
            task_id,
            status=TaskStatus.FAILED,
            error_message=str(e),
            completed_at=datetime.now().isoformat()
        )
    finally:
        # 清理运行中标记
        if task_id in _running_tasks:
            del _running_tasks[task_id]


async def start_task(task_id: str) -> bool:
    """启动任务"""
    task = get_task(task_id)
    if not task or task.status != TaskStatus.PENDING:
        return False

    # 创建异步任务
    asyncio_task = asyncio.create_task(_execute_task(task_id))
    _running_tasks[task_id] = asyncio_task

    return True


async def pause_task(task_id: str) -> bool:
    """暂停任务"""
    task = get_task(task_id)
    if not task or task.status != TaskStatus.RUNNING:
        return False

    _update_task(task_id, status=TaskStatus.PAUSED)
    return True


async def resume_task(task_id: str) -> bool:
    """恢复暂停的任务"""
    task = get_task(task_id)
    if not task or task.status != TaskStatus.PAUSED:
        return False

    _update_task(task_id, status=TaskStatus.RUNNING)

    # 重新启动执行（从当前进度继续）
    asyncio_task = asyncio.create_task(_execute_task(task_id))
    _running_tasks[task_id] = asyncio_task

    return True


# ==================== 导出功能 ====================

def get_task_results(task_id: str, format: str = "json") -> Optional[Dict]:
    """
    获取任务结果

    Args:
        task_id: 任务ID
        format: "json" | "txt" | "md"

    Returns:
        结果字典，包含数据和Content-Type
    """
    task = get_task(task_id)
    if not task:
        return None

    results = task.results

    if format == "json":
        return {
            "content": json.dumps({
                "task_id": task_id,
                "task_type": task.task_type,
                "channel_url": task.channel_url,
                "total": task.total_videos,
                "success": task.success_count,
                "failed": task.failed_count,
                "results": results
            }, ensure_ascii=False, indent=2),
            "content_type": "application/json",
            "filename": f"batch_{task_id}.json"
        }

    elif format == "txt":
        from fetcher import generate_txt_content
        txt = generate_txt_content(results)
        return {
            "content": txt,
            "content_type": "text/plain; charset=utf-8",
            "filename": f"batch_{task_id}.txt"
        }

    elif format == "md":
        from fetcher import generate_md_content
        sections = []
        for i, item in enumerate(results, 1):
            sections.append(generate_md_content(item, i))
        md = "\n\n---\n\n".join(sections)
        return {
            "content": md,
            "content_type": "text/markdown; charset=utf-8",
            "filename": f"batch_{task_id}.md"
        }

    else:
        return None


# ==================== 清理任务 ====================

def cleanup_old_tasks(max_age_hours: int = 24):
    """清理过期的已完成任务"""
    now = datetime.now()
    to_delete = []

    with _task_lock:
        for task_id, task in _task_store.items():
            if task.status in [TaskStatus.COMPLETED, TaskStatus.FAILED]:
                if task.completed_at:
                    completed = datetime.fromisoformat(task.completed_at)
                    age = (now - completed).total_seconds() / 3600
                    if age > max_age_hours:
                        to_delete.append(task_id)

        for task_id in to_delete:
            del _task_store[task_id]

    return len(to_delete)
