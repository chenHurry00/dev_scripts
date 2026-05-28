"""
数据库辅助函数
"""
import sqlite3
from flask import g, current_app


def get_db():
    """获取数据库连接"""
    if 'db' not in g:
        g.db = sqlite3.connect(
            current_app.config['AUDIT_DB'],
            detect_types=sqlite3.PARSE_DECLTYPES
        )
        g.db.row_factory = sqlite3.Row
    return g.db


def close_db(e=None):
    """关闭数据库连接"""
    db = g.pop('db', None)
    if db is not None:
        db.close()


def init_db(app):
    """初始化数据库"""
    with app.app_context():
        db = get_db()
        cursor = db.cursor()

        # 创建用户表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                username     TEXT    NOT NULL UNIQUE,
                password_hash TEXT   NOT NULL,
                role         TEXT    NOT NULL DEFAULT 'user',
                email        TEXT,
                created_at   TEXT    NOT NULL DEFAULT (datetime('now')),
                last_login   TEXT,
                login_ip     TEXT,
                is_active    INTEGER NOT NULL DEFAULT 1,
                failed_login_count INTEGER DEFAULT 0,
                locked_until TEXT
            )
        """)

        # 创建登录会话表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS login_sessions (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id   TEXT    NOT NULL UNIQUE,
                user_id      INTEGER NOT NULL,
                username     TEXT    NOT NULL,
                ip_address   TEXT    NOT NULL,
                user_agent   TEXT,
                login_time   TEXT    NOT NULL DEFAULT (datetime('now')),
                logout_time  TEXT,
                last_active  TEXT,
                is_active    INTEGER NOT NULL DEFAULT 1,
                logout_reason TEXT,
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
        """)

        db.commit()
        close_db()
