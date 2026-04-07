#!/bin/bash
# Subtitle Fetcher Deployment Script

set -e

echo "==============================================="
echo "Subtitle Fetcher 部署脚本"
echo "==============================================="
echo ""

# Check if running as root
if [ "$EUID" -ne 0 ]; then
   echo "⚠️  请使用 sudo 运行此脚本"
   exit 1
fi

# Detect OS
if [ -f /etc/os-release ]; then
    . /etc/os-release
    OS=$NAME
else
    OS=$(uname -s)
fi

echo "检测到的系统: $OS"
echo ""

# Install dependencies
echo "📦 安装依赖..."
if [[ "$OS" == *"Ubuntu"* ]] || [[ "$OS" == *"Debian"* ]]; then
    apt-get update
    apt-get install -y python3 python3-pip python3-venv ffmpeg git curl
elif [[ "$OS" == *"CentOS"* ]] || [[ "$OS" == *"Red Hat"* ]]; then
    yum update -y
    yum install -y python3 python3-pip ffmpeg git curl
elif [[ "$OS" == *"Alpine"* ]]; then
    apk update
    apk add python3 py3-pip ffmpeg git curl
else
    echo "⚠️  未识别的系统，请手动安装: python3, pip, ffmpeg, git, curl"
fi

# Create app directory
APP_DIR="/opt/subtitle-fetcher"
echo "📁 创建应用目录: $APP_DIR"
mkdir -p $APP_DIR
cd $APP_DIR

# Copy files (assuming they are in current directory)
echo "📂 复制应用文件..."
if [ -f "main.py" ]; then
    cp -r . $APP_DIR/
else
    echo "❌ 未找到 main.py，请确保在正确的目录运行此脚本"
    exit 1
fi

# Create virtual environment
echo "🐍 创建 Python 虚拟环境..."
python3 -m venv venv
source venv/bin/activate

# Install requirements
echo "📥 安装 Python 依赖..."
pip install --upgrade pip
pip install -r requirements.txt

# Create environment file
echo ""
echo "🔧 配置环境变量..."
if [ ! -f ".env" ]; then
    cp .env.example .env
    echo "✅ 已创建 .env 文件，请编辑配置你的 API Key"
fi

# Create cookies file
touch cookies.txt
chmod 600 cookies.txt

# Create systemd service
echo ""
echo "⚙️  创建系统服务..."
cat > /etc/systemd/system/subtitle-fetcher.service << 'EOF'
[Unit]
Description=Subtitle Fetcher API
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/subtitle-fetcher
Environment=PATH=/opt/subtitle-fetcher/venv/bin
ExecStart=/opt/subtitle-fetcher/venv/bin/uvicorn main:app --host 0.0.0.0 --port 8765
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

# Reload systemd
systemctl daemon-reload

echo ""
echo "==============================================="
echo "✅ 部署完成！"
echo "==============================================="
echo ""
echo "使用方法:"
echo ""
echo "1. 编辑配置文件:"
echo "   nano /opt/subtitle-fetcher/.env"
echo ""
echo "2. 添加 Bilibili Cookie (如需):"
echo "   nano /opt/subtitle-fetcher/cookies.txt"
echo ""
echo "3. 启动服务:"
echo "   systemctl start subtitle-fetcher"
echo ""
echo "4. 查看状态:"
echo "   systemctl status subtitle-fetcher"
echo ""
echo "5. 设置开机启动:"
echo "   systemctl enable subtitle-fetcher"
echo ""
echo "6. 查看日志:"
echo "   journalctl -u subtitle-fetcher -f"
echo ""
echo "API 地址: http://$(hostname -I | awk '{print $1}'):8765"
echo ""
