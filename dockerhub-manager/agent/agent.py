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
import shlex
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
DEFAULT_DATA = os.environ.get("DATA_PATH", "/mnt/data")

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

# ── API: 健康检查 ────────────────────────────────────────────────────────────
@app.route("/ping")
def ping():
    code, out, _ = run("docker info --format '{{.ServerVersion}}'")
    return jsonify({
        "status":         "ok",
        "time":           now(),
        "docker_version": out if code == 0 else "unavailable",
        "data_path":      DEFAULT_DATA
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
    body  = request.json or {}
    image = body.get("image", "ubuntu:22.04")

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
      "image":     "ubuntu:22.04",
      "ssh_port":  32001,
      "cpu":       "2",
      "memory":    "8g",
      "mounts":    ["/mnt/data:/workspace"]
    }
    """
    body     = request.json or {}
    name     = body.get("name", f"user_{int(time.time())}")
    image    = body.get("image", "ubuntu:22.04")
    ssh_port = int(body.get("ssh_port", 32001))
    cpu      = body.get("cpu", "2")
    memory   = body.get("memory", "8g")
    mounts   = body.get("mounts", [f"{DEFAULT_DATA}:/workspace"])

    mount_args = []
    for m in mounts:
        mount_args += ["-v", m]

    cmd = [
        "docker", "run", "-d",
        "--name",    name,
        "-p",        f"{ssh_port}:22",
        "--cpus",    cpu,
        "--memory",  memory,
        "--restart", "unless-stopped",
    ] + mount_args + [
        image,
        "/bin/bash", "-c",
        # 自动安装 openssh-server 并启动
        "apt-get update -qq && apt-get install -y openssh-server -qq && "
        "mkdir -p /run/sshd && "
        "echo 'PermitRootLogin yes' >> /etc/ssh/sshd_config && "
        "echo 'root:dockerpass' | chpasswd && "
        "/usr/sbin/sshd -D"
    ]

    code, out, err = run(cmd, timeout=120)
    if code != 0:
        return jsonify({"ok": False, "error": err}), 500

    container_id = out
    ssh_cmd = f"ssh -p {ssh_port} root@<SERVER_HOST>"
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

@app.route("/containers/<name>/remove", methods=["DELETE"])
@auth_required
def remove_container(name):
    run(f"docker stop {name}")
    code, out, err = run(f"docker rm -f {name}")
    return jsonify({"ok": code == 0, "error": err if code != 0 else None})

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

# ── API: 系统信息 ────────────────────────────────────────────────────────────
@app.route("/sysinfo")
@auth_required
def sysinfo():
    _, cpu_out,  _ = run("top -bn1 | grep 'Cpu(s)' | awk '{print $2}'")
    _, mem_out,  _ = run("free -m | awk '/Mem/{print $2,$3}'")
    _, disk_out, _ = run(f"df -h {DEFAULT_DATA} | tail -1 | awk '{{print $2,$3,$5}}'")
    return jsonify({
        "cpu_usage":  cpu_out,
        "memory":     mem_out,
        "disk":       disk_out,
        "data_path":  DEFAULT_DATA,
        "time":       now()
    })

# ── 主入口 ───────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--port",  type=int, default=AGENT_PORT)
    parser.add_argument("--token", default=AGENT_TOKEN)
    args = parser.parse_args()

    AGENT_TOKEN = args.token
    print(f"[Agent] 启动中，端口 {args.port}，数据路径 {DEFAULT_DATA}")
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
# Environment=DATA_PATH=/mnt/data
# Restart=always
# User=root
#
# [Install]
# WantedBy=multi-user.target
# ──────────────────────────────────────────────────────────────────────────────
