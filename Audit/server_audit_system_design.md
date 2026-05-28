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

---

## 1. 系统概述

### 1.1 背景与目标

服务器安全审计系统旨在对所有历史登录用户的操作行为进行完整记录，通过操作分级归类机制，支持事后溯源、损失定责和安全分析。核心目标：

- **全量采集**：记录用户登录、文件操作、命令执行、配置变更等全部行为
- **分级归类**：按风险等级将操作分为 5 个级别，快速定位高危操作
- **不可篡改**：日志写入后只读，防止攻击者销毁证据
- **溯源追责**：支持按用户、时间、操作类型多维检索，还原事件时间线
- **管理可视**：Admin 管理界面实时展示告警、统计与审计报告

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

*文档版本：v1.0 | 最后更新：2025-03*  
*适用范围：基于 Flask + SQLite + Werkzeug 技术栈的服务器审计系统*
