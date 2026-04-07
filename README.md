# Subtitle Fetcher API

<p align="center">
  <a href="./README.md">English</a> | <a href="./README_CN.md">简体中文</a>
</p>

A minimalist FastAPI service that receives video URLs and returns subtitles or audio. Supports YouTube and Bilibili.

## Features

- **CC Subtitle Extraction**: Priority access to manual and auto-generated subtitles from YouTube/Bilibili
- **Audio Fallback**: Returns audio as base64 for videos without subtitles, letting callers (Claude/Kimi, etc.) process it
- **Batch Processing**: Supports concurrent fetching for up to 20 URLs
- **Channel Scraping**: Fetch subtitles from YouTube channel or Bilibili UP主 video lists
- **Batch Export**: Export in zip/json/txt/md formats
- **Video Search**: Built-in YouTube/Bilibili search, no external search engine needed

## Quick Start

```bash
# Install dependencies
pip install -r requirements.txt

# Start the service
uvicorn main:app --host 0.0.0.0 --port 8765
```

The service will run at `http://localhost:8765`.

## The `source` Field

The `source` field in API responses indicates the subtitle origin:

| source | Meaning | Description |
|--------|---------|-------------|
| `cc` | Platform Subtitles | Subtitles from the video platform (manual or auto-generated) |
| `audio` | Audio | No subtitles; audio base64 returned for caller processing |
| `none` | No Content | No subtitles; audio not requested or failed to fetch |

## The `return_audio` Parameter

- `return_audio: true` (default): Downloads and returns audio as base64 when no subtitles are available
- `return_audio: false`: Returns `source: "none"` when no subtitles are available

Use cases:
- **Multimodal AI (Claude, Kimi, etc.)**: Set `return_audio: true`, feed the base64 directly to the model
- **Text-only scenarios**: Set `return_audio: false` to skip videos without subtitles

## API Endpoints

### POST /subtitles

Fetch subtitles for a single video.

**Request:**
```json
{
    "url": "https://www.youtube.com/watch?v=xxx",
    "return_audio": true
}
```

**Parameters:**
- `url`: Full video URL
- `return_audio`: Whether to return audio base64 when no subtitles, default `true`

**Success Response (200):**
```json
{
    "title": "Video Title",
    "platform": "youtube",
    "duration": 845,
    "language": "en",
    "source": "cc",
    "subtitles": "Subtitle text content...",
    "audio_base64": null
}
```

### POST /subtitles/batch

Batch fetch subtitles for multiple videos.

**Request:**
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

**Parameters:**
- `urls`: List of URLs, max 20
- `concurrency`: Concurrency level, default 3, max 5
- `return_audio`: Whether to return audio base64 when no subtitles, default `true`

### POST /subtitles/channel

Fetch subtitles from a YouTube channel or Bilibili UP主 page.

**Request:**
```json
{
    "channel_url": "https://www.youtube.com/@mkbhd",
    "limit": 20,
    "concurrency": 3,
    "return_audio": true
}
```

**Parameters:**
- `channel_url`: YouTube channel URL (`@handle` or `/channel/UCxxx`) or Bilibili space URL
- `limit`: Number of recent videos to fetch, default 20, max 50
- `concurrency`: Concurrency level, default 3
- `return_audio`: Whether to return audio base64 when no subtitles, default `true`

### POST /search

Search YouTube or Bilibili videos.

**Request:**
```json
{
    "query": "MacBook Pro review",
    "platform": "youtube",
    "limit": 10
}
```

**Parameters:**
- `query`: Search keyword
- `platform`: Search platform, `youtube` | `bilibili` | `all`, default `youtube`
- `limit`: Number of results, default 10, max 20

**Success Response (200):**
```json
{
    "query": "MacBook Pro review",
    "platform": "youtube",
    "total": 10,
    "results": [
        {
            "title": "MacBook Pro 2024 3-Month Real Usage Review",
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

## Local Testing

### Health Check

```bash
curl http://localhost:8765/health
```

### YouTube Test

```bash
curl -X POST http://localhost:8765/subtitles \
  -H "Content-Type: application/json" \
  -d '{"url": "https://www.youtube.com/watch?v=jNQXAC9IVRw"}'
```

### Bilibili Test (has CC subtitles)

```bash
curl -X POST http://localhost:8765/subtitles \
  -H "Content-Type: application/json" \
  -d '{"url": "https://www.bilibili.com/video/BVxxx"}'
```

### No Subtitles, Return Audio

```bash
curl -X POST http://localhost:8765/subtitles \
  -H "Content-Type: application/json" \
  -d '{"url": "https://www.bilibili.com/video/BV1GJ411x7h7", "return_audio": true}'
```

### Skip Audio (subtitles only)

```bash
curl -X POST http://localhost:8765/subtitles \
  -H "Content-Type: application/json" \
  -d '{"url": "https://www.bilibili.com/video/BVxxx", "return_audio": false}'
```

### Batch Test

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

### Search YouTube

```bash
curl -X POST http://localhost:8765/search \
  -H "Content-Type: application/json" \
  -d '{"query": "MacBook Pro review", "platform": "youtube", "limit": 5}'
```

## Complete Workflow Example

```bash
# Step 1: Search videos
urls=$(curl -s -X POST http://localhost:8765/search \
  -H "Content-Type: application/json" \
  -d '{"query": "Python tutorial", "platform": "youtube", "limit": 3}' | \
  python3 -c "import sys,json; print('\"\"\"' + '\"\"\",\"\"\"'.join([r['url'] for r in json.load(sys.stdin)['results']]) + '\"\"\"')")

# Step 2: Batch fetch subtitles
echo "{\"urls\": [$urls], \"return_audio\": true}" | \
  curl -X POST http://localhost:8765/subtitles/batch \
  -H "Content-Type: application/json" \
  -d @-
```

## Claude Tool Schema

### Single Subtitle Fetch

```json
{
  "name": "get_subtitles",
  "description": "Fetch video subtitles, supports YouTube and Bilibili. Returns audio for processing when no subtitles available. Input video URL, returns subtitle text and video metadata.",
  "input_schema": {
    "type": "object",
    "properties": {
      "url": {
        "type": "string",
        "description": "Full video URL, supports YouTube and Bilibili"
      },
      "return_audio": {
        "type": "boolean",
        "description": "Return audio base64 when no subtitles, default true"
      }
    },
    "required": ["url"]
  }
}
```

### Batch Subtitle Fetch

```json
{
  "name": "get_subtitles_batch",
  "description": "Batch fetch subtitles for multiple videos, supports YouTube and Bilibili. Returns audio for processing when no subtitles available. Individual failures don't affect the overall result.",
  "input_schema": {
    "type": "object",
    "properties": {
      "urls": {
        "type": "array",
        "items": { "type": "string" },
        "description": "List of video URLs, max 20"
      },
      "concurrency": {
        "type": "integer",
        "description": "Concurrency level, default 3, max 5"
      },
      "return_audio": {
        "type": "boolean",
        "description": "Return audio base64 when no subtitles, default true"
      }
    },
    "required": ["urls"]
  }
}
```

### Video Search

```json
{
  "name": "search_videos",
  "description": "Search videos on YouTube or Bilibili, returns video list and URLs. Can be used with get_subtitles_batch to fetch subtitle content after searching.",
  "input_schema": {
    "type": "object",
    "properties": {
      "query": {
        "type": "string",
        "description": "Search keyword"
      },
      "platform": {
        "type": "string",
        "enum": ["youtube", "bilibili", "all"],
        "description": "Search platform, default youtube"
      },
      "limit": {
        "type": "integer",
        "description": "Number of results, default 10, max 20"
      }
    },
    "required": ["query"]
  }
}
```

## Tech Stack

- Python 3.10+
- FastAPI + uvicorn
- yt-dlp
- No database, no auth, local execution

## Subtitle Extraction Logic

1. Priority: manual CC subtitles → auto-generated subtitles
2. Language priority: `zh-Hans` → `zh` → `zh-CN` → `en` → first available
3. Format priority: `json3` → `vtt` → `srt`
4. Removes timestamps, merges into plain text paragraphs
5. No subtitles + return_audio=true: Downloads audio → returns base64

## Notes

- **Audio Return**: Returns audio for caller processing when no subtitles available
- **Temp File Cleanup**: Temporary files automatically deleted after audio processing
- **Rate Limiting**: Add delays between batches for large-scale crawling

## License

MIT
