#!/usr/bin/env python3
"""
命令审计日志导入工具
将本地命令日志导入到审计系统数据库
"""
import json
import sqlite3
import hashlib
from pathlib import Path
from datetime import datetime

# 配置
LOG_FILE = Path.home() / '.audit_commands.log'
SYSTEM_LOG_FILE = Path('/var/log/audit_commands.log')
DB_PATH = Path(__file__).parent / 'data' / 'audit.db'

# 优先使用系统日志
if SYSTEM_LOG_FILE.exists():
    LOG_FILE = SYSTEM_LOG_FILE


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


def import_logs():
    """导入命令日志到数据库"""
    if not LOG_FILE.exists():
        print(f"✗ 日志文件不存在: {LOG_FILE}")
        return

    if not DB_PATH.exists():
        print(f"✗ 数据库不存在: {DB_PATH}")
        print("请先启动审计系统")
        return

    # 连接数据库
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # 读取日志
    imported = 0
    skipped = 0

    print(f"📋 开始导入命令日志: {LOG_FILE}")
    print("=" * 80)

    with open(LOG_FILE, 'r', encoding='utf-8') as f:
        for line in f:
            try:
                log = json.loads(line.strip())

                # 提取信息
                timestamp = log.get('timestamp')
                username = log.get('username', 'unknown')
                hostname = log.get('hostname', 'unknown')
                command = log.get('command', '')
                working_dir = log.get('working_dir', '/')
                session_id = log.get('session_id', '')

                # 分类和风险评估
                category, action_type, risk_level = classify_command(command)

                # 生成校验和
                checksum = hashlib.sha256(
                    f"{timestamp}{username}{command}".encode()
                ).hexdigest()

                # 检查是否已存在
                cursor.execute(
                    "SELECT id FROM audit_logs WHERE checksum = ?",
                    (checksum,)
                )
                if cursor.fetchone():
                    skipped += 1
                    continue

                # 插入数据库
                cursor.execute("""
                    INSERT INTO audit_logs (
                        timestamp, user_id, username, session_id,
                        action_category, action_type, target_resource,
                        result, risk_level, ip_address, user_agent,
                        request_method, request_path, status_code,
                        duration_ms, details, checksum
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    timestamp,
                    None,  # user_id
                    username,
                    session_id,
                    category,
                    action_type,
                    command[:200],  # 命令作为目标资源
                    'success',
                    risk_level,
                    hostname,  # 使用主机名作为 IP
                    f'Terminal@{working_dir}',  # 工作目录作为 user_agent
                    'EXEC',  # 请求方法
                    working_dir,  # 工作目录作为路径
                    0,  # status_code
                    0,  # duration_ms
                    json.dumps({
                        'command': command,
                        'working_dir': working_dir,
                        'hostname': hostname,
                        'pid': log.get('pid'),
                        'ppid': log.get('ppid')
                    }, ensure_ascii=False),
                    checksum
                ))

                imported += 1

                if imported % 100 == 0:
                    print(f"已导入 {imported} 条...")

            except Exception as e:
                print(f"✗ 导入失败: {e}")
                continue

    conn.commit()
    conn.close()

    print("=" * 80)
    print(f"✅ 导入完成！")
    print(f"  - 新增: {imported} 条")
    print(f"  - 跳过: {skipped} 条（已存在）")
    print("")
    print("📊 查看日志:")
    print("  访问 http://localhost:5000")
    print("  使用 admin / BY116358 登录")


if __name__ == '__main__':
    import_logs()
