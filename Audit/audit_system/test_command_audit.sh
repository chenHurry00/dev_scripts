#!/bin/bash
# 测试命令审计功能

echo "============================================================"
echo "测试命令审计功能"
echo "============================================================"
echo ""

# 加载审计钩子
source /home/yuchen/scripts/Audit/audit_system/audit_bash_hook.sh

echo "1. 执行测试命令..."
echo ""

# 模拟执行命令
python3 /home/yuchen/scripts/Audit/audit_system/audit_command.py "ls -la /home/yuchen"
python3 /home/yuchen/scripts/Audit/audit_system/audit_command.py "cat /etc/hosts"
python3 /home/yuchen/scripts/Audit/audit_system/audit_command.py "sudo systemctl status nginx"

echo "✓ 已记录 3 条测试命令"
echo ""

echo "2. 查询数据库..."
echo ""

sqlite3 /home/yuchen/scripts/Audit/audit_system/data/audit.db << 'EOF'
.mode column
.headers on
SELECT
    datetime(timestamp) as time,
    username,
    action_category as category,
    action_type as type,
    substr(target_resource, 1, 40) as command,
    risk_level
FROM audit_logs
WHERE username = 'yuchen'
ORDER BY id DESC
LIMIT 10;
EOF

echo ""
echo "============================================================"
echo "✅ 测试完成！"
echo ""
echo "现在访问 Web 界面查看："
echo "  http://localhost:5000"
echo "  登录：admin / BY116358"
echo ""
echo "应该可以看到 yuchen 用户的命令记录"
echo "============================================================"
