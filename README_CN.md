# VilCC

<p align="center">
  <a href="./README.md">English</a> | <a href="./README_CN.md">简体中文</a>
</p>

让任何 AI 都能「看懂」YouTube 和 B站视频 —— 通过 MCP 或 REST API。

Claude 等 AI 无法直接观看视频。这个服务帮你把字幕（或音频兜底）抓下来，直接喂给模型，让它能回答问题、总结内容、研究某个博主的观点，省去手动复制粘贴的步骤。

## 工作方式

```
你 → Claude："帮我总结一下这个视频的主要观点"
Claude → VilCC（MCP）：fetch_subtitle("https://...")
VilCC → Claude：完整字幕文本
Claude → 你："以下是视频摘要..."
```

## 两种使用方式

| 模式 | 启动命令 | 适用场景 |
|------|---------|----------|
| **MCP Server** | `python mcp_server.py` | Claude / Cursor / VS Code 等直接调用 |
| **REST API** | `uvicorn main:app --port 8765` | 自己的应用、脚本或其他自动化流程 |

两种模式可以同时运行。REST API 同时提供管理界面，地址 `http://localhost:8765`。

## 快速开始

```bash
pip install -r requirements.txt

# 启动 REST API + 管理界面
uvicorn main:app --host 0.0.0.0 --port 8765
```

然后打开 `http://localhost:8765`，在管理界面里一键配置 AI 客户端。

## 管理界面

`localhost:8765` 的仪表盘提供：

- **状态** — 服务运行时长、B站登录状态、MCP 是否就绪
- **连接 AI** — 自动检测已安装的客户端，一键写入 MCP 配置
- **快速测试** — 粘贴视频链接，直接在浏览器里看字幕
- **最近活动** — 实时展示最近 50 条请求记录

### 一键配置 MCP

管理界面会自动检测你装了哪些 AI 工具，并帮你写好配置文件：

| 客户端 | 配置方式 |
|--------|---------|
| Claude Desktop | 自动写入配置文件，重启生效 |
| Claude Code | 自动写入配置文件，重启生效 |
| Cursor | 自动写入 + deep link |
| VS Code | 自动写入 + deep link |
| Windsurf | 自动写入配置文件 |
| Trae / 豆包 MarsCode | 自动写入配置文件 |
| 通义灵码 | 复制 JSON → 在插件「头像→个人设置→MCP服务」中粘贴 |
| OpenClaw | 自动写入 `~/.openclaw/openclaw.json` |

> **配置完成后必须重启对应的 AI 客户端**，MCP 工具才会生效。写入配置文件只是第一步，客户端重新启动后才会加载新的 MCP Server。

## MCP 工具

连接后，Claude（以及任何支持 MCP 的客户端）可以直接调用这 4 个工具：

### `fetch_subtitle`
抓取单个视频的字幕。
```
输入：url（YouTube 或 Bilibili）
输出：title、platform、duration、upload_date、language、source、subtitles、segments
```

### `search_videos`
在 YouTube 或 B站搜索视频。
```
输入：query、platform（"youtube" | "bilibili" | "all"）、limit（1-20）
输出：视频列表，含 title、url、duration、view_count、upload_date、channel
```

### `list_channel_videos`
列出某个频道或 B站 UP 主的所有视频。
```
输入：channel_url、limit（1-50）
输出：channel、platform、total_videos、video_urls
```

### `batch_fetch_subtitles`
并发抓取多个视频字幕。
```
输入：urls（最多 20 条）
输出：total、success、failed、results[]
```

## REST API

### `POST /subtitles`
```json
{ "url": "https://www.bilibili.com/video/BVxxx", "return_audio": false }
```

### `POST /subtitles/batch`
```json
{ "urls": ["url1", "url2"], "concurrency": 3, "return_audio": false }
```

### `POST /subtitles/channel`
```json
{ "channel_url": "https://space.bilibili.com/12345", "limit": 20 }
```

### `POST /search`
```json
{ "query": "智能家居布线", "platform": "bilibili", "limit": 10 }
```

### `GET /batch/tasks/{id}` — 长任务（支持暂停 / 恢复）

完整接口文档见 `http://localhost:8765/docs`。

## source 字段说明

| 值 | 含义 |
|----|------|
| `cc` | 平台字幕（人工字幕或自动字幕） |
| `audio` | 无字幕，已返回音频 base64 供调用方处理 |
| `none` | 无字幕，且未请求音频或音频获取失败 |

MCP 使用场景建议设置 `return_audio: false`，只取字幕，速度更快。

## 字幕抓取逻辑

1. 平台原生 API 和 yt-dlp **并行竞速**，先到先得
2. 语言优先级：`zh-Hans` → `zh` → `zh-CN` → `en` → 任意第一个
3. 两条路径都失败且 `return_audio: true` 时才降级到音频
4. 去除时间戳，合并为干净的纯文本

## B站登录认证

部分 B站视频需要登录才能获取字幕。导出浏览器 Cookie：

```bash
# 如果你在 Chrome 里已登录 B站，yt-dlp 可以直接读取
# 也可以手动导出：
yt-dlp --cookies-from-browser chrome --cookies cookies.txt "https://www.bilibili.com/video/BVxxx"
```

管理界面实时显示 B站认证状态。

## 技术栈

- Python 3.10+
- FastAPI + uvicorn
- yt-dlp
- youtube-transcript-api
- bilibili-api-python
- mcp（FastMCP）

## 已知限制

| 问题 | 说明 |
|------|------|
| B站频道最多返回 50 条 | 未登录状态下 API 限制，充电专属/隐藏视频不计入；需带 cookie 才能获取完整列表 |
| 频道列表不支持按播放量排序 | `list_channel_videos` 目前固定按发布时间；想要「播放量最高的 N 个」需额外调用 |
| B站 API 频繁调用触发风控 | 连续请求会返回 -799 错误，批量操作建议控制频率 |
| MCP 配置后必须重启客户端 | 写入配置文件后，AI 工具不会自动感知，需手动重启才能加载 |

详细说明和修复计划见 [CHANGELOG.md](./CHANGELOG.md)。

## 许可证

MIT
