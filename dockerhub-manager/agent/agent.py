#!/usr/bin/env python3
"""
DockerHub Manager — Server Agent
部署到每台 Ubuntu 服务器，接收管理面板指令，执行 Docker 操作。

启动方式：
  python3 agent.py --port 5001 --token YOUR_SECRET_TOKEN

systemd 服务文件示例见文档末尾注释。
"""

import json
import os
import re
import shlex
import shutil
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path

from flask import Flask, Response, jsonify, request

app = Flask(__name__)

# ── 配置 ────────────────────────────────────────────────────────────────────
AGENT_TOKEN  = os.environ.get("AGENT_TOKEN", "changeme-agent-token")
AGENT_PORT   = int(os.environ.get("AGENT_PORT", 5001))
WORKDIR = Path(__file__).resolve().parent
SSH_PORT_MIN = 32000
SSH_PORT_MAX = 32999
DEFAULT_SSH_IMAGE = "lscr.io/linuxserver/openssh-server:latest"
AGENT_VERSION = "0.4.0"

# ── Token 鉴权 ───────────────────────────────────────────────────────────────
def check_token():
    token = request.headers.get("X-Agent-Token", "")
    return token == AGENT_TOKEN

def auth_required(f):
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if not check_token():
            return jsonify({"error": "Unauthorized"}), 401
        return f(*args, **kwargs)
    return decorated

# ── 工具函数 ────────────────────────────────────────────────────────────────
def run(cmd, timeout=60):
    """运行 shell 命令，返回 (returncode, stdout, stderr)"""
    if isinstance(cmd, str):
        cmd = shlex.split(cmd)
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout.strip(), r.stderr.strip()
    except subprocess.TimeoutExpired:
        return -1, "", "命令超时"
    except Exception as e:
        return -1, "", str(e)

def now():
    return datetime.now().isoformat(timespec="seconds")

def docker_available():
    code, out, _ = run("docker info --format '{{.ServerVersion}}'")
    return code == 0, out

def memory_bytes():
    try:
        with open("/proc/meminfo", "r", encoding="utf-8") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    return int(line.split()[1]) * 1024
    except OSError:
        pass
    return 0

def dir_size(path):
    total = 0
    path = Path(path)
    if not path.exists():
        return 0
    for item in path.rglob("*"):
        try:
            if item.is_file():
                total += item.stat().st_size
        except OSError:
            continue
    return total

def safe_name(value, default="dockeruser"):
    value = value or default
    value = re.sub(r"[^a-zA-Z0-9_-]", "_", value)
    value = value.strip("_-")
    return value or default

def normalize_roots(raw):
    roots = []
    if not isinstance(raw, list):
        return roots
    for item in raw:
        if not isinstance(item, dict):
            continue
        host_path = item.get("host_path", "")
        if not host_path.startswith("/"):
            continue
        roots.append({
            "host_path": str(Path(host_path).resolve()),
            "readonly": bool(item.get("readonly", False)),
        })
    return roots

def path_is_under(path, root):
    try:
        Path(path).resolve().relative_to(Path(root).resolve())
        return True
    except ValueError:
        return False

def validate_mounts(mounts, allowed_roots, puid, pgid):
    blocked_hosts = {"/", "/etc", "/proc", "/sys", "/var/run/docker.sock"}
    blocked_container_prefixes = ("/etc", "/bin", "/usr", "/proc", "/sys", "/var/run")
    roots = normalize_roots(allowed_roots)
    if not roots:
        return False, "服务器未配置允许挂载的宿主机目录", []

    normalized = []
    for mount in mounts:
        if isinstance(mount, str):
            parts = mount.split(":")
            if len(parts) < 2:
                return False, f"挂载格式无效: {mount}", []
            host_path, container_path = parts[0], parts[1]
            readonly = len(parts) > 2 and parts[2] == "ro"
        else:
            host_path = mount.get("host_path", "")
            container_path = mount.get("container_path", "")
            readonly = bool(mount.get("readonly", False))

        if not host_path.startswith("/") or not container_path.startswith("/"):
            return False, "挂载路径必须是绝对路径", []

        host_real = str(Path(host_path).resolve())
        if host_real in blocked_hosts:
            return False, f"禁止挂载危险路径: {host_real}", []
        if container_path == "/" or container_path.startswith(blocked_container_prefixes):
            return False, f"禁止挂载到容器系统路径: {container_path}", []

        matched_root = None
        for root in roots:
            if path_is_under(host_real, root["host_path"]):
                matched_root = root
                break
        if not matched_root:
            return False, f"宿主机路径不在允许范围内: {host_real}", []

        host_path_obj = Path(host_real)
        created = not host_path_obj.exists()
        host_path_obj.mkdir(parents=True, exist_ok=True)
        if not host_path_obj.exists():
            return False, f"宿主机路径不存在: {host_real}", []
        if created and not readonly:
            os.chown(host_real, puid, pgid)
            os.chmod(host_real, 0o770)

        if matched_root.get("readonly"):
            readonly = True
        normalized.append({
            "host_path": host_real,
            "container_path": container_path.rstrip("/") or "/workspace",
            "readonly": readonly,
        })
    return True, "", normalized

def valid_memory(value):
    return bool(re.match(r"^[1-9][0-9]*(m|g|M|G)?$", str(value or "")))

def is_linuxserver_openssh(image):
    return "linuxserver/openssh-server" in image

# ── API: 健康检查 ────────────────────────────────────────────────────────────
@app.route("/ping")
def ping():
    ok, version = docker_available()
    return jsonify({
        "status":         "ok",
        "agent_version":  AGENT_VERSION,
        "time":           now(),
        "docker_version": version if ok else "unavailable",
        "workdir":        str(WORKDIR)
    })

# ── API: 镜像 ────────────────────────────────────────────────────────────────
@app.route("/images")
@auth_required
def list_images():
    code, out, err = run("docker images --format '{{json .}}'")
    images = []
    if code == 0:
        for line in out.splitlines():
            try:
                images.append(json.loads(line))
            except Exception:
                pass
    return jsonify({"images": images, "error": err if code != 0 else None})

@app.route("/images/pull", methods=["POST"])
@auth_required
def pull_image():
    """拉取镜像，SSE 流式返回进度"""
    body  = request.get_json(silent=True) or {}
    image = body.get("image", DEFAULT_SSH_IMAGE)

    def generate():
        proc = subprocess.Popen(
            ["docker", "pull", image],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True
        )
        for line in proc.stdout:
            yield f"data: {json.dumps({'msg': line.rstrip()})}\n\n"
        proc.wait()
        status = "ok" if proc.returncode == 0 else "error"
        yield f"data: {json.dumps({'done': True, 'status': status})}\n\n"

    return Response(generate(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

# ── API: 容器 ────────────────────────────────────────────────────────────────
@app.route("/containers")
@auth_required
def list_containers():
    code, out, err = run(
        "docker ps -a --format '{{json .}}'"
    )
    containers = []
    if code == 0:
        for line in out.splitlines():
            try:
                containers.append(json.loads(line))
            except Exception:
                pass
    return jsonify({"containers": containers})

@app.route("/containers/create", methods=["POST"])
@auth_required
def create_container():
    """
    创建并启动一个隔离的用户容器（内置 SSH）。
    请求体示例：
    {
      "name":      "user_alice",
      "image":     "lscr.io/linuxserver/openssh-server:latest",
      "ssh_port":  32001,
      "cpu":       "2",
      "memory":    "8g",
      "mounts":    ["/mnt/data:/workspace"]
    }
    """
    body     = request.get_json(silent=True) or {}
    name     = body.get("name", f"user_{int(time.time())}")
    image    = body.get("image", DEFAULT_SSH_IMAGE)
    ssh_port = int(body.get("ssh_port", 32001))
    cpu      = str(body.get("cpu", "1"))
    memory   = str(body.get("memory", "1g"))
    pids_limit = int(body.get("pids_limit", 512))
    puid     = int(body.get("puid", 1000))
    pgid     = int(body.get("pgid", 1000))
    login_user = safe_name(body.get("login_user"), "dockeruser")
    public_key = (body.get("ssh_public_key") or "").strip()
    password_access = bool(body.get("password_access", False))
    ssh_password = body.get("ssh_password", "")
    allow_sudo = bool(body.get("allow_sudo", False))
    mounts   = body.get("mounts", [])
    allowed_roots = body.get("allowed_mount_roots", [])

    if ssh_port < SSH_PORT_MIN or ssh_port > SSH_PORT_MAX:
        return jsonify({
            "ok": False,
            "error": f"SSH 端口必须位于 {SSH_PORT_MIN}-{SSH_PORT_MAX}"
        }), 400
    if not valid_memory(memory):
        return jsonify({"ok": False, "error": "内存限制格式无效"}), 400
    try:
        if float(cpu) <= 0:
            raise ValueError
    except ValueError:
        return jsonify({"ok": False, "error": "CPU 限制无效"}), 400
    if pids_limit <= 0:
        return jsonify({"ok": False, "error": "PIDs 限制无效"}), 400
    if puid <= 0 or pgid <= 0:
        return jsonify({"ok": False, "error": "PUID 和 PGID 必须为正整数"}), 400
    linuxserver_openssh = is_linuxserver_openssh(image)
    if password_access and not linuxserver_openssh:
        return jsonify({"ok": False, "error": "密码登录仅支持默认 LinuxServer OpenSSH 镜像"}), 400
    if password_access and len(ssh_password) < 8:
        return jsonify({"ok": False, "error": "SSH 密码至少需要 8 位"}), 400
    if not public_key and not password_access:
        return jsonify({"ok": False, "error": "请填写 SSH 公钥，或启用密码登录"}), 400

    ok, error, normalized_mounts = validate_mounts(mounts, allowed_roots, puid, pgid)
    if not ok:
        return jsonify({"ok": False, "error": error}), 400

    mount_args = []
    for m in normalized_mounts:
        mode = "ro" if m["readonly"] else "rw"
        mount_args += ["-v", f"{m['host_path']}:{m['container_path']}:{mode}"]

    escaped_user = shlex.quote(login_user)
    escaped_key = shlex.quote(public_key)
    sudo_cmd = ""
    if allow_sudo:
        sudo_cmd = "apt-get install -y sudo -qq && usermod -aG sudo {user} && ".format(user=escaped_user)

    bootstrap = (
        "set -e; "
        "export DEBIAN_FRONTEND=noninteractive; "
        "echo '[bootstrap] start'; "
        "if ! command -v sshd >/dev/null 2>&1; then "
        "echo '[bootstrap] installing openssh-server'; "
        "apt-get update -qq && apt-get install -y openssh-server -qq; "
        "fi; "
        "echo '[bootstrap] configuring ssh user'; "
        "mkdir -p /run/sshd; "
        "useradd -m -s /bin/bash {user} 2>/dev/null || true; "
        "mkdir -p /home/{user}/.ssh; "
        "touch /home/{user}/.ssh/authorized_keys; "
        "if [ -n {key} ]; then echo {key} > /home/{user}/.ssh/authorized_keys; fi; "
        "chown -R {user}:{user} /home/{user}/.ssh; "
        "chmod 700 /home/{user}/.ssh; chmod 600 /home/{user}/.ssh/authorized_keys; "
        "{sudo_cmd}"
        "sed -i 's/^#\\?PermitRootLogin .*/PermitRootLogin no/' /etc/ssh/sshd_config; "
        "sed -i 's/^#\\?PasswordAuthentication .*/PasswordAuthentication no/' /etc/ssh/sshd_config; "
        "grep -q '^PubkeyAuthentication yes' /etc/ssh/sshd_config || echo 'PubkeyAuthentication yes' >> /etc/ssh/sshd_config; "
        "echo '[bootstrap] starting sshd'; "
        "/usr/sbin/sshd -D"
    ).format(user=escaped_user, key=escaped_key, sudo_cmd=sudo_cmd)

    container_ssh_port = 2222 if linuxserver_openssh else 22
    image_mode = "linuxserver-openssh" if linuxserver_openssh else "generic-bootstrap"
    cmd = [
        "docker", "run", "-d",
        "--name",    name,
        "-p",        f"{ssh_port}:{container_ssh_port}",
        "--cpus",    cpu,
        "--memory",  memory,
        "--pids-limit", str(pids_limit),
        "--security-opt", "no-new-privileges",
        "--cap-drop", "NET_RAW",
        "--label", "manager=dockerhub",
        "--label", f"manager.login_user={login_user}",
        "--label", f"manager.container_ssh_port={container_ssh_port}",
        "--label", f"manager.image_mode={image_mode}",
        "--restart", "unless-stopped",
    ] + mount_args

    if linuxserver_openssh:
        config_volume = f"dockerhub_config_{safe_name(name)}"
        cmd += [
            "--label", f"manager.config_volume={config_volume}",
            "-v", f"{config_volume}:/config",
            "-e", f"PUID={puid}",
            "-e", f"PGID={pgid}",
            "-e", "TZ=Etc/UTC",
            "-e", f"PUBLIC_KEY={public_key}",
            "-e", f"USER_NAME={login_user}",
            "-e", f"SUDO_ACCESS={'true' if allow_sudo else 'false'}",
            "-e", f"PASSWORD_ACCESS={'true' if password_access else 'false'}",
            image,
        ]
        password_file = None
        if password_access:
            secret_dir = WORKDIR / "secrets"
            secret_dir.mkdir(mode=0o700, exist_ok=True)
            password_file = secret_dir / f"{safe_name(name)}.password"
            password_file.write_text(ssh_password, encoding="utf-8")
            password_file.chmod(0o600)
            cmd[-1:-1] = [
                "--label", f"manager.password_file={password_file}",
                "-v", f"{password_file}:/run/secrets/dockerhub_ssh_password:ro",
                "-e", "USER_PASSWORD_FILE=/run/secrets/dockerhub_ssh_password",
            ]
    else:
        password_file = None
        cmd += [
            image,
            "/bin/bash", "-c",
            bootstrap
        ]

    code, out, err = run(cmd, timeout=300)
    if code != 0:
        if password_file:
            password_file.unlink(missing_ok=True)
        return jsonify({"ok": False, "error": err}), 500

    container_id = out
    ssh_cmd = f"ssh -p {ssh_port} {login_user}@<SERVER_HOST>"
    return jsonify({
        "ok":           True,
        "container_id": container_id,
        "ssh_cmd":      ssh_cmd,
        "name":         name
    })

@app.route("/containers/<name>/stop", methods=["POST"])
@auth_required
def stop_container(name):
    code, out, err = run(f"docker stop {name}")
    return jsonify({"ok": code == 0, "error": err if code != 0 else None})

@app.route("/containers/<name>/start", methods=["POST"])
@auth_required
def start_container(name):
    code, out, err = run(f"docker start {name}")
    return jsonify({"ok": code == 0, "error": err if code != 0 else None})

@app.route("/containers/<name>/resources", methods=["PATCH"])
@auth_required
def update_container_resources(name):
    body = request.get_json(silent=True) or {}
    cpu = str(body.get("cpu", ""))
    memory = str(body.get("memory", ""))
    pids_limit = str(body.get("pids_limit", ""))

    cmd = ["docker", "update"]
    if cpu:
        try:
            if float(cpu) <= 0:
                raise ValueError
        except ValueError:
            return jsonify({"ok": False, "error": "CPU 限制无效"}), 400
        cmd += ["--cpus", cpu]
    if memory:
        if not valid_memory(memory):
            return jsonify({"ok": False, "error": "内存限制格式无效"}), 400
        cmd += ["--memory", memory]
    if pids_limit:
        if not pids_limit.isdigit() or int(pids_limit) <= 0:
            return jsonify({"ok": False, "error": "PIDs 限制无效"}), 400
        cmd += ["--pids-limit", pids_limit]

    cmd.append(name)
    code, out, err = run(cmd)
    return jsonify({"ok": code == 0, "error": err if code != 0 else None})

@app.route("/containers/<name>/remove", methods=["DELETE"])
@auth_required
def remove_container(name):
    _, config_volume, _ = run([
        "docker", "inspect", name,
        "--format", "{{index .Config.Labels \"manager.config_volume\"}}"
    ], timeout=10)
    _, password_file, _ = run([
        "docker", "inspect", name,
        "--format", "{{index .Config.Labels \"manager.password_file\"}}"
    ], timeout=10)
    run(f"docker stop {name}")
    code, out, err = run(f"docker rm -f {name}")
    volume_error = None
    if code == 0 and config_volume:
        volume_code, _, volume_err = run(["docker", "volume", "rm", config_volume], timeout=30)
        if volume_code != 0:
            volume_error = volume_err
    if code == 0 and password_file:
        Path(password_file).unlink(missing_ok=True)
    return jsonify({
        "ok": code == 0,
        "error": err if code != 0 else None,
        "volume_warning": volume_error,
    })

@app.route("/containers/<name>/logs")
@auth_required
def container_logs(name):
    """SSE 流式推送容器日志"""
    def generate():
        proc = subprocess.Popen(
            ["docker", "logs", "--follow", "--tail", "50", name],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True
        )
        try:
            for line in proc.stdout:
                yield f"data: {json.dumps({'msg': line.rstrip()})}\n\n"
        finally:
            proc.terminate()

    return Response(generate(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

@app.route("/containers/<name>/status")
@auth_required
def container_status(name):
    """返回容器状态和最近日志，供部署验证与故障排查。"""
    code, out, err = run([
        "docker", "inspect", name,
        "--format", "{{json .State}}"
    ])
    if code != 0:
        return jsonify({"ok": False, "error": err or "容器不存在"}), 404

    try:
        state = json.loads(out)
    except ValueError:
        state = {"raw": out}

    _, logs, logs_err = run(["docker", "logs", "--tail", "80", name], timeout=20)
    _, container_ssh_port, _ = run([
        "docker", "inspect", name,
        "--format", "{{index .Config.Labels \"manager.container_ssh_port\"}}"
    ], timeout=10)
    container_ssh_port = container_ssh_port or "22"
    _, image_mode, _ = run([
        "docker", "inspect", name,
        "--format", "{{index .Config.Labels \"manager.image_mode\"}}"
    ], timeout=10)
    _, ports, ports_err = run(["docker", "port", name, f"{container_ssh_port}/tcp"], timeout=10)
    _, sshd_process, sshd_process_err = run([
        "docker", "exec", name, "/bin/sh", "-lc",
        "ps -ef | grep '[s]shd' || true"
    ], timeout=10)
    _, sshd_listen, sshd_listen_err = run([
        "docker", "exec", name, "/bin/sh", "-lc",
        f"(command -v ss >/dev/null && ss -lntp | grep ':{container_ssh_port} ') || "
        f"(command -v netstat >/dev/null && netstat -lntp | grep ':{container_ssh_port} ') || true"
    ], timeout=10)
    _, login_user, _ = run([
        "docker", "inspect", name,
        "--format", "{{index .Config.Labels \"manager.login_user\"}}"
    ], timeout=10)
    if image_mode == "linuxserver-openssh":
        authorized_key = "managed_by_image"
        authorized_key_err = ""
    else:
        _, authorized_key, authorized_key_err = run([
            "docker", "exec", name, "/bin/sh", "-lc",
            f"test -s /home/{safe_name(login_user)}/.ssh/authorized_keys && echo present || echo missing"
        ], timeout=10)
    return jsonify({
        "ok": True,
        "name": name,
        "state": state,
        "logs": logs,
        "logs_error": logs_err or None,
        "port_22": ports,
        "port_error": ports_err or None,
        "container_ssh_port": container_ssh_port,
        "image_mode": image_mode,
        "login_user": login_user,
        "authorized_key": authorized_key,
        "authorized_key_error": authorized_key_err or None,
        "sshd_process": sshd_process,
        "sshd_process_error": sshd_process_err or None,
        "sshd_listen": sshd_listen,
        "sshd_listen_error": sshd_listen_err or None,
    })

# ── API: 系统信息 ────────────────────────────────────────────────────────────
@app.route("/sysinfo")
@auth_required
def sysinfo():
    docker_ok, docker_version = docker_available()
    return jsonify({
        "ok":             True,
        "cpu_cores":      os.cpu_count() or 1,
        "memory_bytes":   memory_bytes(),
        "docker_ok":      docker_ok,
        "docker_version": docker_version if docker_ok else "unavailable",
        "workdir":        str(WORKDIR),
        "time":       now()
    })

@app.route("/checks", methods=["GET", "POST"])
@auth_required
def checks():
    body = request.get_json(silent=True) or {}
    docker_ok, docker_version = docker_available()
    warnings = []

    if not str(WORKDIR).split("/")[-1].startswith("."):
        warnings.append("Agent 工作目录不是隐藏目录")

    workdir_size = dir_size(WORKDIR)
    if workdir_size > 100 * 1024 * 1024:
        warnings.append("Agent 工作目录超过 100MB")

    mount_results = []
    for root in normalize_roots(body.get("mount_roots", [])):
        path = Path(root["host_path"])
        usage = shutil.disk_usage(path if path.exists() else "/")
        mount_results.append({
            "path": root["host_path"],
            "exists": path.exists(),
            "writable": os.access(path, os.W_OK) if path.exists() else False,
            "free_gb": round(usage.free / 1024 / 1024 / 1024, 2),
        })
        if not path.exists():
            warnings.append(f"挂载根目录不存在: {root['host_path']}")
        elif not os.access(path, os.W_OK) and not root.get("readonly"):
            warnings.append(f"挂载根目录不可写: {root['host_path']}")

    _, privileged, _ = run("docker ps --filter label=manager=dockerhub --format '{{.Names}}'")

    return jsonify({
        "ok": docker_ok,
        "docker_ok": docker_ok,
        "docker_version": docker_version if docker_ok else "unavailable",
        "agent_user": os.environ.get("USER", "root"),
        "workdir": str(WORKDIR),
        "workdir_size_mb": round(workdir_size / 1024 / 1024, 2),
        "cpu_cores": os.cpu_count() or 1,
        "memory_bytes": memory_bytes(),
        "managed_containers": len([line for line in privileged.splitlines() if line.strip()]),
        "mount_roots": mount_results,
        "warnings": warnings,
        "time": now(),
    })

# ── 主入口 ───────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--port",  type=int, default=AGENT_PORT)
    parser.add_argument("--token", default=AGENT_TOKEN)
    args = parser.parse_args()

    AGENT_TOKEN = args.token
    print(f"[Agent] 启动中，端口 {args.port}，工作目录 {WORKDIR}")
    app.run(host="0.0.0.0", port=args.port, threaded=True)

# ──────────────────────────────────────────────────────────────────────────────
# systemd 服务文件（/etc/systemd/system/dockerhub-agent.service）：
#
# [Unit]
# Description=DockerHub Manager Agent
# After=network.target docker.service
#
# [Service]
# ExecStart=/usr/bin/python3 /opt/dockerhub-agent/agent.py --port 5001
# Environment=AGENT_TOKEN=your-secret-token
# Restart=always
# User=root
#
# [Install]
# WantedBy=multi-user.target
# ──────────────────────────────────────────────────────────────────────────────
