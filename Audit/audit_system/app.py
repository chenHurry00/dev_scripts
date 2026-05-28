"""
服务器审计系统 - 主应用
"""
import os
import logging
import sqlite3
import time
from pathlib import Path
from datetime import datetime
from flask import Flask, request, session, g, render_template, redirect, url_for, flash, jsonify
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps

# ============= Admin 密码配置 =============
# 优先级：环境变量 > 默认值
ADMIN_USERNAME = os.environ.get('AUDIT_ADMIN_USER', 'admin')
ADMIN_PASSWORD = os.environ.get('AUDIT_ADMIN_PASSWORD', 'BY116358')

# 安全提示
# if ADMIN_PASSWORD == 'Admin@2026!Change':
#     logging.warning('⚠️  使用默认 Admin 密码，生产环境请通过环境变量设置！')
#     logging.warning('   export AUDIT_ADMIN_PASSWORD=your_secure_password')

# ============= Flask 应用初始化 =============
from config import config

app = Flask(__name__)

# 加载配置
env = os.environ.get('FLASK_ENV', 'development')
app.config.from_object(config[env])
app.config['ADMIN_USERNAME'] = ADMIN_USERNAME
app.config['ADMIN_PASSWORD_HASH'] = generate_password_hash(ADMIN_PASSWORD)

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s'
)
logger = logging.getLogger(__name__)

# ============= 初始化审计系统 =============
from audit.logger import AuditLogger
from models.database import get_db, close_db, init_db

# 创建审计日志记录器
audit_logger = AuditLogger(
    db_path=app.config['AUDIT_DB'],
    log_dir=app.config['AUDIT_LOG_DIR']
)
app.extensions['audit_logger'] = audit_logger

# 初始化数据库
init_db(app)

# 注册数据库关闭钩子
app.teardown_appcontext(close_db)

# 注册 API 蓝图
from api.auth import auth_bp
from api.routes import api_bp

app.register_blueprint(auth_bp)
app.register_blueprint(api_bp)


# ============= Admin 密码管理 =============
def init_or_refresh_admin():
    """
    应用启动时执行：
    1. 如果 admin 用户不存在，创建
    2. 如果已存在，更新密码为当前配置的密码
    """
    db_path = Path(app.config['AUDIT_DB'])
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # 检查 admin 用户是否存在
    cursor.execute("SELECT id FROM users WHERE username = ?", (ADMIN_USERNAME,))
    admin_exists = cursor.fetchone()

    if admin_exists:
        # 更新密码
        cursor.execute("""
            UPDATE users
            SET password_hash = ?,
                failed_login_count = 0,
                locked_until = NULL,
                is_active = 1
            WHERE username = ?
        """, (app.config['ADMIN_PASSWORD_HASH'], ADMIN_USERNAME))
        logger.info(f'✓ Admin 用户 "{ADMIN_USERNAME}" 密码已刷新')
    else:
        # 创建 admin 用户
        cursor.execute("""
            INSERT INTO users (username, password_hash, role, email)
            VALUES (?, ?, 'admin', 'admin@localhost')
        """, (ADMIN_USERNAME, app.config['ADMIN_PASSWORD_HASH']))
        logger.info(f'✓ Admin 用户 "{ADMIN_USERNAME}" 已创建')

    conn.commit()
    conn.close()


# 应用启动时执行
with app.app_context():
    init_or_refresh_admin()
    logger.info('============================================================')
    logger.info('服务器审计系统启动')
    logger.info(f'Admin 用户: {ADMIN_USERNAME}')
    logger.info(f'数据库: {app.config["AUDIT_DB"]}')
    logger.info(f'日志目录: {app.config["AUDIT_LOG_DIR"]}')
    logger.info('============================================================')


# ============= 认证装饰器 =============
def login_required(f):
    """登录验证装饰器"""
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated


def admin_required(f):
    """Admin 权限验证装饰器"""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('is_admin'):
            audit_logger.log(
                username=session.get('username', 'anonymous'),
                category='ACCESS',
                action_type='FORBIDDEN_ACCESS',
                target_resource=request.path,
                result='failure',
                request=request
            )
            flash('需要管理员权限', 'error')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated


# ============= 全局请求钩子 =============
@app.before_request
def before_request():
    """请求前钩子"""
    g.start_time = time.time()
    g.request_id = os.urandom(8).hex()


@app.after_request
def after_request(response):
    """请求后钩子 - 禁用 Web 访问日志"""
    # 不记录 Web 界面访问日志
    # 只记录 Linux 终端命令（通过 audit_command.py）
    return response


# ============= 路由 =============
@app.route('/')
def index():
    """首页"""
    if 'user_id' in session:
        return redirect(url_for('dashboard'))
    return redirect(url_for('login'))


@app.route('/login', methods=['GET', 'POST'])
def login():
    """登录页面"""
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')

        if not username or not password:
            flash('请输入用户名和密码', 'error')
            return render_template('auth/login.html')

        # 查询用户
        db = get_db()
        user = db.execute(
            'SELECT * FROM users WHERE username = ?',
            (username,)
        ).fetchone()

        if user and check_password_hash(user['password_hash'], password):
            # 登录成功
            session['user_id'] = user['id']
            session['username'] = user['username']
            session['is_admin'] = (user['role'] == 'admin')
            session['session_id'] = os.urandom(16).hex()

            # 更新最后登录时间
            db.execute(
                'UPDATE users SET last_login = ?, login_ip = ? WHERE id = ?',
                (datetime.now().isoformat(), request.remote_addr, user['id'])
            )
            db.commit()

            # 记录登录会话
            db.execute("""
                INSERT INTO login_sessions (
                    session_id, user_id, username, ip_address, user_agent
                ) VALUES (?, ?, ?, ?, ?)
            """, (
                session['session_id'],
                user['id'],
                user['username'],
                request.remote_addr,
                request.headers.get('User-Agent', '')
            ))
            db.commit()

            # 记录审计日志
            audit_logger.log(
                user_id=user['id'],
                username=user['username'],
                session_id=session['session_id'],
                category='AUTH',
                action_type='LOGIN_SUCCESS',
                target_resource=username,
                result='success',
                request=request
            )

            logger.info(f'用户登录: {username} ({user["role"]}) - IP: {request.remote_addr}')

            return redirect(url_for('dashboard'))
        else:
            # 登录失败
            audit_logger.log(
                username=username,
                category='AUTH',
                action_type='LOGIN_FAIL',
                target_resource=username,
                result='failure',
                request=request
            )

            flash('用户名或密码错误', 'error')

    return render_template('auth/login.html')


@app.route('/logout')
@login_required
def logout():
    """登出"""
    username = session.get('username')
    session_id = session.get('session_id')

    # 更新会话状态
    db = get_db()
    db.execute("""
        UPDATE login_sessions
        SET is_active = 0, logout_time = ?, logout_reason = 'manual'
        WHERE session_id = ?
    """, (datetime.now().isoformat(), session_id))
    db.commit()

    # 记录审计日志
    audit_logger.log(
        username=username,
        session_id=session_id,
        category='AUTH',
        action_type='LOGOUT',
        target_resource=username,
        result='success',
        request=request
    )

    session.clear()
    flash('已退出登录', 'success')
    return redirect(url_for('login'))


@app.route('/dashboard')
@login_required
@admin_required
def dashboard():
    """仪表盘"""
    db = get_db()

    # 统计数据
    today = datetime.now().strftime('%Y-%m-%d')

    stats = {
        'total_logs': db.execute('SELECT COUNT(*) FROM audit_logs').fetchone()[0],
        'today_logs': db.execute(
            "SELECT COUNT(*) FROM audit_logs WHERE DATE(timestamp) = ?", (today,)
        ).fetchone()[0],
        'critical_count': db.execute(
            'SELECT COUNT(*) FROM audit_logs WHERE risk_level = 5'
        ).fetchone()[0],
        'high_count': db.execute(
            'SELECT COUNT(*) FROM audit_logs WHERE risk_level = 4'
        ).fetchone()[0],
        'online_users': db.execute(
            'SELECT COUNT(*) FROM login_sessions WHERE is_active = 1'
        ).fetchone()[0],
        'unread_alerts': db.execute(
            'SELECT COUNT(*) FROM alerts WHERE is_read = 0'
        ).fetchone()[0] if db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='alerts'"
        ).fetchone() else 0
    }

    # 最近日志
    recent_logs = db.execute("""
        SELECT * FROM audit_logs
        ORDER BY timestamp DESC
        LIMIT 10
    """).fetchall()

    return render_template('admin/dashboard.html', stats=stats, recent_logs=recent_logs)


@app.route('/logs')
@login_required
@admin_required
def logs():
    """日志列表"""
    db = get_db()

    # 分页参数
    page = request.args.get('page', 1, type=int)
    per_page = 50

    # 筛选参数
    username = request.args.get('username')
    risk_min = request.args.get('risk_min', type=int)
    category = request.args.get('category')

    # 构建查询
    query = 'SELECT * FROM audit_logs WHERE 1=1'
    params = []

    if username:
        query += ' AND username = ?'
        params.append(username)
    if risk_min:
        query += ' AND risk_level >= ?'
        params.append(risk_min)
    if category:
        query += ' AND action_category = ?'
        params.append(category)

    # 总数
    total = db.execute(query.replace('SELECT *', 'SELECT COUNT(*)'), params).fetchone()[0]

    # 分页
    offset = (page - 1) * per_page
    query += f' ORDER BY timestamp DESC LIMIT {per_page} OFFSET {offset}'

    logs = db.execute(query, params).fetchall()

    return render_template('admin/logs.html',
                         logs=logs,
                         page=page,
                         per_page=per_page,
                         total=total)


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
