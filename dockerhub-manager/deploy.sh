#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────
# DockerHub Manager — Agent 一键部署脚本
# 用法：bash deploy.sh <SERVER_IP> [AGENT_TOKEN] [DATA_PATH]
# 示例：bash deploy.sh 192.168.1.10 mysecret /mnt/data
# ─────────────────────────────────────────────────────────────────
set -euo pipefail

SERVER="${1:-}"
TOKEN="${2:-changeme-agent-token}"
DATA_PATH="${3:-/mnt/data}"
AGENT_PORT="${4:-5001}"
REMOTE_DIR="/opt/dockerhub-agent"

if [[ -z "$SERVER" ]]; then
  echo "用法: bash deploy.sh <SERVER_IP> [TOKEN] [DATA_PATH] [PORT]"
  exit 1
fi

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  DockerHub Agent 部署"
echo "  目标服务器: $SERVER"
echo "  Agent 端口: $AGENT_PORT"
echo "  数据路径:   $DATA_PATH"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# 1. 创建远程目录并上传 Agent 脚本
echo "[1/4] 上传 Agent 脚本..."
ssh "root@${SERVER}" "mkdir -p ${REMOTE_DIR}"
scp agent/agent.py "root@${SERVER}:${REMOTE_DIR}/agent.py"

# 2. 安装依赖
echo "[2/4] 安装 Python 依赖..."
ssh "root@${SERVER}" "
  apt-get update -qq && \
  apt-get install -y python3 python3-pip docker.io -qq && \
  pip3 install flask --break-system-packages -q
"

# 3. 写入 systemd 服务
echo "[3/4] 注册 systemd 服务..."
ssh "root@${SERVER}" "cat > /etc/systemd/system/dockerhub-agent.service << 'EOF'
[Unit]
Description=DockerHub Manager Agent
After=network.target docker.service

[Service]
ExecStart=/usr/bin/python3 ${REMOTE_DIR}/agent.py --port ${AGENT_PORT}
Environment=AGENT_TOKEN=${TOKEN}
Environment=DATA_PATH=${DATA_PATH}
WorkingDirectory=${REMOTE_DIR}
Restart=always
User=root

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable --now dockerhub-agent
"

# 4. 验证
echo "[4/4] 验证 Agent 连通性..."
sleep 2
RESULT=$(curl -sf "http://${SERVER}:${AGENT_PORT}/ping" 2>/dev/null || echo "FAIL")
if echo "$RESULT" | grep -q '"status"'; then
  echo ""
  echo "✓ Agent 部署成功！"
  echo "  在管理面板中添加服务器时使用："
  echo "  主机: ${SERVER}"
  echo "  Agent 端口: ${AGENT_PORT}"
  echo "  Token: ${TOKEN}"
else
  echo ""
  echo "⚠ Agent 部署完成，但连通性检查失败，请检查防火墙设置"
  echo "  手动验证: curl http://${SERVER}:${AGENT_PORT}/ping"
fi
