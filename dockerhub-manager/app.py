import base64
import json
import os
import posixpath
import shlex
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path

from flask import Flask, Response, jsonify, render_template_string, request, session, redirect, url_for

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "change-me-in-production-please")

# ── 内置配置（生产环境请替换为数据库） ──────────────────────────────────────
DATA_FILE = Path("data.json")

def load_data():
    if DATA_FILE.exists():
        return json.loads(DATA_FILE.read_text())
    return {
        "users": {
            "admin": {"password": "admin123", "role": "admin", "created_at": datetime.now().isoformat()}
        },
        "servers": {},
        "containers": {},
        "templates": []
    }

def save_data(data):
    DATA_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False))

# ── 模板加载工具 ────────────────────────────────────────────────────────────
def load_template(name):
    p = Path("templates") / name
    if p.exists():
        return p.read_text(encoding="utf-8")
    return f"<h1>模板 {name} 未找到</h1>"

# ── 认证装饰器 ──────────────────────────────────────────────────────────────
from functools import wraps

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user" not in session:
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated

def role_required(*roles):
    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            if session.get("role") not in roles:
                return jsonify({"error": "权限不足"}), 403
            return f(*args, **kwargs)
        return decorated
    return decorator

# ── 路由：认证 ──────────────────────────────────────────────────────────────
@app.route("/")
def index():
    if "user" not in session:
        return redirect(url_for("login"))
    return redirect(url_for("dashboard"))

@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        data = load_data()
        username = request.form.get("username", "")
        password = request.form.get("password", "")
        user = data["users"].get(username)
        if user and user["password"] == password:
            session["user"] = username
            session["role"] = user["role"]
            return redirect(url_for("dashboard"))
        error = "用户名或密码错误"
    return render_template_string(load_template("login.html"), error=error)

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

# ── 路由：主页面（SPA 壳） ───────────────────────────────────────────────────
@app.route("/dashboard")
@login_required
def dashboard():
    return render_template_string(
        load_template("dashboard.html"),
        user=session["user"],
        role=session["role"]
    )

# ── API：服务器管理 ─────────────────────────────────────────────────────────
@app.route("/api/servers", methods=["GET"])
@login_required
def api_servers():
    data = load_data()
    servers = []
    for sid, srv in data["servers"].items():
        servers.append({
            "id": sid,
            "name": srv.get("name", sid),
            "host": srv.get("host", ""),
            "port": srv.get("port", 22),
            "status": "online",   # TODO: 实际 ping
            "containers": len([c for c in data["containers"].values()
                                if c.get("server_id") == sid])
        })
    return jsonify({"servers": servers})

@app.route("/api/servers", methods=["POST"])
@login_required
@role_required("admin")
def api_add_server():
    data = load_data()
    body = request.json or {}
    sid = body.get("id", f"srv_{int(time.time())}")
    data["servers"][sid] = {
        "name": body.get("name", sid),
        "host": body.get("host", ""),
        "port": body.get("port", 22),
        "data_path": body.get("data_path", "/data"),
        "agent_port": body.get("agent_port", 5001),
        "added_at": datetime.now().isoformat()
    }
    save_data(data)
    return jsonify({"ok": True, "id": sid})

@app.route("/api/servers/<sid>", methods=["DELETE"])
@login_required
@role_required("admin")
def api_del_server(sid):
    data = load_data()
    data["servers"].pop(sid, None)
    save_data(data)
    return jsonify({"ok": True})

# ── API：容器管理 ────────────────────────────────────────────────────────────
@app.route("/api/containers", methods=["GET"])
@login_required
def api_containers():
    data = load_data()
    containers = []
    for cid, c in data["containers"].items():
        containers.append({
            "id": cid,
            "name": c.get("name", cid),
            "user": c.get("assigned_to", ""),
            "server": c.get("server_id", ""),
            "image": c.get("image", ""),
            "ssh_port": c.get("ssh_port", ""),
            "status": c.get("status", "running"),
            "created_at": c.get("created_at", ""),
            "ssh_cmd": c.get("ssh_cmd", "")
        })
    return jsonify({"containers": containers})

@app.route("/api/containers", methods=["POST"])
@login_required
@role_required("admin", "allocator")
def api_create_container():
    data = load_data()
    body = request.json or {}
    cid = f"ctr_{int(time.time())}"
    ssh_port = body.get("ssh_port", 32000 + len(data["containers"]))
    server = data["servers"].get(body.get("server_id", ""), {})
    host = server.get("host", "server-host")
    ssh_cmd = f"ssh -p {ssh_port} root@{host}"

    data["containers"][cid] = {
        "name": body.get("name", f"容器_{cid}"),
        "assigned_to": body.get("assigned_to", ""),
        "server_id": body.get("server_id", ""),
        "image": body.get("image", "ubuntu:22.04"),
        "ssh_port": ssh_port,
        "ssh_cmd": ssh_cmd,
        "mounts": body.get("mounts", []),
        "cpu_limit": body.get("cpu_limit", "2"),
        "mem_limit": body.get("mem_limit", "4g"),
        "status": "running",
        "created_at": datetime.now().isoformat(),
        "created_by": session["user"]
    }
    save_data(data)
    # TODO: 调用 Agent API 实际创建
    return jsonify({"ok": True, "id": cid, "ssh_cmd": ssh_cmd})

@app.route("/api/containers/<cid>", methods=["DELETE"])
@login_required
@role_required("admin", "allocator")
def api_del_container(cid):
    data = load_data()
    data["containers"].pop(cid, None)
    save_data(data)
    return jsonify({"ok": True})

# ── API：用户管理 ────────────────────────────────────────────────────────────
@app.route("/api/users", methods=["GET"])
@login_required
@role_required("admin")
def api_users():
    data = load_data()
    users = [{"username": u, "role": v["role"], "created_at": v.get("created_at", "")}
             for u, v in data["users"].items()]
    return jsonify({"users": users})

@app.route("/api/users", methods=["POST"])
@login_required
@role_required("admin")
def api_add_user():
    data = load_data()
    body = request.json or {}
    uname = body.get("username", "")
    if not uname or uname in data["users"]:
        return jsonify({"error": "用户名无效或已存在"}), 400
    data["users"][uname] = {
        "password": body.get("password", "changeme"),
        "role": body.get("role", "allocator"),
        "created_at": datetime.now().isoformat()
    }
    save_data(data)
    return jsonify({"ok": True})

@app.route("/api/users/<uname>", methods=["DELETE"])
@login_required
@role_required("admin")
def api_del_user(uname):
    data = load_data()
    if uname == "admin":
        return jsonify({"error": "不能删除 admin"}), 400
    data["users"].pop(uname, None)
    save_data(data)
    return jsonify({"ok": True})

# ── SSE：日志流 ────────────────────────────────────────────────────────────
@app.route("/api/stream/logs")
@login_required
def stream_logs():
    def generate():
        sample = [
            "[INFO] 系统初始化完成",
            "[INFO] 服务器连接正常",
            "[OK] Docker 守护进程运行中",
            "[INFO] 等待操作指令...",
        ]
        for line in sample:
            ts = datetime.now().strftime("%H:%M:%S")
            yield f"data: {json.dumps({'time': ts, 'msg': line})}\n\n"
            time.sleep(0.4)
    return Response(generate(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

# ── 当前用户信息 ───────────────────────────────────────────────────────────
@app.route("/api/me")
@login_required
def api_me():
    return jsonify({"user": session["user"], "role": session["role"]})

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000, threaded=True)
