#!/bin/bash
# 系统服务安装脚本 - 注册为systemd服务并设置开机自启

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVICE_NAME="invoice-system"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"

echo "=================================="
echo "发票报销系统 - 服务安装"
echo "=================================="

# 检查是否为root
if [ "$EUID" -ne 0 ]; then
    echo "请使用 sudo 运行此脚本"
    exit 1
fi

# 获取Python路径
PYTHON_PATH=$(which python3)
if [ -z "$PYTHON_PATH" ]; then
    echo "错误: 未找到 python3"
    exit 1
fi

# 获取当前用户
CURRENT_USER=${SUDO_USER:-$USER}

echo "安装路径: $SCRIPT_DIR"
echo "Python路径: $PYTHON_PATH"
echo "运行用户: $CURRENT_USER"
echo ""

# 创建systemd服务文件
cat > "$SERVICE_FILE" << EOF
[Unit]
Description=Invoice Reimbursement System
After=network.target

[Service]
Type=simple
User=$CURRENT_USER
WorkingDirectory=$SCRIPT_DIR
Environment="PATH=/usr/local/bin:/usr/bin:/bin"
ExecStart=$PYTHON_PATH $SCRIPT_DIR/app.py
Restart=always
RestartSec=10
StandardOutput=append:$SCRIPT_DIR/logs/service.log
StandardError=append:$SCRIPT_DIR/logs/service.log

[Install]
WantedBy=multi-user.target
EOF

echo "✓ 服务文件已创建: $SERVICE_FILE"

# 重载systemd
systemctl daemon-reload
echo "✓ systemd 已重载"

# 启用服务
systemctl enable $SERVICE_NAME
echo "✓ 服务已设置为开机自启"

# 启动服务
systemctl start $SERVICE_NAME
echo "✓ 服务已启动"

echo ""
echo "=================================="
echo "安装完成！"
echo ""
echo "常用命令："
echo "  启动服务: sudo systemctl start $SERVICE_NAME"
echo "  停止服务: sudo systemctl stop $SERVICE_NAME"
echo "  重启服务: sudo systemctl restart $SERVICE_NAME"
echo "  查看状态: sudo systemctl status $SERVICE_NAME"
echo "  查看日志: sudo journalctl -u $SERVICE_NAME -f"
echo "  禁用自启: sudo systemctl disable $SERVICE_NAME"
echo ""
echo "访问地址: http://localhost:5000"
echo "=================================="
