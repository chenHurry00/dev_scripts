# 审计系统中控台

> 统一管理多台服务器的审计系统，支持自动认证和 Token 管理

## 🌟 核心特性

### 自动认证机制
- ✅ **一键添加**：填写 IP、端口、Admin 密码即可
- ✅ **自动登录**：系统自动使用 Admin 账户获取 Token
- ✅ **自动刷新**：Token 过期前自动刷新，无需手动操作
- ✅ **持久化存储**：Token 和配置保存在本地，重启后自动恢复

### 功能特性
- ✅ 多服务器统一管理
- ✅ 实时状态监控
- ✅ 跨服务器日志查询
- ✅ 告警汇总展示
- ✅ 统计数据可视化

## 🚀 快速开始

### 1. 安装依赖

```bash
cd control_center
pip install -r requirements.txt
```

### 2. 启动中控台

```bash
python app.py
```

访问：http://localhost:8000

### 3. 添加服务器

1. 点击"➕ 添加服务器"
2. 填写服务器信息：
   - **服务器名称**：自定义名称（如"生产服务器 1"）
   - **IP 地址**：服务器 IP（如 192.168.1.10）
   - **端口**：审计系统端口（默认 5000）
   - **Admin 用户名**：默认 admin
   - **Admin 密码**：服务器的 Admin 密码
3. 点击"✓ 添加并连接"

系统会自动：
- 连接到服务器
- 使用 Admin 账户登录
- 获取并保存 API Token
- 显示连接状态

## 📁 项目结构

```
control_center/
├── app.py                    # 主应用（含自动认证逻辑）
├── requirements.txt          # 依赖列表
├── README.md                # 本文档
│
├── config/                   # 配置目录
│   └── servers.json         # 服务器配置（自动生成）
│
└── templates/
    └── control_center/
        ├── index.html       # 服务器列表
        ├── add_server.html  # 添加服务器
        ├── dashboard.html   # 服务器仪表盘
        ├── logs.html        # 日志查询
        └── alerts.html      # 告警汇总
```

## 🔐 自动认证原理

### 工作流程

1. **添加服务器时**
   ```
   用户填写信息 → 发送认证请求 → 获取 Token → 保存配置
   ```

2. **使用 API 时**
   ```
   检查 Token 是否过期 → 如过期则自动刷新 → 使用 Token 调用 API
   ```

3. **Token 刷新**
   ```
   Token 过期前 5 分钟 → 自动使用保存的密码重新登录 → 更新 Token
   ```

### 配置文件格式

`config/servers.json`：

```json
[
  {
    "id": "server_1",
    "name": "生产服务器 1",
    "host": "192.168.1.10",
    "port": "5000",
    "base_url": "http://192.168.1.10:5000",
    "username": "admin",
    "password": "Admin@2026!Change",
    "token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
    "token_expires_at": "2026-05-29T12:00:00",
    "status": "online",
    "added_at": "2026-05-28T12:00:00",
    "last_check": "2026-05-28T12:30:00"
  }
]
```

## 📊 功能说明

### 服务器列表
- 显示所有已添加的服务器
- 实时状态指示（在线/离线/认证失败）
- 快速操作：仪表盘、日志、刷新、删除

### 服务器仪表盘
- 今日操作统计
- 致命/高危事件数量
- 在线用户数
- 未读告警数
- 最近操作列表

### 日志查询
- 按用户名筛选
- 按风险级别筛选
- 按操作分类筛选
- 分页浏览

### 告警汇总
- 所有服务器的未读告警
- 按时间倒序排列
- 显示来源服务器

## 🔧 API 调用示例

### Python 代码

```python
from control_center.app import server_manager

# 添加服务器
success, message, server_id = server_manager.add_server(
    name='测试服务器',
    host='192.168.1.10',
    port='5000',
    username='admin',
    password='Admin@2026!Change'
)

# 调用 API
success, stats = server_manager.api_request(
    server_id, '/api/v1/stats'
)

# 查询日志
success, logs = server_manager.api_request(
    server_id, '/api/v1/logs',
    params={'risk_min': 4, 'per_page': 50}
)
```

## 🔒 安全建议

### 密码存储
- **当前**：明文存储在 `config/servers.json`
- **生产环境**：建议使用加密存储
  - 使用 `cryptography` 库加密密码
  - 或使用密钥管理服务（如 HashiCorp Vault）

### 文件权限
```bash
# 限制配置文件权限
chmod 600 config/servers.json
```

### HTTPS
生产环境建议使用 HTTPS：
```python
# 修改 app.py 中的 base_url
base_url = f"https://{host}:{port}"
```

## 🐛 故障排查

### 问题：无法连接到服务器
```bash
# 检查服务器是否启动
curl http://192.168.1.10:5000/api/v1/server/info

# 检查防火墙
telnet 192.168.1.10 5000
```

### 问题：认证失败
- 检查 Admin 密码是否正确
- 检查服务器审计系统是否正常运行
- 查看服务器日志

### 问题：Token 过期
- 系统会自动刷新，无需手动操作
- 如果自动刷新失败，点击"🔄 刷新"按钮

## 📝 开发说明

### 添加新功能

1. **添加 API 端点**
   ```python
   @app.route('/servers/<server_id>/custom')
   def custom_feature(server_id):
       success, data = server_manager.api_request(
           server_id, '/api/v1/custom'
       )
       return render_template('custom.html', data=data)
   ```

2. **扩展 ServerManager**
   ```python
   def batch_query(self, endpoint, **kwargs):
       """批量查询所有服务器"""
       results = []
       for server in self.servers:
           success, data = self.api_request(
               server['id'], endpoint, **kwargs
           )
           results.append(data)
       return results
   ```

## 📚 相关文档

- [审计系统文档](../audit_system/README.md)
- [API 文档](../audit_system/README.md#-rest-api)

## 📄 许可证

MIT License

---

**版本**：v1.0  
**更新日期**：2026-05-28  
**技术栈**：Python 3 · Flask 3.0 · Requests
