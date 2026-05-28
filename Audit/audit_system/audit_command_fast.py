#!/usr/bin/env python3
"""
命令审计客户端 - 优化版（减少启动开销）
"""
import os
import sys
import socket
import hashlib
import sqlite3
from datetime import datetime
from pathlib import Path

# 数据库路径
DB_PATH = Path(__file__).parent / 'data' / 'audit.db'

# 风险级别映射（避免重复计算）
RISK_LABELS = {1: 'L1-INFO', 2: 'L2-LOW', 3: 'L3-MEDIUM', 4: 'L4-HIGH', 5: 'L5-CRITICAL'}

# 命令分类规则（优化查找）
COMMAND_RULES = [
    (['rm -rf', 'dd if=', 'mkfs', 'fdisk', ':(){:|:&};:'], 'SYSTEM', 'DANGEROUS_COMMAND', 5),
    (['sudo', 'su ', 'systemctl', 'service', 'reboot', 'shutdown'], 'SYSTEM', 'SYSTEM_ADMIN', 4),
    (['rm ', 'mv ', 'cp ', 'chmod', 'chown', 'mkdir', 'rmdir'], 'FILE', 'FILE_OPERATION', 3),
    (['curl', 'wget', 'ssh', 'scp', 'rsync', 'nc ', 'netcat'], 'NETWORK', 'NETWORK_ACCESS', 3),
    (['mysql', 'psql', 'mongo', 'redis-cli', 'sqlite3'], 'DATA', 'DATABASE_ACCESS', 3),
    (['apt', 'yum', 'dnf', 'pip', 'npm', 'gem'], 'SYSTEM', 'PACKAGE_INSTALL', 3),
    (['ls', 'cat', 'less', 'more', 'head', 'tail', 'grep', 'find'], 'FILE', 'FILE_READ', 1),
]


def classify_command(command):
    """命令分类和风险评估（优化版）"""
    cmd_lower = command.lower()

    for keywords, category, action_type, risk_level in COMMAND_RULES:
        for kw in keywords:
            if kw in cmd_lower:
                return category, action_type, risk_level

    return 'SYSTEM', 'COMMAND_EXEC', 2


def log_to_database(username, command, hostname, session_id):
    """直接记录到数据库（优化版）"""
    try:
        # 快速检查
        if not DB_PATH.exists():
            return False

        # 分类
        category, action_type, risk_level = classify_command(command)

        # 生成数据
        timestamp = datetime.now().isoformat()
        checksum = hashlib.sha256(f"{timestamp}{username}{command}".encode()).hexdigest()

        # 快速写入
        conn = sqlite3.connect(DB_PATH, timeout=2, isolation_level='DEFERRED')
        conn.execute("""
            INSERT INTO audit_logs (
                timestamp, username, session_id, ip_address,
                action_category, action_type, target_resource,
                result, risk_level, risk_label, checksum
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            timestamp, username, session_id or '', hostname,
            category, action_type, command[:200], 'success',
            risk_level, RISK_LABELS[risk_level], checksum
        ))
        conn.commit()
        conn.close()
        return True
    except:
        return False


def main():
    """主函数（优化版）"""
    # 快速获取参数
    if len(sys.argv) < 2:
        return

    command = ' '.join(sys.argv[1:])

    # 快速过滤
    if not command or 'audit_command.py' in command or 'view_audit_logs.py' in command:
        return

    # 获取环境信息（最小化系统调用）
    username = os.environ.get('USER', 'unknown')
    hostname = socket.gethostname()
    session_id = os.environ.get('AUDIT_SESSION_ID', '')

    # 写入数据库
    log_to_database(username, command, hostname, session_id)


if __name__ == '__main__':
    main()
