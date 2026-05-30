# Changelog

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

## v0.1.0 — 2026-04-07

初始版本，FastAPI 字幕抓取服务。

- CC 字幕获取，支持 YouTube / Bilibili
- 无字幕时返回音频 base64 供 AI 处理
- 批量处理（最多 20 条并发）
- 频道 / UP主 视频列表抓取
- 批量导出（zip / json / txt / md）
- 内置 YouTube / Bilibili 搜索
