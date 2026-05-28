#!/bin/bash
# 审计系统 - 命令审计安装脚本

set -e

AUDIT_DIR="/home/yuchen/scripts/Audit/audit_system"
AUDIT_HOOK="$AUDIT_DIR/audit_bash_hook.sh"

echo "============================================================"
echo "审计系统 - 命令审计安装"
echo "============================================================"

# 检查文件是否存在
if [ ! -f "$AUDIT_HOOK" ]; then
    echo "✗ 错误: 找不到 $AUDIT_HOOK"
    exit 1
fi

echo ""
echo "📋 安装选项："
echo "  1. 仅为当前用户安装 (推荐)"
echo "  2. 为所有用户安装 (需要 root 权限)"
echo ""
read -p "请选择 [1/2]: " choice

case $choice in
    1)
        echo ""
        echo "📝 为当前用户 ($USER) 安装..."

        # 检查 ~/.bashrc 是否已包含审计钩子
        if grep -q "audit_bash_hook.sh" ~/.bashrc; then
            echo "✓ 审计钩子已存在于 ~/.bashrc"
        else
            echo "" >> ~/.bashrc
            echo "# 审计系统 - 命令记录" >> ~/.bashrc
            echo "source $AUDIT_HOOK" >> ~/.bashrc
            echo "✓ 已添加审计钩子到 ~/.bashrc"
        fi

        echo ""
        echo "✅ 安装完成！"
        echo ""
        echo "📌 使配置生效："
        echo "  source ~/.bashrc"
        echo ""
        echo "📌 测试审计功能："
        echo "  ls -la"
        echo "  python3 $AUDIT_DIR/view_audit_logs.py"
        ;;

    2)
        echo ""
        echo "📝 为所有用户安装..."

        if [ "$EUID" -ne 0 ]; then
            echo "✗ 错误: 需要 root 权限"
            echo "请使用: sudo $0"
            exit 1
        fi

        # 创建全局配置文件
        GLOBAL_HOOK="/etc/profile.d/audit.sh"

        if [ -f "$GLOBAL_HOOK" ]; then
            echo "✓ 全局审计钩子已存在: $GLOBAL_HOOK"
        else
            cp "$AUDIT_HOOK" "$GLOBAL_HOOK"
            chmod 644 "$GLOBAL_HOOK"
            echo "✓ 已创建全局审计钩子: $GLOBAL_HOOK"
        fi

        # 创建系统日志目录
        mkdir -p /var/log
        touch /var/log/audit_commands.log
        chmod 666 /var/log/audit_commands.log
        echo "✓ 已创建系统日志文件: /var/log/audit_commands.log"

        echo ""
        echo "✅ 安装完成！"
        echo ""
        echo "📌 所有用户重新登录后生效"
        echo ""
        echo "📌 查看日志："
        echo "  python3 $AUDIT_DIR/view_audit_logs.py"
        ;;

    *)
        echo "✗ 无效选择"
        exit 1
        ;;
esac

echo ""
echo "============================================================"
echo "📚 使用说明"
echo "============================================================"
echo ""
echo "1. 查看审计日志："
echo "   python3 $AUDIT_DIR/view_audit_logs.py"
echo ""
echo "2. 查看指定用户的日志："
echo "   python3 $AUDIT_DIR/view_audit_logs.py --user yuchen"
echo ""
echo "3. 查看更多记录："
echo "   python3 $AUDIT_DIR/view_audit_logs.py --limit 100"
echo ""
echo "4. 卸载审计功能："
echo "   编辑 ~/.bashrc，删除包含 'audit_bash_hook.sh' 的行"
echo ""
echo "============================================================"
