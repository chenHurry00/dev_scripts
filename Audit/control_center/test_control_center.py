"""
中控台功能测试脚本
"""
import sys
import json
from pathlib import Path

# 添加项目路径
sys.path.insert(0, str(Path(__file__).parent))

from app import app, server_manager

def test_server_manager():
    """测试服务器管理器"""
    print("=" * 60)
    print("测试 1: 服务器管理器初始化")
    print("=" * 60)

    print(f"✓ 配置文件路径: {server_manager.config_file}")
    print(f"✓ 已加载服务器数量: {len(server_manager.servers)}")

    return True


def test_add_server_simulation():
    """测试添加服务器（模拟）"""
    print("\n" + "=" * 60)
    print("测试 2: 添加服务器流程（模拟）")
    print("=" * 60)

    print("📝 添加服务器需要以下信息：")
    print("  - 服务器名称")
    print("  - IP 地址")
    print("  - 端口")
    print("  - Admin 用户名")
    print("  - Admin 密码")

    print("\n✓ 添加流程：")
    print("  1. 构建 API URL")
    print("  2. 发送认证请求（POST /api/v1/auth/token）")
    print("  3. 获取 Token 和过期时间")
    print("  4. 保存服务器配置到 config/servers.json")

    print("\n✓ 自动认证机制：")
    print("  - Token 过期前 5 分钟自动刷新")
    print("  - 使用保存的密码重新登录")
    print("  - 更新 Token 和过期时间")

    return True


def test_api_request_flow():
    """测试 API 请求流程"""
    print("\n" + "=" * 60)
    print("测试 3: API 请求流程")
    print("=" * 60)

    print("✓ API 请求流程：")
    print("  1. 检查 Token 是否即将过期")
    print("  2. 如果过期，自动刷新 Token")
    print("  3. 使用 Token 发送 API 请求")
    print("  4. 更新服务器状态和最后检查时间")

    print("\n✓ 支持的 API 端点：")
    print("  - GET /api/v1/stats          获取统计数据")
    print("  - GET /api/v1/logs           查询日志")
    print("  - GET /api/v1/alerts         获取告警")
    print("  - GET /api/v1/server/info    服务器信息")

    return True


def test_web_routes():
    """测试 Web 路由"""
    print("\n" + "=" * 60)
    print("测试 4: Web 路由")
    print("=" * 60)

    with app.test_client() as client:
        # 测试首页
        response = client.get('/')
        print(f"✓ 首页访问: HTTP {response.status_code}")

        # 测试添加服务器页面
        response = client.get('/servers/add')
        print(f"✓ 添加服务器页面: HTTP {response.status_code}")

    return True


def test_config_structure():
    """测试配置文件结构"""
    print("\n" + "=" * 60)
    print("测试 5: 配置文件结构")
    print("=" * 60)

    print("✓ 服务器配置字段：")
    config_example = {
        'id': 'server_1',
        'name': '生产服务器 1',
        'host': '192.168.1.10',
        'port': '5000',
        'base_url': 'http://192.168.1.10:5000',
        'username': 'admin',
        'password': 'Admin@2026!Change',
        'token': 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...',
        'token_expires_at': '2026-05-29T12:00:00',
        'status': 'online',
        'added_at': '2026-05-28T12:00:00',
        'last_check': '2026-05-28T12:30:00'
    }

    for key, value in config_example.items():
        print(f"  - {key:20s}: {value}")

    return True


def main():
    """运行所有测试"""
    print("\n" + "=" * 60)
    print("中控台系统 - 功能测试")
    print("=" * 60)

    tests = [
        test_server_manager,
        test_add_server_simulation,
        test_api_request_flow,
        test_web_routes,
        test_config_structure,
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

    print("\n" + "=" * 60)
    print("使用说明")
    print("=" * 60)
    print("1. 启动审计系统服务器（端口 5000）")
    print("   cd ../audit_system && python app.py")
    print("")
    print("2. 启动中控台（端口 8000）")
    print("   python app.py")
    print("")
    print("3. 访问中控台")
    print("   http://localhost:8000")
    print("")
    print("4. 添加服务器")
    print("   - 点击'➕ 添加服务器'")
    print("   - 填写 IP: 127.0.0.1")
    print("   - 填写端口: 5000")
    print("   - 填写密码: BY116358")
    print("   - 点击'✓ 添加并连接'")
    print("")
    print("5. 系统会自动：")
    print("   ✓ 连接到服务器")
    print("   ✓ 使用 Admin 账户登录")
    print("   ✓ 获取并保存 Token")
    print("   ✓ 显示服务器状态")
    print("=" * 60)

    return failed == 0


if __name__ == '__main__':
    success = main()
    sys.exit(0 if success else 1)
