#!/usr/bin/env bash
# DockerHub Manager - Agent 本机卸载脚本
set -euo pipefail

AGENT_DIR="/opt/.dockerhub-agent"
AGENT_SERVICE="/etc/systemd/system/dockerhub-agent.service"
LOCAL_AGENT_ENV="/etc/dockerhub-manager/local-agent.env"
CONFIG_DIR="/etc/dockerhub-manager"

if [[ "$EUID" -ne 0 ]]; then
  echo "请使用 sudo 执行："
  echo "  sudo bash ${AGENT_DIR}/uninstall.sh"
  exit 1
fi

echo "⚠️ 危险操作检测！"
echo "操作类型：卸载 DockerHub Agent"
echo "影响范围：dockerhub-agent.service、${AGENT_DIR}、本机 Agent 配置"
echo "风险评估：Agent 将停止，中心面板无法继续管理本机 Docker。"
echo "          不会删除 Docker daemon、已有容器、Docker volume 或宿主机挂载数据。"
echo ""
read -r -p "Continue? [y/N]: " answer
case "$answer" in
  y|Y|yes|YES|Yes) ;;
  *)
    echo "操作已取消。"
    exit 0
    ;;
esac

if [[ -f "$AGENT_SERVICE" ]]; then
  systemctl disable --now dockerhub-agent || true
fi
rm -f "$AGENT_SERVICE"
rm -f "$LOCAL_AGENT_ENV"
systemctl daemon-reload
rmdir "$CONFIG_DIR" 2>/dev/null || true
rm -rf "$AGENT_DIR"

echo "✓ DockerHub Agent 已卸载"
echo "  Docker daemon、已有容器、Docker volume 和宿主机挂载数据均未处理。"
