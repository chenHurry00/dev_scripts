#!/bin/bash
# 发票报销系统启动脚本

cd "$(dirname "$0")"

echo "=================================="
echo "发票报销系统"
echo "=================================="

# 检查依赖
if ! python3 -c "import flask" 2>/dev/null; then
    echo "正在安装依赖..."
    pip3 install -r requirements.txt
fi

# 启动服务
echo "启动服务..."
python3 app.py
