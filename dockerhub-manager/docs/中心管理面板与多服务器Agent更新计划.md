# 中心管理面板与多服务器 Agent 更新计划

## 结论

选择一台服务器作为中心管理面板是可行方案。该服务器同时承担两类职责：

- 对外提供 Web 管理界面，供多个管理员/分配员访问。
- 作为一台普通 Docker 服务器，通过本机 Agent 管理自己的容器。

其他服务器只部署轻量 Agent，不暴露管理前端。中心面板通过 Agent API 管理它们。

推荐拓扑：

```text
管理员浏览器
    |
    | HTTPS / Web
    v
中心管理服务器
    |-- Flask Web 面板
    |-- 共享配置与用户数据
    |-- 本机 dockerhub-agent
    |
    | Agent API + Token
    v
其他 Docker 服务器
    |-- dockerhub-agent
    |-- Docker Engine
```

## 设计原则

- 中心面板是唯一配置源，避免多个管理员各自维护服务器配置。
- Agent 只暴露受 Token 保护的 API，不提供 Web 管理页面。
- 中心服务器也必须通过 Agent 管理本机 Docker，避免为本机写特殊逻辑。
- 部署目录使用隐藏目录，降低误操作概率。
- 容器重要数据必须放在宿主机挂载目录或 Docker volume，配置更新时才可保留数据。

## 角色与部署形态

### 中心管理服务器

运行：

```text
dockerhub-manager/app.py
dockerhub-manager/agent/agent.py
```

职责：

- 用户登录与角色权限。
- 服务器配置管理。
- 多目录挂载配置管理。
- 容器创建、启停、删除、资源更新。
- 配置导入/导出。
- 审计日志记录。

### 普通 Docker 服务器

只运行：

```text
/opt/.dockerhub-agent/agent.py
```

职责：

- 检查 Docker 状态。
- 上报 CPU、内存、磁盘、挂载根目录状态。
- 创建容器。
- 更新容器 CPU、内存、PIDs 等资源限制。
- 安全校验挂载路径和容器权限。

## 部署脚本更新

当前问题：

```text
deploy.sh 写死 root@server
部署时要求传 DATA_PATH
Agent 工作目录是 /opt/dockerhub-agent
```

更新后：

```bash
bash deploy.sh ubuntu@192.168.1.10 my-token 5001
bash deploy.sh root@192.168.1.11 my-token 5001
bash deploy.sh gpu01 my-token 5001
```

参数：

```text
第 1 参数：SSH 目标，支持 user@host 或 SSH config 别名
第 2 参数：Agent Token
第 3 参数：Agent 端口，默认 5001
```

部署目录：

```text
/opt/.dockerhub-agent
```

不再在部署阶段传数据盘路径。数据盘路径由中心面板在服务器配置里维护。

非 root SSH 用户部署时，远程命令使用 `sudo`：

```bash
ssh "$SSH_TARGET" "sudo mkdir -p /opt/.dockerhub-agent"
scp agent/agent.py "$SSH_TARGET:/tmp/dockerhub-agent.py"
ssh "$SSH_TARGET" "sudo mv /tmp/dockerhub-agent.py /opt/.dockerhub-agent/agent.py"
```

## 服务器配置模型

服务器配置由中心面板保存：

```json
{
  "id": "srv_001",
  "name": "GPU-01",
  "host": "192.168.1.10",
  "agent_port": 5001,
  "agent_token": "secret-token",
  "ssh_host": "192.168.1.10",
  "mount_roots": [
    {
      "name": "用户数据",
      "host_path": "/mnt/data/users",
      "default_container_path": "/workspace",
      "readonly": false
    },
    {
      "name": "公共数据集",
      "host_path": "/data/datasets",
      "default_container_path": "/datasets",
      "readonly": true
    }
  ]
}
```

说明：

- `host` 用于中心面板调用 Agent。
- `ssh_host` 用于生成使用者 SSH 登录命令，允许和 Agent 地址不同。
- `mount_roots` 支持多目录管理。
- 每个挂载根目录可配置默认容器路径和只读策略。

## 多目录挂载

创建容器时，前端从该服务器的 `mount_roots` 中选择挂载项，并允许调整容器内路径：

```json
{
  "mounts": [
    {
      "host_path": "/mnt/data/users/alice",
      "container_path": "/workspace",
      "readonly": false
    },
    {
      "host_path": "/data/datasets/imagenet",
      "container_path": "/datasets/imagenet",
      "readonly": true
    }
  ]
}
```

Agent 转换为 Docker 参数：

```bash
-v /mnt/data/users/alice:/workspace:rw
-v /data/datasets/imagenet:/datasets/imagenet:ro
```

Agent 必须校验：

- `host_path` 必须在服务器配置允许的 `mount_roots` 下。
- `host_path` 必须存在。
- `container_path` 必须是绝对路径。
- 禁止挂载 `/`、`/etc`、`/proc`、`/sys`、`/var/run/docker.sock`。
- 禁止容器内挂载到 `/etc`、`/bin`、`/usr`、`/var/run` 等系统路径。

## 容器登录用户

当前问题：

```text
SSH 命令写死 root@host
容器内 PermitRootLogin yes
默认密码 dockerpass
```

更新后创建容器时填写：

```json
{
  "login_user": "alice",
  "ssh_public_key": "ssh-rsa ...",
  "allow_sudo": false
}
```

生成命令：

```bash
ssh -p 32001 alice@192.168.1.10
```

Agent 创建容器时：

- 创建非 root 用户。
- 写入 `authorized_keys`。
- 默认禁用 root SSH 登录。
- 默认禁用密码登录。
- 可选是否授予 sudo。

SSH 配置默认：

```text
PermitRootLogin no
PasswordAuthentication no
PubkeyAuthentication yes
```

## 默认资源限制

CPU、内存默认值由 Agent 上报本机真实资源后计算。

Agent `/sysinfo` 返回：

```json
{
  "cpu_cores": 64,
  "memory_bytes": 274877906944,
  "docker_ok": true
}
```

前端默认：

```text
默认 CPU = max(1, floor(cpu_cores / 8))
默认内存 = max(1GB, floor(memory_bytes / 8))
```

例如：

```text
64 核 / 256GB
默认分配 8 核 / 32GB
```

前端允许管理员或分配员修改：

- CPU 核数。
- 内存上限。
- PIDs 限制。
- 是否允许 GPU。

后端和 Agent 都需要校验：

- CPU 大于 0。
- CPU 不超过服务器总核数。
- 内存格式合法。
- 内存不超过服务器总内存。
- PIDs 限制为正整数。

## 容器配置更新与数据保留

### 可原地更新

以下配置使用 `docker update`，不删除容器，不丢数据：

- CPU。
- 内存。
- PIDs 限制。
- 重启策略。

Agent 新增：

```text
PATCH /containers/<name>/resources
```

执行：

```bash
docker update \
  --cpus 4 \
  --memory 8g \
  --pids-limit 512 \
  container_name
```

### 需要重建容器

以下配置通常不能原地更新：

- 镜像。
- 端口映射。
- 挂载路径。
- 登录用户。
- 部分安全参数。

处理流程：

```text
1. 保留旧容器。
2. 用新配置创建临时容器。
3. 复用原宿主机挂载目录或 Docker volume。
4. 健康检查成功后停止旧容器。
5. 删除旧容器。
6. 将新容器重命名为正式名称。
7. 如果新容器失败，删除新容器并恢复旧容器。
```

命名示例：

```text
旧容器：user_alice
临时容器：user_alice_new_20260531_120000
```

数据保留边界：

- 保证保留宿主机挂载目录和 Docker volume 中的数据。
- 不保证保留容器系统层内未挂载目录的修改。

前端必须提示：

```text
仅挂载目录中的数据会保留，容器内部系统盘修改不保证保留。
```

## 降低磁盘和 CPU 占用

当前 Agent 创建容器时每次运行：

```bash
apt-get update
apt-get install openssh-server
```

问题：

- 启动慢。
- 占 CPU。
- 占网络。
- 占容器层磁盘。

更新方案：

- 不在容器启动时安装 SSH。
- 使用预制基础镜像，例如 `dockerhub-managed/ubuntu-ssh:22.04`。
- 镜像内预装 `openssh-server`、基础用户初始化脚本。
- 容器启动命令只运行 `/usr/sbin/sshd -D`。

默认 Docker 安全参数：

```bash
--cpus <cpu>
--memory <memory>
--pids-limit 512
--restart unless-stopped
--security-opt no-new-privileges
--cap-drop ALL
```

如需 sudo、GPU、额外 capability，必须在前端显式选择。

## Agent 检查接口

新增：

```text
GET /checks
```

返回：

```json
{
  "docker_ok": true,
  "docker_version": "26.x",
  "agent_user": "root",
  "workdir": "/opt/.dockerhub-agent",
  "workdir_size_mb": 12,
  "cpu_cores": 64,
  "memory_bytes": 274877906944,
  "mount_roots": [
    {
      "path": "/mnt/data",
      "exists": true,
      "writable": true,
      "free_gb": 1200
    }
  ],
  "warnings": []
}
```

检查项：

- Docker 是否可用。
- Agent 是否具备执行 Docker 的权限。
- 工作目录是否为隐藏目录。
- 工作目录大小是否异常。
- 挂载根目录是否存在。
- 挂载根目录是否可写。
- 是否存在 privileged 容器。
- 是否暴露 docker.sock。

## 多管理员共享配置

推荐先采用单中心面板模式：

```text
所有管理员访问同一个中心管理服务器 Web 页面
服务器配置、用户、容器记录保存在中心面板
```

这样新增管理员只需要创建账号，不需要重复录入服务器配置。

后续可增加配置导入/导出：

```text
GET  /api/config/export
POST /api/config/import
```

导出策略：

- 默认不导出 `agent_token`。
- 如需包含 Token，必须管理员二次确认。
- 导出文件标记版本和导出时间。

导入策略：

- 按 `server_id` 合并。
- 同 ID 支持跳过或覆盖。
- 导入后自动检查 Agent 连通性。

如果未来需要多个中心面板实例同时运行，再考虑 PostgreSQL / MySQL 共享数据库。不建议当前阶段做多主配置同步。

## 中心面板故障恢复

中心管理服务器可能故障，必须保证可以在另一台机器上快速恢复管理能力，并重新连接已有 Agent。

关键原则：

- Agent 独立运行，不依赖中心面板持续在线。
- 中心面板只保存管理配置和操作记录。
- 原有 Docker 容器继续在各服务器运行。
- 新中心面板只要拿到配置备份，就能重新接管各 Agent。

### 必须备份的数据

中心面板需要定期备份：

```text
data.json
templates/ 中自定义模板
配置导出文件
```

其中最关键的是：

```text
servers
users
containers
templates
```

`servers` 中必须包含：

```json
{
  "host": "192.168.1.10",
  "agent_port": 5001,
  "agent_token": "secret-token",
  "ssh_host": "192.168.1.10",
  "mount_roots": []
}
```

只要 `agent_token` 还在，新中心面板就可以继续调用原有 Agent。

### 备份方式

第一阶段使用文件备份，保持简单：

```text
data.json
config-backup-YYYYMMDD-HHMMSS.json
```

面板提供：

```text
GET  /api/config/export
POST /api/config/import
```

导出内容：

```json
{
  "version": 1,
  "exported_at": "2026-05-31T12:00:00",
  "servers": {},
  "users": {},
  "containers": {},
  "templates": []
}
```

导出选项：

```text
默认导出：不包含 agent_token
灾备导出：包含 agent_token，需要管理员二次确认
```

灾备导出文件必须限制权限：

```bash
chmod 600 config-backup-*.json
```

### 自动备份建议

中心面板本机定期生成灾备文件：

```text
backups/config/config-backup-YYYYMMDD-HHMMSS.json
```

保留策略：

```text
最近 7 天每日保留
最近 4 周每周保留
超过策略自动删除
```

备份文件可以额外复制到：

```text
另一台服务器
对象存储
管理员本地电脑
```

当前阶段不强制实现远程备份，至少先实现一键导出/导入。

### 快速恢复流程

中心服务器故障后，在新机器执行：

```bash
git clone <repo> dockerhub-manager
cd dockerhub-manager
pip3 install -r requirements.txt
python3 app.py
```

然后导入灾备配置：

```text
管理页面 -> 配置导入 -> 上传 config-backup.json
```

或命令行：

```bash
python3 tools/import_config.py config-backup.json
```

导入后系统执行：

```text
1. 校验配置版本
2. 写入 data.json
3. 遍历 servers
4. 调用每台服务器 /ping 和 /checks
5. 标记在线/离线/Token 错误
6. 从 Agent 拉取现有容器列表并和本地 containers 记录对账
```

### Agent 重新接管

Agent 不需要重新部署，只要满足：

```text
Agent 服务仍运行
Agent Token 未丢失
新中心面板能访问 Agent 端口
```

新中心面板即可继续管理：

```text
启动/停止容器
更新资源限制
查看日志
创建新容器
```

如果本地 `containers` 记录丢失，可从 Agent 查询：

```text
GET /containers
```

并重建基础记录：

```json
{
  "name": "user_alice",
  "server_id": "srv_001",
  "status": "running",
  "image": "...",
  "ssh_port": 32001
}
```

挂载详情可以从 Docker inspect 补充：

```bash
docker inspect <container>
```

### 中心面板自管理

作为中心面板的服务器也运行本机 Agent。该服务器故障时：

- 本机 Docker 容器可能不可用。
- 其他服务器上的容器不受影响。
- 新中心面板导入配置后，可以继续管理其他服务器。
- 原中心服务器恢复后，可作为普通服务器重新注册。

### 安全注意

灾备配置如果包含 `agent_token`，等同于拥有全部服务器管理权限。

必须做到：

- 导出时明确标识“包含敏感 Token”。
- 导出文件权限为 `600`。
- 不提交到 Git。
- 不放在公开目录。
- 支持一键轮换某台服务器的 Agent Token。

### 后续增强

第二阶段可以增加：

```text
双中心冷备：主中心每日推送配置到备用中心
配置加密：使用管理员口令加密导出文件
远程对象存储备份：S3 / MinIO
数据库化：PostgreSQL 承载多中心共享状态
```

## 前端页面更新

### 服务器管理

新增字段：

- Agent 地址。
- Agent 端口。
- Agent Token。
- SSH 显示地址。
- 挂载根目录列表。
- Agent 权限检查状态。

支持操作：

- 新增挂载根目录。
- 删除挂载根目录。
- 检查路径是否存在、可写、剩余空间。
- 查看 Agent 工作目录大小和 Docker 权限状态。

### 创建容器

新增字段：

- 登录用户名。
- SSH 公钥。
- CPU。
- 内存。
- PIDs。
- 是否允许 sudo。
- 多目录挂载选择。

默认值：

- CPU 默认服务器总核数的 1/8。
- 内存默认服务器总内存的 1/8。
- 挂载路径从服务器配置中选择。

### 容器管理

新增操作：

- 更新资源限制。
- 重建容器并保留挂载数据。
- 查看容器挂载清单。
- 查看 SSH 登录命令。

## 实施顺序

1. 改 `deploy.sh`
   - 支持 `user@host` 和 SSH config 别名。
   - 移除数据盘参数。
   - 部署到 `/opt/.dockerhub-agent`。
   - 非 root 部署命令使用 `sudo`。

2. 改 Agent 基础能力
   - `/sysinfo` 返回 CPU、内存、Docker 状态。
   - `/checks` 返回权限、目录、Docker 风险检查。
   - 移除 `DATA_PATH` 作为部署参数。

3. 改服务器配置
   - 保存 `agent_token`。
   - 保存 `ssh_host`。
   - 保存多目录 `mount_roots`。
   - 前端支持挂载根目录管理。

4. 改创建容器
   - 前端传 `login_user`、`ssh_public_key`、资源限制、多挂载。
   - 面板真正调用 Agent。
   - Agent 校验挂载路径和资源限制。
   - SSH 命令不再写死 root。

5. 改容器资源更新
   - 增加 `PATCH /containers/<name>/resources`。
   - 前端增加资源更新表单。

6. 改重建流程
   - 镜像、端口、挂载、登录用户变化时走临时容器重建。
   - 复用挂载目录。
   - 失败回滚。

7. 改共享配置
   - 中心面板保存所有配置。
   - 多管理员账号共享同一中心面板。
   - 增加配置导入/导出。

8. 改故障恢复
   - 增加配置灾备导出。
   - 支持导入后自动检查 Agent 连通性。
   - 支持从 Agent 容器列表重建本地容器记录。
   - 文档化新中心面板快速恢复流程。

## 当前阶段不做

- 不引入 Kubernetes。
- 不引入消息队列。
- 不做多中心面板实时同步。
- 不默认开放 privileged、docker.sock、root SSH。
- 不承诺保留容器系统层内未挂载目录数据。
- 不把包含 Agent Token 的灾备文件提交到 Git。
