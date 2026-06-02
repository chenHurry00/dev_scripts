#!/usr/bin/env bash
# DockerHub Manager - 中心面板安装与管理工具
set -euo pipefail

SOURCE_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PANEL_DIR="/opt/.dockerhub-panel"
AGENT_DIR="/opt/.dockerhub-agent"
CONFIG_DIR="/etc/dockerhub-manager"
PANEL_ENV="${CONFIG_DIR}/panel.env"
AGENT_ENV="${CONFIG_DIR}/local-agent.env"
PANEL_SERVICE="/etc/systemd/system/dockerhub-panel.service"
GPU_PORTAL_SERVICE="/etc/systemd/system/dockerhub-gpu-portal.service"
AGENT_SERVICE="/etc/systemd/system/dockerhub-agent.service"
PANEL_USER="dockerhub-panel"

if [[ "$EUID" -eq 0 ]]; then
  SUDO=()
else
  SUDO=(sudo)
fi

run_root() {
  "${SUDO[@]}" "$@"
}

read_env_value() {
  local file="$1"
  local key="$2"
  if [[ -r "$file" ]]; then
    sed -n "s/^${key}=//p" "$file" | tail -n 1
  elif [[ -e "$file" && ${#SUDO[@]} -gt 0 ]]; then
    run_root sed -n "s/^${key}=//p" "$file" | tail -n 1
  fi
}

ask_yes_no() {
  local prompt="$1"
  local default="${2:-n}"
  local answer
  if [[ "$default" == "y" ]]; then
    read -r -p "${prompt} [Y/n]: " answer
    answer="${answer:-y}"
  else
    read -r -p "${prompt} [y/N]: " answer
    answer="${answer:-n}"
  fi
  [[ "$answer" =~ ^([Yy]|[Yy][Ee][Ss])$ ]]
}

run_local_agent_capability_check() {
  local agent_port="$1"
  local agent_token="$2"
  AGENT_PORT="$agent_port" AGENT_TOKEN="$agent_token" python3 - <<'PY'
import json
import os
import sys
import urllib.request

port = os.environ['AGENT_PORT']
token = os.environ['AGENT_TOKEN']
base = f'http://127.0.0.1:{port}'

def fetch(path):
    req = urllib.request.Request(base + path, headers={'X-Agent-Token': token})
    with urllib.request.urlopen(req, timeout=8) as resp:
        return json.loads(resp.read().decode('utf-8'))

try:
    checks = fetch('/checks')
    gpu = fetch('/gpu/info')
except Exception as exc:
    print(f'AGENT_CAPABILITY_ERROR: {exc}')
    sys.exit(2)

storage = checks.get('storage') or {}
driver = storage.get('driver') or 'unknown'
backing = storage.get('backing_filesystem') or 'unknown'
quota = ','.join(storage.get('quota_flags') or []) or 'none'

print('Agent 能力摘要：')
print(f'  - Docker 存储驱动: {driver}')
if storage.get('supports_rootfs_limit'):
    print(f'  - 可写层磁盘限额: 支持（backing fs: {backing}, quota: {quota}）')
else:
    reason = storage.get('reason') or '当前环境不支持'
    print(f'  - 可写层磁盘限额: 不支持（{reason}）')

devices = gpu.get('devices') or []
if gpu.get('supported'):
    print(f'  - GPU 容器支持: 支持（检测到 {len(devices)} 张 GPU）')
    if devices:
        names = ', '.join(f"{item.get('index', '?')}:{item.get('name', 'unknown')}" for item in devices)
        print(f'  - GPU 列表: {names}')
    print('AGENT_GPU_STATUS: supported')
else:
    print('  - GPU 容器支持: 未就绪')
    missing = gpu.get('missing') or []
    suggestions = gpu.get('suggestions') or []
    if missing:
        print('  - 缺失项:')
        for item in missing:
            print(f'    * {item}')
    if suggestions:
        print('  - 建议:')
        for item in suggestions:
            print(f'    * {item}')
    print('  - 后续动作: 继续安装 Agent / 退出脚本')
    print('AGENT_GPU_STATUS: missing')
PY
}

show_local_agent_capability_summary() {
  local agent_port="$1"
  local agent_token="$2"
  local exit_on_gpu_missing="${3:-0}"

  echo ""
  echo "检查 Agent 能力..."
  local capability_output
  capability_output="$(run_local_agent_capability_check "$agent_port" "$agent_token" 2>&1 || true)"
  echo "$capability_output" | sed '/^AGENT_GPU_STATUS:/d'

  if echo "$capability_output" | grep -q '^AGENT_CAPABILITY_ERROR:'; then
    echo "⚠ Agent 能力检查未完成，但 Agent 已启动。"
    return 0
  fi

  if echo "$capability_output" | grep -q 'AGENT_GPU_STATUS: missing'; then
    echo ""
    echo "⚠ 检测到当前服务器未满足 GPU 容器运行条件。"
    echo "  Agent 已完成启动，但 GPU 功能暂不可用。"
    if [[ "$exit_on_gpu_missing" == "1" ]]; then
      if [[ -t 0 ]]; then
        local continue_install
        read -r -p "是否继续完成当前 Agent 安装并稍后手动处理 GPU？输入 continue 继续，其他退出: " continue_install
        if [[ "$continue_install" != "continue" ]]; then
          echo "已退出。当前 Agent 文件和服务已部署，如不需要可执行："
          echo "  sudo bash ${AGENT_DIR}/uninstall.sh"
          exit 35
        fi
      else
        echo "  当前为非交互模式，默认退出。"
        exit 35
      fi
    fi
  fi
}

random_secret() {
  if ! command -v openssl >/dev/null 2>&1; then
    echo "错误：未检测到 openssl，无法生成随机密钥。"
    exit 10
  fi
  openssl rand -hex 32
}

confirm_action() {
  local operation="$1"
  local scope="$2"
  local risk="$3"
  local answer
  echo "⚠️ 危险操作检测！"
  echo "操作类型：${operation}"
  echo "影响范围：${scope}"
  echo "风险评估：${risk}"
  echo ""
  read -r -p "Continue? [y/N]: " answer
  case "$answer" in
    y|Y|yes|YES|Yes) ;;
    *)
      echo "操作已取消。"
      return 1
      ;;
  esac
}

python_venv_available() {
  local probe_dir
  probe_dir="$(mktemp -d /tmp/dockerhub-panel-venv.XXXXXX)"
  if python3 -m venv "$probe_dir" >/dev/null 2>&1 && [[ -x "$probe_dir/bin/pip" ]]; then
    rm -rf "$probe_dir"
    return 0
  fi
  rm -rf "$probe_dir"
  return 1
}

ensure_python_venv() {
  local python_version
  if ! command -v python3 >/dev/null 2>&1; then
    echo "错误：未检测到 python3。请先安装 Python 3。"
    exit 20
  fi

  if ! python_venv_available; then
    if ! command -v apt-get >/dev/null 2>&1; then
      echo "错误：当前 Python 无法创建包含 pip 的虚拟环境，且未检测到 apt-get。"
      echo "请手动安装当前 Python 对应的 venv 软件包后重试。"
      exit 21
    fi
    echo "未检测到可用的 Python venv，准备安装系统依赖。"
    python_version="$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
    run_root apt-get update -qq
    if ! run_root env DEBIAN_FRONTEND=noninteractive apt-get install -y python3-venv -qq; then
      echo "警告：安装 python3-venv 元包失败。"
    fi
    if ! python_venv_available; then
      echo "尝试安装当前解释器对应的软件包：python${python_version}-venv"
      run_root env DEBIAN_FRONTEND=noninteractive apt-get install -y "python${python_version}-venv" -qq
    fi
    if ! python_venv_available; then
      echo "错误：安装 venv 依赖后仍无法创建 Python 虚拟环境。"
      echo "请检查 Python 安装状态后重试。"
      exit 22
    fi
  fi
}

ensure_panel_user() {
  if ! id "$PANEL_USER" >/dev/null 2>&1; then
    run_root useradd --system --home-dir "$PANEL_DIR" --shell /usr/sbin/nologin "$PANEL_USER"
  fi
}

install_panel_files() {
  ensure_python_venv
  ensure_panel_user

  run_root mkdir -p "$PANEL_DIR/templates" "$CONFIG_DIR"
  run_root install -m 0644 "$SOURCE_DIR/app.py" "$PANEL_DIR/app.py"
  run_root install -m 0644 "$SOURCE_DIR/requirements.txt" "$PANEL_DIR/requirements.txt"
  run_root install -m 0644 "$SOURCE_DIR/templates/login.html" "$PANEL_DIR/templates/login.html"
  run_root install -m 0644 "$SOURCE_DIR/templates/dashboard.html" "$PANEL_DIR/templates/dashboard.html"
  run_root install -m 0644 "$SOURCE_DIR/templates/gpu_usage_portal.html" "$PANEL_DIR/templates/gpu_usage_portal.html"

  if [[ ! -x "$PANEL_DIR/.venv/bin/python" || ! -x "$PANEL_DIR/.venv/bin/pip" ]]; then
    run_root rm -rf "$PANEL_DIR/.venv"
    run_root python3 -m venv "$PANEL_DIR/.venv"
  fi
  run_root "$PANEL_DIR/.venv/bin/pip" install -q -r "$PANEL_DIR/requirements.txt"
  run_root chown -R "$PANEL_USER:$PANEL_USER" "$PANEL_DIR"
}

configure_panel_env() {
  local current_port
  local current_secret
  local current_password
  local current_password_b64
  local panel_port
  local gpu_portal_port
  local secret_key
  local admin_password
  local admin_password_b64
  local admin_password_confirm

  current_port="$(read_env_value "$PANEL_ENV" PANEL_PORT || true)"
  current_secret="$(read_env_value "$PANEL_ENV" SECRET_KEY || true)"
  current_password="$(read_env_value "$PANEL_ENV" ADMIN_PASSWORD || true)"
  current_password_b64="$(read_env_value "$PANEL_ENV" ADMIN_PASSWORD_B64 || true)"
  gpu_portal_port="$(read_env_value "$PANEL_ENV" GPU_PORTAL_PORT || true)"

  read -r -p "中心面板端口 [${current_port:-5000}]: " panel_port
  panel_port="${panel_port:-${current_port:-5000}}"
  if [[ ! "$panel_port" =~ ^[0-9]+$ ]] || (( panel_port < 1 || panel_port > 65535 )); then
    echo "错误：面板端口无效。"
    exit 21
  fi
  read -r -p "GPU 对外门户端口 [${gpu_portal_port:-5002}]: " gpu_portal_port
  gpu_portal_port="${gpu_portal_port:-5002}"
  if [[ ! "$gpu_portal_port" =~ ^[0-9]+$ ]] || (( gpu_portal_port < 1 || gpu_portal_port > 65535 )); then
    echo "错误：GPU 门户端口无效。"
    exit 21
  fi

  secret_key="${current_secret:-$(random_secret)}"
  if [[ -f "$PANEL_DIR/data.json" ]]; then
    admin_password_b64="${current_password_b64:-$(printf '%s' "${current_password:-unused-after-data-initialized}" | base64 | tr -d '\n')}"
    echo "检测到现有 data.json，将保留当前 admin 密码。"
  else
    while true; do
      read -r -s -p "请设置 admin 管理员密码（至少 8 位）: " admin_password
      echo ""
      if [[ ${#admin_password} -lt 8 ]]; then
        echo "错误：admin 密码至少需要 8 位。"
        continue
      fi
      read -r -s -p "请再次输入 admin 管理员密码: " admin_password_confirm
      echo ""
      if [[ "$admin_password" != "$admin_password_confirm" ]]; then
        echo "错误：两次输入的密码不一致。"
        continue
      fi
      break
    done
    admin_password_b64="$(printf '%s' "$admin_password" | base64 | tr -d '\n')"
  fi

  run_root mkdir -p "$CONFIG_DIR"
  run_root tee "$PANEL_ENV" >/dev/null <<EOF
SECRET_KEY=${secret_key}
ADMIN_PASSWORD_B64=${admin_password_b64}
PANEL_PORT=${panel_port}
GPU_PORTAL_PORT=${gpu_portal_port}
DEBUG=0
EOF
  run_root chmod 0600 "$PANEL_ENV"

  echo ""
  echo "中心面板配置："
  echo "  监听端口: ${panel_port}"
  echo "  GPU 门户端口: ${gpu_portal_port}"
  echo "  管理员账号: admin"
  if [[ ! -f "$PANEL_DIR/data.json" ]]; then
    echo "  管理员密码: 已按输入值设置"
  else
    echo "  管理员密码: 已沿用现有 data.json 配置"
  fi
}

write_panel_service() {
  local panel_port
  local gpu_portal_port
  panel_port="$(read_env_value "$PANEL_ENV" PANEL_PORT || true)"
  gpu_portal_port="$(read_env_value "$PANEL_ENV" GPU_PORTAL_PORT || true)"
  run_root tee "$PANEL_SERVICE" >/dev/null <<EOF
[Unit]
Description=DockerHub Manager Panel
After=network.target

[Service]
Type=simple
User=${PANEL_USER}
Group=${PANEL_USER}
WorkingDirectory=${PANEL_DIR}
EnvironmentFile=${PANEL_ENV}
ExecStart=${PANEL_DIR}/.venv/bin/gunicorn --bind 0.0.0.0:${panel_port:-5000} --worker-class gthread --workers 1 --threads 4 --timeout 0 app:app
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
  run_root tee "$GPU_PORTAL_SERVICE" >/dev/null <<EOF
[Unit]
Description=DockerHub Manager GPU Portal
After=network.target

[Service]
Type=simple
User=${PANEL_USER}
Group=${PANEL_USER}
WorkingDirectory=${PANEL_DIR}
EnvironmentFile=${PANEL_ENV}
Environment=APP_MODE=portal
ExecStart=${PANEL_DIR}/.venv/bin/gunicorn --bind 0.0.0.0:${gpu_portal_port:-5002} --worker-class gthread --workers 1 --threads 4 --timeout 0 app:app
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
  run_root systemctl daemon-reload
  run_root systemctl enable dockerhub-panel
  run_root systemctl enable dockerhub-gpu-portal
  run_root systemctl restart dockerhub-panel
  run_root systemctl restart dockerhub-gpu-portal
}

show_panel_firewall_hint() {
  local panel_port
  local gpu_portal_port
  panel_port="$(read_env_value "$PANEL_ENV" PANEL_PORT || true)"
  gpu_portal_port="$(read_env_value "$PANEL_ENV" GPU_PORTAL_PORT || true)"
  echo ""
  echo "面板端口提醒："
  echo "  - 请在 1Panel、系统防火墙或云安全组中放行 TCP ${panel_port:-5000}"
  echo "  - 来源建议限制为局域网管理员网段"
  echo "  - 局域网管理员访问：http://<中心服务器IP>:${panel_port:-5000}"
  echo "  - GPU 门户可选端口：http://<中心服务器IP>:${gpu_portal_port:-5002}/portal/<token>"
}

install_or_update_panel() {
  local existing_panel_config="0"
  [[ -f "$PANEL_ENV" ]] && existing_panel_config="1"

  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo "  安装/更新 DockerHub 中心面板"
  echo "  工作目录: ${PANEL_DIR}"
  echo "  配置目录: ${CONFIG_DIR}"
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  confirm_action \
    "安装/更新中心面板并注册 systemd 服务" \
    "${PANEL_DIR}、${CONFIG_DIR}、dockerhub-panel.service" \
    "会安装 Python 依赖并重启中心面板；已有 data.json 将保留。" || return 0
  install_panel_files
  if [[ "$existing_panel_config" == "1" ]]; then
    if ask_yes_no "Existing panel configuration detected. Modify it?" "n"; then
      configure_panel_env
    else
      echo "✓ Existing panel configuration preserved: ${PANEL_ENV}"
    fi
  else
    configure_panel_env
  fi
  write_panel_service
  echo ""
  echo "✓ 中心面板服务已启动"
  show_panel_firewall_hint

  if command -v docker >/dev/null 2>&1; then
    if [[ -f "$AGENT_SERVICE" ]]; then
      if ask_yes_no "Local Docker Agent detected. Update it together?" "n"; then
        install_local_agent
      fi
    elif ask_yes_no "Local Docker detected. Install local Docker management Agent?" "n"; then
      install_local_agent
    fi
  else
    echo ""
    echo "提示：本机未检测到 Docker，跳过本机 Agent。中心面板仍可管理其他服务器。"
  fi
}

install_local_agent() {
  local current_port
  local current_token
  local agent_port
  local agent_token
  local existing_agent_config="0"

  confirm_action \
    "安装/更新本机 Docker Agent 并注册 root systemd 服务" \
    "${AGENT_DIR}、${AGENT_ENV}、dockerhub-agent.service" \
    "会重启本机 Agent；不会安装、重启或修改 Docker daemon，也不会操作已有业务容器。" || return 0

  if ! command -v docker >/dev/null 2>&1; then
    echo "错误：本机未检测到 Docker。为避免影响已有环境，脚本不会自动安装 Docker。"
    exit 30
  fi
  if ! timeout 10 "${SUDO[@]}" docker info >/dev/null 2>&1; then
    echo "错误：docker info 超时或失败。请检查 Docker daemon。"
    exit 31
  fi
  if [[ ! -x "$PANEL_DIR/.venv/bin/python" ]]; then
    echo "错误：请先安装中心面板，再部署本机 Agent。"
    exit 32
  fi

  current_port="$(read_env_value "$AGENT_ENV" AGENT_PORT || true)"
  current_token="$(read_env_value "$AGENT_ENV" AGENT_TOKEN || true)"
  [[ -f "$AGENT_ENV" ]] && existing_agent_config="1"
  if [[ "$existing_agent_config" == "1" ]] && ! ask_yes_no "Existing local Agent configuration detected. Modify it?" "n"; then
    agent_port="${current_port:-5001}"
    agent_token="$current_token"
    echo "✓ Existing local Agent configuration preserved: ${AGENT_ENV}"
  else
    read -r -p "本机 Agent 端口 [${current_port:-5001}]: " agent_port
    agent_port="${agent_port:-${current_port:-5001}}"
    agent_token="${current_token:-$(random_secret)}"
  fi
  if [[ ! "$agent_port" =~ ^[0-9]+$ ]] || (( agent_port < 1 || agent_port > 65535 )); then
    echo "错误：Agent 端口无效。"
    exit 33
  fi
  agent_token="${agent_token:-$(random_secret)}"

  run_root mkdir -p "$AGENT_DIR" "$CONFIG_DIR"
  run_root install -m 0755 "$SOURCE_DIR/agent/agent.py" "$AGENT_DIR/agent.py"
  run_root install -m 0755 "$SOURCE_DIR/agent/uninstall.sh" "$AGENT_DIR/uninstall.sh"
  run_root tee "$AGENT_ENV" >/dev/null <<EOF
AGENT_TOKEN=${agent_token}
AGENT_PORT=${agent_port}
EOF
  run_root chmod 0600 "$AGENT_ENV"

  run_root tee "$AGENT_SERVICE" >/dev/null <<EOF
[Unit]
Description=DockerHub Manager Agent
After=network.target docker.service

[Service]
Type=simple
WorkingDirectory=${AGENT_DIR}
EnvironmentFile=${AGENT_ENV}
ExecStart=${PANEL_DIR}/.venv/bin/python ${AGENT_DIR}/agent.py --port ${agent_port}
Restart=always
RestartSec=5
User=root

[Install]
WantedBy=multi-user.target
EOF
  run_root systemctl daemon-reload
  run_root systemctl enable dockerhub-agent
  run_root systemctl restart dockerhub-agent
  sleep 2

  echo ""
  if curl --noproxy '*' --connect-timeout 3 --max-time 5 -fsS "http://127.0.0.1:${agent_port}/ping"; then
    echo ""
    echo "✓ 本机 Agent 已启动"
  else
    echo "错误：本机 Agent 未通过连通性检查。"
    run_root systemctl status dockerhub-agent --no-pager -l || true
    exit 34
  fi
  show_local_agent_capability_summary "$agent_port" "$agent_token" 1

  echo ""
  echo "请在中心面板中添加本机服务器："
  echo "  主机 IP / 域名: 127.0.0.1"
  echo "  SSH 显示地址: <中心服务器局域网 IP>"
  echo "  Agent 端口: ${agent_port}"
  echo "  Agent Token: ${agent_token}"
  echo ""
  echo "端口提醒："
  echo "  - 面板通过 127.0.0.1 访问本机 Agent，无需为 Agent API 对外放行 ${agent_port}"
  echo "  - 如需为使用者创建容器，请放行 TCP 32000-32999"
}

panel_status() {
  run_root systemctl status dockerhub-panel --no-pager -l
  echo ""
  run_root systemctl status dockerhub-gpu-portal --no-pager -l || true
}

panel_logs() {
  run_root journalctl -u dockerhub-panel -n 100 --no-pager
  echo ""
  run_root journalctl -u dockerhub-gpu-portal -n 100 --no-pager || true
}

panel_restart() {
  run_root systemctl restart dockerhub-panel
  run_root systemctl restart dockerhub-gpu-portal
  echo "✓ 中心面板已重启"
  show_panel_firewall_hint
}

agent_status() {
  run_root systemctl status dockerhub-agent --no-pager -l
  local agent_port
  local agent_token
  agent_port="$(read_env_value "$AGENT_ENV" AGENT_PORT || true)"
  agent_token="$(read_env_value "$AGENT_ENV" AGENT_TOKEN || true)"
  if [[ -n "$agent_port" && -n "$agent_token" ]]; then
    show_local_agent_capability_summary "$agent_port" "$agent_token" 0
  else
    echo ""
    echo "提示：未检测到本机 Agent 配置文件，跳过能力检查。"
  fi
}

agent_logs() {
  run_root journalctl -u dockerhub-agent -n 100 --no-pager
}

agent_restart() {
  local agent_port
  local agent_token
  agent_port="$(read_env_value "$AGENT_ENV" AGENT_PORT || true)"
  agent_token="$(read_env_value "$AGENT_ENV" AGENT_TOKEN || true)"
  run_root systemctl restart dockerhub-agent
  sleep 2
  echo "✓ 本机 Agent 已重启"
  if [[ -n "$agent_port" && -n "$agent_token" ]]; then
    if ! curl --noproxy '*' --connect-timeout 3 --max-time 5 -fsS "http://127.0.0.1:${agent_port}/ping" >/dev/null; then
      echo "错误：本机 Agent 重启后未通过连通性检查。"
      run_root systemctl status dockerhub-agent --no-pager -l || true
      exit 36
    fi
    show_local_agent_capability_summary "$agent_port" "$agent_token" 0
  else
    echo "提示：未检测到本机 Agent 配置文件，跳过能力检查。"
  fi
}

disable_panel() {
  confirm_action \
    "停止中心面板并取消开机启动" \
    "dockerhub-panel.service、dockerhub-gpu-portal.service" \
    "管理网页将不可访问；已有数据目录和配置文件会保留。" || return 0
  run_root systemctl disable --now dockerhub-panel
  run_root systemctl disable --now dockerhub-gpu-portal || true
  echo "✓ 中心面板已停止并取消开机启动"
  echo "  已保留数据目录: ${PANEL_DIR}"
  echo "  已保留配置文件: ${PANEL_ENV}"
}

backup_panel_data() {
  local backup_dir="/var/backups/dockerhub-manager"
  local backup_file="${backup_dir}/panel-backup-$(date +%Y%m%d-%H%M%S).tar.gz"
  local has_data="0"
  local has_env="0"

  [[ -f "$PANEL_DIR/data.json" ]] && has_data="1"
  [[ -f "$PANEL_ENV" ]] && has_env="1"
  if [[ "$has_data" != "1" && "$has_env" != "1" ]]; then
    echo "提示：未检测到可备份的面板数据或配置。"
    return 0
  fi

  run_root mkdir -p "$backup_dir"
  if [[ "$has_data" == "1" && "$has_env" == "1" ]]; then
    run_root tar -czf "$backup_file" \
      -C "$PANEL_DIR" data.json \
      -C "$CONFIG_DIR" panel.env
  elif [[ "$has_data" == "1" ]]; then
    run_root tar -czf "$backup_file" -C "$PANEL_DIR" data.json
  else
    run_root tar -czf "$backup_file" -C "$CONFIG_DIR" panel.env
  fi
  run_root chmod 0600 "$backup_file"
  echo "✓ 已生成卸载前灾备：${backup_file}"
  echo "  该文件可能包含 Agent Token 和面板密钥，请限制访问权限。"
}

uninstall_local_agent() {
  confirm_action \
    "卸载本机 Docker Agent" \
    "dockerhub-agent.service、${AGENT_DIR}、${AGENT_ENV}" \
    "会停止本机 Agent；不会删除 Docker daemon、已有容器、Docker volume 或宿主机挂载数据。" || return 0

  if [[ -f "$AGENT_SERVICE" ]]; then
    run_root systemctl disable --now dockerhub-agent || true
  fi
  run_root rm -f "$AGENT_SERVICE"
  run_root rm -rf "$AGENT_DIR"
  run_root rm -f "$AGENT_ENV"
  run_root systemctl daemon-reload
  run_root rmdir "$CONFIG_DIR" 2>/dev/null || true
  echo "✓ 本机 Agent 已卸载"
  echo "  Docker daemon、已有容器和挂载数据均未处理。"
}

uninstall_panel() {
  confirm_action \
    "卸载中心面板" \
    "dockerhub-panel.service、${PANEL_DIR}、${PANEL_ENV}" \
    "会停止管理网页并删除面板程序和 data.json。已有 Docker 容器与宿主机挂载数据不会被删除。" || return 0

  if ask_yes_no "卸载前是否备份 data.json 和面板配置？" "y"; then
    backup_panel_data
  else
    echo "警告：已选择不备份面板数据。"
  fi

  if [[ -f "$PANEL_SERVICE" ]]; then
    run_root systemctl disable --now dockerhub-panel || true
  fi
  if [[ -f "$GPU_PORTAL_SERVICE" ]]; then
    run_root systemctl disable --now dockerhub-gpu-portal || true
  fi
  run_root rm -f "$PANEL_SERVICE"
  run_root rm -f "$GPU_PORTAL_SERVICE"
  run_root rm -rf "$PANEL_DIR"
  run_root rm -f "$PANEL_ENV"
  run_root systemctl daemon-reload
  run_root rmdir "$CONFIG_DIR" 2>/dev/null || true
  echo "✓ 中心面板已卸载"
  echo "  已有 Docker 容器与宿主机挂载数据均未处理。"

  if [[ -f "$AGENT_SERVICE" ]] && ask_yes_no "是否同时卸载本机 Docker Agent？" "n"; then
    uninstall_local_agent
  else
    echo "  本机 Agent 未卸载。"
  fi
}

show_menu() {
  cat <<EOF
╔════════════════════════════════════════════════════════════╗
║     DockerHub Manager - 中心面板管理工具                  ║
╚════════════════════════════════════════════════════════════╝

【中心面板】
  1. 安装/更新中心面板
  2. 重启中心面板
  3. 查看中心面板状态
  4. 查看中心面板日志
  5. 停止中心面板并取消开机启动（保留数据）
  6. 卸载中心面板

【本机 Docker Agent】
  7. 安装/更新本机 Agent
  8. 重启本机 Agent
  9. 查看本机 Agent 状态
 10. 查看本机 Agent 日志
 11. 卸载本机 Agent

  0. 退出
EOF
}

run_menu() {
  local choice
  while true; do
    show_menu
    read -r -p "请选择 [0-11]: " choice
    case "$choice" in
      1) install_or_update_panel ;;
      2) panel_restart ;;
      3) panel_status ;;
      4) panel_logs ;;
      5) disable_panel ;;
      6) uninstall_panel ;;
      7) install_local_agent ;;
      8) agent_restart ;;
      9) agent_status ;;
      10) agent_logs ;;
      11) uninstall_local_agent ;;
      0) exit 0 ;;
      *) echo "无效选项: ${choice}" ;;
    esac
    echo ""
  done
}

case "${1:-menu}" in
  menu) run_menu ;;
  install|update) install_or_update_panel ;;
  restart) panel_restart ;;
  status) panel_status ;;
  logs) panel_logs ;;
  disable) disable_panel ;;
  uninstall) uninstall_panel ;;
  install-local-agent|update-local-agent) install_local_agent ;;
  agent-restart) agent_restart ;;
  agent-status) agent_status ;;
  agent-logs) agent_logs ;;
  uninstall-local-agent) uninstall_local_agent ;;
  *)
    echo "用法: bash scripts/panel_manager.sh [menu|install|update|restart|status|logs|disable|uninstall|install-local-agent|agent-restart|agent-status|agent-logs|uninstall-local-agent]"
    exit 1
    ;;
esac
