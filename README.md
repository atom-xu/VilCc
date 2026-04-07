# Subtitle Fetcher API

极简 FastAPI 服务，接收视频 URL，返回字幕纯文本或音频。支持 YouTube 和 B站。

## 特性

- **CC 字幕获取**：YouTube / B站 优先获取人工字幕和自动字幕
- **音频兜底**：无字幕视频可返回音频 base64，供调用方（Claude/Kimi 等）自行处理
- **批量处理**：支持最多 20 条 URL 并发获取
- **频道抓取**：支持 YouTube 频道视频列表获取字幕
- **批量导出**：支持 zip/json/txt/md 四种格式导出
- **视频搜索**：内置 YouTube/B站 搜索，无需外部搜索引擎

## 环境配置

### 1. 基础依赖

```bash
# 安装依赖
pip install -r requirements.txt
```

### 2. 启动服务

```bash
uvicorn main:app --host 0.0.0.0 --port 8765
```

服务将在 `http://localhost:8765` 运行。

## source 字段说明

API 响应中的 `source` 字段表示字幕来源：

| source | 含义 | 说明 |
|--------|------|------|
| `cc` | 平台字幕 | 从视频平台获取的字幕（人工字幕或自动字幕） |
| `audio` | 音频 | 无字幕，已返回音频 base64 供调用方处理 |
| `none` | 无内容 | 无字幕，且未请求音频或音频获取失败 |

## return_audio 参数

- `return_audio: true`（默认）：无字幕时下载音频并返回 base64
- `return_audio: false`：无字幕时直接返回 `source: "none"`

适用场景：
- **支持多模态的 AI（Claude、Kimi 等）**：设置 `return_audio: true`，拿到 base64 直接喂给模型
- **纯文本场景**：设置 `return_audio: false`，跳过无字幕视频

## API 接口

### POST /subtitles

获取单条视频字幕。

**请求体：**
```json
{
    "url": "https://www.youtube.com/watch?v=xxx",
    "return_audio": true
}
```

**参数说明：**
- `url`: 视频完整 URL
- `return_audio`: 无字幕时是否返回音频 base64，默认 `true`

**成功响应 (200)：**
```json
{
    "title": "视频标题",
    "platform": "youtube",
    "duration": 845,
    "language": "zh-Hans",
    "source": "cc",
    "subtitles": "字幕纯文本内容...",
    "audio_base64": null
}
```

**source 字段说明：**
- `"cc"`：来自 CC 字幕（人工字幕或自动字幕）
- `"audio"`：无字幕，返回音频 base64
- `"none"`：无字幕，未返回音频

### POST /subtitles/batch

批量获取多个视频的字幕。

**请求体：**
```json
{
    "urls": [
        "https://www.youtube.com/watch?v=xxx",
        "https://www.bilibili.com/video/BVxxx"
    ],
    "concurrency": 3,
    "return_audio": true
}
```

**参数说明：**
- `urls`: URL 列表，最多 20 条
- `concurrency`: 并发数，默认 3，最大 5
- `return_audio`: 无字幕时是否返回音频 base64，默认 `true`

### POST /subtitles/channel

获取频道/UP主的历史视频字幕。

**请求体：**
```json
{
    "channel_url": "https://www.youtube.com/@mkbhd",
    "limit": 20,
    "concurrency": 3,
    "return_audio": true
}
```

**参数说明：**
- `channel_url`: YouTube 频道 URL (`@handle` 或 `/channel/UCxxx`) 或 B站空间 URL
- `limit`: 获取最近 N 条视频，默认 20，最大 50
- `concurrency`: 并发数，默认 3
- `return_audio`: 无字幕时是否返回音频 base64，默认 `true`

### POST /subtitles/batch/export

批量导出视频字幕为文件。

**请求体：**
```json
{
    "urls": ["url1", "url2", ...],
    "format": "zip",
    "concurrency": 3,
    "return_audio": true
}
```

**参数说明：**
- `urls`: 视频 URL 列表，最多 20 条
- `format`: 导出格式，`zip` | `json` | `txt`，默认 `zip`
- `concurrency`: 并发数，默认 3
- `return_audio`: 无字幕时是否返回音频 base64，默认 `true`

**返回：** 直接返回文件流，Content-Disposition 包含文件名

**格式说明：**
- **zip**: 每条视频一个 `.md` 文件，文件名格式 `{序号}_{视频标题}.md`
- **json**: 完整的 JSON 结构，同 `/subtitles/batch` 接口
- **txt**: 所有字幕合并为单个文件，视频间用分隔符隔开

### POST /subtitles/channel/export

导出整个频道的视频字幕为文件。

**请求体：**
```json
{
    "channel_url": "https://www.youtube.com/@mkbhd",
    "limit": 20,
    "format": "zip",
    "concurrency": 3,
    "return_audio": true
}
```

**参数说明：**
- `channel_url`: 频道主页 URL
- `limit`: 获取最近 N 条视频，默认 20，最大 50
- `format`: 导出格式，`zip` | `json` | `txt`，默认 `zip`
- `concurrency`: 并发数，默认 3
- `return_audio`: 无字幕时是否返回音频 base64，默认 `true`

### POST /search

搜索 YouTube 或 B站视频。

**请求体：**
```json
{
    "query": "MacBook Pro 购买建议",
    "platform": "youtube",
    "limit": 10
}
```

**参数说明：**
- `query`: 搜索关键词
- `platform`: 搜索平台，`youtube` | `bilibili` | `all`，默认 `youtube`
- `limit`: 返回结果数量，默认 10，最大 20

**成功响应 (200)：**
```json
{
    "query": "MacBook Pro 购买建议",
    "platform": "youtube",
    "total": 10,
    "results": [
        {
            "title": "MacBook Pro 2024 真实使用3个月体验",
            "url": "https://www.youtube.com/watch?v=xxx",
            "platform": "youtube",
            "duration": 845,
            "view_count": 234521,
            "upload_date": "2024-11-03",
            "channel": "MKBHD"
        }
    ]
}
```

### GET /health

健康检查。

**响应：**
```json
{
    "status": "ok"
}
```

## 本地测试

### 健康检查

```bash
curl http://localhost:8765/health
```

### YouTube 测试

```bash
curl -X POST http://localhost:8765/subtitles \
  -H "Content-Type: application/json" \
  -d '{"url": "https://www.youtube.com/watch?v=jNQXAC9IVRw"}'
```

### B站测试（有 CC 字幕）

```bash
curl -X POST http://localhost:8765/subtitles \
  -H "Content-Type: application/json" \
  -d '{"url": "https://www.bilibili.com/video/BVxxx"}'
```

### B站测试（无字幕，返回音频）

```bash
curl -X POST http://localhost:8765/subtitles \
  -H "Content-Type: application/json" \
  -d '{"url": "https://www.bilibili.com/video/BV1GJ411x7h7", "return_audio": true}'
```

### 不返回音频（只获取有字幕的视频）

```bash
curl -X POST http://localhost:8765/subtitles \
  -H "Content-Type: application/json" \
  -d '{"url": "https://www.bilibili.com/video/BVxxx", "return_audio": false}'
```

### 批量接口测试

```bash
curl -X POST http://localhost:8765/subtitles/batch \
  -H "Content-Type: application/json" \
  -d '{
    "urls": [
        "https://www.youtube.com/watch?v=jNQXAC9IVRw",
        "https://www.youtube.com/watch?v=invalid12345"
    ],
    "concurrency": 2,
    "return_audio": true
  }'
```

### 导出频道字幕为 zip

```bash
curl -X POST http://localhost:8765/subtitles/channel/export \
  -H "Content-Type: application/json" \
  -d '{"channel_url": "https://www.youtube.com/@mkbhd", "limit": 5, "format": "zip"}' \
  --output subtitles.zip
```

### 批量导出为 txt

```bash
curl -X POST http://localhost:8765/subtitles/batch/export \
  -H "Content-Type: application/json" \
  -d '{
    "urls": [
        "https://www.youtube.com/watch?v=jNQXAC9IVRw"
    ],
    "format": "txt"
  }' \
  --output subtitles.txt
```

### 搜索 YouTube 视频

```bash
curl -X POST http://localhost:8765/search \
  -H "Content-Type: application/json" \
  -d '{"query": "MacBook Pro 购买建议", "platform": "youtube", "limit": 5}'
```

### 搜索 B站视频

```bash
curl -X POST http://localhost:8765/search \
  -H "Content-Type: application/json" \
  -d '{"query": "MacBook Pro 购买建议", "platform": "bilibili", "limit": 5}'
```

## 完整使用链路示例

```bash
# Step 1: 搜索视频
urls=$(curl -s -X POST http://localhost:8765/search \
  -H "Content-Type: application/json" \
  -d '{"query": "Python 教程", "platform": "youtube", "limit": 3}' | \
  python3 -c "import sys,json; print('\n'.join([r['url'] for r in json.load(sys.stdin)['results']]))")

# Step 2: 批量获取字幕
echo '{"urls": ["'"$(echo $urls | sed 's/ /","/g')"'"], "return_audio": true}' | \
  curl -X POST http://localhost:8765/subtitles/batch \
  -H "Content-Type: application/json" \
  -d @-
```

## Claude Tool Schema

### 单条字幕获取

```json
{
  "name": "get_subtitles",
  "description": "获取视频字幕，支持 YouTube 和 B站。无字幕时可返回音频供调用方处理。输入视频URL，返回字幕纯文本和视频元数据。",
  "input_schema": {
    "type": "object",
    "properties": {
      "url": {
        "type": "string",
        "description": "视频完整URL，支持 YouTube 和 Bilibili"
      },
      "return_audio": {
        "type": "boolean",
        "description": "无字幕时是否返回音频base64，默认true"
      }
    },
    "required": ["url"]
  }
}
```

### 批量字幕获取

```json
{
  "name": "get_subtitles_batch",
  "description": "批量获取多个视频的字幕，支持 YouTube 和 B站。无字幕时可返回音频供调用方处理。单条失败不影响整体。",
  "input_schema": {
    "type": "object",
    "properties": {
      "urls": {
        "type": "array",
        "items": { "type": "string" },
        "description": "视频URL列表，最多20条"
      },
      "concurrency": {
        "type": "integer",
        "description": "并发数，默认3，最大5"
      },
      "return_audio": {
        "type": "boolean",
        "description": "无字幕时是否返回音频base64，默认true"
      }
    },
    "required": ["urls"]
  }
}
```

### 频道字幕获取

```json
{
  "name": "get_channel_subtitles",
  "description": "获取某个 YouTube 频道或 B站 UP 主的历史视频字幕，用于内容分析。无字幕时可返回音频供调用方处理。",
  "input_schema": {
    "type": "object",
    "properties": {
      "channel_url": {
        "type": "string",
        "description": "频道主页URL，支持 YouTube @handle 和 B站 space.bilibili.com/uid"
      },
      "limit": {
        "type": "integer",
        "description": "获取最近N条视频，默认20，最大50"
      },
      "concurrency": {
        "type": "integer",
        "description": "并发数，默认3，最大5"
      },
      "return_audio": {
        "type": "boolean",
        "description": "无字幕时是否返回音频base64，默认true"
      }
    },
    "required": ["channel_url"]
  }
}
```

### 视频搜索

```json
{
  "name": "search_videos",
  "description": "在 YouTube 或 B站搜索视频，返回视频列表和URL。搜索后可配合 get_subtitles_batch 获取字幕内容。",
  "input_schema": {
    "type": "object",
    "properties": {
      "query": {
        "type": "string",
        "description": "搜索关键词"
      },
      "platform": {
        "type": "string",
        "enum": ["youtube", "bilibili", "all"],
        "description": "搜索平台，默认 youtube"
      },
      "limit": {
        "type": "integer",
        "description": "返回结果数量，默认10，最大20"
      }
    },
    "required": ["query"]
  }
}
```

## 导出格式选择建议

| 格式 | 适用场景 |
|------|----------|
| **zip** | 人工逐条查阅，每个视频独立 `.md` 文件，含元数据 |
| **json** | 二次程序处理，结构完整，含所有字段 |
| **txt** | 全文关键词搜索，快速浏览多个视频内容 |

## 技术栈

- Python 3.10+
- FastAPI + uvicorn
- yt-dlp
- dashscope (阿里云语音识别)
- 无数据库，无鉴权，本地运行

## 字幕获取逻辑

1. 优先获取人工 CC 字幕，fallback 到自动生成的字幕
2. 语言优先级：`zh-Hans` → `zh` → `zh-CN` → `en` → 任意第一个
3. 字幕格式优先：`json3` → `vtt` → `srt`
4. 去除时间戳，合并为纯文本段落
5. B站无字幕视频：如 return_audio=true，下载音频 → 返回 base64

## 注意事项

- **音频返回**：无字幕时可返回音频供调用方处理
- **临时文件清理**：音频处理完成后自动删除临时文件
- **API Key 安全**：`.env` 文件不要提交到版本控制
