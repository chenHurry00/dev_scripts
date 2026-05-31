import base64
import hmac
import json
import os
import posixpath
import re
import shlex
import subprocess
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

import requests
from flask import Flask, jsonify, render_template_string, request, session, redirect, url_for
from werkzeug.exceptions import HTTPException
from werkzeug.security import check_password_hash, generate_password_hash

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "change-me-in-production-please")
admin_password_b64 = os.environ.get("ADMIN_PASSWORD_B64", "")
admin_password = (
    base64.b64decode(admin_password_b64).decode("utf-8")
    if admin_password_b64
    else os.environ.get("ADMIN_PASSWORD", "admin123")
)
agent_http = requests.Session()
agent_http.trust_env = False
image_pull_tasks = {}
image_pull_tasks_lock = threading.Lock()
data_lock = threading.RLock()

# ── 内置配置（生产环境请替换为数据库） ──────────────────────────────────────
DATA_FILE = Path("data.json")
SSH_PORT_MIN = 32000
SSH_PORT_MAX = 32999
DEFAULT_SSH_IMAGE = "lscr.io/linuxserver/openssh-server:latest"
PANEL_VERSION = "0.4.0"

def load_data():
    with data_lock:
        if DATA_FILE.exists():
            data = json.loads(DATA_FILE.read_text(encoding="utf-8"))
        else:
            data = {
            "users": {
                "admin": {"password": generate_password_hash(admin_password), "role": "admin", "created_at": datetime.now().isoformat()}
            },
            "servers": {},
            "containers": {},
            "templates": [],
            "audit_logs": []
        }
    data.setdefault("users", {})
    data.setdefault("servers", {})
    data.setdefault("containers", {})
    data.setdefault("templates", [])
    data.setdefault("audit_logs", [])
    migrate_empty_server_id(data)
    return data

def save_data(data):
    payload = json.dumps(data, indent=2, ensure_ascii=False)
    with data_lock:
        DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
        temp_file = DATA_FILE.with_name(f".{DATA_FILE.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        temp_file.write_text(payload, encoding="utf-8")
        os.replace(temp_file, DATA_FILE)

def migrate_empty_server_id(data):
    """迁移旧版允许保存的空服务器 ID，避免前端下拉框与未选择状态冲突。"""
    if "" not in data["servers"]:
        return
    server = data["servers"].pop("")
    base = re.sub(r"[^a-zA-Z0-9_-]", "_", server.get("name", "").strip()).strip("_-").lower()
    base = f"srv_{base or 'migrated'}"
    sid = base
    suffix = 2
    while sid in data["servers"]:
        sid = f"{base}_{suffix}"
        suffix += 1
    data["servers"][sid] = server
    for container in data["containers"].values():
        if container.get("server_id", "") == "":
            container["server_id"] = sid
    data["audit_logs"].append({
        "time": datetime.now().isoformat(timespec="seconds"),
        "level": "WARN",
        "message": f"自动迁移空服务器 ID 为 {sid}",
    })
    data["audit_logs"] = data["audit_logs"][-200:]
    save_data(data)

def append_audit(data, message, level="INFO"):
    data.setdefault("audit_logs", []).append({
        "time": datetime.now().isoformat(timespec="seconds"),
        "level": level,
        "message": message,
    })
    data["audit_logs"] = data["audit_logs"][-200:]

def verify_password(stored_password, input_password):
    """兼容旧版明文密码，并在成功登录后迁移为哈希。"""
    if stored_password.startswith(("scrypt:", "pbkdf2:")):
        return check_password_hash(stored_password, input_password), False
    return hmac.compare_digest(stored_password, input_password), True

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
            resp = agent_http.delete(url, json=body or {}, headers=headers, timeout=timeout)
        else:
            resp = agent_http.get(url, headers=headers, timeout=timeout)
        try:
            data = resp.json()
        except ValueError:
            data = {"error": resp.text}
        data.setdefault("status_code", resp.status_code)
        if resp.status_code >= 400:
            data.setdefault("ok", False)
            data.setdefault("error", f"Agent HTTP {resp.status_code}")
        return data
    except Exception as exc:
        return {"ok": False, "error": str(exc)}

@app.errorhandler(Exception)
def handle_exception(exc):
    if isinstance(exc, HTTPException):
        return exc
    app.logger.exception("Unhandled exception")
    if request.path.startswith("/api/"):
        return jsonify({"ok": False, "error": f"服务器内部错误: {exc}"}), 500
    return f"服务器内部错误: {exc}", 500

def resolve_image_reference(image, registry_prefix=""):
    """按可选 registry 前缀生成本次拉取使用的镜像地址。"""
    image = (image or "").strip()
    prefix = (registry_prefix or "").strip()
    for scheme in ("https://", "http://"):
        if prefix.startswith(scheme):
            prefix = prefix[len(scheme):]
    prefix = prefix.rstrip("/")
    if not image or image.startswith("-") or any(ch.isspace() for ch in image):
        raise ValueError("镜像地址不能为空且不能包含空白字符")
    if prefix.startswith("-") or any(ch.isspace() for ch in prefix):
        raise ValueError("镜像源前缀不能包含空白字符")
    if not prefix:
        return image
    for docker_hub_prefix in ("docker.io/", "registry-1.docker.io/"):
        if image.startswith(docker_hub_prefix):
            image = image[len(docker_hub_prefix):]
    if image.startswith(f"{prefix}/"):
        return image
    return f"{prefix}/{image}"

def update_image_pull_task(task_id, **changes):
    with image_pull_tasks_lock:
        task = image_pull_tasks.get(task_id)
        if not task:
            return
        task.update(changes)

def append_image_pull_progress(task_id, message):
    with image_pull_tasks_lock:
        task = image_pull_tasks.get(task_id)
        if not task:
            return
        task["progress"].append(message)
        task["progress"] = task["progress"][-100:]

def run_image_pull_task(task_id, server, image):
    host = server.get("host", "")
    port = server.get("agent_port", 5001)
    token = server.get("agent_token", "")
    url = f"http://{host}:{port}/images/pull"
    try:
        pull_http = requests.Session()
        pull_http.trust_env = False
        with pull_http.post(
            url,
            json={"image": image},
            headers={"X-Agent-Token": token, "Content-Type": "application/json"},
            stream=True,
            timeout=(5, 3600),
        ) as resp:
            resp.raise_for_status()
            for raw_line in resp.iter_lines(decode_unicode=True):
                if not raw_line or not raw_line.startswith("data: "):
                    continue
                event = json.loads(raw_line[6:])
                if event.get("msg"):
                    append_image_pull_progress(task_id, event["msg"])
                if event.get("done"):
                    status = "done" if event.get("status") == "ok" else "error"
                    update_image_pull_task(task_id, status=status, finished_at=datetime.now().isoformat())
        with image_pull_tasks_lock:
            task = image_pull_tasks.get(task_id, {})
            if task.get("status") == "running":
                task["status"] = "done"
                task["finished_at"] = datetime.now().isoformat()
    except Exception as exc:
        append_image_pull_progress(task_id, f"ERROR: {exc}")
        update_image_pull_task(task_id, status="error", finished_at=datetime.now().isoformat())

    data = load_data()
    with image_pull_tasks_lock:
        task = image_pull_tasks.get(task_id, {})
        status = task.get("status", "error")
    append_audit(data, f"镜像拉取{('完成' if status == 'done' else '失败')} {image}", "INFO" if status == "done" else "WARN")
    save_data(data)

def safe_container_name(value, default="docker-env"):
    value = re.sub(r"[^a-zA-Z0-9_-]+", "-", (value or "").strip().lower())
    value = value.strip("-_")
    return value or default

def build_container_name(requested_name, assigned_to, login_user):
    requested_name = safe_container_name(requested_name, "")
    if requested_name:
        return requested_name
    base = safe_container_name(assigned_to or login_user, "docker-env")
    return f"{base}-{uuid.uuid4().hex[:6]}"

def build_ssh_cmd(login_user, ssh_port, ssh_host):
    if not ssh_port:
        return ""
    return f"ssh -p {ssh_port} {login_user}@{ssh_host}"

def parse_label_bool(value):
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}

def parse_label_list(value):
    raw = str(value or "").strip()
    if not raw:
        return []
    return [item.strip() for item in raw.split(",") if item.strip()]

def parse_agent_ssh_port(container):
    ssh_port = container.get("ssh_port")
    if ssh_port:
        try:
            return int(ssh_port)
        except (TypeError, ValueError):
            pass
    for port in container.get("ports") or []:
        host_port = port.get("host_port")
        if not host_port:
            continue
        try:
            return int(host_port)
        except (TypeError, ValueError):
            continue
    ports_text = container.get("ports_text") or container.get("Ports") or ""
    match = re.search(r":(\d+)->\d+/tcp", ports_text)
    if match:
        return int(match.group(1))
    return None

def normalize_agent_container(raw):
    if not isinstance(raw, dict):
        return None
    labels = raw.get("labels")
    if labels is None:
        labels_text = raw.get("Labels", "")
        labels = {}
        if labels_text:
            for item in labels_text.split(","):
                key, sep, value = item.partition("=")
                if sep:
                    labels[key.strip()] = value.strip()
    name = (raw.get("name") or raw.get("Names") or raw.get("Name") or "").lstrip("/")
    if not name:
        return None
    managed = labels.get("manager") == "dockerhub" if isinstance(labels, dict) else False
    status = (raw.get("state") or "").strip().lower()
    if not status:
        status_text = raw.get("status") or raw.get("Status") or ""
        status_text = str(status_text).strip().lower()
        if status_text in {"running", "restarting", "paused"}:
            status = status_text
        elif status_text in {"exited", "dead", "created"}:
            status = "stopped"
        elif status_text.startswith("up "):
            status = "running"
        elif status_text.startswith("exited"):
            status = "stopped"
        else:
            status = "unknown"
    return {
        "container_id": raw.get("id") or raw.get("ID") or "",
        "name": name,
        "image": raw.get("image") or raw.get("Image") or "",
        "status": status,
        "created_at": raw.get("created_at") or raw.get("CreatedAt") or "",
        "pids_limit": raw.get("pids_limit"),
        "labels": labels if isinstance(labels, dict) else {},
        "ports": raw.get("ports") or [],
        "ports_text": raw.get("ports_text") or raw.get("Ports") or "",
        "ssh_port": parse_agent_ssh_port(raw),
        "managed": managed,
    }

def find_container_record(data, server_id, name, agent_container_id=""):
    for cid, record in data["containers"].items():
        if agent_container_id and record.get("agent_container_id") == agent_container_id:
            return cid, record
        if record.get("server_id") == server_id and record.get("name") == name:
            return cid, record
    return None, None

def register_container_record(
    data,
    server_id,
    server,
    name,
    *,
    assigned_to="",
    login_user="",
    image="",
    ssh_port=None,
    mounts=None,
    cpu_limit="",
    mem_limit="",
    pids_limit=512,
    gpu_enabled=None,
    gpu_driver="",
    gpu_devices=None,
    gpu_mode="",
    rootfs_limit="",
    image_mode="",
    allow_sudo=None,
    password_access=None,
    config_volume="",
    password_file="",
    agent_container_id="",
    status="running",
    created_at="",
    created_by="",
    recovered=False,
):
    cid, record = find_container_record(data, server_id, name, agent_container_id)
    if record is None:
        cid = f"ctr_{uuid.uuid4().hex[:12]}"
        record = {}
    before = json.dumps(record, sort_keys=True, ensure_ascii=False)
    is_new = before == "{}"
    next_login_user = login_user or record.get("login_user") or "dockeruser"
    next_ssh_port = ssh_port if ssh_port is not None else record.get("ssh_port")
    ssh_host = server.get("ssh_host") or server.get("host", "server-host")
    next_created_by = created_by or record.get("created_by", "")
    if recovered and is_new and not next_created_by:
        next_created_by = "__recovered__"
    record.update({
        "name": name,
        "agent_container_id": agent_container_id or record.get("agent_container_id", ""),
        "assigned_to": assigned_to or record.get("assigned_to", ""),
        "server_id": server_id,
        "image": image or record.get("image", ""),
        "ssh_port": next_ssh_port,
        "ssh_cmd": build_ssh_cmd(next_login_user, next_ssh_port, ssh_host),
        "login_user": next_login_user,
        "mounts": mounts if mounts is not None else record.get("mounts", []),
        "cpu_limit": cpu_limit or record.get("cpu_limit", ""),
        "mem_limit": mem_limit or record.get("mem_limit", ""),
        "pids_limit": pids_limit or record.get("pids_limit", 512),
        "gpu_enabled": bool(gpu_enabled if gpu_enabled is not None else record.get("gpu_enabled", False)),
        "gpu_driver": gpu_driver or record.get("gpu_driver", ""),
        "gpu_devices": list(gpu_devices if gpu_devices is not None else record.get("gpu_devices", [])),
        "gpu_mode": gpu_mode or record.get("gpu_mode", ""),
        "rootfs_limit": rootfs_limit or record.get("rootfs_limit", ""),
        "image_mode": image_mode or record.get("image_mode", ""),
        "allow_sudo": bool(allow_sudo if allow_sudo is not None else record.get("allow_sudo", True)),
        "password_access": bool(password_access if password_access is not None else record.get("password_access", False)),
        "config_volume": config_volume or record.get("config_volume", ""),
        "password_file": password_file or record.get("password_file", ""),
        "status": status or record.get("status", "unknown"),
        "created_at": created_at or record.get("created_at", datetime.now().isoformat()),
        "created_by": next_created_by,
    })
    data["containers"][cid] = record
    after = json.dumps(record, sort_keys=True, ensure_ascii=False)
    if recovered and is_new:
        append_audit(data, f"从 Agent 接管孤立容器 {name}，服务器 {server_id}", "WARN")
    return cid, record, is_new, before != after

def reconcile_containers(data):
    changed = False
    listed_servers = set()
    seen = set()
    for server_id, server in data["servers"].items():
        result = call_agent(server, "/containers", timeout=30)
        if result.get("status_code", 200) >= 400 or result.get("error"):
            continue
        listed_servers.add(server_id)
        for raw in result.get("containers", []):
            agent_container = normalize_agent_container(raw)
            if not agent_container or not agent_container.get("managed"):
                continue
            seen.add((server_id, agent_container["name"]))
            labels = agent_container.get("labels", {})
            _, _, _, record_changed = register_container_record(
                data,
                server_id,
                server,
                agent_container["name"],
                assigned_to=labels.get("manager.assigned_to", ""),
                login_user=labels.get("manager.login_user", ""),
                image=agent_container.get("image", ""),
                ssh_port=agent_container.get("ssh_port"),
                agent_container_id=agent_container.get("container_id", ""),
                status=agent_container.get("status", "unknown"),
                pids_limit=agent_container.get("pids_limit") or labels.get("manager.pids_limit", 512),
                gpu_enabled=parse_label_bool(labels.get("manager.gpu_enabled")),
                gpu_driver=labels.get("manager.gpu_driver", ""),
                gpu_devices=parse_label_list(labels.get("manager.gpu_devices", "")),
                gpu_mode=labels.get("manager.gpu_mode", ""),
                rootfs_limit=labels.get("manager.rootfs_limit", ""),
                image_mode=labels.get("manager.image_mode", ""),
                allow_sudo=parse_label_bool(labels.get("manager.allow_sudo")) if "manager.allow_sudo" in labels else None,
                password_access=parse_label_bool(labels.get("manager.password_access")) if "manager.password_access" in labels else None,
                config_volume=labels.get("manager.config_volume", ""),
                password_file=labels.get("manager.password_file", ""),
                created_at=agent_container.get("created_at", ""),
                recovered=True,
            )
            changed = changed or record_changed
    for record in data["containers"].values():
        server_id = record.get("server_id", "")
        if server_id not in listed_servers:
            continue
        if (server_id, record.get("name", "")) not in seen and record.get("status") != "missing":
            record["status"] = "missing"
            changed = True
    return changed

def adopt_agent_container(data, server_id, server, name, defaults):
    result = call_agent(server, "/containers", timeout=30)
    if result.get("status_code", 200) >= 400 or result.get("error"):
        return None
    for raw in result.get("containers", []):
        agent_container = normalize_agent_container(raw)
        if not agent_container or agent_container["name"] != name:
            continue
        cid, record, _, _ = register_container_record(
            data,
            server_id,
            server,
            name,
            assigned_to=defaults.get("assigned_to", ""),
            login_user=defaults.get("login_user", ""),
            image=agent_container.get("image", defaults.get("image", "")),
            ssh_port=agent_container.get("ssh_port"),
            mounts=defaults.get("mounts"),
            cpu_limit=defaults.get("cpu_limit", ""),
            mem_limit=defaults.get("mem_limit", ""),
            pids_limit=agent_container.get("pids_limit") or defaults.get("pids_limit", 512),
            gpu_enabled=parse_label_bool(agent_container.get("labels", {}).get("manager.gpu_enabled")),
            gpu_driver=agent_container.get("labels", {}).get("manager.gpu_driver", defaults.get("gpu_driver", "")),
            gpu_devices=parse_label_list(agent_container.get("labels", {}).get("manager.gpu_devices", "")) or defaults.get("gpu_devices", []),
            gpu_mode=agent_container.get("labels", {}).get("manager.gpu_mode", defaults.get("gpu_mode", "")),
            rootfs_limit=agent_container.get("labels", {}).get("manager.rootfs_limit", defaults.get("rootfs_limit", "")),
            image_mode=agent_container.get("labels", {}).get("manager.image_mode", defaults.get("image_mode", "")),
            allow_sudo=parse_label_bool(agent_container.get("labels", {}).get("manager.allow_sudo")) if "manager.allow_sudo" in agent_container.get("labels", {}) else defaults.get("allow_sudo"),
            password_access=parse_label_bool(agent_container.get("labels", {}).get("manager.password_access")) if "manager.password_access" in agent_container.get("labels", {}) else defaults.get("password_access"),
            config_volume=agent_container.get("labels", {}).get("manager.config_volume", defaults.get("config_volume", "")),
            password_file=agent_container.get("labels", {}).get("manager.password_file", defaults.get("password_file", "")),
            agent_container_id=agent_container.get("container_id", ""),
            status=agent_container.get("status", "running"),
            created_at=agent_container.get("created_at", ""),
            created_by=defaults.get("created_by", ""),
            recovered=True,
        )
        return cid, record
    return None

def choose_available_ssh_port(server, existing_records=None):
    used_ports = set()
    result = call_agent(server, "/containers", timeout=30)
    if result.get("status_code", 200) < 400 and not result.get("error"):
        for raw in result.get("containers", []):
            agent_container = normalize_agent_container(raw)
            if not agent_container:
                continue
            ssh_port = agent_container.get("ssh_port")
            if ssh_port:
                used_ports.add(int(ssh_port))
    for record in existing_records or []:
        ssh_port = record.get("ssh_port")
        try:
            if ssh_port:
                used_ports.add(int(ssh_port))
        except (TypeError, ValueError):
            continue
    for port in range(SSH_PORT_MIN, SSH_PORT_MAX + 1):
        if port not in used_ports:
            return port
    return None

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
        if user:
            matched, needs_upgrade = verify_password(user["password"], password)
            if matched:
                if needs_upgrade:
                    user["password"] = generate_password_hash(password)
                    save_data(data)
                session["user"] = username
                session["role"] = user["role"]
                return redirect(url_for("dashboard"))
        error = "用户名或密码错误"
    return render_template_string(
        load_template("login.html"),
        error=error,
        panel_version=PANEL_VERSION,
    )

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
        role=session["role"],
        panel_version=PANEL_VERSION,
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
    body = request.json or {}
    sid = (body.get("id") or "").strip() or f"srv_{int(time.time())}"
    if not re.match(r"^[a-zA-Z0-9_-]+$", sid):
        return jsonify({"error": "服务器 ID 只能包含字母、数字、下划线和连字符"}), 400
    with data_lock:
        data = load_data()
        if sid in data["servers"]:
            return jsonify({"error": "服务器 ID 已存在"}), 400
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
        append_audit(data, f"注册服务器 {sid}")
        save_data(data)
    return jsonify({"ok": True, "id": sid})

@app.route("/api/servers/<sid>", methods=["PATCH"])
@login_required
@role_required("admin")
def api_update_server(sid):
    body = request.json or {}
    with data_lock:
        data = load_data()
        server = data["servers"].get(sid)
        if not server:
            return jsonify({"error": "服务器不存在"}), 404
        server["name"] = body.get("name", server.get("name", sid))
        server["host"] = body.get("host", server.get("host", ""))
        server["ssh_host"] = body.get("ssh_host") or server["host"]
        server["agent_port"] = body.get("agent_port", server.get("agent_port", 5001))
        if body.get("agent_token"):
            server["agent_token"] = body["agent_token"]
        if "mount_roots" in body:
            server["mount_roots"] = normalize_mount_roots(body["mount_roots"])
        append_audit(data, f"更新服务器 {sid}")
        save_data(data)
    return jsonify({"ok": True})

@app.route("/api/servers/<sid>/defaults", methods=["GET"])
@login_required
def api_server_defaults(sid):
    data = load_data()
    server = data["servers"].get(sid)
    if not server:
        return jsonify({"error": "服务器不存在"}), 404
    cpu, memory = default_resources(server)
    checks = call_agent(server, "/checks", method="POST", body={"mount_roots": server.get("mount_roots", [])}, timeout=15)
    return jsonify({
        "ok": True,
        "cpu": cpu,
        "memory": memory,
        "default_rootfs_limit": "120g",
        "mount_roots": server.get("mount_roots", []),
        "checks": checks,
        "storage": checks.get("storage", {}),
        "gpu": checks.get("gpu", {}),
    })

@app.route("/api/servers/<sid>/gpu", methods=["GET"])
@login_required
def api_server_gpu(sid):
    data = load_data()
    server = data["servers"].get(sid)
    if not server:
        return jsonify({"error": "服务器不存在"}), 404
    result = call_agent(server, "/gpu/info", timeout=20)
    if result.get("status_code", 200) >= 400 or result.get("error"):
        return jsonify({
            "ok": False,
            "error": result.get("error") or "GPU 能力检查失败",
            "gpu": {}
        }), 502
    result["server_id"] = sid
    return jsonify(result)

@app.route("/api/servers/<sid>", methods=["DELETE"])
@login_required
@role_required("admin")
def api_del_server(sid):
    with data_lock:
        data = load_data()
        data["servers"].pop(sid, None)
        append_audit(data, f"移除服务器 {sid}", "WARN")
        save_data(data)
    return jsonify({"ok": True})

# ── API：镜像管理 ────────────────────────────────────────────────────────────
@app.route("/api/images", methods=["GET"])
@login_required
def api_images():
    data = load_data()
    server_id = request.args.get("server_id", "")
    server = data["servers"].get(server_id)
    if not server:
        return jsonify({"error": "目标服务器不存在"}), 404
    result = call_agent(server, "/images", timeout=30)
    if result.get("error"):
        return jsonify({"error": result["error"], "images": []}), 502
    return jsonify({"ok": True, "images": result.get("images", [])})

@app.route("/api/images", methods=["DELETE"])
@login_required
@role_required("admin", "allocator")
def api_delete_image():
    body = request.json or {}
    with data_lock:
        data = load_data()
        server_id = body.get("server_id", "")
        server = data["servers"].get(server_id)
    if not server:
        return jsonify({"error": "目标服务器不存在"}), 404
    image_ref = str(body.get("image_ref", "") or "").strip()
    if not image_ref:
        return jsonify({"error": "缺少镜像标识"}), 400
    result = call_agent(server, "/images", method="DELETE", body={"image_ref": image_ref}, timeout=180)
    if not result.get("ok"):
        status_code = result.get("status_code", 500)
        if status_code < 400:
            status_code = 500
        return jsonify({"error": result.get("error", "镜像删除失败")}), status_code
    with data_lock:
        data = load_data()
        append_audit(data, f"删除镜像 {image_ref}，服务器 {server_id}", "WARN")
        save_data(data)
    return jsonify({"ok": True})

@app.route("/api/images/pull", methods=["POST"])
@login_required
@role_required("admin", "allocator")
def api_pull_image():
    body = request.json or {}
    with data_lock:
        data = load_data()
        server_id = body.get("server_id", "")
        server = data["servers"].get(server_id)
    if not server:
        return jsonify({"error": "目标服务器不存在"}), 404
    try:
        image = resolve_image_reference(body.get("image", ""), body.get("registry_prefix", ""))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    task_id = uuid.uuid4().hex
    task = {
        "id": task_id,
        "server_id": server_id,
        "image": image,
        "status": "running",
        "progress": [],
        "created_at": datetime.now().isoformat(),
        "finished_at": None,
    }
    with image_pull_tasks_lock:
        image_pull_tasks[task_id] = task
    with data_lock:
        data = load_data()
        append_audit(data, f"开始拉取镜像 {image}，服务器 {server_id}")
        save_data(data)
    threading.Thread(target=run_image_pull_task, args=(task_id, dict(server), image), daemon=True).start()
    return jsonify({"ok": True, "task": task})

@app.route("/api/images/tasks", methods=["GET"])
@login_required
def api_image_pull_tasks():
    server_id = request.args.get("server_id", "")
    with image_pull_tasks_lock:
        tasks = [
            json.loads(json.dumps(task))
            for task in image_pull_tasks.values()
            if not server_id or task.get("server_id") == server_id
        ]
    tasks.sort(key=lambda item: item.get("created_at", ""), reverse=True)
    return jsonify({"tasks": tasks[:20]})

# ── API：容器管理 ────────────────────────────────────────────────────────────
@app.route("/api/containers", methods=["GET"])
@login_required
def api_containers():
    with data_lock:
        data = load_data()
        if reconcile_containers(data):
            save_data(data)
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
                "gpu_enabled": bool(c.get("gpu_enabled", False)),
                "gpu_driver": c.get("gpu_driver", ""),
                "gpu_devices": c.get("gpu_devices", []),
                "gpu_mode": c.get("gpu_mode", ""),
                "rootfs_limit": c.get("rootfs_limit", ""),
                "image_mode": c.get("image_mode", ""),
                "allow_sudo": bool(c.get("allow_sudo", True)),
                "password_access": bool(c.get("password_access", False)),
                "config_volume": c.get("config_volume", ""),
                "password_file": c.get("password_file", ""),
                "mounts": c.get("mounts", []),
                "status": c.get("status", "running"),
                "created_at": c.get("created_at", ""),
                "ssh_cmd": c.get("ssh_cmd", ""),
                "recovered": bool(c.get("created_by") == "__recovered__")
            })
    containers.sort(key=lambda item: item.get("created_at", ""), reverse=True)
    return jsonify({"containers": containers})

@app.route("/api/containers/metrics", methods=["GET"])
@login_required
def api_container_metrics():
    data = load_data()
    metrics = {}
    errors = {}
    for server_id, server in data.get("servers", {}).items():
        result = call_agent(server, "/containers/metrics", timeout=60)
        if result.get("status_code", 200) >= 400 or result.get("error"):
            errors[server_id] = result.get("error") or f"Agent HTTP {result.get('status_code', 500)}"
            continue
        for item in result.get("containers", []):
            name = item.get("name", "")
            if not name:
                continue
            cid, record = find_container_record(data, server_id, name, item.get("id", ""))
            if not cid or not record:
                continue
            metrics[cid] = {
                "container_id": cid,
                "server_id": server_id,
                "name": name,
                "status": item.get("status", record.get("status", "unknown")),
                "cpu_percent": item.get("cpu_percent", 0),
                "memory_used_bytes": item.get("memory_used_bytes", 0),
                "memory_limit_bytes": item.get("memory_limit_bytes", 0),
                "pids_current": item.get("pids_current"),
                "disk_rw_bytes": item.get("disk_rw_bytes", 0),
                "disk_rootfs_bytes": item.get("disk_rootfs_bytes", 0),
                "gpu": item.get("gpu", {}),
            }
    return jsonify({
        "ok": True,
        "metrics": metrics,
        "errors": errors,
        "collected_at": datetime.now().isoformat(timespec="seconds"),
    })

@app.route("/api/containers", methods=["POST"])
@login_required
@role_required("admin", "allocator")
def api_create_container():
    body = request.json or {}
    with data_lock:
        data = load_data()
        server_id = body.get("server_id", "")
        server = data["servers"].get(server_id)
    if not server:
        return jsonify({"error": "目标服务器不存在"}), 400

    assigned_to = body.get("assigned_to", "")
    login_user = safe_container_name(body.get("login_user") or assigned_to or "dockeruser", "dockeruser")
    name = build_container_name(body.get("name", ""), assigned_to, login_user)
    ssh_host = server.get("ssh_host") or server.get("host", "server-host")
    cpu_limit = body.get("cpu_limit")
    mem_limit = body.get("mem_limit")
    if not cpu_limit or not mem_limit:
        default_cpu, default_mem = default_resources(server)
        cpu_limit = cpu_limit or default_cpu
        mem_limit = mem_limit or default_mem
    raw_ssh_port = body.get("ssh_port")
    ssh_port = None
    if str(raw_ssh_port or "").strip():
        try:
            ssh_port = int(raw_ssh_port)
        except (TypeError, ValueError):
            return jsonify({"error": "SSH 端口必须是数字，或留空自动分配"}), 400
        if ssh_port < SSH_PORT_MIN or ssh_port > SSH_PORT_MAX:
            return jsonify({"error": f"SSH 端口必须位于 {SSH_PORT_MIN}-{SSH_PORT_MAX}"}), 400
    else:
        ssh_port = choose_available_ssh_port(server, data["containers"].values())
        if ssh_port is None:
            return jsonify({"error": f"{SSH_PORT_MIN}-{SSH_PORT_MAX} 范围内没有可用 SSH 端口"}), 400

    mounts = body["mounts"] if "mounts" in body else build_default_mounts(server, assigned_to, name)
    gpu_enabled = bool(body.get("gpu_enabled", False))
    gpu_devices = body.get("gpu_devices", [])
    rootfs_limit = str(body.get("rootfs_limit", "") or "").strip()
    agent_body = {
        "name": name,
        "image": body.get("image", DEFAULT_SSH_IMAGE),
        "ssh_port": ssh_port,
        "cpu": cpu_limit,
        "memory": mem_limit,
        "pids_limit": body.get("pids_limit", 512),
        "gpu_enabled": gpu_enabled,
        "gpu_devices": gpu_devices,
        "gpu_mode": body.get("gpu_mode", "shared"),
        "rootfs_limit": rootfs_limit,
        "login_user": login_user,
        "ssh_public_key": body.get("ssh_public_key", ""),
        "password_access": bool(body.get("password_access", False)),
        "ssh_password": body.get("ssh_password", ""),
        "allow_sudo": bool(body.get("allow_sudo", True)),
        "assigned_to": assigned_to,
        "mounts": mounts,
        "allowed_mount_roots": server.get("mount_roots", []),
    }

    agent_result = call_agent(server, "/containers/create", method="POST", body=agent_body, timeout=330)
    requested_gpu_devices = gpu_devices if isinstance(gpu_devices, list) else []
    defaults = {
        "assigned_to": assigned_to,
        "login_user": login_user,
        "image": agent_body["image"],
        "mounts": mounts,
        "cpu_limit": cpu_limit,
        "mem_limit": mem_limit,
        "pids_limit": agent_body["pids_limit"],
        "gpu_enabled": gpu_enabled,
        "gpu_driver": "nvidia" if gpu_enabled else "",
        "gpu_devices": requested_gpu_devices,
        "gpu_mode": agent_body["gpu_mode"] if gpu_enabled else "",
        "rootfs_limit": rootfs_limit,
        "image_mode": agent_result.get("image_mode", ""),
        "allow_sudo": agent_body["allow_sudo"],
        "password_access": agent_body["password_access"],
        "config_volume": agent_result.get("config_volume", ""),
        "password_file": agent_result.get("password_file", ""),
        "created_by": session["user"],
    }
    if not agent_result.get("ok"):
        conflict = (
            agent_result.get("code") == "container_name_conflict"
            or agent_result.get("status_code") == 409
            or "already in use" in str(agent_result.get("error", ""))
        )
        if conflict:
            with data_lock:
                data = load_data()
                adopted = adopt_agent_container(data, server_id, server, name, defaults)
                if adopted:
                    cid, record = adopted
                    save_data(data)
                    return jsonify({
                        "ok": True,
                        "id": cid,
                        "ssh_cmd": record.get("ssh_cmd", ""),
                        "name": record.get("name", name),
                        "ssh_port": record.get("ssh_port"),
                        "recovered": True,
                    })
        status_code = 409 if conflict else (agent_result.get("status_code") if agent_result.get("status_code", 500) >= 400 else 500)
        return jsonify({"error": agent_result.get("error", "Agent 创建容器失败")}), status_code

    final_ssh_port = agent_result.get("ssh_port") or ssh_port
    try:
        final_ssh_port = int(final_ssh_port) if final_ssh_port else None
    except (TypeError, ValueError):
        final_ssh_port = ssh_port
    with data_lock:
        data = load_data()
        resolved_gpu_devices = agent_result.get("gpu_devices", requested_gpu_devices)
        cid, record, _, _ = register_container_record(
            data,
            server_id,
            server,
            name,
            assigned_to=assigned_to,
            login_user=login_user,
            image=agent_body["image"],
            ssh_port=final_ssh_port,
            mounts=mounts,
            cpu_limit=cpu_limit,
            mem_limit=mem_limit,
            pids_limit=agent_body["pids_limit"],
            gpu_enabled=gpu_enabled,
            gpu_driver="nvidia" if gpu_enabled else "",
            gpu_devices=resolved_gpu_devices if isinstance(resolved_gpu_devices, list) else requested_gpu_devices,
            gpu_mode=agent_body["gpu_mode"] if gpu_enabled else "",
            rootfs_limit=rootfs_limit,
            image_mode=agent_result.get("image_mode", ""),
            allow_sudo=agent_result.get("allow_sudo", agent_body["allow_sudo"]),
            password_access=agent_result.get("password_access", agent_body["password_access"]),
            config_volume=agent_result.get("config_volume", ""),
            password_file=agent_result.get("password_file", ""),
            agent_container_id=agent_result.get("container_id", ""),
            status=agent_result.get("status", "running"),
            created_at=datetime.now().isoformat(),
            created_by=session["user"],
        )
        append_audit(data, f"创建容器 {name}，服务器 {server_id}")
        save_data(data)
    return jsonify({
        "ok": True,
        "id": cid,
        "ssh_cmd": record.get("ssh_cmd", build_ssh_cmd(login_user, final_ssh_port, ssh_host)),
        "name": record.get("name", name),
        "ssh_port": record.get("ssh_port"),
    })

@app.route("/api/containers/<cid>", methods=["DELETE"])
@login_required
@role_required("admin", "allocator")
def api_del_container(cid):
    agent_warnings = {}
    with data_lock:
        data = load_data()
        container = data["containers"].get(cid)
    if container:
        server = data["servers"].get(container.get("server_id", ""))
        if server:
            result = call_agent(server, f"/containers/{container.get('name')}/remove", method="DELETE", timeout=60)
            not_found = (
                result.get("status_code") == 404
                or "No such container" in str(result.get("error", ""))
                or "No such object" in str(result.get("error", ""))
            )
            if not result.get("ok") and not not_found:
                return jsonify({"error": result.get("error", "Agent 删除容器失败")}), 500
            agent_warnings = {
                "volume_warning": result.get("volume_warning"),
                "image_warning": result.get("image_warning"),
            }
    with data_lock:
        data = load_data()
        container = data["containers"].pop(cid, None)
        append_audit(data, f"删除容器 {container.get('name', cid) if container else cid}", "WARN")
        save_data(data)
    return jsonify({"ok": True, **agent_warnings})

@app.route("/api/containers/<cid>/resources", methods=["PATCH"])
@login_required
@role_required("admin", "allocator")
def api_update_container_resources(cid):
    body = request.json or {}
    with data_lock:
        data = load_data()
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

    with data_lock:
        data = load_data()
        container = data["containers"].get(cid)
        if not container:
            return jsonify({"error": "容器不存在"}), 404
        container["cpu_limit"] = payload["cpu"]
        container["mem_limit"] = payload["memory"]
        container["pids_limit"] = result.get("pids_limit", payload["pids_limit"])
        append_audit(data, f"更新容器资源 {container.get('name', cid)}")
        save_data(data)
    return jsonify({"ok": True, "pids_limit": result.get("pids_limit", payload["pids_limit"])})

@app.route("/api/containers/<cid>/backup-preview", methods=["GET"])
@login_required
def api_container_backup_preview(cid):
    data = load_data()
    container = data["containers"].get(cid)
    if not container:
        return jsonify({"error": "容器不存在"}), 404
    server = data["servers"].get(container.get("server_id", ""))
    if not server:
        return jsonify({"error": "服务器不存在"}), 404
    result = call_agent(server, f"/containers/{container.get('name')}/backup-preview", timeout=60)
    if not result.get("ok"):
        status_code = result.get("status_code", 500)
        if status_code < 400:
            status_code = 500
        return jsonify({"error": result.get("error", "备份预估失败")}), status_code
    return jsonify(result)

@app.route("/api/containers/<cid>/backup-image", methods=["POST"])
@login_required
@role_required("admin", "allocator")
def api_container_backup_image(cid):
    body = request.json or {}
    with data_lock:
        data = load_data()
        container = data["containers"].get(cid)
        server = data["servers"].get(container.get("server_id", "")) if container else None
    if not container:
        return jsonify({"error": "容器不存在"}), 404
    if not server:
        return jsonify({"error": "服务器不存在"}), 404
    payload = {
        "image_name": body.get("image_name", ""),
    }
    result = call_agent(server, f"/containers/{container.get('name')}/backup-image", method="POST", body=payload, timeout=900)
    if not result.get("ok"):
        status_code = result.get("status_code", 500)
        if status_code < 400:
            status_code = 500
        return jsonify({"error": result.get("error", "容器备份失败")}), status_code
    with data_lock:
        data = load_data()
        append_audit(data, f"备份容器 {container.get('name', cid)} 为镜像 {result.get('image_ref', '')}，服务器 {container.get('server_id', '')}")
        save_data(data)
    return jsonify(result)

@app.route("/api/containers/<cid>/rebuild", methods=["POST"])
@login_required
@role_required("admin", "allocator")
def api_rebuild_container(cid):
    body = request.json or {}
    with data_lock:
        data = load_data()
        container = data["containers"].get(cid)
        server = data["servers"].get(container.get("server_id", "")) if container else None
    if not container:
        return jsonify({"error": "容器不存在"}), 404
    if not server:
        return jsonify({"error": "服务器不存在"}), 404

    payload = {
        "cpu_limit": body.get("cpu_limit", container.get("cpu_limit")),
        "mem_limit": body.get("mem_limit", container.get("mem_limit")),
        "pids_limit": body.get("pids_limit", container.get("pids_limit", 512)),
        "gpu_enabled": bool(body.get("gpu_enabled", container.get("gpu_enabled", False))),
        "gpu_devices": body.get("gpu_devices", container.get("gpu_devices", [])),
        "gpu_mode": body.get("gpu_mode", container.get("gpu_mode", "shared") or "shared"),
        "rootfs_limit": str(body.get("rootfs_limit", container.get("rootfs_limit", "")) or "").strip(),
        "source_type": body.get("source_type", "temporary_snapshot"),
        "image_ref": body.get("image_ref", ""),
    }
    result = call_agent(server, f"/containers/{container.get('name')}/rebuild", method="POST", body=payload, timeout=1200)
    if not result.get("ok"):
        status_code = result.get("status_code", 500)
        if status_code < 400:
            status_code = 500
        return jsonify({"error": result.get("error", "容器重建失败")}), status_code

    with data_lock:
        data = load_data()
        container = data["containers"].get(cid)
        if not container:
            return jsonify({"error": "容器不存在"}), 404
        container["cpu_limit"] = payload["cpu_limit"]
        container["mem_limit"] = payload["mem_limit"]
        container["pids_limit"] = payload["pids_limit"]
        container["gpu_enabled"] = bool(result.get("gpu_enabled", payload["gpu_enabled"]))
        container["gpu_driver"] = "nvidia" if container["gpu_enabled"] else ""
        container["gpu_devices"] = result.get("gpu_devices", payload["gpu_devices"]) if isinstance(result.get("gpu_devices", payload["gpu_devices"]), list) else payload["gpu_devices"]
        container["gpu_mode"] = result.get("gpu_mode", payload["gpu_mode"] if container["gpu_enabled"] else "")
        container["rootfs_limit"] = result.get("rootfs_limit", payload["rootfs_limit"])
        container["password_file"] = ""
        container["status"] = result.get("status", "running")
        container["agent_container_id"] = result.get("container_id", container.get("agent_container_id", ""))
        if result.get("ssh_port"):
            container["ssh_port"] = result.get("ssh_port")
            server_ssh_host = server.get("ssh_host") or server.get("host", "server-host")
            container["ssh_cmd"] = build_ssh_cmd(container.get("login_user", "dockeruser"), container["ssh_port"], server_ssh_host)
        if result.get("image"):
            container["image"] = result.get("image")
        if result.get("image_mode"):
            container["image_mode"] = result.get("image_mode")
        source_suffix = f" / {payload['image_ref']}" if payload.get("image_ref") else ""
        append_audit(
            data,
            f"重建容器 {container.get('name', cid)}，来源 {payload['source_type']}{source_suffix}"
        )
        save_data(data)
    return jsonify(result)

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
    body = request.json or {}
    uname = body.get("username", "")
    password = body.get("password", "")
    role = body.get("role", "allocator")
    if len(password) < 8:
        return jsonify({"error": "密码至少需要 8 位"}), 400
    if role not in ("admin", "allocator"):
        return jsonify({"error": "角色无效"}), 400
    with data_lock:
        data = load_data()
        if not uname or uname in data["users"]:
            return jsonify({"error": "用户名无效或已存在"}), 400
        data["users"][uname] = {
            "password": generate_password_hash(password),
            "role": role,
            "created_at": datetime.now().isoformat()
        }
        append_audit(data, f"添加平台用户 {uname}")
        save_data(data)
    return jsonify({"ok": True})

@app.route("/api/users/<uname>", methods=["DELETE"])
@login_required
@role_required("admin")
def api_del_user(uname):
    if uname == "admin":
        return jsonify({"error": "不能删除 admin"}), 400
    with data_lock:
        data = load_data()
        data["users"].pop(uname, None)
        append_audit(data, f"删除平台用户 {uname}", "WARN")
        save_data(data)
    return jsonify({"ok": True})

# ── 审计日志 ───────────────────────────────────────────────────────────────
@app.route("/api/logs")
@login_required
def api_logs():
    data = load_data()
    return jsonify({"logs": data.get("audit_logs", [])[-200:]})

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
        "audit_logs": data.get("audit_logs", []),
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
    with data_lock:
        data = load_data()
        for key in ("users", "servers", "containers", "templates", "audit_logs"):
            if key in payload:
                data[key] = payload[key]
        save_data(data)
    return jsonify({"ok": True})

if __name__ == "__main__":
    debug = os.environ.get("DEBUG", "0") == "1"
    port = int(os.environ.get("PANEL_PORT", "5000"))
    app.run(debug=debug, host="0.0.0.0", port=port, threaded=True)
