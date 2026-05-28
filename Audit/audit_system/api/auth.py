"""
API 认证模块
"""
import jwt
from datetime import datetime, timedelta
from flask import Blueprint, request, jsonify, current_app
from werkzeug.security import check_password_hash
from functools import wraps

auth_bp = Blueprint('api_auth', __name__, url_prefix='/api/v1/auth')


@auth_bp.route('/token', methods=['POST'])
def generate_token():
    """
    生成 API Token

    请求体：
    {
        "username": "admin",
        "password": "password"
    }

    响应：
    {
        "token": "eyJ0eXAiOiJKV1QiLCJhbGc...",
        "expires_in": 86400
    }
    """
    data = request.get_json()
    if not data:
        return jsonify({'error': 'Missing request body'}), 400

    username = data.get('username')
    password = data.get('password')

    if not username or not password:
        return jsonify({'error': 'Missing username or password'}), 400

    # 验证 admin 账户
    if username == current_app.config['ADMIN_USERNAME']:
        if check_password_hash(current_app.config['ADMIN_PASSWORD_HASH'], password):
            # 生成 JWT Token
            token = jwt.encode({
                'client_id': 'control_center',
                'username': username,
                'exp': datetime.utcnow() + timedelta(hours=24)
            }, current_app.config['API_SECRET_KEY'], algorithm='HS256')

            return jsonify({
                'token': token,
                'expires_in': 86400
            })

    return jsonify({'error': 'Invalid credentials'}), 401


def api_auth_required(f):
    """API 认证装饰器"""
    @wraps(f)
    def decorated(*args, **kwargs):
        from flask import g

        token = request.headers.get('Authorization', '').replace('Bearer ', '')

        if not token:
            return jsonify({'error': 'Missing token'}), 401

        try:
            payload = jwt.decode(
                token,
                current_app.config['API_SECRET_KEY'],
                algorithms=['HS256']
            )
            g.api_client = payload['client_id']
            g.api_username = payload['username']
        except jwt.ExpiredSignatureError:
            return jsonify({'error': 'Token expired'}), 401
        except jwt.InvalidTokenError:
            return jsonify({'error': 'Invalid token'}), 401

        return f(*args, **kwargs)
    return decorated
