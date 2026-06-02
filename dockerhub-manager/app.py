import base64
import csv
import hmac
import io
import json
import os
import posixpath
import re
import secrets
import shlex
import sqlite3
import subprocess
import threading
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import requests
from flask import Flask, Response, jsonify, render_template_string, request, session, redirect, url_for, has_request_context
from werkzeug.exceptions import HTTPException
from werkzeug.security import check_password_hash, generate_password_hash

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "change-me-in-production-please")
APP_MODE = str(os.environ.get("APP_MODE", "panel") or "panel").strip().lower()
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
APP_DIR = Path(__file__).resolve().parent
GPU_ACCOUNTING_DB = APP_DIR / "gpu_accounting.db"
SSH_PORT_MIN = 32000
SSH_PORT_MAX = 32199
EXTRA_PORT_MIN = 32200
EXTRA_PORT_MAX = 32999
DEFAULT_EXTRA_PORT_COUNT = 5
MAX_EXTRA_PORT_COUNT = 32
DEFAULT_SSH_IMAGE = "lscr.io/linuxserver/openssh-server:latest"
PANEL_VERSION = "0.4.0"
AUDIT_LOG_LIMIT = 2000
GPU_ACCOUNTING_DEFAULT_WEEKLY_QUOTA_HOURS = 72
GPU_ACCOUNTING_DEFAULT_RETENTION_DAYS = 30
GPU_ACCOUNTING_DEFAULT_SAMPLING_INTERVAL_SECONDS = 10
GPU_ACCOUNTING_RELEASE_SECONDS = 30
GPU_ACCOUNTING_ACTIVE_UTIL_THRESHOLD = 10
GPU_ACCOUNTING_MIN_MEMORY_BYTES = 2 * 1024 * 1024 * 1024
GPU_ACCOUNTING_STALE_SECONDS = 60
GPU_ACCOUNTING_WARN_RATIO = 0.8
GPU_ACCOUNTING_OVER_RATIO = 1.0
GPU_ACCOUNTING_CRITICAL_RATIO = 1.2
GPU_PORTAL_DEFAULT_DAYS = 30
GPU_PORTAL_TOKEN_LENGTH = 16
gpu_accounting_db_lock = threading.RLock()
gpu_accounting_runtime_lock = threading.RLock()
gpu_accounting_worker_lock = threading.Lock()
gpu_accounting_worker_started = False
gpu_accounting_runtime = {
    "device_states": {},
    "current_containers": {},
    "last_sample_at": "",
    "last_success_at": "",
    "last_cleanup_at": "",
    "server_errors": {},
}

def clamp_int(value, default, minimum=0, maximum=None):
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    if number < minimum:
        number = minimum
    if maximum is not None and number > maximum:
        number = maximum
    return number

def ensure_gpu_accounting_defaults(data):
    cfg = data.setdefault("gpu_accounting", {})
    cfg["default_weekly_quota_hours"] = clamp_int(
        cfg.get("default_weekly_quota_hours", GPU_ACCOUNTING_DEFAULT_WEEKLY_QUOTA_HOURS),
        GPU_ACCOUNTING_DEFAULT_WEEKLY_QUOTA_HOURS,
        minimum=1,
        maximum=100000,
    )
    cfg["retention_days"] = clamp_int(
        cfg.get("retention_days", GPU_ACCOUNTING_DEFAULT_RETENTION_DAYS),
        GPU_ACCOUNTING_DEFAULT_RETENTION_DAYS,
        minimum=7,
        maximum=365,
    )
    cfg["sampling_interval_seconds"] = clamp_int(
        cfg.get("sampling_interval_seconds", GPU_ACCOUNTING_DEFAULT_SAMPLING_INTERVAL_SECONDS),
        GPU_ACCOUNTING_DEFAULT_SAMPLING_INTERVAL_SECONDS,
        minimum=5,
        maximum=300,
    )
    legacy_temp_quotas = data.pop("gpu_temp_quotas", [])
    raw_user_base_quotas = cfg.get("user_base_quotas", {})
    normalized_user_base_quotas = {}
    if isinstance(raw_user_base_quotas, dict):
        for username, value in raw_user_base_quotas.items():
            key = str(username or "").strip()
            if not key:
                continue
            normalized_user_base_quotas[key] = clamp_int(
                value,
                cfg["default_weekly_quota_hours"],
                minimum=1,
                maximum=100000,
            )
    cfg["user_base_quotas"] = normalized_user_base_quotas
    raw_temp_quotas = cfg.get("temp_quotas", [])
    if isinstance(legacy_temp_quotas, list):
        raw_temp_quotas = list(raw_temp_quotas) + legacy_temp_quotas
    normalized_temp_quotas = []
    for record in raw_temp_quotas if isinstance(raw_temp_quotas, list) else []:
        if not isinstance(record, dict):
            continue
        username = str(record.get("username") or "").strip()
        if not username:
            continue
        normalized_temp_quotas.append({
            "id": str(record.get("id") or f"tmp_{uuid.uuid4().hex[:12]}").strip(),
            "username": username,
            "extra_hours_per_week": clamp_int(
                record.get("extra_hours_per_week"),
                0,
                minimum=1,
                maximum=100000,
            ),
            "effective_weeks": clamp_int(
                record.get("effective_weeks"),
                1,
                minimum=1,
                maximum=52,
            ),
            "start_week": str(record.get("start_week") or "").strip(),
            "created_at": str(record.get("created_at") or "").strip(),
            "created_by": str(record.get("created_by") or "").strip(),
            "note": str(record.get("note") or "").strip(),
            "deactivated_at": str(record.get("deactivated_at") or "").strip(),
            "deactivated_by": str(record.get("deactivated_by") or "").strip(),
        })
    cfg["temp_quotas"] = normalized_temp_quotas
    raw_portal_tokens = cfg.get("portal_tokens", {})
    normalized_portal_tokens = {}
    if isinstance(raw_portal_tokens, dict):
        for username, record in raw_portal_tokens.items():
            normalized_username = str(username or "").strip()
            if not normalized_username or not isinstance(record, dict):
                continue
            token = str(record.get("token") or "").strip()
            if not token:
                continue
            normalized_portal_tokens[normalized_username] = {
                "token": token[:64],
                "created_at": str(record.get("created_at") or "").strip(),
                "created_by": str(record.get("created_by") or "").strip(),
                "last_reset_at": str(record.get("last_reset_at") or "").strip(),
                "last_reset_by": str(record.get("last_reset_by") or "").strip(),
            }
    cfg["portal_tokens"] = normalized_portal_tokens
    return cfg

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
    ensure_gpu_accounting_defaults(data)
    trim_audit_logs(data)
    migrate_empty_server_id(data)
    return data

def save_data(data):
    trim_audit_logs(data)
    payload = json.dumps(data, indent=2, ensure_ascii=False)
    with data_lock:
        DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
        temp_file = DATA_FILE.with_name(f".{DATA_FILE.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        temp_file.write_text(payload, encoding="utf-8")
        os.replace(temp_file, DATA_FILE)

def trim_audit_logs(data):
    data.setdefault("audit_logs", [])
    data["audit_logs"] = data["audit_logs"][-AUDIT_LOG_LIMIT:]

def resolve_audit_operator(operator=None, operator_role=None):
    resolved_operator = str(operator or "").strip()
    resolved_role = str(operator_role or "").strip()
    if resolved_operator:
        return resolved_operator, resolved_role
    if has_request_context():
        session_user = str(session.get("user") or "").strip()
        if session_user:
            return session_user, str(session.get("role") or "").strip()
    return "system", "system"

def normalize_audit_log_entry(entry):
    item = dict(entry or {})
    item["time"] = str(item.get("time") or "")
    item["level"] = str(item.get("level") or "INFO")
    item["message"] = str(item.get("message") or "")
    item["operator"] = str(item.get("operator") or "")
    item["operator_role"] = str(item.get("operator_role") or "")
    return item

def can_manage_users():
    return str(session.get("role") or "").strip() == "admin"

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
    trim_audit_logs(data)
    save_data(data)

def append_audit(data, message, level="INFO", operator=None, operator_role=None):
    resolved_operator, resolved_role = resolve_audit_operator(operator, operator_role)
    data.setdefault("audit_logs", []).append({
        "time": datetime.now().isoformat(timespec="seconds"),
        "level": level,
        "message": message,
        "operator": resolved_operator,
        "operator_role": resolved_role,
    })
    trim_audit_logs(data)

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

def iso_seconds(dt):
    return dt.isoformat(timespec="seconds")

def minute_bucket_start(dt):
    return dt.replace(second=0, microsecond=0)

def current_week_window(reference=None):
    current = reference or datetime.now()
    start = (current - timedelta(days=current.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    return start, start + timedelta(days=7)

def resolve_gpu_accounting_username(assigned_to="", login_user=""):
    username = str(assigned_to or "").strip()
    if username:
        return username
    return str(login_user or "").strip()

def parse_iso_datetime(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None

def normalize_week_start(value):
    if isinstance(value, datetime):
        return current_week_window(value)[0]
    parsed = parse_iso_datetime(value)
    if parsed is None:
        return None
    return current_week_window(parsed)[0]

def current_gpu_user_base_quota_hours(data, username):
    cfg = ensure_gpu_accounting_defaults(data)
    normalized_username = str(username or "").strip()
    if normalized_username:
        user_base_quotas = cfg.get("user_base_quotas", {}) or {}
        if normalized_username in user_base_quotas:
            return clamp_int(
                user_base_quotas.get(normalized_username),
                cfg["default_weekly_quota_hours"],
                minimum=1,
                maximum=100000,
            )
        users = data.get("users", {}) or {}
        user = users.get(normalized_username, {}) if isinstance(users, dict) else {}
        if "gpu_base_quota_hours" in user:
            return clamp_int(
                user.get("gpu_base_quota_hours"),
                cfg["default_weekly_quota_hours"],
                minimum=1,
                maximum=100000,
            )
    return cfg["default_weekly_quota_hours"]

def current_gpu_base_quota_hours(data, username):
    return current_gpu_user_base_quota_hours(data, username)

def current_gpu_base_quota_override_hours(data, username):
    cfg = ensure_gpu_accounting_defaults(data)
    normalized_username = str(username or "").strip()
    if not normalized_username:
        return None
    user_base_quotas = cfg.get("user_base_quotas", {}) or {}
    if normalized_username in user_base_quotas:
        return clamp_int(
            user_base_quotas.get(normalized_username),
            cfg["default_weekly_quota_hours"],
            minimum=1,
            maximum=100000,
        )
    return None

def set_gpu_base_quota_override(data, username, base_quota_hours):
    cfg = ensure_gpu_accounting_defaults(data)
    normalized_username = str(username or "").strip()
    if not normalized_username:
        return
    user_base_quotas = cfg.setdefault("user_base_quotas", {})
    if base_quota_hours is None:
        user_base_quotas.pop(normalized_username, None)
        return
    user_base_quotas[normalized_username] = clamp_int(
        base_quota_hours,
        cfg["default_weekly_quota_hours"],
        minimum=1,
        maximum=100000,
    )

def iter_gpu_temp_quotas(data, username=""):
    cfg = ensure_gpu_accounting_defaults(data)
    normalized_username = str(username or "").strip()
    for record in cfg.get("temp_quotas", []) or []:
        if normalized_username and str(record.get("username") or "").strip() != normalized_username:
            continue
        yield record

def build_gpu_temp_quota_view(record, week_start):
    start_week = normalize_week_start(record.get("start_week") or record.get("created_at"))
    effective_weeks = clamp_int(record.get("effective_weeks"), 1, minimum=1, maximum=52)
    deactivated_at = str(record.get("deactivated_at") or "").strip()
    deactivated_by = str(record.get("deactivated_by") or "").strip()
    active = False
    weeks_remaining = 0
    end_week = None
    status = "ended"
    if start_week is not None:
        end_week = start_week + timedelta(days=7 * effective_weeks)
        active = (not deactivated_at) and start_week <= week_start < end_week
        if active:
            status = "active"
            weeks_remaining = max(0, int((end_week - week_start).days // 7))
        elif deactivated_at:
            status = "reset"
    elif deactivated_at:
        status = "reset"
    return {
        "id": str(record.get("id") or "").strip(),
        "username": str(record.get("username") or "").strip(),
        "extra_hours_per_week": clamp_int(record.get("extra_hours_per_week"), 0, minimum=1, maximum=100000),
        "effective_weeks": effective_weeks,
        "start_week": iso_seconds(start_week) if start_week else "",
        "end_week": iso_seconds(end_week) if end_week else "",
        "created_at": str(record.get("created_at") or "").strip(),
        "created_by": str(record.get("created_by") or "").strip(),
        "note": str(record.get("note") or "").strip(),
        "active": active,
        "status": status,
        "weeks_remaining": weeks_remaining,
        "deactivated_at": deactivated_at,
        "deactivated_by": deactivated_by,
    }

def current_gpu_temp_quota_hours(data, username, week_start):
    total_extra_hours = 0
    temp_quota_views = []
    active_temp_quota_views = []
    normalized_username = str(username or "").strip()
    for record in iter_gpu_temp_quotas(data, normalized_username):
        view = build_gpu_temp_quota_view(record, week_start)
        temp_quota_views.append(view)
        if view["active"]:
            total_extra_hours += view["extra_hours_per_week"]
            active_temp_quota_views.append(view)
    temp_quota_views.sort(key=lambda item: (item.get("created_at", ""), item.get("id", "")), reverse=True)
    active_temp_quota_views.sort(key=lambda item: (item.get("created_at", ""), item.get("id", "")), reverse=True)
    return total_extra_hours, active_temp_quota_views, temp_quota_views

def current_gpu_quota_snapshot(data, username, week_start=None):
    normalized_week_start = week_start or current_week_window()[0]
    base_quota_hours = current_gpu_base_quota_hours(data, username)
    user_base_quota_hours = current_gpu_base_quota_override_hours(data, username)
    temporary_extra_quota_hours, active_temp_quotas, temp_quotas = current_gpu_temp_quota_hours(
        data,
        username,
        normalized_week_start,
    )
    effective_quota_hours = base_quota_hours + temporary_extra_quota_hours
    return {
        "base_quota_hours": base_quota_hours,
        "user_base_quota_hours": user_base_quota_hours,
        "temporary_extra_quota_hours": temporary_extra_quota_hours,
        "effective_quota_hours": effective_quota_hours,
        "active_temp_quotas": active_temp_quotas,
        "temp_quotas": temp_quotas,
    }

def current_gpu_quota_status(used_hours, effective_quota_hours):
    quota_hours = float(effective_quota_hours or 0.0)
    usage_hours_value = float(used_hours or 0.0)
    usage_ratio = (usage_hours_value / quota_hours) if quota_hours > 0 else 0.0
    over_quota_hours = max(0.0, usage_hours_value - quota_hours) if quota_hours > 0 else usage_hours_value
    if usage_ratio >= GPU_ACCOUNTING_CRITICAL_RATIO:
        status = "critical"
    elif usage_ratio >= GPU_ACCOUNTING_OVER_RATIO:
        status = "over"
    elif usage_ratio >= GPU_ACCOUNTING_WARN_RATIO:
        status = "warn"
    else:
        status = "normal"
    return {
        "usage_ratio": round(usage_ratio, 4),
        "over_quota_hours": round(over_quota_hours, 4),
        "quota_status": status,
    }

def gpu_accounting_known_usernames(data):
    cfg = ensure_gpu_accounting_defaults(data)
    usernames = set()
    for username in (cfg.get("user_base_quotas", {}) or {}).keys():
        normalized = str(username or "").strip()
        if normalized:
            usernames.add(normalized)
    for record in cfg.get("temp_quotas", []) or []:
        normalized = str(record.get("username") or "").strip()
        if normalized:
            usernames.add(normalized)
    for username, details in (data.get("users", {}) or {}).items():
        if isinstance(details, dict) and "gpu_base_quota_hours" in details:
            normalized = str(username or "").strip()
            if normalized:
                usernames.add(normalized)
    return usernames

def open_gpu_accounting_db():
    conn = sqlite3.connect(str(GPU_ACCOUNTING_DB), timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def ensure_gpu_accounting_db():
    with gpu_accounting_db_lock:
        GPU_ACCOUNTING_DB.parent.mkdir(parents=True, exist_ok=True)
        conn = open_gpu_accounting_db()
        try:
            conn.executescript("""
            CREATE TABLE IF NOT EXISTS gpu_accounting_minute_usage (
                bucket_start TEXT NOT NULL,
                username TEXT NOT NULL,
                server_id TEXT NOT NULL,
                container_name TEXT NOT NULL,
                gpu_card_hours REAL NOT NULL DEFAULT 0,
                low_efficiency_card_hours REAL NOT NULL DEFAULT 0,
                util_percent_sum REAL NOT NULL DEFAULT 0,
                memory_ratio_sum REAL NOT NULL DEFAULT 0,
                peak_active_gpu_count INTEGER NOT NULL DEFAULT 0,
                sample_count INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (bucket_start, username, server_id, container_name)
            );
            CREATE INDEX IF NOT EXISTS idx_gpu_accounting_user_bucket
                ON gpu_accounting_minute_usage (username, bucket_start);
            CREATE INDEX IF NOT EXISTS idx_gpu_accounting_bucket_server
                ON gpu_accounting_minute_usage (bucket_start, server_id);
            """)
            conn.commit()
        finally:
            conn.close()

def cleanup_gpu_accounting_history(retention_days):
    retention_days = clamp_int(retention_days, GPU_ACCOUNTING_DEFAULT_RETENTION_DAYS, minimum=7, maximum=365)
    cutoff = minute_bucket_start(datetime.now() - timedelta(days=retention_days))
    with gpu_accounting_db_lock:
        conn = open_gpu_accounting_db()
        try:
            conn.execute(
                "DELETE FROM gpu_accounting_minute_usage WHERE bucket_start < ?",
                (iso_seconds(cutoff),),
            )
            conn.commit()
        finally:
            conn.close()

def evaluate_gpu_accounting_device(device, util_supported):
    util_percent = clamp_int(device.get("container_util_percent", 0), 0, minimum=0, maximum=100000)
    memory_used_bytes = clamp_int(device.get("container_memory_used_bytes", 0), 0, minimum=0)
    total_memory_bytes = clamp_int(device.get("device_memory_total_bytes", 0), 0, minimum=0)
    memory_threshold = GPU_ACCOUNTING_MIN_MEMORY_BYTES
    if total_memory_bytes > 0:
        memory_threshold = max(memory_threshold, int(total_memory_bytes * 0.1))
    meets_util = bool(util_supported) and util_percent >= GPU_ACCOUNTING_ACTIVE_UTIL_THRESHOLD
    meets_memory = memory_used_bytes >= memory_threshold
    active_candidate = meets_util or meets_memory
    low_efficiency = bool(util_supported) and active_candidate and util_percent < GPU_ACCOUNTING_ACTIVE_UTIL_THRESHOLD and meets_memory
    memory_ratio = (float(memory_used_bytes) / float(total_memory_bytes)) if total_memory_bytes > 0 else 0.0
    return {
        "active_candidate": active_candidate,
        "low_efficiency": low_efficiency,
        "util_percent": util_percent,
        "memory_used_bytes": memory_used_bytes,
        "memory_ratio": memory_ratio,
        "total_memory_bytes": total_memory_bytes,
    }

def apply_gpu_accounting_device_observation_locked(state_key, metadata, observation, sample_ts):
    state = dict(gpu_accounting_runtime["device_states"].get(state_key) or {})
    state.update(metadata)
    state["state_key"] = state_key
    state["last_updated_at"] = sample_ts
    if observation.get("observed"):
        state["last_observed_at"] = sample_ts
        state["last_util_percent"] = observation.get("util_percent", 0)
        state["last_memory_used_bytes"] = observation.get("memory_used_bytes", 0)
        state["last_memory_ratio"] = observation.get("memory_ratio", 0.0)
        state["device_memory_total_bytes"] = observation.get("total_memory_bytes", 0)
    else:
        state["last_util_percent"] = 0
        state["last_memory_used_bytes"] = 0
        state["last_memory_ratio"] = 0.0

    if observation.get("active_candidate"):
        state["active"] = True
        state["below_since"] = None
        state["low_efficiency"] = bool(observation.get("low_efficiency"))
    elif state.get("active"):
        below_since = float(state.get("below_since") or sample_ts)
        if not state.get("below_since"):
            state["below_since"] = below_since
        if sample_ts - below_since >= GPU_ACCOUNTING_RELEASE_SECONDS:
            state["active"] = False
            state["low_efficiency"] = False
        else:
            state["active"] = True
            state["low_efficiency"] = False
    else:
        state["active"] = False
        state["below_since"] = sample_ts
        state["low_efficiency"] = False

    gpu_accounting_runtime["device_states"][state_key] = state
    return state

def prune_gpu_accounting_device_states_locked(now_ts):
    stale_after = float(now_ts) - GPU_ACCOUNTING_STALE_SECONDS
    remove_keys = []
    for state_key, state in list(gpu_accounting_runtime["device_states"].items()):
        last_updated_at = float(state.get("last_updated_at") or 0)
        if state.get("active") and last_updated_at < stale_after:
            state["active"] = False
            state["low_efficiency"] = False
        if (not state.get("active")) and last_updated_at < stale_after:
            remove_keys.append(state_key)
            continue
        gpu_accounting_runtime["device_states"][state_key] = state
    for state_key in remove_keys:
        gpu_accounting_runtime["device_states"].pop(state_key, None)

def rebuild_gpu_accounting_current_containers_locked(now_ts):
    grouped = {}
    for state in gpu_accounting_runtime["device_states"].values():
        if not state.get("active"):
            continue
        if now_ts - float(state.get("last_updated_at") or 0) > GPU_ACCOUNTING_STALE_SECONDS:
            continue
        key = (
            state.get("server_id", ""),
            state.get("container_key", ""),
        )
        entry = grouped.get(key)
        if entry is None:
            entry = {
                "server_id": state.get("server_id", ""),
                "server_name": state.get("server_name", state.get("server_id", "")),
                "username": state.get("username", ""),
                "assigned_to": state.get("assigned_to", ""),
                "login_user": state.get("login_user", ""),
                "container_name": state.get("container_name", ""),
                "container_key": state.get("container_key", ""),
                "container_id": state.get("container_id", ""),
                "active_gpu_count": 0,
                "low_efficiency_gpu_count": 0,
                "util_percent_sum": 0.0,
                "memory_ratio_sum": 0.0,
                "memory_used_bytes": 0,
                "devices": [],
                "last_sample_at": "",
            }
            grouped[key] = entry
        entry["active_gpu_count"] += 1
        entry["util_percent_sum"] += float(state.get("last_util_percent") or 0)
        entry["memory_ratio_sum"] += float(state.get("last_memory_ratio") or 0.0)
        entry["memory_used_bytes"] += clamp_int(state.get("last_memory_used_bytes", 0), 0, minimum=0)
        if state.get("low_efficiency"):
            entry["low_efficiency_gpu_count"] += 1
        entry["devices"].append({
            "id": state.get("device_id", ""),
            "uuid": state.get("device_uuid", ""),
            "name": state.get("device_name", ""),
            "util_percent": clamp_int(state.get("last_util_percent", 0), 0, minimum=0),
            "memory_used_bytes": clamp_int(state.get("last_memory_used_bytes", 0), 0, minimum=0),
            "memory_ratio": float(state.get("last_memory_ratio") or 0.0),
            "low_efficiency": bool(state.get("low_efficiency", False)),
        })
        last_sample_ts = float(state.get("last_updated_at") or 0)
        if last_sample_ts > 0:
            entry["last_sample_at"] = iso_seconds(datetime.fromtimestamp(last_sample_ts))

    current_containers = []
    for entry in grouped.values():
        entry["devices"].sort(key=lambda item: item.get("id", ""))
        current_containers.append(entry)
    current_containers.sort(key=lambda item: (item.get("username", ""), item.get("server_id", ""), item.get("container_name", "")))
    gpu_accounting_runtime["current_containers"] = current_containers
    return current_containers

def snapshot_gpu_accounting_runtime():
    with gpu_accounting_runtime_lock:
        now_ts = time.time()
        prune_gpu_accounting_device_states_locked(now_ts)
        current_containers = rebuild_gpu_accounting_current_containers_locked(now_ts)
        return {
            "last_sample_at": gpu_accounting_runtime.get("last_sample_at", ""),
            "last_success_at": gpu_accounting_runtime.get("last_success_at", ""),
            "last_cleanup_at": gpu_accounting_runtime.get("last_cleanup_at", ""),
            "server_errors": dict(gpu_accounting_runtime.get("server_errors", {})),
            "current_containers": json.loads(json.dumps(current_containers, ensure_ascii=False)),
        }

def write_gpu_accounting_bucket_samples(sample_dt, sample_interval_seconds, container_entries):
    rows = []
    bucket_start = iso_seconds(minute_bucket_start(sample_dt))
    updated_at = iso_seconds(sample_dt)
    hours_per_sample = float(sample_interval_seconds) / 3600.0
    for entry in container_entries:
        active_gpu_count = clamp_int(entry.get("active_gpu_count", 0), 0, minimum=0)
        if active_gpu_count <= 0:
            continue
        low_efficiency_gpu_count = clamp_int(entry.get("low_efficiency_gpu_count", 0), 0, minimum=0)
        avg_util_percent = float(entry.get("util_percent_sum", 0.0)) / float(active_gpu_count)
        avg_memory_ratio = float(entry.get("memory_ratio_sum", 0.0)) / float(active_gpu_count)
        rows.append((
            bucket_start,
            str(entry.get("username", "")).strip(),
            str(entry.get("server_id", "")).strip(),
            str(entry.get("container_name", "")).strip(),
            active_gpu_count * hours_per_sample,
            low_efficiency_gpu_count * hours_per_sample,
            avg_util_percent,
            avg_memory_ratio,
            active_gpu_count,
            1,
            updated_at,
        ))
    if not rows:
        return
    with gpu_accounting_db_lock:
        conn = open_gpu_accounting_db()
        try:
            conn.executemany(
                """
                INSERT INTO gpu_accounting_minute_usage (
                    bucket_start, username, server_id, container_name,
                    gpu_card_hours, low_efficiency_card_hours,
                    util_percent_sum, memory_ratio_sum,
                    peak_active_gpu_count, sample_count, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(bucket_start, username, server_id, container_name) DO UPDATE SET
                    gpu_card_hours = gpu_card_hours + excluded.gpu_card_hours,
                    low_efficiency_card_hours = low_efficiency_card_hours + excluded.low_efficiency_card_hours,
                    util_percent_sum = util_percent_sum + excluded.util_percent_sum,
                    memory_ratio_sum = memory_ratio_sum + excluded.memory_ratio_sum,
                    peak_active_gpu_count = MAX(peak_active_gpu_count, excluded.peak_active_gpu_count),
                    sample_count = sample_count + excluded.sample_count,
                    updated_at = excluded.updated_at
                """,
                rows,
            )
            conn.commit()
        finally:
            conn.close()

def apply_gpu_accounting_server_snapshot(server_id, server_name, sample_dt, sample_ts, sample_interval_seconds, containers):
    observed_state_keys = set()
    with gpu_accounting_runtime_lock:
        for container in containers:
            gpu = container.get("gpu", {}) or {}
            if not gpu.get("enabled"):
                continue
            username = resolve_gpu_accounting_username(container.get("assigned_to", ""), container.get("login_user", ""))
            if not username:
                continue
            container_key = str(container.get("id") or container.get("name") or "").strip()
            container_name = str(container.get("name") or container_key).strip()
            if not container_key or not container_name:
                continue
            metadata = {
                "server_id": server_id,
                "server_name": server_name,
                "username": username,
                "assigned_to": str(container.get("assigned_to") or "").strip(),
                "login_user": str(container.get("login_user") or "").strip(),
                "container_name": container_name,
                "container_key": container_key,
                "container_id": str(container.get("id") or "").strip(),
            }
            util_supported = bool(gpu.get("utilization_supported"))
            for device in gpu.get("devices", []) or []:
                device_id = str(device.get("id") or "").strip()
                if not device_id:
                    continue
                state_key = (server_id, container_key, device_id)
                observed_state_keys.add(state_key)
                observation = evaluate_gpu_accounting_device(device, util_supported)
                observation["observed"] = True
                apply_gpu_accounting_device_observation_locked(
                    state_key,
                    {
                        **metadata,
                        "device_id": device_id,
                        "device_uuid": str(device.get("uuid") or "").strip(),
                        "device_name": str(device.get("name") or "").strip(),
                    },
                    observation,
                    sample_ts,
                )

        for state_key, state in list(gpu_accounting_runtime["device_states"].items()):
            if state.get("server_id") != server_id or state_key in observed_state_keys:
                continue
            apply_gpu_accounting_device_observation_locked(
                state_key,
                state,
                {
                    "observed": False,
                    "active_candidate": False,
                    "low_efficiency": False,
                    "util_percent": 0,
                    "memory_used_bytes": 0,
                    "memory_ratio": 0.0,
                    "total_memory_bytes": clamp_int(state.get("device_memory_total_bytes", 0), 0, minimum=0),
                },
                sample_ts,
            )

        prune_gpu_accounting_device_states_locked(sample_ts)
        current_containers = rebuild_gpu_accounting_current_containers_locked(sample_ts)
        server_entries = [
            item for item in current_containers
            if item.get("server_id") == server_id
        ]

    write_gpu_accounting_bucket_samples(sample_dt, sample_interval_seconds, server_entries)

def sample_gpu_accounting_once():
    data = load_data()
    cfg = ensure_gpu_accounting_defaults(data)
    sample_interval_seconds = clamp_int(
        cfg.get("sampling_interval_seconds", GPU_ACCOUNTING_DEFAULT_SAMPLING_INTERVAL_SECONDS),
        GPU_ACCOUNTING_DEFAULT_SAMPLING_INTERVAL_SECONDS,
        minimum=5,
        maximum=300,
    )
    retention_days = clamp_int(
        cfg.get("retention_days", GPU_ACCOUNTING_DEFAULT_RETENTION_DAYS),
        GPU_ACCOUNTING_DEFAULT_RETENTION_DAYS,
        minimum=7,
        maximum=365,
    )
    sample_dt = datetime.now()
    sample_ts = time.time()
    server_errors = {}
    success_count = 0
    for server_id, server in (data.get("servers", {}) or {}).items():
        result = call_agent(server, "/gpu/accounting", timeout=max(15, sample_interval_seconds * 2))
        if result.get("status_code", 200) >= 400 or result.get("error"):
            server_errors[server_id] = result.get("error") or f"Agent HTTP {result.get('status_code', 500)}"
            continue
        apply_gpu_accounting_server_snapshot(
            server_id,
            server.get("name", server_id),
            sample_dt,
            sample_ts,
            sample_interval_seconds,
            result.get("containers", []) or [],
        )
        success_count += 1

    with gpu_accounting_runtime_lock:
        now_ts = time.time()
        prune_gpu_accounting_device_states_locked(now_ts)
        rebuild_gpu_accounting_current_containers_locked(now_ts)
        gpu_accounting_runtime["last_sample_at"] = iso_seconds(sample_dt)
        gpu_accounting_runtime["server_errors"] = server_errors
        if success_count > 0:
            gpu_accounting_runtime["last_success_at"] = iso_seconds(sample_dt)

    return sample_interval_seconds, retention_days

def gpu_accounting_worker_loop():
    next_cleanup_at = 0.0
    while True:
        started_at = time.time()
        interval = GPU_ACCOUNTING_DEFAULT_SAMPLING_INTERVAL_SECONDS
        retention_days = GPU_ACCOUNTING_DEFAULT_RETENTION_DAYS
        try:
            interval, retention_days = sample_gpu_accounting_once()
            now_ts = time.time()
            if now_ts >= next_cleanup_at:
                cleanup_gpu_accounting_history(retention_days)
                with gpu_accounting_runtime_lock:
                    gpu_accounting_runtime["last_cleanup_at"] = iso_seconds(datetime.now())
                next_cleanup_at = now_ts + 3600
        except Exception:
            app.logger.exception("GPU 计算时后台采样失败")
        elapsed = time.time() - started_at
        time.sleep(max(1, interval - elapsed))

def ensure_gpu_accounting_worker_started():
    global gpu_accounting_worker_started
    with gpu_accounting_worker_lock:
        if gpu_accounting_worker_started:
            return
        ensure_gpu_accounting_db()
        worker = threading.Thread(
            target=gpu_accounting_worker_loop,
            name="gpu-accounting-worker",
            daemon=True,
        )
        worker.start()
        gpu_accounting_worker_started = True

def query_gpu_accounting_rows(sql, params):
    ensure_gpu_accounting_db()
    with gpu_accounting_db_lock:
        conn = open_gpu_accounting_db()
        try:
            rows = conn.execute(sql, params).fetchall()
            return [dict(row) for row in rows]
        finally:
            conn.close()

def query_gpu_accounting_weekly_by_user(week_start, week_end):
    return query_gpu_accounting_rows(
        """
        SELECT
            username,
            SUM(gpu_card_hours) AS gpu_card_hours,
            SUM(low_efficiency_card_hours) AS low_efficiency_card_hours,
            SUM(util_percent_sum) AS util_percent_sum,
            SUM(memory_ratio_sum) AS memory_ratio_sum,
            MAX(peak_active_gpu_count) AS peak_active_gpu_count,
            SUM(sample_count) AS sample_count
        FROM gpu_accounting_minute_usage
        WHERE bucket_start >= ? AND bucket_start < ?
        GROUP BY username
        ORDER BY username
        """,
        (iso_seconds(week_start), iso_seconds(week_end)),
    )

def query_gpu_accounting_weekly_by_server_for_user(week_start, week_end, username):
    return query_gpu_accounting_rows(
        """
        SELECT
            username,
            server_id,
            SUM(gpu_card_hours) AS gpu_card_hours,
            SUM(low_efficiency_card_hours) AS low_efficiency_card_hours,
            SUM(util_percent_sum) AS util_percent_sum,
            SUM(memory_ratio_sum) AS memory_ratio_sum,
            MAX(peak_active_gpu_count) AS peak_active_gpu_count,
            SUM(sample_count) AS sample_count
        FROM gpu_accounting_minute_usage
        WHERE bucket_start >= ? AND bucket_start < ? AND username = ?
        GROUP BY username, server_id
        ORDER BY server_id
        """,
        (iso_seconds(week_start), iso_seconds(week_end), username),
    )

def query_gpu_accounting_weekly_by_container_for_user(week_start, week_end, username):
    return query_gpu_accounting_rows(
        """
        SELECT
            username,
            container_name,
            server_id,
            SUM(gpu_card_hours) AS gpu_card_hours,
            SUM(low_efficiency_card_hours) AS low_efficiency_card_hours,
            SUM(util_percent_sum) AS util_percent_sum,
            SUM(memory_ratio_sum) AS memory_ratio_sum,
            MAX(peak_active_gpu_count) AS peak_active_gpu_count,
            SUM(sample_count) AS sample_count
        FROM gpu_accounting_minute_usage
        WHERE bucket_start >= ? AND bucket_start < ? AND username = ?
        GROUP BY username, container_name, server_id
        ORDER BY container_name, server_id
        """,
        (iso_seconds(week_start), iso_seconds(week_end), username),
    )

def query_gpu_accounting_ranking_by_user(window_start, window_end):
    return query_gpu_accounting_rows(
        """
        SELECT
            username,
            SUM(gpu_card_hours) AS gpu_card_hours,
            SUM(low_efficiency_card_hours) AS low_efficiency_card_hours,
            SUM(util_percent_sum) AS util_percent_sum,
            SUM(memory_ratio_sum) AS memory_ratio_sum,
            MAX(peak_active_gpu_count) AS peak_active_gpu_count,
            SUM(sample_count) AS sample_count
        FROM gpu_accounting_minute_usage
        WHERE bucket_start >= ? AND bucket_start < ?
        GROUP BY username
        ORDER BY SUM(gpu_card_hours) DESC, username
        """,
        (iso_seconds(window_start), iso_seconds(window_end)),
    )

def query_gpu_accounting_daily_usage_for_user(window_start, window_end, username):
    return query_gpu_accounting_rows(
        """
        SELECT
            substr(bucket_start, 1, 10) AS usage_date,
            SUM(gpu_card_hours) AS gpu_card_hours,
            SUM(low_efficiency_card_hours) AS low_efficiency_card_hours,
            SUM(util_percent_sum) AS util_percent_sum,
            SUM(memory_ratio_sum) AS memory_ratio_sum,
            MAX(peak_active_gpu_count) AS peak_active_gpu_count,
            SUM(sample_count) AS sample_count
        FROM gpu_accounting_minute_usage
        WHERE bucket_start >= ? AND bucket_start < ? AND username = ?
        GROUP BY substr(bucket_start, 1, 10)
        ORDER BY usage_date
        """,
        (iso_seconds(window_start), iso_seconds(window_end), username),
    )

def usage_hours(value):
    try:
        return round(float(value or 0.0), 4)
    except (TypeError, ValueError):
        return 0.0

def usage_percent(value):
    try:
        return round(float(value or 0.0), 2)
    except (TypeError, ValueError):
        return 0.0

def clamp_day_window(value, default=GPU_PORTAL_DEFAULT_DAYS, minimum=1, maximum=365):
    return clamp_int(value, default, minimum=minimum, maximum=maximum)

def date_bucket_start(dt):
    return dt.replace(hour=0, minute=0, second=0, microsecond=0)

def daterange_days(start_dt, day_count):
    current = date_bucket_start(start_dt)
    for _ in range(max(0, int(day_count))):
        yield current
        current += timedelta(days=1)

def generate_short_token(length=GPU_PORTAL_TOKEN_LENGTH):
    token = ""
    while len(token) < length:
        token += secrets.token_urlsafe(length)
        token = re.sub(r"[^A-Za-z0-9]", "", token)
    return token[:length]

def mask_portal_username(username, viewer_username):
    normalized_username = str(username or "").strip()
    normalized_viewer = str(viewer_username or "").strip()
    if not normalized_username:
        return ""
    if normalized_username == normalized_viewer:
        return normalized_username
    if len(normalized_username) <= 1:
        return "*"
    if len(normalized_username) == 2:
        return normalized_username[0] + "*"
    return normalized_username[0] + ("*" * (len(normalized_username) - 2)) + normalized_username[-1]

def build_gpu_accounting_portal_url(token):
    normalized_token = str(token or "").strip()
    if not normalized_token:
        return ""
    root = str(os.environ.get("GPU_PORTAL_PUBLIC_BASE_URL") or "").strip()
    if root:
        parsed = urlsplit(root)
        path = (parsed.path or "").rstrip("/")
        return urlunsplit((parsed.scheme, parsed.netloc, f"{path}/portal/{normalized_token}", "", ""))
    if has_request_context():
        try:
            current = urlsplit(request.url_root)
            hostname = current.hostname or ""
            if hostname:
                portal_port = clamp_int(
                    os.environ.get("GPU_PORTAL_PORT", "5002"),
                    5002,
                    minimum=1,
                    maximum=65535,
                )
                if ":" in hostname and not hostname.startswith("["):
                    hostname = f"[{hostname}]"
                netloc = f"{hostname}:{portal_port}"
                return urlunsplit((current.scheme or "http", netloc, f"/portal/{normalized_token}", "", ""))
        except Exception:
            pass
    return f"/portal/{normalized_token}"

def find_gpu_portal_token_record(data, username=""):
    cfg = ensure_gpu_accounting_defaults(data)
    portal_tokens = cfg.setdefault("portal_tokens", {})
    normalized_username = str(username or "").strip()
    if normalized_username:
        return portal_tokens.get(normalized_username)
    return portal_tokens

def ensure_gpu_portal_token(data, username, operator="", force_reset=False):
    cfg = ensure_gpu_accounting_defaults(data)
    portal_tokens = cfg.setdefault("portal_tokens", {})
    normalized_username = str(username or "").strip()
    if not normalized_username:
        return None
    now_text = datetime.now().isoformat(timespec="seconds")
    current = portal_tokens.get(normalized_username) or {}
    if current.get("token") and not force_reset:
        return {
            "username": normalized_username,
            **current,
            "url": build_gpu_accounting_portal_url(current.get("token")),
        }
    token = generate_short_token()
    while any((item or {}).get("token") == token for item in portal_tokens.values()):
        token = generate_short_token()
    next_record = {
        "token": token,
        "created_at": str(current.get("created_at") or now_text).strip() or now_text,
        "created_by": str(current.get("created_by") or operator).strip(),
        "last_reset_at": now_text if current.get("token") else "",
        "last_reset_by": operator if current.get("token") else "",
    }
    portal_tokens[normalized_username] = next_record
    return {
        "username": normalized_username,
        **next_record,
        "url": build_gpu_accounting_portal_url(token),
    }

def get_gpu_portal_user_by_token(data, token):
    normalized_token = str(token or "").strip()
    if not normalized_token:
        return "", None
    for username, record in (find_gpu_portal_token_record(data) or {}).items():
        if str((record or {}).get("token") or "").strip() == normalized_token:
            return str(username or "").strip(), record
    return "", None

def avg_from_sums(total, count):
    try:
        count_value = float(count or 0)
    except (TypeError, ValueError):
        count_value = 0.0
    if count_value <= 0:
        return 0.0
    try:
        total_value = float(total or 0.0)
    except (TypeError, ValueError):
        total_value = 0.0
    return total_value / count_value

def build_gpu_accounting_current_user_map(current_containers):
    user_map = {}
    for entry in current_containers:
        username = str(entry.get("username") or "").strip()
        if not username:
            continue
        user_entry = user_map.setdefault(username, {
            "current_active_gpu_count": 0,
            "current_low_efficiency_gpu_count": 0,
            "servers": {},
        })
        active_gpu_count = clamp_int(entry.get("active_gpu_count", 0), 0, minimum=0)
        low_efficiency_gpu_count = clamp_int(entry.get("low_efficiency_gpu_count", 0), 0, minimum=0)
        user_entry["current_active_gpu_count"] += active_gpu_count
        user_entry["current_low_efficiency_gpu_count"] += low_efficiency_gpu_count
        server_id = str(entry.get("server_id") or "").strip()
        server_entry = user_entry["servers"].setdefault(server_id, {
            "server_id": server_id,
            "server_name": entry.get("server_name", server_id),
            "current_active_gpu_count": 0,
            "current_low_efficiency_gpu_count": 0,
            "containers": [],
        })
        server_entry["current_active_gpu_count"] += active_gpu_count
        server_entry["current_low_efficiency_gpu_count"] += low_efficiency_gpu_count
        server_entry["containers"].append(json.loads(json.dumps(entry, ensure_ascii=False)))
    return user_map

def build_gpu_ranking_payload(data, rows, week_start=None):
    current_week_start = week_start or current_week_window()[0]
    current_week_end = current_week_start + timedelta(days=7)
    weekly_rows = query_gpu_accounting_weekly_by_user(current_week_start, current_week_end)
    weekly_map = {
        str(row.get("username") or "").strip(): row
        for row in weekly_rows
        if str(row.get("username") or "").strip()
    }
    ranking = []
    for index, row in enumerate(rows, start=1):
        username = str(row.get("username") or "").strip()
        if not username:
            continue
        quota = current_gpu_quota_snapshot(data, username, current_week_start)
        used_hours = usage_hours(row.get("gpu_card_hours", 0))
        weekly_row = weekly_map.get(username, {})
        weekly_used_hours = usage_hours(weekly_row.get("gpu_card_hours", 0))
        weekly_quota_status = current_gpu_quota_status(weekly_used_hours, quota["effective_quota_hours"])
        ranking.append({
            "rank": index,
            "username": username,
            "base_quota_hours": quota["base_quota_hours"],
            "user_base_quota_hours": quota["user_base_quota_hours"],
            "temporary_extra_quota_hours": usage_hours(quota["temporary_extra_quota_hours"]),
            "effective_quota_hours": usage_hours(quota["effective_quota_hours"]),
            "gpu_card_hours": used_hours,
            "low_efficiency_card_hours": usage_hours(row.get("low_efficiency_card_hours", 0)),
            "avg_gpu_util_percent": usage_percent(avg_from_sums(row.get("util_percent_sum", 0), row.get("sample_count", 0))),
            "avg_gpu_memory_ratio_percent": usage_percent(avg_from_sums(row.get("memory_ratio_sum", 0), row.get("sample_count", 0)) * 100.0),
            "peak_active_gpu_count": clamp_int(row.get("peak_active_gpu_count", 0), 0, minimum=0),
            "weekly_gpu_card_hours": weekly_used_hours,
            "weekly_over_quota_hours": usage_hours(weekly_quota_status["over_quota_hours"]),
            "weekly_usage_ratio": weekly_quota_status["usage_ratio"],
            "quota_status": weekly_quota_status["quota_status"],
            "usage_ratio": weekly_quota_status["usage_ratio"],
            "over_quota_hours": usage_hours(weekly_quota_status["over_quota_hours"]),
        })
    ranking.sort(key=lambda item: (-item.get("gpu_card_hours", 0.0), item.get("username", "")))
    for index, item in enumerate(ranking, start=1):
        item["rank"] = index
    return ranking

def build_gpu_daily_usage_payload(username, rows, days, window_start):
    daily_map = {}
    for row in rows:
        usage_date = str(row.get("usage_date") or "").strip()
        if not usage_date:
            continue
        daily_map[usage_date] = {
            "date": usage_date,
            "gpu_card_hours": usage_hours(row.get("gpu_card_hours", 0)),
            "low_efficiency_card_hours": usage_hours(row.get("low_efficiency_card_hours", 0)),
            "avg_gpu_util_percent": usage_percent(avg_from_sums(row.get("util_percent_sum", 0), row.get("sample_count", 0))),
            "avg_gpu_memory_ratio_percent": usage_percent(avg_from_sums(row.get("memory_ratio_sum", 0), row.get("sample_count", 0)) * 100.0),
            "peak_active_gpu_count": clamp_int(row.get("peak_active_gpu_count", 0), 0, minimum=0),
        }
    series = []
    for day in daterange_days(window_start, days):
        key = day.date().isoformat()
        series.append(daily_map.get(key, {
            "date": key,
            "gpu_card_hours": 0.0,
            "low_efficiency_card_hours": 0.0,
            "avg_gpu_util_percent": 0.0,
            "avg_gpu_memory_ratio_percent": 0.0,
            "peak_active_gpu_count": 0,
        }))
    return {
        "username": username,
        "days": days,
        "series": series,
    }

def build_gpu_portal_me_payload(data, username, days):
    normalized_days = clamp_day_window(days, default=GPU_PORTAL_DEFAULT_DAYS, minimum=7, maximum=365)
    window_end = date_bucket_start(datetime.now()) + timedelta(days=1)
    window_start = window_end - timedelta(days=normalized_days)
    ranking_rows = query_gpu_accounting_ranking_by_user(window_start, window_end)
    ranking = build_gpu_ranking_payload(data, ranking_rows)
    my_row = next((item for item in ranking if item.get("username") == username), None)
    daily_rows = query_gpu_accounting_daily_usage_for_user(window_start, window_end, username)
    daily = build_gpu_daily_usage_payload(username, daily_rows, normalized_days, window_start)
    quota = current_gpu_quota_snapshot(data, username, current_week_window()[0])
    return {
        "username": username,
        "days": normalized_days,
        "window_start": window_start.date().isoformat(),
        "window_end": (window_end - timedelta(days=1)).date().isoformat(),
        "me": my_row or {
            "rank": None,
            "username": username,
            "gpu_card_hours": 0.0,
            "low_efficiency_card_hours": 0.0,
            "avg_gpu_util_percent": 0.0,
            "avg_gpu_memory_ratio_percent": 0.0,
            "peak_active_gpu_count": 0,
            "base_quota_hours": quota["base_quota_hours"],
            "user_base_quota_hours": quota["user_base_quota_hours"],
            "temporary_extra_quota_hours": usage_hours(quota["temporary_extra_quota_hours"]),
            "effective_quota_hours": usage_hours(quota["effective_quota_hours"]),
            **current_gpu_quota_status(0.0, quota["effective_quota_hours"]),
        },
        "daily": daily,
    }

@app.errorhandler(Exception)
def handle_exception(exc):
    if isinstance(exc, HTTPException):
        return exc
    app.logger.exception("Unhandled exception")
    if request.path.startswith("/api/") or request.path.startswith("/portal-api/"):
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

def run_image_pull_task(task_id, server, image, operator="system", operator_role="system"):
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
    append_audit(
        data,
        f"镜像拉取{('完成' if status == 'done' else '失败')} {image}",
        "INFO" if status == "done" else "WARN",
        operator=operator,
        operator_role=operator_role,
    )
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

def normalize_runtime_options(raw_options):
    options = raw_options if isinstance(raw_options, dict) else {}
    network_mode = str(options.get("network_mode", "bridge") or "bridge").strip().lower() or "bridge"
    if network_mode not in {"bridge", "host", "none"}:
        network_mode = "bridge"
    ipc_mode = str(options.get("ipc_mode", "private") or "private").strip().lower() or "private"
    if ipc_mode not in {"private", "host"}:
        ipc_mode = "private"
    shm_size = str(options.get("shm_size", "") or "").strip()
    if shm_size and not re.match(r"^[1-9][0-9]*(m|g|M|G)?$", shm_size):
        shm_size = ""
    elif shm_size:
        match = re.match(r"^([1-9][0-9]*)([mMgG]?)$", shm_size)
        if match:
            number, suffix = match.groups()
            shm_size = f"{number}{suffix.upper()}" if suffix else number
    raw_ulimits = options.get("ulimits", {})
    ulimits = {}
    if isinstance(raw_ulimits, dict):
        for key in ("memlock", "stack", "nofile"):
            value = str(raw_ulimits.get(key, "") or "").strip()
            if not value:
                continue
            if re.match(r"^-?[0-9]+(?::-?[0-9]+)?$", value):
                ulimits[key] = value
    raw_caps = options.get("cap_add", [])
    cap_add = []
    if isinstance(raw_caps, list):
        for item in raw_caps:
            cap = str(item or "").strip().upper()
            if cap == "SYS_PTRACE" and cap not in cap_add:
                cap_add.append(cap)
    return {
        "network_mode": network_mode,
        "ipc_mode": ipc_mode,
        "shm_size": shm_size,
        "ulimits": ulimits,
        "cap_add": cap_add,
    }

def normalize_extra_port_count(value, default=DEFAULT_EXTRA_PORT_COUNT):
    try:
        count = int(value)
    except (TypeError, ValueError):
        count = default
    if count < 0:
        count = 0
    if count > MAX_EXTRA_PORT_COUNT:
        count = MAX_EXTRA_PORT_COUNT
    return count

def normalize_container_port_value(value):
    try:
        port = int(value)
    except (TypeError, ValueError):
        return None
    if port < 1 or port > 65535:
        return None
    return port

def build_default_extra_port_bindings(extra_port_count):
    bindings = []
    for index in range(extra_port_count):
        bindings.append({
            "host_port": None,
            "container_port": 8000 + index,
            "protocol": "tcp",
        })
    return bindings

def normalize_extra_port_bindings(raw_bindings, extra_port_count=None):
    count = normalize_extra_port_count(
        extra_port_count if extra_port_count is not None else (len(raw_bindings) if isinstance(raw_bindings, list) else DEFAULT_EXTRA_PORT_COUNT),
        default=DEFAULT_EXTRA_PORT_COUNT,
    )
    items = raw_bindings if isinstance(raw_bindings, list) else []
    normalized = []
    for index in range(count):
        item = items[index] if index < len(items) and isinstance(items[index], dict) else {}
        host_port = item.get("host_port")
        if host_port in ("", None):
            host_port = None
        else:
            try:
                host_port = int(host_port)
            except (TypeError, ValueError):
                host_port = None
        container_port = normalize_container_port_value(item.get("container_port"))
        if container_port is None:
            container_port = 8000 + index
        normalized.append({
            "host_port": host_port,
            "container_port": container_port,
            "protocol": "tcp",
        })
    return normalized

def format_port_mapping_rules(extra_port_bindings, ssh_host):
    rules = []
    host_text = ssh_host or "<SERVER_HOST>"
    for binding in extra_port_bindings or []:
        host_port = binding.get("host_port")
        container_port = binding.get("container_port")
        protocol = str(binding.get("protocol", "tcp") or "tcp").lower()
        if not host_port or not container_port:
            continue
        rules.append(f"{host_text}:{host_port} -> container:{container_port}/{protocol}")
    return rules

def build_connection_bundle(login_user, ssh_port, ssh_host, extra_port_bindings):
    ssh_cmd = build_ssh_cmd(login_user, ssh_port, ssh_host)
    lines = []
    if ssh_cmd:
        lines.append(ssh_cmd)
    port_rules = format_port_mapping_rules(extra_port_bindings, ssh_host)
    if port_rules:
        if lines:
            lines.append("")
        lines.append("# 端口映射")
        lines.extend(port_rules)
    return {
        "ssh_cmd": ssh_cmd,
        "port_rules": port_rules,
        "copy_text": "\n".join(lines).strip(),
    }

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
        "runtime_options": normalize_runtime_options(raw.get("runtime_options", {})),
        "extra_port_bindings": normalize_extra_port_bindings(
            raw.get("extra_port_bindings", []),
            extra_port_count=len(raw.get("extra_port_bindings", [])) if isinstance(raw.get("extra_port_bindings"), list) else None,
        ),
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
    runtime_options=None,
    extra_port_bindings=None,
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
    next_runtime_options = normalize_runtime_options(runtime_options if runtime_options is not None else record.get("runtime_options", {}))
    fallback_extra_port_count = 0 if not is_new and not record.get("extra_port_bindings") and extra_port_bindings is None else DEFAULT_EXTRA_PORT_COUNT
    next_extra_port_bindings = normalize_extra_port_bindings(
        extra_port_bindings if extra_port_bindings is not None else record.get("extra_port_bindings", []),
        extra_port_count=len(extra_port_bindings) if isinstance(extra_port_bindings, list) else len(record.get("extra_port_bindings", []) or []) or fallback_extra_port_count,
    )
    bundle = build_connection_bundle(next_login_user, next_ssh_port, ssh_host, next_extra_port_bindings)
    if recovered and is_new and not next_created_by:
        next_created_by = "__recovered__"
    record.update({
        "name": name,
        "agent_container_id": agent_container_id or record.get("agent_container_id", ""),
        "assigned_to": assigned_to or record.get("assigned_to", ""),
        "server_id": server_id,
        "image": image or record.get("image", ""),
        "ssh_port": next_ssh_port,
        "ssh_cmd": bundle["ssh_cmd"],
        "connection_copy_text": bundle["copy_text"],
        "port_rules": bundle["port_rules"],
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
        "runtime_options": next_runtime_options,
        "extra_port_bindings": next_extra_port_bindings,
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
                runtime_options=agent_container.get("runtime_options", {}),
                extra_port_bindings=agent_container.get("extra_port_bindings", []),
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
            runtime_options=agent_container.get("runtime_options", defaults.get("runtime_options", {})),
            extra_port_bindings=agent_container.get("extra_port_bindings", defaults.get("extra_port_bindings", [])),
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

def choose_available_extra_port_block(server, existing_records=None, count=DEFAULT_EXTRA_PORT_COUNT):
    count = normalize_extra_port_count(count)
    if count <= 0:
        return []
    used_ports = set()
    result = call_agent(server, "/containers", timeout=30)
    if result.get("status_code", 200) < 400 and not result.get("error"):
        for raw in result.get("containers", []):
            agent_container = normalize_agent_container(raw)
            if not agent_container:
                continue
            for binding in agent_container.get("extra_port_bindings", []):
                host_port = binding.get("host_port")
                try:
                    if host_port:
                        used_ports.add(int(host_port))
                except (TypeError, ValueError):
                    continue
    for record in existing_records or []:
        for binding in record.get("extra_port_bindings", []) or []:
            host_port = binding.get("host_port")
            try:
                if host_port:
                    used_ports.add(int(host_port))
            except (TypeError, ValueError):
                continue
    for start in range(EXTRA_PORT_MIN, EXTRA_PORT_MAX - count + 2):
        block = list(range(start, start + count))
        if all(port not in used_ports for port in block):
            return block
    available = [port for port in range(EXTRA_PORT_MIN, EXTRA_PORT_MAX + 1) if port not in used_ports]
    if len(available) < count:
        return []
    return available[:count]

def fill_missing_extra_host_ports(server, existing_records, extra_port_bindings):
    bindings = normalize_extra_port_bindings(
        extra_port_bindings,
        extra_port_count=len(extra_port_bindings) if isinstance(extra_port_bindings, list) else 0,
    )
    assigned_host_ports = set()
    for binding in bindings:
        host_port = normalize_container_port_value(binding.get("host_port"))
        if host_port is None:
            continue
        if host_port < EXTRA_PORT_MIN or host_port > EXTRA_PORT_MAX or host_port in assigned_host_ports:
            return []
        assigned_host_ports.add(host_port)
    missing_indexes = [index for index, binding in enumerate(bindings) if not binding.get("host_port")]
    if not missing_indexes:
        return bindings
    reserved_records = list(existing_records or [])
    if assigned_host_ports:
        reserved_records.append({
            "extra_port_bindings": [{"host_port": port} for port in sorted(assigned_host_ports)]
        })
    allocated_ports = choose_available_extra_port_block(server, reserved_records, len(missing_indexes))
    if len(allocated_ports) != len(missing_indexes):
        return []
    for index, host_port in zip(missing_indexes, allocated_ports):
        bindings[index]["host_port"] = host_port
    return bindings

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
        if APP_MODE != "panel":
            return ("Not Found", 404)
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

def panel_mode_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if APP_MODE != "panel":
            return ("Not Found", 404)
        return f(*args, **kwargs)
    return decorated

def portal_mode_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if APP_MODE != "portal":
            return ("Not Found", 404)
        return f(*args, **kwargs)
    return decorated

# ── 路由：认证 ──────────────────────────────────────────────────────────────
@app.route("/")
@panel_mode_required
def index():
    if "user" not in session:
        return redirect(url_for("login"))
    return redirect(url_for("dashboard"))

@app.route("/login", methods=["GET", "POST"])
@panel_mode_required
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
@panel_mode_required
def logout():
    session.clear()
    return redirect(url_for("login"))

# ── 路由：主页面（SPA 壳） ───────────────────────────────────────────────────
@app.route("/dashboard")
@panel_mode_required
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
@role_required("admin", "allocator")
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
@role_required("admin", "allocator")
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
@role_required("admin", "allocator")
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
    threading.Thread(
        target=run_image_pull_task,
        args=(task_id, dict(server), image, session["user"], session["role"]),
        daemon=True,
    ).start()
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
                "runtime_options": normalize_runtime_options(c.get("runtime_options", {})),
                "extra_port_bindings": normalize_extra_port_bindings(c.get("extra_port_bindings", [])),
                "connection_copy_text": c.get("connection_copy_text", ""),
                "port_rules": c.get("port_rules", []),
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
                "port_statuses": item.get("port_statuses", []),
                "gpu": item.get("gpu", {}),
            }
    return jsonify({
        "ok": True,
        "metrics": metrics,
        "errors": errors,
        "collected_at": datetime.now().isoformat(timespec="seconds"),
    })

@app.route("/api/gpu-accounting/summary", methods=["GET"])
@login_required
def api_gpu_accounting_summary():
    ensure_gpu_accounting_worker_started()
    data = load_data()
    week_start, week_end = current_week_window()
    weekly_rows = query_gpu_accounting_weekly_by_user(week_start, week_end)
    weekly_map = {str(row.get("username") or "").strip(): row for row in weekly_rows if str(row.get("username") or "").strip()}
    runtime = snapshot_gpu_accounting_runtime()
    current_user_map = build_gpu_accounting_current_user_map(runtime.get("current_containers", []))

    users = []
    all_usernames = set(weekly_map.keys()) | set(current_user_map.keys()) | gpu_accounting_known_usernames(data)
    for username in sorted(all_usernames):
        weekly = weekly_map.get(username, {})
        current = current_user_map.get(username, {})
        quota = current_gpu_quota_snapshot(data, username, week_start)
        weekly_gpu_card_hours = usage_hours(weekly.get("gpu_card_hours", 0))
        quota_status = current_gpu_quota_status(weekly_gpu_card_hours, quota["effective_quota_hours"])
        users.append({
            "username": username,
            "base_quota_hours": quota["base_quota_hours"],
            "user_base_quota_hours": quota["user_base_quota_hours"],
            "temporary_extra_quota_hours": usage_hours(quota["temporary_extra_quota_hours"]),
            "effective_quota_hours": usage_hours(quota["effective_quota_hours"]),
            "weekly_gpu_card_hours": weekly_gpu_card_hours,
            "weekly_low_efficiency_card_hours": usage_hours(weekly.get("low_efficiency_card_hours", 0)),
            "weekly_avg_gpu_util_percent": usage_percent(avg_from_sums(weekly.get("util_percent_sum", 0), weekly.get("sample_count", 0))),
            "weekly_avg_gpu_memory_ratio_percent": usage_percent(avg_from_sums(weekly.get("memory_ratio_sum", 0), weekly.get("sample_count", 0)) * 100.0),
            "weekly_peak_active_gpu_count": clamp_int(weekly.get("peak_active_gpu_count", 0), 0, minimum=0),
            "current_active_gpu_count": clamp_int(current.get("current_active_gpu_count", 0), 0, minimum=0),
            "current_low_efficiency_gpu_count": clamp_int(current.get("current_low_efficiency_gpu_count", 0), 0, minimum=0),
            "active_temp_quota_count": len(quota["active_temp_quotas"]),
            **quota_status,
        })
    users.sort(key=lambda item: (-item.get("weekly_gpu_card_hours", 0.0), item.get("username", "")))

    cfg = ensure_gpu_accounting_defaults(data)
    stats = {
        "weekly_total_gpu_card_hours": usage_hours(sum(item.get("weekly_gpu_card_hours", 0.0) for item in users)),
        "over_quota_user_count": len([item for item in users if item.get("quota_status") in ("over", "critical")]),
        "warning_user_count": len([item for item in users if item.get("quota_status") == "warn"]),
        "critical_user_count": len([item for item in users if item.get("quota_status") == "critical"]),
        "current_active_user_count": len([item for item in users if item.get("current_active_gpu_count", 0) > 0]),
        "current_low_efficiency_user_count": len([item for item in users if item.get("current_low_efficiency_gpu_count", 0) > 0]),
    }
    return jsonify({
        "ok": True,
        "week_start": iso_seconds(week_start),
        "week_end": iso_seconds(week_end),
        "users": users,
        "stats": stats,
        "sampling_interval_seconds": cfg.get("sampling_interval_seconds", GPU_ACCOUNTING_DEFAULT_SAMPLING_INTERVAL_SECONDS),
        "retention_days": cfg.get("retention_days", GPU_ACCOUNTING_DEFAULT_RETENTION_DAYS),
        "default_weekly_quota_hours": cfg.get("default_weekly_quota_hours", GPU_ACCOUNTING_DEFAULT_WEEKLY_QUOTA_HOURS),
        "last_sample_at": runtime.get("last_sample_at", ""),
        "last_success_at": runtime.get("last_success_at", ""),
        "server_errors": runtime.get("server_errors", {}),
    })

@app.route("/api/gpu-accounting/users/<path:username>", methods=["GET"])
@login_required
def api_gpu_accounting_user_detail(username):
    ensure_gpu_accounting_worker_started()
    data = load_data()
    username = str(username or "").strip()
    if not username:
        return jsonify({"error": "缺少用户标识"}), 400

    week_start, week_end = current_week_window()
    server_rows = query_gpu_accounting_weekly_by_server_for_user(week_start, week_end, username)
    container_rows = query_gpu_accounting_weekly_by_container_for_user(week_start, week_end, username)
    runtime = snapshot_gpu_accounting_runtime()
    current_user_map = build_gpu_accounting_current_user_map(runtime.get("current_containers", []))
    current_user = current_user_map.get(username, {"servers": {}, "current_active_gpu_count": 0, "current_low_efficiency_gpu_count": 0})

    server_names = {
        sid: (server.get("name", sid))
        for sid, server in (data.get("servers", {}) or {}).items()
    }
    current_server_map = current_user.get("servers", {})
    current_container_map = {}
    for server_entry in current_server_map.values():
        for container in server_entry.get("containers", []):
            current_container_map[(server_entry.get("server_id", ""), container.get("container_name", ""))] = container

    weekly_server_map = {
        str(row.get("server_id") or "").strip(): row
        for row in server_rows
    }
    weekly_container_map = {}
    containers_by_name = {}
    for row in container_rows:
        server_id = str(row.get("server_id") or "").strip()
        container_name = str(row.get("container_name") or "").strip()
        weekly_container_map[(server_id, container_name)] = row
        aggregated = containers_by_name.setdefault(container_name, {
            "container_name": container_name,
            "weekly_gpu_card_hours": 0.0,
            "weekly_low_efficiency_card_hours": 0.0,
            "weekly_peak_active_gpu_count": 0,
            "sample_count": 0,
            "util_percent_sum": 0.0,
            "memory_ratio_sum": 0.0,
            "current_active_gpu_count": 0,
            "current_low_efficiency_gpu_count": 0,
            "servers": [],
        })
        aggregated["weekly_gpu_card_hours"] += float(row.get("gpu_card_hours") or 0.0)
        aggregated["weekly_low_efficiency_card_hours"] += float(row.get("low_efficiency_card_hours") or 0.0)
        aggregated["weekly_peak_active_gpu_count"] = max(
            aggregated["weekly_peak_active_gpu_count"],
            clamp_int(row.get("peak_active_gpu_count", 0), 0, minimum=0),
        )
        aggregated["sample_count"] += clamp_int(row.get("sample_count", 0), 0, minimum=0)
        aggregated["util_percent_sum"] += float(row.get("util_percent_sum") or 0.0)
        aggregated["memory_ratio_sum"] += float(row.get("memory_ratio_sum") or 0.0)

    servers = []
    for server_id in sorted(set(weekly_server_map.keys()) | set(current_server_map.keys())):
        weekly = weekly_server_map.get(server_id, {})
        current = current_server_map.get(server_id, {})
        containers = []
        server_container_names = {
            key[1] for key in weekly_container_map.keys() if key[0] == server_id
        } | {
            str(item.get("container_name") or "").strip()
            for item in current.get("containers", [])
            if str(item.get("container_name") or "").strip()
        }
        for container_name in sorted(server_container_names):
            weekly_container = weekly_container_map.get((server_id, container_name), {})
            current_container = current_container_map.get((server_id, container_name), {})
            current_devices = current_container.get("devices", [])
            containers.append({
                "container_name": container_name,
                "login_user": current_container.get("login_user", ""),
                "weekly_gpu_card_hours": usage_hours(weekly_container.get("gpu_card_hours", 0)),
                "weekly_low_efficiency_card_hours": usage_hours(weekly_container.get("low_efficiency_card_hours", 0)),
                "weekly_avg_gpu_util_percent": usage_percent(avg_from_sums(weekly_container.get("util_percent_sum", 0), weekly_container.get("sample_count", 0))),
                "weekly_avg_gpu_memory_ratio_percent": usage_percent(avg_from_sums(weekly_container.get("memory_ratio_sum", 0), weekly_container.get("sample_count", 0)) * 100.0),
                "weekly_peak_active_gpu_count": clamp_int(weekly_container.get("peak_active_gpu_count", 0), 0, minimum=0),
                "current_active_gpu_count": clamp_int(current_container.get("active_gpu_count", 0), 0, minimum=0),
                "current_low_efficiency_gpu_count": clamp_int(current_container.get("low_efficiency_gpu_count", 0), 0, minimum=0),
                "current_gpu_devices": [device.get("id", "") for device in current_devices],
                "current_devices": current_devices,
            })
        containers.sort(key=lambda item: (-item.get("weekly_gpu_card_hours", 0.0), item.get("container_name", "")))
        servers.append({
            "server_id": server_id,
            "server_name": server_names.get(server_id, current.get("server_name", server_id)),
            "weekly_gpu_card_hours": usage_hours(weekly.get("gpu_card_hours", 0)),
            "weekly_low_efficiency_card_hours": usage_hours(weekly.get("low_efficiency_card_hours", 0)),
            "weekly_avg_gpu_util_percent": usage_percent(avg_from_sums(weekly.get("util_percent_sum", 0), weekly.get("sample_count", 0))),
            "weekly_avg_gpu_memory_ratio_percent": usage_percent(avg_from_sums(weekly.get("memory_ratio_sum", 0), weekly.get("sample_count", 0)) * 100.0),
            "weekly_peak_active_gpu_count": clamp_int(weekly.get("peak_active_gpu_count", 0), 0, minimum=0),
            "current_active_gpu_count": clamp_int(current.get("current_active_gpu_count", 0), 0, minimum=0),
            "current_low_efficiency_gpu_count": clamp_int(current.get("current_low_efficiency_gpu_count", 0), 0, minimum=0),
            "containers": containers,
        })
    servers.sort(key=lambda item: (-item.get("weekly_gpu_card_hours", 0.0), item.get("server_id", "")))

    for server_entry in current_server_map.values():
        for container in server_entry.get("containers", []):
            container_name = str(container.get("container_name") or "").strip()
            if not container_name:
                continue
            aggregated = containers_by_name.setdefault(container_name, {
                "container_name": container_name,
                "weekly_gpu_card_hours": 0.0,
                "weekly_low_efficiency_card_hours": 0.0,
                "weekly_peak_active_gpu_count": 0,
                "sample_count": 0,
                "util_percent_sum": 0.0,
                "memory_ratio_sum": 0.0,
                "current_active_gpu_count": 0,
                "current_low_efficiency_gpu_count": 0,
                "servers": [],
            })
            aggregated["current_active_gpu_count"] += clamp_int(container.get("active_gpu_count", 0), 0, minimum=0)
            aggregated["current_low_efficiency_gpu_count"] += clamp_int(container.get("low_efficiency_gpu_count", 0), 0, minimum=0)

    container_name_view = []
    for container_name, aggregated in containers_by_name.items():
        server_list = []
        for server_id in sorted(server_names.keys() | current_server_map.keys()):
            weekly_container = weekly_container_map.get((server_id, container_name), {})
            current_container = current_container_map.get((server_id, container_name), {})
            if not weekly_container and not current_container:
                continue
            current_devices = current_container.get("devices", [])
            server_list.append({
                "server_id": server_id,
                "server_name": server_names.get(server_id, current_container.get("server_name", server_id)),
                "weekly_gpu_card_hours": usage_hours(weekly_container.get("gpu_card_hours", 0)),
                "weekly_low_efficiency_card_hours": usage_hours(weekly_container.get("low_efficiency_card_hours", 0)),
                "current_active_gpu_count": clamp_int(current_container.get("active_gpu_count", 0), 0, minimum=0),
                "current_low_efficiency_gpu_count": clamp_int(current_container.get("low_efficiency_gpu_count", 0), 0, minimum=0),
                "current_gpu_devices": [device.get("id", "") for device in current_devices],
            })
        container_name_view.append({
            "container_name": container_name,
            "weekly_gpu_card_hours": usage_hours(aggregated.get("weekly_gpu_card_hours", 0)),
            "weekly_low_efficiency_card_hours": usage_hours(aggregated.get("weekly_low_efficiency_card_hours", 0)),
            "weekly_avg_gpu_util_percent": usage_percent(avg_from_sums(aggregated.get("util_percent_sum", 0), aggregated.get("sample_count", 0))),
            "weekly_avg_gpu_memory_ratio_percent": usage_percent(avg_from_sums(aggregated.get("memory_ratio_sum", 0), aggregated.get("sample_count", 0)) * 100.0),
            "weekly_peak_active_gpu_count": clamp_int(aggregated.get("weekly_peak_active_gpu_count", 0), 0, minimum=0),
            "current_active_gpu_count": clamp_int(aggregated.get("current_active_gpu_count", 0), 0, minimum=0),
            "current_low_efficiency_gpu_count": clamp_int(aggregated.get("current_low_efficiency_gpu_count", 0), 0, minimum=0),
            "servers": server_list,
        })
    container_name_view.sort(key=lambda item: (-item.get("weekly_gpu_card_hours", 0.0), item.get("container_name", "")))

    cfg = ensure_gpu_accounting_defaults(data)
    quota = current_gpu_quota_snapshot(data, username, week_start)
    weekly_total_hours = sum(item.get("weekly_gpu_card_hours", 0.0) for item in servers)
    weekly_low_efficiency_total_hours = sum(item.get("weekly_low_efficiency_card_hours", 0.0) for item in servers)
    quota_status = current_gpu_quota_status(weekly_total_hours, quota["effective_quota_hours"])
    return jsonify({
        "ok": True,
        "username": username,
        "week_start": iso_seconds(week_start),
        "week_end": iso_seconds(week_end),
        "base_quota_hours": quota["base_quota_hours"],
        "user_base_quota_hours": quota["user_base_quota_hours"],
        "temporary_extra_quota_hours": usage_hours(quota["temporary_extra_quota_hours"]),
        "effective_quota_hours": usage_hours(quota["effective_quota_hours"]),
        "active_temp_quotas": quota["active_temp_quotas"],
        "temp_quotas": quota["temp_quotas"],
        "retention_days": cfg.get("retention_days", GPU_ACCOUNTING_DEFAULT_RETENTION_DAYS),
        "sampling_interval_seconds": cfg.get("sampling_interval_seconds", GPU_ACCOUNTING_DEFAULT_SAMPLING_INTERVAL_SECONDS),
        "weekly_gpu_card_hours": usage_hours(weekly_total_hours),
        "weekly_low_efficiency_card_hours": usage_hours(weekly_low_efficiency_total_hours),
        **quota_status,
        "current_active_gpu_count": clamp_int(current_user.get("current_active_gpu_count", 0), 0, minimum=0),
        "current_low_efficiency_gpu_count": clamp_int(current_user.get("current_low_efficiency_gpu_count", 0), 0, minimum=0),
        "servers": servers,
        "containers_by_name": container_name_view,
        "last_sample_at": runtime.get("last_sample_at", ""),
        "last_success_at": runtime.get("last_success_at", ""),
        "server_errors": runtime.get("server_errors", {}),
    })

@app.route("/api/gpu-accounting/ranking", methods=["GET"])
@login_required
def api_gpu_accounting_ranking():
    data = load_data()
    days = clamp_day_window(request.args.get("days"), default=GPU_PORTAL_DEFAULT_DAYS, minimum=7, maximum=365)
    window_end = date_bucket_start(datetime.now()) + timedelta(days=1)
    window_start = window_end - timedelta(days=days)
    rows = query_gpu_accounting_ranking_by_user(window_start, window_end)
    return jsonify({
        "ok": True,
        "days": days,
        "window_start": window_start.date().isoformat(),
        "window_end": (window_end - timedelta(days=1)).date().isoformat(),
        "retention_days": ensure_gpu_accounting_defaults(data).get("retention_days", GPU_ACCOUNTING_DEFAULT_RETENTION_DAYS),
        "ranking": build_gpu_ranking_payload(data, rows),
    })

@app.route("/api/gpu-accounting/users/<path:username>/daily", methods=["GET"])
@login_required
def api_gpu_accounting_user_daily(username):
    normalized_username = str(username or "").strip()
    if not normalized_username:
        return jsonify({"error": "缺少用户标识"}), 400
    days = clamp_day_window(request.args.get("days"), default=GPU_PORTAL_DEFAULT_DAYS, minimum=7, maximum=365)
    window_end = date_bucket_start(datetime.now()) + timedelta(days=1)
    window_start = window_end - timedelta(days=days)
    rows = query_gpu_accounting_daily_usage_for_user(window_start, window_end, normalized_username)
    data = load_data()
    return jsonify({
        "ok": True,
        "days": days,
        "window_start": window_start.date().isoformat(),
        "window_end": (window_end - timedelta(days=1)).date().isoformat(),
        "retention_days": ensure_gpu_accounting_defaults(data).get("retention_days", GPU_ACCOUNTING_DEFAULT_RETENTION_DAYS),
        **build_gpu_daily_usage_payload(normalized_username, rows, days, window_start),
    })

@app.route("/api/gpu-accounting/users/<path:username>/portal-token", methods=["POST"])
@login_required
@role_required("admin", "allocator")
def api_gpu_accounting_portal_token(username):
    normalized_username = str(username or "").strip()
    if not normalized_username:
        return jsonify({"error": "缺少用户标识"}), 400
    with data_lock:
        data = load_data()
        token_info = ensure_gpu_portal_token(data, normalized_username, operator=session["user"], force_reset=False)
        append_audit(data, f"读取 GPU 门户访问链接 {normalized_username}")
        save_data(data)
    return jsonify({"ok": True, "token": token_info})

@app.route("/api/gpu-accounting/users/<path:username>/portal-token/reset", methods=["POST"])
@login_required
@role_required("admin", "allocator")
def api_gpu_accounting_portal_token_reset(username):
    normalized_username = str(username or "").strip()
    if not normalized_username:
        return jsonify({"error": "缺少用户标识"}), 400
    with data_lock:
        data = load_data()
        token_info = ensure_gpu_portal_token(data, normalized_username, operator=session["user"], force_reset=True)
        append_audit(data, f"重置 GPU 门户访问链接 {normalized_username}", "WARN")
        save_data(data)
    return jsonify({"ok": True, "token": token_info})

@app.route("/api/gpu-accounting/portal-links/export", methods=["GET"])
@login_required
@role_required("admin", "allocator")
def api_gpu_accounting_portal_links_export():
    with data_lock:
        data = load_data()
        ensure_gpu_accounting_defaults(data)
        window_end = date_bucket_start(datetime.now()) + timedelta(days=1)
        window_start = window_end - timedelta(days=GPU_PORTAL_DEFAULT_DAYS)
        summary_users = query_gpu_accounting_ranking_by_user(window_start, window_end)
        usernames = sorted({
            str(row.get("username") or "").strip()
            for row in summary_users
            if str(row.get("username") or "").strip()
        } | gpu_accounting_known_usernames(data) | set((find_gpu_portal_token_record(data) or {}).keys()))
        rows = []
        for username in usernames:
            token_info = ensure_gpu_portal_token(data, username, operator=session["user"], force_reset=False)
            rows.append({
                "username": username,
                "token": token_info.get("token", ""),
                "url": token_info.get("url", ""),
                "created_at": token_info.get("created_at", ""),
                "created_by": token_info.get("created_by", ""),
                "last_reset_at": token_info.get("last_reset_at", ""),
                "last_reset_by": token_info.get("last_reset_by", ""),
            })
        append_audit(data, f"导出 GPU 门户访问链接 CSV，共 {len(rows)} 条")
        save_data(data)
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["username", "token", "url", "created_at", "created_by", "last_reset_at", "last_reset_by"])
    for item in rows:
        writer.writerow([
            item["username"],
            item["token"],
            item["url"],
            item["created_at"],
            item["created_by"],
            item["last_reset_at"],
            item["last_reset_by"],
        ])
    csv_text = buffer.getvalue()
    response = Response(csv_text, mimetype="text/csv; charset=utf-8")
    response.headers["Content-Disposition"] = f"attachment; filename=gpu-portal-links-{datetime.now().strftime('%Y%m%d-%H%M%S')}.csv"
    return response

@app.route("/portal/<token>", methods=["GET"])
@portal_mode_required
def gpu_usage_portal(token):
    data = load_data()
    username, _ = get_gpu_portal_user_by_token(data, token)
    if not username:
        return "访问链接无效或已失效", 404
    cfg = ensure_gpu_accounting_defaults(data)
    return render_template_string(
        load_template("gpu_usage_portal.html"),
        portal_username=username,
        portal_token=token,
        retention_days=cfg.get("retention_days", GPU_ACCOUNTING_DEFAULT_RETENTION_DAYS),
        panel_version=PANEL_VERSION,
    )

@app.route("/portal-api/me/<token>", methods=["GET"])
@portal_mode_required
def api_gpu_usage_portal_me(token):
    data = load_data()
    username, _ = get_gpu_portal_user_by_token(data, token)
    if not username:
        return jsonify({"ok": False, "error": "访问链接无效或已失效"}), 404
    days = clamp_day_window(request.args.get("days"), default=GPU_PORTAL_DEFAULT_DAYS, minimum=7, maximum=365)
    payload = build_gpu_portal_me_payload(data, username, days)
    payload["ok"] = True
    payload["retention_days"] = ensure_gpu_accounting_defaults(data).get("retention_days", GPU_ACCOUNTING_DEFAULT_RETENTION_DAYS)
    payload["portal_url"] = build_gpu_accounting_portal_url(token)
    return jsonify(payload)

@app.route("/portal-api/ranking/<token>", methods=["GET"])
@portal_mode_required
def api_gpu_usage_portal_ranking(token):
    data = load_data()
    viewer_username, _ = get_gpu_portal_user_by_token(data, token)
    if not viewer_username:
        return jsonify({"ok": False, "error": "访问链接无效或已失效"}), 404
    days = clamp_day_window(request.args.get("days"), default=GPU_PORTAL_DEFAULT_DAYS, minimum=7, maximum=365)
    window_end = date_bucket_start(datetime.now()) + timedelta(days=1)
    window_start = window_end - timedelta(days=days)
    rows = query_gpu_accounting_ranking_by_user(window_start, window_end)
    ranking = build_gpu_ranking_payload(data, rows)
    masked_ranking = []
    for item in ranking:
        masked_item = dict(item)
        masked_item["display_name"] = mask_portal_username(item.get("username", ""), viewer_username)
        masked_item["is_self"] = item.get("username") == viewer_username
        masked_item.pop("username", None)
        masked_ranking.append(masked_item)
    return jsonify({
        "ok": True,
        "viewer_username": viewer_username,
        "days": days,
        "window_start": window_start.date().isoformat(),
        "window_end": (window_end - timedelta(days=1)).date().isoformat(),
        "retention_days": ensure_gpu_accounting_defaults(data).get("retention_days", GPU_ACCOUNTING_DEFAULT_RETENTION_DAYS),
        "ranking": masked_ranking,
    })

@app.route("/api/gpu-accounting/settings", methods=["PATCH"])
@login_required
@role_required("admin", "allocator")
def api_gpu_accounting_settings():
    body = request.json or {}
    with data_lock:
        data = load_data()
        cfg = ensure_gpu_accounting_defaults(data)
        cfg["default_weekly_quota_hours"] = clamp_int(
            body.get("default_weekly_quota_hours", cfg.get("default_weekly_quota_hours")),
            cfg.get("default_weekly_quota_hours", GPU_ACCOUNTING_DEFAULT_WEEKLY_QUOTA_HOURS),
            minimum=1,
            maximum=100000,
        )
        cfg["retention_days"] = clamp_int(
            body.get("retention_days", cfg.get("retention_days")),
            cfg.get("retention_days", GPU_ACCOUNTING_DEFAULT_RETENTION_DAYS),
            minimum=7,
            maximum=365,
        )
        append_audit(
            data,
            f"更新 GPU 计算时设置：默认额度 {cfg['default_weekly_quota_hours']}h/周，保留 {cfg['retention_days']} 天"
        )
        save_data(data)
    return jsonify({
        "ok": True,
        "default_weekly_quota_hours": cfg["default_weekly_quota_hours"],
        "retention_days": cfg["retention_days"],
        "sampling_interval_seconds": cfg.get("sampling_interval_seconds", GPU_ACCOUNTING_DEFAULT_SAMPLING_INTERVAL_SECONDS),
    })

@app.route("/api/gpu-accounting/users/<path:username>/base-quota", methods=["PATCH"])
@login_required
@role_required("admin", "allocator")
def api_gpu_accounting_set_base_quota(username):
    username = str(username or "").strip()
    if not username:
        return jsonify({"error": "缺少用户标识"}), 400
    body = request.json or {}
    raw_value = body.get("base_quota_hours")
    clear_override = raw_value in (None, "", 0, "0")
    with data_lock:
        data = load_data()
        if clear_override:
            set_gpu_base_quota_override(data, username, None)
            append_audit(data, f"清除 GPU 用户基础额度覆盖 {username}")
        else:
            base_quota_hours = clamp_int(
                raw_value,
                ensure_gpu_accounting_defaults(data)["default_weekly_quota_hours"],
                minimum=1,
                maximum=100000,
            )
            set_gpu_base_quota_override(data, username, base_quota_hours)
            append_audit(data, f"更新 GPU 用户基础额度 {username} 为 {base_quota_hours}h/周")
        save_data(data)
        quota = current_gpu_quota_snapshot(data, username, current_week_window()[0])
    return jsonify({
        "ok": True,
        "username": username,
        "base_quota_hours": quota["base_quota_hours"],
        "user_base_quota_hours": quota["user_base_quota_hours"],
        "temporary_extra_quota_hours": quota["temporary_extra_quota_hours"],
        "effective_quota_hours": quota["effective_quota_hours"],
    })

@app.route("/api/gpu-accounting/users/<path:username>/temp-quotas", methods=["POST"])
@login_required
@role_required("admin", "allocator")
def api_gpu_accounting_add_temp_quota(username):
    username = str(username or "").strip()
    if not username:
        return jsonify({"error": "缺少用户标识"}), 400
    body = request.json or {}
    extra_hours_per_week = clamp_int(body.get("extra_hours_per_week"), 0, minimum=1, maximum=100000)
    effective_weeks = clamp_int(body.get("effective_weeks"), 1, minimum=1, maximum=52)
    note = str(body.get("note") or "").strip()
    week_start, _ = current_week_window()
    record = {
        "id": f"tmp_{uuid.uuid4().hex[:12]}",
        "username": username,
        "extra_hours_per_week": extra_hours_per_week,
        "effective_weeks": effective_weeks,
        "start_week": iso_seconds(week_start),
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "created_by": session["user"],
        "note": note,
    }
    with data_lock:
        data = load_data()
        cfg = ensure_gpu_accounting_defaults(data)
        cfg.setdefault("temp_quotas", []).append(record)
        append_audit(
            data,
            f"新增 GPU 临时额度 {username} +{extra_hours_per_week}h/周，持续 {effective_weeks} 周"
            + (f"，备注：{note}" if note else "")
        )
        save_data(data)
    return jsonify({
        "ok": True,
        "record": build_gpu_temp_quota_view(record, week_start),
    })

@app.route("/api/gpu-accounting/temp-quotas/<quota_id>", methods=["DELETE"])
@login_required
@role_required("admin", "allocator")
def api_gpu_accounting_delete_temp_quota(quota_id):
    quota_id = str(quota_id or "").strip()
    if not quota_id:
        return jsonify({"error": "缺少临时额度标识"}), 400
    with data_lock:
        data = load_data()
        cfg = ensure_gpu_accounting_defaults(data)
        temp_quotas = cfg.setdefault("temp_quotas", [])
        removed = None
        remaining = []
        for record in temp_quotas:
            if removed is None and str(record.get("id") or "").strip() == quota_id:
                removed = record
                continue
            remaining.append(record)
        if removed is None:
            return jsonify({"error": "临时额度记录不存在"}), 404
        cfg["temp_quotas"] = remaining
        append_audit(
            data,
            f"删除 GPU 临时额度 {removed.get('username', '')} +{removed.get('extra_hours_per_week', 0)}h/周"
        )
        save_data(data)
    return jsonify({"ok": True, "username": str(removed.get("username") or "").strip()})

@app.route("/api/gpu-accounting/temp-quotas/<quota_id>/reset", methods=["POST"])
@login_required
@role_required("admin", "allocator")
def api_gpu_accounting_reset_temp_quota(quota_id):
    quota_id = str(quota_id or "").strip()
    if not quota_id:
        return jsonify({"error": "缺少临时额度标识"}), 400
    week_start, _ = current_week_window()
    with data_lock:
        data = load_data()
        cfg = ensure_gpu_accounting_defaults(data)
        target = None
        for record in cfg.setdefault("temp_quotas", []):
            if str(record.get("id") or "").strip() != quota_id:
                continue
            target = record
            break
        if target is None:
            return jsonify({"error": "临时额度记录不存在"}), 404
        view = build_gpu_temp_quota_view(target, week_start)
        if not view.get("active"):
            return jsonify({"error": "当前临时额度未处于生效状态，无需重置"}), 400
        target["deactivated_at"] = datetime.now().isoformat(timespec="seconds")
        target["deactivated_by"] = session["user"]
        append_audit(
            data,
            f"重置 GPU 临时额度 {target.get('username', '')} +{target.get('extra_hours_per_week', 0)}h/周"
        )
        save_data(data)
        view = build_gpu_temp_quota_view(target, week_start)
    return jsonify({"ok": True, "record": view, "username": str(target.get("username") or "").strip()})

@app.route("/api/gpu-accounting/users/<path:username>/temp-quotas/reset", methods=["POST"])
@login_required
@role_required("admin", "allocator")
def api_gpu_accounting_reset_user_temp_quotas(username):
    username = str(username or "").strip()
    if not username:
        return jsonify({"error": "缺少用户标识"}), 400
    week_start, _ = current_week_window()
    reset_count = 0
    with data_lock:
        data = load_data()
        cfg = ensure_gpu_accounting_defaults(data)
        for record in cfg.setdefault("temp_quotas", []):
            if str(record.get("username") or "").strip() != username:
                continue
            view = build_gpu_temp_quota_view(record, week_start)
            if not view.get("active"):
                continue
            record["deactivated_at"] = datetime.now().isoformat(timespec="seconds")
            record["deactivated_by"] = session["user"]
            reset_count += 1
        if reset_count <= 0:
            return jsonify({"error": "当前用户没有生效中的临时额度，无需重置"}), 400
        append_audit(data, f"重置用户 GPU 临时额度 {username}，共 {reset_count} 条")
        save_data(data)
    return jsonify({"ok": True, "username": username, "reset_count": reset_count})

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
    runtime_options = normalize_runtime_options(body.get("runtime_options", {}))
    extra_port_count = normalize_extra_port_count(body.get("extra_port_count", DEFAULT_EXTRA_PORT_COUNT))
    extra_port_bindings = normalize_extra_port_bindings(body.get("extra_port_bindings", []), extra_port_count=extra_port_count)
    if runtime_options.get("network_mode") != "bridge":
        return jsonify({"error": "当前版本暂不支持对 SSH 登录型容器启用非 bridge 网络模式"}), 400
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
    if runtime_options.get("network_mode") == "host":
        extra_port_count = 0
        extra_port_bindings = []
    else:
        extra_port_bindings = fill_missing_extra_host_ports(server, data["containers"].values(), extra_port_bindings)
        if len(extra_port_bindings) != extra_port_count:
            return jsonify({"error": f"{EXTRA_PORT_MIN}-{EXTRA_PORT_MAX} 范围内没有足够的可用业务端口"}), 400

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
        "runtime_options": runtime_options,
        "login_user": login_user,
        "ssh_public_key": body.get("ssh_public_key", ""),
        "password_access": bool(body.get("password_access", False)),
        "ssh_password": body.get("ssh_password", ""),
        "allow_sudo": bool(body.get("allow_sudo", True)),
        "assigned_to": assigned_to,
        "mounts": mounts,
        "allowed_mount_roots": server.get("mount_roots", []),
        "extra_port_bindings": extra_port_bindings,
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
        "runtime_options": runtime_options,
        "extra_port_bindings": extra_port_bindings,
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
                        "copy_text": record.get("connection_copy_text", ""),
                        "port_rules": record.get("port_rules", []),
                        "extra_port_bindings": record.get("extra_port_bindings", []),
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
        resolved_runtime_options = normalize_runtime_options(agent_result.get("runtime_options", runtime_options))
        resolved_extra_port_bindings = normalize_extra_port_bindings(
            agent_result.get("extra_port_bindings", extra_port_bindings),
            extra_port_count=len(agent_result.get("extra_port_bindings", extra_port_bindings) or []),
        )
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
            runtime_options=resolved_runtime_options,
            extra_port_bindings=resolved_extra_port_bindings,
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
        "copy_text": record.get("connection_copy_text", ""),
        "port_rules": record.get("port_rules", []),
        "extra_port_bindings": record.get("extra_port_bindings", []),
        "name": record.get("name", name),
        "ssh_port": record.get("ssh_port"),
        "runtime_options": record.get("runtime_options", {}),
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

    requested_extra_port_bindings = normalize_extra_port_bindings(
        body.get("extra_port_bindings", container.get("extra_port_bindings", [])),
        extra_port_count=len(body.get("extra_port_bindings", container.get("extra_port_bindings", [])) or []),
    )
    payload = {
        "cpu_limit": body.get("cpu_limit", container.get("cpu_limit")),
        "mem_limit": body.get("mem_limit", container.get("mem_limit")),
        "pids_limit": body.get("pids_limit", container.get("pids_limit", 512)),
        "gpu_enabled": bool(body.get("gpu_enabled", container.get("gpu_enabled", False))),
        "gpu_devices": body.get("gpu_devices", container.get("gpu_devices", [])),
        "gpu_mode": body.get("gpu_mode", container.get("gpu_mode", "shared") or "shared"),
        "rootfs_limit": str(body.get("rootfs_limit", container.get("rootfs_limit", "")) or "").strip(),
        "runtime_options": normalize_runtime_options(body.get("runtime_options", container.get("runtime_options", {}))),
        "extra_port_bindings": requested_extra_port_bindings,
        "source_type": body.get("source_type", "temporary_snapshot"),
        "image_ref": body.get("image_ref", ""),
    }
    payload["extra_port_bindings"] = fill_missing_extra_host_ports(
        server,
        [record for record in data["containers"].values() if record is not container],
        payload["extra_port_bindings"],
    )
    if len(payload["extra_port_bindings"]) != len(requested_extra_port_bindings):
        return jsonify({"error": f"{EXTRA_PORT_MIN}-{EXTRA_PORT_MAX} 范围内没有足够的可用业务端口"}), 400
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
        container["runtime_options"] = normalize_runtime_options(result.get("runtime_options", payload["runtime_options"]))
        container["extra_port_bindings"] = normalize_extra_port_bindings(
            result.get("extra_port_bindings", payload["extra_port_bindings"]),
            extra_port_count=len(result.get("extra_port_bindings", payload["extra_port_bindings"]) or []),
        )
        container["password_file"] = ""
        container["status"] = result.get("status", "running")
        container["agent_container_id"] = result.get("container_id", container.get("agent_container_id", ""))
        bundle = build_connection_bundle(
            container.get("login_user", "dockeruser"),
            result.get("ssh_port", container.get("ssh_port")),
            server.get("ssh_host") or server.get("host", "server-host"),
            container.get("extra_port_bindings", []),
        )
        if result.get("ssh_port"):
            container["ssh_port"] = result.get("ssh_port")
        container["ssh_cmd"] = bundle["ssh_cmd"]
        container["connection_copy_text"] = bundle["copy_text"]
        container["port_rules"] = bundle["port_rules"]
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
    logs = [
        normalize_audit_log_entry(item)
        for item in data.get("audit_logs", [])[-AUDIT_LOG_LIMIT:]
    ]
    return jsonify({"logs": logs})

# ── 当前用户信息 ───────────────────────────────────────────────────────────
@app.route("/api/me")
@login_required
def api_me():
    return jsonify({"user": session["user"], "role": session["role"]})

@app.route("/api/config/export")
@login_required
@role_required("admin", "allocator")
def api_config_export():
    include_tokens = request.args.get("include_tokens") == "1"
    include_users = can_manage_users()
    with data_lock:
        data = load_data()
        export_scope = "完整" if include_tokens else "普通"
        if not include_users:
            export_scope += "（不含用户）"
        append_audit(data, f"导出{export_scope}配置", "WARN" if include_tokens else "INFO")
        save_data(data)
    payload = {
        "version": 1,
        "exported_at": datetime.now().isoformat(),
        "users": data.get("users", {}) if include_users else {},
        "servers": json.loads(json.dumps(data.get("servers", {}))),
        "containers": data.get("containers", {}),
        "templates": data.get("templates", []),
        "audit_logs": data.get("audit_logs", []),
        "gpu_accounting": data.get("gpu_accounting", {}),
        "contains_tokens": include_tokens,
        "contains_users": include_users,
    }
    if not include_tokens:
        for server in payload["servers"].values():
            server["agent_token"] = ""
    return jsonify(payload)

@app.route("/api/config/import", methods=["POST"])
@login_required
@role_required("admin", "allocator")
def api_config_import():
    payload = request.json or {}
    if payload.get("version") != 1:
        return jsonify({"error": "不支持的配置版本"}), 400
    include_users = can_manage_users()
    users_ignored = not include_users and "users" in payload
    with data_lock:
        data = load_data()
        allowed_keys = ("servers", "containers", "templates", "audit_logs", "gpu_accounting")
        if include_users:
            allowed_keys = ("users",) + allowed_keys
        for key in allowed_keys:
            if key in payload:
                data[key] = payload[key]
        ensure_gpu_accounting_defaults(data)
        message = "导入配置并覆盖当前面板记录"
        if users_ignored:
            message += "（已忽略用户数据）"
        append_audit(data, message, "WARN")
        save_data(data)
    return jsonify({"ok": True, "users_ignored": users_ignored})

if APP_MODE == "panel":
    ensure_gpu_accounting_worker_started()

if __name__ == "__main__":
    debug = os.environ.get("DEBUG", "0") == "1"
    default_port = "5000" if APP_MODE == "panel" else "5002"
    port = int(os.environ.get("PANEL_PORT" if APP_MODE == "panel" else "GPU_PORTAL_PORT", default_port))
    app.run(debug=debug, host="0.0.0.0", port=port, threaded=True)
