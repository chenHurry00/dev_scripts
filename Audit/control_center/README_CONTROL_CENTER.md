# 审计系统中控台 - 使用指南

> 统一管理多台服务器的审计系统，支持自动认证和 Token 管理

## 📋 目录

- [核心特性](#核心特性)
- [快速开始](#快速开始)
- [自动认证原理](#自动认证原理)
- [功能说明](#功能说明)
- [配置文件](#配置文件)
- [API 调用](#api-调用)
- [安全建议](#安全建议)
- [故障排查](#故障排查)

---

## 🌟 核心特性

### 自动认证机制

**传统方式的痛点：**
- ❌ 需要手动获取 Token
- ❌ Token 过期后需要重新登录
- ❌ 管理多个服务器时需要记住多个 Token
- ❌ 需要编写脚本处理 Token 刷新

**中控台的解决方案：**
- ✅ **一键添加**：填写 IP、端口、Admin 密码即可
- ✅ **自动登录**：系统自动使用 Admin 账户获取 Token
- ✅ **自动刷新**：Token 过期前 5 分钟自动刷新
- ✅ **持久化存储**：Token 和配置保存在本地，重启后自动恢复
- ✅ **零维护**：添加后完全自动化，无需手动操作

### 功能特性

- ✅ 多服务器统一管理
- ✅ 实时状态监控（在线/离线/认证失败）
- ✅ 跨服务器日志查询
- ✅ 告警汇总展示
- ✅ 统计数据可视化
- ✅ 服务器分组管理

---

## 🚀 快速开始

### 1. 安装依赖

```bash
cd /home/yuchen/scripts/Audit/control_center
pip install -r requirements.txt
```

**依赖列表：**
- Flask 3.0.0 - Web 框架
- requests 2.31.0 - HTTP 客户端

### 2. 启动中控台

```bash
python app.py
```

**启动信息：**
```
============================================================
中控台系统启动
配置文件: /path/to/config/servers.json
访问地址: http://localhost:8000
============================================================
```

### 3. 添加第一台服务器

#### 方式 1：通过 Web 界面（推荐）

1. 访问：http://localhost:8000
2. 点击 **"➕ 添加服务器"**
3. 填写表单：

| 字段 | 说明 | 示例 | 必填 |
|------|------|------|------|
| 服务器名称 | 自定义名称 | 生产服务器 1 | ✓ |
| IP 地址 | 服务器 IP 或域名 | 192.168.1.10 | ✓ |
| 端口 | 审计系统端口 | 5000 | - |
| Admin 用户名 | Admin 账户 | admin | - |
| Admin 密码 | Admin 密码 | BY116358 | ✓ |
| 描述 | 服务器用途 | 主业务服务器 | - |

4. 点击 **"✓ 添加并连接"**

#### 方式 2：通过 Python 代码

```python
from app import server_manager

success, message, server_id = server_manager.add_server(
    name='生产服务器 1',
    host='192.168.1.10',
    port='5000',
    username='admin',
    password='BY116358',
    description='主业务服务器'
)

if success:
    print(f'✓ {message}，服务器 ID: {server_id}')
else:
    print(f'✗ {message}')
```

### 4. 查看服务器状态

添加成功后，服务器卡片会显示：

```
┌─────────────────────────────────────┐
│ 🟢 生产服务器 1                     │
├─────────────────────────────────────┤
│ 🌐 地址：192.168.1.10:5000          │
│ 👤 用户：admin                      │
│ 📝 描述：主业务服务器               │
│ ⏰ 添加时间：2026-05-28 12:00:00    │
│ 🔄 最后检查：2026-05-28 12:30:00    │
│ 🔑 Token 过期：2026-05-29 12:00:00  │
├─────────────────────────────────────┤
│ [📊 仪表盘] [📋 日志] [🔄 刷新] [🗑️ 删除] │
└─────────────────────────────────────┘
```

---

## 🔐 自动认证原理

### 工作流程图

```
┌─────────────┐
│ 用户填写信息 │
│ - IP 地址   │
│ - 端口      │
│ - Admin 密码│
└──────┬──────┘
       │
       ▼
┌─────────────────────────────┐
│ 系统自动执行                │
│ 1. 构建 API URL             │
│ 2. 发送认证请求             │
│    POST /api/v1/auth/token  │
│    Body: {username, password}│
└──────┬──────────────────────┘
       │
       ▼
┌─────────────────────────────┐
│ 服务器返回                  │
│ {                           │
│   "token": "eyJhbGc...",    │
│   "expires_in": 86400       │
│ }                           │
└──────┬──────────────────────┘
       │
       ▼
┌─────────────────────────────┐
│ 保存配置                    │
│ - Token                     │
│ - 过期时间                  │
│ - 服务器信息                │
│ 写入 config/servers.json    │
└──────┬──────────────────────┘
       │
       ▼
┌─────────────────────────────┐
│ 后续自动维护                │
│ - 每次 API 调用前检查       │
│ - Token 过期前 5 分钟刷新   │
│ - 使用保存的密码重新登录    │
└─────────────────────────────┘
```

### 详细步骤

#### 步骤 1：添加服务器时

```python
# 用户填写信息
name = "生产服务器 1"
host = "192.168.1.10"
port = "5000"
username = "admin"
password = "BY116358"

# 系统构建 URL
base_url = f"http://{host}:{port}"

# 发送认证请求
response = requests.post(
    f"{base_url}/api/v1/auth/token",
    json={'username': username, 'password': password}
)

# 获取 Token
data = response.json()
token = data['token']
expires_in = data['expires_in']  # 86400 秒（24 小时）

# 计算过期时间
expires_at = datetime.now() + timedelta(seconds=expires_in)
# 结果：2026-05-29T12:00:00

# 保存配置
server_config = {
    'id': 'server_1',
    'name': name,
    'host': host,
    'port': port,
    'username': username,
    'password': password,  # 保存密码用于自动刷新
    'token': token,
    'token_expires_at': expires_at.isoformat(),
    'status': 'online'
}
```

#### 步骤 2：使用 API 时

```python
# 每次调用 API 前自动检查
def api_request(server_id, endpoint):
    # 1. 检查 Token 是否即将过期
    server = get_server(server_id)
    expires_at = datetime.fromisoformat(server['token_expires_at'])
    
    # 2. 如果过期前 5 分钟，自动刷新
    if datetime.now() >= expires_at - timedelta(minutes=5):
        refresh_token(server_id)
    
    # 3. 使用 Token 发送请求
    response = requests.get(
        f"{server['base_url']}{endpoint}",
        headers={'Authorization': f"Bearer {server['token']}"}
    )
    
    return response.json()
```

#### 步骤 3：Token 刷新

```python
def refresh_token(server_id):
    server = get_server(server_id)
    
    # 使用保存的密码重新登录
    response = requests.post(
        f"{server['base_url']}/api/v1/auth/token",
        json={
            'username': server['username'],
            'password': server['password']
        }
    )
    
    # 更新 Token
    data = response.json()
    server['token'] = data['token']
    server['token_expires_at'] = (
        datetime.now() + timedelta(seconds=data['expires_in'])
    ).isoformat()
    
    # 保存配置
    save_servers()
```

### 时间线示例

```
12:00:00  添加服务器，获取 Token
          Token 有效期：24 小时
          过期时间：次日 12:00:00

23:55:00  系统检测到 Token 即将过期（剩余 5 分钟）
          自动使用保存的密码重新登录
          获取新 Token，有效期：24 小时
          新过期时间：后天 23:55:00

用户无需任何操作，系统自动完成刷新
```

---

## 📊 功能说明

### 1. 服务器列表

**访问地址：** http://localhost:8000/

**功能：**
- 显示所有已添加的服务器
- 实时状态指示：
  - 🟢 **在线**：服务器正常运行
  - 🔴 **离线**：无法连接到服务器
  - 🟠 **认证失败**：Token 无效或密码错误
- 快速操作按钮：
  - 📊 **仪表盘**：查看服务器统计数据
  - 📋 **日志**：查询服务器日志
  - 🔄 **刷新**：手动刷新 Token
  - 🗑️ **删除**：移除服务器

### 2. 服务器仪表盘

**访问地址：** http://localhost:8000/servers/{server_id}/dashboard

**显示内容：**

```
┌─────────────────────────────────────────────┐
│ 统计卡片                                    │
├─────────────────────────────────────────────┤
│ 今日操作    致命事件    高危事件    在线用户│
│   1,234        5          12         8     │
│                                             │
│ 未读告警    总操作数                        │
│    3        45,678                          │
└─────────────────────────────────────────────┘

┌─────────────────────────────────────────────┐
│ 最近操作（最新 10 条）                      │
├─────────────────────────────────────────────┤
│ 时间       用户   操作      目标    风险    │
│ 12:30:00  admin  LOGIN    admin   L2-LOW   │
│ 12:29:55  user1  FILE_READ /etc   L3-MED   │
│ ...                                         │
└─────────────────────────────────────────────┘
```

### 3. 日志查询

**访问地址：** http://localhost:8000/servers/{server_id}/logs

**筛选条件：**
- **用户名**：按用户筛选
- **最小风险级别**：L1-INFO / L2-LOW / L3-MEDIUM / L4-HIGH / L5-CRITICAL
- **操作分类**：AUTH / FILE / SYSTEM / DATA / CONFIG / ACCESS

**示例查询：**
```
查询条件：
- 用户名：admin
- 最小风险级别：L4-HIGH
- 操作分类：FILE

结果：显示 admin 用户的所有高危及以上文件操作
```

### 4. 告警汇总

**访问地址：** http://localhost:8000/alerts

**功能：**
- 汇总所有服务器的未读告警
- 按时间倒序排列
- 显示告警来源服务器
- 告警级别标识：
  - 🔴 **CRITICAL**：致命告警
  - 🟠 **HIGH**：高危告警
  - 🟡 **MEDIUM**：中危告警

---

## 📁 配置文件

### 配置文件位置

```
control_center/
└── config/
    └── servers.json    # 服务器配置（自动生成）
```

### 配置文件格式

```json
[
  {
    "id": "server_1",
    "name": "生产服务器 1",
    "host": "192.168.1.10",
    "port": "5000",
    "base_url": "http://192.168.1.10:5000",
    "username": "admin",
    "password": "BY116358",
    "description": "主业务服务器",
    "token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJ1c2VyX2lkIjoxLCJ1c2VybmFtZSI6ImFkbWluIiwiZXhwIjoxNzE3MDY4MDAwfQ.xxx",
    "token_expires_at": "2026-05-29T12:00:00",
    "status": "online",
    "added_at": "2026-05-28T12:00:00",
    "last_check": "2026-05-28T12:30:00"
  },
  {
    "id": "server_2",
    "name": "测试服务器",
    "host": "192.168.1.20",
    "port": "5000",
    "base_url": "http://192.168.1.20:5000",
    "username": "admin",
    "password": "TestPass123",
    "description": "测试环境",
    "token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
    "token_expires_at": "2026-05-29T14:00:00",
    "status": "online",
    "added_at": "2026-05-28T14:00:00",
    "last_check": "2026-05-28T14:30:00"
  }
]
```

### 字段说明

| 字段 | 类型 | 说明 |
|------|------|------|
| id | string | 服务器唯一标识 |
| name | string | 服务器名称 |
| host | string | IP 地址或域名 |
| port | string | 端口号 |
| base_url | string | 完整 API URL |
| username | string | Admin 用户名 |
| password | string | Admin 密码（用于自动刷新） |
| description | string | 服务器描述 |
| token | string | JWT Token |
| token_expires_at | string | Token 过期时间（ISO 8601） |
| status | string | 状态：online / offline / auth_failed |
| added_at | string | 添加时间（ISO 8601） |
| last_check | string | 最后检查时间（ISO 8601） |

---

## 🔧 API 调用

### Python 代码示例

#### 1. 添加服务器

```python
from app import server_manager

success, message, server_id = server_manager.add_server(
    name='生产服务器 1',
    host='192.168.1.10',
    port='5000',
    username='admin',
    password='BY116358',
    description='主业务服务器'
)

if success:
    print(f'✓ 服务器添加成功，ID: {server_id}')
else:
    print(f'✗ 添加失败: {message}')
```

#### 2. 获取服务器列表

```python
servers = server_manager.get_all_servers()

for server in servers:
    print(f"服务器: {server['name']}")
    print(f"  状态: {server['status']}")
    print(f"  地址: {server['host']}:{server['port']}")
    print(f"  Token 过期: {server['token_expires_at']}")
```

#### 3. 调用服务器 API

```python
# 获取统计数据
success, stats = server_manager.api_request(
    'server_1',
    '/api/v1/stats'
)

if success:
    print(f"今日操作: {stats['today_logs']}")
    print(f"致命事件: {stats['critical_count']}")
else:
    print(f"请求失败: {stats}")

# 查询日志
success, logs_data = server_manager.api_request(
    'server_1',
    '/api/v1/logs',
    params={
        'risk_min': 4,
        'per_page': 50,
        'user': 'admin'
    }
)

if success:
    for log in logs_data['data']:
        print(f"{log['timestamp']} - {log['action_type']}")
```

#### 4. 刷新 Token

```python
success, message = server_manager.refresh_token('server_1')

if success:
    print('✓ Token 刷新成功')
else:
    print(f'✗ 刷新失败: {message}')
```

#### 5. 删除服务器

```python
server_manager.remove_server('server_1')
print('✓ 服务器已删除')
```

### 批量操作示例

```python
# 批量查询所有服务器的统计数据
def get_all_stats():
    results = []
    
    for server in server_manager.get_all_servers():
        success, stats = server_manager.api_request(
            server['id'],
            '/api/v1/stats'
        )
        
        if success:
            stats['server_name'] = server['name']
            results.append(stats)
    
    return results

# 使用
all_stats = get_all_stats()
for stats in all_stats:
    print(f"{stats['server_name']}: {stats['today_logs']} 条操作")
```

---

## 🔒 安全建议

### 1. 密码存储

**当前实现：**
- 密码以明文形式存储在 `config/servers.json`
- 仅用于自动刷新 Token

**生产环境建议：**

#### 方案 1：使用加密存储

```python
from cryptography.fernet import Fernet

# 生成密钥（仅一次）
key = Fernet.generate_key()
cipher = Fernet(key)

# 加密密码
encrypted_password = cipher.encrypt(password.encode())

# 解密密码
decrypted_password = cipher.decrypt(encrypted_password).decode()
```

#### 方案 2：使用密钥管理服务

```python
# 使用 HashiCorp Vault
import hvac

client = hvac.Client(url='http://vault:8200')
client.secrets.kv.v2.create_or_update_secret(
    path='audit/server1',
    secret={'password': 'BY116358'}
)

# 读取密码
secret = client.secrets.kv.v2.read_secret_version(
    path='audit/server1'
)
password = secret['data']['data']['password']
```

### 2. 文件权限

```bash
# 限制配置文件权限
chmod 600 config/servers.json

# 限制目录权限
chmod 700 config/
```

### 3. HTTPS 支持

**修改 `app.py`：**

```python
# 使用 HTTPS
base_url = f"https://{host}:{port}"

# 如果使用自签名证书
response = requests.post(
    f"{base_url}/api/v1/auth/token",
    json={'username': username, 'password': password},
    verify='/path/to/ca-cert.pem'  # 或 verify=False（不推荐）
)
```

### 4. 网络隔离

```bash
# 使用防火墙限制访问
# 仅允许中控台 IP 访问审计系统 API

# iptables 示例
iptables -A INPUT -p tcp --dport 5000 -s 192.168.1.100 -j ACCEPT
iptables -A INPUT -p tcp --dport 5000 -j DROP
```

### 5. 审计日志

```python
# 记录中控台操作
import logging

logging.basicConfig(
    filename='control_center.log',
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s'
)

# 记录操作
logging.info(f'用户添加服务器: {name} ({host}:{port})')
logging.info(f'Token 刷新: {server_id}')
logging.warning(f'认证失败: {server_id}')
```

---

## 🐛 故障排查

### 问题 1：无法连接到服务器

**症状：**
```
✗ 添加服务器失败: 无法连接到服务器 192.168.1.10:5000
```

**排查步骤：**

1. **检查服务器是否启动**
```bash
# 在服务器上检查进程
ps aux | grep "python.*app.py"

# 检查端口监听
netstat -tlnp | grep 5000
```

2. **测试网络连通性**
```bash
# Ping 测试
ping 192.168.1.10

# 端口测试
telnet 192.168.1.10 5000

# 或使用 curl
curl http://192.168.1.10:5000/api/v1/server/info
```

3. **检查防火墙**
```bash
# 服务器端
sudo ufw status
sudo ufw allow 5000/tcp

# 或 iptables
sudo iptables -L -n | grep 5000
```

### 问题 2：认证失败

**症状：**
```
✗ 添加服务器失败: 认证失败: HTTP 401
```

**排查步骤：**

1. **检查密码是否正确**
```bash
# 手动测试登录
curl -X POST http://192.168.1.10:5000/api/v1/auth/token \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"BY116358"}'
```

2. **检查服务器日志**
```bash
# 查看审计系统日志
tail -f /path/to/audit_system/logs/audit.log
```

3. **检查 Admin 密码配置**
```python
# 在服务器的 app.py 中检查
ADMIN_PASSWORD = os.environ.get('AUDIT_ADMIN_PASSWORD', 'BY116358')
print(f'Admin 密码: {ADMIN_PASSWORD}')
```

### 问题 3：Token 自动刷新失败

**症状：**
```
服务器状态显示：🟠 认证失败
```

**排查步骤：**

1. **检查配置文件**
```bash
cat config/servers.json | jq '.[] | {name, status, token_expires_at}'
```

2. **手动刷新 Token**
```python
from app import server_manager

success, message = server_manager.refresh_token('server_1')
print(f'刷新结果: {success} - {message}')
```

3. **检查密码是否被修改**
```bash
# 如果服务器的 Admin 密码被修改，需要更新配置
# 方式 1：删除后重新添加
# 方式 2：手动编辑 config/servers.json
```

### 问题 4：配置文件损坏

**症状：**
```
✗ 加载服务器配置失败: JSON decode error
```

**解决方案：**

1. **备份配置文件**
```bash
cp config/servers.json config/servers.json.bak
```

2. **验证 JSON 格式**
```bash
python3 -m json.tool config/servers.json
```

3. **修复或重建配置**
```bash
# 如果无法修复，删除并重新添加服务器
rm config/servers.json
# 重启中控台，会自动创建空配置
```

### 问题 5：中控台启动失败

**症状：**
```
ModuleNotFoundError: No module named 'flask'
```

**解决方案：**

```bash
# 重新安装依赖
pip install -r requirements.txt

# 检查 Python 版本
python3 --version  # 需要 Python 3.7+

# 检查依赖是否安装
pip list | grep -E "Flask|requests"
```

---

## 📈 性能优化

### 1. 批量查询优化

```python
import concurrent.futures

def batch_query_stats():
    """并发查询所有服务器的统计数据"""
    servers = server_manager.get_all_servers()
    results = []
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = {
            executor.submit(
                server_manager.api_request,
                server['id'],
                '/api/v1/stats'
            ): server
            for server in servers
        }
        
        for future in concurrent.futures.as_completed(futures):
            server = futures[future]
            try:
                success, stats = future.result()
                if success:
                    stats['server_name'] = server['name']
                    results.append(stats)
            except Exception as e:
                print(f"查询失败: {server['name']} - {e}")
    
    return results
```

### 2. 缓存优化

```python
from functools import lru_cache
from datetime import datetime, timedelta

@lru_cache(maxsize=128)
def get_cached_stats(server_id, cache_key):
    """缓存统计数据（5 分钟）"""
    success, stats = server_manager.api_request(
        server_id,
        '/api/v1/stats'
    )
    return stats if success else {}

# 使用
cache_key = datetime.now().strftime('%Y%m%d%H%M')[:11]  # 精确到 5 分钟
stats = get_cached_stats('server_1', cache_key)
```

---

## 🔄 版本历史

### v1.0 (2026-05-28)

**初始版本：**
- ✅ 自动认证机制
- ✅ Token 自动刷新
- ✅ 多服务器管理
- ✅ 日志查询
- ✅ 告警汇总
- ✅ Web 管理界面

---

## 📞 技术支持

### 相关文档

- [审计系统文档](../audit_system/README.md)
- [API 文档](../audit_system/README.md#-rest-api)
- [系统设计文档](../server_audit_system_design.md)

### 常见问题

**Q: Token 有效期是多久？**
A: 默认 24 小时，系统会在过期前 5 分钟自动刷新。

**Q: 可以同时管理多少台服务器？**
A: 理论上无限制，建议不超过 100 台以保证性能。

**Q: 密码存储安全吗？**
A: 当前版本明文存储，生产环境建议使用加密存储或密钥管理服务。

**Q: 支持 HTTPS 吗？**
A: 支持，修改 `app.py` 中的 `base_url` 即可。

**Q: 可以自定义端口吗？**
A: 可以，修改 `app.py` 最后一行的 `port` 参数。

---

## 📄 许可证

MIT License

---

**版本**：v1.0  
**更新日期**：2026-05-28  
**技术栈**：Python 3 · Flask 3.0 · Requests  
**项目路径**：/home/yuchen/scripts/Audit/control_center/
