"""
8小时智能调度系统 - 分散请求避免Bilibili限流

策略：
- 885视频分9批，每批约100个
- 8小时 = 480分钟
- 每批间隔：50-60分钟（随机避免规律）
- 每批内部：batch_size=3, concurrency=2（低速模式）
"""

import asyncio
import json
import random
import time
from datetime import datetime, timedelta
import requests

BASE_URL = "http://localhost:8000"
TOTAL_BATCHES = 9
BATCH_INTERVAL_MIN = 50  # 最小间隔50分钟
BATCH_INTERVAL_MAX = 60  # 最大间隔60分钟

def load_urls():
    """加载所有视频URL"""
    with open('/tmp/ysjf_all_urls.txt', 'r') as f:
        urls = [line.strip() for line in f if line.strip()]
    return urls

def get_batch(urls, batch_index):
    """获取指定批次的URL"""
    start = batch_index * 100
    end = min(start + 100, len(urls))
    return urls[start:end]

async def create_and_wait_batch(urls, batch_name):
    """创建批次任务并等待完成"""
    print(f"\n[{datetime.now().strftime('%H:%M:%S')}] 开始创建 {batch_name} ({len(urls)}个视频)...")

    # 创建任务 - 低速模式
    resp = requests.post(f"{BASE_URL}/batch/tasks/urls", json={
        "urls": urls,
        "batch_size": 3,      # 每批3个，更慢
        "concurrency": 2,     # 并发2个，更低
        "use_asr": False
    })

    if resp.status_code != 200:
        print(f"  ✗ 创建失败: {resp.text}")
        return None

    task = resp.json()
    task_id = task['task_id']
    print(f"  ✓ 任务创建: {task_id}")

    # 等待完成
    while True:
        await asyncio.sleep(30)  # 每30秒检查一次

        status_resp = requests.get(f"{BASE_URL}/batch/tasks/{task_id}")
        if status_resp.status_code != 200:
            continue

        status = status_resp.json()
        processed = status['processed_videos']
        total = status['total_videos']
        success = status['success_count']

        print(f"  [{datetime.now().strftime('%H:%M:%S')}] 进度: {processed}/{total} | 成功: {success}")

        if status['status'] in ['completed', 'failed']:
            print(f"  ✓ {batch_name} 完成: 成功 {success}/{total}")
            return task_id

        # 如果卡住超过5分钟没有进度，也视为完成
        # （实际由batch_executor的90秒超时处理）

async def export_batch(task_id, batch_num):
    """导出批次结果"""
    print(f"  导出 {task_id} 结果...")
    resp = requests.get(f"{BASE_URL}/batch/tasks/{task_id}/results?format=txt")
    if resp.status_code == 200:
        filename = f"/tmp/ysjf_batch_{batch_num}_{task_id}.txt"
        with open(filename, 'wb') as f:
            f.write(resp.content)
        print(f"  ✓ 已保存: {filename}")
        return filename
    return None

async def run_scheduler():
    """主调度器"""
    urls = load_urls()
    print(f"=== 8小时智能调度系统启动 ===")
    print(f"总视频数: {len(urls)}")
    print(f"分批数: {TOTAL_BATCHES}")
    print(f"预计完成时间: {(datetime.now() + timedelta(hours=8)).strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"=" * 50)

    completed_batches = []
    failed_batches = []

    for i in range(TOTAL_BATCHES):
        batch_name = f"批次{i+1}/9"
        batch_urls = get_batch(urls, i)

        if not batch_urls:
            print(f"{batch_name} 无视频，跳过")
            continue

        # 执行当前批次
        task_id = await create_and_wait_batch(batch_urls, batch_name)

        if task_id:
            # 导出结果
            filename = await export_batch(task_id, i+1)
            if filename:
                completed_batches.append((i+1, task_id, filename))
        else:
            failed_batches.append(i+1)

        # 如果不是最后一批，等待
        if i < TOTAL_BATCHES - 1:
            wait_minutes = random.randint(BATCH_INTERVAL_MIN, BATCH_INTERVAL_MAX)
            next_run = datetime.now() + timedelta(minutes=wait_minutes)
            print(f"\n  ⏱  等待 {wait_minutes} 分钟后开始下一批...")
            print(f"  📅 下次执行: {next_run.strftime('%H:%M:%S')}")
            await asyncio.sleep(wait_minutes * 60)

    # 汇总
    print(f"\n" + "=" * 50)
    print(f"=== 所有批次执行完成 ===")
    print(f"完成批次: {len(completed_batches)}/9")
    print(f"失败批次: {failed_batches if failed_batches else '无'}")
    print(f"\n结果文件:")
    for num, task_id, filename in completed_batches:
        print(f"  批次{num}: {filename}")

    return completed_batches

async def merge_all_results():
    """合并所有批次结果"""
    print(f"\n=== 合并所有结果 ===")

    import glob
    files = sorted(glob.glob("/tmp/ysjf_batch_*.txt"))

    if not files:
        print("没有找到结果文件")
        return

    total_success = 0
    with open("/tmp/影视飓风_全字幕_8小时任务.txt", "wb") as outfile:
        for f in files:
            with open(f, "rb") as infile:
                outfile.write(infile.read())
                outfile.write(b"\n\n")
            # 统计成功数
            with open(f, "r") as check:
                content = check.read()
                total_success += content.count("来源：ai_subtitle")

    final_file = "/tmp/影视飓风_全字幕_8小时任务.txt"
    size = "%.1fM" % (sum(len(open(f, "rb").read()) for f in files) / 1024 / 1024)
    print(f"✓ 合并完成: {final_file}")
    print(f"  文件大小: {size}")
    print(f"  成功提取: 约 {total_success} 个视频")
    print(f"  包含批次: {len(files)} 个")

    # 复制到桌面
    import shutil
    shutil.copy(final_file, "/Users/atom/Desktop/影视飓风_全字幕.txt")
    print(f"  ✓ 已复制到桌面")

if __name__ == "__main__":
    # 运行调度器
    batches = asyncio.run(run_scheduler())

    # 合并结果
    if batches:
        asyncio.run(merge_all_results())

    print(f"\n{'='*50}")
    print(f"任务完成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
