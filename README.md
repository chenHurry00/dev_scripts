# scripts

个人工具脚本集合。

---

## 目录

| 路径 | 类型 | 简介 |
|---|---|---|
| [Rsync/](#rsync) | Web 应用 | rsync 仓库同步管理 |
| [Invoice/](#invoice) | Web 应用 | 发票报销管理系统 |
| [Audit/](#audit) | 模块 | 服务器审计系统（审计节点 + 中控台） |
| [gpu_monitor.py](#gpu_monitorpy) | Web 应用 | GPU/CPU/内存实时监控 |
| [dkr.sh](#dkrsh) | CLI 工具 | Docker 容器管理 TUI |
| [conda_menu_helper.sh](#conda_menu_helpersh) | Shell 工具 | conda 环境快捷菜单 |

---

## Rsync

**路径：** `Rsync/`  
**端口：** 7788

基于 rsync 的代码仓库同步 Web 管理工具，支持多服务器、多仓库、实时日志流、定向上传/回传、`.gitignore` 排除规则。

```bash
cd Rsync
pip install flask
python app.py
# http://localhost:7788
```

---

## Invoice

**路径：** `Invoice/`  
**端口：** 5000

轻量级发票报销管理系统，支持多角色（填报人 / 材料报账 / 资产报账 / 管理员）、按单价自动分类、验收流程、图片压缩为 WebP。

```bash
cd Invoice
pip install -r requirements.txt
python app.py
# http://localhost:5000
```

也可用 systemd 服务运行：

```bash
sudo bash install_service.sh
```

---

## Audit

**路径：** `Audit/`

服务器审计模块，包含两个子组件：

### audit_system

**路径：** `Audit/audit_system/`  
**端口：** 由 `manage.sh` 或 `start.sh` 指定

部署在被监控服务器上，记录登录、命令、文件操作，五级风险分级，日志只读不可篡改，提供 REST API 供中控台接入。

```bash
cd Audit/audit_system
bash manage.sh          # 交互式管理菜单（推荐）
# 或直接启动
bash start.sh
```

安装为系统服务：

```bash
sudo bash install_service.sh
```

### control_center

**路径：** `Audit/control_center/`  
**端口：** 8000

统一管理多台部署了 audit_system 的服务器，自动 Token 刷新，跨服务器日志查询与告警汇总。

```bash
cd Audit/control_center
pip install -r requirements.txt
python app.py
# http://localhost:8000
```

---

## gpu_monitor.py

**路径：** `gpu_monitor.py`  
**端口：** 8082

GPU 服务器实时监控，展示 CPU、内存、各卡显存与利用率，支持多 GPU。

```bash
pip install flask psutil
python gpu_monitor.py
# http://localhost:8082
```

---

## dkr.sh

**路径：** `dkr.sh`

终端 Docker 容器管理工具，提供美观的交互界面，快速查看、启停、进入容器。

```bash
bash dkr.sh
```

---

## conda_menu_helper.sh

**路径：** `conda_menu_helper.sh`

为 shell 添加 conda 环境快捷切换菜单，安装后在终端输入快捷命令即可列出并激活环境。

```bash
bash conda_menu_helper.sh --install   # 写入 ~/.bashrc
source ~/.bashrc
```
