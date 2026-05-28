#!/bin/bash
# 审计系统 - Bash 集成脚本
# 将此脚本添加到 ~/.bashrc 或 /etc/profile.d/audit.sh

# 审计脚本路径（自动检测）
# 优先级：环境变量 > 相对路径 > 默认路径
if [ -n "$AUDIT_SCRIPT_PATH" ]; then
    AUDIT_SCRIPT="$AUDIT_SCRIPT_PATH"
else
    # 获取当前脚本所在目录
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    AUDIT_SCRIPT="$SCRIPT_DIR/audit_command_buffer.py"
fi

# 检查脚本是否存在
if [ -f "$AUDIT_SCRIPT" ]; then
    # 生成会话 ID（如果不存在）
    if [ -z "$AUDIT_SESSION_ID" ]; then
        export AUDIT_SESSION_ID="${USER}_$$_$(date +%Y%m%d%H%M%S)"
    fi

    # 定义命令记录函数
    audit_log_command() {
        local last_cmd=$(history 1 | sed 's/^[ ]*[0-9]*[ ]*//')

        # 过滤掉空命令和审计脚本自身
        if [ -n "$last_cmd" ] && [[ ! "$last_cmd" =~ "audit_command.py" ]]; then
            # 使用 disown 静默执行，不显示后台任务提示
            (python3 "$AUDIT_SCRIPT" "$last_cmd" 2>/dev/null &)
        fi
    }

    # 设置 PROMPT_COMMAND 钩子
    # 在每个命令执行后自动调用
    if [[ ! "$PROMPT_COMMAND" =~ "audit_log_command" ]]; then
        PROMPT_COMMAND="audit_log_command${PROMPT_COMMAND:+;$PROMPT_COMMAND}"
    fi
fi
