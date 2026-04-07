#!/bin/bash
# Subtitle Fetcher 本地启动脚本

cd "$(dirname "$0")"

# Check if virtual environment exists
if [ ! -d "venv" ]; then
    echo "📦 创建虚拟环境..."
    python3 -m venv venv
fi

# Activate virtual environment
source venv/bin/activate

# Check if dependencies are installed
if ! pip show fastapi > /dev/null 2>&1; then
    echo "📥 安装依赖..."
    pip install -r requirements.txt
fi

# Load environment variables
if [ -f ".env" ]; then
    export $(cat .env | grep -v '^#' | xargs)
fi

# Start server
echo "🚀 启动 Subtitle Fetcher..."
echo "📍 地址: http://localhost:8765"
echo "📖 文档: http://localhost:8765/docs"
echo ""

uvicorn main:app --host 0.0.0.0 --port 8765 --reload
