"""
通用轮询调度器 - 规避B站风控的长周期任务调度

特点：
- 支持 channel 和 urls 两种任务模式
- 自动轮询任务进度直到完成
- 支持慢速模式（低并发 + 长批次延迟）
- 完成后自动下载结果

示例：
  # 慢速拉取B站UP主全部视频字幕
  python scheduler_polling.py channel "https://space.bilibili.com/3546816064784771" --limit 50 --slow-mode

  # 指定URL列表批量提取
  python scheduler_polling.py urls url1 url2 url3 --batch-delay 5
"""

import argparse
import sys
import time
import requests
from datetime import datetime

# 自动获取 B 站 Cookie（如需）
from fetcher import ensure_bilibili_cookies


def parse_args():
    parser = argparse.ArgumentParser(description="subtitle-fetcher 通用轮询调度器")
    subparsers = parser.add_subparsers(dest="mode", required=True)

    # channel 子命令
    channel_parser = subparsers.add_parser("channel", help="从频道/UP主拉取视频字幕")
    channel_parser.add_argument("channel_url", help="频道主页URL")
    channel_parser.add_argument("--limit", type=int, default=50, help="最大获取视频数")

    # urls 子命令
    urls_parser = subparsers.add_parser("urls", help="从指定URL列表提取字幕")
    urls_parser.add_argument("urls", nargs="+", help="视频URL列表")

    # 通用参数
    for p in (channel_parser, urls_parser):
        p.add_argument("--base-url", default="http://localhost:8765", help="API 基础地址")
        p.add_argument("--batch-size", type=int, default=5, help="每批处理数量")
        p.add_argument("--concurrency", type=int, default=3, help="并发数")
        p.add_argument("--batch-delay", type=float, default=5.0, help="批次间延迟（秒）")
        p.add_argument("--return-audio", action="store_true", help="无字幕时返回音频")
        p.add_argument("--use-asr", action="store_true", help="无字幕时使用DashScope ASR自动转文字（需配置DASHSCOPE_API_KEY）")
        p.add_argument("--poll-interval", type=int, default=10, help="进度轮询间隔（秒）")
        p.add_argument("--slow-mode", action="store_true", help="启用慢速模式（batch_size=3, concurrency=2, batch_delay=10）")
        p.add_argument("--format", default="txt", choices=["json", "txt", "md"], help="结果导出格式")
        p.add_argument("--output", default=None, help="结果保存路径")

    return parser.parse_args()


def create_task(base_url: str, mode: str, payload: dict):
    """创建并启动任务"""
    endpoint = f"{base_url}/batch/tasks/{mode}"
    resp = requests.post(endpoint, json=payload)
    if resp.status_code != 200:
        print(f"创建任务失败 [{resp.status_code}]: {resp.text}")
        sys.exit(1)
    return resp.json()


def poll_task(base_url: str, task_id: str, poll_interval: int):
    """轮询任务进度直到完成"""
    endpoint = f"{base_url}/batch/tasks/{task_id}"
    start_time = time.time()

    while True:
        resp = requests.get(endpoint)
        if resp.status_code != 200:
            print(f"  查询进度失败 [{resp.status_code}]")
            time.sleep(poll_interval)
            continue

        task = resp.json()
        status = task["status"]
        processed = task["processed_videos"]
        total = task["total_videos"]
        success = task["success_count"]
        failed = task["failed_count"]
        percent = task["progress_percent"]
        elapsed = int(time.time() - start_time)

        print(
            f"  [{datetime.now().strftime('%H:%M:%S')}] "
            f"状态:{status} 进度:{processed}/{total}({percent}%) "
            f"成功:{success} 失败:{failed} 已用:{elapsed}s"
        )

        if status in ("completed", "failed"):
            return task

        time.sleep(poll_interval)


def download_result(base_url: str, task_id: str, fmt: str, output_path: str):
    """下载任务结果"""
    endpoint = f"{base_url}/batch/tasks/{task_id}/results?format={fmt}"
    resp = requests.get(endpoint)
    if resp.status_code != 200:
        print(f"下载结果失败 [{resp.status_code}]: {resp.text}")
        return False

    with open(output_path, "wb") as f:
        f.write(resp.content)
    print(f"  ✓ 结果已保存: {output_path} ({len(resp.content)} bytes)")
    return True


def main():
    args = parse_args()

    # 自动获取 B 站 Cookie（如缺失）
    if args.mode == "channel" or (args.mode == "urls" and any("bilibili" in u for u in args.urls)):
        ensure_bilibili_cookies(interactive=True)

    # 慢速模式覆盖参数
    batch_size = 3 if args.slow_mode else args.batch_size
    concurrency = 2 if args.slow_mode else args.concurrency
    batch_delay = 10.0 if args.slow_mode else args.batch_delay

    print("=" * 50)
    print("subtitle-fetcher 通用轮询调度器")
    print("=" * 50)
    print(f"模式: {args.mode}")
    print(f"API: {args.base_url}")
    print(f"配置: batch_size={batch_size}, concurrency={concurrency}, batch_delay={batch_delay}s")
    print(f"轮询间隔: {args.poll_interval}s")
    print("=" * 50)

    # 构造请求体
    payload = {
        "batch_size": batch_size,
        "concurrency": concurrency,
        "batch_delay": batch_delay,
        "return_audio": args.return_audio,
        "use_asr": args.use_asr,
        "auto_start": True,
    }

    if args.mode == "channel":
        payload["channel_url"] = args.channel_url
        payload["limit"] = args.limit
        print(f"频道URL: {args.channel_url}")
        print(f"获取上限: {args.limit}")
    else:
        payload["urls"] = args.urls
        print(f"URL数量: {len(args.urls)}")

    # 创建任务
    print("\n[1/3] 创建任务...")
    task = create_task(args.base_url, args.mode, payload)
    task_id = task["task_id"]
    print(f"  ✓ 任务ID: {task_id}")
    print(f"  ✓ 总视频数: {task['total_videos']}")

    # 轮询进度
    print("\n[2/3] 轮询任务进度...")
    final_task = poll_task(args.base_url, task_id, args.poll_interval)

    # 下载结果
    print("\n[3/3] 下载结果...")
    if args.output:
        output_path = args.output
    else:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = f"/tmp/subfetch_{args.mode}_{task_id}_{ts}.{args.format}"

    download_result(args.base_url, task_id, args.format, output_path)

    # 汇总
    print("\n" + "=" * 50)
    print("调度完成")
    print(f"任务状态: {final_task['status']}")
    print(f"成功: {final_task['success_count']} / {final_task['total_videos']}")
    print(f"失败: {final_task['failed_count']}")
    print(f"结果文件: {output_path}")
    print("=" * 50)


if __name__ == "__main__":
    main()
