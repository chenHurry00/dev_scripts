"""
审计装饰器 - 用于路由函数的审计日志记录
"""
import time
from functools import wraps
from flask import request, session, g


def audit_log(category: str, action_type: str,
              target_extractor=None, level_override=None):
    """
    路由函数审计装饰器

    Args:
        category: 操作分类 (AUTH/FILE/SYSTEM/DATA/CONFIG/ACCESS)
        action_type: 操作类型
        target_extractor: 目标资源提取函数
        level_override: 强制指定风险级别

    用法：
        @app.route('/files/delete', methods=['POST'])
        @login_required
        @audit_log(category='FILE', action_type='FILE_DELETE',
                   target_extractor=lambda: request.form.get('path'))
        def delete_file():
            ...
    """
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            from flask import current_app

            start_time = time.time()
            target = target_extractor() if target_extractor else request.path

            try:
                result = f(*args, **kwargs)
                status = 'success'
                error_msg = None
                status_code = 200
            except Exception as e:
                result = None
                status = 'error'
                error_msg = str(e)
                status_code = 500
                raise
            finally:
                duration_ms = int((time.time() - start_time) * 1000)

                # 获取审计日志记录器
                audit_logger = current_app.extensions.get('audit_logger')
                if audit_logger:
                    audit_logger.log(
                        user_id=session.get('user_id'),
                        username=session.get('username', 'anonymous'),
                        session_id=session.get('session_id', ''),
                        category=category,
                        action_type=action_type,
                        target_resource=target,
                        result=status,
                        error_message=error_msg,
                        status_code=status_code,
                        duration_ms=duration_ms,
                        request=request
                    )

            return result
        return wrapper
    return decorator
