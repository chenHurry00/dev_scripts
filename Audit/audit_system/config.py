"""
配置管理模块
"""
import os
from pathlib import Path

# 基础路径
BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / 'data'
LOGS_DIR = BASE_DIR / 'logs'
BACKUPS_DIR = BASE_DIR / 'backups'

# 确保目录存在
DATA_DIR.mkdir(exist_ok=True)
LOGS_DIR.mkdir(exist_ok=True)
BACKUPS_DIR.mkdir(exist_ok=True)
(LOGS_DIR / 'audit').mkdir(exist_ok=True)
(LOGS_DIR / 'access').mkdir(exist_ok=True)
(LOGS_DIR / 'error').mkdir(exist_ok=True)
(LOGS_DIR / 'alert').mkdir(exist_ok=True)

class Config:
    """基础配置"""
    # Flask 配置
    SECRET_KEY = os.environ.get('SECRET_KEY', os.urandom(24).hex())

    # 数据库配置
    AUDIT_DB = str(DATA_DIR / 'audit.db')

    # Admin 配置
    ADMIN_USERNAME = os.environ.get('AUDIT_ADMIN_USER', 'admin')
    ADMIN_PASSWORD = os.environ.get('AUDIT_ADMIN_PASSWORD', 'Admin@2026!Change')

    # API 配置
    API_SECRET_KEY = os.environ.get('API_SECRET_KEY', 'api-secret-change-in-production')

    # 日志配置
    LOG_DIR = str(LOGS_DIR)
    AUDIT_LOG_DIR = str(LOGS_DIR / 'audit')
    ACCESS_LOG_DIR = str(LOGS_DIR / 'access')
    ERROR_LOG_DIR = str(LOGS_DIR / 'error')
    ALERT_LOG_DIR = str(LOGS_DIR / 'alert')
    BACKUP_DIR = str(BACKUPS_DIR)

    # 服务端自动维护配置
    AUTO_MAINTENANCE_ENABLED = os.environ.get(
        'AUDIT_AUTO_MAINTENANCE_ENABLED', 'true'
    ).lower() == 'true'
    AUTO_MAINTENANCE_INTERVAL = int(os.environ.get('AUDIT_AUTO_MAINTENANCE_INTERVAL', 300))
    BACKUP_INTERVAL_HOURS = int(os.environ.get('AUDIT_BACKUP_INTERVAL_HOURS', 24))
    BACKUP_MAX_BYTES = int(os.environ.get('AUDIT_BACKUP_MAX_BYTES', 1024 * 1024 * 1024))
    MIN_FREE_BYTES = int(os.environ.get('AUDIT_MIN_FREE_BYTES', 2 * 1024 * 1024 * 1024))
    KEEP_LAST_BACKUP = os.environ.get('AUDIT_KEEP_LAST_BACKUP', 'true').lower() == 'true'
    CLEANUP_DB_ENABLED = os.environ.get('AUDIT_CLEANUP_DB_ENABLED', 'true').lower() == 'true'


    # 会话配置
    SESSION_TIMEOUT = 1800  # 30 分钟
    MAX_LOGIN_ATTEMPTS = 5
    LOCKOUT_DURATION = 900  # 15 分钟

    # 数据保留策略（天数）
    RETENTION_POLICY = {
        5: -1,      # L5 永久保留
        4: 1095,    # L4 保留 3 年
        3: 365,     # L3 保留 1 年
        2: 90,      # L2 保留 90 天
        1: 30,      # L1 保留 30 天
    }

class DevelopmentConfig(Config):
    """开发环境配置"""
    DEBUG = True
    TESTING = False

class ProductionConfig(Config):
    """生产环境配置"""
    DEBUG = False
    TESTING = False

class TestingConfig(Config):
    """测试环境配置"""
    DEBUG = True
    TESTING = True
    AUDIT_DB = ':memory:'

# 配置字典
config = {
    'development': DevelopmentConfig,
    'production': ProductionConfig,
    'testing': TestingConfig,
    'default': DevelopmentConfig
}
