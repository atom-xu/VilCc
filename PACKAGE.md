# Subtitle Fetcher 部署包

## 文件清单

| 文件 | 说明 | 必需 |
|------|------|------|
| main.py | FastAPI 主服务 | ✅ |
| fetcher.py | 字幕获取核心逻辑 | ✅ |
| requirements.txt | Python 依赖列表 | ✅ |
| .env.example | 环境变量示例 | ✅ |
| Dockerfile | Docker 构建文件 | ⭕ |
| docker-compose.yml | Docker Compose 配置 | ⭕ |
| deploy.sh | 传统部署脚本 | ⭕ |
| start.sh | 本地启动脚本 | ⭕ |
| README.md | 使用文档 | ✅ |
| DEPLOY.md | 部署文档 | ✅ |

## 快速开始

### 方式一：Docker（推荐）

```bash
# 1. 上传到服务器并解压
cd subtitle-fetcher

# 2. 配置环境
cp .env.example .env
nano .env

# 3. 启动
docker-compose up -d
```

### 方式二：传统部署

```bash
# 上传到服务器后运行
sudo bash deploy.sh
```

## 配置说明

### 必需配置 (.env)

```bash
# DashScope API Key（用于B站ASR）
DASHSCOPE_API_KEY=sk-xxx

# Bilibili Cookie（用于B站字幕）
BILIBILI_SESSDATA=xxx
```

### B站 Cookie 获取

1. 浏览器登录 bilibili.com
2. F12 → Application → Cookies
3. 复制 SESSDATA 值

## 测试命令

```bash
# 健康检查
curl http://localhost:8765/health

# 获取单条字幕
curl -X POST http://localhost:8765/subtitles \
  -d '{"url": "https://www.youtube.com/watch?v=xxx"}'

# 批量获取
curl -X POST http://localhost:8765/subtitles/batch \
  -d '{"urls": ["url1", "url2"]}'

# 搜索视频
curl -X POST http://localhost:8765/search \
  -d '{"query": "Python 教程", "platform": "youtube"}'
```

## 端口

- 默认端口：8765
- 可在 docker-compose.yml 或 deploy.sh 中修改

## 更新

```bash
# Docker
docker-compose down
docker-compose up -d --build

# 传统
sudo systemctl restart subtitle-fetcher
```
