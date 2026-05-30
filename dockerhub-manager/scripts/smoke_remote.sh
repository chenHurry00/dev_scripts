#!/usr/bin/env bash
# 远程端到端冒烟测试：可选部署 Agent，检查 Agent，可选创建测试容器并清理。
set -euo pipefail

SSH_TARGET=""
HOST=""
TOKEN=""
PORT="5001"
MOUNT_ROOT="/tmp/dockerhub-test"
CREATE_CONTAINER="0"
DEPLOY_AGENT="0"
OPEN_FIREWALL="0"
ALLOW_FROM=""
SSH_PUBLIC_KEY=""
CONTAINER_NAME="dockerhub_smoke_${USER}_$(date +%s)"
LOGIN_USER="smoketest"
SSH_PORT="32091"
IMAGE="lscr.io/linuxserver/openssh-server:latest"
SSH_PORT_MIN="32000"
SSH_PORT_MAX="32999"

usage() {
  cat <<EOF
用法:
  bash scripts/smoke_remote.sh --host <HOST> --token <TOKEN> [选项]

常用:
  bash scripts/smoke_remote.sh --host 192.168.1.10 --token my-token
  bash scripts/smoke_remote.sh --ssh ubuntu@192.168.1.10 --host 192.168.1.10 --token my-token --deploy
  bash scripts/smoke_remote.sh --host 192.168.1.10 --token my-token --create-container

选项:
  --ssh <USER@HOST>        SSH 目标；配合 --deploy 或远程创建测试目录使用
  --host <HOST>            Agent HTTP 地址中的主机
  --token <TOKEN>          Agent Token
  --port <PORT>            Agent 端口，默认 5001
  --mount-root <PATH>      测试挂载根目录，默认 /tmp/dockerhub-test
  --deploy                 先执行 deploy.sh 部署/更新 Agent
  --open-firewall          部署时新增 Agent 端口允许规则；必须同时提供 --allow-from
  --allow-from <IP/CIDR>   允许访问 Agent 的中心管理机 IP 或可信网段
  --create-container       创建测试容器、验证 SSH、最后删除容器
  --key <PUBKEY_PATH>      SSH 公钥路径，默认自动选择 id_ed25519.pub 或 id_rsa.pub
  --ssh-port <PORT>        测试容器 SSH 端口，默认 32091
  --image <IMAGE>          测试镜像，默认 lscr.io/linuxserver/openssh-server:latest
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --ssh) SSH_TARGET="$2"; shift 2 ;;
    --host) HOST="$2"; shift 2 ;;
    --token) TOKEN="$2"; shift 2 ;;
    --port) PORT="$2"; shift 2 ;;
    --mount-root) MOUNT_ROOT="$2"; shift 2 ;;
    --deploy) DEPLOY_AGENT="1"; shift ;;
    --open-firewall) OPEN_FIREWALL="1"; shift ;;
    --allow-from) ALLOW_FROM="$2"; shift 2 ;;
    --create-container) CREATE_CONTAINER="1"; shift ;;
    --key) SSH_PUBLIC_KEY="$2"; shift 2 ;;
    --ssh-port) SSH_PORT="$2"; shift 2 ;;
    --image) IMAGE="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "未知参数: $1"; usage; exit 1 ;;
  esac
done

if [[ -z "$HOST" || -z "$TOKEN" ]]; then
  usage
  exit 1
fi

if [[ "$DEPLOY_AGENT" == "1" && -z "$SSH_TARGET" ]]; then
  echo "使用 --deploy 时必须提供 --ssh <USER@HOST>"
  exit 1
fi

if [[ "$OPEN_FIREWALL" == "1" && "$DEPLOY_AGENT" != "1" ]]; then
  echo "使用 --open-firewall 时必须同时提供 --deploy"
  exit 1
fi

if [[ "$OPEN_FIREWALL" == "1" && -z "$ALLOW_FROM" ]]; then
  echo "使用 --open-firewall 时必须提供 --allow-from <中心管理机IP>"
  exit 1
fi

if (( SSH_PORT < SSH_PORT_MIN || SSH_PORT > SSH_PORT_MAX )); then
  echo "测试容器 SSH 端口必须位于 ${SSH_PORT_MIN}-${SSH_PORT_MAX}"
  exit 1
fi

if [[ "$CREATE_CONTAINER" == "1" ]]; then
  if [[ -z "$SSH_PUBLIC_KEY" ]]; then
    for candidate in "${HOME}/.ssh/id_ed25519.pub" "${HOME}/.ssh/id_rsa.pub"; do
      if [[ -f "$candidate" ]]; then
        SSH_PUBLIC_KEY="$candidate"
        break
      fi
    done
  fi
  if [[ -z "$SSH_PUBLIC_KEY" || ! -f "$SSH_PUBLIC_KEY" ]]; then
    echo "找不到 SSH 公钥。请先执行 ssh-keygen -t ed25519，或通过 --key 指定公钥路径。"
    exit 1
  fi
fi

BASE_URL="http://${HOST}:${PORT}"
ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
WORKSPACE_HOST_PATH="${MOUNT_ROOT}/${CONTAINER_NAME}"

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  DockerHub 远程冒烟测试"
echo "  Agent: ${BASE_URL}"
echo "  部署 Agent: ${DEPLOY_AGENT}"
echo "  创建容器: ${CREATE_CONTAINER}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

if [[ "$DEPLOY_AGENT" == "1" ]]; then
  echo "[1/5] 部署/更新 Agent"
  DEPLOY_ARGS=("$SSH_TARGET" "$TOKEN" "$PORT")
  if [[ "$OPEN_FIREWALL" == "1" ]]; then
    DEPLOY_ARGS+=(--open-firewall --allow-from "$ALLOW_FROM")
  fi
  bash "${ROOT_DIR}/deploy.sh" "${DEPLOY_ARGS[@]}"
else
  echo "[1/5] 跳过 Agent 部署"
fi

if [[ -n "$SSH_TARGET" ]]; then
  echo "[2/5] 准备测试挂载目录"
  ssh "$SSH_TARGET" "sudo mkdir -p '$MOUNT_ROOT' && sudo chmod 777 '$MOUNT_ROOT'"
else
  echo "[2/5] 跳过远程目录准备；请确认 ${MOUNT_ROOT} 已存在且可写"
fi

echo "[3/5] 检查 Agent"
bash "${ROOT_DIR}/scripts/check_agent.sh" "$HOST" "$TOKEN" "$MOUNT_ROOT" "$PORT"

if [[ "$CREATE_CONTAINER" != "1" ]]; then
  echo "[4/5] 跳过容器创建"
  echo "[5/5] 完成。需要端到端容器测试时加 --create-container"
  exit 0
fi

PUBKEY_CONTENT="$(tr -d '\n' < "$SSH_PUBLIC_KEY")"

echo "[4/5] 创建测试容器"
CREATE_PAYLOAD="$(cat <<EOF
{
  "name": "${CONTAINER_NAME}",
  "image": "${IMAGE}",
  "ssh_port": ${SSH_PORT},
  "cpu": "1",
  "memory": "1g",
  "pids_limit": 256,
  "login_user": "${LOGIN_USER}",
  "ssh_public_key": "${PUBKEY_CONTENT}",
  "mounts": [
    {
      "host_path": "${WORKSPACE_HOST_PATH}",
      "container_path": "/workspace",
      "readonly": false
    }
  ],
  "allowed_mount_roots": [
    {
      "host_path": "${MOUNT_ROOT}",
      "readonly": false
    }
  ]
}
EOF
)"

cleanup() {
  echo "[cleanup] 删除测试容器 ${CONTAINER_NAME}"
  rm -f /tmp/dockerhub-smoke-ssh.out /tmp/dockerhub-smoke-ssh.err
  curl --noproxy '*' --connect-timeout 3 --max-time 8 -fsS \
    -X DELETE "${BASE_URL}/containers/${CONTAINER_NAME}/remove" \
    -H "X-Agent-Token: ${TOKEN}" >/dev/null 2>&1 || true
}
trap cleanup EXIT

show_container_diagnostics() {
  local response
  local http_code
  local status_json
  response=$(curl --noproxy '*' --connect-timeout 3 --max-time 8 -sS \
    "${BASE_URL}/containers/${CONTAINER_NAME}/status" \
    -H "X-Agent-Token: ${TOKEN}" \
    -w $'\n%{http_code}' 2>&1 || true)
  http_code="${response##*$'\n'}"
  status_json="${response%$'\n'*}"

  if [[ "$http_code" == "200" && -n "$status_json" ]]; then
    echo "$status_json"
  else
    echo "  状态 API 不可用（HTTP ${http_code:-unknown}）。可能是远程 Agent 版本较旧或请求失败。"
    if [[ -n "$SSH_TARGET" ]]; then
      echo "  使用 SSH 直接读取远程 Docker 状态："
      ssh "$SSH_TARGET" "
        echo '--- docker inspect ---'
        sudo docker inspect '$CONTAINER_NAME' --format '{{json .State}}' 2>&1 || true
        echo '--- docker logs ---'
        sudo docker logs --tail 80 '$CONTAINER_NAME' 2>&1 || true
      "
    fi
  fi

  echo "  本机到 ${HOST}:${SSH_PORT} 的 TCP 检查："
  if timeout 3 bash -c "</dev/tcp/${HOST}/${SSH_PORT}" 2>/dev/null; then
    echo "  TCP 端口可达。若 SSH 仍失败，请检查公钥认证。"
  else
    if echo "$status_json" | grep -q '"port_22":"[^"]'; then
      if echo "$status_json" | grep -q '"sshd_listen":""'; then
        echo "  Docker 端口已映射，但容器内 sshd 尚未监听 SSH 端口。通常仍在初始化配置。"
      else
        echo "  Docker 端口已映射且容器内已有监听，但外部 TCP 不可达。请检查防火墙规则。"
      fi
    else
      echo "  TCP 端口不可达，且未发现 Docker 端口映射。请检查容器创建参数。"
    fi
  fi

  echo "  最近一次 SSH 失败原因："
  ssh \
    -v \
    -o BatchMode=yes \
    -o StrictHostKeyChecking=no \
    -o UserKnownHostsFile=/dev/null \
    -o ConnectTimeout=3 \
    -p "$SSH_PORT" "${LOGIN_USER}@${HOST}" true 2>&1 | tail -n 14 || true
  if [[ -s /tmp/dockerhub-smoke-ssh.err ]]; then
    echo "  最近一次工作区写入失败原因："
    tail -n 8 /tmp/dockerhub-smoke-ssh.err
  fi
}

curl --noproxy '*' --connect-timeout 3 --max-time 330 -fsS \
  -X POST "${BASE_URL}/containers/create" \
  -H "Content-Type: application/json" \
  -H "X-Agent-Token: ${TOKEN}" \
  -d "$CREATE_PAYLOAD"
echo

echo "等待 SSH 服务启动，最多 120 秒..."

SSH_READY="0"
for attempt in $(seq 1 24); do
  if ssh \
    -o BatchMode=yes \
    -o StrictHostKeyChecking=no \
    -o UserKnownHostsFile=/dev/null \
    -o ConnectTimeout=3 \
    -p "$SSH_PORT" "${LOGIN_USER}@${HOST}" \
    "echo smoke-ok > /workspace/smoke.txt && cat /workspace/smoke.txt" >/tmp/dockerhub-smoke-ssh.out 2>/tmp/dockerhub-smoke-ssh.err; then
    SSH_READY="1"
    break
  fi

  STATUS_JSON=$(curl --noproxy '*' --connect-timeout 3 --max-time 8 -sS \
    "${BASE_URL}/containers/${CONTAINER_NAME}/status" \
    -H "X-Agent-Token: ${TOKEN}" 2>&1 || true)
  if echo "$STATUS_JSON" | grep -q '"Running":false'; then
    echo "测试容器已退出，停止等待。"
    break
  fi

  echo "  - 第 ${attempt}/24 次检查：SSH 尚未就绪"
  if (( attempt % 4 == 0 )); then
    echo "  - 当前容器状态与最近日志："
    show_container_diagnostics
  fi
  sleep 5
done

echo "[5/5] 验证 SSH 和挂载"
if [[ "$SSH_READY" != "1" ]]; then
  echo "✗ SSH 未就绪。容器诊断信息："
  show_container_diagnostics
  echo
  exit 31
fi

cat /tmp/dockerhub-smoke-ssh.out
rm -f /tmp/dockerhub-smoke-ssh.out
rm -f /tmp/dockerhub-smoke-ssh.err

echo "✓ 端到端冒烟测试通过"
