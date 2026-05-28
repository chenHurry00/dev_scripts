#!/bin/bash
# 审计系统 - 统一管理脚本

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

check_dependencies() {
    # 检查并安装依赖
    local missing_deps=()
    local missing_pip_packages=()

    # 检查系统依赖
    if ! command -v python3 &> /dev/null; then
        missing_deps+=("python3")
    fi

    if ! command -v sqlite3 &> /dev/null; then
        missing_deps+=("sqlite3")
    fi

    # 检查 pip
    if ! command -v pip3 &> /dev/null; then
        missing_deps+=("python3-pip")
    fi

    # 安装系统依赖
    if [ ${#missing_deps[@]} -gt 0 ]; then
        echo "⚠️  缺少系统依赖: ${missing_deps[*]}"
        echo ""

        if [ "$EUID" -ne 0 ]; then
            echo "✗ 需要 root 权限安装依赖"
            return 1
        fi

        read -p "是否自动安装？[Y/n] " -n 1 -r
        echo
        if [[ $REPLY =~ ^[Nn]$ ]]; then
            return 1
        fi

        # 检测包管理器并安装
        if command -v apt &> /dev/null; then
            apt update
            apt install -y "${missing_deps[@]}"
        elif command -v yum &> /dev/null; then
            yum install -y "${missing_deps[@]}"
        elif command -v dnf &> /dev/null; then
            dnf install -y "${missing_deps[@]}"
        elif command -v pacman &> /dev/null; then
            pacman -S --noconfirm "${missing_deps[@]}"
        else
            echo "✗ 无法识别包管理器，请手动安装: ${missing_deps[*]}"
            return 1
        fi

        echo "✓ 系统依赖安装完成"
    fi

    # 检查 Python 包
    if ! python3 -c "import flask" 2>/dev/null; then
        missing_pip_packages+=("flask")
    fi

    if [ ${#missing_pip_packages[@]} -gt 0 ]; then
        echo "⚠️  缺少 Python 包: ${missing_pip_packages[*]}"
        echo ""

        read -p "是否自动安装？[Y/n] " -n 1 -r
        echo
        if [[ $REPLY =~ ^[Nn]$ ]]; then
            return 1
        fi

        # 使用 --break-system-packages 和 --ignore-installed 避免冲突
        pip3 install --break-system-packages --ignore-installed "${missing_pip_packages[@]}"
        echo "✓ Python 包安装完成"
    fi

    return 0
}

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
    echo "  8. 清空数据库（保留服务）"
    echo "  9. 完全卸载（服务+审计+数据）"
    echo ""
    echo "  0. 退出"
    echo ""
    read -p "请选择 [0-9]: " choice
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

    # 检查依赖
    if ! check_dependencies; then
        echo "✗ 依赖检查失败"
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

    echo ""
    echo "【配置数据库】"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

    # 等待数据库初始化（最多等待 10 秒）
    DB_PATH="$SCRIPT_DIR/data/audit.db"
    for i in {1..10}; do
        if [ -f "$DB_PATH" ]; then
            echo "✓ 数据库已初始化"
            break
        fi
        echo "等待数据库初始化... ($i/10)"
        sleep 1
    done

    if [ ! -f "$DB_PATH" ]; then
        echo "⚠️  数据库未创建，请访问 Web 界面触发初始化"
        echo ""
        echo "✓ 安装完成"
        echo ""
        echo "访问地址: http://localhost:$port"
        echo "用户名: admin"
        echo "密码: $password"
        return 0
    fi

    # 添加防重复索引
    INDEX_EXISTS=$(sqlite3 "$DB_PATH" "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_audit_checksum_unique';" 2>/dev/null)
    if [ -z "$INDEX_EXISTS" ]; then
        echo "正在添加防重复索引..."

        # 尝试创建索引
        ERROR_MSG=$(sqlite3 "$DB_PATH" "CREATE UNIQUE INDEX idx_audit_checksum_unique ON audit_logs(checksum);" 2>&1)

        if [ $? -eq 0 ]; then
            echo "✓ 已添加防重复索引"
        else
            echo "✗ 添加索引失败"
            echo "  错误信息: $ERROR_MSG"

            # 检查是否因为重复数据导致失败
            if echo "$ERROR_MSG" | grep -q "UNIQUE"; then
                echo ""
                echo "⚠️  数据库中存在重复记录，需要先清理"
                echo "  执行以下命令清理重复记录："
                echo ""
                echo "  sudo systemctl stop audit-sync"
                echo "  sqlite3 $DB_PATH \"DELETE FROM audit_logs WHERE id NOT IN (SELECT MIN(id) FROM audit_logs GROUP BY checksum);\""
                echo "  sqlite3 $DB_PATH \"CREATE UNIQUE INDEX idx_audit_checksum_unique ON audit_logs(checksum);\""
                echo "  sudo systemctl start audit-sync"
            fi
        fi
    else
        echo "✓ 防重复索引已存在"
    fi

    echo ""
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

clear_data() {
    echo "【清空数据库】"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "⚠️  警告: 将删除以下内容："
    echo "  - 数据库文件（$SCRIPT_DIR/data/audit.db）"
    echo "  - 日志文件（$SCRIPT_DIR/logs/*.log）"
    echo "  - 用户缓冲文件（~/.audit_buffer.jsonl）"
    echo ""
    echo "✓ 保留以下内容："
    echo "  - systemd 服务配置"
    echo "  - 命令审计钩子"
    echo ""
    read -p "确认清空数据？[y/N] " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        return 0
    fi

    if [ "$EUID" -ne 0 ]; then
        echo "✗ 需要 root 权限"
        return 1
    fi

    # 停止服务
    echo "正在停止服务..."
    systemctl stop audit-web audit-sync 2>/dev/null
    echo "✓ 已停止服务"

    # 删除数据
    rm -rf "$SCRIPT_DIR/data"
    rm -rf "$SCRIPT_DIR/logs"
    echo "✓ 已删除数据库和日志"

    # 清理缓冲
    find /home /root -maxdepth 2 -name ".audit_buffer.jsonl" -delete 2>/dev/null
    echo "✓ 已清理缓冲文件"

    # 重启服务
    echo ""
    read -p "是否重启服务？[Y/n] " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Nn]$ ]]; then
        systemctl start audit-web audit-sync
        echo "✓ 已重启服务"
        echo "  数据库将在首次访问时自动初始化"
    fi

    echo ""
    echo "✓ 清空完成"
}

full_uninstall() {
    echo "【完全卸载】"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "⚠️  警告: 将删除以下内容："
    echo "  - systemd 服务（audit-web, audit-sync）"
    echo "  - 命令审计钩子（/etc/profile.d/audit.sh）"
    echo "  - 用户缓冲文件（~/.audit_buffer.jsonl）"
    echo "  - 数据库和日志（$SCRIPT_DIR/data, $SCRIPT_DIR/logs）"
    echo ""
    read -p "确认完全卸载（包括数据）？[y/N] " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        return 0
    fi

    if [ "$EUID" -ne 0 ]; then
        echo "✗ 需要 root 权限"
        return 1
    fi

    # 停止服务
    echo "正在停止服务..."
    systemctl stop audit-web audit-sync 2>/dev/null
    systemctl disable audit-web audit-sync 2>/dev/null
    rm -f /etc/systemd/system/audit-web.service
    rm -f /etc/systemd/system/audit-sync.service
    systemctl daemon-reload
    echo "✓ 已删除服务"

    # 停止进程
    pkill -f sync_buffer.py

    # 删除钩子
    rm -f /etc/profile.d/audit.sh
    echo "✓ 已删除命令审计钩子"

    # 清理缓冲
    find /home /root -maxdepth 2 -name ".audit_buffer.jsonl" -delete 2>/dev/null
    echo "✓ 已清理缓冲文件"

    # 删除数据
    rm -rf "$SCRIPT_DIR/data"
    rm -rf "$SCRIPT_DIR/logs"
    echo "✓ 已删除数据库和日志"

    # 删除防火墙规则
    echo ""
    read -p "是否删除防火墙规则？[y/N] " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        if command -v ufw &> /dev/null; then
            ufw status numbered | grep "Audit System" | while read line; do
                rule_num=$(echo "$line" | grep -oP '^\[\s*\K\d+')
                if [ -n "$rule_num" ]; then
                    ufw --force delete $rule_num
                fi
            done
            echo "✓ 已删除防火墙规则"
        elif command -v firewall-cmd &> /dev/null; then
            echo "⚠️  请手动删除端口规则："
            echo "   firewall-cmd --permanent --remove-port=<端口>/tcp"
            echo "   firewall-cmd --reload"
        fi
    fi

    echo ""
    echo "✓ 完全卸载完成"
    echo "⚠️  用户需要重新登录或执行: unset PROMPT_COMMAND"
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
        8) clear_data ;;
        9) full_uninstall ;;
        0) echo "退出"; exit 0 ;;
        *) echo "无效选择" ;;
    esac

    echo ""
    read -p "按回车继续..."
    clear
done
