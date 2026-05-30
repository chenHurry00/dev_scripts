#!/usr/bin/env bash
# DockerHub Manager - Agent 一键部署脚本
# 用法：bash deploy.sh <SSH_TARGET> [AGENT_TOKEN] [AGENT_PORT] [--open-firewall --allow-from <SOURCE_IP>]
# 示例：bash deploy.sh ubuntu@192.168.1.10 mysecret 5001 --open-firewall --allow-from 192.168.1.20
set -euo pipefail

SSH_TARGET="${1:-}"
TOKEN="${2:-changeme-agent-token}"
AGENT_PORT="${3:-5001}"
REMOTE_DIR="/opt/.dockerhub-agent"
OPEN_FIREWALL="0"
ALLOW_FROM=""

shift $(( $# >= 3 ? 3 : $# ))
while [[ $# -gt 0 ]]; do
  case "$1" in
    --open-firewall) OPEN_FIREWALL="1"; shift ;;
    --allow-from) ALLOW_FROM="${2:-}"; shift 2 ;;
    *) echo "未知参数: $1"; exit 1 ;;
  esac
done

if [[ -z "$SSH_TARGET" ]]; then
  echo "用法: bash deploy.sh <SSH_TARGET> [TOKEN] [PORT] [--open-firewall --allow-from <SOURCE_IP>]"
  echo "示例: bash deploy.sh ubuntu@192.168.1.10 mysecret 5001 --open-firewall --allow-from 192.168.1.20"
  exit 1
fi

if [[ "$OPEN_FIREWALL" == "1" ]]; then
  if [[ -z "$ALLOW_FROM" ]]; then
    echo "使用 --open-firewall 时必须提供 --allow-from <中心管理机IP>"
    exit 1
  fi
  case "$ALLOW_FROM" in
    any|0.0.0.0/0|::/0)
      echo "拒绝放行所有来源，请使用中心管理机 IP 或可信网段"
      exit 1
      ;;
  esac
fi

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  DockerHub Agent 部署"
echo "  SSH 目标:    $SSH_TARGET"
echo "  Agent 端口:  $AGENT_PORT"
echo "  工作目录:    $REMOTE_DIR"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "端口提醒："
echo "  - Agent API: TCP $AGENT_PORT，仅允许中心管理服务器访问"
echo "  - 容器 SSH:  TCP 32000-32999，按实际使用者来源网段放行"
echo "  - 脚本不会自动修改 1Panel、云安全组或容器 SSH 端口规则"
echo ""

echo "[1/4] 上传 Agent 脚本..."
ssh "$SSH_TARGET" "sudo mkdir -p '$REMOTE_DIR'"
scp "agent/agent.py" "$SSH_TARGET:/tmp/dockerhub-agent.py"
ssh "$SSH_TARGET" "sudo mv /tmp/dockerhub-agent.py '$REMOTE_DIR/agent.py' && sudo chmod 755 '$REMOTE_DIR/agent.py'"

echo "[2/4] 安装依赖..."
ssh "$SSH_TARGET" "
  set -e

  echo '  - 检查 Docker 命令'
  if ! command -v docker >/dev/null 2>&1; then
    echo 'ERROR: 远程服务器未检测到 docker。为避免破坏原有环境，部署脚本不会自动安装 Docker。'
    echo '请先按该服务器现有规范安装/配置 Docker 后重试。'
    exit 20
  fi

  echo '  - 检查 Docker daemon，最多等待 10 秒'
  if ! timeout 10 sudo docker info >/dev/null 2>&1; then
    echo 'ERROR: docker info 超时或失败。请检查 Docker daemon 和当前用户 sudo 权限。'
    exit 21
  fi

  echo '  - 检查 Python 和 pip'
  missing_pkgs=''
  if ! command -v python3 >/dev/null 2>&1; then
    missing_pkgs=\"\$missing_pkgs python3\"
  fi
  if ! command -v pip3 >/dev/null 2>&1; then
    missing_pkgs=\"\$missing_pkgs python3-pip\"
  fi

  if [ -n \"\$missing_pkgs\" ]; then
    echo \"  - 安装缺失系统依赖:\$missing_pkgs，最多等待 180 秒\"
    sudo apt-get update -qq
    timeout 180 sudo DEBIAN_FRONTEND=noninteractive apt-get install -y \$missing_pkgs -qq
  fi

  if ! python3 -c 'import flask' >/dev/null 2>&1; then
    echo '  - 安装 Flask，最多等待 120 秒'
    pip_extra=''
    if pip3 install --help 2>/dev/null | grep -q -- '--break-system-packages'; then
      pip_extra='--break-system-packages'
    fi
    timeout 120 sudo pip3 install flask \$pip_extra -q
  fi

  echo '  ✓ 依赖检查完成'
"

echo "[3/4] 注册 systemd 服务..."
SERVICE_FILE="/tmp/dockerhub-agent.service"
cat > "$SERVICE_FILE" << EOF
[Unit]
Description=DockerHub Manager Agent
After=network.target docker.service

[Service]
Type=simple
WorkingDirectory=$REMOTE_DIR
ExecStart=/usr/bin/python3 $REMOTE_DIR/agent.py --port $AGENT_PORT
Environment=AGENT_TOKEN=$TOKEN
Restart=always
RestartSec=5
User=root

[Install]
WantedBy=multi-user.target
EOF

scp "$SERVICE_FILE" "$SSH_TARGET:/tmp/dockerhub-agent.service"
ssh "$SSH_TARGET" "
  sudo mv /tmp/dockerhub-agent.service /etc/systemd/system/dockerhub-agent.service &&
  sudo systemctl daemon-reload &&
  sudo systemctl enable dockerhub-agent &&
  sudo systemctl restart dockerhub-agent
"
rm -f "$SERVICE_FILE"

if [[ "$OPEN_FIREWALL" == "1" ]]; then
  echo "[firewall] 添加 Agent 端口访问规则..."
  ssh "$SSH_TARGET" "
    set -e
    if command -v ufw >/dev/null 2>&1; then
      if sudo ufw status | grep -q '^Status: active'; then
        sudo ufw allow from '$ALLOW_FROM' to any port '$AGENT_PORT' proto tcp comment 'DockerHub Agent'
        echo '✓ 已通过 UFW 放行 $ALLOW_FROM -> TCP $AGENT_PORT'
      else
        echo '⚠ 检测到 UFW，但 UFW 当前未启用。未通过 UFW 添加规则。'
        echo '  这不代表服务器没有其他防火墙，请检查 1Panel、iptables、nftables 或云安全组。'
        echo '  为避免影响 SSH，脚本不会自动启用 UFW。'
      fi
    elif command -v firewall-cmd >/dev/null 2>&1; then
      if sudo firewall-cmd --state >/dev/null 2>&1; then
        sudo firewall-cmd --permanent --add-rich-rule='rule family=\"ipv4\" source address=\"$ALLOW_FROM\" port protocol=\"tcp\" port=\"$AGENT_PORT\" accept'
        sudo firewall-cmd --reload
        echo '✓ 已通过 firewalld 放行 $ALLOW_FROM -> TCP $AGENT_PORT'
      else
        echo '⚠ 检测到 firewalld，但当前未运行。不会自动启动防火墙。'
      fi
    else
      echo '⚠ 未检测到 UFW 或 firewalld。请在 1Panel、云安全组或现有防火墙中手动放行。'
    fi
  "
fi

echo "[4/4] 验证 Agent 连通性..."
HOST_FOR_CURL="${SSH_TARGET#*@}"
sleep 2
REMOTE_RESULT=$(ssh "$SSH_TARGET" "curl --noproxy '*' --connect-timeout 3 --max-time 5 -fsS 'http://127.0.0.1:${AGENT_PORT}/ping' 2>/dev/null || echo FAIL")
EXTERNAL_RESULT=$(curl --noproxy '*' --connect-timeout 3 --max-time 5 -fsS "http://${HOST_FOR_CURL}:${AGENT_PORT}/ping" 2>/dev/null || echo "FAIL")
if echo "$REMOTE_RESULT" | grep -q '"status"' && echo "$EXTERNAL_RESULT" | grep -q '"status"'; then
  echo ""
  echo "✓ Agent 部署成功"
  echo "  主机: ${HOST_FOR_CURL}"
  echo "  Agent 端口: ${AGENT_PORT}"
  echo "  Token: ${TOKEN}"
else
  echo ""
  echo "⚠ Agent 外部连通性检查失败"
  echo "  远程本机检查: ${REMOTE_RESULT}"
  echo "  管理机外部检查: ${EXTERNAL_RESULT}"
  echo ""
  if echo "$REMOTE_RESULT" | grep -q '"status"'; then
    echo "  Agent 在远程本机运行正常。请检查防火墙、1Panel 安全组或反向代理配置。"
    echo "  需要放行 TCP 端口: ${AGENT_PORT}"
  else
    echo "  Agent 在远程本机也不可用。自动输出诊断信息："
    ssh "$SSH_TARGET" "
      echo '--- systemctl status ---'
      sudo systemctl status dockerhub-agent --no-pager -l || true
      echo '--- journalctl ---'
      sudo journalctl -u dockerhub-agent -n 30 --no-pager || true
      echo '--- port listen ---'
      sudo ss -lntp | grep ':${AGENT_PORT} ' || true
    "
  fi
  echo ""
  echo "  手动验证: curl --noproxy '*' -v http://${HOST_FOR_CURL}:${AGENT_PORT}/ping"
  exit 30
fi

echo ""
echo "端口检查清单："
echo "  - TCP ${AGENT_PORT}: 仅允许中心管理服务器访问 Agent API"
echo "  - TCP 32000-32999: 按实际使用者来源网段放行容器 SSH"
echo "  - 如使用 1Panel 或云安全组，请在对应界面手动确认规则"
