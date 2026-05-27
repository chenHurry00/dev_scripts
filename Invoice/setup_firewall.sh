#!/bin/bash
# 端口开放脚本 - 开放5000端口供外部访问

echo "=================================="
echo "发票报销系统 - 端口开放"
echo "=================================="

PORT=5000

# 检查防火墙状态
if command -v firewall-cmd &> /dev/null; then
    echo "检测到 firewalld..."

    # 检查防火墙是否运行
    if sudo firewall-cmd --state &> /dev/null; then
        echo "开放端口 $PORT..."
        sudo firewall-cmd --permanent --add-port=$PORT/tcp
        sudo firewall-cmd --reload
        echo "✓ 端口 $PORT 已开放"

        # 显示当前开放的端口
        echo ""
        echo "当前开放的端口："
        sudo firewall-cmd --list-ports
    else
        echo "防火墙未运行，无需配置"
    fi

elif command -v ufw &> /dev/null; then
    echo "检测到 ufw..."
    sudo ufw allow $PORT/tcp
    echo "✓ 端口 $PORT 已开放"

    # 显示状态
    echo ""
    sudo ufw status

elif command -v iptables &> /dev/null; then
    echo "检测到 iptables..."
    sudo iptables -I INPUT -p tcp --dport $PORT -j ACCEPT
    sudo iptables-save > /etc/iptables/rules.v4 2>/dev/null || true
    echo "✓ 端口 $PORT 已开放"

else
    echo "未检测到防火墙，跳过端口配置"
fi

echo ""
echo "=================================="
echo "配置完成！"
echo "访问地址: http://$(hostname -I | awk '{print $1}'):$PORT"
echo "=================================="
