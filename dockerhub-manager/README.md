# DockerHub Manager

多用户 Docker 环境分配与管理系统。推荐选择一台服务器作为中心管理面板，对外提供 Web 页面；所有 Docker 服务器，包括中心服务器自己，都通过 Agent 接入管理。

## 部署状态

当前版本可以开始受控部署。

已于 `2026-05-31` 在 Ubuntu 22.04 远程服务器完成端到端验证：

```text
Agent 版本：0.3.0
Docker 版本：29.4.0
Agent systemd 部署与重启：通过
Docker 权限检查：通过
隐藏工作目录检查：通过，测试时占用 0.02 MB
挂载根目录检查：通过
OpenSSH 容器创建：通过
TCP 32000-32999 端口映射：通过
SSH 公钥登录：通过
/workspace 挂载目录写入：通过
测试容器自动清理：通过
```

推荐先部署到内网可信环境，再逐台接入正式服务器。Agent 部署脚本不会修改现有 Docker daemon 配置，也不会停止、删除或重启已有业务容器。

当前中心面板使用 `data.json` 保存配置，适合小规模受控部署。面板可以供多个管理员共享使用，但不要直接将 Flask 服务裸露到公网。需要公网访问时，应在前方配置 HTTPS 反向代理、来源限制和独立认证策略。

## 架构

```text
管理员浏览器
    |
    | Web
    v
中心管理服务器
    |-- app.py                  # 管理面板
    |-- data.json               # 用户、服务器、容器配置
    |-- agent/agent.py          # 本机 Agent，可管理本机 Docker
    |
    | Agent API + X-Agent-Token
    v
其他 Docker 服务器
    |-- /opt/.dockerhub-agent/agent.py
    |-- Docker Engine
```

Agent 独立运行。中心面板故障时，已有容器仍继续运行；新中心面板导入配置后可以重新接管各服务器。

## 部署中心面板

先选择一台局域网内服务器作为中心管理服务器。其他管理员的浏览器将访问这台服务器，而不是各自在本机运行面板。

在管理员电脑上连接中心服务器：

```bash
ssh ubuntu@192.168.1.20
```

在中心服务器上准备项目代码。可以使用内部 Git 仓库拉取，也可以将当前目录上传到服务器。进入实际项目目录后启动：

```bash
cd /opt/dockerhub-manager
pip3 install -r requirements.txt
export SECRET_KEY="$(openssl rand -hex 32)"
export ADMIN_PASSWORD="$(openssl rand -base64 24)"
echo "首次登录账号：admin / ${ADMIN_PASSWORD}"
PANEL_PORT=5000 python3 app.py
```

首次验证可以直接以前台方式运行。需要长期运行时，应使用 systemd 或现有运维平台托管该命令，并持久化保存同一个 `SECRET_KEY` 和 `ADMIN_PASSWORD`，不要在每次重启时重新生成。

在中心服务器的 1Panel、系统防火墙或云安全组中放行：

```text
来源：局域网管理员网段，例如 192.168.1.0/24
协议：TCP
端口：5000
用途：中心管理网页
```

局域网内其他管理员电脑访问：

```text
http://192.168.1.20:5000
```

只有在中心服务器本机打开浏览器时才使用 `http://localhost:5000`。

未设置 `ADMIN_PASSWORD` 时的开发环境默认账号：

```text
admin / admin123
```

正式部署不要使用默认密码。当前页面尚未提供密码修改入口，应在首次启动前设置 `ADMIN_PASSWORD`。如果已经生成 `data.json`，其中的密码配置优先，环境变量不会覆盖已有配置。

正式部署前检查：

```text
首次启动前设置随机 ADMIN_PASSWORD
设置随机 SECRET_KEY
为每台服务器设置独立 Agent Token
在 1Panel、系统防火墙或云安全组中限制 Agent 端口来源
按需放行容器 SSH 端口 TCP 32000-32999
导出一份包含 Token 的灾备配置并限制文件权限
```

## 部署 Agent

部署脚本在管理机或中心服务器上执行。支持 `user@host`、`root@host` 或 SSH config 别名。

```bash
chmod +x deploy.sh
bash deploy.sh ubuntu@192.168.1.10 my-secret-token 5001
```

参数：

```text
第 1 参数：SSH 目标，例如 ubuntu@192.168.1.10
第 2 参数：Agent Token
第 3 参数：Agent 端口，默认 5001
```

部署 Agent 前，在目标 Docker 服务器的 1Panel、系统防火墙或云安全组中确认：

```text
TCP 5001：仅允许中心管理服务器 IP 访问，用于 Agent API
TCP 32000-32999：按实际使用者来源网段放行，用于容器 SSH
```

其中 `5001` 可以按需替换为部署时指定的 Agent 端口。`deploy.sh` 支持为已启用的 UFW 或 firewalld 添加 Agent API 规则，但不会自动修改 1Panel、云安全组或容器 SSH 端口范围。

部署后 Agent 位于隐藏目录：

```text
/opt/.dockerhub-agent
```

systemd 服务：

```bash
sudo systemctl status dockerhub-agent
sudo journalctl -u dockerhub-agent -f
```

手动验证：

```bash
curl --noproxy '*' http://192.168.1.10:5001/ping
```

注意：数据盘路径不再在部署时指定，而是在管理面板的服务器配置中维护。

如果管理机设置了 `http_proxy` / `https_proxy`，访问内网 Agent 时必须绕过代理。项目脚本和管理面板已经默认直连 Agent，不使用环境代理。手工测试时保留 `--noproxy '*'`。

安全边界：

```text
deploy.sh 不会安装或覆盖 Docker
deploy.sh 不会修改 Docker daemon 配置
deploy.sh 不会停止、删除、重启已有业务容器
deploy.sh 只部署 /opt/.dockerhub-agent 和 dockerhub-agent systemd 服务
```

如果远程服务器没有 Docker，脚本会直接失败并提示人工处理。这样避免破坏已有 NVIDIA Docker、Docker CE、自定义 registry、daemon.json、运行中容器等原始环境。

## 一键验证远程服务器

不想逐条敲 `curl` 时，使用验证脚本。

### 只检查 Agent，不创建容器

```bash
cd /home/yuchen/scripts/dockerhub-manager
bash scripts/check_agent.sh 192.168.1.10 my-secret-token /tmp/dockerhub-test 5001
```

该命令会检查：

```text
/ping
/sysinfo
/checks
Docker 权限
挂载根目录状态
```

### 部署并检查

```bash
bash scripts/smoke_remote.sh \
  --ssh ubuntu@192.168.1.10 \
  --host 192.168.1.10 \
  --token my-secret-token \
  --deploy
```

该命令会：

```text
部署/更新 Agent
创建测试挂载目录 /tmp/dockerhub-test
检查 Agent
不创建容器
```

如果需要脚本同时新增 Agent 端口允许规则：

```bash
bash scripts/smoke_remote.sh \
  --ssh ubuntu@192.168.1.10 \
  --host 192.168.1.10 \
  --token my-secret-token \
  --deploy \
  --open-firewall \
  --allow-from 192.168.1.20
```

其中 `192.168.1.20` 是中心管理服务器 IP。脚本只会新增：

```text
中心管理服务器 IP -> TCP 5001
```

防火墙安全边界：

```text
必须显式传入 --open-firewall
必须显式传入 --allow-from
拒绝放行 any、0.0.0.0/0、::/0
只新增规则，不删除已有规则
不会自动启用 UFW
不会自动启动 firewalld
不会修改默认策略
不会修改 SSH 或 1Panel 端口
```

Ubuntu / Debian 上优先使用 UFW，CentOS / RedHat 上使用 firewalld。如果服务器还有云平台安全组，脚本无法修改云安全组，仍需要在云平台放行。

### 完整端到端测试

确认可以创建测试容器时再执行：

```bash
bash scripts/smoke_remote.sh \
  --ssh ubuntu@192.168.1.10 \
  --host 192.168.1.10 \
  --token my-secret-token \
  --deploy \
  --create-container
```

该命令会：

```text
部署/更新 Agent
检查 Agent
创建测试容器
最多等待 120 秒，直到容器 SSH 就绪
用 SSH 登录测试容器
在 /workspace 写入 smoke.txt
自动删除测试容器
```

每次完整测试会使用独立的宿主机测试子目录，避免旧测试目录权限影响结果。脚本不会自动删除宿主机测试数据目录。

如果测试容器启动失败，脚本会自动输出容器状态和最近 80 行日志，再执行清理。等待期间每 20 秒也会输出一次容器状态、端口映射、sshd 进程、容器内 SSH 端口监听、公钥状态、TCP 连通性和最近一次 SSH 错误，便于区分容器启动、网络和公钥认证问题。

默认测试参数：

```text
测试挂载目录：/tmp/dockerhub-test
测试镜像：lscr.io/linuxserver/openssh-server:latest
测试登录用户：smoketest
测试 SSH 端口：32091
SSH 公钥：~/.ssh/id_rsa.pub
```

默认镜像已内置 OpenSSH 服务，容器内部监听 `2222` 端口，不再在首次启动时执行 `apt install openssh-server`。对外仍使用宿主机约定范围 `32000-32999`。Agent 只会为新建的数据子目录设置 `PUID=1000`、`PGID=1000` 和可写权限，不会批量修改已有数据目录。显式删除容器时会清理该容器专属的 SSH 配置卷，但保留宿主机挂载的数据目录。

完整测试会自动优先选择：

```text
~/.ssh/id_ed25519.pub
~/.ssh/id_rsa.pub
```

如果两者都不存在，先生成一把测试公钥：

```bash
ssh-keygen -t ed25519
```

可自定义：

```bash
bash scripts/smoke_remote.sh \
  --host 192.168.1.10 \
  --token my-secret-token \
  --mount-root /mnt/data/dockerhub-test \
  --ssh-port 32100 \
  --image lscr.io/linuxserver/openssh-server:latest \
  --create-container
```

## 添加服务器

进入管理面板后，在“服务器”页面添加节点：

```text
名称：GPU-01
服务器 ID：srv_gpu01
主机 IP / 域名：192.168.1.10
SSH 显示地址：192.168.1.10
Agent 端口：5001
Agent Token：部署 Agent 时使用的 Token
```

挂载根目录每行一个：

```text
用户数据|/mnt/data/users|/workspace|rw
公共数据|/data/datasets|/datasets|ro
```

格式：

```text
名称|宿主机路径|默认容器路径|ro/rw
```

创建容器时只能挂载这些根目录下的路径，Agent 会做权限和路径校验。

## 创建容器

点击“分配容器”，填写：

```text
容器名称
分配给
容器登录用户
目标服务器
镜像
SSH 公钥
CPU
内存
PIDs 限制
SSH 端口
挂载目录
```

资源默认值来自 Agent 上报的真实资源：

```text
默认 CPU = 服务器 CPU 总核数的 1/8
默认内存 = 服务器内存总量的 1/8
```

前端可修改，Agent 仍会校验格式和合法性。

挂载目录每行一个：

```text
/mnt/data/users/alice|/workspace|rw
/data/datasets/imagenet|/datasets/imagenet|ro
```

格式：

```text
宿主机路径|容器内路径|ro/rw
```

SSH 登录命令生成示例：

```bash
ssh -p 32001 alice@192.168.1.10
```

容器 SSH 端口统一约定为：

```text
TCP 32000-32999
```

中心服务器和各 Docker 服务器应在 1Panel、系统防火墙或云安全组中按可信来源网段放行该范围。例如：

```text
协议：TCP
端口：32000-32999
来源：192.168.2.0/24
```

面板和 Agent 都会拒绝范围外的容器 SSH 端口。

默认策略：

```text
禁用 root SSH 登录
禁用密码登录
使用 SSH 公钥登录
默认不授予 sudo
```

## 更新容器资源

容器列表中点击“资源”，可以更新：

```text
CPU
内存
PIDs 限制
```

这类更新使用 `docker update`，不会删除容器，也不会影响挂载目录数据。

镜像、端口、挂载路径、登录用户等配置不适合原地更新，后续应走“重建容器 + 复用挂载目录 + 失败回滚”流程。只有挂载目录或 Docker volume 中的数据承诺保留，容器系统层内未挂载目录的修改不保证保留。

## Agent 检查接口

Agent 提供：

```text
GET  /ping
GET  /sysinfo
POST /checks
```

面板会调用 `/checks` 检查：

```text
Docker 是否可用
Agent 工作目录是否隐藏
工作目录大小
挂载根目录是否存在
挂载根目录是否可写
CPU / 内存信息
```

## 配置导入导出

中心面板提供配置灾备接口：

```text
GET  /api/config/export
GET  /api/config/export?include_tokens=1
POST /api/config/import
```

普通导出不包含 Agent Token。灾备导出包含 Token，需要妥善保存：

```bash
chmod 600 config-backup.json
```

不要把包含 Token 的配置提交到 Git。

## 中心面板故障恢复

如果中心管理服务器故障：

1. 在新机器拉取项目。
2. 安装依赖并启动面板。
3. 导入之前的灾备配置。
4. 面板会重新连接各服务器 Agent。
5. 原服务器上的容器继续运行，无需重建。

恢复命令示例：

```bash
git clone <repo> dockerhub-manager
cd dockerhub-manager
pip3 install -r requirements.txt
python3 app.py
```

导入包含 Token 的配置后，只要这些条件成立，就能重新接管：

```text
Agent 服务仍运行
Agent Token 未改变
新中心面板能访问 Agent 端口
```

## 环境变量

### 管理面板

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `SECRET_KEY` | Flask session 密钥 | `change-me-in-production-please` |
| `ADMIN_PASSWORD` | 首次生成 `data.json` 前使用的管理员密码 | `admin123` |
| `PANEL_PORT` | 中心面板监听端口 | `5000` |
| `DEBUG` | Flask 调试模式，仅开发时设置为 `1` | `0` |

### Agent

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `AGENT_TOKEN` | Agent 鉴权 Token | `changeme-agent-token` |
| `AGENT_PORT` | Agent 监听端口 | `5001` |

## 目录结构

```text
dockerhub-manager/
├── app.py
├── requirements.txt
├── deploy.sh
├── scripts/
│   ├── check_agent.sh
│   └── smoke_remote.sh
├── data.json
├── templates/
│   ├── login.html
│   └── dashboard.html
├── agent/
│   └── agent.py
└── docs/
    ├── Ubuntu多用户Docker环境隔离管理系统.md
    └── 中心管理面板与多服务器Agent更新计划.md
```

## 当前边界

当前阶段不做：

```text
Kubernetes
多中心实时同步
消息队列
默认 privileged 容器
默认挂载 docker.sock
默认 root SSH
```
