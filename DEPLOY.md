# Subtitle Fetcher 部署指南

## 快速部署（推荐）

### 方式一：Docker 部署（最简单）

```bash
# 1. 进入项目目录
cd subtitle-fetcher

# 2. 配置环境变量
cp .env.example .env
nano .env  # 编辑你的配置

# 3. 启动服务
docker-compose up -d

# 4. 查看日志
docker-compose logs -f
```

服务将在 `http://localhost:8765` 运行。

### 方式二：传统部署

```bash
# 1. 运行部署脚本
sudo bash deploy.sh

# 2. 编辑配置
sudo nano /opt/subtitle-fetcher/.env

# 3. 启动服务
sudo systemctl start subtitle-fetcher
sudo systemctl enable subtitle-fetcher

# 4. 查看状态
sudo systemctl status subtitle-fetcher
```

---

## 详细配置

### 1. 环境变量配置 (.env)

```bash
# DashScope API Key（用于B站ASR识别）
DASHSCOPE_API_KEY=your_key_here

# Bilibili SESSDATA（用于获取B站字幕）
BILIBILI_SESSDATA=your_sessdata_here
```

**获取 Bilibili SESSDATA：**

1. 登录 Bilibili 网页版
2. F12 打开开发者工具 → Application → Cookies
3. 找到 `SESSDATA` 字段，复制值
4. 粘贴到 `.env` 文件

### 2. Cookie 文件 (cookies.txt)

如需使用 Netscape 格式的 cookie 文件：

```bash
# 格式：domain  flag  path  secure  expiration  name  value
.bilibili.com	TRUE	/	FALSE	1790955127	SESSDATA	your_sessdata_here
```

### 3. 系统服务管理

```bash
# 启动
sudo systemctl start subtitle-fetcher

# 停止
sudo systemctl stop subtitle-fetcher

# 重启
sudo systemctl restart subtitle-fetcher

# 查看日志
sudo journalctl -u subtitle-fetcher -f

# 查看状态
sudo systemctl status subtitle-fetcher
```

---

## 生产环境建议

### 1. 使用 Nginx 反向代理

```nginx
server {
    listen 80;
    server_name your-domain.com;

    location / {
        proxy_pass http://127.0.0.1:8765;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

### 2. 使用 HTTPS (Certbot)

```bash
sudo apt install certbot python3-certbot-nginx
sudo certbot --nginx -d your-domain.com
```

### 3. 防火墙配置

```bash
# 开放 8765 端口
sudo ufw allow 8765/tcp

# 如果使用 Nginx，只需开放 80/443
sudo ufw allow 'Nginx Full'
```

---

## 测试部署

```bash
# 健康检查
curl http://localhost:8765/health

# 测试 YouTube 字幕
curl -X POST http://localhost:8765/subtitles \
  -H "Content-Type: application/json" \
  -d '{"url": "https://www.youtube.com/watch?v=jNQXAC9IVRw"}'

# 测试搜索
curl -X POST http://localhost:8765/search \
  -H "Content-Type: application/json" \
  -d '{"query": "Python tutorial", "limit": 5}'
```

---

## 常见问题

### Q: B站视频返回 412 错误？

A: 需要配置 Bilibili Cookie：
1. 登录 Bilibili 网页版
2. 复制 SESSDATA
3. 更新 `.env` 文件或 `cookies.txt`
4. 重启服务

### Q: ASR 识别失败？

A: 检查：
- DASHSCOPE_API_KEY 是否配置正确
- 音频文件是否正确下载
- DashScope 服务是否可用

### Q: 服务启动失败？

A: 检查日志：
```bash
# Docker
docker-compose logs

# 系统服务
sudo journalctl -u subtitle-fetcher -f
```

---

## 目录结构

```
subtitle-fetcher/
├── main.py              # FastAPI 主服务
├── fetcher.py           # 字幕获取核心逻辑
├── requirements.txt     # Python 依赖
├── Dockerfile           # Docker 配置
├── docker-compose.yml   # Docker Compose 配置
├── deploy.sh            # 部署脚本
├── .env.example         # 环境变量示例
├── .env                 # 实际配置（不提交到git）
├── cookies.txt          # Bilibili Cookie
└── README.md            # 使用文档
```

---

## 更新部署

```bash
# Docker 方式
cd subtitle-fetcher
git pull  # 如果有更新
docker-compose down
docker-compose up -d --build

# 传统方式
cd /opt/subtitle-fetcher
git pull
source venv/bin/activate
pip install -r requirements.txt
sudo systemctl restart subtitle-fetcher
```
