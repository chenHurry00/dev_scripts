# 服务器历史登录用户操作审计与溯源系统设计方案

> **技术栈**：Python · Flask · SQLite · Werkzeug  
> **目标**：对服务器历史登录用户的所有操作进行记录、分级归类，支持管理员溯源查询与审计管理。

---

## 目录

1. [系统概述](#1-系统概述)
2. [操作分级体系](#2-操作分级体系)
3. [系统架构设计](#3-系统架构设计)
4. [数据库设计](#4-数据库设计)
5. [日志采集方案](#5-日志采集方案)
6. [后端模块设计](#6-后端模块设计)
7. [管理界面设计](#7-管理界面设计)
8. [API 接口设计](#8-api-接口设计)
9. [安全策略](#9-安全策略)
10. [部署与运维](#10-部署与运维)
11. [关键代码结构](#11-关键代码结构)
12. [Admin 密码管理方案](#12-admin-密码管理方案) ⭐ 新增
13. [中控台系统设计](#13-中控台系统设计) ⭐ 新增

---

## 1. 系统概述

### 1.1 背景与目标

服务器安全审计系统旨在对所有历史登录用户的操作行为进行完整记录，通过操作分级归类机制，支持事后溯源、损失定责和安全分析。核心目标：

- **全量采集**：记录用户登录、文件操作、命令执行、配置变更等全部行为
- **分级归类**：按风险等级将操作分为 5 个级别，快速定位高危操作
- **不可篡改**：日志写入后只读，防止攻击者销毁证据
- **溯源追责**：支持按用户、时间、操作类型多维检索，还原事件时间线
- **管理可视**：Admin 管理界面实时展示告警、统计与审计报告
- **密码管理**：⭐ 在 app.py 顶部统一配置 Admin 密码，每次启动自动刷新
- **中控台监控**：⭐ 本地部署统一管理界面，同时监控多台服务器

### 1.2 系统边界

```
┌─────────────────────────────────────────────────────┐
│                   被监控服务器                        │
│                                                     │
│  ┌──────────┐   ┌──────────┐   ┌───────────────┐   │
│  │ SSH/登录  │   │ 系统调用  │   │  Web应用操作   │   │
│  │  事件    │   │  审计    │   │   (Flask)     │   │
│  └────┬─────┘   └────┬─────┘   └──────┬────────┘   │
│       │              │                │             │
│       └──────────────┴────────────────┘             │
│                       │                             │
│              ┌─────────▼─────────┐                  │
│              │   审计采集中间层    │                  │
│              │  (Audit Hooks)    │                  │
│              └─────────┬─────────┘                  │
│                        │                            │
│              ┌─────────▼─────────┐                  │
│              │   SQLite 审计库    │                  │
│              │  + 日志文件(不可删) │                  │
│              └─────────┬─────────┘                  │
│                        │                            │
│              ┌─────────▼─────────┐                  │
│              │  Flask 管理后台    │                  │
│              │  (Admin Only)     │                  │
│              └───────────────────┘                  │
└─────────────────────────────────────────────────────┘
```

---

## 2. 操作分级体系

### 2.1 五级风险分级定义

| 级别 | 名称 | 颜色标识 | 描述 | 示例操作 |
|------|------|----------|------|----------|
| **L5** | 致命 (CRITICAL) | 🔴 红色 | 可直接导致系统损毁或数据丢失 | `rm -rf /`、删除数据库、格式化磁盘、关闭防火墙 |
| **L4** | 高危 (HIGH) | 🟠 橙色 | 涉及权限提升或核心配置变更 | `sudo`/`su`切换、修改 `/etc/passwd`、安装/卸载软件、停止关键服务 |
| **L3** | 中危 (MEDIUM) | 🟡 黄色 | 影响系统正常运行的操作 | 修改配置文件、创建/删除用户、网络配置变更、服务重启 |
| **L2** | 低危 (LOW) | 🔵 蓝色 | 常规操作但需留存记录 | 文件读写、目录浏览、普通命令执行、应用内操作 |
| **L1** | 信息 (INFO) | ⚪ 灰色 | 正常行为基线记录 | 登录/登出、页面访问、查询操作、文件下载 |

### 2.2 操作类型分类体系

```
操作大类
├── AUTH         认证类
│   ├── LOGIN_SUCCESS     登录成功
│   ├── LOGIN_FAIL        登录失败
│   ├── LOGOUT            登出
│   ├── PASSWORD_CHANGE   密码修改
│   └── PRIVILEGE_CHANGE  权限变更
│
├── FILE         文件操作类
│   ├── FILE_READ         文件读取
│   ├── FILE_WRITE        文件写入
│   ├── FILE_DELETE       文件删除 ⚠️
│   ├── FILE_UPLOAD       文件上传
│   ├── FILE_DOWNLOAD     文件下载
│   └── FILE_PERMISSION   权限修改
│
├── SYSTEM       系统操作类
│   ├── CMD_EXEC          命令执行
│   ├── PROCESS_KILL      进程终止
│   ├── SERVICE_CHANGE    服务变更
│   ├── CRON_CHANGE       定时任务变更
│   └── NETWORK_CHANGE    网络配置变更
│
├── DATA         数据操作类
│   ├── DB_READ           数据库读取
│   ├── DB_WRITE          数据库写入
│   ├── DB_DELETE         数据库删除 ⚠️
│   ├── DB_SCHEMA_CHANGE  结构变更
│   └── DATA_EXPORT       数据导出
│
├── CONFIG       配置变更类
│   ├── APP_CONFIG        应用配置
│   ├── SYS_CONFIG        系统配置
│   └── SECURITY_CONFIG   安全配置 ⚠️
│
└── ACCESS       访问操作类
    ├── PAGE_VIEW         页面访问
    ├── API_CALL          API调用
    ├── RESOURCE_ACCESS   资源访问
    └── FORBIDDEN_ACCESS  越权访问 ⚠️
```

### 2.3 自动风险评分规则

操作的最终风险级别 = `base_level` + `context_bonus`，规则如下：

```
上下文加权规则：
  + 操作对象为 /etc/、/root/、数据库核心表  → +1 级
  + 深夜时段（00:00~06:00）执行              → +1 级
  + 同一用户 1 分钟内连续相同操作 > 10 次   → +1 级（批量操作告警）
  + 新账户（注册 < 24h）执行 L3+ 操作       → +1 级
  + IP 为首次登录 IP                         → +0.5 级（标记异常）
```

---

## 3. 系统架构设计

### 3.1 整体架构

```
┌─────────────────────────────────────────────────────────────┐
│                        Flask Application                     │
│                                                             │
│  ┌─────────────┐   ┌──────────────┐   ┌─────────────────┐  │
│  │  认证模块    │   │  路由/视图层  │   │   管理界面层    │  │
│  │  Auth Module│   │  Routes/Views│   │  Admin Dashboard│  │
│  └──────┬──────┘   └──────┬───────┘   └────────┬────────┘  │
│         │                 │                     │           │
│         └─────────────────┴─────────────────────┘           │
│                           │                                 │
│              ┌────────────▼────────────┐                    │
│              │      审计中间件层        │                    │
│              │   AuditMiddleware       │                    │
│              │   @audit_log decorator  │                    │
│              └────────────┬────────────┘                    │
│                           │                                 │
│         ┌─────────────────┼──────────────────┐              │
│         ▼                 ▼                  ▼              │
│  ┌─────────────┐  ┌──────────────┐  ┌──────────────┐       │
│  │  SQLite DB  │  │  File Logger │  │  Alert Engine│       │
│  │  audit_log  │  │  .log files  │  │  实时告警推送  │       │
│  └─────────────┘  └──────────────┘  └──────────────┘       │
└─────────────────────────────────────────────────────────────┘
```

### 3.2 数据流向

```
用户请求
    │
    ▼
Flask Before_Request Hook
    │ 记录请求开始时间、IP、User-Agent
    ▼
路由处理函数（带 @audit_log 装饰器）
    │ 提取操作类型、操作对象、操作参数
    ▼
操作执行
    │
    ▼
Flask After_Request Hook
    │ 记录响应状态码、耗时
    │ 计算风险级别
    ▼
┌───────────────────────────────┐
│       AuditLogger.write()     │
│  ┌────────────┐ ┌───────────┐ │
│  │ SQLite写入  │ │ 文件追加   │ │
│  │ (结构化查询) │ │ (不可删除) │ │
│  └────────────┘ └───────────┘ │
│  ┌────────────────────────┐   │
│  │ L4/L5 → 触发实时告警   │   │
│  └────────────────────────┘   │
└───────────────────────────────┘
```

---

## 4. 数据库设计

### 4.1 数据库表结构

#### 4.1.1 用户表 `users`

```sql
CREATE TABLE users (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    username     TEXT    NOT NULL UNIQUE,
    password_hash TEXT   NOT NULL,
    role         TEXT    NOT NULL DEFAULT 'user',  -- 'admin' | 'user'
    email        TEXT,
    created_at   TEXT    NOT NULL DEFAULT (datetime('now')),
    last_login   TEXT,
    login_ip     TEXT,
    is_active    INTEGER NOT NULL DEFAULT 1,
    failed_login_count INTEGER DEFAULT 0,
    locked_until TEXT    -- 暴力破解锁定时间
);
```

#### 4.1.2 审计日志主表 `audit_logs`

```sql
CREATE TABLE audit_logs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    -- 身份信息
    user_id         INTEGER,
    username        TEXT    NOT NULL,
    session_id      TEXT,
    -- 网络信息
    ip_address      TEXT    NOT NULL,
    user_agent      TEXT,
    -- 操作信息
    action_category TEXT    NOT NULL,  -- AUTH/FILE/SYSTEM/DATA/CONFIG/ACCESS
    action_type     TEXT    NOT NULL,  -- 具体操作类型
    action_detail   TEXT,             -- JSON格式的详细参数
    target_resource TEXT,             -- 操作对象（文件路径/用户名/表名等）
    -- 风险信息
    risk_level      INTEGER NOT NULL, -- 1~5
    risk_label      TEXT    NOT NULL, -- INFO/LOW/MEDIUM/HIGH/CRITICAL
    -- 结果信息
    status_code     INTEGER,          -- HTTP状态码
    result          TEXT,             -- 'success' | 'failure' | 'error'
    error_message   TEXT,
    -- 时间信息
    timestamp       TEXT    NOT NULL DEFAULT (datetime('now')),
    duration_ms     INTEGER,
    -- 完整性校验
    checksum        TEXT,             -- SHA256(关键字段)，防篡改
    FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE INDEX idx_audit_timestamp   ON audit_logs(timestamp);
CREATE INDEX idx_audit_user_id     ON audit_logs(user_id);
CREATE INDEX idx_audit_risk_level  ON audit_logs(risk_level);
CREATE INDEX idx_audit_ip          ON audit_logs(ip_address);
CREATE INDEX idx_audit_category    ON audit_logs(action_category);
```

#### 4.1.3 登录会话表 `login_sessions`

```sql
CREATE TABLE login_sessions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id   TEXT    NOT NULL UNIQUE,
    user_id      INTEGER NOT NULL,
    username     TEXT    NOT NULL,
    ip_address   TEXT    NOT NULL,
    user_agent   TEXT,
    login_time   TEXT    NOT NULL DEFAULT (datetime('now')),
    logout_time  TEXT,
    last_active  TEXT,
    is_active    INTEGER NOT NULL DEFAULT 1,
    logout_reason TEXT,  -- 'manual'/'timeout'/'forced'/'error'
    FOREIGN KEY (user_id) REFERENCES users(id)
);
```

#### 4.1.4 告警记录表 `alerts`

```sql
CREATE TABLE alerts (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    audit_log_id INTEGER NOT NULL,
    alert_type   TEXT    NOT NULL,  -- 'single_event'/'pattern'/'threshold'
    severity     TEXT    NOT NULL,  -- HIGH / CRITICAL
    title        TEXT    NOT NULL,
    description  TEXT,
    is_read      INTEGER NOT NULL DEFAULT 0,
    is_handled   INTEGER NOT NULL DEFAULT 0,
    handler_id   INTEGER,           -- 处理人（admin user_id）
    handled_at   TEXT,
    created_at   TEXT    NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (audit_log_id) REFERENCES audit_logs(id),
    FOREIGN KEY (handler_id)   REFERENCES users(id)
);
```

#### 4.1.5 溯源关联表 `incident_traces`

```sql
-- 将多条日志关联为一个"事件"，用于溯源分析
CREATE TABLE incident_traces (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    incident_id  TEXT    NOT NULL,  -- UUID，同一事件共用
    audit_log_id INTEGER NOT NULL,
    sequence_no  INTEGER NOT NULL,  -- 事件内排序
    note         TEXT,
    created_by   INTEGER,           -- admin
    created_at   TEXT    NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (audit_log_id) REFERENCES audit_logs(id)
);
```

---

## 5. 日志采集方案

### 5.1 采集层次

#### 层次一：Flask 应用层（Web 操作）

通过 `before_request` / `after_request` Hook 和装饰器采集所有 HTTP 请求：

```
采集内容：URL、Method、参数、用户身份、响应码、耗时
覆盖范围：所有 Flask 路由
```

#### 层次二：系统命令层（Shell 操作）

集成 Linux `auditd` 或使用 Python subprocess 监控：

```
采集工具：auditd（/etc/audit/rules.d/）
关键规则：
  -w /etc/passwd -p wa -k user_modify
  -w /etc/shadow -p wa -k shadow_modify
  -w /var/log    -p wa -k log_modify
  -a always,exit -F arch=b64 -S execve -k cmd_exec
```

#### 层次三：文件系统层

使用 Python `watchdog` 库监控关键目录：

```
监控目录：/etc/  /var/www/  /home/  /root/  应用目录
监控事件：created / modified / deleted / moved
```

#### 层次四：数据库操作层

在 SQLite 操作封装函数中注入审计钩子：

```python
def audit_db_execute(sql, params, user_context):
    # 记录执行前状态
    audit_logger.log(category='DATA', action=classify_sql(sql), ...)
    result = db.execute(sql, params)
    return result
```

### 5.2 日志文件分层存储

```
logs/
├── audit/
│   ├── audit_2025-01.log        # 按月切割，只追加
│   ├── audit_2025-02.log
│   └── ...
├── access/
│   └── access.log               # HTTP访问日志（RotatingFileHandler）
├── error/
│   └── error.log                # 应用错误日志
└── alert/
    └── alert.log                # 高危告警专用日志
```

**日志格式（JSON Lines）**：

```json
{
  "id": 10234,
  "ts": "2025-03-15T14:32:01.123Z",
  "user": "zhang_san",
  "ip": "192.168.1.101",
  "session": "sess_abc123",
  "category": "FILE",
  "action": "FILE_DELETE",
  "target": "/var/www/html/config.php",
  "risk": 4,
  "risk_label": "HIGH",
  "result": "success",
  "detail": {"size": 4096, "mode": "0644"},
  "checksum": "sha256:a3f9..."
}
```

### 5.3 日志防篡改机制

每条日志生成 `checksum`：

```python
def compute_checksum(record: dict) -> str:
    payload = f"{record['id']}|{record['ts']}|{record['user']}|{record['action']}|{record['result']}"
    return "sha256:" + hashlib.sha256(payload.encode()).hexdigest()
```

定期校验脚本（cron 每小时执行）：

```python
# 对比 DB 中 checksum 字段与重新计算值
# 发现不一致则立即生成 CRITICAL 告警
```

---

## 6. 后端模块设计

### 6.1 模块划分

```
audit_system/
├── app.py                    # Flask 应用入口
├── config.py                 # 配置管理
├── extensions.py             # Flask扩展初始化
│
├── models/
│   ├── user.py               # 用户模型
│   ├── audit_log.py          # 审计日志模型
│   ├── session.py            # 会话模型
│   └── alert.py              # 告警模型
│
├── audit/
│   ├── __init__.py
│   ├── logger.py             # 核心日志记录器
│   ├── classifier.py         # 操作分类与风险评分
│   ├── decorator.py          # @audit_log 装饰器
│   ├── middleware.py         # Flask请求钩子
│   └── integrity.py          # 日志完整性校验
│
├── admin/
│   ├── __init__.py
│   ├── routes.py             # 管理界面路由
│   ├── auth.py               # Admin认证
│   └── report.py             # 报告生成
│
├── api/
│   ├── __init__.py
│   └── routes.py             # REST API
│
└── templates/
    ├── admin/
    │   ├── base.html
    │   ├── dashboard.html
    │   ├── logs.html
    │   ├── users.html
    │   ├── alerts.html
    │   └── trace.html
    └── auth/
        └── login.html
```

### 6.2 核心组件设计

#### 6.2.1 AuditLogger（核心日志记录器）

```python
class AuditLogger:
    """
    线程安全的审计日志记录器，同时写入 SQLite 和文件。
    
    使用方式：
        audit_logger.log(
            user_id=1,
            username='zhang_san',
            category='FILE',
            action_type='FILE_DELETE',
            target='/etc/passwd',
            result='success',
            request=request  # Flask request 对象
        )
    """
    
    def __init__(self, db_path, log_dir):
        self.db_path = db_path
        self.log_dir = Path(log_dir)
        self.classifier = OperationClassifier()
        self._setup_file_logger()
    
    def log(self, **kwargs) -> int:
        """记录一条审计日志，返回日志ID"""
        record = self._build_record(**kwargs)
        record['risk_level'], record['risk_label'] = \
            self.classifier.classify(record)
        record['checksum'] = self._compute_checksum(record)
        
        log_id = self._write_to_db(record)
        self._write_to_file(record)
        
        if record['risk_level'] >= 4:
            self._trigger_alert(record, log_id)
        
        return log_id
```

#### 6.2.2 OperationClassifier（操作分类器）

```python
class OperationClassifier:
    """
    根据操作类型、目标资源、上下文环境计算风险级别。
    
    分类规则采用规则引擎模式，支持动态配置。
    """
    
    BASE_RISK_MAP = {
        # 认证类
        'LOGIN_SUCCESS':    (1, 'AUTH'),
        'LOGIN_FAIL':       (2, 'AUTH'),
        'PRIVILEGE_CHANGE': (4, 'AUTH'),
        # 文件类
        'FILE_READ':        (1, 'FILE'),
        'FILE_WRITE':       (2, 'FILE'),
        'FILE_DELETE':      (4, 'FILE'),
        # 系统类
        'CMD_EXEC':         (3, 'SYSTEM'),
        'SERVICE_CHANGE':   (4, 'SYSTEM'),
        # 数据类
        'DB_DELETE':        (5, 'DATA'),
        'DB_SCHEMA_CHANGE': (4, 'DATA'),
        # ...
    }
    
    SENSITIVE_PATHS = ['/etc/', '/root/', '/var/log/', '/boot/']
    
    def classify(self, record: dict) -> tuple[int, str]:
        base_level = self.BASE_RISK_MAP.get(record['action_type'], (2, 'ACCESS'))[0]
        bonus = self._context_bonus(record)
        final_level = min(5, base_level + bonus)
        return final_level, self._level_to_label(final_level)
    
    def _context_bonus(self, record) -> int:
        bonus = 0
        target = record.get('target_resource', '')
        
        # 敏感路径加权
        if any(target.startswith(p) for p in self.SENSITIVE_PATHS):
            bonus += 1
        
        # 深夜操作加权
        hour = datetime.fromisoformat(record['timestamp']).hour
        if 0 <= hour < 6:
            bonus += 1
        
        return bonus
```

#### 6.2.3 @audit_log 装饰器

```python
def audit_log(category: str, action_type: str, 
              target_extractor=None, level_override=None):
    """
    路由函数审计装饰器。
    
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
            start_time = time.time()
            target = target_extractor() if target_extractor else request.path
            
            try:
                result = f(*args, **kwargs)
                status = 'success'
                error_msg = None
            except Exception as e:
                result = None
                status = 'error'
                error_msg = str(e)
                raise
            finally:
                duration_ms = int((time.time() - start_time) * 1000)
                audit_logger.log(
                    user_id=session.get('user_id'),
                    username=session.get('username', 'anonymous'),
                    category=category,
                    action_type=action_type,
                    target_resource=target,
                    result=status,
                    error_message=error_msg,
                    duration_ms=duration_ms,
                    request=request
                )
            return result
        return wrapper
    return decorator
```

---

## 7. 管理界面设计

### 7.1 页面功能清单

| 页面 | 路由 | 功能 |
|------|------|------|
| 登录页 | `/admin/login` | Admin 专用登录，双重认证 |
| 仪表盘 | `/admin/dashboard` | 实时统计、告警概览、趋势图 |
| 日志列表 | `/admin/logs` | 全量日志，多维筛选、导出 |
| 用户管理 | `/admin/users` | 用户列表、会话管理、强制登出 |
| 告警中心 | `/admin/alerts` | 告警列表、处理记录 |
| 溯源分析 | `/admin/trace` | 事件关联分析、时间线还原 |
| 完整性校验 | `/admin/integrity` | 日志完整性检测结果 |
| 报告导出 | `/admin/report` | 生成 PDF/CSV 审计报告 |

### 7.2 仪表盘核心指标

```
┌──────────────────────────────────────────────────────────────┐
│  ⚠️ 今日告警 12    🔴 L5 事件 2    👥 在线用户 5    📊 总操作 1,234  │
├──────────────────────────────────────────────────────────────┤
│  操作分级分布图（24h）       │    实时操作流（最新10条）          │
│  ████ L1: 45%              │  14:32 zhang_san DELETE /etc/  │
│  ███  L2: 30%              │  14:31 li_si    LOGIN  192.168  │
│  ██   L3: 15%              │  14:30 wang_wu  EXEC   rm -rf  │
│  █    L4: 8%               │  ...                           │
│  ▌    L5: 2%               │                                │
├──────────────────────────────────────────────────────────────┤
│  高风险用户 TOP5             │    高危操作分类 TOP5              │
│  1. zhang_san  L4×3 L5×1  │  1. FILE_DELETE    ×23         │
│  2. unknown_ip L4×2        │  2. CMD_EXEC(root) ×15         │
│  ...                       │  ...                           │
└──────────────────────────────────────────────────────────────┘
```

### 7.3 日志筛选功能

支持以下维度的组合筛选：

- **时间范围**：开始时间 ~ 结束时间（精确到秒）
- **用户**：用户名 / IP 地址 / 会话 ID
- **操作分类**：大类（AUTH/FILE/SYSTEM...）+ 具体类型
- **风险级别**：L1 ~ L5 多选
- **操作结果**：success / failure / error
- **关键词**：操作目标资源模糊搜索

### 7.4 溯源分析功能

**时间线视图**：以事件时间线形式展示某用户/IP 的操作序列：

```
2025-03-15 攻击事件溯源时间线
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 14:20  ⚪ LOGIN     192.168.1.105  用户 zhang_san 登录
 14:22  🔵 FILE_READ  /etc/passwd    读取用户列表
 14:25  🔵 FILE_READ  /var/www/...   读取配置文件
 14:28  🟡 CMD_EXEC   cat /etc/cron  查看定时任务
 14:31  🟠 FILE_WRITE /etc/crontab   修改定时任务 ⚠️告警
 14:35  🔴 DB_DELETE  users 表       删除用户数据 ⚠️告警
 14:36  ⚪ LOGOUT     -              登出
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 损失评估：users 表被清空（约 5,000 条记录）
 关联证据：日志 ID #10231 ~ #10238，会话 sess_xyz789
```

---

## 8. API 接口设计

### 8.1 认证接口

| Method | 路径 | 描述 |
|--------|------|------|
| POST | `/admin/login` | Admin 登录 |
| POST | `/admin/logout` | Admin 登出 |

### 8.2 日志查询接口

| Method | 路径 | 描述 |
|--------|------|------|
| GET | `/api/logs` | 查询审计日志列表 |
| GET | `/api/logs/<id>` | 查询单条日志详情 |
| GET | `/api/logs/export` | 导出日志（CSV/JSON） |
| GET | `/api/logs/stats` | 日志统计数据 |

**查询参数示例**：

```
GET /api/logs?
  start=2025-03-01T00:00:00&
  end=2025-03-15T23:59:59&
  user=zhang_san&
  risk_min=4&
  category=FILE&
  page=1&
  per_page=50
```

**响应格式**：

```json
{
  "total": 234,
  "page": 1,
  "per_page": 50,
  "data": [
    {
      "id": 10238,
      "timestamp": "2025-03-15T14:35:22",
      "username": "zhang_san",
      "ip_address": "192.168.1.105",
      "action_category": "DATA",
      "action_type": "DB_DELETE",
      "target_resource": "users",
      "risk_level": 5,
      "risk_label": "CRITICAL",
      "result": "success",
      "duration_ms": 245
    }
  ]
}
```

### 8.3 告警接口

| Method | 路径 | 描述 |
|--------|------|------|
| GET | `/api/alerts` | 查询告警列表 |
| PUT | `/api/alerts/<id>/handle` | 标记告警已处理 |
| GET | `/api/alerts/unread/count` | 未读告警数量（轮询用） |

### 8.4 用户管理接口

| Method | 路径 | 描述 |
|--------|------|------|
| GET | `/api/users` | 用户列表 |
| GET | `/api/users/<id>/sessions` | 用户会话记录 |
| POST | `/api/users/<id>/force-logout` | 强制踢出用户 |
| GET | `/api/users/<id>/timeline` | 用户操作时间线 |

---

## 9. 安全策略

### 9.1 Admin 账户保护

```python
# 双重认证配置
ADMIN_CONFIG = {
    'session_timeout': 1800,      # 30分钟无操作自动登出
    'max_login_attempts': 5,       # 最大失败次数
    'lockout_duration': 900,       # 锁定 15 分钟
    'require_2fa': True,           # 建议开启 TOTP
    'allowed_ips': ['127.0.0.1'],  # IP 白名单（可选）
}

# Admin 操作本身也被审计（审计管理员行为）
@before_request
def audit_admin_access():
    if request.path.startswith('/admin/'):
        audit_logger.log(category='ACCESS', action_type='ADMIN_ACCESS', ...)
```

### 9.2 日志保护

```python
# 日志文件设置为只追加（防止删除或覆盖）
import fcntl, os

def set_append_only(filepath):
    """设置文件只追加属性（需 root 权限）"""
    os.system(f'chattr +a {filepath}')

# SQLite 审计表禁止 DELETE/UPDATE
# 通过触发器实现：
"""
CREATE TRIGGER prevent_audit_delete
BEFORE DELETE ON audit_logs
BEGIN
    SELECT RAISE(ABORT, 'Audit logs are immutable');
END;

CREATE TRIGGER prevent_audit_update
BEFORE UPDATE ON audit_logs
BEGIN
    SELECT RAISE(ABORT, 'Audit logs are immutable');
END;
"""
```

### 9.3 传输安全

```python
# HTTPS 配置（生产环境必须）
# Nginx 反代配置片段：
"""
server {
    listen 443 ssl;
    ssl_certificate     /etc/ssl/certs/audit.crt;
    ssl_certificate_key /etc/ssl/private/audit.key;
    ssl_protocols       TLSv1.2 TLSv1.3;
    
    # 安全响应头
    add_header X-Content-Type-Options nosniff;
    add_header X-Frame-Options DENY;
    add_header Content-Security-Policy "default-src 'self'";
    add_header Strict-Transport-Security "max-age=31536000";
}
"""
```

### 9.4 数据隔离

- 审计数据库与业务数据库**完全分离**，使用独立的 SQLite 文件
- Admin 管理界面与业务应用**分开部署**（不同端口）
- 审计模块使用**只写**数据库连接，无法读取业务数据

---

## 10. 部署与运维

### 10.1 目录结构

```
/opt/audit-system/
├── app/                          # 应用代码
├── data/
│   ├── audit.db                  # 审计数据库（只追加）
│   └── app.db                    # 业务数据库
├── logs/
│   ├── audit/                    # 审计日志文件
│   ├── access/                   # 访问日志
│   └── error/                    # 错误日志
├── config/
│   ├── config.py                 # 应用配置
│   └── gunicorn.conf.py          # 生产服务器配置
└── scripts/
    ├── backup_audit.sh           # 每日备份脚本
    └── verify_integrity.sh       # 完整性校验脚本
```

### 10.2 数据保留策略

```python
RETENTION_POLICY = {
    'L5_CRITICAL': 'permanent',   # 永久保留
    'L4_HIGH':     '3_years',     # 保留 3 年
    'L3_MEDIUM':   '1_year',      # 保留 1 年
    'L2_LOW':      '90_days',     # 保留 90 天
    'L1_INFO':     '30_days',     # 保留 30 天
}
```

### 10.3 备份策略

```bash
#!/bin/bash
# backup_audit.sh - 每日凌晨 2:00 执行

DATE=$(date +%Y%m%d)
BACKUP_DIR="/backup/audit/${DATE}"
mkdir -p ${BACKUP_DIR}

# 备份数据库
sqlite3 /opt/audit-system/data/audit.db ".backup ${BACKUP_DIR}/audit.db"

# 备份日志文件
tar -czf ${BACKUP_DIR}/logs.tar.gz /opt/audit-system/logs/audit/

# 验证备份完整性
sha256sum ${BACKUP_DIR}/audit.db > ${BACKUP_DIR}/checksums.txt
sha256sum ${BACKUP_DIR}/logs.tar.gz >> ${BACKUP_DIR}/checksums.txt

# 同步到远程备份服务器（确保备份不可被本机用户删除）
rsync -az ${BACKUP_DIR}/ backup-server:/backup/audit/${DATE}/
```

### 10.4 告警通知配置

```python
ALERT_NOTIFICATION = {
    'channels': ['email', 'webhook'],
    'email': {
        'recipients': ['admin@company.com', 'security@company.com'],
        'threshold': 'HIGH',      # HIGH 及以上发邮件
    },
    'webhook': {
        'url': 'https://hooks.slack.com/...',  # 企业微信/飞书/Slack
        'threshold': 'CRITICAL',  # CRITICAL 才推送 webhook
    }
}
```

---

## 11. 关键代码结构

### 11.1 Flask 应用入口（app.py 结构）

```python
import logging
import sqlite3
import time
import hashlib
import json
from datetime import datetime, timedelta
from functools import wraps
from logging.handlers import RotatingFileHandler
from pathlib import Path
from flask import (Flask, Response, flash, jsonify, redirect,
                   render_template_string, request, send_file,
                   session, url_for)
from werkzeug.security import check_password_hash, generate_password_hash

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'change-in-production')
app.config['AUDIT_DB'] = 'data/audit.db'
app.config['ADMIN_USERNAME'] = 'admin'

# ── 初始化审计系统 ──────────────────────────────────────────────
audit_logger = AuditLogger(
    db_path=app.config['AUDIT_DB'],
    log_dir='logs/audit'
)

# ── 全局请求钩子 ────────────────────────────────────────────────
@app.before_request
def before_request():
    g.start_time = time.time()
    g.request_id = generate_request_id()

@app.after_request
def after_request(response):
    # 自动记录所有访问（L1 级别）
    if not request.path.startswith('/static/'):
        audit_logger.log(
            username=session.get('username', 'anonymous'),
            category='ACCESS',
            action_type='PAGE_VIEW',
            target_resource=request.path,
            status_code=response.status_code,
            duration_ms=int((time.time() - g.start_time) * 1000),
            request=request
        )
    return response

# ── Admin 认证装饰器 ────────────────────────────────────────────
def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('is_admin'):
            audit_logger.log(
                username=session.get('username', 'anonymous'),
                category='ACCESS',
                action_type='FORBIDDEN_ACCESS',
                target_resource=request.path,
                risk_level=3,
                request=request
            )
            return redirect(url_for('admin.login'))
        return f(*args, **kwargs)
    return decorated
```

### 11.2 数据库初始化脚本

```python
def init_db():
    """初始化数据库，创建所有表和约束"""
    conn = sqlite3.connect(app.config['AUDIT_DB'])
    cursor = conn.cursor()
    
    # 创建表...（见第4节建表语句）
    
    # 创建防篡改触发器
    cursor.executescript("""
        CREATE TRIGGER IF NOT EXISTS prevent_audit_delete
        BEFORE DELETE ON audit_logs
        BEGIN
            SELECT RAISE(ABORT, 'Audit logs are immutable - deletion not allowed');
        END;
        
        CREATE TRIGGER IF NOT EXISTS prevent_audit_update
        BEFORE UPDATE ON audit_logs
        BEGIN
            SELECT RAISE(ABORT, 'Audit logs are immutable - modification not allowed');
        END;
    """)
    
    # 创建默认 admin 用户
    admin_hash = generate_password_hash('admin-change-immediately')
    cursor.execute("""
        INSERT OR IGNORE INTO users (username, password_hash, role)
        VALUES (?, ?, 'admin')
    """, ('admin', admin_hash))
    
    conn.commit()
    conn.close()
```

---

## 12. Admin 密码管理方案

### 12.1 设计目标

- **集中配置**：在 `app.py` 顶部统一管理 Admin 密码
- **自动刷新**：每次应用启动时自动更新数据库中的密码
- **环境变量优先**：支持通过环境变量覆盖默认配置
- **安全提示**：使用默认密码时发出警告

### 12.2 实现方案

#### 12.2.1 app.py 顶部配置

```python
# ============= app.py 顶部配置 =============
import os
import logging
from pathlib import Path
from flask import Flask
from werkzeug.security import generate_password_hash

# ── Admin 密码配置 ──────────────────────────────────────
# 优先级：环境变量 > 配置文件 > 默认值
ADMIN_USERNAME = os.environ.get('AUDIT_ADMIN_USER', 'admin')
ADMIN_PASSWORD = os.environ.get('AUDIT_ADMIN_PASSWORD', 'Admin@2026!Change')

# 安全提示
if ADMIN_PASSWORD == 'Admin@2026!Change':
    logging.warning('⚠️  使用默认 Admin 密码，生产环境请通过环境变量设置！')
    logging.warning('   export AUDIT_ADMIN_PASSWORD=your_secure_password')

# ── Flask 应用初始化 ────────────────────────────────────
app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', os.urandom(24).hex())
app.config['AUDIT_DB'] = 'data/audit.db'
app.config['ADMIN_USERNAME'] = ADMIN_USERNAME
app.config['ADMIN_PASSWORD_HASH'] = generate_password_hash(ADMIN_PASSWORD)
```

#### 12.2.2 密码自动刷新函数

```python
def init_or_refresh_admin():
    """
    应用启动时执行：
    1. 如果 admin 用户不存在，创建
    2. 如果已存在，更新密码为当前配置的密码
    """
    import sqlite3
    
    db_path = Path(app.config['AUDIT_DB'])
    db_path.parent.mkdir(parents=True, exist_ok=True)
    
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    # 创建用户表（如果不存在）
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            username     TEXT    NOT NULL UNIQUE,
            password_hash TEXT   NOT NULL,
            role         TEXT    NOT NULL DEFAULT 'user',
            email        TEXT,
            created_at   TEXT    NOT NULL DEFAULT (datetime('now')),
            last_login   TEXT,
            login_ip     TEXT,
            is_active    INTEGER NOT NULL DEFAULT 1,
            failed_login_count INTEGER DEFAULT 0,
            locked_until TEXT
        )
    """)
    
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
        logging.info(f'✓ Admin 用户 "{ADMIN_USERNAME}" 密码已刷新')
    else:
        # 创建 admin 用户
        cursor.execute("""
            INSERT INTO users (username, password_hash, role, email)
            VALUES (?, ?, 'admin', 'admin@localhost')
        """, (ADMIN_USERNAME, app.config['ADMIN_PASSWORD_HASH']))
        logging.info(f'✓ Admin 用户 "{ADMIN_USERNAME}" 已创建')
    
    conn.commit()
    conn.close()

# 应用启动时执行
with app.app_context():
    init_or_refresh_admin()
```

### 12.3 使用方式

#### 开发环境

```bash
# 使用默认密码（会有警告）
python app.py

# 通过环境变量设置
export AUDIT_ADMIN_USER=admin
export AUDIT_ADMIN_PASSWORD=MySecurePassword123!
python app.py
```

#### 生产环境（systemd）

```ini
# /etc/systemd/system/audit-system.service
[Unit]
Description=Server Audit System
After=network.target

[Service]
Type=simple
User=audit
WorkingDirectory=/opt/audit-system
Environment="AUDIT_ADMIN_USER=admin"
Environment="AUDIT_ADMIN_PASSWORD=ProductionPassword2026!"
Environment="SECRET_KEY=your-secret-key-here"
ExecStart=/usr/bin/python3 /opt/audit-system/app.py
Restart=always

[Install]
WantedBy=multi-user.target
```

#### Docker 部署

```bash
docker run -d \
  -e AUDIT_ADMIN_USER=admin \
  -e AUDIT_ADMIN_PASSWORD=SecurePass123! \
  -e SECRET_KEY=your-secret-key \
  -p 5000:5000 \
  -v /opt/audit-data:/app/data \
  audit-system:latest
```

#### Docker Compose

```yaml
version: '3.8'
services:
  audit-system:
    image: audit-system:latest
    environment:
      - AUDIT_ADMIN_USER=admin
      - AUDIT_ADMIN_PASSWORD=${AUDIT_ADMIN_PASSWORD}
      - SECRET_KEY=${SECRET_KEY}
    ports:
      - "5000:5000"
    volumes:
      - ./data:/app/data
      - ./logs:/app/logs
    restart: always
```

### 12.4 安全建议

1. **强密码策略**：密码至少 12 位，包含大小写字母、数字、特殊字符
2. **定期轮换**：建议每 90 天更换一次密码
3. **环境变量保护**：生产环境使用密钥管理服务（如 HashiCorp Vault）
4. **审计记录**：密码更新操作应被记录到审计日志
5. **多因素认证**：建议启用 TOTP 双因素认证

---

## 13. 中控台系统设计

### 13.1 设计目标

- **统一监控**：在本地部署中控台，同时查看多个服务器的审计数据
- **实时告警**：汇总所有服务器的告警信息
- **跨服务器分析**：支持跨服务器的日志查询和统计分析
- **集中管理**：统一的用户界面，无需逐个登录服务器

### 13.2 系统架构

```
┌─────────────────────────────────────────────────────────────┐
│                    本地中控台 (Control Center)               │
│                     http://localhost:8000                   │
│                                                             │
│  ┌──────────────────────────────────────────────────────┐  │
│  │  统一管理界面 (Unified Dashboard)                     │  │
│  │  ┌────────────────────────────────────────────────┐  │  │
│  │  │  服务器状态卡片                                 │  │  │
│  │  │  ┌──────────┐ ┌──────────┐ ┌──────────┐       │  │  │
│  │  │  │ Server 1 │ │ Server 2 │ │ Server 3 │       │  │  │
│  │  │  │ 在线     │ │ 在线     │ │ 离线     │       │  │  │
│  │  │  │ L5: 2    │ │ L5: 0    │ │ -        │       │  │  │
│  │  │  │ L4: 5    │ │ L4: 3    │ │ -        │       │  │  │
│  │  │  └──────────┘ └──────────┘ └──────────┘       │  │  │
│  │  └────────────────────────────────────────────────┘  │  │
│  │  ┌────────────────────────────────────────────────┐  │  │
│  │  │  实时告警汇总                                   │  │  │
│  │  │  🔴 [Server 1] 删除 /etc/passwd                │  │  │
│  │  │  🟠 [Server 2] sudo 权限提升                   │  │  │
│  │  │  🟡 [Server 1] 修改配置文件                    │  │  │
│  │  └────────────────────────────────────────────────┘  │  │
│  │  ┌────────────────────────────────────────────────┐  │  │
│  │  │  跨服务器日志查询                               │  │  │
│  │  │  [时间范围] [用户] [风险级别] [服务器] [搜索]  │  │  │
│  │  └────────────────────────────────────────────────┘  │  │
│  └──────────────────────────────────────────────────────┘  │
│                           │                                 │
│              ┌────────────┼────────────┐                    │
│              │            │            │                    │
│              ▼            ▼            ▼                    │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐        │
│  │ API Client  │  │ API Client  │  │ API Client  │        │
│  │ (JWT Auth)  │  │ (JWT Auth)  │  │ (JWT Auth)  │        │
│  └─────────────┘  └─────────────┘  └─────────────┘        │
└─────────────────────────────────────────────────────────────┘
         │                 │                 │
         │ HTTPS           │ HTTPS           │ HTTPS
         │ REST API        │ REST API        │ REST API
         ▼                 ▼                 ▼
┌──────────────┐  ┌──────────────┐  ┌──────────────┐
│  服务器 1     │  │  服务器 2     │  │  服务器 3     │
│ 192.168.1.10 │  │ 192.168.1.11 │  │ 192.168.1.12 │
│              │  │              │  │              │
│ Audit System │  │ Audit System │  │ Audit System │
│ + REST API   │  │ + REST API   │  │ + REST API   │
│ Port: 5000   │  │ Port: 5000   │  │ Port: 5000   │
└──────────────┘  └──────────────┘  └──────────────┘
```

### 13.3 服务器端 REST API 扩展

在每台服务器的审计系统中添加 REST API 支持。

#### 13.3.1 API 认证

```python
# api/auth.py
import jwt
from datetime import datetime, timedelta
from flask import Blueprint, request, jsonify, current_app
from werkzeug.security import check_password_hash

auth_bp = Blueprint('api_auth', __name__, url_prefix='/api/v1/auth')

@auth_bp.route('/token', methods=['POST'])
def generate_token():
    """
    中控台获取 API Token
    
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
    username = data.get('username')
    password = data.get('password')
    
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
```

#### 13.3.2 API 路由

```python
# api/routes.py
from flask import Blueprint, request, jsonify, g
from functools import wraps
import jwt

api_bp = Blueprint('api', __name__, url_prefix='/api/v1')

def api_auth_required(f):
    """API 认证装饰器"""
    @wraps(f)
    def decorated(*args, **kwargs):
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
    
    conn.close()
    
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
        'unread_alerts': conn.execute(
            "SELECT COUNT(*) FROM alerts WHERE is_read = 0"
        ).fetchone()[0]
    }
    
    conn.close()
    return jsonify(stats)

@api_bp.route('/alerts', methods=['GET'])
@api_auth_required
def get_alerts():
    """获取告警列表"""
    unread_only = request.args.get('unread_only', 'false').lower() == 'true'
    
    conn = get_db()
    query = "SELECT * FROM alerts"
    
    if unread_only:
        query += " WHERE is_read = 0"
    
    query += " ORDER BY created_at DESC LIMIT 100"
    
    cursor = conn.execute(query)
    alerts = [dict(row) for row in cursor.fetchall()]
    conn.close()
    
    return jsonify({'data': alerts})

@api_bp.route('/server/info', methods=['GET'])
@api_auth_required
def get_server_info():
    """获取服务器基本信息"""
    import socket
    import platform
    
    return jsonify({
        'hostname': socket.gethostname(),
        'platform': platform.system(),
        'version': platform.version(),
        'python_version': platform.python_version()
    })
```

### 13.4 中控台应用实现

#### 13.4.1 服务器配置

```python
# control_center/config.py
SERVERS = [
    {
        'id': 'prod-server-1',
        'name': '生产服务器 1',
        'url': 'https://192.168.1.10:5000',
        'username': 'admin',
        'password': 'password1',
        'description': 'Web 应用服务器'
    },
    {
        'id': 'prod-server-2',
        'name': '生产服务器 2',
        'url': 'https://192.168.1.11:5000',
        'username': 'admin',
        'password': 'password2',
        'description': '数据库服务器'
    },
    {
        'id': 'test-server',
        'name': '测试服务器',
        'url': 'https://192.168.1.12:5000',
        'username': 'admin',
        'password': 'password3',
        'description': '测试环境'
    }
]
```

#### 13.4.2 API 客户端

```python
# control_center/api_client.py
import requests
from datetime import datetime
import logging

class AuditAPIClient:
    """审计系统 API 客户端"""
    
    def __init__(self, server_config):
        self.server_id = server_config['id']
        self.server_name = server_config['name']
        self.base_url = server_config['url']
        self.username = server_config['username']
        self.password = server_config['password']
        self.token = None
        self.token_expires = None
        self.logger = logging.getLogger(f'APIClient[{self.server_id}]')
    
    def authenticate(self):
        """获取 API Token"""
        try:
            resp = requests.post(
                f"{self.base_url}/api/v1/auth/token",
                json={'username': self.username, 'password': self.password},
                timeout=10,
                verify=False  # 生产环境应验证证书
            )
            
            if resp.status_code == 200:
                data = resp.json()
                self.token = data['token']
                self.token_expires = datetime.now().timestamp() + data['expires_in']
                self.logger.info(f'认证成功')
                return True
            else:
                self.logger.error(f'认证失败: {resp.status_code}')
        except Exception as e:
            self.logger.error(f'认证异常: {e}')
        
        return False
    
    def _ensure_token(self):
        """确保 Token 有效"""
        if not self.token or datetime.now().timestamp() >= self.token_expires:
            return self.authenticate()
        return True
    
    def _request(self, method, endpoint, **kwargs):
        """统一请求方法"""
        if not self._ensure_token():
            return None
        
        try:
            resp = requests.request(
                method,
                f"{self.base_url}{endpoint}",
                headers={'Authorization': f'Bearer {self.token}'},
                timeout=kwargs.pop('timeout', 30),
                verify=False,
                **kwargs
            )
            
            if resp.status_code == 200:
                return resp.json()
            else:
                self.logger.error(f'{method} {endpoint} 失败: {resp.status_code}')
        except Exception as e:
            self.logger.error(f'{method} {endpoint} 异常: {e}')
        
        return None
    
    def get_logs(self, **params):
        """查询日志"""
        return self._request('GET', '/api/v1/logs', params=params)
    
    def get_stats(self):
        """获取统计数据"""
        return self._request('GET', '/api/v1/stats')
    
    def get_alerts(self, unread_only=False):
        """获取告警"""
        return self._request('GET', '/api/v1/alerts', 
                           params={'unread_only': str(unread_only).lower()})
    
    def get_server_info(self):
        """获取服务器信息"""
        return self._request('GET', '/api/v1/server/info')
```

#### 13.4.3 中控台主应用

```python
# control_center/app.py
from flask import Flask, render_template, request, jsonify
from config import SERVERS
from api_client import AuditAPIClient
import logging

app = Flask(__name__)
app.config['SECRET_KEY'] = 'control-center-secret-key'

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s'
)

# 初始化所有服务器客户端
server_clients = {
    server['id']: AuditAPIClient(server)
    for server in SERVERS
}

@app.route('/')
def dashboard():
    """统一仪表盘"""
    return render_template('dashboard.html', servers=SERVERS)

@app.route('/api/servers')
def list_servers():
    """服务器列表"""
    return jsonify({'servers': SERVERS})

@app.route('/api/servers/<server_id>/stats')
def server_stats(server_id):
    """单个服务器统计"""
    client = server_clients.get(server_id)
    if not client:
        return jsonify({'error': 'Server not found'}), 404
    
    stats = client.get_stats()
    if stats:
        stats['server_id'] = server_id
        stats['server_name'] = client.server_name
        stats['status'] = 'online'
        return jsonify(stats)
    
    return jsonify({
        'server_id': server_id,
        'server_name': client.server_name,
        'status': 'offline'
    })

@app.route('/api/servers/all/stats')
def all_servers_stats():
    """所有服务器统计汇总"""
    results = []
    
    for server_id, client in server_clients.items():
        stats = client.get_stats()
        if stats:
            stats['server_id'] = server_id
            stats['server_name'] = client.server_name
            stats['status'] = 'online'
        else:
            stats = {
                'server_id': server_id,
                'server_name': client.server_name,
                'status': 'offline'
            }
        
        results.append(stats)
    
    return jsonify({'servers': results})

@app.route('/api/servers/<server_id>/logs')
def server_logs(server_id):
    """单个服务器日志"""
    client = server_clients.get(server_id)
    if not client:
        return jsonify({'error': 'Server not found'}), 404
    
    # 传递查询参数
    params = {k: v for k, v in request.args.items()}
    
    logs = client.get_logs(**params)
    if logs:
        return jsonify(logs)
    
    return jsonify({'error': 'Failed to fetch logs'}), 500

@app.route('/api/servers/all/alerts')
def all_servers_alerts():
    """所有服务器告警汇总"""
    all_alerts = []
    
    for server_id, client in server_clients.items():
        alerts_data = client.get_alerts(unread_only=True)
        if alerts_data:
            for alert in alerts_data.get('data', []):
                alert['server_id'] = server_id
                alert['server_name'] = client.server_name
                all_alerts.append(alert)
    
    # 按时间排序
    all_alerts.sort(key=lambda x: x.get('created_at', ''), reverse=True)
    
    return jsonify({'data': all_alerts})

@app.route('/logs')
def logs_page():
    """日志查询页面"""
    return render_template('logs.html', servers=SERVERS)

@app.route('/alerts')
def alerts_page():
    """告警中心页面"""
    return render_template('alerts.html', servers=SERVERS)

if __name__ == '__main__':
    app.run(host='127.0.0.1', port=8000, debug=True)
```

### 13.5 中控台前端界面

前端界面使用简洁的 HTML + JavaScript 实现，支持：

- **服务器状态卡片**：实时显示每台服务器的统计数据
- **告警汇总**：跨服务器的告警列表
- **日志查询**：支持选择服务器、时间范围、用户等条件查询
- **自动刷新**：每 30 秒自动刷新数据

详细前端代码见 `templates/dashboard.html`。

### 13.6 部署方案

#### 13.6.1 服务器端部署

```bash
# 每台服务器
cd /opt/audit-system

# 设置环境变量
export AUDIT_ADMIN_PASSWORD=SecurePassword123!
export API_SECRET_KEY=api-secret-key-change-me
export SECRET_KEY=flask-secret-key

# 启动应用（使用 gunicorn）
gunicorn -w 4 -b 0.0.0.0:5000 \
  --certfile=/etc/ssl/certs/server.crt \
  --keyfile=/etc/ssl/private/server.key \
  app:app
```

#### 13.6.2 中控台部署

```bash
# 本地机器
cd /opt/control-center

# 安装依赖
pip install flask requests

# 编辑服务器配置
vim config.py

# 启动中控台
python app.py

# 访问
open http://localhost:8000
```

### 13.7 安全建议

1. **HTTPS 通信**：服务器端必须启用 HTTPS
2. **Token 管理**：定期轮换 API Secret Key
3. **IP 白名单**：限制中控台访问服务器的 IP
4. **密码管理**：使用密钥管理服务存储服务器密码
5. **审计日志**：中控台的操作也应被记录
6. **访问控制**：中控台应有独立的认证机制
7. **网络隔离**：中控台与服务器之间使用专用网络

### 13.8 功能扩展

未来可扩展的功能：

- **实时推送**：使用 WebSocket 实现实时告警推送
- **数据聚合**：跨服务器的统计分析和报表生成
- **批量操作**：批量查询、批量导出
- **拓扑视图**：服务器拓扑关系可视化
- **智能告警**：基于机器学习的异常检测

---

## 附录：操作风险速查表

| 操作类型 | 默认级别 | 敏感路径 | 深夜 | 批量 |
|----------|----------|----------|------|------|
| 用户登录失败连续 5 次 | L2 | - | +1 | → L4 |
| 删除 /etc/ 下文件 | L4 | +1 | - | → L5 |
| 执行 rm -rf | L4 | +1 | +1 | → L5 |
| 修改 /etc/passwd | L4 | +1 | - | → L5 |
| 停止 nginx/mysql 服务 | L4 | - | - | → L4 |
| 清空数据库表 | L5 | - | - | → L5 |
| 格式化磁盘命令 | L5 | - | - | → L5 |
| 普通文件上传 | L2 | - | - | → L2 |
| 普通 API 调用 | L1 | - | - | → L1 |

---

*文档版本：v2.0 | 最后更新：2026-05-28*  
*适用范围：基于 Flask + SQLite + Werkzeug 技术栈的服务器审计系统*  
*新增功能：Admin 密码管理 · 中控台系统*
