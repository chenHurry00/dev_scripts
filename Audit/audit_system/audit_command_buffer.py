#!/usr/bin/env python3
"""
命令审计客户端 - 缓冲版（最小性能影响）
先写入本地缓冲文件，后台定期批量导入数据库
"""
import os
import sys
import json
from datetime import datetime
from pathlib import Path

# 缓冲文件路径
BUFFER_FILE = Path.home() / '.audit_buffer.jsonl'


def main():
    """主函数 - 仅写入本地文件（极快）"""
    # 快速获取参数
    if len(sys.argv) < 2:
        return

    command = ' '.join(sys.argv[1:])

    # 快速过滤
    if not command or 'audit_command' in command or 'view_audit_logs' in command:
        return

    # 获取客户端 IP（SSH 连接者的 IP）
    client_ip = os.environ.get('SSH_CLIENT', '').split()[0] if os.environ.get('SSH_CLIENT') else None
    if not client_ip:
        # 备用：SSH_CONNECTION
        client_ip = os.environ.get('SSH_CONNECTION', '').split()[0] if os.environ.get('SSH_CONNECTION') else None
    if not client_ip:
        # 本地登录或无法获取，使用 localhost
        client_ip = '127.0.0.1'

    # 构建日志条目
    log_entry = {
        'timestamp': datetime.now().isoformat(),
        'username': os.environ.get('USER', 'unknown'),
        'command': command,
        'session_id': os.environ.get('AUDIT_SESSION_ID', ''),
        'client_ip': client_ip
    }

    # 快速写入文件（追加模式，无锁）
    try:
        with open(BUFFER_FILE, 'a', encoding='utf-8') as f:
            f.write(json.dumps(log_entry, ensure_ascii=False) + '\n')
    except:
        pass  # 静默失败


if __name__ == '__main__':
    main()
