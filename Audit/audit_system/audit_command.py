#!/usr/bin/env python3
"""
命令审计客户端 - 实时记录到数据库
通过 bash PROMPT_COMMAND 钩子调用
"""
import os
import sys
import json
import socket
import hashlib
import sqlite3
from datetime import datetime
from pathlib import Path

# 数据库路径
DB_PATH = Path(__file__).parent / 'data' / 'audit.db'


def classify_command(command):
    """命令分类和风险评估"""
    command_lower = command.lower()

    # 高危命令
    if any(kw in command_lower for kw in ['rm -rf', 'dd if=', 'mkfs', 'fdisk', ':(){:|:&};:']):
        return 'SYSTEM', 'DANGEROUS_COMMAND', 5

    # 系统管理命令
    if any(kw in command_lower for kw in ['sudo', 'su ', 'systemctl', 'service', 'reboot', 'shutdown']):
        return 'SYSTEM', 'SYSTEM_ADMIN', 4

    # 文件操作
    if any(kw in command_lower for kw in ['rm ', 'mv ', 'cp ', 'chmod', 'chown', 'mkdir', 'rmdir']):
        return 'FILE', 'FILE_OPERATION', 3

    # 网络操作
    if any(kw in command_lower for kw in ['curl', 'wget', 'ssh', 'scp', 'rsync', 'nc ', 'netcat']):
        return 'NETWORK', 'NETWORK_ACCESS', 3

    # 数据库操作
    if any(kw in command_lower for kw in ['mysql', 'psql', 'mongo', 'redis-cli', 'sqlite3']):
        return 'DATA', 'DATABASE_ACCESS', 3

    # 包管理
    if any(kw in command_lower for kw in ['apt', 'yum', 'dnf', 'pip', 'npm', 'gem']):
        return 'SYSTEM', 'PACKAGE_INSTALL', 3

    # 查看命令
    if any(kw in command_lower for kw in ['ls', 'cat', 'less', 'more', 'head', 'tail', 'grep', 'find']):
        return 'FILE', 'FILE_READ', 1

    # 其他命令
    return 'SYSTEM', 'COMMAND_EXEC', 2


def log_to_database(username, command, working_dir, hostname, session_id):
    """直接记录到数据库"""
    try:
        # 检查数据库是否存在
        if not DB_PATH.exists():
            return False

        # 连接数据库
        conn = sqlite3.connect(DB_PATH, timeout=5)
        cursor = conn.cursor()

        # 分类和风险评估
        category, action_type, risk_level = classify_command(command)

        # 风险标签
        risk_labels = {
            1: 'L1-INFO',
            2: 'L2-LOW',
            3: 'L3-MEDIUM',
            4: 'L4-HIGH',
            5: 'L5-CRITICAL'
        }
        risk_label = risk_labels.get(risk_level, 'L2-LOW')

        # 生成时间戳和校验和
        timestamp = datetime.now().isoformat()
        checksum = hashlib.sha256(
            f"{timestamp}{username}{command}".encode()
        ).hexdigest()

        # 插入数据库
        cursor.execute("""
            INSERT INTO audit_logs (
                timestamp, username, session_id, ip_address,
                action_category, action_type, target_resource,
                result, risk_level, risk_label, checksum
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            timestamp,
            username,
            session_id or '',
            hostname,
            category,
            action_type,
            command[:200],  # 命令作为目标资源
            'success',
            risk_level,
            risk_label,
            checksum
        ))

        conn.commit()
        conn.close()
        return True

    except Exception as e:
        # 静默失败，不影响用户操作
        return False


def get_command_from_history():
    """从 bash history 获取最后执行的命令"""
    try:
        history_file = Path.home() / '.bash_history'
        if history_file.exists():
            with open(history_file, 'r', encoding='utf-8', errors='ignore') as f:
                lines = f.readlines()
                if lines:
                    return lines[-1].strip()
    except Exception:
        pass
    return None


def main():
    """主函数"""
    # 获取环境信息
    username = os.environ.get('USER', 'unknown')
    hostname = socket.gethostname()
    working_dir = os.getcwd()
    session_id = os.environ.get('AUDIT_SESSION_ID', '')

    # 如果没有 session_id，生成一个
    if not session_id:
        session_id = f"{username}_{os.getpid()}_{datetime.now().strftime('%Y%m%d%H%M%S')}"

    # 获取命令（从参数或 history）
    if len(sys.argv) > 1:
        command = ' '.join(sys.argv[1:])
    else:
        command = get_command_from_history()

    if command and command.strip():
        # 过滤掉审计脚本自身的调用
        if 'audit_command.py' not in command and 'view_audit_logs.py' not in command:
            # 直接写入数据库
            log_to_database(username, command, working_dir, hostname, session_id)


if __name__ == '__main__':
    main()
