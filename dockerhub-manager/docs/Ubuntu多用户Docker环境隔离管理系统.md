# 方案设计：DockerHub 多用户管理系统

---
## 详细方案设计

### 一、系统整体结构

系统分为两大部分：**中心管理面板**（运行在某台管理机或其中一台服务器上）和**各服务器 Agent**（轻量守护进程）。

**目录结构建议：**

```
dockerhub-manager/
├── app.py                  # Flask 主程序（面板）
├── templates/              # Jinja2 模板（或 render_template_string）
├── config.json             # 多服务器配置
├── db.sqlite               # 用户、容器、分配记录
├── agent/
│   └── agent.py            # 部署到每台服务器的守护脚本
├── deploy.sh               # 一键部署 Agent 脚本
└── requirements.txt
```

---

### 二、三类角色权限设计

| 角色 | 权限 |
|------|------|
| **管理员** | 添加/删除分配员账号；注册服务器；查看所有容器；强制回收 |
| **分配员** | 为指定使用者创建容器（选服务器、选镜像、配挂载）；生成登录命令；停止/重启容器 |
| **使用者** | 凭分配员给出的 SSH 命令直连，无需账号登录平台 |

---

### 三、核心功能实现思路

#### 3.1 直连 Docker 的机制

用户拿到的命令形如：
```bash
ssh -p 32001 -i ~/.ssh/id_rsa root@192.168.1.10
```

实现方式：每个容器启动时开放 SSH 服务，并将**容器内 22 端口**随机映射到宿主机的高位端口（如 32001）。平台记录映射关系，生成命令返回给分配员。

```python
# 启动容器示例（agent 侧执行）
cmd = [
    "docker", "run", "-d",
    "--name", f"user_{username}",
    "-p", f"{host_port}:22",         # SSH 直连
    "-v", f"{host_data_path}:/workspace",  # 挂载数据盘
    "--restart", "unless-stopped",
    image_name,
    "/usr/sbin/sshd", "-D"
]
```

#### 3.2 快速多服务器部署（Agent）

`deploy.sh` 通过 SSH 将 agent.py 推送到目标服务器并注册为 systemd 服务：

```bash
#!/bin/bash
SERVER=$1
scp agent/agent.py root@$SERVER:/opt/dockerhub-agent/agent.py
ssh root@$SERVER "pip3 install flask --break-system-packages && \
  systemctl enable --now dockerhub-agent"
```

Agent 是一个轻量 Flask 服务（监听本地端口），接收面板下发的指令（创建容器、拉取镜像、查询状态），用 `subprocess` 调用 docker 命令。**面板与 Agent 之间用 API token 鉴权**。

#### 3.3 镜像管理与挂载配置

分配容器时，面板提供：
- **已下载镜像**：调 `docker images` API 列出
- **未下载镜像**：输入 registry 地址，Agent 执行 `docker pull`（支持进度 SSE 推流回面板）
- **挂载路径配置**：不同服务器数据盘路径（宿主机侧）可在服务器配置里单独设置，容器内挂载点统一为 `/workspace`、`/data` 等固定名称

#### 3.4 Web 面板功能页面

| 页面 | 功能 |
|------|------|
| 登录页 | Session + bcrypt 密码认证 |
| 服务器总览 | 各服务器状态、容器数量、资源用量 |
| 容器管理 | 列表、启停、删除、查看日志（SSE 流式） |
| 创建容器 | 选服务器 → 选镜像 → 配置挂载 → 生成命令 |
| 用户管理 | 管理员管理分配员账号 |
| 镜像库 | 各服务器已有镜像；拉取新镜像（进度条） |

---

### 四、额外建议功能

**容器资源限制**：创建时可选 CPU 核数、内存上限（`--cpus`, `--memory`），防止单用户占满服务器。

**容器模板**：管理员预设常用镜像+挂载组合存为模板，分配员一键套用。

**操作审计日志**：所有创建/删除/分配操作写入日志，管理员可查。

**SSH 密钥注入**：创建容器时支持上传使用者公钥，自动写入容器 `authorized_keys`，免密登录更安全。

**容器到期回收**：可设置容器有效期，到期自动停止并提醒。

---

### 五、技术栈确认

完全符合你给出的 Flask 技术栈，关键依赖：

```python
# requirements.txt
flask
flask-login
bcrypt
paramiko        # SSH/Agent 通信备用
requests        # 面板调用 Agent API
```

Agent 与面板全用 Python + subprocess 控制 Docker，无需额外中间件，部署极简。

---

# 框架

框架包含以下 6 个文件，开箱即用：

**`app.py`** — Flask 主程序，含完整的登录认证、角色权限装饰器、所有 REST API（服务器/容器/用户 的 CRUD）、SSE 日志流。数据暂存 `data.json`，后续可替换为 SQLite/PostgreSQL。

**`templates/login.html`** — 工业深色风格登录页，背景网格 + 动态光晕 + 角标装饰。

**`templates/dashboard.html`** — 完整 SPA 壳，包含：
- 顶栏（系统状态、实时时钟、用户信息）
- 侧边栏（按角色隐藏管理员菜单）
- 5 个功能面板：仪表盘 / 容器管理 / 服务器管理 / 用户管理 / 审计日志
- 3 个 Modal：分配容器（含 SSH 命令生成复制）/ 添加服务器 / 添加用户
- Toast 通知 + 表格搜索过滤

**`agent/agent.py`** — 部署到各服务器的守护进程，含 Token 鉴权、镜像拉取（SSE 进度）、容器创建/启停/日志流、系统信息接口。

**`deploy.sh`** — 4 步自动化部署：上传脚本 → 安装依赖 → 注册 systemd → 连通验证。

**启动方式：**
```bash
pip3 install -r requirements.txt
python3 app.py
# 访问 http://localhost:5000  账号: admin / admin123
```



# 部署

## `bash deploy.sh` 在哪执行？

**在你的管理机（本机）上执行**，不是在服务器上。

```
你的电脑（管理机）                    目标 Ubuntu 服务器
      │                                      │
      │  bash deploy.sh 192.168.1.10 ...     │
      │ ─────────────────────────────────► │
      │         通过 SSH 自动操作             │
      │  上传 agent.py                       │
      │  安装 pip 依赖                       │
      │  注册 systemd 服务                   │
      │  启动 Agent 进程                     │
```

前提是你的管理机能 `ssh root@192.168.1.10` 免密登录目标服务器（即已配置 SSH 公钥）。

---

## `192.168.1.10` 是什么？

就是**目标 Ubuntu 服务器的 IP 地址**，换成你实际的服务器 IP 即可。比如你有 3 台服务器就跑 3 次：

```bash
bash deploy.sh 192.168.1.10 my-token /mnt/data
bash deploy.sh 192.168.1.11 my-token /mnt/ssd
bash deploy.sh 10.0.0.5    my-token /data
```

---

## Token 是什么，怎么来的？

Token 是**你自己随意起的一串密码字符串**，用于管理面板和 Agent 之间的身份验证，防止其他人随意调用 Agent 接口。

```
管理面板请求 Agent 时会带上：
  Header: X-Agent-Token: my-secret-token

Agent 收到后校验这个 token，不匹配就拒绝。
```

你可以随便起，比如用命令生成一个：
```bash
openssl rand -hex 32
# 输出：a3f8c2d19e4b7a056f1c3d8e2b9a4f71c0d5e8b2a7f3c9d1e4b6a2f8c3d0e7b1
```

或者就写 `MyServer2024!Token` 这种，只要面板和 Agent 配置一致就行。**在管理面板添加服务器时填写同一个 token**，面板才能成功调用该服务器的 Agent。

---

## `/mnt/data` 是什么？

是**那台 Ubuntu 服务器上的数据盘挂载路径**，也就是你想让用户在 Docker 里访问的共享目录。

```
Ubuntu 服务器磁盘结构示例：

  /          ← 系统盘（SSD，100GB）
  /mnt/data  ← 数据盘（HDD，4TB，存放用户数据）
  /mnt/ssd   ← 另一块 NVMe（某些服务器可能不同）
```

脚本会把这个路径告诉 Agent，之后每次创建容器时，Agent 会执行：

```bash
docker run -v /mnt/data:/workspace ...
#              ↑                ↑
#         服务器上的路径    容器内统一叫 /workspace
```

**不同服务器数据盘路径可以不一样**（有的叫 `/mnt/data`，有的叫 `/data`，有的叫 `/mnt/ssd`），但容器内部挂载点统一都是 `/workspace`，所以使用者不用关心底层差异。

---

## 完整流程一句话总结

```bash
bash deploy.sh  192.168.1.10      my-secret-token    /mnt/data
#    脚本名      目标服务器IP       你定的鉴权密码      服务器数据盘路径
```

**在管理机上跑一次，自动把 Agent 装到那台服务器上，之后面板就能管它了。**



# 问题

## 登录用户唯一

192.168.1.10输入时不指定用户名吗?脚本怎么知道登进哪个用户的

脚本里写死了 `root`脚本里是这样写的

```bash
ssh "root@${SERVER}"   # ← 硬编码了 root
scp agent/agent.py "root@${SERVER}:..."
```

所以你传入 `192.168.1.10`，脚本自动拼成 `root@192.168.1.10`。

---

#### 为什么默认用 root？

部署 Agent 需要做几件需要 root 权限的事：
- 安装软件包（`apt-get install`）
- 写入 systemd 服务文件（`/etc/systemd/system/`）
- 操作 Docker

所以默认直接用 root 最省事。大多数云服务器（阿里云、腾讯云）默认也是 root 登录。

---

### 如果你的服务器不是 root 登录怎么办

比如用户名是 `ubuntu`（AWS 默认）或 `admin`，需要修改脚本，把 `SERVER` 改成支持传入 `用户名@IP` 的格式。

打开 `deploy.sh`，把第一个参数的用法改一下：

```bash
# 改前（写死 root）
ssh "root@${SERVER}" "..."
scp agent/agent.py "root@${SERVER}:..."

# 改后（支持自定义用户）
SSH_TARGET="${1:-}"   # 接收 ubuntu@192.168.1.10 这种格式
ssh "${SSH_TARGET}" "..."
scp agent/agent.py "${SSH_TARGET}:..."
```

然后这样调用：

```bash
bash deploy.sh ubuntu@192.168.1.10 my-token /mnt/data
bash deploy.sh admin@10.0.0.5      my-token /data
bash deploy.sh root@192.168.1.11   my-token /mnt/ssd
```

非 root 用户还需要加 `sudo`，脚本里的远程命令也要相应加上：

```bash
ssh "${SSH_TARGET}" "sudo apt-get install -y ..."
ssh "${SSH_TARGET}" "sudo systemctl enable --now dockerhub-agent"
```

---

### 建议的修改版本

```bash
SERVER="${1:-}"          # 支持 root@ip 或 ubuntu@ip 两种写法
                         # 不带用户名默认由 SSH 配置决定（~/.ssh/config）
```

这样如果你在 `~/.ssh/config` 里配了别名：

```
Host gpu01
    HostName 192.168.1.10
    User ubuntu
    IdentityFile ~/.ssh/id_rsa
```

就可以直接：
```bash
bash deploy.sh gpu01 my-token /mnt/data
```

SSH 自己知道用哪个用户和密钥。这是最灵活的方式。



`app.py` 里调用 Agent 的地方也硬编码了主机地址，没有带用户名的问题（因为面板调 Agent 是 HTTP 请求，不是 SSH），但有几处需要对应调整。

---

### app.py 目前的问题

**问题1：Agent 调用是空壳**，创建容器那里有一行注释：

```python
# TODO: 调用 Agent API 实际创建
return jsonify({"ok": True, "id": cid, "ssh_cmd": ssh_cmd})
```

现在只是往 `data.json` 写记录，根本没有真的去服务器上跑 `docker run`。

**问题2：面板调 Agent 时没带 Token**，Agent 有鉴权，但面板发请求时没有加 `X-Agent-Token` 头。

**问题3：SSH 命令里主机地址是硬编码拼接的**：

```python
host = server.get("host", "server-host")
ssh_cmd = f"ssh -p {ssh_port} root@{host}"
#                                ↑ 容器内用户写死了 root
```

---

### 需要改的地方

#### 1. 加一个调用 Agent 的工具函数

在 `app.py` 顶部的 import 下面加：

```python
import requests   # 需要 pip install requests

def call_agent(server: dict, path: str, method="GET", body=None):
    """向指定服务器的 Agent 发 HTTP 请求"""
    host       = server.get("host")
    port       = server.get("agent_port", 5001)
    token      = server.get("agent_token", "")   # ← 每台服务器单独存 token
    url        = f"http://{host}:{port}{path}"
    headers    = {"X-Agent-Token": token, "Content-Type": "application/json"}
    try:
        if method == "POST":
            r = requests.post(url, json=body, headers=headers, timeout=30)
        elif method == "DELETE":
            r = requests.delete(url, headers=headers, timeout=10)
        else:
            r = requests.get(url, headers=headers, timeout=10)
        return r.json()
    except Exception as e:
        return {"ok": False, "error": str(e)}
```

#### 2. 注册服务器时保存 token

`api_add_server` 里加一个字段：

```python
data["servers"][sid] = {
    "name":        body.get("name", sid),
    "host":        body.get("host", ""),
    "port":        body.get("port", 22),
    "agent_port":  body.get("agent_port", 5001),
    "agent_token": body.get("agent_token", ""),   # ← 新增
    "data_path":   body.get("data_path", "/data"),
    "added_at":    datetime.now().isoformat()
}
```

#### 3. 创建容器时真正调 Agent

把 `api_create_container` 里的 TODO 替换掉：

```python
# 之前
# TODO: 调用 Agent API 实际创建
return jsonify({"ok": True, "id": cid, "ssh_cmd": ssh_cmd})

# 改后
server_info = data["servers"].get(body.get("server_id", ""), {})
agent_result = call_agent(
    server_info,
    "/containers/create",
    method="POST",
    body={
        "name":     data["containers"][cid]["name"],
        "image":    data["containers"][cid]["image"],
        "ssh_port": ssh_port,
        "cpu":      body.get("cpu_limit", "2"),
        "memory":   body.get("mem_limit", "4g"),
        "mounts":   body.get("mounts", [
            f"{server_info.get('data_path', '/mnt/data')}:/workspace"
        ])
    }
)
if not agent_result.get("ok"):
    # Agent 失败时回滚本地记录
    data["containers"].pop(cid, None)
    save_data(data)
    return jsonify({"error": agent_result.get("error", "Agent 调用失败")}), 500

save_data(data)
return jsonify({"ok": True, "id": cid, "ssh_cmd": ssh_cmd})
```

#### 4. requirements.txt 加上 requests

```
flask>=3.0.0
requests>=2.31.0
```

---

### 不需要改的地方

面板调 Agent 是 HTTP，没有用户名的概念，所以 `deploy.sh` 里 SSH 用户名的问题和 `app.py` 完全独立，互不影响。`deploy.sh` 是一次性部署工具，`app.py` 是日常运行时通信，走的是两套完全不同的通道。