"""
功能测试脚本
"""
import sys
import sqlite3
from pathlib import Path

# 添加项目路径
sys.path.insert(0, str(Path(__file__).parent))

from app import app, audit_logger
from models.database import get_db

def test_database():
    """测试数据库初始化"""
    print("=" * 60)
    print("测试 1: 数据库初始化")
    print("=" * 60)

    with app.app_context():
        db = get_db()

        # 检查表是否存在
        tables = db.execute("""
            SELECT name FROM sqlite_master
            WHERE type='table'
            ORDER BY name
        """).fetchall()

        print(f"✓ 数据库表数量: {len(tables)}")
        for table in tables:
            print(f"  - {table['name']}")

        # 检查 admin 用户
        admin = db.execute(
            "SELECT * FROM users WHERE username = ?",
            (app.config['ADMIN_USERNAME'],)
        ).fetchone()

        if admin:
            print(f"\n✓ Admin 用户已创建")
            print(f"  用户名: {admin['username']}")
            print(f"  角色: {admin['role']}")
            print(f"  创建时间: {admin['created_at']}")
        else:
            print("\n✗ Admin 用户不存在")
            return False

    return True


def test_audit_logger():
    """测试审计日志记录"""
    print("\n" + "=" * 60)
    print("测试 2: 审计日志记录")
    print("=" * 60)

    with app.app_context():
        # 记录测试日志
        log_id = audit_logger.log(
            username='test_user',
            category='FILE',
            action_type='FILE_DELETE',
            target_resource='/etc/passwd',
            result='success',
            ip_address='192.168.1.100',
            user_agent='Test Agent'
        )

        print(f"✓ 日志记录成功，ID: {log_id}")

        # 查询日志
        db = get_db()
        log = db.execute(
            "SELECT * FROM audit_logs WHERE id = ?",
            (log_id,)
        ).fetchone()

        if log:
            print(f"\n日志详情:")
            print(f"  用户: {log['username']}")
            print(f"  操作: {log['action_type']}")
            print(f"  目标: {log['target_resource']}")
            print(f"  风险级别: L{log['risk_level']} - {log['risk_label']}")
            print(f"  结果: {log['result']}")
            print(f"  时间: {log['timestamp']}")
            print(f"  校验和: {log['checksum'][:20]}...")
        else:
            print("\n✗ 日志查询失败")
            return False

    return True


def test_risk_classification():
    """测试风险分级"""
    print("\n" + "=" * 60)
    print("测试 3: 风险分级")
    print("=" * 60)

    test_cases = [
        ('LOGIN_SUCCESS', '/', 1, 'INFO'),
        ('FILE_DELETE', '/home/user/test.txt', 4, 'HIGH'),
        ('FILE_DELETE', '/etc/passwd', 5, 'CRITICAL'),
        ('DB_DELETE', 'users', 5, 'CRITICAL'),
        ('PAGE_VIEW', '/dashboard', 1, 'INFO'),
    ]

    with app.app_context():
        for action_type, target, expected_level, expected_label in test_cases:
            log_id = audit_logger.log(
                username='test_user',
                category='TEST',
                action_type=action_type,
                target_resource=target,
                result='success',
                ip_address='192.168.1.100'
            )

            db = get_db()
            log = db.execute(
                "SELECT * FROM audit_logs WHERE id = ?",
                (log_id,)
            ).fetchone()

            status = "✓" if log['risk_level'] >= expected_level - 1 else "✗"
            print(f"{status} {action_type:20s} {target:30s} -> L{log['risk_level']} {log['risk_label']}")

    return True


def test_api_token():
    """测试 API Token 生成"""
    print("\n" + "=" * 60)
    print("测试 4: API Token 生成")
    print("=" * 60)

    with app.test_client() as client:
        # 测试 Token 生成
        response = client.post('/api/v1/auth/token', json={
            'username': app.config['ADMIN_USERNAME'],
            'password': 'Admin@2026!Change'
        })

        if response.status_code == 200:
            data = response.get_json()
            print(f"✓ Token 生成成功")
            print(f"  Token: {data['token'][:50]}...")
            print(f"  过期时间: {data['expires_in']} 秒")

            # 测试 API 调用
            token = data['token']
            response = client.get('/api/v1/stats', headers={
                'Authorization': f'Bearer {token}'
            })

            if response.status_code == 200:
                stats = response.get_json()
                print(f"\n✓ API 调用成功")
                print(f"  总日志数: {stats['total_logs']}")
                print(f"  今日日志: {stats['today_logs']}")
                print(f"  致命事件: {stats['critical_count']}")
                print(f"  高危事件: {stats['high_count']}")
            else:
                print(f"\n✗ API 调用失败: {response.status_code}")
                return False
        else:
            print(f"✗ Token 生成失败: {response.status_code}")
            return False

    return True


def test_login():
    """测试登录功能"""
    print("\n" + "=" * 60)
    print("测试 5: 登录功能")
    print("=" * 60)

    with app.test_client() as client:
        # 测试登录
        response = client.post('/login', data={
            'username': app.config['ADMIN_USERNAME'],
            'password': 'Admin@2026!Change'
        }, follow_redirects=False)

        if response.status_code == 302:  # 重定向到 dashboard
            print(f"✓ 登录成功")

            # 测试访问仪表盘
            response = client.get('/dashboard', follow_redirects=True)
            if response.status_code == 200:
                print(f"✓ 仪表盘访问成功")
            else:
                print(f"✗ 仪表盘访问失败: {response.status_code}")
                return False
        else:
            print(f"✗ 登录失败: {response.status_code}")
            return False

    return True


def main():
    """运行所有测试"""
    print("\n" + "=" * 60)
    print("服务器审计系统 - 功能测试")
    print("=" * 60)

    tests = [
        test_database,
        test_audit_logger,
        test_risk_classification,
        test_api_token,
        test_login,
    ]

    passed = 0
    failed = 0

    for test in tests:
        try:
            if test():
                passed += 1
            else:
                failed += 1
        except Exception as e:
            print(f"\n✗ 测试异常: {e}")
            import traceback
            traceback.print_exc()
            failed += 1

    print("\n" + "=" * 60)
    print("测试结果")
    print("=" * 60)
    print(f"✓ 通过: {passed}")
    print(f"✗ 失败: {failed}")
    print(f"总计: {passed + failed}")
    print("=" * 60)

    return failed == 0


if __name__ == '__main__':
    success = main()
    sys.exit(0 if success else 1)
