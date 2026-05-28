#!/bin/bash
# 审计系统 - 服务卸载脚本

set -e

echo "╔════════════════════════════════════════════════════════════╗"
echo "║     审计系统 - 服务卸载                                  ║"
echo "╚════════════════════════════════════════════════════════════╝"
echo ""

# 检查是否为 root
if [ "$EUID" -ne 0 ]; then
    echo "✗ 错误: 需要 root 权限"
    echo "请使用: sudo $0"
    exit 1
fi

# 确认卸载
read -p "确认卸载审计系统服务？[y/N] " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "已取消"
    exit 0
fi

echo ""

# ============================================================
# 1. 停止服务
# ============================================================
echo "【1】停止服务"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

if systemctl is-active --quiet audit-web; then
    systemctl stop audit-web
    echo "✓ 已停止 audit-web"
else
    echo "⊘ audit-web 未运行"
fi

if systemctl is-active --quiet audit-sync; then
    systemctl stop audit-sync
    echo "✓ 已停止 audit-sync"
else
    echo "⊘ audit-sync 未运行"
fi

echo ""

# ============================================================
# 2. 禁用开机自启
# ============================================================
echo "【2】禁用开机自启"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

if systemctl is-enabled --quiet audit-web 2>/dev/null; then
    systemctl disable audit-web
    echo "✓ 已禁用 audit-web"
else
    echo "⊘ audit-web 未启用"
fi

if systemctl is-enabled --quiet audit-sync 2>/dev/null; then
    systemctl disable audit-sync
    echo "✓ 已禁用 audit-sync"
else
    echo "⊘ audit-sync 未启用"
fi

echo ""

# ============================================================
# 3. 删除服务文件
# ============================================================
echo "【3】删除服务文件"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

if [ -f /etc/systemd/system/audit-web.service ]; then
    rm -f /etc/systemd/system/audit-web.service
    echo "✓ 已删除 audit-web.service"
else
    echo "⊘ audit-web.service 不存在"
fi

if [ -f /etc/systemd/system/audit-sync.service ]; then
    rm -f /etc/systemd/system/audit-sync.service
    echo "✓ 已删除 audit-sync.service"
else
    echo "⊘ audit-sync.service 不存在"
fi

systemctl daemon-reload
echo "✓ 已重载 systemd 配置"

echo ""

# ============================================================
# 4. 关闭防火墙端口（可选）
# ============================================================
echo "【4】关闭防火墙端口"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

read -p "是否关闭防火墙端口 5000？[y/N] " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    if command -v ufw &> /dev/null; then
        ufw delete allow 5000/tcp 2>/dev/null || true
        echo "✓ 已关闭 ufw 端口 5000"

    elif command -v firewall-cmd &> /dev/null; then
        firewall-cmd --permanent --remove-port=5000/tcp 2>/dev/null || true
        firewall-cmd --reload
        echo "✓ 已关闭 firewalld 端口 5000"

    else
        echo "⊘ 未检测到防火墙"
    fi
else
    echo "⊘ 跳过关闭端口"
fi

echo ""

# ============================================================
# 5. 清理数据（可选）
# ============================================================
echo "【5】清理数据"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

read -p "是否删除数据库和日志？[y/N] " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    if [ -d "$SCRIPT_DIR/data" ]; then
        rm -rf "$SCRIPT_DIR/data"
        echo "✓ 已删除数据库"
    fi

    if [ -d "$SCRIPT_DIR/logs" ]; then
        rm -rf "$SCRIPT_DIR/logs"
        echo "✓ 已删除日志"
    fi

    echo "⚠️  数据已永久删除，无法恢复"
else
    echo "⊘ 保留数据库和日志"
fi

echo ""

# ============================================================
# 完成
# ============================================================
echo "╔════════════════════════════════════════════════════════════╗"
echo "║     卸载完成！                                            ║"
echo "╚════════════════════════════════════════════════════════════╝"
echo ""
echo "📝 说明"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  ✓ 服务已停止并删除"
echo "  ✓ 开机自启已禁用"
echo ""
echo "如需重新安装，运行:"
echo "  sudo $SCRIPT_DIR/install_service.sh"
echo ""
