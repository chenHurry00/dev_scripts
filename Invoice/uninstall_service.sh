#!/bin/bash
# 卸载系统服务

SERVICE_NAME="invoice-system"

echo "=================================="
echo "发票报销系统 - 服务卸载"
echo "=================================="

# 检查是否为root
if [ "$EUID" -ne 0 ]; then
    echo "请使用 sudo 运行此脚本"
    exit 1
fi

# 停止服务
systemctl stop $SERVICE_NAME 2>/dev/null
echo "✓ 服务已停止"

# 禁用服务
systemctl disable $SERVICE_NAME 2>/dev/null
echo "✓ 服务已禁用"

# 删除服务文件
rm -f /etc/systemd/system/${SERVICE_NAME}.service
echo "✓ 服务文件已删除"

# 重载systemd
systemctl daemon-reload
echo "✓ systemd 已重载"

echo ""
echo "=================================="
echo "卸载完成！"
echo "=================================="
