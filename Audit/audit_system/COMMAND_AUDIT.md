# Linux 命令审计 - 使用指南

> 记录所有 Linux 用户在终端执行的命令

## 🎯 功能说明

### 当前审计系统的问题

**之前：**
- ❌ 只记录 Web 界面操作（admin 登录等）
- ❌ 看不到 Linux 系统用户（如 yuchen）的命令
- ❌ 日志只显示操作类型，看不到具体命令

**现在：**
- ✅ 记录所有 Linux 用户的终端命令
- ✅ 显示完整的命令内容
- ✅ 显示执行用户、时间、工作目录
- ✅ 自动分类和风险评估

---

## 🚀 快速开始

### 1. 安装命令审计

```bash
cd /home/yuchen/scripts/Audit/audit_system
./install_command_audit.sh
```

**选择安装方式：**
- **选项 1**：仅为当前用户安装（推荐）
- **选项 2**：为所有用户安装（需要 root）

### 2. 使配置生效

```bash
source ~/.bashrc
```

### 3. 测试功能

```bash
# 执行一些命令
ls -la
cd /tmp
cat /etc/hosts
```

### 4. 查看审计日志

```bash
# 查看最近 50 条命令
python3 view_audit_logs.py

# 查看指定用户的命令
python3 view_audit_logs.py --user yuchen

# 查看更多记录
python3 view_audit_logs.py --limit 100
```

**输出示例：**
```
📋 命令审计日志 - /home/yuchen/.audit_commands.log
====================================================================================================
时间                 用户       主机            工作目录                       命令
----------------------------------------------------------------------------------------------------
2026-05-28 12:30:15 yuchen     ubuntu-server   /home/yuchen                   ls -la
2026-05-28 12:30:20 yuchen     ubuntu-server   /home/yuchen                   cd /tmp
2026-05-28 12:30:25 yuchen     ubuntu-server   /tmp                           cat /etc/hosts
----------------------------------------------------------------------------------------------------
共 3 条记录
```

### 5. 导入到审计系统

```bash
# 将命令日志导入到审计系统数据库
python3 import_command_logs.py
```

**输出示例：**
```
📋 开始导入命令日志: /home/yuchen/.audit_commands.log
================================================================================
已导入 100 条...
已导入 200 条...
================================================================================
✅ 导入完成！
  - 新增: 234 条
  - 跳过: 12 条（已存在）

📊 查看日志:
  访问 http://localhost:5000
  使用 admin / BY116358 登录
```

### 6. 在 Web 界面查看

1. 访问：http://localhost:5000
2. 登录：admin / BY116358
3. 点击"日志查询"
4. 现在可以看到：
   - ✅ 用户名：yuchen（而不是 anonymous）
   - ✅ 操作类型：COMMAND_EXEC、FILE_OPERATION 等
   - ✅ 目标资源：完整的命令内容
   - ✅ 详细信息：工作目录、主机名、进程 ID

---

## 📊 命令分类和风险级别

### 自动分类规则

| 命令类型 | 示例命令 | 分类 | 风险级别 |
|---------|---------|------|---------|
| 高危命令 | `rm -rf /`, `dd if=/dev/zero`, `mkfs` | SYSTEM / DANGEROUS_COMMAND | L5 - CRITICAL |
| 系统管理 | `sudo`, `systemctl`, `reboot` | SYSTEM / SYSTEM_ADMIN | L4 - HIGH |
| 文件操作 | `rm`, `mv`, `chmod`, `chown` | FILE / FILE_OPERATION | L3 - MEDIUM |
| 网络操作 | `curl`, `wget`, `ssh`, `scp` | NETWORK / NETWORK_ACCESS | L3 - MEDIUM |
| 数据库操作 | `mysql`, `psql`, `mongo` | DATA / DATABASE_ACCESS | L3 - MEDIUM |
| 包管理 | `apt`, `pip`, `npm` | SYSTEM / PACKAGE_INSTALL | L3 - MEDIUM |
| 查看命令 | `ls`, `cat`, `grep`, `find` | FILE / FILE_READ | L1 - INFO |
| 其他命令 | 其他所有命令 | SYSTEM / COMMAND_EXEC | L2 - LOW |

### 示例

```bash
# L5 - CRITICAL
sudo rm -rf /var/log/*

# L4 - HIGH
sudo systemctl restart nginx

# L3 - MEDIUM
chmod 777 /tmp/test.sh
curl https://api.example.com/data

# L2 - LOW
echo "Hello World"
python3 script.py

# L1 - INFO
ls -la
cat /etc/hosts
```

---

## 🔍 日志格式

### 本地日志格式

**文件位置：**
- 用户安装：`~/.audit_commands.log`
- 系统安装：`/var/log/audit_commands.log`

**JSON 格式：**
```json
{
  "timestamp": "2026-05-28T12:30:15.123456",
  "username": "yuchen",
  "hostname": "ubuntu-server",
  "session_id": "yuchen_12345_20260528123000",
  "command": "ls -la /home/yuchen",
  "working_dir": "/home/yuchen",
  "pid": 12345,
  "ppid": 12344,
  "checksum": "a1b2c3d4e5f6g7h8"
}
```

### 数据库格式

导入到审计系统后，会转换为标准审计日志格式：

| 字段 | 值 | 说明 |
|------|---|------|
| timestamp | 2026-05-28T12:30:15 | 执行时间 |
| username | yuchen | Linux 用户名 |
| action_category | FILE | 操作分类 |
| action_type | FILE_OPERATION | 操作类型 |
| target_resource | ls -la /home/yuchen | 完整命令 |
| risk_level | 1 | 风险级别 |
| ip_address | ubuntu-server | 主机名 |
| user_agent | Terminal@/home/yuchen | 工作目录 |
| details | JSON 详细信息 | 包含 PID、PPID 等 |

---

## 🔧 工作原理

### 技术实现

```
┌─────────────────────────────────────────────────────────┐
│ 1. 用户执行命令                                         │
│    $ ls -la                                             │
└────────────────┬────────────────────────────────────────┘
                 │
                 ▼
┌─────────────────────────────────────────────────────────┐
│ 2. Bash PROMPT_COMMAND 钩子触发                         │
│    audit_log_command() 函数被调用                       │
└────────────────┬────────────────────────────────────────┘
                 │
                 ▼
┌─────────────────────────────────────────────────────────┐
│ 3. 调用 audit_command.py                                │
│    - 获取命令内容（从 history）                         │
│    - 获取环境信息（用户、主机、目录）                   │
│    - 生成会话 ID                                        │
└────────────────┬────────────────────────────────────────┘
                 │
                 ▼
┌─────────────────────────────────────────────────────────┐
│ 4. 写入本地日志                                         │
│    ~/.audit_commands.log（JSON 格式）                   │
└────────────────┬────────────────────────────────────────┘
                 │
                 ▼
┌─────────────────────────────────────────────────────────┐
│ 5. 定期导入到审计系统                                   │
│    python3 import_command_logs.py                       │
│    - 读取本地日志                                       │
│    - 分类和风险评估                                     │
│    - 写入数据库                                         │
└─────────────────────────────────────────────────────────┘
```

### 关键文件

```
audit_system/
├── audit_command.py           # 命令记录脚本
├── audit_bash_hook.sh         # Bash 钩子脚本
├── view_audit_logs.py         # 日志查看工具
├── import_command_logs.py     # 日志导入工具
└── install_command_audit.sh   # 安装脚本
```

---

## 📝 使用场景

### 场景 1：查看自己的命令历史

```bash
# 查看最近执行的命令
python3 view_audit_logs.py --user yuchen --limit 20
```

### 场景 2：审计高危操作

```bash
# 导入到审计系统
python3 import_command_logs.py

# 在 Web 界面筛选
# - 最小风险级别：L4 - HIGH
# - 用户：yuchen
```

### 场景 3：追踪问题

```bash
# 查看某个时间段的所有操作
# 1. 查看本地日志
python3 view_audit_logs.py --limit 1000 | grep "2026-05-28 12:"

# 2. 或在 Web 界面查询
# 访问 http://localhost:5000
# 使用时间范围筛选
```

### 场景 4：多用户审计

```bash
# 为所有用户安装（需要 root）
sudo ./install_command_audit.sh
# 选择选项 2

# 查看所有用户的命令
python3 view_audit_logs.py --limit 500
```

---

## ⚙️ 配置选项

### 修改日志文件位置

编辑 `audit_command.py`：

```python
# 自定义日志路径
AUDIT_LOG_FILE = Path('/custom/path/audit_commands.log')
```

### 修改审计服务器地址

编辑 `audit_command.py`：

```python
# 如果需要实时上报到远程服务器
AUDIT_SERVER_URL = 'http://192.168.1.10:5000'
```

### 过滤特定命令

编辑 `audit_bash_hook.sh`：

```bash
# 添加过滤规则
if [[ "$last_cmd" =~ "ls" ]] || [[ "$last_cmd" =~ "cd" ]]; then
    return  # 不记录 ls 和 cd 命令
fi
```

---

## 🔒 安全建议

### 1. 保护日志文件

```bash
# 限制日志文件权限
chmod 600 ~/.audit_commands.log

# 系统日志
sudo chmod 644 /var/log/audit_commands.log
```

### 2. 定期清理日志

```bash
# 保留最近 30 天的日志
find ~/.audit_commands.log -mtime +30 -delete

# 或使用 logrotate
sudo vim /etc/logrotate.d/audit_commands
```

### 3. 防止绕过

**注意：** 此审计方案基于 bash 钩子，可以被绕过：

- 用户可以使用其他 shell（zsh、fish）
- 用户可以删除 ~/.bashrc 中的钩子
- 用户可以使用 `bash --norc` 启动

**更安全的方案：**
- 使用 `auditd`（Linux 内核审计）
- 使用 `snoopy`（系统级命令记录）
- 使用 `sudo` 日志

---

## 🐛 故障排查

### 问题 1：命令没有被记录

**检查钩子是否生效：**
```bash
echo $PROMPT_COMMAND
# 应该包含 audit_log_command
```

**重新加载配置：**
```bash
source ~/.bashrc
```

### 问题 2：日志文件不存在

**检查路径：**
```bash
ls -la ~/.audit_commands.log
```

**手动创建：**
```bash
touch ~/.audit_commands.log
chmod 600 ~/.audit_commands.log
```

### 问题 3：导入失败

**检查数据库：**
```bash
ls -la /home/yuchen/scripts/Audit/audit_system/data/audit.db
```

**确保审计系统已启动：**
```bash
cd /home/yuchen/scripts/Audit/audit_system
./start.sh
```

---

## 📚 相关文档

- [审计系统主文档](README.md)
- [中控台文档](../control_center/README_CONTROL_CENTER.md)

---

## 📄 许可证

MIT License

---

**版本**：v1.0  
**更新日期**：2026-05-28  
**适用系统**：Linux（Bash）
