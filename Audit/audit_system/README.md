# 服务器审计系统 v2.0

> 基于 Flask + SQLite 的服务器操作审计与溯源系统

## 📋 功能特性

### 核心功能
- ✅ **全量审计**：记录所有用户操作（登录、文件、命令、配置等）
- ✅ **五级分级**：自动分类操作风险（L1-L5）
- ✅ **不可篡改**：日志写入后只读，防止证据销毁
- ✅ **溯源追责**：多维度检索，还原事件时间线
- ✅ **实时告警**：高危操作自动触发告警

### 新增功能 ⭐
- ✅ **Admin 密码管理**：在 app.py 顶部统一配置，每次启动自动刷新
- ✅ **REST API**：支持中控台远程访问（JWT 认证）
- ✅ **中控台支持**：可部署统一管理界面监控多台服务器

## 🚀 快速开始

### 1. 安装依赖

```bash
cd audit_system
pip install -r requirements.txt
```

### 2. 启动应用

```bash
# 开发环境（使用默认密码）
python app.py

# 生产环境（设置自定义密码）
export AUDIT_ADMIN_USER=admin
export AUDIT_ADMIN_PASSWORD=YourSecurePassword123!
export API_SECRET_KEY=your-api-secret-key
python app.py
```

### 3. 访问系统

- **管理界面**：http://localhost:5000
- **默认账户**：admin / Admin@2026!Change

## 📁 项目结构

```
audit_system/
├── app.py                          # Flask 主应用
├── config.py                       # 配置文件
├── start.sh                        # 启动脚本
├── requirements.txt                # 依赖列表
│
├── audit_command_buffer.py         # 命令审计（高性能缓冲版）
├── audit_bash_hook.sh              # Bash 钩子
├── sync_buffer.py                  # 后台同步服务
├── install_command_audit.sh        # 安装脚本
│
├── models/                         # 数据库模型
├── audit/                          # 审计模块（logger, classifier, decorator）
├── api/                            # REST API（auth, routes）
├── admin/                          # 管理模块
├── static/                         # 静态文件（CSS, JS）
├── templates/                      # HTML 模板（login, dashboard, logs）
│
├── tests/                          # 测试文件
├── tools/                          # 工具脚本
├── docs/                           # 文档
│
├── data/                           # 数据库
└── logs/                           # 日志（audit, access, error, alert）
```

## 🔐 Admin 密码管理

### 配置方式

在 `app.py` 顶部通过环境变量配置：

```python
# 优先级：环境变量 > 默认值
ADMIN_USERNAME = os.environ.get('AUDIT_ADMIN_USER', 'admin')
ADMIN_PASSWORD = os.environ.get('AUDIT_ADMIN_PASSWORD', 'Admin@2026!Change')
```

### 自动刷新机制

每次应用启动时，自动执行 `init_or_refresh_admin()` 函数：
- 如果 admin 用户不存在 → 创建
- 如果已存在 → 更新密码为当前配置的密码

### 部署示例

#### 开发环境
```bash
python app.py
```

#### 生产环境（systemd）
```ini
# /etc/systemd/system/audit-system.service
[Service]
Environment="AUDIT_ADMIN_USER=admin"
Environment="AUDIT_ADMIN_PASSWORD=ProductionPassword2026!"
Environment="API_SECRET_KEY=your-api-secret-key"
ExecStart=/usr/bin/python3 /opt/audit-system/app.py
```

#### Docker
```bash
docker run -d \
  -e AUDIT_ADMIN_USER=admin \
  -e AUDIT_ADMIN_PASSWORD=SecurePass123! \
  -e API_SECRET_KEY=your-api-secret \
  -p 5000:5000 \
  -v ./data:/app/data \
  audit-system:latest
```

## 🌐 REST API

### 认证

获取 Token：

```bash
curl -X POST http://localhost:5000/api/v1/auth/token \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"Admin@2026!Change"}'
```

响应：
```json
{
  "token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "expires_in": 86400
}
```

### API 端点

#### 1. 查询日志
```bash
curl -X GET "http://localhost:5000/api/v1/logs?risk_min=4&page=1" \
  -H "Authorization: Bearer YOUR_TOKEN"
```

#### 2. 获取统计
```bash
curl -X GET http://localhost:5000/api/v1/stats \
  -H "Authorization: Bearer YOUR_TOKEN"
```

响应：
```json
{
  "total_logs": 1234,
  "today_logs": 56,
  "critical_count": 2,
  "high_count": 8,
  "online_users": 3,
  "unread_alerts": 5
}
```

#### 3. 获取告警
```bash
curl -X GET "http://localhost:5000/api/v1/alerts?unread_only=true" \
  -H "Authorization: Bearer YOUR_TOKEN"
```

#### 4. 服务器信息
```bash
curl -X GET http://localhost:5000/api/v1/server/info \
  -H "Authorization: Bearer YOUR_TOKEN"
```

## 📊 操作分级体系

| 级别 | 名称 | 颜色 | 描述 | 示例 |
|------|------|------|------|------|
| **L5** | CRITICAL | 🔴 | 致命操作 | 删除数据库、格式化磁盘 |
| **L4** | HIGH | 🟠 | 高危操作 | sudo 提升、删除 /etc/ 文件 |
| **L3** | MEDIUM | 🟡 | 中危操作 | 修改配置、重启服务 |
| **L2** | LOW | 🔵 | 低危操作 | 文件读写、普通命令 |
| **L1** | INFO | ⚪ | 信息记录 | 登录、页面访问 |

### 上下文加权规则

- 敏感路径（/etc/, /root/）：+1 级
- 深夜操作（00:00-06:00）：+1 级
- 批量操作：+1 级

## 🧪 功能测试

运行测试脚本验证所有功能：

```bash
python test_system.py
```

测试内容：
1. ✅ 数据库初始化
2. ✅ 审计日志记录
3. ✅ 风险分级
4. ✅ API Token 生成
5. ✅ 登录功能

## 🔒 安全建议

### 密码管理
- ✅ 强密码策略（至少 12 位，包含大小写字母、数字、特殊字符）
- ✅ 定期轮换（建议 90 天）
- ✅ 使用密钥管理服务（如 HashiCorp Vault）
- ✅ 启用双因素认证（TOTP）

### 生产部署
- ✅ 使用 HTTPS（配置 SSL 证书）
- ✅ 使用生产级 WSGI 服务器（gunicorn/uwsgi）
- ✅ 配置防火墙规则
- ✅ 定期备份数据库和日志
- ✅ 监控磁盘空间

### API 安全
- ✅ 定期轮换 API Secret Key
- ✅ 限制 Token 有效期
- ✅ IP 白名单
- ✅ 速率限制

## 📦 生产部署

### 使用 Gunicorn

```bash
# 安装 gunicorn
pip install gunicorn

# 启动应用
gunicorn -w 4 -b 0.0.0.0:5000 app:app
```

### 使用 Nginx 反向代理

```nginx
server {
    listen 443 ssl;
    server_name audit.example.com;

    ssl_certificate /etc/ssl/certs/audit.crt;
    ssl_certificate_key /etc/ssl/private/audit.key;

    location / {
        proxy_pass http://127.0.0.1:5000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }
}
```

## 🎛️ 中控台部署

详见设计文档第 13 章《中控台系统设计》。

中控台可以：
- 统一监控多台服务器
- 实时告警汇总
- 跨服务器日志查询
- 集中管理界面

## 📝 日志格式

### 数据库记录
```sql
SELECT * FROM audit_logs LIMIT 1;
```

### 文件日志（JSON Lines）
```json
{
  "timestamp": "2026-05-28T12:30:00",
  "username": "admin",
  "ip_address": "192.168.1.100",
  "action_category": "FILE",
  "action_type": "FILE_DELETE",
  "target_resource": "/etc/passwd",
  "risk_level": 5,
  "risk_label": "CRITICAL",
  "result": "success",
  "checksum": "sha256:a3f9..."
}
```

## 🐛 故障排查

### 问题：无法启动应用
```bash
# 检查依赖
pip install -r requirements.txt

# 检查端口占用
lsof -i :5000
```

### 问题：无法登录
```bash
# 检查 admin 用户
sqlite3 data/audit.db "SELECT * FROM users WHERE username='admin';"

# 重置密码（重启应用）
export AUDIT_ADMIN_PASSWORD=NewPassword123!
python app.py
```

### 问题：API 认证失败
```bash
# 检查 Token 是否过期
# 重新生成 Token

# 检查 API_SECRET_KEY 是否一致
echo $API_SECRET_KEY
```

## 📚 相关文档

- [设计文档](server_audit_system_design.md) - 完整的系统设计方案
- [API 文档](server_audit_system_design.md#8-api-接口设计) - REST API 详细说明
- [中控台文档](server_audit_system_design.md#13-中控台系统设计) - 中控台部署指南

## 📄 许可证

MIT License

## 👥 贡献

欢迎提交 Issue 和 Pull Request！

## 📧 联系方式

如有问题，请联系系统管理员。

---

**版本**：v2.0  
**更新日期**：2026-05-28  
**技术栈**：Python 3 · Flask 3.0 · SQLite · JWT
