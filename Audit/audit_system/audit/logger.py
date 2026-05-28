"""
审计日志记录器 - 核心日志记录模块
"""
import sqlite3
import json
import hashlib
import logging
from datetime import datetime
from pathlib import Path
from threading import Lock
from .classifier import OperationClassifier


class AuditLogger:
    """
    线程安全的审计日志记录器
    同时写入 SQLite 和文件
    """

    def __init__(self, db_path: str, log_dir: str):
        self.db_path = db_path
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)

        self.classifier = OperationClassifier()
        self.lock = Lock()

        # 配置文件日志
        self.logger = logging.getLogger('AuditLogger')
        self.logger.setLevel(logging.INFO)

        # 确保数据库表存在
        self._init_db()

    def _init_db(self):
        """初始化数据库表"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        # 创建审计日志表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS audit_logs (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id         INTEGER,
                username        TEXT    NOT NULL,
                session_id      TEXT,
                ip_address      TEXT    NOT NULL,
                user_agent      TEXT,
                action_category TEXT    NOT NULL,
                action_type     TEXT    NOT NULL,
                action_detail   TEXT,
                target_resource TEXT,
                risk_level      INTEGER NOT NULL,
                risk_label      TEXT    NOT NULL,
                status_code     INTEGER,
                result          TEXT,
                error_message   TEXT,
                timestamp       TEXT    NOT NULL DEFAULT (datetime('now')),
                duration_ms     INTEGER,
                checksum        TEXT
            )
        """)

        # 创建索引
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_audit_timestamp
            ON audit_logs(timestamp)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_audit_user_id
            ON audit_logs(user_id)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_audit_risk_level
            ON audit_logs(risk_level)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_audit_ip
            ON audit_logs(ip_address)
        """)

        # 创建防篡改触发器
        cursor.execute("""
            CREATE TRIGGER IF NOT EXISTS prevent_audit_delete
            BEFORE DELETE ON audit_logs
            BEGIN
                SELECT RAISE(ABORT, 'Audit logs are immutable - deletion not allowed');
            END
        """)

        cursor.execute("""
            CREATE TRIGGER IF NOT EXISTS prevent_audit_update
            BEFORE UPDATE ON audit_logs
            BEGIN
                SELECT RAISE(ABORT, 'Audit logs are immutable - modification not allowed');
            END
        """)

        conn.commit()
        conn.close()

    def log(self, **kwargs) -> int:
        """
        记录一条审计日志

        Args:
            user_id: 用户ID
            username: 用户名
            session_id: 会话ID
            ip_address: IP地址
            user_agent: User-Agent
            category: 操作分类
            action_type: 操作类型
            action_detail: 操作详情（JSON字符串）
            target_resource: 目标资源
            result: 结果 (success/failure/error)
            error_message: 错误信息
            status_code: HTTP状态码
            duration_ms: 耗时（毫秒）
            request: Flask request 对象（可选）

        Returns:
            日志ID
        """
        with self.lock:
            # 构建记录
            record = self._build_record(**kwargs)

            # 分类并计算风险级别
            record['risk_level'], record['risk_label'] = self.classifier.classify(record)

            # 计算校验和
            record['checksum'] = self._compute_checksum(record)

            # 写入数据库
            log_id = self._write_to_db(record)

            # 写入文件
            self._write_to_file(record)

            # 高危操作触发告警
            if record['risk_level'] >= 4:
                self._trigger_alert(record, log_id)

            return log_id

    def _build_record(self, **kwargs) -> dict:
        """构建审计记录"""
        # 从 Flask request 对象提取信息
        request = kwargs.pop('request', None)

        if request:
            ip_address = kwargs.get('ip_address') or request.remote_addr
            user_agent = kwargs.get('user_agent') or request.headers.get('User-Agent', '')
        else:
            ip_address = kwargs.get('ip_address', 'unknown')
            user_agent = kwargs.get('user_agent', '')

        # 构建记录
        record = {
            'user_id': kwargs.get('user_id'),
            'username': kwargs.get('username', 'anonymous'),
            'session_id': kwargs.get('session_id', ''),
            'ip_address': ip_address,
            'user_agent': user_agent,
            'action_category': kwargs.get('category', 'ACCESS'),
            'action_type': kwargs.get('action_type', 'UNKNOWN'),
            'action_detail': kwargs.get('action_detail', ''),
            'target_resource': kwargs.get('target_resource', ''),
            'result': kwargs.get('result', 'success'),
            'error_message': kwargs.get('error_message', ''),
            'status_code': kwargs.get('status_code'),
            'duration_ms': kwargs.get('duration_ms'),
            'timestamp': datetime.now().isoformat(),
        }

        return record

    def _compute_checksum(self, record: dict) -> str:
        """计算记录校验和"""
        payload = f"{record['timestamp']}|{record['username']}|{record['action_type']}|{record['result']}"
        return 'sha256:' + hashlib.sha256(payload.encode()).hexdigest()

    def _write_to_db(self, record: dict) -> int:
        """写入数据库"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute("""
            INSERT INTO audit_logs (
                user_id, username, session_id, ip_address, user_agent,
                action_category, action_type, action_detail, target_resource,
                risk_level, risk_label, status_code, result, error_message,
                timestamp, duration_ms, checksum
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            record['user_id'],
            record['username'],
            record['session_id'],
            record['ip_address'],
            record['user_agent'],
            record['action_category'],
            record['action_type'],
            record['action_detail'],
            record['target_resource'],
            record['risk_level'],
            record['risk_label'],
            record['status_code'],
            record['result'],
            record['error_message'],
            record['timestamp'],
            record['duration_ms'],
            record['checksum']
        ))

        log_id = cursor.lastrowid
        conn.commit()
        conn.close()

        return log_id

    def _write_to_file(self, record: dict):
        """写入日志文件（JSON Lines 格式）"""
        # 按月切割日志文件
        log_file = self.log_dir / f"audit_{datetime.now().strftime('%Y-%m')}.log"

        try:
            with open(log_file, 'a', encoding='utf-8') as f:
                f.write(json.dumps(record, ensure_ascii=False) + '\n')
        except Exception as e:
            self.logger.error(f'写入日志文件失败: {e}')

    def _trigger_alert(self, record: dict, log_id: int):
        """触发告警"""
        # 写入告警日志
        alert_file = Path(self.log_dir).parent / 'alert' / f"alert_{datetime.now().strftime('%Y-%m')}.log"
        alert_file.parent.mkdir(parents=True, exist_ok=True)

        alert = {
            'log_id': log_id,
            'timestamp': record['timestamp'],
            'username': record['username'],
            'ip_address': record['ip_address'],
            'action_type': record['action_type'],
            'target_resource': record['target_resource'],
            'risk_level': record['risk_level'],
            'risk_label': record['risk_label']
        }

        try:
            with open(alert_file, 'a', encoding='utf-8') as f:
                f.write(json.dumps(alert, ensure_ascii=False) + '\n')
        except Exception as e:
            self.logger.error(f'写入告警文件失败: {e}')

        # 写入告警表
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            # 创建告警表（如果不存在）
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS alerts (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    audit_log_id INTEGER NOT NULL,
                    alert_type   TEXT    NOT NULL,
                    severity     TEXT    NOT NULL,
                    title        TEXT    NOT NULL,
                    description  TEXT,
                    is_read      INTEGER NOT NULL DEFAULT 0,
                    is_handled   INTEGER NOT NULL DEFAULT 0,
                    handler_id   INTEGER,
                    handled_at   TEXT,
                    created_at   TEXT    NOT NULL DEFAULT (datetime('now'))
                )
            """)

            # 插入告警
            title = f"{record['risk_label']} - {record['action_type']}"
            description = f"用户 {record['username']} 从 {record['ip_address']} 执行了 {record['action_type']} 操作"

            cursor.execute("""
                INSERT INTO alerts (
                    audit_log_id, alert_type, severity, title, description
                ) VALUES (?, ?, ?, ?, ?)
            """, (log_id, 'single_event', record['risk_label'], title, description))

            conn.commit()
            conn.close()
        except Exception as e:
            self.logger.error(f'写入告警表失败: {e}')
