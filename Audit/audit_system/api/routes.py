"""
REST API 路由
"""
from flask import Blueprint, request, jsonify, g
from datetime import datetime
import socket
import platform
from .auth import api_auth_required
from models.database import get_db

api_bp = Blueprint('api', __name__, url_prefix='/api/v1')


@api_bp.route('/logs', methods=['GET'])
@api_auth_required
def get_logs():
    """
    查询审计日志

    查询参数：
    - start: 开始时间 (ISO 8601)
    - end: 结束时间 (ISO 8601)
    - user: 用户名
    - risk_min: 最小风险级别 (1-5)
    - category: 操作分类
    - page: 页码 (默认 1)
    - per_page: 每页数量 (默认 50)
    """
    # 参数解析
    start = request.args.get('start')
    end = request.args.get('end')
    user = request.args.get('user')
    risk_min = request.args.get('risk_min', type=int)
    category = request.args.get('category')
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 50, type=int)

    # 构建查询
    conn = get_db()
    query = "SELECT * FROM audit_logs WHERE 1=1"
    params = []

    if start:
        query += " AND timestamp >= ?"
        params.append(start)
    if end:
        query += " AND timestamp <= ?"
        params.append(end)
    if user:
        query += " AND username = ?"
        params.append(user)
    if risk_min:
        query += " AND risk_level >= ?"
        params.append(risk_min)
    if category:
        query += " AND action_category = ?"
        params.append(category)

    # 总数
    count_query = query.replace('SELECT *', 'SELECT COUNT(*)')
    total = conn.execute(count_query, params).fetchone()[0]

    # 分页
    offset = (page - 1) * per_page
    query += f" ORDER BY timestamp DESC LIMIT {per_page} OFFSET {offset}"

    cursor = conn.execute(query, params)
    logs = [dict(row) for row in cursor.fetchall()]

    return jsonify({
        'total': total,
        'page': page,
        'per_page': per_page,
        'data': logs
    })


@api_bp.route('/stats', methods=['GET'])
@api_auth_required
def get_stats():
    """获取统计数据"""
    conn = get_db()

    today = datetime.now().strftime('%Y-%m-%d')

    stats = {
        'total_logs': conn.execute("SELECT COUNT(*) FROM audit_logs").fetchone()[0],
        'today_logs': conn.execute(
            "SELECT COUNT(*) FROM audit_logs WHERE DATE(timestamp) = ?", (today,)
        ).fetchone()[0],
        'critical_count': conn.execute(
            "SELECT COUNT(*) FROM audit_logs WHERE risk_level = 5"
        ).fetchone()[0],
        'high_count': conn.execute(
            "SELECT COUNT(*) FROM audit_logs WHERE risk_level = 4"
        ).fetchone()[0],
        'online_users': conn.execute(
            "SELECT COUNT(*) FROM login_sessions WHERE is_active = 1"
        ).fetchone()[0],
        'unread_alerts': 0
    }

    # 检查 alerts 表是否存在
    alerts_table = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='alerts'"
    ).fetchone()

    if alerts_table:
        stats['unread_alerts'] = conn.execute(
            "SELECT COUNT(*) FROM alerts WHERE is_read = 0"
        ).fetchone()[0]

    return jsonify(stats)


@api_bp.route('/alerts', methods=['GET'])
@api_auth_required
def get_alerts():
    """获取告警列表"""
    unread_only = request.args.get('unread_only', 'false').lower() == 'true'

    conn = get_db()

    # 检查 alerts 表是否存在
    alerts_table = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='alerts'"
    ).fetchone()

    if not alerts_table:
        return jsonify({'data': []})

    query = "SELECT * FROM alerts"

    if unread_only:
        query += " WHERE is_read = 0"

    query += " ORDER BY created_at DESC LIMIT 100"

    cursor = conn.execute(query)
    alerts = [dict(row) for row in cursor.fetchall()]

    return jsonify({'data': alerts})


@api_bp.route('/server/info', methods=['GET'])
@api_auth_required
def get_server_info():
    """获取服务器基本信息"""
    return jsonify({
        'hostname': socket.gethostname(),
        'platform': platform.system(),
        'version': platform.version(),
        'python_version': platform.python_version()
    })
