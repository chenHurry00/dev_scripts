#!/bin/bash
# 服务器审计系统 - 快速启动脚本

set -e

echo "============================================================"
echo "服务器审计系统 v2.0 - 启动脚本"
echo "============================================================"

# 检查 Python
if ! command -v python3 &> /dev/null; then
    echo "❌ 错误: 未找到 python3"
    exit 1
fi

echo "✓ Python 版本: $(python3 --version)"

# 检查依赖
echo ""
echo "检查依赖..."
if ! python3 -c "import flask" 2>/dev/null; then
    echo "⚠️  Flask 未安装，正在安装依赖..."
    pip3 install -r requirements.txt
else
    echo "✓ 依赖已安装"
fi

# 检查环境变量
echo ""
echo "环境配置:"
if [ -z "$AUDIT_ADMIN_PASSWORD" ]; then
    echo "⚠️  使用默认 Admin 密码"
    echo "   建议设置: export AUDIT_ADMIN_PASSWORD=your_password"
else
    echo "✓ Admin 密码已配置"
fi

if [ -z "$API_SECRET_KEY" ]; then
    echo "⚠️  使用默认 API Secret Key"
    echo "   建议设置: export API_SECRET_KEY=your_secret_key"
else
    echo "✓ API Secret Key 已配置"
fi

# 创建必要目录
echo ""
echo "初始化目录..."
mkdir -p data logs/{audit,access,error,alert}
echo "✓ 目录创建完成"

# 启动应用
echo ""
echo "============================================================"
echo "启动应用..."
echo "============================================================"
echo ""
echo "访问地址: http://localhost:5000"
echo "默认账户: admin / Admin@2026!Change"
echo ""
echo "按 Ctrl+C 停止服务"
echo ""

python3 app.py
