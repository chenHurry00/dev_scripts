"""
中控台系统 - 服务器管理与自动认证
"""
import os
import json
import requests
import logging
from pathlib import Path
from datetime import datetime, timedelta
from flask import Flask, render_template, request, jsonify, flash, redirect, url_for

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', os.urandom(24).hex())

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s'
)
logger = logging.getLogger(__name__)

# 服务器配置文件路径
CONFIG_DIR = Path(__file__).parent / 'config'
CONFIG_DIR.mkdir(exist_ok=True)
SERVERS_CONFIG_FILE = CONFIG_DIR / 'servers.json'


class ServerManager:
    """服务器管理器 - 自动认证和 Token 管理"""

    def __init__(self, config_file):
        self.config_file = Path(config_file)
        self.servers = self._load_servers()

    def _load_servers(self):
        """加载服务器配置"""
        if self.config_file.exists():
            try:
                with open(self.config_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                logger.error(f'加载服务器配置失败: {e}')
        return []

    def _save_servers(self):
        """保存服务器配置"""
        try:
            with open(self.config_file, 'w', encoding='utf-8') as f:
                json.dump(self.servers, f, ensure_ascii=False, indent=2)
            logger.info('服务器配置已保存')
        except Exception as e:
            logger.error(f'保存服务器配置失败: {e}')

    def add_server(self, name, host, port, username, password, description=''):
        """
        添加服务器并自动获取 Token

        Args:
            name: 服务器名称
            host: IP 地址
            port: 端口
            username: Admin 用户名
            password: Admin 密码
            description: 描述

        Returns:
            (success, message, server_id)
        """
        # 构建 URL
        base_url = f"http://{host}:{port}"

        # 尝试获取 Token
        try:
            response = requests.post(
                f"{base_url}/api/v1/auth/token",
                json={'username': username, 'password': password},
                timeout=10,
                verify=False
            )

            if response.status_code == 200:
                data = response.json()
                token = data['token']
                expires_in = data['expires_in']

                # 计算过期时间
                expires_at = (datetime.now() + timedelta(seconds=expires_in)).isoformat()

                # 生成服务器 ID
                server_id = f"server_{len(self.servers) + 1}"

                # 保存服务器配置
                server_config = {
                    'id': server_id,
                    'name': name,
                    'host': host,
                    'port': port,
                    'base_url': base_url,
                    'username': username,
                    'password': password,  # 加密存储（生产环境应使用加密）
                    'description': description,
                    'token': token,
                    'token_expires_at': expires_at,
                    'status': 'online',
                    'added_at': datetime.now().isoformat(),
                    'last_check': datetime.now().isoformat()
                }

                self.servers.append(server_config)
                self._save_servers()

                logger.info(f'服务器添加成功: {name} ({host}:{port})')
                return True, '服务器添加成功', server_id
            else:
                error_msg = f'认证失败: HTTP {response.status_code}'
                logger.error(f'服务器 {name} 认证失败: {error_msg}')
                return False, error_msg, None

        except requests.exceptions.ConnectionError:
            error_msg = f'无法连接到服务器 {host}:{port}'
            logger.error(error_msg)
            return False, error_msg, None
        except Exception as e:
            error_msg = f'添加服务器失败: {str(e)}'
            logger.error(error_msg)
            return False, error_msg, None

    def get_server(self, server_id):
        """获取服务器配置"""
        for server in self.servers:
            if server['id'] == server_id:
                return server
        return None

    def get_all_servers(self):
        """获取所有服务器"""
        return self.servers

    def remove_server(self, server_id):
        """删除服务器"""
        self.servers = [s for s in self.servers if s['id'] != server_id]
        self._save_servers()
        logger.info(f'服务器已删除: {server_id}')

    def refresh_token(self, server_id):
        """刷新服务器 Token"""
        server = self.get_server(server_id)
        if not server:
            return False, '服务器不存在'

        try:
            response = requests.post(
                f"{server['base_url']}/api/v1/auth/token",
                json={'username': server['username'], 'password': server['password']},
                timeout=10,
                verify=False
            )

            if response.status_code == 200:
                data = response.json()
                server['token'] = data['token']
                server['token_expires_at'] = (
                    datetime.now() + timedelta(seconds=data['expires_in'])
                ).isoformat()
                server['status'] = 'online'
                server['last_check'] = datetime.now().isoformat()

                self._save_servers()
                logger.info(f'Token 刷新成功: {server["name"]}')
                return True, 'Token 刷新成功'
            else:
                server['status'] = 'auth_failed'
                self._save_servers()
                return False, f'认证失败: HTTP {response.status_code}'

        except Exception as e:
            server['status'] = 'offline'
            self._save_servers()
            return False, f'刷新失败: {str(e)}'

    def ensure_valid_token(self, server_id):
        """确保 Token 有效，如果过期则自动刷新"""
        server = self.get_server(server_id)
        if not server:
            return False

        # 检查 Token 是否即将过期（提前 5 分钟刷新）
        expires_at = datetime.fromisoformat(server['token_expires_at'])
        if datetime.now() >= expires_at - timedelta(minutes=5):
            success, _ = self.refresh_token(server_id)
            return success

        return True

    def api_request(self, server_id, endpoint, method='GET', **kwargs):
        """
        向服务器发送 API 请求（自动处理 Token）

        Args:
            server_id: 服务器 ID
            endpoint: API 端点（如 /api/v1/stats）
            method: HTTP 方法
            **kwargs: requests 参数

        Returns:
            (success, data/error_message)
        """
        server = self.get_server(server_id)
        if not server:
            return False, '服务器不存在'

        # 确保 Token 有效
        if not self.ensure_valid_token(server_id):
            return False, 'Token 刷新失败'

        # 发送请求
        try:
            response = requests.request(
                method,
                f"{server['base_url']}{endpoint}",
                headers={'Authorization': f"Bearer {server['token']}"},
                timeout=kwargs.pop('timeout', 30),
                verify=False,
                **kwargs
            )

            if response.status_code == 200:
                server['status'] = 'online'
                server['last_check'] = datetime.now().isoformat()
                self._save_servers()
                return True, response.json()
            else:
                return False, f'请求失败: HTTP {response.status_code}'

        except Exception as e:
            server['status'] = 'offline'
            self._save_servers()
            return False, f'请求异常: {str(e)}'


# 初始化服务器管理器
server_manager = ServerManager(SERVERS_CONFIG_FILE)


# ============= 路由 =============
@app.route('/')
def index():
    """首页 - 服务器列表"""
    servers = server_manager.get_all_servers()
    return render_template('control_center/index.html', servers=servers)


@app.route('/servers/add', methods=['GET', 'POST'])
def add_server():
    """添加服务器"""
    if request.method == 'POST':
        name = request.form.get('name')
        host = request.form.get('host')
        port = request.form.get('port', '5000')
        username = request.form.get('username', 'admin')
        password = request.form.get('password')
        description = request.form.get('description', '')

        if not all([name, host, password]):
            flash('请填写必填字段', 'error')
            return render_template('control_center/add_server.html')

        success, message, server_id = server_manager.add_server(
            name, host, port, username, password, description
        )

        if success:
            flash(f'✓ {message}', 'success')
            return redirect(url_for('index'))
        else:
            flash(f'✗ {message}', 'error')

    return render_template('control_center/add_server.html')


@app.route('/servers/<server_id>/remove', methods=['POST'])
def remove_server(server_id):
    """删除服务器"""
    server_manager.remove_server(server_id)
    flash('服务器已删除', 'success')
    return redirect(url_for('index'))


@app.route('/servers/<server_id>/refresh', methods=['POST'])
def refresh_token(server_id):
    """刷新 Token"""
    success, message = server_manager.refresh_token(server_id)
    if success:
        flash(f'✓ {message}', 'success')
    else:
        flash(f'✗ {message}', 'error')
    return redirect(url_for('index'))


@app.route('/servers/<server_id>/dashboard')
def server_dashboard(server_id):
    """服务器仪表盘"""
    server = server_manager.get_server(server_id)
    if not server:
        flash('服务器不存在', 'error')
        return redirect(url_for('index'))

    # 获取统计数据
    success, stats = server_manager.api_request(server_id, '/api/v1/stats')
    if not success:
        flash(f'获取统计数据失败: {stats}', 'error')
        stats = {}

    # 获取最近日志
    success, logs_data = server_manager.api_request(
        server_id, '/api/v1/logs', params={'per_page': 10}
    )
    logs = logs_data.get('data', []) if success else []

    return render_template('control_center/dashboard.html',
                         server=server,
                         stats=stats,
                         logs=logs)


@app.route('/servers/<server_id>/logs')
def server_logs(server_id):
    """服务器日志查询"""
    server = server_manager.get_server(server_id)
    if not server:
        flash('服务器不存在', 'error')
        return redirect(url_for('index'))

    # 获取查询参数
    params = {
        'page': request.args.get('page', 1, type=int),
        'per_page': 50,
        'user': request.args.get('user'),
        'risk_min': request.args.get('risk_min', type=int),
        'category': request.args.get('category')
    }

    # 移除空参数
    params = {k: v for k, v in params.items() if v}

    # 获取日志
    success, logs_data = server_manager.api_request(
        server_id, '/api/v1/logs', params=params
    )

    if success:
        logs = logs_data.get('data', [])
        total = logs_data.get('total', 0)
        page = logs_data.get('page', 1)
    else:
        flash(f'获取日志失败: {logs_data}', 'error')
        logs = []
        total = 0
        page = 1

    return render_template('control_center/logs.html',
                         server=server,
                         logs=logs,
                         total=total,
                         page=page,
                         per_page=50)


@app.route('/api/servers/all/stats')
def all_servers_stats():
    """所有服务器统计汇总（API）"""
    results = []

    for server in server_manager.get_all_servers():
        success, stats = server_manager.api_request(server['id'], '/api/v1/stats')

        if success:
            stats['server_id'] = server['id']
            stats['server_name'] = server['name']
            stats['status'] = 'online'
        else:
            stats = {
                'server_id': server['id'],
                'server_name': server['name'],
                'status': 'offline',
                'error': stats
            }

        results.append(stats)

    return jsonify({'servers': results})


@app.route('/alerts')
def all_alerts():
    """所有服务器告警汇总"""
    all_alerts = []

    for server in server_manager.get_all_servers():
        success, alerts_data = server_manager.api_request(
            server['id'], '/api/v1/alerts', params={'unread_only': 'true'}
        )

        if success:
            for alert in alerts_data.get('data', []):
                alert['server_id'] = server['id']
                alert['server_name'] = server['name']
                all_alerts.append(alert)

    # 按时间排序
    all_alerts.sort(key=lambda x: x.get('created_at', ''), reverse=True)

    return render_template('control_center/alerts.html', alerts=all_alerts)


if __name__ == '__main__':
    logger.info('============================================================')
    logger.info('中控台系统启动')
    logger.info(f'配置文件: {SERVERS_CONFIG_FILE}')
    logger.info('访问地址: http://localhost:8000')
    logger.info('============================================================')

    app.run(host='127.0.0.1', port=8000, debug=True)
