#!/bin/bash
# 审计系统 - 服务安装脚本

set -e

# 获取脚本所在目录
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
AUDIT_DIR="$SCRIPT_DIR"

# 默认端口
DEFAULT_PORT=5000

# 解析参数
PORT=$DEFAULT_PORT
ADMIN_USER="admin"
ADMIN_PASSWORD="BY116358"

while [[ $# -gt 0 ]]; do
    case $1 in
        -p|--port)
            PORT="$2"
            shift 2
            ;;
        -u|--user)
            ADMIN_USER="$2"
            shift 2
            ;;
        --password)
            ADMIN_PASSWORD="$2"
            shift 2
            ;;
        -h|--help)
            echo "用法: $0 [选项]"
            echo ""
            echo "选项:"
            echo "  -p, --port PORT        指定端口 (默认: 5000)"
            echo "  -u, --user USER        指定管理员用户名 (默认: admin)"
            echo "  --password PASSWORD    指定管理员密码 (默认: BY116358)"
            echo "  -h, --help             显示帮助信息"
            echo ""
            echo "示例:"
            echo "  sudo $0 -p 8080"
            echo "  sudo $0 -p 8080 --password MyPassword123"
            exit 0
            ;;
        *)
            echo "未知参数: $1"
            echo "使用 -h 查看帮助"
            exit 1
            ;;
    esac
done

echo "╔════════════════════════════════════════════════════════════╗"
echo "║     审计系统 - 服务安装                                  ║"
echo "╚════════════════════════════════════════════════════════════╝"
echo ""
echo "审计系统路径: $AUDIT_DIR"
echo "监听端口:     $PORT"
echo "管理员用户:   $ADMIN_USER"
echo ""

# 检查是否为 root
if [ "$EUID" -ne 0 ]; then
    echo "✗ 错误: 需要 root 权限"
    echo "请使用: sudo $0"
    exit 1
fi

# 获取当前用户
REAL_USER="${SUDO_USER:-$USER}"
echo "运行用户: $REAL_USER"
echo ""

# ============================================================
# 1. 检查端口是否被占用
# ============================================================
echo "【1】检查端口"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

if netstat -tuln 2>/dev/null | grep -q ":$PORT "; then
    echo "✗ 错误: 端口 $PORT 已被占用"
    echo ""
    echo "查看占用进程:"
    netstat -tulnp | grep ":$PORT "
    echo ""
    echo "请使用 -p 参数指定其他端口"
    exit 1
fi

echo "✓ 端口 $PORT 可用"
echo ""

# ============================================================
# 2. 配置防火墙
# ============================================================
echo "【2】配置防火墙"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

if command -v ufw &> /dev/null; then
    echo "检测到 ufw 防火墙"

    # 检查防火墙是否启用
    if ufw status | grep -q "Status: active"; then
        # 只添加审计系统端口，不修改其他规则
        ufw allow $PORT/tcp comment 'Audit System Web'
        echo "✓ 已开放端口 $PORT"
    else
        echo "⚠️  防火墙未启用，跳过配置"
        echo "   如需启用防火墙，请手动执行："
        echo "   sudo ufw allow 22/tcp"
        echo "   sudo ufw allow $PORT/tcp"
        echo "   sudo ufw enable"
    fi

elif command -v firewall-cmd &> /dev/null; then
    echo "检测到 firewalld 防火墙"
    firewall-cmd --permanent --add-port=$PORT/tcp
    firewall-cmd --reload
    echo "✓ 已开放端口 $PORT"

else
    echo "⊘ 未检测到防火墙，跳过配置"
fi

echo ""

# ============================================================
# 3. 创建 systemd 服务 - Web 应用
# ============================================================
echo "【3】创建 systemd 服务 - Web 应用"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

cat > /etc/systemd/system/audit-web.service << EOF
[Unit]
Description=Audit System Web Service
After=network.target

[Service]
Type=simple
User=$REAL_USER
WorkingDirectory=$AUDIT_DIR
Environment="AUDIT_ADMIN_USER=$ADMIN_USER"
Environment="AUDIT_ADMIN_PASSWORD=$ADMIN_PASSWORD"
Environment="AUDIT_PORT=$PORT"
Environment="API_SECRET_KEY=$(openssl rand -hex 32)"
ExecStart=/usr/bin/python3 $AUDIT_DIR/app.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

echo "✓ 已创建 /etc/systemd/system/audit-web.service"
echo ""

# ============================================================
# 4. 创建 systemd 服务 - 后台同步
# ============================================================
echo "【4】创建 systemd 服务 - 后台同步"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

cat > /etc/systemd/system/audit-sync.service << EOF
[Unit]
Description=Audit System Buffer Sync Service
After=network.target

[Service]
Type=simple
User=$REAL_USER
WorkingDirectory=$AUDIT_DIR
ExecStart=/usr/bin/python3 $AUDIT_DIR/sync_buffer.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

echo "✓ 已创建 /etc/systemd/system/audit-sync.service"
echo ""

# ============================================================
# 5. 启动服务
# ============================================================
echo "【5】启动服务"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

systemctl daemon-reload
echo "✓ 已重载 systemd 配置"

systemctl start audit-web
systemctl start audit-sync
echo "✓ 已启动服务"

systemctl enable audit-web
systemctl enable audit-sync
echo "✓ 已设置开机自启"

echo ""

# ============================================================
# 6. 检查服务状态
# ============================================================
echo "【6】检查服务状态"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

sleep 2

WEB_STATUS=$(systemctl is-active audit-web)
SYNC_STATUS=$(systemctl is-active audit-sync)

echo "Web 服务:  $WEB_STATUS"
echo "同步服务:  $SYNC_STATUS"

if [ "$WEB_STATUS" != "active" ]; then
    echo ""
    echo "⚠️  Web 服务启动失败，查看日志:"
    journalctl -u audit-web -n 20 --no-pager
fi

if [ "$SYNC_STATUS" != "active" ]; then
    echo ""
    echo "⚠️  同步服务启动失败，查看日志:"
    journalctl -u audit-sync -n 20 --no-pager
fi

echo ""

# ============================================================
# 完成
# ============================================================
echo "╔════════════════════════════════════════════════════════════╗"
echo "║     安装完成！                                            ║"
echo "╚════════════════════════════════════════════════════════════╝"
echo ""
echo "🌐 访问地址"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  本地:  http://localhost:$PORT"
echo "  远程:  http://$(hostname -I | awk '{print $1}'):$PORT"
echo ""
echo "🔐 登录信息"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  用户名:  $ADMIN_USER"
echo "  密码:    $ADMIN_PASSWORD"
echo ""
echo "🔧 常用命令"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  查看状态:  sudo systemctl status audit-web"
echo "  查看日志:  sudo journalctl -u audit-web -f"
echo "  重启服务:  sudo systemctl restart audit-web"
echo "  停止服务:  sudo systemctl stop audit-web"
echo "  卸载服务:  sudo $AUDIT_DIR/uninstall_service.sh"
echo ""
