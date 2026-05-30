# DockerHub Manager — 框架基座

多用户 Docker 环境分配与管理系统，支持多服务器、可视化面板、SSH 直连分发。

---

## 快速启动（管理面板）

```bash
# 安装依赖
pip3 install -r requirements.txt

# 启动面板（默认 5000 端口）
python3 app.py
```

访问 http://localhost:5000 ，默认账号：`admin` / `admin123`

---

## 部署 Agent 到服务器

```bash
# 赋权
chmod +x deploy.sh

# 部署（替换为实际 IP 和 Token）
bash deploy.sh 192.168.1.10 my-secret-token /mnt/data
```

手动启动 Agent（无 systemd 时）：
```bash
python3 agent/agent.py --port 5001 --token my-secret-token
```

---

## 目录结构

```
dockerhub-manager/
├── app.py               # Flask 主程序（管理面板）
├── requirements.txt     # Python 依赖
├── deploy.sh            # Agent 一键部署脚本
├── data.json            # 运行时数据（自动生成）
├── templates/
│   ├── login.html       # 登录页
│   └── dashboard.html   # 主面板（SPA）
└── agent/
    └── agent.py         # 服务器端守护进程
```

---

## 角色说明

| 角色 | 功能 |
|------|------|
| `admin` | 全局管理：添加服务器、管理用户、查看所有容器 |
| `allocator` | 分配容器：创建/停止/删除容器，生成 SSH 命令 |

---

## 默认账号

- 用户名：`admin`
- 密码：`admin123`

**生产环境请立即修改 `app.py` 中的 `secret_key` 及默认密码，并对接数据库。**

---

## 环境变量

### 管理面板
| 变量 | 说明 | 默认值 |
|------|------|--------|
| `SECRET_KEY` | Flask session 密钥 | `change-me-in-production-please` |

### Agent
| 变量 | 说明 | 默认值 |
|------|------|--------|
| `AGENT_TOKEN` | 鉴权 Token | `changeme-agent-token` |
| `AGENT_PORT`  | 监听端口 | `5001` |
| `DATA_PATH`   | 数据盘挂载路径 | `/mnt/data` |
