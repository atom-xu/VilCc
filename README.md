# VilCC

<p align="center">
  <a href="./README.md">English</a> | <a href="./README_CN.md">简体中文</a>
</p>

Give any AI the ability to read YouTube and Bilibili videos — via MCP or REST API.

Claude and other AI models can't watch videos. This service fetches subtitles (or audio fallback) and hands the text directly to the model, so it can answer questions, summarize content, or research topics without you doing any copy-paste.

## How It Works

```
You → Claude: "What are the main points in this video?"
Claude → VilCC (MCP): fetch_subtitle("https://...")
VilCC → Claude: full subtitle text
Claude → You: "Here's a summary..."
```

## Two Ways to Use

| Mode | Command | Use Case |
|------|---------|----------|
| **MCP Server** | `python mcp_server.py` | Claude / Cursor / VS Code etc. call it directly |
| **REST API** | `uvicorn main:app --port 8765` | Your own app, scripts, or other automation |

Both can run simultaneously. The REST API also serves the management UI at `http://localhost:8765`.

## Quick Start

```bash
pip install -r requirements.txt

# Start REST API + management UI
uvicorn main:app --host 0.0.0.0 --port 8765
```

Then open `http://localhost:8765` to see the dashboard and connect your AI clients.

## Management UI

The dashboard at `localhost:8765` provides:

- **Status** — server uptime, Bilibili auth status, MCP readiness
- **Connect to AI** — detects installed clients and configures MCP with one click
- **Quick Test** — paste a URL and see subtitles in the browser
- **Recent Activity** — live log of the last 50 requests

### One-Click MCP Setup

The UI auto-detects which AI tools you have installed and writes the config file for you:

| Client | Method |
|--------|--------|
| Claude Desktop | Auto-write config, restart to activate |
| Claude Code | Auto-write config, restart to activate |
| Cursor | Auto-write + deep link |
| VS Code | Auto-write + deep link |
| Windsurf | Auto-write config |
| Trae / MarsCode | Auto-write config |
| 通义灵码 | Copy JSON → paste in plugin settings |
| OpenClaw | Auto-write `~/.openclaw/openclaw.json` |

> **After configuring any client, you must restart it** for the MCP server to be loaded. The tools will not appear until the client restarts and picks up the new config.

## MCP Tools

Once connected, Claude (and any MCP-compatible client) can call these tools directly:

### `fetch_subtitle`
Fetch subtitles for a single video URL.
```
Input:  url (YouTube or Bilibili)
Output: title, platform, duration, upload_date, language, source, subtitles, segments
```

### `search_videos`
Search for videos on YouTube or Bilibili.
```
Input:  query, platform ("youtube" | "bilibili" | "all"), limit (1-20)
Output: list of videos with title, url, duration, view_count, upload_date, channel
```

### `list_channel_videos`
List all videos from a channel or Bilibili UP主 space.
```
Input:  channel_url, limit (1-50)
Output: channel name, platform, total_videos, video_urls
```

### `batch_fetch_subtitles`
Fetch subtitles for multiple URLs concurrently.
```
Input:  urls (up to 20)
Output: total, success, failed, results[]
```

## REST API

### `POST /subtitles`
```json
{ "url": "https://www.youtube.com/watch?v=xxx", "return_audio": false }
```

### `POST /subtitles/batch`
```json
{ "urls": ["url1", "url2"], "concurrency": 3, "return_audio": false }
```

### `POST /subtitles/channel`
```json
{ "channel_url": "https://www.youtube.com/@handle", "limit": 20 }
```

### `POST /search`
```json
{ "query": "keyword", "platform": "bilibili", "limit": 10 }
```

### `GET /batch/tasks/{id}` — Long-running batch tasks (pause / resume supported)

Full Swagger docs available at `http://localhost:8765/docs`.

## The `source` Field

| Value | Meaning |
|-------|---------|
| `cc` | Platform subtitles (manual or auto-generated) |
| `audio` | No subtitles; audio base64 returned for caller processing |
| `none` | No subtitles; audio not requested or unavailable |

Set `return_audio: false` (recommended for MCP usage) to skip audio and get a fast response.

## Subtitle Extraction

1. Tries platform-native API and yt-dlp **in parallel** — first CC result wins
2. Language priority: `zh-Hans` → `zh` → `zh-CN` → `en` → first available
3. Falls back to audio only if both CC paths fail and `return_audio: true`
4. Timestamps stripped, merged into clean plain text

## Bilibili Authentication

Some Bilibili videos require a logged-in session. Export cookies from your browser:

```bash
# If you're logged in to Bilibili in Chrome, yt-dlp can use those cookies automatically
# Or export manually:
yt-dlp --cookies-from-browser chrome --cookies cookies.txt "https://www.bilibili.com/video/BVxxx"
```

The management UI shows Bilibili auth status in real time.

## Tech Stack

- Python 3.10+
- FastAPI + uvicorn
- yt-dlp
- youtube-transcript-api
- bilibili-api-python
- mcp (FastMCP)

## Known Limitations

| Issue | Details |
|-------|---------|
| Bilibili channel capped at 50 videos | Unauthenticated API limit; charging-exclusive and hidden videos not counted — needs cookie for full list |
| Channel list sorts by date only | `list_channel_videos` doesn't support sorting by view count; requires a separate API call |
| Bilibili rate limiting (-799) | Rapid consecutive requests trigger throttling; batch operations should pace requests |
| MCP config requires client restart | Writing the config file doesn't notify the AI client — a manual restart is needed to load the new server |

Full details and fix roadmap in [CHANGELOG.md](./CHANGELOG.md).

## License

MIT
