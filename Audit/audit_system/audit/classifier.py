"""
操作分类器 - 根据操作类型、目标资源、上下文环境计算风险级别
"""
from datetime import datetime


class OperationClassifier:
    """操作分类与风险评分"""

    # 基础风险映射表 (操作类型 -> (风险级别, 分类))
    BASE_RISK_MAP = {
        # 认证类
        'LOGIN_SUCCESS': (1, 'AUTH'),
        'LOGIN_FAIL': (2, 'AUTH'),
        'LOGOUT': (1, 'AUTH'),
        'PASSWORD_CHANGE': (3, 'AUTH'),
        'PRIVILEGE_CHANGE': (4, 'AUTH'),

        # 文件类
        'FILE_READ': (1, 'FILE'),
        'FILE_WRITE': (2, 'FILE'),
        'FILE_DELETE': (4, 'FILE'),
        'FILE_UPLOAD': (2, 'FILE'),
        'FILE_DOWNLOAD': (1, 'FILE'),
        'FILE_PERMISSION': (3, 'FILE'),

        # 系统类
        'CMD_EXEC': (3, 'SYSTEM'),
        'PROCESS_KILL': (3, 'SYSTEM'),
        'SERVICE_CHANGE': (4, 'SYSTEM'),
        'CRON_CHANGE': (4, 'SYSTEM'),
        'NETWORK_CHANGE': (4, 'SYSTEM'),

        # 数据类
        'DB_READ': (1, 'DATA'),
        'DB_WRITE': (2, 'DATA'),
        'DB_DELETE': (5, 'DATA'),
        'DB_SCHEMA_CHANGE': (4, 'DATA'),
        'DATA_EXPORT': (3, 'DATA'),

        # 配置类
        'APP_CONFIG': (3, 'CONFIG'),
        'SYS_CONFIG': (4, 'CONFIG'),
        'SECURITY_CONFIG': (4, 'CONFIG'),

        # 访问类
        'PAGE_VIEW': (1, 'ACCESS'),
        'API_CALL': (1, 'ACCESS'),
        'RESOURCE_ACCESS': (2, 'ACCESS'),
        'FORBIDDEN_ACCESS': (3, 'ACCESS'),
        'ADMIN_ACCESS': (2, 'ACCESS'),
    }

    # 敏感路径列表
    SENSITIVE_PATHS = [
        '/etc/',
        '/root/',
        '/var/log/',
        '/boot/',
        '/sys/',
        '/proc/',
        'passwd',
        'shadow',
        'sudoers',
    ]

    # 风险级别标签
    RISK_LABELS = {
        1: 'INFO',
        2: 'LOW',
        3: 'MEDIUM',
        4: 'HIGH',
        5: 'CRITICAL',
    }

    def classify(self, record: dict) -> tuple:
        """
        分类操作并计算风险级别

        Args:
            record: 审计记录字典

        Returns:
            (风险级别, 风险标签) 元组
        """
        action_type = record.get('action_type', 'UNKNOWN')

        # 获取基础风险级别
        base_level, category = self.BASE_RISK_MAP.get(
            action_type,
            (2, record.get('action_category', 'ACCESS'))
        )

        # 计算上下文加权
        bonus = self._context_bonus(record)

        # 最终风险级别（不超过 5）
        final_level = min(5, base_level + bonus)

        return final_level, self.RISK_LABELS[final_level]

    def _context_bonus(self, record: dict) -> int:
        """
        计算上下文加权分数

        加权规则：
        - 敏感路径 +1
        - 深夜操作 +1
        - 批量操作 +1
        """
        bonus = 0
        target = record.get('target_resource', '')

        # 敏感路径加权
        if any(sensitive in target for sensitive in self.SENSITIVE_PATHS):
            bonus += 1

        # 深夜操作加权（00:00 - 06:00）
        timestamp = record.get('timestamp')
        if timestamp:
            try:
                dt = datetime.fromisoformat(timestamp)
                if 0 <= dt.hour < 6:
                    bonus += 1
            except:
                pass

        # 批量操作检测（可以通过 action_detail 判断）
        action_detail = record.get('action_detail', '')
        if 'batch' in action_detail.lower() or 'multiple' in action_detail.lower():
            bonus += 1

        return bonus

    def get_category(self, action_type: str) -> str:
        """获取操作分类"""
        return self.BASE_RISK_MAP.get(action_type, (2, 'ACCESS'))[1]
