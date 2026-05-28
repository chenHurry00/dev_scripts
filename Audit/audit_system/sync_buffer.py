#!/usr/bin/env python3
"""
审计缓冲同步服务 - 定期将缓冲文件导入数据库
"""
import os
import json
import time
import socket
import hashlib
import sqlite3
from pathlib import Path
from datetime import datetime

# 配置
BUFFER_FILE = Path.home() / '.audit_buffer.jsonl'
DB_PATH = Path(__file__).parent / 'data' / 'audit.db'
SYNC_INTERVAL = 10  # 每 10 秒同步一次
BATCH_SIZE = 100    # 每次最多处理 100 条

# 风险级别映射
RISK_LABELS = {1: 'L1-INFO', 2: 'L2-LOW', 3: 'L3-MEDIUM', 4: 'L4-HIGH', 5: 'L5-CRITICAL'}

# 命令分类规则
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
    """命令分类和风险评估"""
    cmd_lower = command.lower()
    for keywords, category, action_type, risk_level in COMMAND_RULES:
        for kw in keywords:
            if kw in cmd_lower:
                return category, action_type, risk_level
    return 'SYSTEM', 'COMMAND_EXEC', 2


def sync_buffer_to_db():
    """同步缓冲文件到数据库"""
    if not BUFFER_FILE.exists() or not DB_PATH.exists():
        return 0

    # 读取缓冲文件
    try:
        with open(BUFFER_FILE, 'r', encoding='utf-8') as f:
            lines = f.readlines()
    except:
        return 0

    if not lines:
        return 0

    # 批量处理
    processed = 0
    hostname = socket.gethostname()

    try:
        conn = sqlite3.connect(DB_PATH, timeout=5)
        cursor = conn.cursor()

        for line in lines[:BATCH_SIZE]:
            try:
                log = json.loads(line.strip())
                command = log.get('command', '')
                username = log.get('username', 'unknown')
                timestamp = log.get('timestamp', datetime.now().isoformat())
                session_id = log.get('session_id', '')

                # 分类
                category, action_type, risk_level = classify_command(command)

                # 生成校验和
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
                    timestamp, username, session_id, hostname,
                    category, action_type, command[:200], 'success',
                    risk_level, RISK_LABELS[risk_level], checksum
                ))

                processed += 1
            except:
                continue

        conn.commit()
        conn.close()

        # 删除已处理的行
        if processed > 0:
            remaining_lines = lines[processed:]
            with open(BUFFER_FILE, 'w', encoding='utf-8') as f:
                f.writelines(remaining_lines)

    except Exception as e:
        pass

    return processed


def main():
    """主循环"""
    print("=" * 60)
    print("审计缓冲同步服务启动")
    print(f"缓冲文件: {BUFFER_FILE}")
    print(f"数据库: {DB_PATH}")
    print(f"同步间隔: {SYNC_INTERVAL} 秒")
    print("=" * 60)
    print()

    try:
        while True:
            processed = sync_buffer_to_db()
            if processed > 0:
                print(f"[{datetime.now().strftime('%H:%M:%S')}] 同步 {processed} 条记录")

            time.sleep(SYNC_INTERVAL)
    except KeyboardInterrupt:
        print("\n同步服务已停止")


if __name__ == '__main__':
    main()
