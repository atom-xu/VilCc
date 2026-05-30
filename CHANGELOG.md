# Changelog

## v0.3.0 — 开发中

### 新增：多平台社交媒体聚合（MediaCrawler 集成）

**支持平台：** 小红书、微博、抖音、快手、B站、知乎、贴吧

**新增 MCP 工具：**
- `search_social(keyword, platform, limit)` — 在社交平台搜索内容
- `get_creator_posts(creator_url, platform, limit)` — 获取博主所有帖子

**新增 REST 接口：**
- `POST /social/search` — 关键词搜索
- `POST /social/creator` — 博主主页爬取
- `GET /api/social/status` — 各平台 Cookie 状态
- `POST /api/social/cookies` — 设置平台 Cookie

**技术方案：**
- MediaCrawler 作为 git submodule（`media_crawler/`）
- 全局 asyncio lock 保证 config 安全
- Playwright + headless Chrome

**安装依赖：**
```bash
git submodule update --init media_crawler
pip install -r media_crawler/requirements.txt
playwright install chromium
```

---

## v0.2.0 — 2026-05-30

### 项目正式命名为 VilCC

本版本将项目从通用字幕抓取服务升级为 **AI 原生工具**，核心变化是接入 MCP 协议，让 Claude 等 AI 客户端可以直接调用字幕抓取能力，无需手动操作。

### 新增

**MCP Server (`mcp_server.py`)**
- 通过 FastMCP 暴露 4 个工具：`fetch_subtitle`、`search_videos`、`list_channel_videos`、`batch_fetch_subtitles`
- 自动剥除 `audio_base64`，减少 token 消耗
- 支持 stdio 传输，兼容所有 MCP 客户端

**管理界面 (`localhost:8765`)**
- 深色主题单页仪表盘，服务状态、B站认证、MCP 状态一览
- Quick Test：粘贴链接直接预览字幕，支持下载 TXT / JSON
- 最近活动日志：实时展示最近 50 条请求记录

**一键配置 MCP（8 个 AI 客户端）**
- 自动检测已安装的客户端并写入配置文件
- 支持：Claude Desktop、Claude Code、Cursor（含 deep link）、VS Code（含 deep link）、Windsurf、Trae / 豆包 MarsCode、OpenClaw
- 通义灵码：无配置文件，显示复制 JSON 并引导在插件内粘贴
- 配置完成后需重启对应客户端才生效

**工具脚本**
- `parallel_fetch.py`：多实例并行字幕获取，适合大批量任务
- `scheduler_polling.py`：长周期轮询调度，规避 B站风控

### 改进

**字幕抓取（`fetcher.py`）**
- racing 模式：平台原生 API 与 yt-dlp 并行竞速，先到先得，大幅降低延迟
- 修复 Bilibili 分页字段 bug：API 返回 `count`（总数）而非 `pagecount`，之前只能拉到第一页
- 修复 retry 包裹翻页循环的反模式：改为逐页单独重试，避免触发更多风控
- yt-dlp 全局加 `socket_timeout` + `retries`，杜绝无限挂起
- 适配 youtube-transcript-api v1.x：改用实例化调用方式

**批量任务（`batch_executor.py`）**
- 修复 `resume_task` 从头重跑 bug：恢复时从上次中断的 batch 继续，不再从第 0 条重新处理

### 文档

- 重写中英文 README，以「让 AI 看视频」为主线
- 补充配置 MCP 后必须重启客户端的说明

---

## 已知问题 & 待优化（v0.3.0 方向）

以下问题在 v0.2.0 实际使用中暴露，记录备查：

### 1. B站 API 风控（-799）
频繁调用 Bilibili API 会触发 `-799 请求过于频繁` 错误。
目前翻页间有 1s 延迟，但连续多次独立请求之间没有全局限速。
**待优化**：加全局请求队列或指数退避，批量操作前自动等待。

### 2. `VideoOrder` 枚举值需运行时确认
`bilibili-api-python` 不同版本枚举值不同。
本次发现 `VideoOrder.CLICK` 不存在，实际应为 `VideoOrder.VIEW`；
`VideoOrder.PUBDATE` 在某些版本也会报错，默认不传 order 参数反而更稳定。
**待优化**：在 fetcher 初始化时做枚举值探测，或锁定 requirements.txt 版本。

### 3. `list_channel_videos` 不支持按播放量排序
`get_bilibili_video_list` 目前固定按发布时间拉取，
用户想要「播放量最多的 N 个视频」需要在外层额外调 API。
**待优化**：给 `fetch_channel_videos` 加 `order` 参数（`pubdate` / `view`），透传给 B站 API。

### 4. B站频道视频数量受限
未登录或低权限状态下，B站 API 最多返回 50 条公开视频。
充电专属、隐藏视频不计入 `page.count`，需要带登录 cookie 才能看到完整数量。
**待优化**：在 `fetch_channel_videos` 返回结果里注明 `is_partial: true` 并提示用户检查认证状态。

### 5. MCP 工具配置后需重启才生效
一键配置写入文件后，AI 客户端必须重启才能加载新的 MCP Server。
目前管理界面只提示「重启」，但没有检测工具是否真的已经上线。
**待优化**：增加 MCP 连通性检测（如 ping mcp server），在界面上显示「已连接 / 未连接」的实时状态。

---

## v0.1.0 — 2026-04-07

初始版本，FastAPI 字幕抓取服务。

- CC 字幕获取，支持 YouTube / Bilibili
- 无字幕时返回音频 base64 供 AI 处理
- 批量处理（最多 20 条并发）
- 频道 / UP主 视频列表抓取
- 批量导出（zip / json / txt / md）
- 内置 YouTube / Bilibili 搜索
