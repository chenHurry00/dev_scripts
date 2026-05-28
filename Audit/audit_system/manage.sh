#!/bin/bash
# 审计系统 - 统一管理脚本

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

show_menu() {
    echo "╔════════════════════════════════════════════════════════════╗"
    echo "║     审计系统 - 管理工具                                  ║"
    echo "╚════════════════════════════════════════════════════════════╝"
    echo ""
    echo "【服务管理】"
    echo "  1. 安装 Web 服务（systemd）"
    echo "  2. 卸载 Web 服务"
    echo "  3. 重启 Web 服务"
    echo "  4. 查看服务状态"
    echo ""
    echo "【命令审计】"
    echo "  5. 安装命令审计"
    echo "  6. 卸载命令审计"
    echo ""
    echo "【维护工具】"
    echo "  7. 清理重复进程"
    echo "  8. 完全卸载（服务+审计+数据）"
    echo ""
    echo "  0. 退出"
    echo ""
    read -p "请选择 [0-8]: " choice
    echo ""
}

install_service() {
    echo "【安装 Web 服务】"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

    if [ "$EUID" -ne 0 ]; then
        echo "✗ 需要 root 权限"
        echo "请使用: sudo $0"
        return 1
    fi

    read -p "端口 [5000]: " port
    port=${port:-5000}

    read -p "管理员密码 [BY116358]: " password
    password=${password:-BY116358}

    # 检查端口占用
    if netstat -tuln 2>/dev/null | grep -q ":$port "; then
        echo "✗ 端口 $port 已被占用"
        return 1
    fi

    REAL_USER="${SUDO_USER:-$USER}"

    # 配置防火墙（只管理审计系统端口）
    if command -v ufw &> /dev/null; then
        if ufw status | grep -q "Status: active"; then
            ufw allow $port/tcp comment 'Audit System Web'
            echo "✓ 已开放端口 $port"
        else
            echo "⚠️  防火墙未启用，跳过配置"
        fi
    elif command -v firewall-cmd &> /dev/null; then
        firewall-cmd --permanent --add-port=$port/tcp
        firewall-cmd --reload
        echo "✓ 已开放端口 $port"
    fi

    # 创建 Web 服务
    cat > /etc/systemd/system/audit-web.service << EOF
[Unit]
Description=Audit System Web Service
After=network.target

[Service]
Type=simple
User=$REAL_USER
WorkingDirectory=$SCRIPT_DIR
Environment="AUDIT_ADMIN_USER=admin"
Environment="AUDIT_ADMIN_PASSWORD=$password"
Environment="AUDIT_PORT=$port"
Environment="API_SECRET_KEY=$(openssl rand -hex 32)"
ExecStart=/usr/bin/python3 $SCRIPT_DIR/app.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

    # 创建同步服务
    cat > /etc/systemd/system/audit-sync.service << EOF
[Unit]
Description=Audit System Buffer Sync Service
After=network.target

[Service]
Type=simple
User=$REAL_USER
WorkingDirectory=$SCRIPT_DIR
ExecStart=/usr/bin/python3 $SCRIPT_DIR/sync_buffer.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

    systemctl daemon-reload
    systemctl start audit-web audit-sync
    systemctl enable audit-web audit-sync

    echo "✓ 安装完成"
    echo ""
    echo "访问地址: http://localhost:$port"
    echo "用户名: admin"
    echo "密码: $password"
}

uninstall_service() {
    echo "【卸载 Web 服务】"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

    if [ "$EUID" -ne 0 ]; then
        echo "✗ 需要 root 权限"
        return 1
    fi

    read -p "确认卸载？[y/N] " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        return 0
    fi

    systemctl stop audit-web audit-sync 2>/dev/null
    systemctl disable audit-web audit-sync 2>/dev/null
    rm -f /etc/systemd/system/audit-web.service
    rm -f /etc/systemd/system/audit-sync.service
    systemctl daemon-reload

    # 删除防火墙规则（只删除审计系统端口）
    echo ""
    read -p "是否删除防火墙规则？[y/N] " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        if command -v ufw &> /dev/null; then
            # 查找并删除审计系统相关规则
            ufw status numbered | grep "Audit System" | while read line; do
                rule_num=$(echo "$line" | grep -oP '^\[\s*\K\d+')
                if [ -n "$rule_num" ]; then
                    ufw --force delete $rule_num
                fi
            done
            echo "✓ 已删除防火墙规则"
        elif command -v firewall-cmd &> /dev/null; then
            # firewalld 需要知道具体端口，提示用户手动删除
            echo "⚠️  请手动删除端口规则："
            echo "   firewall-cmd --permanent --remove-port=<端口>/tcp"
            echo "   firewall-cmd --reload"
        fi
    fi

    echo "✓ 卸载完成"
}

restart_service() {
    echo "【重启服务】"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

    if [ "$EUID" -ne 0 ]; then
        echo "✗ 需要 root 权限"
        return 1
    fi

    systemctl restart audit-web audit-sync
    echo "✓ 重启完成"
}

show_status() {
    echo "【服务状态】"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

    if systemctl is-active --quiet audit-web; then
        echo "✓ Web 服务: 运行中"
    else
        echo "✗ Web 服务: 已停止"
    fi

    if systemctl is-active --quiet audit-sync; then
        echo "✓ 同步服务: 运行中"
    else
        echo "✗ 同步服务: 已停止"
    fi

    SYNC_COUNT=$(ps aux | grep -c "[s]ync_buffer.py")
    echo "同步进程数: $SYNC_COUNT"

    if [ -n "$PROMPT_COMMAND" ] && echo "$PROMPT_COMMAND" | grep -q "audit_log_command"; then
        echo "✓ 命令审计: 已启用"
    else
        echo "✗ 命令审计: 未启用"
    fi
}

install_command_audit() {
    echo "【安装命令审计】"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

    if [ "$EUID" -ne 0 ]; then
        echo "✗ 需要 root 权限（全局安装）"
        return 1
    fi

    # 创建全局钩子
    cat > /etc/profile.d/audit.sh << EOF
#!/bin/bash
export AUDIT_SCRIPT_PATH="$SCRIPT_DIR/audit_command_buffer.py"
if [ -f "$SCRIPT_DIR/audit_bash_hook.sh" ]; then
    source "$SCRIPT_DIR/audit_bash_hook.sh"
fi
EOF

    chmod +x /etc/profile.d/audit.sh

    echo "✓ 安装完成"
    echo "⚠️  所有用户需要重新登录才能生效"
}

uninstall_command_audit() {
    echo "【卸载命令审计】"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

    if [ "$EUID" -ne 0 ]; then
        echo "✗ 需要 root 权限"
        return 1
    fi

    read -p "确认卸载？[y/N] " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        return 0
    fi

    # 停止同步进程
    pkill -f sync_buffer.py

    # 删除全局钩子
    rm -f /etc/profile.d/audit.sh

    # 清理缓冲文件
    find /home /root -maxdepth 2 -name ".audit_buffer.jsonl" -delete 2>/dev/null

    echo "✓ 卸载完成"
    echo "⚠️  用户需要重新登录或执行: unset PROMPT_COMMAND"
}

cleanup() {
    echo "【清理重复进程】"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

    SYNC_COUNT=$(ps aux | grep -c "[s]ync_buffer.py")

    if [ $SYNC_COUNT -eq 0 ]; then
        echo "⊘ 未发现同步进程"
    elif [ $SYNC_COUNT -eq 1 ]; then
        echo "✓ 同步进程正常（1 个）"
    else
        echo "⚠️  发现 $SYNC_COUNT 个同步进程（异常）"
        read -p "是否停止所有进程？[y/N] " -n 1 -r
        echo
        if [[ $REPLY =~ ^[Yy]$ ]]; then
            pkill -f sync_buffer.py
            echo "✓ 已停止所有进程"
            echo "请使用: sudo systemctl restart audit-sync"
        fi
    fi
}

full_uninstall() {
    echo "【完全卸载】"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "⚠️  警告: 将删除所有服务、钩子和数据"
    echo ""
    read -p "确认完全卸载？[y/N] " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        return 0
    fi

    if [ "$EUID" -ne 0 ]; then
        echo "✗ 需要 root 权限"
        return 1
    fi

    # 停止服务
    systemctl stop audit-web audit-sync 2>/dev/null
    systemctl disable audit-web audit-sync 2>/dev/null
    rm -f /etc/systemd/system/audit-web.service
    rm -f /etc/systemd/system/audit-sync.service
    systemctl daemon-reload

    # 停止进程
    pkill -f sync_buffer.py

    # 删除钩子
    rm -f /etc/profile.d/audit.sh

    # 清理缓冲
    find /home /root -maxdepth 2 -name ".audit_buffer.jsonl" -delete 2>/dev/null

    # 删除数据
    read -p "是否删除数据库和日志？[y/N] " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        rm -rf "$SCRIPT_DIR/data"
        rm -rf "$SCRIPT_DIR/logs"
        echo "✓ 已删除数据"
    fi

    echo "✓ 完全卸载完成"
}

# 主循环
while true; do
    show_menu

    case $choice in
        1) install_service ;;
        2) uninstall_service ;;
        3) restart_service ;;
        4) show_status ;;
        5) install_command_audit ;;
        6) uninstall_command_audit ;;
        7) cleanup ;;
        8) full_uninstall ;;
        0) echo "退出"; exit 0 ;;
        *) echo "无效选择" ;;
    esac

    echo ""
    read -p "按回车继续..."
    clear
done
