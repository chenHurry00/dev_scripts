#!/usr/bin/env bash
# 检查远程 DockerHub Agent 连通性、鉴权、Docker 权限和挂载根目录。
set -euo pipefail

TARGET="${1:-}"
TOKEN="${2:-}"
MOUNT_ROOT="${3:-/tmp/dockerhub-test}"
PORT="${4:-5001}"

if [[ -z "$TARGET" || -z "$TOKEN" ]]; then
  echo "用法: bash scripts/check_agent.sh <HOST> <TOKEN> [MOUNT_ROOT] [PORT]"
  echo "示例: bash scripts/check_agent.sh 192.168.1.10 my-token /tmp/dockerhub-test 5001"
  exit 1
fi

BASE_URL="http://${TARGET}:${PORT}"

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  DockerHub Agent 检查"
echo "  Agent: ${BASE_URL}"
echo "  挂载根目录: ${MOUNT_ROOT}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

echo "[1/3] /ping"
curl --noproxy '*' --connect-timeout 3 --max-time 5 -fsS "${BASE_URL}/ping"
echo

echo "[2/3] /sysinfo"
curl --noproxy '*' --connect-timeout 3 --max-time 8 -fsS \
  -H "X-Agent-Token: ${TOKEN}" \
  "${BASE_URL}/sysinfo"
echo

echo "[3/3] /checks"
curl --noproxy '*' --connect-timeout 3 --max-time 8 -fsS \
  -X POST "${BASE_URL}/checks" \
  -H "Content-Type: application/json" \
  -H "X-Agent-Token: ${TOKEN}" \
  -d "{\"mount_roots\":[{\"host_path\":\"${MOUNT_ROOT}\",\"readonly\":false}]}"
echo

echo "✓ Agent 基础检查完成"
