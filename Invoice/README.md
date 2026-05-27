# 发票报销系统

轻量级Flask单文件发票报销管理系统，支持多角色协作、自动分类、验收流程。

**📦 快速部署：** 查看 [DEPLOY.md](DEPLOY.md) 了解如何打包和部署到其他服务器

## 功能特性

- **用户注册**：外部用户可自助注册，默认填报人角色
- **多角色权限**：填报人、材料报账、资产报账、管理员
- **自动分类**：根据单价自动分类（材料/低值品/资产）
- **验收流程**：资产/低值品需验收后分流报销
- **附件管理**：图片自动压缩为WebP，节省空间
- **操作追溯**：完整的历史记录
- **运行日志**：自动记录关键操作，保留最近10MB
- **空间监控**：实时查看数据库、附件、备份占用
- **数据导出**：支持CSV导出和完整备份

## 业务流程

### 分类规则
- **单价 < 500元**：材料 → 直接进入材料报账
- **500元 ≤ 单价 < 1000元**：低值品 → 资产报账验收 → 材料报账
- **单价 ≥ 1000元**：资产 → 资产报账验收 → 资产报账

### 状态流转
```
草稿 draft
  ↓ 提交
待验收 pending_check (单价≥500)
  ↓ 资产报账验收
  ├→ 待材料报销 pending_material (低值品)
  └→ 待资产报销 pending_asset (资产)

待材料报销 pending_material (材料或已验收低值品)
  ↓ 材料报账确认
已报销 reimbursed

待资产报销 pending_asset
  ↓ 资产报账确认
已报销 reimbursed
```

## 快速开始

### 1. 安装依赖

```bash
cd /home/yuchen/scripts/Invoice
pip3 install -r requirements.txt
```

### 2. 配置管理员密码

编辑 `app.py` 顶部：

```python
ADMIN_USERNAME = "admin"
ADMIN_PASSWORD = "your_strong_password"  # 修改为强密码
ADMIN_INITIAL_PASSWORD = "123456"  # 管理员添加用户时的初始密码
```

**说明：**
- `ADMIN_PASSWORD`：管理员登录密码，每次启动自动更新
- `ADMIN_INITIAL_PASSWORD`：管理员手动添加用户时的初始密码（用户首次登录需修改）
- 用户自助注册时自己设置密码，无需初始密码

### 3. 启动服务

```bash
python3 app.py
```

访问：http://localhost:5000

### 4. 首次登录

**方式一：用户自助注册**
1. 访问系统首页，点击"还没有账号？立即注册"
2. 填写学号、姓名、密码
3. 注册成功后自动成为填报人角色
4. 如需报账权限，联系管理员修改角色

**方式二：管理员添加用户**
1. 管理员登录后进入"系统管理" → "用户管理"
2. 添加用户（学号、姓名、角色）
3. 初始密码为 `ADMIN_INITIAL_PASSWORD`（默认123456），用户首次登录强制修改

**管理员登录：**
- 学号：`admin`
- 密码：`app.py`中设置的密码

### 5. 生产环境部署

**安装为系统服务（开机自启）：**
```bash
sudo ./install_service.sh
```

**开放端口供外部访问：**
```bash
sudo ./setup_firewall.sh
```

**完整部署指南：** 见下方"生产环境部署"章节

## 生产环境部署

### 方式一：打包部署（推荐，支持数据迁移）

**1. 在源服务器打包**
```bash
cd /home/yuchen/scripts/Invoice

# 仅打包程序（不含数据）
./package.sh

# 打包程序+数据（迁移现有系统）
./package.sh --with-data
```

**2. 传输到目标服务器**
```bash
scp invoice-system-*.tar.gz user@target-server:/opt/
```

**3. 在目标服务器部署**
```bash
cd /opt
tar -xzf invoice-system-*.tar.gz
cd invoice-system

# 安装依赖
pip3 install -r requirements.txt

# 修改管理员密码（如果是全新部署）
vim app.py  # 修改顶部 ADMIN_PASSWORD

# 安装为系统服务（开机自启）
chmod +x *.sh
sudo ./install_service.sh

# 开放端口
sudo ./setup_firewall.sh
```

**4. 访问系统**
```
http://服务器IP:5000
```

### 方式二：Git克隆部署

```bash
git clone <repository-url>
cd invoice-system
pip3 install -r requirements.txt
vim app.py  # 修改管理员密码
sudo ./install_service.sh
sudo ./setup_firewall.sh
```

### 系统服务管理

**安装服务（开机自启）：**
```bash
sudo ./install_service.sh
```

**服务管理命令：**
```bash
sudo systemctl start invoice-system    # 启动
sudo systemctl stop invoice-system     # 停止
sudo systemctl restart invoice-system  # 重启
sudo systemctl status invoice-system   # 状态
sudo journalctl -u invoice-system -f   # 日志
```

**卸载服务：**
```bash
sudo ./uninstall_service.sh
```

### 端口配置

**开放5000端口：**
```bash
sudo ./setup_firewall.sh
```

**修改端口：**
编辑 `app.py` 最后一行：
```python
app.run(host="0.0.0.0", port=5001, debug=False)
```

### 使用Nginx反向代理（推荐）

**1. 安装Nginx**
```bash
sudo apt install nginx  # Ubuntu/Debian
sudo yum install nginx  # CentOS/RHEL
```

**2. 配置反向代理**
```bash
sudo vim /etc/nginx/sites-available/invoice
```

```nginx
server {
    listen 80;
    server_name your-domain.com;

    location / {
        proxy_pass http://127.0.0.1:5000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    client_max_body_size 50M;
}
```

**3. 启用配置**
```bash
sudo ln -s /etc/nginx/sites-available/invoice /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl restart nginx
```

**4. 启用HTTPS（推荐）**
```bash
sudo apt install certbot python3-certbot-nginx
sudo certbot --nginx -d your-domain.com
```

### 使用Gunicorn（生产环境推荐）

**1. 安装Gunicorn**
```bash
pip3 install gunicorn
```

**2. 修改服务文件**
```bash
sudo vim /etc/systemd/system/invoice-system.service
```

修改 `ExecStart` 行：
```ini
ExecStart=/usr/local/bin/gunicorn -w 4 -b 127.0.0.1:5000 app:app
```

**3. 重启服务**
```bash
sudo systemctl daemon-reload
sudo systemctl restart invoice-system
```

### 数据迁移

**从旧服务器迁移到新服务器：**

1. 在旧服务器打包（含数据）：
```bash
./package.sh --with-data
```

2. 传输并解压到新服务器

3. 数据会自动恢复：
   - 数据库：`invoice.db`
   - 附件：`uploads/`
   - 日志：`logs/`（可选）

**注意**：如果新服务器已有数据，打包文件中的数据会覆盖现有数据。

### 安全加固

**1. 修改默认密码**
```bash
vim app.py  # 修改 ADMIN_PASSWORD 和 DEFAULT_USER_PASSWORD
```

**2. 限制访问IP**
```bash
# 仅允许内网访问
sudo firewall-cmd --permanent --add-rich-rule='rule family="ipv4" source address="192.168.1.0/24" port port="5000" protocol="tcp" accept'
sudo firewall-cmd --reload
```

**3. 定期备份**
```bash
# 添加cron任务
crontab -e
```

添加：
```
0 2 * * * cd /opt/invoice-system && python3 -c "from app import app, create_backup; with app.app_context(): create_backup()"
```

**4. 监控日志**
```bash
# 实时监控
tail -f logs/app.log

# 查看错误
grep ERROR logs/app.log
```

### 性能优化

**1. 使用Gunicorn多进程**
```bash
gunicorn -w 4 -b 0.0.0.0:5000 app:app
```

**2. 配置数据库连接池**（如需要）

**3. 启用Nginx缓存**（静态资源）

### 故障排查

**服务无法启动：**
```bash
# 查看详细日志
sudo journalctl -u invoice-system -n 50

# 检查端口占用
sudo netstat -tlnp | grep 5000

# 手动启动测试
cd /opt/invoice-system
python3 app.py
```

**无法访问：**
```bash
# 检查防火墙
sudo firewall-cmd --list-ports

# 检查服务状态
sudo systemctl status invoice-system

# 检查监听端口
sudo ss -tlnp | grep 5000
```

**数据库锁定：**
```bash
# 检查占用进程
lsof invoice.db

# 重启服务
sudo systemctl restart invoice-system
```

## 角色说明

| 角色 | 权限 | 说明 |
|------|------|------|
| **填报人 filler** | 创建报销单、查看自己的记录 | 初始默认角色 |
| **材料报账 material_reimburser** | 处理材料和已验收低值品 + 填报权限 | 可填报也可报账 |
| **资产报账 asset_reimburser** | 验收低值品/资产、报销资产 + 填报权限 | 可填报也可报账 |
| **管理员 admin** | 全部权限 | 用户管理、数据导出、备份 |

**注意**：报账人同时拥有填报权限，可以提交自己的报销单。

## 文件结构

```
Invoice/
├── app.py              # 主程序（单文件）
├── requirements.txt    # Python依赖
├── README.md          # 本文档
├── invoice.db         # SQLite数据库（自动创建）
├── uploads/           # 附件存储（自动创建）
│   ├── invoices/      # 发票图片/PDF
│   └── check_reports/ # 验收报告
├── logs/              # 运行日志（自动创建）
│   └── app.log        # 主日志文件（最大10MB，滚动备份）
└── backup/            # 备份目录（自动创建）
```

## 使用说明

### 填报人操作

1. **新建报销单**
   - 填写报销者、项目名称
   - 添加发票明细（名称、单价、数量）
   - 上传附件（图片自动压缩）
   - **保存草稿**：暂存，稍后继续编辑
   - **提交**：提交进入审批流程

2. **草稿管理**
   - 草稿只有填报人自己和管理员可见
   - 可以编辑草稿内容
   - 可以提交草稿
   - 可以删除不需要的草稿

3. **撤回功能**
   - 已提交但未处理的报销单可以撤回
   - 撤回后变为草稿状态，可重新编辑
   - 适用状态：待验收、待材料报销、待资产报销

4. **查看状态**
   - 我的填报页面查看各状态统计
   - 点击查看详情和操作历史

### 报账者操作

1. **我的待办**
   - 材料报账：处理材料和已验收的低值品
   - 资产报账：验收资产/低值品，报销资产

2. **全部待办**
   - 查看所有待处理的报销单（共享视图）
   - 避免遗漏，协作处理

3. **历史记录**
   - 查看已报销和已驳回的记录
   - 材料报账：显示材料和低值品历史（最近50条）
   - 资产报账：显示低值品和资产历史（最近50条）
   - 管理员：显示所有历史（最近100条）

4. **处理流程**
   - 验收：资产报账者验收后自动分流
   - 确认报销：标记为已报销
   - 驳回：填写原因并驳回

### 管理员操作

1. **用户管理**
   - 添加新用户
   - 修改用户角色
   - 重置用户密码

2. **数据管理**
   - 查看空间占用
   - 创建备份（数据库+附件）
   - 导出CSV数据

## 运行日志

系统自动记录关键操作到 `logs/app.log`：

**记录内容：**
- 用户登录/登出/注册
- 报销单创建/提交
- 报销单处理（验收/确认/驳回）
- 备份操作
- 错误信息

**日志配置：**
- 单个日志文件最大：10MB
- 备份文件数：1个
- 总容量：最多20MB（当前10MB + 备份10MB）
- 自动滚动：超过10MB自动归档

**查看日志：**
```bash
# 查看最新日志
tail -f logs/app.log

# 查看最近100行
tail -n 100 logs/app.log

# 搜索特定用户操作
grep "用户登录" logs/app.log
```

## 数据备份

### 手动备份
管理员面板 → "创建备份" → 生成 `backup/时间戳.tar.gz`

### 自动备份（可选）
添加cron任务：

```bash
# 每天凌晨2点备份
0 2 * * * cd /home/yuchen/scripts/Invoice && python3 -c "
import shutil
from datetime import datetime
from pathlib import Path
timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
backup_dir = Path('backup') / timestamp
backup_dir.mkdir(parents=True, exist_ok=True)
shutil.copy2('invoice.db', backup_dir / 'invoice.db')
shutil.copytree('uploads', backup_dir / 'uploads')
shutil.make_archive(str(backup_dir), 'gztar', backup_dir)
shutil.rmtree(backup_dir)
# 删除30天前的备份
for old in Path('backup').glob('*.tar.gz'):
    if (datetime.now() - datetime.fromtimestamp(old.stat().st_mtime)).days > 30:
        old.unlink()
"
```

## 空间优化

- **图片压缩**：自动转WebP格式，压缩至1920px宽
- **滚动备份**：保留最近30天
- **空间监控**：管理员面板实时显示占用

预估：500条记录/年 ≈ 300MB

## 安全建议

1. **修改默认密码**：首次部署必须修改admin密码
2. **HTTPS部署**：生产环境使用Nginx反向代理+SSL
3. **防火墙**：限制5000端口仅内网访问
4. **定期备份**：设置自动备份任务

## 故障排查

### 图片无法压缩
```bash
pip3 install --upgrade Pillow
```
**注意**：如果Pillow未安装或压缩失败，系统会自动保存原图，不会丢失图片数据。

### 端口被占用
修改 `app.py` 最后一行：
```python
app.run(host="0.0.0.0", port=5001, debug=True)  # 改为5001
```

### 数据库锁定
```bash
# 检查是否有其他进程占用
lsof invoice.db
# 重启应用
```

## 技术栈

- **后端**：Flask 3.0 + SQLite
- **前端**：Bootstrap 5 + Jinja2模板
- **图片处理**：Pillow
- **认证**：Session + 密码哈希

## 许可证

MIT License
