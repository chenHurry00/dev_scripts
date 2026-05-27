#!/usr/bin/env python3
"""
Invoice Reimbursement System
Single-file Flask application with SQLite backend
Run: python3 app.py
Access: http://localhost:5000
"""

# ============= ADMIN CONFIGURATION =============
ADMIN_USERNAME = "admin"
ADMIN_PASSWORD = "BY116358"  # 请修改为强密码
ADMIN_INITIAL_PASSWORD = "123456"  # 管理员添加用户时的初始密码
# ===============================================

import base64
import hashlib
import io
import json
import logging
import os
import shutil
import sqlite3
import time
from datetime import datetime, timedelta
from functools import wraps
from logging.handlers import RotatingFileHandler
from pathlib import Path

from flask import (
    Flask,
    Response,
    flash,
    jsonify,
    redirect,
    render_template_string,
    request,
    send_file,
    session,
    url_for,
)
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

try:
    from PIL import Image
except ImportError:
    Image = None
    print("Warning: Pillow not installed, image compression disabled")

app = Flask(__name__)
app.secret_key = os.urandom(24)
app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024  # 50MB max upload
app.config["UPLOAD_FOLDER"] = Path(__file__).parent / "uploads"
app.config["BACKUP_FOLDER"] = Path(__file__).parent / "backup"
app.config["DATABASE"] = Path(__file__).parent / "invoice.db"
app.config["LOG_FOLDER"] = Path(__file__).parent / "logs"

# 确保目录存在
for folder in ["uploads/invoices", "uploads/check_reports", "backup", "logs"]:
    (Path(__file__).parent / folder).mkdir(parents=True, exist_ok=True)

# 配置日志
log_file = app.config["LOG_FOLDER"] / "app.log"
handler = RotatingFileHandler(
    log_file,
    maxBytes=10 * 1024 * 1024,  # 10MB
    backupCount=1,  # 保留1个备份，总共最多20MB（当前10MB + 备份10MB）
    encoding='utf-8'
)
handler.setLevel(logging.INFO)
formatter = logging.Formatter(
    '%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
handler.setFormatter(formatter)

# 添加到Flask logger
app.logger.addHandler(handler)
app.logger.setLevel(logging.INFO)

# 添加到root logger（捕获所有日志）
logging.getLogger().addHandler(handler)
logging.getLogger().setLevel(logging.INFO)

app.logger.info("=" * 60)
app.logger.info("发票报销系统启动")


# ============= Database Schema =============
def init_db():
    """初始化数据库"""
    conn = sqlite3.connect(app.config["DATABASE"])
    c = conn.cursor()

    # 用户表
    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            real_name TEXT NOT NULL,
            role TEXT NOT NULL,
            must_change_password INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # 发票主表
    c.execute("""
        CREATE TABLE IF NOT EXISTS invoices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            filler_id INTEGER NOT NULL,
            filler_name TEXT NOT NULL,
            reimburser_name TEXT NOT NULL,
            project_name TEXT NOT NULL,
            invoice_company TEXT,
            purchase_reason TEXT,
            payment_method TEXT,
            category TEXT NOT NULL,
            status TEXT NOT NULL,
            current_handler TEXT,
            submitted_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (filler_id) REFERENCES users(id)
        )
    """)

    # 发票明细表
    c.execute("""
        CREATE TABLE IF NOT EXISTS invoice_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            invoice_id INTEGER NOT NULL,
            item_name TEXT NOT NULL,
            unit_price REAL NOT NULL,
            quantity INTEGER NOT NULL,
            total_amount REAL NOT NULL,
            invoice_number TEXT,
            payment_record TEXT,
            physical_photo TEXT,
            notes TEXT,
            FOREIGN KEY (invoice_id) REFERENCES invoices(id) ON DELETE CASCADE
        )
    """)

    # 附件表
    c.execute("""
        CREATE TABLE IF NOT EXISTS attachments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            invoice_id INTEGER NOT NULL,
            category TEXT NOT NULL,
            file_type TEXT NOT NULL,
            file_path TEXT NOT NULL,
            file_size INTEGER NOT NULL,
            uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (invoice_id) REFERENCES invoices(id) ON DELETE CASCADE
        )
    """)

    # 验收报告表
    c.execute("""
        CREATE TABLE IF NOT EXISTS check_reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            invoice_id INTEGER NOT NULL,
            checker_id INTEGER NOT NULL,
            checker_name TEXT NOT NULL,
            report_content TEXT,
            file_path TEXT,
            checked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (invoice_id) REFERENCES invoices(id) ON DELETE CASCADE,
            FOREIGN KEY (checker_id) REFERENCES users(id)
        )
    """)

    # 操作历史表
    c.execute("""
        CREATE TABLE IF NOT EXISTS history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            invoice_id INTEGER NOT NULL,
            operator_id INTEGER NOT NULL,
            operator_name TEXT NOT NULL,
            action TEXT NOT NULL,
            old_status TEXT,
            new_status TEXT,
            notes TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (invoice_id) REFERENCES invoices(id) ON DELETE CASCADE,
            FOREIGN KEY (operator_id) REFERENCES users(id)
        )
    """)

    # 创建或更新admin用户
    admin_hash = generate_password_hash(ADMIN_PASSWORD)
    existing_admin = c.execute("SELECT id FROM users WHERE username = ?", (ADMIN_USERNAME,)).fetchone()

    if existing_admin:
        # 更新现有admin密码
        c.execute(
            "UPDATE users SET password_hash = ?, real_name = ?, role = ?, must_change_password = 0 WHERE username = ?",
            (admin_hash, "系统管理员", "admin", ADMIN_USERNAME)
        )
        app.logger.info(f"Admin用户密码已更新")
    else:
        # 创建新admin用户
        c.execute(
            """
            INSERT INTO users (username, password_hash, real_name, role, must_change_password)
            VALUES (?, ?, ?, ?, 0)
        """,
            (ADMIN_USERNAME, admin_hash, "系统管理员", "admin"),
        )
        app.logger.info(f"Admin用户已创建")

    # 数据库迁移：为旧附件表添加category列
    try:
        c.execute("SELECT category FROM attachments LIMIT 1")
    except sqlite3.OperationalError:
        c.execute("ALTER TABLE attachments ADD COLUMN category TEXT NOT NULL DEFAULT 'invoice_file'")
        app.logger.info("数据库迁移：attachments表添加category列")

    # 数据库迁移：为旧invoices表添加payment_method列
    try:
        c.execute("SELECT payment_method FROM invoices LIMIT 1")
    except sqlite3.OperationalError:
        c.execute("ALTER TABLE invoices ADD COLUMN payment_method TEXT")
        app.logger.info("数据库迁移：invoices表添加payment_method列")

    conn.commit()
    conn.close()


# ============= Helper Functions =============
def get_db():
    """获取数据库连接"""
    conn = sqlite3.connect(app.config["DATABASE"])
    conn.row_factory = sqlite3.Row
    return conn


def login_required(f):
    """登录验证装饰器"""

    @wraps(f)
    def decorated_function(*args, **kwargs):
        if "user_id" not in session:
            flash("请先登录", "warning")
            return redirect(url_for("login"))
        if session.get("must_change_password"):
            return redirect(url_for("change_password"))
        return f(*args, **kwargs)

    return decorated_function


def role_required(*roles):
    """角色验证装饰器"""

    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if session.get("role") not in roles:
                flash("权限不足", "danger")
                return redirect(url_for("index"))
            return f(*args, **kwargs)

        return decorated_function

    return decorator


def auto_classify(unit_price):
    """根据单价返回分类"""
    if unit_price < 500:
        return "material"
    elif unit_price < 1000:
        return "low_value"
    else:
        return "asset"


def calc_category_and_status(items_data):
    """根据所有明细计算综合分类和状态"""
    categories = set()
    for item in items_data:
        categories.add(auto_classify(item["unit_price"]))

    # 状态由最高分类决定
    if "asset" in categories:
        status = "pending_check"
    elif "low_value" in categories:
        status = "pending_check"
    else:
        status = "pending_material"

    # 分类按优先级排序存储
    order = ["material", "low_value", "asset"]
    category = ",".join(c for c in order if c in categories)
    return category, status


def compress_image(file_obj, max_width=1920, quality=85):
    """压缩图片为WebP格式，如果失败则返回原图数据和原始标志"""
    if not Image:
        file_obj.seek(0)
        return file_obj.read(), False  # 返回原图数据和失败标志

    try:
        img = Image.open(file_obj)
        if img.mode in ("RGBA", "LA", "P"):
            img = img.convert("RGB")

        # 等比例缩放
        if img.width > max_width:
            ratio = max_width / img.width
            new_size = (max_width, int(img.height * ratio))
            img = img.resize(new_size, Image.Resampling.LANCZOS)

        # 保存为WebP
        output = io.BytesIO()
        img.save(output, format="WEBP", quality=quality, method=6)
        return output.getvalue(), True  # 返回压缩数据和成功标志
    except Exception as e:
        print(f"Image compression failed: {e}")
        file_obj.seek(0)
        return file_obj.read(), False  # 返回原图数据和失败标志


def get_folder_size(folder_path):
    """计算文件夹大小"""
    total = 0
    try:
        for entry in os.scandir(folder_path):
            if entry.is_file():
                total += entry.stat().st_size
            elif entry.is_dir():
                total += get_folder_size(entry.path)
    except Exception:
        pass
    return total


def format_size(bytes_size):
    """格式化文件大小"""
    for unit in ["B", "KB", "MB", "GB"]:
        if bytes_size < 1024:
            return f"{bytes_size:.2f} {unit}"
        bytes_size /= 1024
    return f"{bytes_size:.2f} TB"


def add_history(conn, invoice_id, operator_id, operator_name, action, old_status=None, new_status=None, notes=None):
    """添加操作历史"""
    conn.execute(
        """
        INSERT INTO history (invoice_id, operator_id, operator_name, action, old_status, new_status, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """,
        (invoice_id, operator_id, operator_name, action, old_status, new_status, notes),
    )


# ============= Routes =============
@app.route("/")
def index():
    """首页"""
    if "user_id" not in session:
        return redirect(url_for("login"))
    if session.get("must_change_password"):
        return redirect(url_for("change_password"))

    role = session.get("role")
    if role == "admin":
        return redirect(url_for("admin_dashboard"))
    elif role in ["material_reimburser", "asset_reimburser"]:
        return redirect(url_for("reimburser_dashboard"))
    elif role == "filler":
        return redirect(url_for("filler_dashboard"))
    else:
        flash("未分配角色，请联系管理员", "warning")
        return redirect(url_for("logout"))


@app.route("/login", methods=["GET", "POST"])
def login():
    """登录"""
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()

        if not username or not password:
            flash("请输入学号和密码", "danger")
            return redirect(url_for("login"))

        conn = get_db()
        user = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        conn.close()

        if user and check_password_hash(user["password_hash"], password):
            session["user_id"] = user["id"]
            session["username"] = user["username"]
            session["real_name"] = user["real_name"]
            session["role"] = user["role"]
            session["must_change_password"] = user["must_change_password"]

            app.logger.info(f"用户登录: {username} ({user['real_name']}) - 角色: {user['role']}")

            if user["must_change_password"]:
                flash("首次登录，请修改密码", "info")
                return redirect(url_for("change_password"))

            flash(f"欢迎回来，{user['real_name']}", "success")
            return redirect(url_for("index"))
        else:
            app.logger.warning(f"登录失败: {username} - IP: {request.remote_addr}")
            flash("学号或密码错误", "danger")

    return render_template_string(LOGIN_TEMPLATE)


@app.route("/register", methods=["GET", "POST"])
def register():
    """用户注册"""
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        real_name = request.form.get("real_name", "").strip()
        password = request.form.get("password", "").strip()
        confirm_password = request.form.get("confirm_password", "").strip()

        if not all([username, real_name, password, confirm_password]):
            flash("请填写完整信息", "danger")
            return redirect(url_for("register"))

        if password != confirm_password:
            flash("两次输入的密码不一致", "danger")
            return redirect(url_for("register"))

        if len(password) < 6:
            flash("密码长度至少6位", "danger")
            return redirect(url_for("register"))

        conn = get_db()

        # 检查学号是否已存在
        existing = conn.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()
        if existing:
            flash("该学号已注册", "danger")
            conn.close()
            return redirect(url_for("register"))

        # 创建用户，默认角色为填报人
        password_hash = generate_password_hash(password)
        conn.execute(
            """
            INSERT INTO users (username, password_hash, real_name, role, must_change_password)
            VALUES (?, ?, ?, ?, 0)
        """,
            (username, password_hash, real_name, "filler"),
        )
        conn.commit()
        conn.close()

        app.logger.info(f"新用户注册: {username} ({real_name}) - IP: {request.remote_addr}")
        flash("注册成功，请登录", "success")
        return redirect(url_for("login"))

    return render_template_string(REGISTER_TEMPLATE)


@app.route("/logout")
def logout():
    """登出"""
    username = session.get("username", "unknown")
    app.logger.info(f"用户登出: {username}")
    session.clear()
    flash("已退出登录", "info")
    return redirect(url_for("login"))


@app.route("/change_password", methods=["GET", "POST"])
def change_password():
    """修改密码"""
    if "user_id" not in session:
        return redirect(url_for("login"))

    if request.method == "POST":
        old_password = request.form.get("old_password", "").strip()
        new_password = request.form.get("new_password", "").strip()
        confirm_password = request.form.get("confirm_password", "").strip()

        if not all([old_password, new_password, confirm_password]):
            flash("请填写完整信息", "danger")
            return redirect(url_for("change_password"))

        if new_password != confirm_password:
            flash("两次输入的新密码不一致", "danger")
            return redirect(url_for("change_password"))

        if len(new_password) < 6:
            flash("新密码长度至少6位", "danger")
            return redirect(url_for("change_password"))

        conn = get_db()
        user = conn.execute("SELECT * FROM users WHERE id = ?", (session["user_id"],)).fetchone()

        if not check_password_hash(user["password_hash"], old_password):
            flash("原密码错误", "danger")
            conn.close()
            return redirect(url_for("change_password"))

        new_hash = generate_password_hash(new_password)
        conn.execute(
            "UPDATE users SET password_hash = ?, must_change_password = 0 WHERE id = ?",
            (new_hash, session["user_id"]),
        )
        conn.commit()
        conn.close()

        session["must_change_password"] = 0
        flash("密码修改成功", "success")
        return redirect(url_for("index"))

    return render_template_string(CHANGE_PASSWORD_TEMPLATE)


@app.route("/filler/dashboard")
@login_required
@role_required("filler", "material_reimburser", "asset_reimburser", "admin")
def filler_dashboard():
    """填报人仪表盘"""
    conn = get_db()
    user_id = session["user_id"]

    # 获取各状态统计
    stats = {}
    for status in ["draft", "pending_check", "pending_material", "pending_asset", "reimbursed", "rejected"]:
        count = conn.execute(
            "SELECT COUNT(*) as cnt FROM invoices WHERE filler_id = ? AND status = ?", (user_id, status)
        ).fetchone()["cnt"]
        stats[status] = count

    # 获取最近提交
    recent = conn.execute(
        """
        SELECT id, project_name, category, status, submitted_at, created_at
        FROM invoices WHERE filler_id = ?
        ORDER BY created_at DESC LIMIT 10
    """,
        (user_id,),
    ).fetchall()

    conn.close()
    return render_template_string(FILLER_DASHBOARD_TEMPLATE, stats=stats, recent=recent)


@app.route("/filler/create", methods=["GET", "POST"])
@login_required
@role_required("filler", "material_reimburser", "asset_reimburser", "admin")
def create_invoice():
    """创建报销单"""
    if request.method == "POST":
        try:
            # 基本信息
            reimburser_name = request.form.get("reimburser_name", "").strip()
            project_name = request.form.get("project_name", "").strip()
            invoice_company = request.form.get("invoice_company", "").strip()
            purchase_reason = request.form.get("purchase_reason", "").strip()
            payment_method = request.form.get("payment_method", "").strip()

            # 明细信息
            item_names = request.form.getlist("item_name[]")
            unit_prices = request.form.getlist("unit_price[]")
            quantities = request.form.getlist("quantity[]")
            invoice_numbers = request.form.getlist("invoice_number[]")
            payment_records = request.form.getlist("payment_record[]")
            physical_photos = request.form.getlist("physical_photo[]")
            notes_list = request.form.getlist("notes[]")

            if not all([reimburser_name, project_name, item_names, payment_method]):
                missing = []
                if not reimburser_name:
                    missing.append("报销者姓名")
                if not project_name:
                    missing.append("项目名称")
                if not item_names:
                    missing.append("发票明细")
                if not payment_method:
                    missing.append("支付方式")
                flash(f"请填写必填项：{', '.join(missing)}", "danger")
                return redirect(url_for("create_invoice"))

            conn = get_db()

            # 计算总金额和分类
            max_unit_price = 0
            total_invoice_amount = 0
            items_data = []

            for i in range(len(item_names)):
                if not item_names[i].strip():
                    continue

                unit_price = float(unit_prices[i])
                quantity = int(quantities[i])
                total_amount = unit_price * quantity

                total_invoice_amount += total_amount

                items_data.append(
                    {
                        "item_name": item_names[i].strip(),
                        "unit_price": unit_price,
                        "quantity": quantity,
                        "total_amount": total_amount,
                        "invoice_number": invoice_numbers[i].strip() if i < len(invoice_numbers) else "",
                        "payment_record": payment_records[i].strip() if i < len(payment_records) else "",
                        "physical_photo": physical_photos[i].strip() if i < len(physical_photos) else "",
                        "notes": notes_list[i].strip() if i < len(notes_list) else "",
                    }
                )

            # 自动分类（根据所有明细）
            category, status = calc_category_and_status(items_data)

            # 保存为草稿还是提交
            action = request.form.get("action", "draft")
            if action == "submit":
                submitted_at = datetime.now().isoformat()
            else:
                status = "draft"
                submitted_at = None

            # 插入主表
            cursor = conn.execute(
                """
                INSERT INTO invoices (filler_id, filler_name, reimburser_name, project_name,
                                      invoice_company, purchase_reason, payment_method, category, status, submitted_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    session["user_id"],
                    session["real_name"],
                    reimburser_name,
                    project_name,
                    invoice_company,
                    purchase_reason,
                    payment_method,
                    category,
                    status,
                    submitted_at,
                ),
            )
            invoice_id = cursor.lastrowid

            # 插入明细
            for item in items_data:
                conn.execute(
                    """
                    INSERT INTO invoice_items (invoice_id, item_name, unit_price, quantity, total_amount,
                                               invoice_number, payment_record, physical_photo, notes)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                    (
                        invoice_id,
                        item["item_name"],
                        item["unit_price"],
                        item["quantity"],
                        item["total_amount"],
                        item["invoice_number"],
                        item["payment_record"],
                        item["physical_photo"],
                        item["notes"],
                    ),
                )

            # 处理附件上传
            attachment_categories = ["invoice_file", "physical_photo", "order_screenshot", "payment_record"]
            for att_category in attachment_categories:
                files = request.files.getlist(att_category)
                for file in files:
                    if file and file.filename:
                        filename = secure_filename(file.filename)
                        ext = filename.rsplit(".", 1)[-1].lower()

                        # 生成唯一文件名
                        timestamp = int(time.time() * 1000)
                        hash_name = hashlib.md5(f"{invoice_id}_{timestamp}_{filename}".encode()).hexdigest()[:8]
                        new_filename = f"{invoice_id}_{att_category}_{timestamp}_{hash_name}.{ext}"

                        file_path = app.config["UPLOAD_FOLDER"] / "invoices" / new_filename

                        # 图片压缩
                        if ext in ["jpg", "jpeg", "png", "bmp"]:
                            compressed_data, success = compress_image(file)
                            if success:
                                new_filename = new_filename.rsplit(".", 1)[0] + ".webp"
                                file_path = app.config["UPLOAD_FOLDER"] / "invoices" / new_filename
                                ext = "webp"
                            else:
                                file_path = app.config["UPLOAD_FOLDER"] / "invoices" / new_filename

                            with open(file_path, "wb") as f:
                                f.write(compressed_data)
                            file_size = len(compressed_data)
                        else:
                            file.save(file_path)
                            file_size = file_path.stat().st_size

                        conn.execute(
                            """
                            INSERT INTO attachments (invoice_id, category, file_type, file_path, file_size)
                            VALUES (?, ?, ?, ?, ?)
                        """,
                            (invoice_id, att_category, ext, str(file_path), file_size),
                        )

            # 记录历史
            if action == "submit":
                add_history(
                    conn,
                    invoice_id,
                    session["user_id"],
                    session["real_name"],
                    "提交报销单",
                    None,
                    status,
                    f"总金额: {total_invoice_amount:.2f}元",
                )

            conn.commit()
            conn.close()

            app.logger.info(
                f"报销单{'提交' if action == 'submit' else '保存'}: "
                f"ID={invoice_id}, 填报人={session['real_name']}, "
                f"报销者={reimburser_name}, 总金额={total_invoice_amount:.2f}元, "
                f"分类={category}, 状态={status}"
            )

            flash(f"报销单{'提交' if action == 'submit' else '保存'}成功", "success")
            return redirect(url_for("filler_dashboard"))

        except Exception as e:
            flash(f"创建失败: {str(e)}", "danger")
            return redirect(url_for("create_invoice"))

    return render_template_string(CREATE_INVOICE_TEMPLATE)


@app.route("/filler/invoice/<int:invoice_id>")
@login_required
@role_required("filler", "material_reimburser", "asset_reimburser", "admin")
def view_invoice(invoice_id):
    """查看报销单详情"""
    conn = get_db()

    invoice = conn.execute(
        "SELECT * FROM invoices WHERE id = ? AND (filler_id = ? OR ? = 'admin')",
        (invoice_id, session["user_id"], session["role"])
    ).fetchone()

    if not invoice:
        flash("报销单不存在或无权访问", "danger")
        conn.close()
        return redirect(url_for("filler_dashboard"))

    items = conn.execute(
        "SELECT * FROM invoice_items WHERE invoice_id = ?", (invoice_id,)
    ).fetchall()

    attachments = conn.execute(
        "SELECT * FROM attachments WHERE invoice_id = ?", (invoice_id,)
    ).fetchall()

    history = conn.execute(
        """
        SELECT * FROM history WHERE invoice_id = ?
        ORDER BY created_at DESC
    """, (invoice_id,)
    ).fetchall()

    check_report = conn.execute(
        "SELECT * FROM check_reports WHERE invoice_id = ?", (invoice_id,)
    ).fetchone()

    conn.close()

    # 判断是否可以编辑（草稿状态且是填报人或admin）
    can_edit = (invoice["status"] == "draft" and
                (invoice["filler_id"] == session["user_id"] or session["role"] == "admin"))

    # 判断是否可以撤回（已提交但未处理的报销单）
    can_withdraw = (invoice["status"] in ["pending_check", "pending_material", "pending_asset"] and
                    (invoice["filler_id"] == session["user_id"] or session["role"] == "admin"))

    return render_template_string(
        VIEW_INVOICE_TEMPLATE,
        invoice=invoice,
        items=items,
        attachments=attachments,
        history=history,
        check_report=check_report,
        can_edit=can_edit,
        can_withdraw=can_withdraw
    )


@app.route("/filler/invoice/<int:invoice_id>/edit", methods=["GET", "POST"])
@login_required
@role_required("filler", "material_reimburser", "asset_reimburser", "admin")
def edit_invoice(invoice_id):
    """编辑草稿"""
    conn = get_db()

    # 检查权限：只能编辑自己的草稿
    invoice = conn.execute(
        "SELECT * FROM invoices WHERE id = ? AND status = 'draft' AND (filler_id = ? OR ? = 'admin')",
        (invoice_id, session["user_id"], session["role"])
    ).fetchone()

    if not invoice:
        flash("草稿不存在或无权编辑", "danger")
        conn.close()
        return redirect(url_for("filler_dashboard"))

    if request.method == "POST":
        try:
            # 基本信息
            reimburser_name = request.form.get("reimburser_name", "").strip()
            project_name = request.form.get("project_name", "").strip()
            invoice_company = request.form.get("invoice_company", "").strip()
            purchase_reason = request.form.get("purchase_reason", "").strip()
            payment_method = request.form.get("payment_method", "").strip()

            # 明细信息
            item_names = request.form.getlist("item_name[]")
            unit_prices = request.form.getlist("unit_price[]")
            quantities = request.form.getlist("quantity[]")
            invoice_numbers = request.form.getlist("invoice_number[]")
            payment_records = request.form.getlist("payment_record[]")
            physical_photos = request.form.getlist("physical_photo[]")
            notes_list = request.form.getlist("notes[]")

            if not all([reimburser_name, project_name, item_names, payment_method]):
                missing = []
                if not reimburser_name:
                    missing.append("报销者姓名")
                if not project_name:
                    missing.append("项目名称")
                if not item_names:
                    missing.append("发票明细")
                if not payment_method:
                    missing.append("支付方式")
                flash(f"请填写必填项：{', '.join(missing)}", "danger")
                conn.close()
                return redirect(url_for("edit_invoice", invoice_id=invoice_id))

            # 计算总金额和分类
            items_data = []

            for i in range(len(item_names)):
                if not item_names[i].strip():
                    continue

                unit_price = float(unit_prices[i])
                quantity = int(quantities[i])
                total_amount = unit_price * quantity

                items_data.append({
                    "item_name": item_names[i].strip(),
                    "unit_price": unit_price,
                    "quantity": quantity,
                    "total_amount": total_amount,
                    "invoice_number": invoice_numbers[i].strip() if i < len(invoice_numbers) else "",
                    "payment_record": payment_records[i].strip() if i < len(payment_records) else "",
                    "physical_photo": physical_photos[i].strip() if i < len(physical_photos) else "",
                    "notes": notes_list[i].strip() if i < len(notes_list) else "",
                })

            # 自动分类（根据所有明细）
            category, status = calc_category_and_status(items_data)

            # 保存为草稿还是提交
            action = request.form.get("action", "draft")
            if action == "submit":
                submitted_at = datetime.now().isoformat()
            else:
                status = "draft"
                submitted_at = None

            # 更新主表
            conn.execute(
                """
                UPDATE invoices SET reimburser_name = ?, project_name = ?,
                                   invoice_company = ?, purchase_reason = ?, payment_method = ?,
                                   category = ?, status = ?, submitted_at = ?,
                                   updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """,
                (reimburser_name, project_name, invoice_company, purchase_reason, payment_method,
                 category, status, submitted_at, invoice_id)
            )

            # 删除旧明细
            conn.execute("DELETE FROM invoice_items WHERE invoice_id = ?", (invoice_id,))

            # 插入新明细
            for item in items_data:
                conn.execute(
                    """
                    INSERT INTO invoice_items (invoice_id, item_name, unit_price, quantity, total_amount,
                                               invoice_number, payment_record, physical_photo, notes)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                    (invoice_id, item["item_name"], item["unit_price"], item["quantity"],
                     item["total_amount"], item["invoice_number"], item["payment_record"],
                     item["physical_photo"], item["notes"])
                )

            # 记录历史
            if action == "submit":
                add_history(
                    conn, invoice_id, session["user_id"], session["real_name"],
                    "提交报销单", "draft", status, "从草稿提交"
                )
                app.logger.info(f"草稿提交: ID={invoice_id}, 填报人={session['real_name']}")
            else:
                app.logger.info(f"草稿更新: ID={invoice_id}, 填报人={session['real_name']}")

            conn.commit()
            conn.close()

            flash(f"草稿{'提交' if action == 'submit' else '保存'}成功", "success")
            return redirect(url_for("filler_dashboard"))

        except Exception as e:
            conn.close()
            flash(f"操作失败: {str(e)}", "danger")
            return redirect(url_for("edit_invoice", invoice_id=invoice_id))

    # GET请求：显示编辑表单
    items = conn.execute(
        "SELECT * FROM invoice_items WHERE invoice_id = ?", (invoice_id,)
    ).fetchall()

    conn.close()

    return render_template_string(EDIT_INVOICE_TEMPLATE, invoice=invoice, items=items)


@app.route("/filler/invoice/<int:invoice_id>/delete", methods=["POST"])
@login_required
@role_required("filler", "material_reimburser", "asset_reimburser", "admin")
def delete_invoice(invoice_id):
    """删除草稿"""
    conn = get_db()

    # 检查权限：只能删除自己的草稿
    invoice = conn.execute(
        "SELECT * FROM invoices WHERE id = ? AND status = 'draft' AND (filler_id = ? OR ? = 'admin')",
        (invoice_id, session["user_id"], session["role"])
    ).fetchone()

    if not invoice:
        flash("草稿不存在或无权删除", "danger")
        conn.close()
        return redirect(url_for("filler_dashboard"))

    # 删除附件文件
    attachments = conn.execute(
        "SELECT file_path FROM attachments WHERE invoice_id = ?", (invoice_id,)
    ).fetchall()

    for att in attachments:
        try:
            Path(att["file_path"]).unlink(missing_ok=True)
        except Exception:
            pass

    # 删除数据库记录（级联删除）
    conn.execute("DELETE FROM invoices WHERE id = ?", (invoice_id,))
    conn.commit()
    conn.close()

    app.logger.info(f"草稿删除: ID={invoice_id}, 操作人={session['real_name']}")
    flash("草稿已删除", "success")
    return redirect(url_for("filler_dashboard"))


@app.route("/filler/invoice/<int:invoice_id>/withdraw", methods=["POST"])
@login_required
@role_required("filler", "material_reimburser", "asset_reimburser", "admin")
def withdraw_invoice(invoice_id):
    """撤回报销单为草稿"""
    conn = get_db()

    # 检查权限：只能撤回自己的未处理报销单
    invoice = conn.execute(
        """
        SELECT * FROM invoices
        WHERE id = ?
        AND status IN ('pending_check', 'pending_material', 'pending_asset')
        AND (filler_id = ? OR ? = 'admin')
        """,
        (invoice_id, session["user_id"], session["role"])
    ).fetchone()

    if not invoice:
        flash("报销单不存在或无权撤回", "danger")
        conn.close()
        return redirect(url_for("filler_dashboard"))

    old_status = invoice["status"]

    # 更新为草稿状态
    conn.execute(
        """
        UPDATE invoices
        SET status = 'draft', submitted_at = NULL, updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (invoice_id,)
    )

    # 记录历史
    add_history(
        conn, invoice_id, session["user_id"], session["real_name"],
        "撤回报销单", old_status, "draft", "撤回为草稿重新编辑"
    )

    conn.commit()
    conn.close()

    app.logger.info(f"报销单撤回: ID={invoice_id}, 操作人={session['real_name']}, {old_status}→draft")
    flash("报销单已撤回为草稿，可以重新编辑", "success")
    return redirect(url_for("view_invoice", invoice_id=invoice_id))


@app.route("/reimburser/dashboard")
@login_required
@role_required("material_reimburser", "asset_reimburser", "admin")
def reimburser_dashboard():
    """报账者仪表盘"""
    conn = get_db()
    role = session["role"]

    # 构建查询条件
    if role == "material_reimburser":
        # 材料报账者：材料 + 已验收的低值品
        my_pending = conn.execute("""
            SELECT i.*,
                   (SELECT SUM(total_amount) FROM invoice_items WHERE invoice_id = i.id) as total_amount
            FROM invoices i
            WHERE i.status = 'pending_material'
            ORDER BY i.submitted_at ASC
        """).fetchall()

        # 我处理过的历史记录
        my_history = conn.execute("""
            SELECT i.*,
                   (SELECT SUM(total_amount) FROM invoice_items WHERE invoice_id = i.id) as total_amount
            FROM invoices i
            WHERE i.status IN ('reimbursed', 'rejected')
            AND (i.category LIKE '%material%' OR i.category LIKE '%low_value%')
            ORDER BY i.updated_at DESC
            LIMIT 50
        """).fetchall()

    elif role == "asset_reimburser":
        # 资产报账者：待验收 + 待资产报销
        my_pending = conn.execute("""
            SELECT i.*,
                   (SELECT SUM(total_amount) FROM invoice_items WHERE invoice_id = i.id) as total_amount
            FROM invoices i
            WHERE i.status IN ('pending_check', 'pending_asset')
            ORDER BY i.submitted_at ASC
        """).fetchall()

        # 我处理过的历史记录
        my_history = conn.execute("""
            SELECT i.*,
                   (SELECT SUM(total_amount) FROM invoice_items WHERE invoice_id = i.id) as total_amount
            FROM invoices i
            WHERE i.status IN ('reimbursed', 'rejected')
            AND (i.category LIKE '%low_value%' OR i.category LIKE '%asset%')
            ORDER BY i.updated_at DESC
            LIMIT 50
        """).fetchall()

    else:  # admin
        my_pending = conn.execute("""
            SELECT i.*,
                   (SELECT SUM(total_amount) FROM invoice_items WHERE invoice_id = i.id) as total_amount
            FROM invoices i
            WHERE i.status IN ('pending_check', 'pending_material', 'pending_asset')
            ORDER BY i.submitted_at ASC
        """).fetchall()

        # 所有历史记录
        my_history = conn.execute("""
            SELECT i.*,
                   (SELECT SUM(total_amount) FROM invoice_items WHERE invoice_id = i.id) as total_amount
            FROM invoices i
            WHERE i.status IN ('reimbursed', 'rejected')
            ORDER BY i.updated_at DESC
            LIMIT 100
        """).fetchall()

    # 全部待办（共享视图）
    all_pending = conn.execute("""
        SELECT i.*,
               (SELECT SUM(total_amount) FROM invoice_items WHERE invoice_id = i.id) as total_amount
        FROM invoices i
        WHERE i.status IN ('pending_check', 'pending_material', 'pending_asset')
        ORDER BY i.submitted_at ASC
    """).fetchall()

    conn.close()

    return render_template_string(
        REIMBURSER_DASHBOARD_TEMPLATE,
        my_pending=my_pending,
        all_pending=all_pending,
        my_history=my_history
    )


@app.route("/reimburser/invoice/<int:invoice_id>", methods=["GET", "POST"])
@login_required
@role_required("material_reimburser", "asset_reimburser", "admin")
def handle_invoice(invoice_id):
    """处理报销单"""
    conn = get_db()

    invoice = conn.execute("SELECT * FROM invoices WHERE id = ?", (invoice_id,)).fetchone()

    if not invoice:
        flash("报销单不存在", "danger")
        conn.close()
        return redirect(url_for("reimburser_dashboard"))

    if request.method == "POST":
        action = request.form.get("action")
        notes = request.form.get("notes", "").strip()

        old_status = invoice["status"]
        new_status = old_status

        if action == "approve_check":
            # 资产报账者验收通过，根据分类决定下一步
            categories = invoice["category"].split(",") if invoice["category"] else []

            # 优先级：资产 > 低值品 > 材料
            if "asset" in categories:
                new_status = "pending_asset"
            elif "low_value" in categories:
                new_status = "pending_material"
            elif "material" in categories:
                new_status = "pending_material"
            else:
                new_status = "pending_material"  # 默认

            # 保存验收报告
            report_content = request.form.get("report_content", "").strip()
            report_file = request.files.get("report_file")

            file_path = None
            if report_file and report_file.filename:
                filename = secure_filename(report_file.filename)
                timestamp = int(time.time() * 1000)
                new_filename = f"{invoice_id}_check_{timestamp}.{filename.rsplit('.', 1)[-1]}"
                file_path = app.config["UPLOAD_FOLDER"] / "check_reports" / new_filename
                report_file.save(file_path)

            conn.execute("""
                INSERT INTO check_reports (invoice_id, checker_id, checker_name, report_content, file_path)
                VALUES (?, ?, ?, ?, ?)
            """, (invoice_id, session["user_id"], session["real_name"], report_content, str(file_path) if file_path else None))

            add_history(conn, invoice_id, session["user_id"], session["real_name"],
                       "验收通过", old_status, new_status, notes)

        elif action == "approve_reimburse":
            # 确认报销
            categories = invoice["category"].split(",") if invoice["category"] else []

            # 如果是资产报销阶段，且包含材料/低值品，转到材料报销
            if invoice["status"] == "pending_asset" and ("material" in categories or "low_value" in categories):
                new_status = "pending_material"
                add_history(conn, invoice_id, session["user_id"], session["real_name"],
                           "资产报销完成", old_status, new_status, notes)
            else:
                # 否则直接完成
                new_status = "reimbursed"
                add_history(conn, invoice_id, session["user_id"], session["real_name"],
                           "确认报销", old_status, new_status, notes)

        elif action == "reject":
            # 驳回
            new_status = "rejected"
            add_history(conn, invoice_id, session["user_id"], session["real_name"],
                       "驳回", old_status, new_status, notes)

        # 更新状态
        conn.execute(
            "UPDATE invoices SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (new_status, invoice_id)
        )
        conn.commit()
        conn.close()

        app.logger.info(
            f"报销单处理: ID={invoice_id}, 操作人={session['real_name']}, "
            f"操作={action}, 状态变更={old_status}→{new_status}"
        )

        flash("操作成功", "success")
        return redirect(url_for("reimburser_dashboard"))

    # GET请求：显示详情
    items = conn.execute(
        "SELECT * FROM invoice_items WHERE invoice_id = ?", (invoice_id,)
    ).fetchall()

    attachments = conn.execute(
        "SELECT * FROM attachments WHERE invoice_id = ?", (invoice_id,)
    ).fetchall()

    history = conn.execute(
        "SELECT * FROM history WHERE invoice_id = ? ORDER BY created_at DESC", (invoice_id,)
    ).fetchall()

    check_report = conn.execute(
        "SELECT * FROM check_reports WHERE invoice_id = ?", (invoice_id,)
    ).fetchone()

    conn.close()

    return render_template_string(
        HANDLE_INVOICE_TEMPLATE,
        invoice=invoice,
        items=items,
        attachments=attachments,
        history=history,
        check_report=check_report
    )


@app.route("/admin/dashboard")
@login_required
@role_required("admin")
def admin_dashboard():
    """管理员仪表盘"""
    conn = get_db()

    # 统计数据
    total_users = conn.execute("SELECT COUNT(*) as cnt FROM users WHERE role != 'admin'").fetchone()["cnt"]
    total_invoices = conn.execute("SELECT COUNT(*) as cnt FROM invoices").fetchone()["cnt"]
    pending_count = conn.execute(
        "SELECT COUNT(*) as cnt FROM invoices WHERE status IN ('pending_check', 'pending_material', 'pending_asset')"
    ).fetchone()["cnt"]

    # 空间占用
    db_size = app.config["DATABASE"].stat().st_size if app.config["DATABASE"].exists() else 0
    uploads_size = get_folder_size(app.config["UPLOAD_FOLDER"])
    backup_size = get_folder_size(app.config["BACKUP_FOLDER"])
    total_size = db_size + uploads_size + backup_size

    space_info = {
        "database": format_size(db_size),
        "uploads": format_size(uploads_size),
        "backup": format_size(backup_size),
        "total": format_size(total_size)
    }

    # 最近活动
    recent_activity = conn.execute("""
        SELECT h.*, i.project_name
        FROM history h
        JOIN invoices i ON h.invoice_id = i.id
        ORDER BY h.created_at DESC
        LIMIT 20
    """).fetchall()

    conn.close()

    return render_template_string(
        ADMIN_DASHBOARD_TEMPLATE,
        total_users=total_users,
        total_invoices=total_invoices,
        pending_count=pending_count,
        space_info=space_info,
        recent_activity=recent_activity
    )


@app.route("/admin/users")
@login_required
@role_required("admin")
def manage_users():
    """用户管理"""
    conn = get_db()
    users = conn.execute(
        "SELECT * FROM users WHERE role != 'admin' ORDER BY created_at DESC"
    ).fetchall()
    conn.close()

    return render_template_string(MANAGE_USERS_TEMPLATE, users=users)


@app.route("/admin/user/add", methods=["POST"])
@login_required
@role_required("admin")
def add_user():
    """添加用户"""
    username = request.form.get("username", "").strip()
    real_name = request.form.get("real_name", "").strip()
    role = request.form.get("role", "").strip()

    if not all([username, real_name, role]):
        flash("请填写完整信息", "danger")
        return redirect(url_for("manage_users"))

    if role not in ["filler", "material_reimburser", "asset_reimburser"]:
        flash("角色无效", "danger")
        return redirect(url_for("manage_users"))

    conn = get_db()

    # 检查用户名是否存在
    existing = conn.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()
    if existing:
        flash("学号已存在", "danger")
        conn.close()
        return redirect(url_for("manage_users"))

    # 创建用户
    password_hash = generate_password_hash(ADMIN_INITIAL_PASSWORD)
    conn.execute(
        """
        INSERT INTO users (username, password_hash, real_name, role, must_change_password)
        VALUES (?, ?, ?, ?, 1)
    """, (username, password_hash, real_name, role)
    )
    conn.commit()
    conn.close()

    flash(f"用户 {real_name} 创建成功，初始密码: {ADMIN_INITIAL_PASSWORD}", "success")
    return redirect(url_for("manage_users"))


@app.route("/admin/user/<int:user_id>/role", methods=["POST"])
@login_required
@role_required("admin")
def update_user_role(user_id):
    """更新用户角色"""
    new_role = request.form.get("role", "").strip()

    if new_role not in ["filler", "material_reimburser", "asset_reimburser"]:
        flash("角色无效", "danger")
        return redirect(url_for("manage_users"))

    conn = get_db()
    conn.execute("UPDATE users SET role = ? WHERE id = ?", (new_role, user_id))
    conn.commit()
    conn.close()

    flash("角色更新成功", "success")
    return redirect(url_for("manage_users"))


@app.route("/admin/user/<int:user_id>/reset_password", methods=["POST"])
@login_required
@role_required("admin")
def reset_user_password(user_id):
    """重置用户密码"""
    conn = get_db()
    password_hash = generate_password_hash(ADMIN_INITIAL_PASSWORD)
    conn.execute(
        "UPDATE users SET password_hash = ?, must_change_password = 1 WHERE id = ?",
        (password_hash, user_id)
    )
    conn.commit()
    conn.close()

    flash(f"密码已重置为: {ADMIN_INITIAL_PASSWORD}", "success")
    return redirect(url_for("manage_users"))


@app.route("/admin/backup")
@login_required
@role_required("admin")
def create_backup():
    """创建备份"""
    try:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_dir = app.config["BACKUP_FOLDER"] / timestamp
        backup_dir.mkdir(parents=True, exist_ok=True)

        # 备份数据库
        shutil.copy2(app.config["DATABASE"], backup_dir / "invoice.db")

        # 备份附件
        shutil.copytree(app.config["UPLOAD_FOLDER"], backup_dir / "uploads")

        # 创建压缩包
        shutil.make_archive(str(backup_dir), "gztar", backup_dir)
        shutil.rmtree(backup_dir)

        app.logger.info(f"备份创建成功: {timestamp}.tar.gz - 操作人: {session['real_name']}")
        flash(f"备份创建成功: {timestamp}.tar.gz", "success")
    except Exception as e:
        app.logger.error(f"备份失败: {str(e)} - 操作人: {session['real_name']}")
        flash(f"备份失败: {str(e)}", "danger")

    return redirect(url_for("admin_dashboard"))


@app.route("/admin/export")
@login_required
@role_required("admin")
def export_data():
    """导出数据为Excel"""
    try:
        import csv
        from io import StringIO

        conn = get_db()

        # 导出发票数据
        invoices = conn.execute("""
            SELECT i.id, i.filler_name, i.reimburser_name, i.project_name,
                   i.invoice_company, i.purchase_reason, i.category, i.status,
                   i.submitted_at, i.created_at,
                   GROUP_CONCAT(it.item_name || '(' || it.quantity || ')') as items,
                   SUM(it.total_amount) as total_amount
            FROM invoices i
            LEFT JOIN invoice_items it ON i.id = it.invoice_id
            GROUP BY i.id
            ORDER BY i.created_at DESC
        """).fetchall()

        conn.close()

        # 生成CSV
        output = StringIO()
        writer = csv.writer(output)
        writer.writerow([
            "ID", "填报人", "报销者", "项目名称", "发票公司", "购买事由",
            "分类", "状态", "提交时间", "创建时间", "明细", "总金额"
        ])

        for inv in invoices:
            writer.writerow([
                inv["id"], inv["filler_name"], inv["reimburser_name"],
                inv["project_name"], inv["invoice_company"], inv["purchase_reason"],
                inv["category"], inv["status"], inv["submitted_at"],
                inv["created_at"], inv["items"], inv["total_amount"]
            ])

        output.seek(0)
        return Response(
            output.getvalue(),
            mimetype="text/csv",
            headers={"Content-Disposition": f"attachment;filename=invoices_{datetime.now().strftime('%Y%m%d')}.csv"}
        )

    except Exception as e:
        flash(f"导出失败: {str(e)}", "danger")
        return redirect(url_for("admin_dashboard"))


@app.route("/attachment/<int:attachment_id>")
@login_required
def download_attachment(attachment_id):
    """下载附件"""
    conn = get_db()
    attachment = conn.execute("SELECT * FROM attachments WHERE id = ?", (attachment_id,)).fetchone()
    conn.close()

    if not attachment:
        flash("附件不存在", "danger")
        return redirect(url_for("index"))

    file_path = Path(attachment["file_path"])
    if not file_path.exists():
        flash("文件不存在", "danger")
        return redirect(url_for("index"))

    return send_file(file_path, as_attachment=True)


# ============= HTML Templates =============
BASE_TEMPLATE = """
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{% block title %}发票报销系统{% endblock %}</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <link href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.10.0/font/bootstrap-icons.css" rel="stylesheet">
    <style>
        body { min-height: 100vh; background: #f8f9fa; }
        .navbar-brand { font-weight: bold; }
        .card { box-shadow: 0 0.125rem 0.25rem rgba(0,0,0,0.075); margin-bottom: 1.5rem; }
        .status-badge { font-size: 0.875rem; padding: 0.25rem 0.75rem; }
        .table-hover tbody tr:hover { background-color: rgba(0,0,0,0.02); }
    </style>
</head>
<body>
    {% if session.user_id %}
    <nav class="navbar navbar-expand-lg navbar-dark bg-primary">
        <div class="container-fluid">
            <a class="navbar-brand" href="{{ url_for('index') }}">
                <i class="bi bi-receipt"></i> 发票报销系统
            </a>
            <button class="navbar-toggler" type="button" data-bs-toggle="collapse" data-bs-target="#navbarNav">
                <span class="navbar-toggler-icon"></span>
            </button>
            <div class="collapse navbar-collapse" id="navbarNav">
                <ul class="navbar-nav me-auto">
                    <li class="nav-item">
                        <a class="nav-link" href="{{ url_for('filler_dashboard') }}">我的填报</a>
                    </li>
                    {% if session.role in ['material_reimburser', 'asset_reimburser', 'admin'] %}
                    <li class="nav-item">
                        <a class="nav-link" href="{{ url_for('reimburser_dashboard') }}">报销管理</a>
                    </li>
                    {% endif %}
                    {% if session.role == 'admin' %}
                    <li class="nav-item">
                        <a class="nav-link" href="{{ url_for('admin_dashboard') }}">系统管理</a>
                    </li>
                    {% endif %}
                </ul>
                <ul class="navbar-nav">
                    <li class="nav-item dropdown">
                        <a class="nav-link dropdown-toggle" href="#" role="button" data-bs-toggle="dropdown">
                            <i class="bi bi-person-circle"></i> {{ session.real_name }}
                        </a>
                        <ul class="dropdown-menu dropdown-menu-end">
                            <li><a class="dropdown-item" href="{{ url_for('change_password') }}">修改密码</a></li>
                            <li><hr class="dropdown-divider"></li>
                            <li><a class="dropdown-item" href="{{ url_for('logout') }}">退出登录</a></li>
                        </ul>
                    </li>
                </ul>
            </div>
        </div>
    </nav>
    {% endif %}

    <div class="container mt-4">
        {% with messages = get_flashed_messages(with_categories=true) %}
            {% if messages %}
                {% for category, message in messages %}
                <div class="alert alert-{{ category }} alert-dismissible fade show" role="alert">
                    {{ message }}
                    <button type="button" class="btn-close" data-bs-dismiss="alert"></button>
                </div>
                {% endfor %}
            {% endif %}
        {% endwith %}

        {% block content %}{% endblock %}
    </div>

    <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"></script>
    {% block scripts %}{% endblock %}
</body>
</html>
"""

LOGIN_TEMPLATE = BASE_TEMPLATE.replace("{% block content %}{% endblock %}", """
{% block content %}
<div class="row justify-content-center mt-5">
    <div class="col-md-4">
        <div class="card">
            <div class="card-body">
                <h3 class="card-title text-center mb-4">
                    <i class="bi bi-receipt"></i> 发票报销系统
                </h3>
                <form method="POST">
                    <div class="mb-3">
                        <label class="form-label">学号/工号</label>
                        <input type="text" class="form-control" name="username" required autofocus>
                    </div>
                    <div class="mb-3">
                        <label class="form-label">密码</label>
                        <input type="password" class="form-control" name="password" required>
                    </div>
                    <button type="submit" class="btn btn-primary w-100">登录</button>
                </form>
                <div class="text-center mt-3">
                    <a href="{{ url_for('register') }}" class="text-decoration-none">还没有账号？立即注册</a>
                </div>
            </div>
        </div>
    </div>
</div>
{% endblock %}
""")

CHANGE_PASSWORD_TEMPLATE = BASE_TEMPLATE.replace("{% block content %}{% endblock %}", """
{% block content %}
<div class="row justify-content-center">
    <div class="col-md-6">
        <div class="card">
            <div class="card-header">
                <h5 class="mb-0">修改密码</h5>
            </div>
            <div class="card-body">
                <form method="POST">
                    <div class="mb-3">
                        <label class="form-label">原密码</label>
                        <input type="password" class="form-control" name="old_password" required>
                    </div>
                    <div class="mb-3">
                        <label class="form-label">新密码</label>
                        <input type="password" class="form-control" name="new_password" required minlength="6">
                        <small class="text-muted">至少6位</small>
                    </div>
                    <div class="mb-3">
                        <label class="form-label">确认新密码</label>
                        <input type="password" class="form-control" name="confirm_password" required>
                    </div>
                    <button type="submit" class="btn btn-primary">确认修改</button>
                    {% if not session.must_change_password %}
                    <a href="{{ url_for('index') }}" class="btn btn-secondary">取消</a>
                    {% endif %}
                </form>
            </div>
        </div>
    </div>
</div>
{% endblock %}
""")

REGISTER_TEMPLATE = BASE_TEMPLATE.replace("{% block content %}{% endblock %}", """
{% block content %}
<div class="row justify-content-center mt-5">
    <div class="col-md-5">
        <div class="card">
            <div class="card-body">
                <h3 class="card-title text-center mb-4">
                    <i class="bi bi-person-plus"></i> 用户注册
                </h3>
                <form method="POST">
                    <div class="mb-3">
                        <label class="form-label">学号/工号 <span class="text-danger">*</span></label>
                        <input type="text" class="form-control" name="username" required autofocus>
                        <small class="text-muted">用于登录的唯一标识</small>
                    </div>
                    <div class="mb-3">
                        <label class="form-label">真实姓名 <span class="text-danger">*</span></label>
                        <input type="text" class="form-control" name="real_name" required>
                    </div>
                    <div class="mb-3">
                        <label class="form-label">密码 <span class="text-danger">*</span></label>
                        <input type="password" class="form-control" name="password" required minlength="6">
                        <small class="text-muted">至少6位</small>
                    </div>
                    <div class="mb-3">
                        <label class="form-label">确认密码 <span class="text-danger">*</span></label>
                        <input type="password" class="form-control" name="confirm_password" required>
                    </div>
                    <div class="alert alert-info">
                        <small><i class="bi bi-info-circle"></i> 注册后默认为填报人角色，如需报账权限请联系管理员</small>
                    </div>
                    <button type="submit" class="btn btn-primary w-100">注册</button>
                </form>
                <div class="text-center mt-3">
                    <a href="{{ url_for('login') }}" class="text-decoration-none">已有账号？立即登录</a>
                </div>
            </div>
        </div>
    </div>
</div>
{% endblock %}
""")

FILLER_DASHBOARD_TEMPLATE = BASE_TEMPLATE.replace("{% block content %}{% endblock %}", """
{% block content %}
<h2 class="mb-4">我的填报</h2>

<div class="row mb-4">
    <div class="col-md-3">
        <div class="card text-center">
            <div class="card-body">
                <h3 class="text-secondary">{{ stats.draft }}</h3>
                <p class="mb-0">草稿</p>
            </div>
        </div>
    </div>
    <div class="col-md-3">
        <div class="card text-center">
            <div class="card-body">
                <h3 class="text-warning">{{ stats.pending_check + stats.pending_material + stats.pending_asset }}</h3>
                <p class="mb-0">待处理</p>
            </div>
        </div>
    </div>
    <div class="col-md-3">
        <div class="card text-center">
            <div class="card-body">
                <h3 class="text-success">{{ stats.reimbursed }}</h3>
                <p class="mb-0">已报销</p>
            </div>
        </div>
    </div>
    <div class="col-md-3">
        <div class="card text-center">
            <div class="card-body">
                <h3 class="text-danger">{{ stats.rejected }}</h3>
                <p class="mb-0">已驳回</p>
            </div>
        </div>
    </div>
</div>

<div class="mb-3">
    <a href="{{ url_for('create_invoice') }}" class="btn btn-primary">
        <i class="bi bi-plus-circle"></i> 新建报销单
    </a>
</div>

<div class="card">
    <div class="card-header">
        <h5 class="mb-0">最近提交</h5>
    </div>
    <div class="card-body">
        <table class="table table-hover">
            <thead>
                <tr>
                    <th>ID</th>
                    <th>项目名称</th>
                    <th>分类</th>
                    <th>状态</th>
                    <th>提交时间</th>
                    <th>操作</th>
                </tr>
            </thead>
            <tbody>
                {% for inv in recent %}
                <tr>
                    <td>{{ inv.id }}</td>
                    <td>{{ inv.project_name }}</td>
                    <td>
                        {% set cats = inv.category.split(',') if inv.category else [] %}
                        {% for cat in cats %}
                            {% if cat == 'material' %}<span class="badge bg-secondary">材料</span>
                            {% elif cat == 'low_value' %}<span class="badge bg-info">低值品</span>
                            {% elif cat == 'asset' %}<span class="badge bg-warning">资产</span>
                            {% endif %}
                        {% endfor %}
                    </td>
                    <td>
                        {% if inv.status == 'draft' %}
                        <span class="badge bg-secondary">草稿</span>
                        {% elif inv.status == 'pending_check' %}
                        <span class="badge bg-warning">待验收</span>
                        {% elif inv.status == 'pending_material' %}
                        <span class="badge bg-info">待材料报销</span>
                        {% elif inv.status == 'pending_asset' %}
                        <span class="badge bg-info">待资产报销</span>
                        {% elif inv.status == 'reimbursed' %}
                        <span class="badge bg-success">已报销</span>
                        {% elif inv.status == 'rejected' %}
                        <span class="badge bg-danger">已驳回</span>
                        {% endif %}
                    </td>
                    <td>{{ inv.submitted_at or inv.created_at }}</td>
                    <td>
                        <a href="{{ url_for('view_invoice', invoice_id=inv.id) }}" class="btn btn-sm btn-outline-primary">查看</a>
                    </td>
                </tr>
                {% endfor %}
            </tbody>
        </table>
    </div>
</div>
{% endblock %}
""")

CREATE_INVOICE_TEMPLATE = BASE_TEMPLATE.replace("{% block content %}{% endblock %}", """
{% block content %}
<h2 class="mb-4">新建报销单</h2>

<form method="POST" enctype="multipart/form-data">
    <div class="card mb-3">
        <div class="card-header">
            <h5 class="mb-0">基本信息</h5>
        </div>
        <div class="card-body">
            <div class="row">
                <div class="col-md-6 mb-3">
                    <label class="form-label">报销者姓名 <span class="text-danger">*</span></label>
                    <input type="text" class="form-control" name="reimburser_name" required>
                </div>
                <div class="col-md-6 mb-3">
                    <label class="form-label">项目名称 <span class="text-danger">*</span></label>
                    <input type="text" class="form-control" name="project_name" required>
                </div>
            </div>
            <div class="row">
                <div class="col-md-6 mb-3">
                    <label class="form-label">发票公司（总金额>1000需填）</label>
                    <input type="text" class="form-control" name="invoice_company">
                </div>
                <div class="col-md-6 mb-3">
                    <label class="form-label">购买事由</label>
                    <input type="text" class="form-control" name="purchase_reason">
                </div>
            </div>
        </div>
    </div>

    <div class="card mb-3">
        <div class="card-header d-flex justify-content-between align-items-center">
            <h5 class="mb-0">发票明细</h5>
            <button type="button" class="btn btn-sm btn-success" onclick="addItem()">
                <i class="bi bi-plus"></i> 添加明细
            </button>
        </div>
        <div class="card-body">
            <div id="items-container">
                <div class="item-row border p-3 mb-3 rounded">
                    <div class="row">
                        <div class="col-md-3 mb-2">
                            <label class="form-label">名称 <span class="text-danger">*</span></label>
                            <input type="text" class="form-control" name="item_name[]" required>
                        </div>
                        <div class="col-md-2 mb-2">
                            <label class="form-label">单价 <span class="text-danger">*</span></label>
                            <input type="number" step="0.01" class="form-control" name="unit_price[]" required>
                        </div>
                        <div class="col-md-2 mb-2">
                            <label class="form-label">数量 <span class="text-danger">*</span></label>
                            <input type="number" class="form-control" name="quantity[]" required value="1">
                        </div>
                        <div class="col-md-2 mb-2">
                            <label class="form-label">发票号后四位</label>
                            <input type="text" class="form-control" name="invoice_number[]" maxlength="4">
                        </div>
                        <div class="col-md-3 mb-2">
                            <label class="form-label">支付记录</label>
                            <select class="form-select" name="payment_record[]">
                                <option value="不需要">不需要</option>
                                <option value="需要">需要</option>
                            </select>
                        </div>
                    </div>
                    <div class="row">
                        <div class="col-md-3 mb-2">
                            <label class="form-label">实物图</label>
                            <select class="form-select" name="physical_photo[]">
                                <option value="不需要">不需要</option>
                                <option value="需要">需要</option>
                            </select>
                        </div>
                        <div class="col-md-9 mb-2">
                            <label class="form-label">备注</label>
                            <input type="text" class="form-control" name="notes[]">
                        </div>
                    </div>
                </div>
            </div>
        </div>
    </div>

    <div class="card mb-3">
        <div class="card-header">
            <h5 class="mb-0">支付信息</h5>
        </div>
        <div class="card-body">
            <div class="row">
                <div class="col-md-6 mb-3">
                    <label class="form-label">支付方式 <span class="text-danger">*</span></label>
                    <select class="form-control" name="payment_method" id="payment-method" required onchange="updateAttachmentRequirements()">
                        <option value="">请选择</option>
                        <option value="corporate_transfer">对公转账</option>
                        <option value="official_card">公务卡支付</option>
                        <option value="personal_payment">个人支付</option>
                    </select>
                </div>
            </div>
        </div>
    </div>

    <div class="card mb-3">
        <div class="card-header">
            <h5 class="mb-0">附件上传</h5>
        </div>
        <div class="card-body">
            <div class="mb-3">
                <label class="form-label">发票文件 <span class="text-danger">*</span></label>
                <input type="file" class="form-control" name="invoice_file" multiple accept="image/*,.pdf" required>
                <small class="text-muted">发票扫描件或照片，支持多文件</small>
            </div>
            <div class="mb-3" id="physical-photo-section" style="display:none;">
                <label class="form-label">实物图 <span class="text-danger" id="physical-required">*</span></label>
                <input type="file" class="form-control" name="physical_photo" multiple accept="image/*">
                <small class="text-muted">物品实物照片</small>
            </div>
            <div class="mb-3" id="order-screenshot-section" style="display:none;">
                <label class="form-label">订单截图 <span class="text-danger">*</span></label>
                <input type="file" class="form-control" name="order_screenshot" multiple accept="image/*,.pdf">
                <small class="text-muted">电商订单截图</small>
            </div>
            <div class="mb-3" id="payment-record-section" style="display:none;">
                <label class="form-label">支付记录 <span class="text-danger">*</span></label>
                <input type="file" class="form-control" name="payment_record" multiple accept="image/*,.pdf">
                <small class="text-muted">支付凭证截图</small>
            </div>
            <div id="attachment-hint" class="alert alert-info d-none">
                <small><i class="bi bi-info-circle"></i> <span id="hint-text"></span></small>
            </div>
            <div id="payment-warning" class="alert alert-danger d-none">
                <small><i class="bi bi-exclamation-triangle"></i> <span id="warning-text"></span></small>
            </div>
        </div>
    </div>

    <div class="mb-3">
        <button type="submit" name="action" value="draft" class="btn btn-secondary">保存草稿</button>
        <button type="submit" name="action" value="submit" class="btn btn-primary">提交</button>
        <a href="{{ url_for('filler_dashboard') }}" class="btn btn-outline-secondary">取消</a>
    </div>
</form>

<script>
function addItem() {
    const container = document.getElementById('items-container');
    const template = container.querySelector('.item-row').cloneNode(true);
    template.querySelectorAll('input').forEach(input => input.value = input.name === 'quantity[]' ? '1' : '');
    template.querySelectorAll('select').forEach(select => select.selectedIndex = 0);
    container.appendChild(template);
    updateAttachmentRequirements();
}

function getMaxUnitPrice() {
    const prices = document.querySelectorAll('input[name="unit_price[]"]');
    let maxPrice = 0;
    prices.forEach(input => {
        const val = parseFloat(input.value) || 0;
        if (val > maxPrice) maxPrice = val;
    });
    return maxPrice;
}

function getTotalAmount() {
    const prices = document.querySelectorAll('input[name="unit_price[]"]');
    const quantities = document.querySelectorAll('input[name="quantity[]"]');
    let total = 0;
    prices.forEach((input, i) => {
        const price = parseFloat(input.value) || 0;
        const qty = parseInt(quantities[i]?.value) || 1;
        total += price * qty;
    });
    return total;
}

function updateAttachmentRequirements() {
    const maxPrice = getMaxUnitPrice();
    const totalAmount = getTotalAmount();
    const paymentMethod = document.getElementById('payment-method').value;

    // 分类规则：<500材料，500-1000低值品，>=1000资产
    const isLowValue = maxPrice >= 500 && maxPrice < 1000;
    const isAsset = maxPrice >= 1000;

    // 实物图：低值品和资产需要
    const physicalSection = document.getElementById('physical-photo-section');
    if (isLowValue || isAsset) {
        physicalSection.style.display = '';
    } else {
        physicalSection.style.display = 'none';
    }

    // 订单截图和支付记录：公务卡支付时需要
    const orderSection = document.getElementById('order-screenshot-section');
    const paymentSection = document.getElementById('payment-record-section');
    if (paymentMethod === 'official_card') {
        orderSection.style.display = '';
        paymentSection.style.display = '';
    } else {
        orderSection.style.display = 'none';
        paymentSection.style.display = 'none';
    }

    // 提示信息
    const hint = document.getElementById('attachment-hint');
    const hintText = document.getElementById('hint-text');
    const hints = [];

    if (isLowValue) {
        hints.push('低值品：需上传实物图');
    } else if (isAsset) {
        hints.push('资产：需上传实物图');
    }

    if (paymentMethod === 'official_card') {
        hints.push('公务卡支付：需上传订单截图和支付记录');
    }

    if (hints.length > 0) {
        hintText.textContent = hints.join('；');
        hint.classList.remove('d-none');
    } else {
        hint.classList.add('d-none');
    }

    // 支付方式警告
    const warning = document.getElementById('payment-warning');
    const warningText = document.getElementById('warning-text');

    if (totalAmount > 3000 && paymentMethod === 'personal_payment') {
        warningText.textContent = '个人支付总额超过3000元，必须使用公务卡支付，不能自行垫付';
        warning.classList.remove('d-none');
    } else {
        warning.classList.add('d-none');
    }
}

document.addEventListener('input', function(e) {
    if (e.target.name === 'unit_price[]' || e.target.name === 'quantity[]') {
        updateAttachmentRequirements();
    }
});
</script>
{% endblock %}
""")

EDIT_INVOICE_TEMPLATE = BASE_TEMPLATE.replace("{% block content %}{% endblock %}", """
{% block content %}
<h2 class="mb-4">编辑草稿 #{{ invoice.id }}</h2>

<form method="POST" enctype="multipart/form-data">
    <div class="card mb-3">
        <div class="card-header">
            <h5 class="mb-0">基本信息</h5>
        </div>
        <div class="card-body">
            <div class="row">
                <div class="col-md-6 mb-3">
                    <label class="form-label">报销者姓名 <span class="text-danger">*</span></label>
                    <input type="text" class="form-control" name="reimburser_name"
                           value="{{ invoice.reimburser_name }}" required>
                </div>
                <div class="col-md-6 mb-3">
                    <label class="form-label">项目名称 <span class="text-danger">*</span></label>
                    <input type="text" class="form-control" name="project_name"
                           value="{{ invoice.project_name }}" required>
                </div>
            </div>
            <div class="row">
                <div class="col-md-6 mb-3">
                    <label class="form-label">发票公司</label>
                    <input type="text" class="form-control" name="invoice_company"
                           value="{{ invoice.invoice_company or '' }}">
                </div>
                <div class="col-md-6 mb-3">
                    <label class="form-label">购买事由</label>
                    <textarea class="form-control" name="purchase_reason" rows="2">{{ invoice.purchase_reason or '' }}</textarea>
                </div>
            </div>
            <div class="row">
                <div class="col-md-6 mb-3">
                    <label class="form-label">支付方式 <span class="text-danger">*</span></label>
                    <select class="form-control" name="payment_method" id="payment-method" required onchange="updateAttachmentRequirements()">
                        <option value="">请选择</option>
                        <option value="corporate_transfer" {% if invoice.payment_method == 'corporate_transfer' %}selected{% endif %}>对公转账</option>
                        <option value="official_card" {% if invoice.payment_method == 'official_card' %}selected{% endif %}>公务卡支付</option>
                        <option value="personal_payment" {% if invoice.payment_method == 'personal_payment' %}selected{% endif %}>个人支付</option>
                    </select>
                </div>
            </div>
        </div>
    </div>

    <div class="card mb-3">
        <div class="card-header d-flex justify-content-between align-items-center">
            <h5 class="mb-0">发票明细</h5>
            <button type="button" class="btn btn-sm btn-primary" onclick="addItem()">
                <i class="bi bi-plus"></i> 添加明细
            </button>
        </div>
        <div class="card-body">
            <div id="items-container">
                {% for item in items %}
                <div class="item-row border-bottom pb-3 mb-3">
                    <div class="row">
                        <div class="col-md-3 mb-2">
                            <label class="form-label">物品名称 <span class="text-danger">*</span></label>
                            <input type="text" class="form-control" name="item_name[]"
                                   value="{{ item.item_name }}" required>
                        </div>
                        <div class="col-md-2 mb-2">
                            <label class="form-label">单价 <span class="text-danger">*</span></label>
                            <input type="number" class="form-control" name="unit_price[]"
                                   value="{{ item.unit_price }}" step="0.01" required>
                        </div>
                        <div class="col-md-2 mb-2">
                            <label class="form-label">数量 <span class="text-danger">*</span></label>
                            <input type="number" class="form-control" name="quantity[]"
                                   value="{{ item.quantity }}" required>
                        </div>
                        <div class="col-md-2 mb-2">
                            <label class="form-label">发票号</label>
                            <input type="text" class="form-control" name="invoice_number[]"
                                   value="{{ item.invoice_number or '' }}">
                        </div>
                        <div class="col-md-3 mb-2">
                            <label class="form-label">付款记录</label>
                            <input type="text" class="form-control" name="payment_record[]"
                                   value="{{ item.payment_record or '' }}">
                        </div>
                    </div>
                    <div class="row">
                        <div class="col-md-6 mb-2">
                            <label class="form-label">实物照片</label>
                            <input type="text" class="form-control" name="physical_photo[]"
                                   value="{{ item.physical_photo or '' }}">
                        </div>
                        <div class="col-md-6 mb-2">
                            <label class="form-label">备注</label>
                            <input type="text" class="form-control" name="notes[]"
                                   value="{{ item.notes or '' }}">
                        </div>
                    </div>
                </div>
                {% endfor %}
            </div>
        </div>
    </div>

    <div class="mb-3">
        <button type="submit" name="action" value="draft" class="btn btn-secondary">保存草稿</button>
        <button type="submit" name="action" value="submit" class="btn btn-primary">提交</button>
        <a href="{{ url_for('view_invoice', invoice_id=invoice.id) }}" class="btn btn-outline-secondary">取消</a>
    </div>
</form>

<script>
function addItem() {
    const container = document.getElementById('items-container');
    const template = container.querySelector('.item-row').cloneNode(true);
    template.querySelectorAll('input').forEach(input => input.value = input.name === 'quantity[]' ? '1' : '');
    container.appendChild(template);
}
</script>
{% endblock %}
""")

VIEW_INVOICE_TEMPLATE = BASE_TEMPLATE.replace("{% block content %}{% endblock %}", """
{% block content %}
<div class="d-flex justify-content-between align-items-center mb-4">
    <h2>报销单详情 #{{ invoice.id }}</h2>
    <div>
        {% if can_edit %}
        <a href="{{ url_for('edit_invoice', invoice_id=invoice.id) }}" class="btn btn-primary">
            <i class="bi bi-pencil"></i> 编辑
        </a>
        <form method="POST" action="{{ url_for('delete_invoice', invoice_id=invoice.id) }}" class="d-inline"
              onsubmit="return confirm('确定要删除吗？');">
            <button type="submit" class="btn btn-danger">
                <i class="bi bi-trash"></i> 删除
            </button>
        </form>
        {% endif %}
        {% if can_withdraw %}
        <form method="POST" action="{{ url_for('withdraw_invoice', invoice_id=invoice.id) }}" class="d-inline"
              onsubmit="return confirm('撤回后将变为草稿状态，需要重新提交。确定要撤回吗？');">
            <button type="submit" class="btn btn-warning">
                <i class="bi bi-arrow-counterclockwise"></i> 撤回
            </button>
        </form>
        {% endif %}
        <a href="{{ url_for('filler_dashboard') }}" class="btn btn-outline-secondary">返回</a>
    </div>
</div>

<div class="card mb-3">
    <div class="card-header">
        <h5 class="mb-0">基本信息</h5>
    </div>
    <div class="card-body">
        <div class="row">
            <div class="col-md-6">
                <p><strong>填报人:</strong> {{ invoice.filler_name }}</p>
                <p><strong>报销者:</strong> {{ invoice.reimburser_name }}</p>
                <p><strong>项目名称:</strong> {{ invoice.project_name }}</p>
                <p><strong>发票公司:</strong> {{ invoice.invoice_company or '无' }}</p>
            </div>
            <div class="col-md-6">
                <p><strong>购买事由:</strong> {{ invoice.purchase_reason or '无' }}</p>
                <p><strong>支付方式:</strong>
                    {% if invoice.payment_method == 'corporate_transfer' %}对公转账
                    {% elif invoice.payment_method == 'official_card' %}公务卡支付
                    {% elif invoice.payment_method == 'personal_payment' %}个人支付
                    {% else %}未填写{% endif %}
                </p>
                <p><strong>分类:</strong>
                    {% set cats = invoice.category.split(',') if invoice.category else [] %}
                    {% for cat in cats %}
                        {% if cat == 'material' %}<span class="badge bg-secondary">材料</span>
                        {% elif cat == 'low_value' %}<span class="badge bg-info">低值品</span>
                        {% elif cat == 'asset' %}<span class="badge bg-warning">资产</span>
                        {% endif %}
                    {% endfor %}
                </p>
                <p><strong>状态:</strong>
                    {% if invoice.status == 'draft' %}
                    <span class="badge bg-secondary">草稿</span>
                    {% elif invoice.status == 'pending_check' %}
                    <span class="badge bg-warning">待验收</span>
                    {% elif invoice.status == 'pending_material' %}
                    <span class="badge bg-info">待材料报销</span>
                    {% elif invoice.status == 'pending_asset' %}
                    <span class="badge bg-info">待资产报销</span>
                    {% elif invoice.status == 'reimbursed' %}
                    <span class="badge bg-success">已报销</span>
                    {% elif invoice.status == 'rejected' %}
                    <span class="badge bg-danger">已驳回</span>
                    {% endif %}
                </p>
                <p><strong>提交时间:</strong> {{ invoice.submitted_at or '未提交' }}</p>
            </div>
        </div>
    </div>
</div>

<div class="card mb-3">
    <div class="card-header">
        <h5 class="mb-0">发票明细</h5>
    </div>
    <div class="card-body">
        <table class="table">
            <thead>
                <tr>
                    <th>名称</th>
                    <th>单价</th>
                    <th>数量</th>
                    <th>总金额</th>
                    <th>发票号</th>
                    <th>备注</th>
                </tr>
            </thead>
            <tbody>
                {% for item in items %}
                <tr>
                    <td>{{ item.item_name }}</td>
                    <td>{{ item.unit_price }}</td>
                    <td>{{ item.quantity }}</td>
                    <td>{{ item.total_amount }}</td>
                    <td>{{ item.invoice_number or '-' }}</td>
                    <td>{{ item.notes or '-' }}</td>
                </tr>
                {% endfor %}
            </tbody>
        </table>
    </div>
</div>

{% if attachments %}
<div class="card mb-3">
    <div class="card-header">
        <h5 class="mb-0">附件</h5>
    </div>
    <div class="card-body">
        {% set att_categories = {'invoice_file': '发票文件', 'physical_photo': '实物图', 'order_screenshot': '订单截图', 'payment_record': '支付记录'} %}
        {% for cat_key, cat_name in att_categories.items() %}
            {% set cat_files = attachments|selectattr('category', 'equalto', cat_key)|list %}
            {% if cat_files %}
            <div class="mb-2">
                <strong>{{ cat_name }}：</strong>
                {% for att in cat_files %}
                <a href="{{ url_for('download_attachment', attachment_id=att.id) }}" class="btn btn-sm btn-outline-primary mb-1">
                    <i class="bi bi-download"></i> {{ cat_name }}{{ loop.index }}
                </a>
                {% endfor %}
            </div>
            {% endif %}
        {% endfor %}
        {% set other_files = attachments|rejectattr('category', 'in', att_categories.keys())|list %}
        {% if other_files %}
        <div class="mb-2">
            <strong>其他：</strong>
            {% for att in other_files %}
            <a href="{{ url_for('download_attachment', attachment_id=att.id) }}" class="btn btn-sm btn-outline-primary mb-1">
                <i class="bi bi-download"></i> 附件{{ loop.index }}
            </a>
            {% endfor %}
        </div>
        {% endif %}
    </div>
</div>
{% endif %}

{% if check_report %}
<div class="card mb-3">
    <div class="card-header">
        <h5 class="mb-0">验收报告</h5>
    </div>
    <div class="card-body">
        <p><strong>验收人:</strong> {{ check_report.checker_name }}</p>
        <p><strong>验收时间:</strong> {{ check_report.checked_at }}</p>
        <p><strong>报告内容:</strong> {{ check_report.report_content or '无' }}</p>
    </div>
</div>
{% endif %}

<div class="card">
    <div class="card-header">
        <h5 class="mb-0">操作历史</h5>
    </div>
    <div class="card-body">
        <table class="table table-sm">
            <thead>
                <tr>
                    <th>时间</th>
                    <th>操作人</th>
                    <th>操作</th>
                    <th>状态变更</th>
                    <th>备注</th>
                </tr>
            </thead>
            <tbody>
                {% for h in history %}
                <tr>
                    <td>{{ h.created_at }}</td>
                    <td>{{ h.operator_name }}</td>
                    <td>{{ h.action }}</td>
                    <td>{{ h.old_status or '-' }} → {{ h.new_status or '-' }}</td>
                    <td>{{ h.notes or '-' }}</td>
                </tr>
                {% endfor %}
            </tbody>
        </table>
    </div>
</div>
{% endblock %}
""")

REIMBURSER_DASHBOARD_TEMPLATE = BASE_TEMPLATE.replace("{% block content %}{% endblock %}", """
{% block content %}
<h2 class="mb-4">报销管理</h2>

<ul class="nav nav-tabs mb-3" role="tablist">
    <li class="nav-item">
        <a class="nav-link active" data-bs-toggle="tab" href="#my-pending">我的待办 ({{ my_pending|length }})</a>
    </li>
    <li class="nav-item">
        <a class="nav-link" data-bs-toggle="tab" href="#all-pending">全部待办 ({{ all_pending|length }})</a>
    </li>
    <li class="nav-item">
        <a class="nav-link" data-bs-toggle="tab" href="#history">历史记录 ({{ my_history|length }})</a>
    </li>
</ul>

<div class="tab-content">
    <div class="tab-pane fade show active" id="my-pending">
        <div class="card">
            <div class="card-body">
                {% if my_pending %}
                <table class="table table-hover">
                    <thead>
                        <tr>
                            <th>ID</th>
                            <th>项目名称</th>
                            <th>填报人</th>
                            <th>报销者</th>
                            <th>总金额</th>
                            <th>分类</th>
                            <th>状态</th>
                            <th>提交时间</th>
                            <th>操作</th>
                        </tr>
                    </thead>
                    <tbody>
                        {% for inv in my_pending %}
                        <tr>
                            <td>{{ inv.id }}</td>
                            <td>{{ inv.project_name }}</td>
                            <td>{{ inv.filler_name }}</td>
                            <td>{{ inv.reimburser_name }}</td>
                            <td>{{ inv.total_amount }}</td>
                            <td>
                                {% set cats = inv.category.split(',') if inv.category else [] %}
                                {% for cat in cats %}
                                    {% if cat == 'material' %}<span class="badge bg-secondary">材料</span>
                                    {% elif cat == 'low_value' %}<span class="badge bg-info">低值品</span>
                                    {% elif cat == 'asset' %}<span class="badge bg-warning">资产</span>
                                    {% endif %}
                                {% endfor %}
                            </td>
                            <td>
                                {% if inv.status == 'pending_check' %}
                                <span class="badge bg-warning">待验收</span>
                                {% elif inv.status == 'pending_material' %}
                                <span class="badge bg-info">待材料报销</span>
                                {% elif inv.status == 'pending_asset' %}
                                <span class="badge bg-info">待资产报销</span>
                                {% endif %}
                            </td>
                            <td>{{ inv.submitted_at }}</td>
                            <td>
                                <a href="{{ url_for('handle_invoice', invoice_id=inv.id) }}" class="btn btn-sm btn-primary">处理</a>
                            </td>
                        </tr>
                        {% endfor %}
                    </tbody>
                </table>
                {% else %}
                <p class="text-muted text-center py-4">暂无待办事项</p>
                {% endif %}
            </div>
        </div>
    </div>

    <div class="tab-pane fade" id="all-pending">
        <div class="card">
            <div class="card-body">
                {% if all_pending %}
                <table class="table table-hover">
                    <thead>
                        <tr>
                            <th>ID</th>
                            <th>项目名称</th>
                            <th>填报人</th>
                            <th>报销者</th>
                            <th>总金额</th>
                            <th>分类</th>
                            <th>状态</th>
                            <th>提交时间</th>
                            <th>操作</th>
                        </tr>
                    </thead>
                    <tbody>
                        {% for inv in all_pending %}
                        <tr>
                            <td>{{ inv.id }}</td>
                            <td>{{ inv.project_name }}</td>
                            <td>{{ inv.filler_name }}</td>
                            <td>{{ inv.reimburser_name }}</td>
                            <td>{{ inv.total_amount }}</td>
                            <td>
                                {% set cats = inv.category.split(',') if inv.category else [] %}
                                {% for cat in cats %}
                                    {% if cat == 'material' %}<span class="badge bg-secondary">材料</span>
                                    {% elif cat == 'low_value' %}<span class="badge bg-info">低值品</span>
                                    {% elif cat == 'asset' %}<span class="badge bg-warning">资产</span>
                                    {% endif %}
                                {% endfor %}
                            </td>
                            <td>
                                {% if inv.status == 'pending_check' %}
                                <span class="badge bg-warning">待验收</span>
                                {% elif inv.status == 'pending_material' %}
                                <span class="badge bg-info">待材料报销</span>
                                {% elif inv.status == 'pending_asset' %}
                                <span class="badge bg-info">待资产报销</span>
                                {% endif %}
                            </td>
                            <td>{{ inv.submitted_at }}</td>
                            <td>
                                <a href="{{ url_for('handle_invoice', invoice_id=inv.id) }}" class="btn btn-sm btn-primary">查看</a>
                            </td>
                        </tr>
                        {% endfor %}
                    </tbody>
                </table>
                {% else %}
                <p class="text-muted text-center py-4">暂无待办事项</p>
                {% endif %}
            </div>
        </div>
    </div>

    <div class="tab-pane fade" id="history">
        <div class="card">
            <div class="card-body">
                {% if my_history %}
                <table class="table table-hover">
                    <thead>
                        <tr>
                            <th>ID</th>
                            <th>项目名称</th>
                            <th>填报人</th>
                            <th>报销者</th>
                            <th>总金额</th>
                            <th>分类</th>
                            <th>状态</th>
                            <th>更新时间</th>
                            <th>操作</th>
                        </tr>
                    </thead>
                    <tbody>
                        {% for inv in my_history %}
                        <tr>
                            <td>{{ inv.id }}</td>
                            <td>{{ inv.project_name }}</td>
                            <td>{{ inv.filler_name }}</td>
                            <td>{{ inv.reimburser_name }}</td>
                            <td>{{ inv.total_amount }}</td>
                            <td>
                                {% set cats = inv.category.split(',') if inv.category else [] %}
                                {% for cat in cats %}
                                    {% if cat == 'material' %}<span class="badge bg-secondary">材料</span>
                                    {% elif cat == 'low_value' %}<span class="badge bg-info">低值品</span>
                                    {% elif cat == 'asset' %}<span class="badge bg-warning">资产</span>
                                    {% endif %}
                                {% endfor %}
                            </td>
                            <td>
                                {% if inv.status == 'reimbursed' %}
                                <span class="badge bg-success">已报销</span>
                                {% elif inv.status == 'rejected' %}
                                <span class="badge bg-danger">已驳回</span>
                                {% endif %}
                            </td>
                            <td>{{ inv.updated_at }}</td>
                            <td>
                                <a href="{{ url_for('handle_invoice', invoice_id=inv.id) }}" class="btn btn-sm btn-outline-primary">查看</a>
                            </td>
                        </tr>
                        {% endfor %}
                    </tbody>
                </table>
                {% else %}
                <p class="text-muted text-center py-4">暂无历史记录</p>
                {% endif %}
            </div>
        </div>
    </div>
</div>
{% endblock %}
""")

HANDLE_INVOICE_TEMPLATE = BASE_TEMPLATE.replace("{% block content %}{% endblock %}", """
{% block content %}
<div class="d-flex justify-content-between align-items-center mb-4">
    <h2>处理报销单 #{{ invoice.id }}</h2>
    <a href="{{ url_for('reimburser_dashboard') }}" class="btn btn-outline-secondary">返回</a>
</div>

<div class="card mb-3">
    <div class="card-header">
        <h5 class="mb-0">基本信息</h5>
    </div>
    <div class="card-body">
        <div class="row">
            <div class="col-md-6">
                <p><strong>填报人:</strong> {{ invoice.filler_name }}</p>
                <p><strong>报销者:</strong> {{ invoice.reimburser_name }}</p>
                <p><strong>项目名称:</strong> {{ invoice.project_name }}</p>
            </div>
            <div class="col-md-6">
                <p><strong>分类:</strong>
                    {% set cats = invoice.category.split(',') if invoice.category else [] %}
                    {% for cat in cats %}
                        {% if cat == 'material' %}<span class="badge bg-secondary">材料</span>
                        {% elif cat == 'low_value' %}<span class="badge bg-info">低值品</span>
                        {% elif cat == 'asset' %}<span class="badge bg-warning">资产</span>
                        {% endif %}
                    {% endfor %}
                </p>
                <p><strong>当前状态:</strong>
                    {% if invoice.status == 'pending_check' %}
                    <span class="badge bg-warning">待验收</span>
                    {% elif invoice.status == 'pending_material' %}
                    <span class="badge bg-info">待材料报销</span>
                    {% elif invoice.status == 'pending_asset' %}
                    <span class="badge bg-info">待资产报销</span>
                    {% endif %}
                </p>
            </div>
        </div>
    </div>
</div>

<div class="card mb-3">
    <div class="card-header">
        <h5 class="mb-0">发票明细</h5>
    </div>
    <div class="card-body">
        <table class="table">
            <thead>
                <tr>
                    <th>名称</th>
                    <th>单价</th>
                    <th>数量</th>
                    <th>总金额</th>
                    <th>发票号</th>
                </tr>
            </thead>
            <tbody>
                {% for item in items %}
                <tr>
                    <td>{{ item.item_name }}</td>
                    <td>{{ item.unit_price }}</td>
                    <td>{{ item.quantity }}</td>
                    <td>{{ item.total_amount }}</td>
                    <td>{{ item.invoice_number or '-' }}</td>
                </tr>
                {% endfor %}
            </tbody>
        </table>
    </div>
</div>

{% if attachments %}
<div class="card mb-3">
    <div class="card-header">
        <h5 class="mb-0">附件</h5>
    </div>
    <div class="card-body">
        {% set att_categories = {'invoice_file': '发票文件', 'physical_photo': '实物图', 'order_screenshot': '订单截图', 'payment_record': '支付记录'} %}
        {% for cat_key, cat_name in att_categories.items() %}
            {% set cat_files = attachments|selectattr('category', 'equalto', cat_key)|list %}
            {% if cat_files %}
            <div class="mb-2">
                <strong>{{ cat_name }}：</strong>
                {% for att in cat_files %}
                <a href="{{ url_for('download_attachment', attachment_id=att.id) }}" class="btn btn-sm btn-outline-primary mb-1">
                    <i class="bi bi-download"></i> {{ cat_name }}{{ loop.index }}
                </a>
                {% endfor %}
            </div>
            {% endif %}
        {% endfor %}
        {% set other_files = attachments|rejectattr('category', 'in', att_categories.keys())|list %}
        {% if other_files %}
        <div class="mb-2">
            <strong>其他：</strong>
            {% for att in other_files %}
            <a href="{{ url_for('download_attachment', attachment_id=att.id) }}" class="btn btn-sm btn-outline-primary mb-1">
                <i class="bi bi-download"></i> 附件{{ loop.index }}
            </a>
            {% endfor %}
        </div>
        {% endif %}
    </div>
</div>
{% endif %}

<div class="card mb-3">
    <div class="card-header">
        <h5 class="mb-0">操作</h5>
    </div>
    <div class="card-body">
        <form method="POST" enctype="multipart/form-data">
            {% if invoice.status == 'pending_check' and session.role in ['asset_reimburser', 'admin'] %}
            <div class="mb-3">
                <label class="form-label">验收报告内容</label>
                <textarea class="form-control" name="report_content" rows="3"></textarea>
            </div>
            <div class="mb-3">
                <label class="form-label">验收报告附件</label>
                <input type="file" class="form-control" name="report_file" accept=".pdf,.jpg,.jpeg,.png">
            </div>
            <div class="mb-3">
                <label class="form-label">备注</label>
                <input type="text" class="form-control" name="notes">
            </div>
            <button type="submit" name="action" value="approve_check" class="btn btn-success">验收通过</button>
            <button type="submit" name="action" value="reject" class="btn btn-danger">驳回</button>
            {% elif invoice.status == 'pending_material' and session.role in ['material_reimburser', 'admin'] %}
            <div class="mb-3">
                <label class="form-label">备注</label>
                <input type="text" class="form-control" name="notes">
            </div>
            <button type="submit" name="action" value="approve_reimburse" class="btn btn-success">确认报销</button>
            <button type="submit" name="action" value="reject" class="btn btn-danger">驳回</button>
            {% elif invoice.status == 'pending_asset' and session.role in ['asset_reimburser', 'admin'] %}
            <div class="mb-3">
                <label class="form-label">备注</label>
                <input type="text" class="form-control" name="notes">
            </div>
            <button type="submit" name="action" value="approve_reimburse" class="btn btn-success">确认报销</button>
            <button type="submit" name="action" value="reject" class="btn btn-danger">驳回</button>
            {% else %}
            <p class="text-muted">当前状态无法操作</p>
            {% endif %}
        </form>
    </div>
</div>

<div class="card">
    <div class="card-header">
        <h5 class="mb-0">操作历史</h5>
    </div>
    <div class="card-body">
        <table class="table table-sm">
            <thead>
                <tr>
                    <th>时间</th>
                    <th>操作人</th>
                    <th>操作</th>
                    <th>状态变更</th>
                    <th>备注</th>
                </tr>
            </thead>
            <tbody>
                {% for h in history %}
                <tr>
                    <td>{{ h.created_at }}</td>
                    <td>{{ h.operator_name }}</td>
                    <td>{{ h.action }}</td>
                    <td>{{ h.old_status or '-' }} → {{ h.new_status or '-' }}</td>
                    <td>{{ h.notes or '-' }}</td>
                </tr>
                {% endfor %}
            </tbody>
        </table>
    </div>
</div>
{% endblock %}
""")

ADMIN_DASHBOARD_TEMPLATE = BASE_TEMPLATE.replace("{% block content %}{% endblock %}", """
{% block content %}
<h2 class="mb-4">系统管理</h2>

<div class="row mb-4">
    <div class="col-md-3">
        <div class="card text-center">
            <div class="card-body">
                <h3 class="text-primary">{{ total_users }}</h3>
                <p class="mb-0">用户总数</p>
            </div>
        </div>
    </div>
    <div class="col-md-3">
        <div class="card text-center">
            <div class="card-body">
                <h3 class="text-info">{{ total_invoices }}</h3>
                <p class="mb-0">报销单总数</p>
            </div>
        </div>
    </div>
    <div class="col-md-3">
        <div class="card text-center">
            <div class="card-body">
                <h3 class="text-warning">{{ pending_count }}</h3>
                <p class="mb-0">待处理</p>
            </div>
        </div>
    </div>
    <div class="col-md-3">
        <div class="card text-center">
            <div class="card-body">
                <h3 class="text-success">{{ space_info.total }}</h3>
                <p class="mb-0">空间占用</p>
            </div>
        </div>
    </div>
</div>

<div class="card mb-3">
    <div class="card-header">
        <h5 class="mb-0">空间占用详情</h5>
    </div>
    <div class="card-body">
        <div class="row">
            <div class="col-md-4">
                <p><strong>数据库:</strong> {{ space_info.database }}</p>
            </div>
            <div class="col-md-4">
                <p><strong>附件:</strong> {{ space_info.uploads }}</p>
            </div>
            <div class="col-md-4">
                <p><strong>备份:</strong> {{ space_info.backup }}</p>
            </div>
        </div>
    </div>
</div>

<div class="card mb-3">
    <div class="card-header d-flex justify-content-between align-items-center">
        <h5 class="mb-0">快捷操作</h5>
    </div>
    <div class="card-body">
        <a href="{{ url_for('manage_users') }}" class="btn btn-primary">
            <i class="bi bi-people"></i> 用户管理
        </a>
        <a href="{{ url_for('create_backup') }}" class="btn btn-success">
            <i class="bi bi-archive"></i> 创建备份
        </a>
        <a href="{{ url_for('export_data') }}" class="btn btn-info">
            <i class="bi bi-download"></i> 导出数据
        </a>
    </div>
</div>

<div class="card">
    <div class="card-header">
        <h5 class="mb-0">最近活动</h5>
    </div>
    <div class="card-body">
        <table class="table table-sm">
            <thead>
                <tr>
                    <th>时间</th>
                    <th>项目</th>
                    <th>操作人</th>
                    <th>操作</th>
                    <th>状态变更</th>
                </tr>
            </thead>
            <tbody>
                {% for act in recent_activity %}
                <tr>
                    <td>{{ act.created_at }}</td>
                    <td>{{ act.project_name }}</td>
                    <td>{{ act.operator_name }}</td>
                    <td>{{ act.action }}</td>
                    <td>{{ act.old_status or '-' }} → {{ act.new_status or '-' }}</td>
                </tr>
                {% endfor %}
            </tbody>
        </table>
    </div>
</div>
{% endblock %}
""")

MANAGE_USERS_TEMPLATE = BASE_TEMPLATE.replace("{% block content %}{% endblock %}", """
{% block content %}
<div class="d-flex justify-content-between align-items-center mb-4">
    <h2>用户管理</h2>
    <a href="{{ url_for('admin_dashboard') }}" class="btn btn-outline-secondary">返回</a>
</div>

<div class="card mb-3">
    <div class="card-header">
        <h5 class="mb-0">添加用户</h5>
    </div>
    <div class="card-body">
        <form method="POST" action="{{ url_for('add_user') }}" class="row g-3">
            <div class="col-md-3">
                <input type="text" class="form-control" name="username" placeholder="学号/工号" required>
            </div>
            <div class="col-md-3">
                <input type="text" class="form-control" name="real_name" placeholder="姓名" required>
            </div>
            <div class="col-md-3">
                <select class="form-select" name="role" required>
                    <option value="">选择角色</option>
                    <option value="filler">填报人</option>
                    <option value="material_reimburser">材料报账</option>
                    <option value="asset_reimburser">资产报账</option>
                </select>
            </div>
            <div class="col-md-3">
                <button type="submit" class="btn btn-primary w-100">添加</button>
            </div>
        </form>
    </div>
</div>

<div class="card">
    <div class="card-header">
        <h5 class="mb-0">用户列表</h5>
    </div>
    <div class="card-body">
        <table class="table table-hover">
            <thead>
                <tr>
                    <th>ID</th>
                    <th>学号/工号</th>
                    <th>姓名</th>
                    <th>角色</th>
                    <th>创建时间</th>
                    <th>操作</th>
                </tr>
            </thead>
            <tbody>
                {% for user in users %}
                <tr>
                    <td>{{ user.id }}</td>
                    <td>{{ user.username }}</td>
                    <td>{{ user.real_name }}</td>
                    <td>
                        <form method="POST" action="{{ url_for('update_user_role', user_id=user.id) }}" class="d-inline">
                            <select class="form-select form-select-sm" name="role" onchange="this.form.submit()">
                                <option value="filler" {% if user.role == 'filler' %}selected{% endif %}>填报人</option>
                                <option value="material_reimburser" {% if user.role == 'material_reimburser' %}selected{% endif %}>材料报账</option>
                                <option value="asset_reimburser" {% if user.role == 'asset_reimburser' %}selected{% endif %}>资产报账</option>
                            </select>
                        </form>
                    </td>
                    <td>{{ user.created_at }}</td>
                    <td>
                        <form method="POST" action="{{ url_for('reset_user_password', user_id=user.id) }}" class="d-inline">
                            <button type="submit" class="btn btn-sm btn-warning">重置密码</button>
                        </form>
                    </td>
                </tr>
                {% endfor %}
            </tbody>
        </table>
    </div>
</div>
{% endblock %}
""")


# ============= Main =============
if __name__ == "__main__":
    init_db()
    print("=" * 60)
    print("发票报销系统启动中...")
    print(f"管理员账号: {ADMIN_USERNAME}")
    print(f"管理员密码: {ADMIN_PASSWORD}")
    print(f"管理员添加用户的初始密码: {ADMIN_INITIAL_PASSWORD}")
    print("=" * 60)
    print("访问地址: http://localhost:5000")
    print("=" * 60)
    app.run(host="0.0.0.0", port=5000, debug=True)

