#!/usr/bin/env python3
"""
DockerHub Manager — Server Agent
部署到每台 Ubuntu 服务器，接收管理面板指令，执行 Docker 操作。

启动方式：
  python3 agent.py --port 5001 --token YOUR_SECRET_TOKEN

systemd 服务文件示例见文档末尾注释。
"""

import csv
import json
import os
import re
import shlex
import shutil
import socket
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path

from flask import Flask, Response, jsonify, request
from werkzeug.exceptions import HTTPException

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

def find_available_ssh_port():
    for port in range(SSH_PORT_MIN, SSH_PORT_MAX + 1):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind(("0.0.0.0", port))
                return port
            except OSError:
                continue
    return None

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

def ensure_dir(path: Path, mode=0o700):
    if path.exists() and not path.is_dir():
        path.unlink(missing_ok=True)
    path.mkdir(mode=mode, parents=True, exist_ok=True)

def cleanup_secret_path(path):
    secret_path = Path(path)
    if not secret_path.exists():
        return
    if secret_path.is_dir():
        shutil.rmtree(secret_path)
        return
    secret_path.unlink(missing_ok=True)

def prepare_secret_file(path, content):
    secret_path = Path(path)
    ensure_dir(secret_path.parent, mode=0o700)
    cleanup_secret_path(secret_path)
    temp_path = secret_path.with_name(f".{secret_path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    cleanup_secret_path(temp_path)
    temp_path.write_text(content, encoding="utf-8")
    temp_path.chmod(0o600)
    os.replace(temp_path, secret_path)
    return secret_path

def inspect_container_raw(name):
    code, out, err = run(["docker", "inspect", name, "--format", "{{json .}}"], timeout=20)
    if code != 0:
        return None, err or "容器不存在"
    try:
        return json.loads(out), None
    except ValueError:
        return None, "docker inspect 返回无效 JSON"

def inspect_container_details(name):
    raw, error = inspect_container_raw(name)
    if not raw:
        return None, error
    labels = raw.get("Config", {}).get("Labels") or {}
    ports = []
    for container_port, bindings in (raw.get("NetworkSettings", {}).get("Ports") or {}).items():
        for binding in bindings or []:
            ports.append({
                "container_port": container_port,
                "host_ip": binding.get("HostIp", ""),
                "host_port": binding.get("HostPort", ""),
            })
    state = raw.get("State", {}) or {}
    ssh_port = ""
    label_port = str(labels.get("manager.container_ssh_port") or "")
    for port in ports:
        if label_port and port["container_port"] == f"{label_port}/tcp":
            ssh_port = port["host_port"]
            break
    if not ssh_port and ports:
        ssh_port = ports[0]["host_port"]
    return {
        "id": raw.get("Id", ""),
        "name": (raw.get("Name") or "").lstrip("/"),
        "image": raw.get("Config", {}).get("Image", ""),
        "status": state.get("Status", "unknown"),
        "created_at": raw.get("Created", ""),
        "labels": labels,
        "ports": ports,
        "ports_text": ", ".join(
            f"{item['host_ip']}:{item['host_port']}->{item['container_port']}" for item in ports if item.get("host_port")
        ),
        "ssh_port": ssh_port,
    }, None

def inspect_image_details(image_ref):
    code, out, err = run(["docker", "image", "inspect", image_ref, "--format", "{{json .}}"], timeout=30)
    if code != 0:
        return None, err or "镜像不存在"
    try:
        raw = json.loads(out)
    except ValueError:
        return None, "docker image inspect 返回无效 JSON"
    config = raw.get("Config", {}) or {}
    return {
        "id": raw.get("Id", ""),
        "repo_tags": raw.get("RepoTags") or [],
        "repo_digests": raw.get("RepoDigests") or [],
        "size_bytes": int(raw.get("Size") or 0),
        "created_at": raw.get("Created", ""),
        "labels": config.get("Labels") or {},
        "env": config.get("Env") or [],
        "entrypoint": config.get("Entrypoint") or [],
        "cmd": config.get("Cmd") or [],
        "raw": raw,
    }, None

def format_bytes_human(value):
    try:
        size = float(value or 0)
    except (TypeError, ValueError):
        return "0 B"
    if size <= 0:
        return "0 B"
    units = ["B", "KB", "MB", "GB", "TB"]
    index = 0
    while size >= 1024 and index < len(units) - 1:
        size /= 1024
        index += 1
    digits = 0 if index == 0 or size >= 100 else 1
    return f"{size:.{digits}f} {units[index]}"

def parse_label_bool(value):
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}

def bool_label(value):
    return "true" if value else "false"

def preferred_image_reference(repository, tag, fallback=""):
    repo = str(repository or "").strip()
    image_tag = str(tag or "").strip()
    if repo and repo != "<none>":
        if image_tag and image_tag != "<none>":
            return f"{repo}:{image_tag}"
        return repo
    return str(fallback or "").strip()

def preferred_repo_tag(repo_tags, fallback=""):
    for item in repo_tags or []:
        raw = str(item or "").strip()
        if raw and not raw.startswith("<none>:"):
            return raw
    return str(fallback or "").strip()

def build_backup_image_name(container_name):
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"dockerhub-backup/{safe_name(container_name, 'container')}:{stamp}"

def build_temp_snapshot_image_name(container_name):
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"dockerhub-temp/{safe_name(container_name, 'container')}:{stamp}"

def valid_image_reference(image_ref):
    raw = str(image_ref or "").strip()
    return bool(raw) and not raw.startswith("-") and not any(ch.isspace() for ch in raw)

def dockerfile_label_instruction(key, value):
    return f"LABEL {key}={json.dumps(str(value or ''))}"

def commit_container_image(container_name, image_name, labels=None):
    cmd = ["docker", "commit"]
    for key, value in (labels or {}).items():
        cmd += ["-c", dockerfile_label_instruction(key, value)]
    cmd += [container_name, image_name]
    code, out, err = run(cmd, timeout=600)
    if code != 0:
        return None, err or "容器提交镜像失败"
    image_info, inspect_error = inspect_image_details(image_name)
    if not image_info:
        return None, inspect_error
    return image_info, None

def env_list_to_map(env_list):
    env_map = {}
    order = []
    for item in env_list or []:
        key, sep, value = str(item or "").partition("=")
        key = key.strip()
        if not key:
            continue
        if key not in env_map:
            order.append(key)
        env_map[key] = value if sep else ""
    return env_map, order

def env_map_to_list(env_map, order):
    keys = list(order or []) + [key for key in env_map.keys() if key not in (order or [])]
    items = []
    for key in keys:
        if key not in env_map:
            continue
        items.append(f"{key}={env_map[key]}")
    return items

def extract_container_port_bindings(raw):
    bindings = []
    host_config = raw.get("HostConfig", {}) or {}
    for container_port, entries in (host_config.get("PortBindings") or {}).items():
        for item in entries or []:
            host_port = str(item.get("HostPort") or "").strip()
            host_ip = str(item.get("HostIp") or "").strip()
            if not host_port:
                continue
            bindings.append({
                "container_port": str(container_port or "").strip(),
                "host_port": host_port,
                "host_ip": host_ip,
            })
    return bindings

def build_publish_arg(binding):
    host_port = str(binding.get("host_port") or "").strip()
    container_port = str(binding.get("container_port") or "").strip()
    host_ip = str(binding.get("host_ip") or "").strip()
    if not host_port or not container_port:
        return ""
    if host_ip and host_ip not in {"0.0.0.0", "::"}:
        return f"{host_ip}:{host_port}:{container_port}"
    return f"{host_port}:{container_port}"

def strip_secret_binds(binds):
    filtered = []
    removed = []
    for bind in binds or []:
        raw = str(bind or "").strip()
        if not raw:
            continue
        if ":/run/secrets/dockerhub_ssh_password" in raw:
            removed.append(raw)
            continue
        filtered.append(raw)
    return filtered, removed

def rollback_container_name(name):
    stamp = datetime.now().strftime("%Y%m%d%H%M%S")
    return safe_name(f"{name}__rollback__{stamp}", f"{name}_rollback")

def remove_image_quietly(image_ref):
    if not image_ref:
        return
    run(["docker", "image", "rm", image_ref], timeout=180)

def resolve_container_image_mode(labels):
    image_mode = str((labels or {}).get("manager.image_mode") or "").strip()
    if image_mode:
        return image_mode
    container_ssh_port = str((labels or {}).get("manager.container_ssh_port") or "").strip()
    return "linuxserver-openssh" if container_ssh_port == "2222" else "generic-bootstrap"

def resolve_allow_sudo(labels, host_config):
    if "manager.allow_sudo" in (labels or {}):
        return parse_label_bool(labels.get("manager.allow_sudo"))
    security_opts = [str(item or "") for item in (host_config or {}).get("SecurityOpt") or []]
    return not any("no-new-privileges" in item for item in security_opts)

def resolve_password_access(labels, raw_config):
    if "manager.password_access" in (labels or {}):
        return parse_label_bool(labels.get("manager.password_access"))
    if (labels or {}).get("manager.password_file"):
        return True
    env_map, _ = env_list_to_map((raw_config or {}).get("Env") or [])
    return str(env_map.get("PASSWORD_ACCESS", "")).strip().lower() == "true"

def normalize_rebuild_resources(body, raw):
    cpu = str(body.get("cpu", body.get("cpu_limit", "")) or "").strip()
    memory = str(body.get("memory", body.get("mem_limit", "")) or "").strip()
    pids_raw = body.get("pids_limit", "")
    host_config = raw.get("HostConfig", {}) or {}

    if not cpu:
        nano_cpus = int(host_config.get("NanoCpus") or 0)
        if nano_cpus > 0:
            cpu = f"{nano_cpus / 1_000_000_000:.3f}".rstrip("0").rstrip(".")
        else:
            cpu = "1"
    if not memory:
        memory_bytes_value = int(host_config.get("Memory") or 0)
        memory_gb = max(1, round(memory_bytes_value / (1024 ** 3))) if memory_bytes_value else 1
        memory = f"{memory_gb}g"
    if pids_raw in (None, ""):
        pids_limit = int(host_config.get("PidsLimit") or 512)
    else:
        try:
            pids_limit = int(pids_raw)
        except (TypeError, ValueError):
            pids_limit = 0
    if not valid_memory(memory):
        return None, "内存限制格式无效"
    try:
        if float(cpu) <= 0:
            raise ValueError
    except ValueError:
        return None, "CPU 限制无效"
    if pids_limit <= 0:
        return None, "PIDs 限制无效"
    return {
        "cpu": cpu,
        "memory": memory,
        "pids_limit": pids_limit,
    }, ""

def resolve_rebuild_gpu_settings(body, labels):
    gpu_enabled = bool(body.get("gpu_enabled", parse_label_bool((labels or {}).get("manager.gpu_enabled"))))
    requested_devices = body.get("gpu_devices", None)
    if requested_devices is None:
        requested_devices = parse_label_csv((labels or {}).get("manager.gpu_devices", ""))
    gpu_devices = normalize_gpu_device_list(requested_devices)
    gpu_mode = str(body.get("gpu_mode", (labels or {}).get("manager.gpu_mode", "shared")) or "shared").strip() or "shared"
    if gpu_mode != "shared":
        return None, "当前仅支持共享 GPU 模式"
    selected_gpu_devices = []
    gpu_cli_value = ""
    if not gpu_enabled:
        return {
            "gpu_enabled": False,
            "gpu_devices": [],
            "gpu_mode": "",
            "gpu_driver": "",
            "gpu_cli_value": "",
        }, ""

    gpu_info = collect_gpu_info()
    if not gpu_info.get("supported"):
        return None, gpu_info.get("missing", ["当前服务器未满足 GPU 容器运行条件"])[0]
    available_devices = gpu_info.get("devices", [])
    available_indices = [str(item.get("index", "")).strip() for item in available_devices if str(item.get("index", "")).strip()]
    if not available_indices:
        return None, "当前服务器未检测到可用 GPU"
    if not gpu_devices:
        selected_gpu_devices = available_indices
    else:
        invalid_devices = [item for item in gpu_devices if item not in available_indices]
        if invalid_devices:
            return None, f"所选 GPU 不存在: {', '.join(invalid_devices)}"
        selected_gpu_devices = gpu_devices
    gpu_cli_value = "all" if set(selected_gpu_devices) == set(available_indices) else f"device={','.join(selected_gpu_devices)}"
    return {
        "gpu_enabled": True,
        "gpu_devices": selected_gpu_devices,
        "gpu_mode": "shared",
        "gpu_driver": "nvidia",
        "gpu_cli_value": gpu_cli_value,
    }, ""

def build_rebuild_create_command(raw, image_ref, image_mode, resource_settings, gpu_settings, rootfs_limit, labels, allow_sudo):
    host_config = raw.get("HostConfig", {}) or {}
    raw_config = raw.get("Config", {}) or {}
    password_access = resolve_password_access(labels, raw_config)
    cmd = [
        "docker", "create",
        "--name", (raw.get("Name") or "").lstrip("/"),
        "--cpus", resource_settings["cpu"],
        "--memory", resource_settings["memory"],
        "--pids-limit", str(resource_settings["pids_limit"]),
        "--restart", str((host_config.get("RestartPolicy") or {}).get("Name") or "unless-stopped"),
    ]

    for cap in host_config.get("CapDrop") or []:
        if cap:
            cmd += ["--cap-drop", str(cap)]

    security_opts = [str(item or "").strip() for item in host_config.get("SecurityOpt") or [] if str(item or "").strip()]
    security_opts = [item for item in security_opts if "no-new-privileges" not in item]
    if not allow_sudo:
        security_opts.append("no-new-privileges")
    for item in security_opts:
        cmd += ["--security-opt", item]

    for binding in extract_container_port_bindings(raw):
        publish_value = build_publish_arg(binding)
        if publish_value:
            cmd += ["-p", publish_value]

    binds, _ = strip_secret_binds(host_config.get("Binds") or [])
    for bind in binds:
        cmd += ["-v", bind]

    env_map, env_order = env_list_to_map(raw_config.get("Env") or [])
    env_map.pop("USER_PASSWORD_FILE", None)
    env_map["SUDO_ACCESS"] = "true" if allow_sudo else "false"
    env_map["PASSWORD_ACCESS"] = "true" if password_access else "false"
    if gpu_settings.get("gpu_enabled"):
        env_map["NVIDIA_VISIBLE_DEVICES"] = ",".join(gpu_settings["gpu_devices"])
        env_map["NVIDIA_DRIVER_CAPABILITIES"] = "compute,utility"
    else:
        env_map.pop("NVIDIA_VISIBLE_DEVICES", None)
        env_map.pop("NVIDIA_DRIVER_CAPABILITIES", None)
    for item in env_map_to_list(env_map, env_order):
        cmd += ["-e", item]

    next_labels = dict(raw_config.get("Labels") or {})
    next_labels["manager.image_mode"] = image_mode
    next_labels["manager.gpu_enabled"] = bool_label(gpu_settings.get("gpu_enabled"))
    next_labels["manager.gpu_driver"] = gpu_settings.get("gpu_driver", "")
    next_labels["manager.gpu_devices"] = ",".join(gpu_settings.get("gpu_devices", []))
    next_labels["manager.gpu_mode"] = gpu_settings.get("gpu_mode", "")
    next_labels["manager.rootfs_limit"] = rootfs_limit
    next_labels["manager.allow_sudo"] = bool_label(allow_sudo)
    next_labels["manager.password_access"] = bool_label(password_access)
    next_labels["manager.backup_kind"] = ""
    next_labels["manager.backup_source_container"] = ""
    next_labels["manager.backup_source_image"] = ""
    next_labels["manager.backup_created_at"] = ""
    next_labels.pop("manager.password_file", None)
    for key, value in next_labels.items():
        cmd += ["--label", f"{key}={value}"]

    if rootfs_limit:
        cmd += ["--storage-opt", f"size={rootfs_limit}"]
    if gpu_settings.get("gpu_enabled"):
        cmd += ["--gpus", gpu_settings["gpu_cli_value"]]

    entrypoint = list((raw.get("Config", {}) or {}).get("Entrypoint") or [])
    command = list((raw.get("Config", {}) or {}).get("Cmd") or [])
    if entrypoint:
        cmd += ["--entrypoint", entrypoint[0]]
        command = entrypoint[1:] + command

    cmd.append(image_ref)
    cmd += command
    return cmd

def docker_info_json():
    code, out, err = run(["docker", "info", "--format", "{{json .}}"], timeout=30)
    if code != 0:
        return None, err or "docker info 执行失败"
    try:
        return json.loads(out), None
    except ValueError:
        return None, "docker info 返回无效 JSON"

def parse_driver_status_map(driver_status):
    mapping = {}
    if not isinstance(driver_status, list):
        return mapping
    for row in driver_status:
        if isinstance(row, (list, tuple)) and len(row) >= 2:
            mapping[str(row[0])] = str(row[1])
    return mapping

def decode_proc_mount_field(value):
    return value.replace("\\040", " ").replace("\\011", "\t").replace("\\012", "\n").replace("\\134", "\\")

def mount_options_for_path(target_path):
    target = Path(target_path or "/").resolve()
    best_mount = None
    best_options = set()
    try:
        with open("/proc/mounts", "r", encoding="utf-8") as f:
            for line in f:
                parts = line.split()
                if len(parts) < 4:
                    continue
                mount_point = Path(decode_proc_mount_field(parts[1])).resolve()
                try:
                    target.relative_to(mount_point)
                except ValueError:
                    continue
                if best_mount is None or len(str(mount_point)) > len(str(best_mount)):
                    best_mount = mount_point
                    best_options = set(parts[3].split(","))
    except OSError:
        return set()
    return best_options

def collect_storage_capabilities():
    info, error = docker_info_json()
    if not info:
        return {
            "driver": "",
            "backing_filesystem": "",
            "docker_root_dir": "",
            "supports_rootfs_limit": False,
            "quota_flags": [],
            "reason": error or "Docker 信息读取失败",
        }

    driver = str(info.get("Driver") or "")
    docker_root_dir = str(info.get("DockerRootDir") or "")
    driver_status = parse_driver_status_map(info.get("DriverStatus") or [])
    backing_fs = (
        driver_status.get("Backing Filesystem")
        or driver_status.get("Backing filesystem")
        or driver_status.get("Backing FS")
        or ""
    )
    mount_options = mount_options_for_path(docker_root_dir or "/")
    quota_flags = [flag for flag in ("pquota", "prjquota") if flag in mount_options]

    supports_rootfs_limit = False
    reason = ""
    if driver == "overlay2":
        supports_rootfs_limit = backing_fs.lower() == "xfs" and bool(quota_flags)
        if not supports_rootfs_limit:
            reason = "overlay2 仅在 XFS 且启用 pquota/prjquota 时支持可写层限额"
    elif driver in {"btrfs", "zfs", "windowsfilter"}:
        supports_rootfs_limit = True
    else:
        reason = f"当前存储驱动 {driver or 'unknown'} 未启用可写层限额支持"

    return {
        "driver": driver,
        "backing_filesystem": backing_fs,
        "docker_root_dir": docker_root_dir,
        "supports_rootfs_limit": supports_rootfs_limit,
        "quota_flags": quota_flags,
        "reason": reason,
    }

SIZE_UNITS = {
    "b": 1,
    "kb": 1000,
    "mb": 1000 ** 2,
    "gb": 1000 ** 3,
    "tb": 1000 ** 4,
    "kib": 1024,
    "mib": 1024 ** 2,
    "gib": 1024 ** 3,
    "tib": 1024 ** 4,
}

def parse_size_to_bytes(value):
    raw = str(value or "").strip()
    if not raw or raw in {"0", "0B", "0.00B", "--"}:
        return 0
    match = re.match(r"^([0-9]+(?:\.[0-9]+)?)\s*([A-Za-z]+)?$", raw)
    if not match:
        return 0
    number = float(match.group(1))
    unit = (match.group(2) or "B").lower()
    factor = SIZE_UNITS.get(unit)
    if factor is None:
        return 0
    return int(number * factor)

def parse_percent(value):
    raw = str(value or "").strip().rstrip("%")
    if not raw:
        return 0.0
    try:
        return round(float(raw), 2)
    except ValueError:
        return 0.0

def parse_mem_usage_bytes(value):
    parts = str(value or "").split(" / ", 1)
    used = parse_size_to_bytes(parts[0]) if parts else 0
    limit = parse_size_to_bytes(parts[1]) if len(parts) > 1 else 0
    return used, limit

def parse_csv_int(value):
    raw = str(value or "").strip()
    if not raw or raw in {"-", "N/A", "[Not Supported]"}:
        return None
    try:
        return int(float(raw))
    except ValueError:
        return None

def parse_label_csv(value):
    items = []
    for item in str(value or "").split(","):
        item = item.strip()
        if item and item not in items:
            items.append(item)
    return items

def container_gpu_enabled(labels):
    return str((labels or {}).get("manager.gpu_enabled", "")).strip().lower() == "true"

def extract_container_id_candidates(cgroup_text):
    text = str(cgroup_text or "").lower()
    matches = re.findall(r"(?:docker[-/]|cri-containerd-|containerd-)?([0-9a-f]{12,64})(?:\.scope)?", text)
    candidates = []
    for item in matches:
        token = item.strip()
        if token and token not in candidates:
            candidates.append(token)
    return candidates

def resolve_container_name_from_pid(pid, managed_container_ids):
    try:
        with open(f"/proc/{pid}/cgroup", "r", encoding="utf-8") as f:
            cgroup_text = f.read()
    except OSError:
        return None
    candidates = extract_container_id_candidates(cgroup_text)
    if not candidates:
        return None
    full_ids = {
        key: value for key, value in managed_container_ids.items()
        if len(key) == 64
    }
    for token in candidates:
        matched = managed_container_ids.get(token)
        if matched:
            return matched
        short_token = token[:12]
        matched = managed_container_ids.get(short_token)
        if matched:
            return matched
        for full_id, name in full_ids.items():
            if full_id.startswith(token):
                return name
    return None

def list_gpu_compute_processes(device_by_uuid):
    if not device_by_uuid:
        return [], None
    code, out, err = run([
        "nvidia-smi",
        "--query-compute-apps=gpu_uuid,pid,used_gpu_memory",
        "--format=csv,noheader,nounits",
    ], timeout=20)
    if code != 0:
        message = err or out or "nvidia-smi 进程列表读取失败"
        if "no running compute processes found" in message.lower():
            return [], None
        return [], message
    processes = []
    for raw_line in out.splitlines():
        line = raw_line.strip()
        if not line or line.lower().startswith("no running compute processes found"):
            continue
        row = next(csv.reader([line]), [])
        if len(row) < 3:
            continue
        gpu_uuid = row[0].strip()
        pid = parse_csv_int(row[1])
        used_mb = parse_csv_int(row[2])
        device = device_by_uuid.get(gpu_uuid)
        if pid is None or not device:
            continue
        processes.append({
            "gpu_uuid": gpu_uuid,
            "gpu_index": str(device.get("index", "")).strip(),
            "pid": pid,
            "used_gpu_memory_bytes": max(0, used_mb or 0) * 1024 * 1024,
        })
    return processes, None

def list_gpu_process_utilization():
    code, out, err = run(["nvidia-smi", "pmon", "-c", "1", "-s", "um"], timeout=20)
    if code != 0:
        reason = (err or out or "nvidia-smi pmon 执行失败").strip()
        return {}, False, reason
    usage = {}
    for raw_line in out.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) < 4:
            continue
        gpu_index = parts[0].strip()
        pid_text = parts[1].strip()
        if not pid_text.isdigit():
            continue
        util_value = parse_csv_int(parts[3])
        if util_value is None:
            continue
        key = (gpu_index, int(pid_text))
        usage[key] = usage.get(key, 0) + max(0, util_value)
    return usage, True, ""

def collect_container_gpu_metrics(managed_details):
    metrics = {}
    gpu_enabled_names = []
    for name, details in managed_details.items():
        labels = details.get("labels") or {}
        enabled = container_gpu_enabled(labels)
        configured_devices = parse_label_csv(labels.get("manager.gpu_devices", ""))
        metrics[name] = {
            "enabled": enabled,
            "driver": labels.get("manager.gpu_driver", ""),
            "configured_devices": configured_devices,
            "devices": [],
            "utilization_supported": False,
            "utilization_reason": "",
            "total_memory_used_bytes": 0,
            "total_util_percent": None,
            "active_process_count": 0,
        }
        if enabled:
            gpu_enabled_names.append(name)
    if not gpu_enabled_names:
        return metrics, []

    gpu_info = collect_gpu_info()
    device_list = gpu_info.get("devices", []) or []
    if not device_list:
        reason = (gpu_info.get("missing") or [gpu_info.get("docker_info_error") or "当前未检测到 GPU 设备"])[0]
        for name in gpu_enabled_names:
            metrics[name]["utilization_reason"] = str(reason)
        return metrics, []

    device_by_uuid = {
        str(device.get("uuid", "")).strip(): device
        for device in device_list
        if str(device.get("uuid", "")).strip()
    }
    processes, process_error = list_gpu_compute_processes(device_by_uuid)
    util_by_process, util_supported, util_reason = list_gpu_process_utilization()
    warnings = []
    if process_error:
        warnings.append(process_error)

    managed_container_ids = {}
    for name, details in managed_details.items():
        container_id = str(details.get("id", "")).strip().lower()
        if not container_id:
            continue
        managed_container_ids[container_id] = name
        managed_container_ids.setdefault(container_id[:12], name)

    pid_cache = {}
    for process in processes:
        pid = process["pid"]
        if pid not in pid_cache:
            pid_cache[pid] = resolve_container_name_from_pid(pid, managed_container_ids)
        container_name = pid_cache.get(pid)
        if not container_name or container_name not in metrics:
            continue
        metric = metrics[container_name]
        if not metric.get("enabled"):
            continue
        device_uuid = process["gpu_uuid"]
        device = device_by_uuid.get(device_uuid)
        if not device:
            continue
        device_index = str(device.get("index", "")).strip()
        device_entry = next((item for item in metric["devices"] if item.get("id") == device_index), None)
        if not device_entry:
            device_entry = {
                "id": device_index,
                "uuid": device_uuid,
                "name": device.get("name", ""),
                "container_memory_used_bytes": 0,
                "container_util_percent": 0,
                "device_util_percent": device.get("utilization_gpu", 0),
                "device_memory_used_bytes": device.get("memory_used_bytes", 0),
                "device_memory_total_bytes": device.get("memory_total_bytes", 0),
            }
            metric["devices"].append(device_entry)
        device_entry["container_memory_used_bytes"] += process["used_gpu_memory_bytes"]
        util_value = util_by_process.get((device_index, pid))
        if util_value is not None:
            device_entry["container_util_percent"] += util_value
        metric["active_process_count"] += 1

    for name, metric in metrics.items():
        if not metric.get("enabled"):
            continue
        metric["devices"].sort(key=lambda item: item.get("id", ""))
        metric["utilization_supported"] = util_supported
        metric["utilization_reason"] = "" if util_supported else util_reason
        metric["total_memory_used_bytes"] = sum(item.get("container_memory_used_bytes", 0) for item in metric["devices"])
        if util_supported:
            metric["total_util_percent"] = sum(item.get("container_util_percent", 0) for item in metric["devices"])
        else:
            metric["total_util_percent"] = None
    return metrics, warnings

def list_managed_container_names(include_stopped=True):
    cmd = ["docker", "ps"]
    if include_stopped:
        cmd.append("-a")
    cmd += ["--filter", "label=manager=dockerhub", "--format", "{{.Names}}"]
    code, out, err = run(cmd, timeout=20)
    if code != 0:
        return None, err or "读取容器列表失败"
    return [line.strip() for line in out.splitlines() if line.strip()], None

def inspect_container_size(name):
    code, out, err = run(["docker", "inspect", "--size", "--format", "{{json .}}", name], timeout=20)
    if code != 0:
        return None, err or "容器大小读取失败"
    try:
        raw = json.loads(out)
    except ValueError:
        return None, "容器大小返回无效 JSON"
    return {
        "disk_rw_bytes": int(raw.get("SizeRw") or 0),
        "disk_rootfs_bytes": int(raw.get("SizeRootFs") or 0),
    }, None

def collect_running_container_stats(names):
    if not names:
        return {}, None
    code, out, err = run(
        ["docker", "stats", "--no-stream", "--format", "{{json .}}"] + list(names),
        timeout=max(20, 5 + len(names) * 2),
    )
    if code != 0:
        return {}, err or "docker stats 执行失败"
    stats = {}
    for line in out.splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        name = row.get("Name") or row.get("Container") or ""
        if not name:
            continue
        memory_used, memory_limit = parse_mem_usage_bytes(row.get("MemUsage", ""))
        pids_value = str(row.get("PIDs", "")).strip()
        try:
            pids = int(pids_value)
        except ValueError:
            pids = None
        stats[name] = {
            "cpu_percent": parse_percent(row.get("CPUPerc", "")),
            "memory_used_bytes": memory_used,
            "memory_limit_bytes": memory_limit,
            "pids_current": pids,
        }
    return stats, None

def docker_supports_gpus_flag():
    code, out, _ = run(["docker", "run", "--help"], timeout=20)
    return code == 0 and "--gpus" in out

def collect_gpu_info():
    info, docker_info_error = docker_info_json()
    runtimes = {}
    if info:
        runtimes = info.get("Runtimes") or {}

    nvidia_smi_exists = shutil.which("nvidia-smi") is not None
    nvidia_ctk_exists = shutil.which("nvidia-ctk") is not None
    nvidia_runtime_exists = shutil.which("nvidia-container-runtime") is not None
    docker_has_gpus_flag = docker_supports_gpus_flag()
    toolkit_installed = bool(nvidia_ctk_exists or nvidia_runtime_exists or "nvidia" in runtimes)
    driver_installed = False
    devices = []
    missing = []
    suggestions = []

    if not nvidia_smi_exists:
        missing.append("未找到 nvidia-smi，疑似未安装 NVIDIA 驱动")
        suggestions.append("先在宿主机安装与当前 GPU/内核匹配的 NVIDIA 驱动，并确认 nvidia-smi 可执行。")
    else:
        code, out, err = run([
            "nvidia-smi",
            "--query-gpu=index,uuid,name,memory.total,memory.used,utilization.gpu,driver_version",
            "--format=csv,noheader,nounits",
        ], timeout=20)
        if code != 0:
            missing.append(f"nvidia-smi 执行失败: {err or 'unknown error'}")
            suggestions.append("先修复宿主机 NVIDIA 驱动状态，确认 nvidia-smi 可正常返回 GPU 列表。")
        else:
            driver_installed = True
            reader = csv.reader(out.splitlines())
            for row in reader:
                if len(row) < 7:
                    continue
                try:
                    total_mb = int(float(row[3].strip()))
                except ValueError:
                    total_mb = 0
                try:
                    used_mb = int(float(row[4].strip()))
                except ValueError:
                    used_mb = 0
                try:
                    util_percent = int(float(row[5].strip()))
                except ValueError:
                    util_percent = 0
                devices.append({
                    "index": row[0].strip(),
                    "uuid": row[1].strip(),
                    "name": row[2].strip(),
                    "memory_total_bytes": total_mb * 1024 * 1024,
                    "memory_used_bytes": used_mb * 1024 * 1024,
                    "utilization_gpu": util_percent,
                    "driver_version": row[6].strip(),
                })
            if not devices:
                missing.append("未检测到 NVIDIA GPU 设备")
                suggestions.append("确认服务器已安装 NVIDIA GPU 且驱动已正确加载。")

    if not toolkit_installed:
        missing.append("未检测到 nvidia-container-toolkit / nvidia runtime")
        suggestions.append("安装 nvidia-container-toolkit，并按官方步骤重启 Docker。")
    if not docker_has_gpus_flag:
        missing.append("当前 Docker CLI 不支持 --gpus 参数")
        suggestions.append("升级到支持 --gpus 的 Docker 版本，或检查 Docker 安装是否完整。")
    if docker_info_error:
        suggestions.append(f"Docker 信息读取失败: {docker_info_error}")

    supported = driver_installed and toolkit_installed and docker_has_gpus_flag and bool(devices)
    return {
        "ok": True,
        "supported": supported,
        "driver_installed": driver_installed,
        "toolkit_installed": toolkit_installed,
        "docker_supports_gpus": docker_has_gpus_flag,
        "devices": devices,
        "missing": missing,
        "suggestions": suggestions,
        "runtimes": sorted(runtimes.keys()),
        "recommended_actions": ["continue_install", "exit"] if missing else [],
        "auto_install_supported": False,
        "docker_info_error": docker_info_error,
    }

@app.errorhandler(Exception)
def handle_exception(exc):
    if isinstance(exc, HTTPException):
        return exc
    app.logger.exception("Unhandled exception")
    return jsonify({"ok": False, "error": f"服务器内部错误: {exc}"}), 500

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
    if not mounts:
        return True, "", []
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
            try:
                os.chown(host_real, puid, pgid)
                os.chmod(host_real, 0o770)
            except PermissionError:
                print(f"[WARN] 无权限设置目录所有者 {host_real}，跳过 chown/chmod")
            except Exception as e:
                print(f"[WARN] 设置目录权限失败 {host_real}: {e}")

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

def valid_size_limit(value):
    return valid_memory(value)

def normalize_size_limit(value):
    raw = str(value or "").strip()
    if not raw:
        return ""
    match = re.match(r"^([1-9][0-9]*)([mMgG]?)$", raw)
    if not match:
        return ""
    number, suffix = match.groups()
    return f"{number}{suffix.upper()}" if suffix else number

def normalize_gpu_device_list(raw_devices):
    if raw_devices in (None, "", []):
        return []
    if isinstance(raw_devices, str):
        devices = [item.strip() for item in raw_devices.split(",")]
    elif isinstance(raw_devices, list):
        devices = [str(item).strip() for item in raw_devices]
    else:
        return []
    normalized = []
    for item in devices:
        if not item:
            continue
        if not re.match(r"^[A-Za-z0-9._:-]+$", item):
            return []
        normalized.append(item)
    return normalized

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
    code, out, err = run(["docker", "image", "ls", "--no-trunc", "--format", "{{json .}}"], timeout=60)
    images = []
    if code == 0:
        for line in out.splitlines():
            try:
                row = json.loads(line)
            except Exception:
                continue
            reference = preferred_image_reference(row.get("Repository"), row.get("Tag"), row.get("ID"))
            image_info, inspect_error = inspect_image_details(reference)
            labels = image_info.get("labels", {}) if image_info else {}
            repo_tags = image_info.get("repo_tags", []) if image_info else []
            size_bytes = image_info.get("size_bytes", 0) if image_info else parse_size_to_bytes(row.get("Size", "0"))
            images.append({
                **row,
                "Reference": reference,
                "RepoTags": repo_tags,
                "Labels": labels,
                "SizeBytes": size_bytes,
                "CreatedAt": image_info.get("created_at", "") if image_info else "",
                "backup_kind": labels.get("manager.backup_kind", ""),
                "backup_source_container": labels.get("manager.backup_source_container", ""),
                "image_mode": labels.get("manager.image_mode", ""),
                "inspect_error": inspect_error or "",
            })
    images.sort(key=lambda item: item.get("CreatedAt") or item.get("CreatedSince") or "", reverse=True)
    return jsonify({"images": images, "error": err if code != 0 else None})

@app.route("/images", methods=["DELETE"])
@auth_required
def delete_image():
    body = request.get_json(silent=True) or {}
    image_ref = str(body.get("image_ref", "") or "").strip()
    if not image_ref:
        return jsonify({"ok": False, "error": "缺少镜像标识"}), 400
    code, out, err = run(["docker", "image", "rm", image_ref], timeout=180)
    if code != 0:
        message = err or out or "镜像删除失败"
        lower = message.lower()
        status_code = 409 if ("being used by running container" in lower or "image is being used" in lower or "must be forced" in lower) else 500
        return jsonify({"ok": False, "error": message}), status_code
    return jsonify({"ok": True, "output": out.splitlines() if out else []})

@app.route("/images/pull", methods=["POST"])
@auth_required
def pull_image():
    """拉取镜像，SSE 流式返回进度"""
    body  = request.get_json(silent=True) or {}
    image = body.get("image", DEFAULT_SSH_IMAGE)
    if not image or image.startswith("-") or any(ch.isspace() for ch in image):
        return jsonify({"error": "镜像地址无效"}), 400

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
    containers = []
    code, out, err = run("docker ps -a --filter label=manager=dockerhub --format '{{.Names}}'")
    if code != 0:
        return jsonify({"ok": False, "containers": [], "error": err}), 500
    for name in out.splitlines():
        name = name.strip()
        if not name:
            continue
        details, inspect_error = inspect_container_details(name)
        if details:
            containers.append(details)
        else:
            print(f"[WARN] 读取容器详情失败 {name}: {inspect_error}")
    return jsonify({"ok": True, "containers": containers})

@app.route("/containers/metrics")
@auth_required
def container_metrics():
    all_names, all_error = list_managed_container_names(include_stopped=True)
    if all_names is None:
        return jsonify({"ok": False, "containers": [], "error": all_error}), 500

    running_names, running_error = list_managed_container_names(include_stopped=False)
    if running_names is None:
        running_names = []

    running_stats, stats_error = collect_running_container_stats(running_names)
    containers = []
    errors = []
    if running_error:
        errors.append(running_error)
    if stats_error:
        errors.append(stats_error)

    managed_details = {}
    for name in all_names:
        details, inspect_error = inspect_container_details(name)
        if not details:
            errors.append(f"{name}: {inspect_error}")
            continue
        managed_details[name] = details

    gpu_metrics, gpu_errors = collect_container_gpu_metrics(managed_details)
    errors.extend(gpu_errors)

    for name, details in managed_details.items():
        size_info, size_error = inspect_container_size(name)
        if size_error:
            errors.append(f"{name}: {size_error}")
            size_info = {"disk_rw_bytes": 0, "disk_rootfs_bytes": 0}
        runtime_stats = running_stats.get(name, {})
        containers.append({
            "id": details.get("id", ""),
            "name": name,
            "status": details.get("status", "unknown"),
            "cpu_percent": runtime_stats.get("cpu_percent", 0.0),
            "memory_used_bytes": runtime_stats.get("memory_used_bytes", 0),
            "memory_limit_bytes": runtime_stats.get("memory_limit_bytes", 0),
            "pids_current": runtime_stats.get("pids_current"),
            "disk_rw_bytes": size_info.get("disk_rw_bytes", 0),
            "disk_rootfs_bytes": size_info.get("disk_rootfs_bytes", 0),
            "gpu": gpu_metrics.get(name, {
                "enabled": False,
                "driver": "",
                "configured_devices": [],
                "devices": [],
                "utilization_supported": False,
                "utilization_reason": "",
                "total_memory_used_bytes": 0,
                "total_util_percent": None,
                "active_process_count": 0,
            }),
        })
    containers.sort(key=lambda item: item.get("name", ""))
    return jsonify({
        "ok": True,
        "containers": containers,
        "errors": errors,
        "collected_at": now(),
    })

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
    raw_ssh_port = body.get("ssh_port")
    cpu      = str(body.get("cpu", "1"))
    memory   = str(body.get("memory", "1g"))
    pids_limit = int(body.get("pids_limit", 512))
    puid     = int(body.get("puid", 1000))
    pgid     = int(body.get("pgid", 1000))
    login_user = safe_name(body.get("login_user"), "dockeruser")
    assigned_to = safe_name(body.get("assigned_to"), "")
    public_key = (body.get("ssh_public_key") or "").strip()
    password_access = bool(body.get("password_access", False))
    ssh_password = body.get("ssh_password", "")
    allow_sudo = bool(body.get("allow_sudo", True))
    gpu_enabled = bool(body.get("gpu_enabled", False))
    gpu_devices = normalize_gpu_device_list(body.get("gpu_devices", []))
    gpu_mode = str(body.get("gpu_mode", "shared") or "shared").strip() or "shared"
    rootfs_limit = str(body.get("rootfs_limit", "") or "").strip()
    mounts   = body.get("mounts", [])
    allowed_roots = body.get("allowed_mount_roots", [])

    ssh_port = None
    if str(raw_ssh_port or "").strip():
        try:
            ssh_port = int(raw_ssh_port)
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "SSH 端口必须是数字，或留空自动分配"}), 400
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
    if gpu_mode != "shared":
        return jsonify({"ok": False, "error": "当前仅支持共享 GPU 模式"}), 400
    if rootfs_limit:
        if not valid_size_limit(rootfs_limit):
            return jsonify({"ok": False, "error": "容器磁盘上限格式无效"}), 400
        rootfs_limit = normalize_size_limit(rootfs_limit)
    linuxserver_openssh = is_linuxserver_openssh(image)
    if password_access and len(ssh_password) < 8:
        return jsonify({"ok": False, "error": "SSH 密码至少需要 8 位"}), 400
    if not public_key and not password_access:
        return jsonify({"ok": False, "error": "请填写 SSH 公钥，或启用密码登录"}), 400

    ok, error, normalized_mounts = validate_mounts(mounts, allowed_roots, puid, pgid)
    if not ok:
        return jsonify({"ok": False, "error": error}), 400

    storage = collect_storage_capabilities()
    if rootfs_limit and not storage.get("supports_rootfs_limit"):
        return jsonify({
            "ok": False,
            "error": storage.get("reason") or "当前服务器不支持容器可写层磁盘限额",
            "code": "rootfs_limit_unsupported",
        }), 400

    gpu_info = collect_gpu_info() if gpu_enabled else {"supported": False, "devices": []}
    selected_gpu_devices = []
    gpu_cli_value = ""
    if gpu_enabled:
        if not gpu_info.get("supported"):
            return jsonify({
                "ok": False,
                "error": "当前服务器未满足 GPU 容器运行条件",
                "code": "gpu_not_supported",
                "missing": gpu_info.get("missing", []),
            }), 400
        available_devices = gpu_info.get("devices", [])
        available_indices = [str(item.get("index", "")).strip() for item in available_devices if str(item.get("index", "")).strip()]
        if not available_indices:
            return jsonify({"ok": False, "error": "当前服务器未检测到可用 GPU", "code": "gpu_not_found"}), 400
        if not gpu_devices:
            selected_gpu_devices = available_indices
        else:
            invalid_devices = [item for item in gpu_devices if item not in available_indices]
            if invalid_devices:
                return jsonify({
                    "ok": False,
                    "error": f"所选 GPU 不存在: {', '.join(invalid_devices)}",
                    "code": "gpu_device_invalid",
                }), 400
            selected_gpu_devices = gpu_devices
        gpu_cli_value = "all" if set(selected_gpu_devices) == set(available_indices) else f"device={','.join(selected_gpu_devices)}"

    existing, _ = inspect_container_details(name)
    if existing:
        return jsonify({
            "ok": False,
            "error": f"容器名称已存在: {name}",
            "code": "container_name_conflict",
            "existing": existing,
        }), 409

    mount_args = []
    for m in normalized_mounts:
        mode = "ro" if m["readonly"] else "rw"
        mount_args += ["-v", f"{m['host_path']}:{m['container_path']}:{mode}"]

    escaped_user = shlex.quote(login_user)
    escaped_key = shlex.quote(public_key)
    sudo_cmd = ""
    if allow_sudo:
        sudo_cmd = (
            "apt-get install -y sudo -qq && "
            "usermod -aG sudo {user} && "
            "echo {sudoers} > /etc/sudoers.d/90-dockerhub-user && "
            "chmod 440 /etc/sudoers.d/90-dockerhub-user && "
        ).format(
            user=escaped_user,
            sudoers=shlex.quote(f"{login_user} ALL=(ALL) NOPASSWD:ALL"),
        )
    password_bootstrap = (
        "if grep -Eq '^#?PasswordAuthentication ' /etc/ssh/sshd_config; then "
        "sed -i 's/^#\\?PasswordAuthentication .*/PasswordAuthentication no/' /etc/ssh/sshd_config; "
        "else echo 'PasswordAuthentication no' >> /etc/ssh/sshd_config; fi; "
    )
    if password_access:
        password_bootstrap = (
            "if [ -f /run/secrets/dockerhub_ssh_password ]; then "
            "USER_PASSWORD=$(cat /run/secrets/dockerhub_ssh_password); "
            "if [ -n \"$USER_PASSWORD\" ]; then "
            f"printf '%s:%s\\n' {escaped_user} \"$USER_PASSWORD\" | chpasswd; "
            "fi; "
            "fi; "
            "if grep -Eq '^#?PasswordAuthentication ' /etc/ssh/sshd_config; then "
            "sed -i 's/^#\\?PasswordAuthentication .*/PasswordAuthentication yes/' /etc/ssh/sshd_config; "
            "else echo 'PasswordAuthentication yes' >> /etc/ssh/sshd_config; fi; "
        )

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
        "{password_bootstrap}"
        "grep -q '^PubkeyAuthentication yes' /etc/ssh/sshd_config || echo 'PubkeyAuthentication yes' >> /etc/ssh/sshd_config; "
        "echo '[bootstrap] starting sshd'; "
        "/usr/sbin/sshd -D"
    ).format(user=escaped_user, key=escaped_key, sudo_cmd=sudo_cmd, password_bootstrap=password_bootstrap)

    container_ssh_port = 2222 if linuxserver_openssh else 22
    image_mode = "linuxserver-openssh" if linuxserver_openssh else "generic-bootstrap"
    config_volume = ""
    password_file = None
    if password_access:
        secret_dir = WORKDIR / "secrets"
        password_file = prepare_secret_file(secret_dir / f"{safe_name(name)}.password", ssh_password)

    max_attempts = 8 if ssh_port is None else 1
    code, out, err = -1, "", "未开始执行"
    selected_ssh_port = ssh_port
    for _ in range(max_attempts):
        selected_ssh_port = ssh_port or find_available_ssh_port()
        if not selected_ssh_port:
            err = "32000-32999 范围内没有可用 SSH 端口"
            break
        cmd = [
            "docker", "run", "-d",
            "--name",    name,
            "-p",        f"{selected_ssh_port}:{container_ssh_port}",
            "--cpus",    cpu,
            "--memory",  memory,
            "--pids-limit", str(pids_limit),
            "--cap-drop", "NET_RAW",
            "--label", "manager=dockerhub",
            "--label", f"manager.assigned_to={assigned_to}",
            "--label", f"manager.login_user={login_user}",
            "--label", f"manager.container_ssh_port={container_ssh_port}",
            "--label", f"manager.image_mode={image_mode}",
            "--label", f"manager.gpu_enabled={'true' if gpu_enabled else 'false'}",
            "--label", f"manager.gpu_driver={'nvidia' if gpu_enabled else ''}",
            "--label", f"manager.gpu_devices={','.join(selected_gpu_devices)}",
            "--label", f"manager.gpu_mode={gpu_mode if gpu_enabled else ''}",
            "--label", f"manager.rootfs_limit={rootfs_limit}",
            "--label", f"manager.allow_sudo={bool_label(allow_sudo)}",
            "--label", f"manager.password_access={bool_label(password_access)}",
            "--restart", "unless-stopped",
        ] + mount_args
        if not allow_sudo:
            cmd += ["--security-opt", "no-new-privileges"]

        if rootfs_limit:
            cmd += ["--storage-opt", f"size={rootfs_limit}"]
        if gpu_enabled:
            cmd += ["--gpus", gpu_cli_value]
        if password_file:
            cmd += [
                "--label", f"manager.password_file={password_file}",
                "-v", f"{password_file}:/run/secrets/dockerhub_ssh_password:ro",
            ]

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
            ]
            if gpu_enabled:
                cmd += [
                    "-e", f"NVIDIA_VISIBLE_DEVICES={','.join(selected_gpu_devices)}",
                    "-e", "NVIDIA_DRIVER_CAPABILITIES=compute,utility",
                ]
            if password_file:
                cmd += [
                    "-e", "USER_PASSWORD_FILE=/run/secrets/dockerhub_ssh_password",
                ]
            cmd.append(image)
        else:
            cmd += [
                image,
                "/bin/bash", "-c",
                bootstrap
            ]

        code, out, err = run(cmd, timeout=300)
        if code == 0:
            break
        err_lower = (err or "").lower()
        if "already in use by container" in err_lower:
            existing, _ = inspect_container_details(name)
            if password_file:
                cleanup_secret_path(password_file)
            return jsonify({
                "ok": False,
                "error": f"容器名称已存在: {name}",
                "code": "container_name_conflict",
                "existing": existing,
            }), 409
        port_conflict = "port is already allocated" in err_lower or "bind: address already in use" in err_lower
        if port_conflict and ssh_port is None:
            continue
        if port_conflict:
            if password_file:
                cleanup_secret_path(password_file)
            return jsonify({
                "ok": False,
                "error": f"SSH 端口 {selected_ssh_port} 已被占用",
                "code": "ssh_port_conflict",
            }), 409
        break

    if code != 0:
        if password_file:
            cleanup_secret_path(password_file)
        error_code = "ssh_port_conflict" if "port is already allocated" in (err or "").lower() else "docker_run_failed"
        return jsonify({"ok": False, "error": err, "code": error_code}), 500

    container_id = out
    ssh_cmd = f"ssh -p {selected_ssh_port} {login_user}@<SERVER_HOST>"
    return jsonify({
        "ok":           True,
        "container_id": container_id,
        "ssh_cmd":      ssh_cmd,
        "name":         name,
        "ssh_port":     selected_ssh_port,
        "status":       "running",
        "image_mode":   image_mode,
        "allow_sudo":   allow_sudo,
        "password_access": password_access,
        "config_volume": config_volume if linuxserver_openssh else "",
        "password_file": str(password_file) if password_file else "",
        "gpu_enabled":  gpu_enabled,
        "gpu_devices":  selected_gpu_devices,
        "gpu_mode":     gpu_mode if gpu_enabled else "",
        "rootfs_limit": rootfs_limit,
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

@app.route("/containers/<name>/backup-preview")
@auth_required
def container_backup_preview(name):
    details, error = inspect_container_details(name)
    if not details:
        return jsonify({"ok": False, "error": error or "容器不存在"}), 404
    size_info, size_error = inspect_container_size(name)
    if not size_info:
        return jsonify({"ok": False, "error": size_error or "容器大小读取失败"}), 500
    image_info, image_error = inspect_image_details(details.get("image", ""))
    if not image_info:
        return jsonify({"ok": False, "error": image_error or "当前镜像读取失败"}), 500
    estimated_writable_bytes = int(size_info.get("disk_rw_bytes") or 0)
    current_image_size_bytes = int(image_info.get("size_bytes") or 0)
    estimated_image_bytes = current_image_size_bytes + estimated_writable_bytes
    return jsonify({
        "ok": True,
        "name": name,
        "image": details.get("image", ""),
        "suggested_image_name": build_backup_image_name(name),
        "estimated_writable_bytes": estimated_writable_bytes,
        "estimated_writable_text": format_bytes_human(estimated_writable_bytes),
        "current_image_size_bytes": current_image_size_bytes,
        "current_image_size_text": format_bytes_human(current_image_size_bytes),
        "estimated_image_bytes": estimated_image_bytes,
        "estimated_image_text": format_bytes_human(estimated_image_bytes),
    })

@app.route("/containers/<name>/backup-image", methods=["POST"])
@auth_required
def backup_container_image(name):
    details, error = inspect_container_details(name)
    if not details:
        return jsonify({"ok": False, "error": error or "容器不存在"}), 404
    body = request.get_json(silent=True) or {}
    image_name = str(body.get("image_name", "") or "").strip() or build_backup_image_name(name)
    if not valid_image_reference(image_name):
        return jsonify({"ok": False, "error": "镜像名称无效"}), 400
    existing_image, _ = inspect_image_details(image_name)
    if existing_image:
        return jsonify({"ok": False, "error": f"镜像已存在: {image_name}"}), 409
    labels = details.get("labels") or {}
    image_mode = resolve_container_image_mode(labels)
    image_info, commit_error = commit_container_image(name, image_name, {
        "manager.backup_kind": "manual_backup",
        "manager.backup_source_container": name,
        "manager.backup_source_image": details.get("image", ""),
        "manager.backup_created_at": now(),
        "manager.image_mode": image_mode,
        "manager.login_user": labels.get("manager.login_user", ""),
    })
    if not image_info:
        return jsonify({"ok": False, "error": commit_error or "容器备份失败"}), 500
    reference = preferred_repo_tag(image_info.get("repo_tags", []), image_name)
    return jsonify({
        "ok": True,
        "image_ref": reference,
        "image_id": image_info.get("id", ""),
        "size_bytes": image_info.get("size_bytes", 0),
        "size_text": format_bytes_human(image_info.get("size_bytes", 0)),
        "backup_kind": "manual_backup",
        "backup_source_container": name,
        "created_at": image_info.get("created_at", ""),
    })

@app.route("/containers/<name>/rebuild", methods=["POST"])
@auth_required
def rebuild_container(name):
    raw, inspect_error = inspect_container_raw(name)
    if not raw:
        return jsonify({"ok": False, "error": inspect_error or "容器不存在"}), 404

    body = request.get_json(silent=True) or {}
    labels = dict((raw.get("Config", {}) or {}).get("Labels") or {})
    host_config = raw.get("HostConfig", {}) or {}
    source_type = str(body.get("source_type", "temporary_snapshot") or "temporary_snapshot").strip() or "temporary_snapshot"
    current_image_mode = resolve_container_image_mode(labels)
    allow_sudo = resolve_allow_sudo(labels, host_config)
    resource_settings, resource_error = normalize_rebuild_resources(body, raw)
    if not resource_settings:
        return jsonify({"ok": False, "error": resource_error}), 400

    rootfs_limit = str(body.get("rootfs_limit", labels.get("manager.rootfs_limit", "")) or "").strip()
    if rootfs_limit:
        if not valid_size_limit(rootfs_limit):
            return jsonify({"ok": False, "error": "容器磁盘上限格式无效"}), 400
        rootfs_limit = normalize_size_limit(rootfs_limit)
        storage = collect_storage_capabilities()
        if not storage.get("supports_rootfs_limit"):
            return jsonify({
                "ok": False,
                "error": storage.get("reason") or "当前服务器不支持容器可写层磁盘限额",
                "code": "rootfs_limit_unsupported",
            }), 400

    gpu_settings, gpu_error = resolve_rebuild_gpu_settings(body, labels)
    if not gpu_settings:
        return jsonify({"ok": False, "error": gpu_error}), 400

    target_image_ref = ""
    target_image_mode = current_image_mode
    temporary_image_ref = ""
    source_image_ref = ""
    if source_type == "temporary_snapshot":
        target_image_ref = build_temp_snapshot_image_name(name)
        temporary_image_ref = target_image_ref
        image_info, commit_error = commit_container_image(name, target_image_ref, {
            "manager.backup_kind": "temporary_rebuild_snapshot",
            "manager.backup_source_container": name,
            "manager.backup_source_image": str((raw.get("Config", {}) or {}).get("Image") or ""),
            "manager.backup_created_at": now(),
            "manager.image_mode": current_image_mode,
            "manager.login_user": labels.get("manager.login_user", ""),
        })
        if not image_info:
            return jsonify({"ok": False, "error": commit_error or "容器临时快照创建失败"}), 500
        source_image_ref = preferred_repo_tag(image_info.get("repo_tags", []), target_image_ref)
    elif source_type == "backup_image":
        target_image_ref = str(body.get("image_ref", "") or body.get("backup_image", "") or "").strip()
        if not valid_image_reference(target_image_ref):
            return jsonify({"ok": False, "error": "请选择有效的备份镜像"}), 400
        image_info, image_error = inspect_image_details(target_image_ref)
        if not image_info:
            return jsonify({"ok": False, "error": image_error or "备份镜像不存在"}), 404
        image_labels = image_info.get("labels", {}) or {}
        if image_labels.get("manager.backup_kind") != "manual_backup":
            return jsonify({"ok": False, "error": "当前仅支持从手动备份镜像重建"}), 400
        target_image_mode = str(image_labels.get("manager.image_mode") or current_image_mode).strip() or current_image_mode
        source_image_ref = preferred_repo_tag(image_info.get("repo_tags", []), target_image_ref)
    else:
        return jsonify({"ok": False, "error": "不支持的重建来源"}), 400

    create_cmd = build_rebuild_create_command(
        raw,
        target_image_ref,
        target_image_mode,
        resource_settings,
        gpu_settings,
        rootfs_limit,
        labels,
        allow_sudo,
    )
    rollback_name = rollback_container_name(name)
    new_container_id = ""

    def cleanup_temp_image():
        if temporary_image_ref:
            remove_image_quietly(temporary_image_ref)

    def restore_original_container(error_message):
        rollback_errors = []
        existing_new, _ = inspect_container_details(name)
        if existing_new:
            code, _, err = run(["docker", "rm", "-f", name], timeout=120)
            if code != 0:
                rollback_errors.append(f"删除失败的新容器失败: {err or 'unknown'}")
        renamed, rename_error = inspect_container_details(rollback_name)
        if renamed:
            code, _, err = run(["docker", "rename", rollback_name, name], timeout=30)
            if code != 0:
                rollback_errors.append(f"回滚容器改名失败: {err or 'unknown'}")
            else:
                code, _, err = run(["docker", "start", name], timeout=120)
                if code != 0 and "is already running" not in (err or "").lower():
                    rollback_errors.append(f"回滚容器启动失败: {err or 'unknown'}")
        cleanup_temp_image()
        message = error_message
        if rollback_errors:
            message = f"{message}；回滚补救异常：{'；'.join(rollback_errors)}"
        return jsonify({"ok": False, "error": message}), 500

    code, _, err = run(["docker", "stop", name], timeout=120)
    if code != 0 and "is not running" not in (err or "").lower():
        cleanup_temp_image()
        return jsonify({"ok": False, "error": err or "停止原容器失败"}), 500

    code, _, err = run(["docker", "rename", name, rollback_name], timeout=30)
    if code != 0:
        cleanup_temp_image()
        run(["docker", "start", name], timeout=120)
        return jsonify({"ok": False, "error": err or "原容器改名失败"}), 500

    code, out, err = run(create_cmd, timeout=600)
    if code != 0:
        return restore_original_container(err or "重建容器创建失败")
    new_container_id = out

    code, _, err = run(["docker", "start", name], timeout=180)
    if code != 0:
        return restore_original_container(err or "重建容器启动失败")

    cleanup_warning = None
    code, _, err = run(["docker", "rm", "-f", rollback_name], timeout=180)
    if code != 0:
        cleanup_warning = err or "旧容器清理失败"
    cleanup_temp_image()
    details, _ = inspect_container_details(name)
    return jsonify({
        "ok": True,
        "container_id": new_container_id,
        "name": name,
        "status": details.get("status", "running") if details else "running",
        "image": details.get("image", source_image_ref or target_image_ref) if details else (source_image_ref or target_image_ref),
        "image_mode": target_image_mode,
        "ssh_port": details.get("ssh_port", "") if details else "",
        "source_type": source_type,
        "source_image_ref": source_image_ref,
        "gpu_enabled": gpu_settings.get("gpu_enabled", False),
        "gpu_devices": gpu_settings.get("gpu_devices", []),
        "gpu_mode": gpu_settings.get("gpu_mode", ""),
        "rootfs_limit": rootfs_limit,
        "cpu": resource_settings["cpu"],
        "memory": resource_settings["memory"],
        "pids_limit": resource_settings["pids_limit"],
        "rollback_warning": cleanup_warning,
    })

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
        cleanup_secret_path(password_file)
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
    storage = collect_storage_capabilities()
    return jsonify({
        "ok":             True,
        "cpu_cores":      os.cpu_count() or 1,
        "memory_bytes":   memory_bytes(),
        "docker_ok":      docker_ok,
        "docker_version": docker_version if docker_ok else "unavailable",
        "storage":        storage,
        "workdir":        str(WORKDIR),
        "time":       now()
    })

@app.route("/gpu/info")
@auth_required
def gpu_info():
    return jsonify(collect_gpu_info())

@app.route("/checks", methods=["GET", "POST"])
@auth_required
def checks():
    body = request.get_json(silent=True) or {}
    docker_ok, docker_version = docker_available()
    warnings = []
    storage = collect_storage_capabilities()
    gpu = collect_gpu_info()

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
        "storage": storage,
        "gpu": gpu,
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
