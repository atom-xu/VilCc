#!/usr/bin/env python3
"""
多实例并行字幕获取工具

启动多个 subtitle-fetcher 服务实例，分割任务并行处理，大幅提升效率。

使用方法：
1. 启动多个实例：python parallel_fetch.py start --instances 3
2. 并行获取字幕：python parallel_fetch.py fetch --channel https://space.bilibili.com/259532200 --limit 100
3. 查看状态：python parallel_fetch.py status
4. 停止所有实例：python parallel_fetch.py stop
"""

import argparse
import json
import os
import subprocess
import sys
import time
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Dict, Optional
import signal

# 默认端口范围
DEFAULT_PORT_START = 8765
DEFAULT_INSTANCES = 3

# 进程管理
_processes: Dict[int, subprocess.Popen] = {}


def start_instances(count: int = DEFAULT_INSTANCES, port_start: int = DEFAULT_PORT_START) -> Dict[int, str]:
    """启动多个服务实例"""
    instances = {}
    
    for i in range(count):
        port = port_start + i
        print(f"启动实例 {i+1}，端口 {port}...")
        
        proc = subprocess.Popen(
            ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", str(port)],
            cwd=os.path.dirname(os.path.abspath(__file__)) or ".",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            preexec_fn=os.setpgrp if hasattr(os, 'setpgrp') else None
        )
        
        _processes[port] = proc
        instances[port] = f"http://localhost:{port}"
    
    # 等待服务启动
    print(f"等待 {count} 个实例启动...")
    time.sleep(3)
    
    # 检查健康状态
    healthy = []
    for port, url in instances.items():
        try:
            resp = requests.get(f"{url}/health", timeout=5)
            if resp.json().get("status") == "ok":
                healthy.append(port)
                print(f"  ✅ 端口 {port} 已就绪")
            else:
                print(f"  ❌ 端口 {port} 异常")
        except Exception as e:
            print(f"  ❌ 端口 {port} 连接失败: {e}")
    
    return {p: instances[p] for p in healthy}


def stop_instances():
    """停止所有实例"""
    global _processes
    
    for port, proc in _processes.items():
        print(f"停止端口 {port}...")
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except:
            proc.kill()
    
    _processes.clear()
    print("所有实例已停止")


def get_video_list(channel_url: str, limit: int = 100, use_cookie: bool = True) -> List[str]:
    """获取频道完整视频列表（优先使用 bilibili_api，带风控处理）"""
    import re
    import time
    
    # 检测平台
    if "bilibili.com" in channel_url or "b23.tv" in channel_url:
        # B站：使用 bilibili_api 获取完整列表
        uid_match = re.search(r'space\.bilibili\.com/(\d+)', channel_url)
        if uid_match:
            uid = int(uid_match.group(1))
            print(f"使用 bilibili_api 获取 UID:{uid} 的视频列表...")
            
            try:
                from bilibili_api import user, sync, settings
                # 设置代理和延迟以规避风控
                settings.timeout = 30
                
                u = user.User(uid)
                
                # 分页获取，每页30个，添加延迟
                urls = []
                page = 1
                max_retries = 3
                
                while len(urls) < limit:
                    retry_count = 0
                    while retry_count < max_retries:
                        try:
                            videos = sync(u.get_videos(ps=30, pn=page))
                            vlist = videos.get('list', {}).get('vlist', [])
                            
                            if not vlist:
                                break
                            
                            for v in vlist:
                                bvid = v.get('bvid')
                                if bvid:
                                    urls.append(f"https://www.bilibili.com/video/{bvid}")
                            
                            print(f"  已获取 {len(urls)} 个视频...")
                            
                            # 添加延迟规避风控
                            time.sleep(2)
                            break
                            
                        except Exception as e:
                            if "412" in str(e) or "风控" in str(e):
                                retry_count += 1
                                wait_time = 10 * retry_count
                                print(f"  触发风控，等待 {wait_time} 秒后重试...")
                                time.sleep(wait_time)
                            else:
                                raise
                    
                    if retry_count >= max_retries:
                        print(f"  风控重试失败，使用已获取的 {len(urls)} 个视频")
                        break
                    
                    page += 1
                    if len(vlist) < 30:  # 没有更多视频
                        break
                
                print(f"获取到 {len(urls)} 个视频URL")
                return urls[:limit]
                
            except ImportError:
                print("bilibili_api 未安装，使用 yt-dlp fallback")
            except Exception as e:
                print(f"bilibili_api 失败: {e}")
    
    # Fallback: 使用 yt-dlp（带cookie）
    import yt_dlp
    
    cookie_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cookies.txt")
    
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "extract_flat": True,
        "playlistend": limit,
        "nocheckcertificate": True,
    }
    
    # 添加cookie规避风控
    if use_cookie and os.path.exists(cookie_file):
        ydl_opts["cookiefile"] = cookie_file
        print("使用 cookies.txt 规避风控")
    elif use_cookie:
        ydl_opts["cookiesfrombrowser"] = ("chrome", None, None, None)
        print("从 Chrome 提取 cookie")
    
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(channel_url, download=False)
    except Exception as e:
        if "412" in str(e):
            print(f"触发风控，等待 30 秒后重试...")
            time.sleep(30)
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(channel_url, download=False)
        else:
            raise
    
    urls = []
    for entry in info.get("entries", [])[:limit]:
        url = entry.get("url") or entry.get("webpage_url")
        if url and not url.startswith("http"):
            if "bilibili" in channel_url:
                url = f"https://www.bilibili.com/video/{url}"
            else:
                url = f"https://www.youtube.com/watch?v={url}"
        if url:
            urls.append(url)
    
    return urls


def split_urls(urls: List[str], chunks: int) -> List[List[str]]:
    """分割URL列表"""
    chunk_size = len(urls) // chunks + 1
    return [urls[i:i+chunk_size] for i in range(0, len(urls), chunk_size)]


def fetch_from_instance(instance_url: str, urls: List[str], concurrency: int = 3) -> Dict:
    """从单个实例获取字幕"""
    try:
        resp = requests.post(
            f"{instance_url}/subtitles/batch",
            json={
                "urls": urls,
                "concurrency": concurrency,
                "return_audio": False
            },
            timeout=300
        )
        return resp.json()
    except Exception as e:
        return {"error": str(e), "urls": urls}


def parallel_fetch(
    channel_url: str,
    limit: int = 100,
    instances: int = DEFAULT_INSTANCES,
    port_start: int = DEFAULT_PORT_START,
    concurrency: int = 3
) -> Dict:
    """并行获取字幕"""
    
    # 1. 启动实例
    instance_urls = start_instances(instances, port_start)
    if not instance_urls:
        return {"error": "无法启动任何实例"}
    
    # 2. 获取视频列表
    urls = get_video_list(channel_url, limit)
    if not urls:
        return {"error": "无法获取视频列表"}
    
    # 3. 分割任务
    chunks = split_urls(urls, len(instance_urls))
    print(f"分割为 {len(chunks)} 个任务块:")
    for i, chunk in enumerate(chunks):
        print(f"  块 {i+1}: {len(chunk)} 个视频")
    
    # 4. 并行执行
    print("开始并行获取字幕...")
    results = []
    
    with ThreadPoolExecutor(max_workers=len(instance_urls)) as executor:
        futures = {}
        for i, (port, url) in enumerate(instance_urls.items()):
            chunk = chunks[i] if i < len(chunks) else []
            if chunk:
                futures[executor.submit(fetch_from_instance, url, chunk, concurrency)] = port
        
        for future in as_completed(futures):
            port = futures[future]
            try:
                result = future.result()
                print(f"  ✅ 端口 {port} 完成: {result.get('success', 0)} 成功")
                results.append(result)
            except Exception as e:
                print(f"  ❌ 端口 {port} 失败: {e}")
    
    # 5. 合并结果
    all_results = []
    total_success = 0
    total_failed = 0
    
    for r in results:
        if "results" in r:
            all_results.extend(r["results"])
            total_success += r.get("success", 0)
            total_failed += r.get("failed", 0)
    
    print(f"\n总计: {total_success} 成功, {total_failed} 失败")
    
    return {
        "total": len(urls),
        "success": total_success,
        "failed": total_failed,
        "results": all_results,
        "instances": list(instance_urls.keys())
    }


def export_results(results: Dict, format: str = "zip", output: str = None) -> str:
    """导出结果"""
    if not output:
        output = f"subtitles_{int(time.time())}.{format}"
    
    if format == "json":
        with open(output, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
        return output
    
    elif format == "txt":
        lines = []
        for r in results.get("results", []):
            if r.get("subtitles"):
                lines.append(f"=== {r.get('title', '未知')} ===")
                lines.append(r["subtitles"])
                lines.append("")
        
        with open(output, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        return output
    
    elif format == "zip":
        import zipfile
        import io
        
        with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as zf:
            for i, r in enumerate(results.get("results", []), 1):
                if r.get("subtitles"):
                    title = r.get("title", f"video_{i}")
                    # 安全文件名
                    safe_title = "".join(c if c.isalnum() or c in " _-" else "_" for c in title)
                    zf.writestr(f"{i}_{safe_title}.md", f"# {title}\n\n{r['subtitles']}")
        
        return output
    
    return output


def main():
    parser = argparse.ArgumentParser(description="多实例并行字幕获取工具")
    subparsers = parser.add_subparsers(dest="command", help="命令")
    
    # start 命令
    start_parser = subparsers.add_parser("start", help="启动多个实例")
    start_parser.add_argument("--instances", type=int, default=DEFAULT_INSTANCES, help="实例数量")
    start_parser.add_argument("--port-start", type=int, default=DEFAULT_PORT_START, help="起始端口")
    
    # fetch 命令
    fetch_parser = subparsers.add_parser("fetch", help="并行获取字幕")
    fetch_parser.add_argument("--channel", required=True, help="频道URL")
    fetch_parser.add_argument("--limit", type=int, default=100, help="视频数量限制")
    fetch_parser.add_argument("--instances", type=int, default=DEFAULT_INSTANCES, help="实例数量")
    fetch_parser.add_argument("--port-start", type=int, default=DEFAULT_PORT_START, help="起始端口")
    fetch_parser.add_argument("--concurrency", type=int, default=3, help="每个实例的并发数")
    fetch_parser.add_argument("--format", choices=["json", "txt", "zip"], default="zip", help="导出格式")
    fetch_parser.add_argument("--output", help="输出文件路径")
    
    # stop 命令
    stop_parser = subparsers.add_parser("stop", help="停止所有实例")
    
    # status 命令
    status_parser = subparsers.add_parser("status", help="查看实例状态")
    status_parser.add_argument("--port-start", type=int, default=DEFAULT_PORT_START, help="起始端口")
    status_parser.add_argument("--instances", type=int, default=DEFAULT_INSTANCES, help="检查的实例数量")
    
    args = parser.parse_args()
    
    if args.command == "start":
        start_instances(args.instances, args.port_start)
        print("实例已启动，按 Ctrl+C 停止")
        try:
            signal.pause()
        except KeyboardInterrupt:
            stop_instances()
    
    elif args.command == "fetch":
        results = parallel_fetch(
            args.channel,
            args.limit,
            args.instances,
            args.port_start,
            args.concurrency
        )
        
        if "error" not in results:
            output = export_results(results, args.format, args.output)
            print(f"\n✅ 已导出到: {output}")
        
        # 停止实例
        stop_instances()
    
    elif args.command == "stop":
        stop_instances()
    
    elif args.command == "status":
        for i in range(args.instances):
            port = args.port_start + i
            try:
                resp = requests.get(f"http://localhost:{port}/health", timeout=2)
                status = "✅ 运行中" if resp.json().get("status") == "ok" else "❌ 异常"
            except:
                status = "❌ 未运行"
            print(f"端口 {port}: {status}")
    
    else:
        parser.print_help()


if __name__ == "__main__":
    main()