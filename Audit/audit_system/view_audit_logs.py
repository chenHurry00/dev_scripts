#!/usr/bin/env python3
"""
命令审计日志查看器
"""
import json
import sys
from pathlib import Path
from datetime import datetime

# 日志文件路径
LOG_FILE = Path.home() / '.audit_commands.log'
SYSTEM_LOG_FILE = Path('/var/log/audit_commands.log')

# 优先使用系统日志
if SYSTEM_LOG_FILE.exists():
    LOG_FILE = SYSTEM_LOG_FILE


def format_timestamp(ts_str):
    """格式化时间戳"""
    try:
        dt = datetime.fromisoformat(ts_str)
        return dt.strftime('%Y-%m-%d %H:%M:%S')
    except:
        return ts_str


def view_logs(username=None, limit=50):
    """查看审计日志"""
    if not LOG_FILE.exists():
        print(f"日志文件不存在: {LOG_FILE}")
        print("\n请先启用审计功能：")
        print("  source /home/yuchen/scripts/Audit/audit_system/audit_bash_hook.sh")
        return

    print(f"📋 命令审计日志 - {LOG_FILE}")
    print("=" * 100)

    logs = []
    with open(LOG_FILE, 'r', encoding='utf-8') as f:
        for line in f:
            try:
                log = json.loads(line.strip())
                if username is None or log.get('username') == username:
                    logs.append(log)
            except:
                continue

    # 显示最近的记录
    logs = logs[-limit:]

    if not logs:
        print("暂无日志记录")
        return

    print(f"{'时间':<20} {'用户':<10} {'主机':<15} {'工作目录':<30} {'命令'}")
    print("-" * 100)

    for log in logs:
        timestamp = format_timestamp(log.get('timestamp', ''))
        username = log.get('username', 'unknown')
        hostname = log.get('hostname', 'unknown')
        working_dir = log.get('working_dir', '/')
        command = log.get('command', '')

        # 截断过长的路径和命令
        if len(working_dir) > 28:
            working_dir = '...' + working_dir[-25:]
        if len(command) > 50:
            command = command[:47] + '...'

        print(f"{timestamp:<20} {username:<10} {hostname:<15} {working_dir:<30} {command}")

    print("-" * 100)
    print(f"共 {len(logs)} 条记录")


def main():
    """主函数"""
    if len(sys.argv) > 1:
        if sys.argv[1] == '--user':
            username = sys.argv[2] if len(sys.argv) > 2 else None
            view_logs(username=username)
        elif sys.argv[1] == '--limit':
            limit = int(sys.argv[2]) if len(sys.argv) > 2 else 50
            view_logs(limit=limit)
        elif sys.argv[1] == '--help':
            print("用法:")
            print("  python3 view_audit_logs.py              # 查看最近 50 条")
            print("  python3 view_audit_logs.py --user yuchen # 查看指定用户")
            print("  python3 view_audit_logs.py --limit 100   # 查看最近 100 条")
        else:
            view_logs()
    else:
        view_logs()


if __name__ == '__main__':
    main()
