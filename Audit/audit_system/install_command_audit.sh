#!/bin/bash
# 审计系统 - 命令审计安装脚本

# 获取脚本所在目录的绝对路径
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
AUDIT_DIR="$SCRIPT_DIR"
AUDIT_HOOK="$AUDIT_DIR/audit_bash_hook.sh"

echo "============================================================"
echo "审计系统 - 命令审计安装"
echo "============================================================"
echo ""
echo "检测到审计系统路径: $AUDIT_DIR"
echo ""

# 检查文件是否存在
if [ ! -f "$AUDIT_HOOK" ]; then
    echo "✗ 错误: 找不到 $AUDIT_HOOK"
    echo "当前目录: $(pwd)"
    echo "脚本目录: $SCRIPT_DIR"
    ls -la "$SCRIPT_DIR/" | head -10
    exit 1
fi

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
        echo "  等待 10 秒后查看 Web 界面"
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

        cat > "$GLOBAL_HOOK" << EOF
#!/bin/bash
# 审计系统 - 全局命令审计钩子
# 自动生成，请勿手动编辑

# 设置审计脚本路径
export AUDIT_SCRIPT_PATH="$AUDIT_DIR/audit_command_buffer.py"

# 加载审计钩子
if [ -f "$AUDIT_HOOK" ]; then
    source "$AUDIT_HOOK"
fi
EOF

        chmod 644 "$GLOBAL_HOOK"
        echo "✓ 已创建全局审计钩子: $GLOBAL_HOOK"

        echo ""
        echo "✅ 安装完成！"
        echo ""
        echo "📌 所有用户重新登录后生效"
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
echo "1. 查看审计日志（Web 界面）："
echo "   http://服务器IP:5000"
echo ""
echo "2. 查看审计日志（命令行）："
echo "   python3 $AUDIT_DIR/tools/view_audit_logs.py"
echo ""
echo "3. 卸载审计功能："
echo "   - 当前用户: 编辑 ~/.bashrc，删除包含 'audit_bash_hook.sh' 的行"
echo "   - 所有用户: sudo rm /etc/profile.d/audit.sh"
echo ""
echo "============================================================"
