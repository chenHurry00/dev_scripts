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

import requests
from flask import Flask, Response, jsonify, render_template_string, request, session, redirect, url_for

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "change-me-in-production-please")
admin_password = os.environ.get("ADMIN_PASSWORD", "admin123")
agent_http = requests.Session()
agent_http.trust_env = False

# ── 内置配置（生产环境请替换为数据库） ──────────────────────────────────────
DATA_FILE = Path("data.json")
SSH_PORT_MIN = 32000
SSH_PORT_MAX = 32999
DEFAULT_SSH_IMAGE = "lscr.io/linuxserver/openssh-server:latest"

def load_data():
    if DATA_FILE.exists():
        data = json.loads(DATA_FILE.read_text())
    else:
        data = {
        "users": {
            "admin": {"password": admin_password, "role": "admin", "created_at": datetime.now().isoformat()}
        },
        "servers": {},
        "containers": {},
        "templates": []
    }
    data.setdefault("users", {})
    data.setdefault("servers", {})
    data.setdefault("containers", {})
    data.setdefault("templates", [])
    return data

def save_data(data):
    DATA_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False))

def call_agent(server: dict, path: str, method="GET", body=None, timeout=20):
    """向指定服务器 Agent 发 HTTP 请求。"""
    host = server.get("host", "")
    port = server.get("agent_port", 5001)
    token = server.get("agent_token", "")
    if not host:
        return {"ok": False, "error": "服务器 host 为空"}

    url = f"http://{host}:{port}{path}"
    headers = {"X-Agent-Token": token, "Content-Type": "application/json"}
    try:
        if method == "POST":
            resp = agent_http.post(url, json=body or {}, headers=headers, timeout=timeout)
        elif method == "PATCH":
            resp = agent_http.patch(url, json=body or {}, headers=headers, timeout=timeout)
        elif method == "DELETE":
            resp = agent_http.delete(url, headers=headers, timeout=timeout)
        else:
            resp = agent_http.get(url, headers=headers, timeout=timeout)
        try:
            data = resp.json()
        except ValueError:
            data = {"error": resp.text}
        if resp.status_code >= 400:
            data.setdefault("ok", False)
            data.setdefault("error", f"Agent HTTP {resp.status_code}")
        return data
    except Exception as exc:
        return {"ok": False, "error": str(exc)}

def normalize_mount_roots(raw):
    """规范化服务器挂载根目录配置。"""
    roots = []
    if not isinstance(raw, list):
        return roots
    for idx, item in enumerate(raw):
        if not isinstance(item, dict):
            continue
        host_path = (item.get("host_path") or "").strip()
        container_path = (item.get("default_container_path") or "/workspace").strip()
        if not host_path.startswith("/") or not container_path.startswith("/"):
            continue
        roots.append({
            "name": (item.get("name") or f"挂载{idx + 1}").strip(),
            "host_path": host_path.rstrip("/") or "/",
            "default_container_path": container_path.rstrip("/") or "/workspace",
            "readonly": bool(item.get("readonly", False)),
        })
    return roots

def default_resources(server):
    """按服务器真实资源 1/8 生成默认 CPU 和内存。"""
    info = call_agent(server, "/sysinfo")
    cpu_cores = int(info.get("cpu_cores") or 8)
    memory_bytes = int(info.get("memory_bytes") or 8 * 1024 * 1024 * 1024)
    cpu = max(1, cpu_cores // 8)
    memory_gb = max(1, memory_bytes // 8 // 1024 // 1024 // 1024)
    return str(cpu), f"{memory_gb}g"

def build_default_mounts(server, assigned_to, container_name):
    roots = server.get("mount_roots") or []
    if not roots:
        return []
    root = roots[0]
    safe_name = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in (assigned_to or container_name))
    return [{
        "host_path": f"{root['host_path'].rstrip('/')}/{safe_name}",
        "container_path": root.get("default_container_path", "/workspace"),
        "readonly": bool(root.get("readonly", False)),
    }]

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
        checks = call_agent(srv, "/checks", method="POST", body={"mount_roots": srv.get("mount_roots", [])}, timeout=5)
        status = "online" if checks.get("ok") else "offline"
        servers.append({
            "id": sid,
            "name": srv.get("name", sid),
            "host": srv.get("host", ""),
            "ssh_host": srv.get("ssh_host", srv.get("host", "")),
            "port": srv.get("port", 22),
            "agent_port": srv.get("agent_port", 5001),
            "mount_roots": srv.get("mount_roots", []),
            "checks": checks,
            "status": status,
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
    mount_roots = normalize_mount_roots(body.get("mount_roots", []))
    data["servers"][sid] = {
        "name": body.get("name", sid),
        "host": body.get("host", ""),
        "ssh_host": body.get("ssh_host") or body.get("host", ""),
        "port": body.get("port", 22),
        "agent_port": body.get("agent_port", 5001),
        "agent_token": body.get("agent_token", ""),
        "mount_roots": mount_roots,
        "added_at": datetime.now().isoformat()
    }
    save_data(data)
    return jsonify({"ok": True, "id": sid})

@app.route("/api/servers/<sid>/defaults", methods=["GET"])
@login_required
def api_server_defaults(sid):
    data = load_data()
    server = data["servers"].get(sid)
    if not server:
        return jsonify({"error": "服务器不存在"}), 404
    cpu, memory = default_resources(server)
    return jsonify({
        "ok": True,
        "cpu": cpu,
        "memory": memory,
        "mount_roots": server.get("mount_roots", [])
    })

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
            "login_user": c.get("login_user", ""),
            "cpu_limit": c.get("cpu_limit", ""),
            "mem_limit": c.get("mem_limit", ""),
            "pids_limit": c.get("pids_limit", ""),
            "mounts": c.get("mounts", []),
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
    ssh_port = int(body.get("ssh_port", SSH_PORT_MIN + len(data["containers"])))
    if ssh_port < SSH_PORT_MIN or ssh_port > SSH_PORT_MAX:
        return jsonify({"error": f"SSH 端口必须位于 {SSH_PORT_MIN}-{SSH_PORT_MAX}"}), 400
    server_id = body.get("server_id", "")
    server = data["servers"].get(server_id)
    if not server:
        return jsonify({"error": "目标服务器不存在"}), 400

    name = body.get("name", f"容器_{cid}")
    assigned_to = body.get("assigned_to", "")
    login_user = body.get("login_user") or assigned_to or "dockeruser"
    ssh_host = server.get("ssh_host") or server.get("host", "server-host")
    ssh_cmd = f"ssh -p {ssh_port} {login_user}@{ssh_host}"
    cpu_limit = body.get("cpu_limit")
    mem_limit = body.get("mem_limit")
    if not cpu_limit or not mem_limit:
        default_cpu, default_mem = default_resources(server)
        cpu_limit = cpu_limit or default_cpu
        mem_limit = mem_limit or default_mem

    mounts = body.get("mounts") or build_default_mounts(server, assigned_to, name)
    agent_body = {
        "name": name,
        "image": body.get("image", DEFAULT_SSH_IMAGE),
        "ssh_port": ssh_port,
        "cpu": cpu_limit,
        "memory": mem_limit,
        "pids_limit": body.get("pids_limit", 512),
        "login_user": login_user,
        "ssh_public_key": body.get("ssh_public_key", ""),
        "allow_sudo": bool(body.get("allow_sudo", False)),
        "mounts": mounts,
        "allowed_mount_roots": server.get("mount_roots", []),
    }

    agent_result = call_agent(server, "/containers/create", method="POST", body=agent_body, timeout=330)
    if not agent_result.get("ok"):
        return jsonify({"error": agent_result.get("error", "Agent 创建容器失败")}), 500

    data["containers"][cid] = {
        "name": name,
        "agent_container_id": agent_result.get("container_id", ""),
        "assigned_to": assigned_to,
        "server_id": server_id,
        "image": agent_body["image"],
        "ssh_port": ssh_port,
        "ssh_cmd": ssh_cmd,
        "login_user": login_user,
        "mounts": mounts,
        "cpu_limit": cpu_limit,
        "mem_limit": mem_limit,
        "pids_limit": agent_body["pids_limit"],
        "status": "running",
        "created_at": datetime.now().isoformat(),
        "created_by": session["user"]
    }
    save_data(data)
    return jsonify({"ok": True, "id": cid, "ssh_cmd": ssh_cmd})

@app.route("/api/containers/<cid>", methods=["DELETE"])
@login_required
@role_required("admin", "allocator")
def api_del_container(cid):
    data = load_data()
    container = data["containers"].get(cid)
    if container:
        server = data["servers"].get(container.get("server_id", ""))
        if server:
            result = call_agent(server, f"/containers/{container.get('name')}/remove", method="DELETE", timeout=60)
            if not result.get("ok"):
                return jsonify({"error": result.get("error", "Agent 删除容器失败")}), 500
    data["containers"].pop(cid, None)
    save_data(data)
    return jsonify({"ok": True})

@app.route("/api/containers/<cid>/resources", methods=["PATCH"])
@login_required
@role_required("admin", "allocator")
def api_update_container_resources(cid):
    data = load_data()
    body = request.json or {}
    container = data["containers"].get(cid)
    if not container:
        return jsonify({"error": "容器不存在"}), 404
    server = data["servers"].get(container.get("server_id", ""))
    if not server:
        return jsonify({"error": "服务器不存在"}), 404

    payload = {
        "cpu": body.get("cpu_limit", container.get("cpu_limit")),
        "memory": body.get("mem_limit", container.get("mem_limit")),
        "pids_limit": body.get("pids_limit", container.get("pids_limit", 512)),
    }
    result = call_agent(
        server,
        f"/containers/{container.get('name')}/resources",
        method="PATCH",
        body=payload,
        timeout=60,
    )
    if not result.get("ok"):
        return jsonify({"error": result.get("error", "资源更新失败")}), 500

    container["cpu_limit"] = payload["cpu"]
    container["mem_limit"] = payload["memory"]
    container["pids_limit"] = payload["pids_limit"]
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

@app.route("/api/config/export")
@login_required
@role_required("admin")
def api_config_export():
    data = load_data()
    include_tokens = request.args.get("include_tokens") == "1"
    payload = {
        "version": 1,
        "exported_at": datetime.now().isoformat(),
        "users": data.get("users", {}),
        "servers": json.loads(json.dumps(data.get("servers", {}))),
        "containers": data.get("containers", {}),
        "templates": data.get("templates", []),
        "contains_tokens": include_tokens,
    }
    if not include_tokens:
        for server in payload["servers"].values():
            server["agent_token"] = ""
    return jsonify(payload)

@app.route("/api/config/import", methods=["POST"])
@login_required
@role_required("admin")
def api_config_import():
    payload = request.json or {}
    if payload.get("version") != 1:
        return jsonify({"error": "不支持的配置版本"}), 400
    data = load_data()
    for key in ("users", "servers", "containers", "templates"):
        if key in payload:
            data[key] = payload[key]
    save_data(data)
    return jsonify({"ok": True})

if __name__ == "__main__":
    debug = os.environ.get("DEBUG", "0") == "1"
    port = int(os.environ.get("PANEL_PORT", "5000"))
    app.run(debug=debug, host="0.0.0.0", port=port, threaded=True)
