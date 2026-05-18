#!/usr/bin/env bash
# =============================================================================
#  dkr.sh — Docker 容器管理工具
#  美观的终端界面，帮助你快速管理 Docker 容器
# =============================================================================

set -euo pipefail

# ── 颜色 & 样式 ──────────────────────────────────────────────────────────────
RESET='\033[0m'
BOLD='\033[1m'
DIM='\033[2m'

BLACK='\033[30m'
RED='\033[31m'
GREEN='\033[32m'
YELLOW='\033[33m'
BLUE='\033[34m'
MAGENTA='\033[35m'
CYAN='\033[36m'
WHITE='\033[37m'

BG_BLACK='\033[40m'
BG_BLUE='\033[44m'
BG_CYAN='\033[46m'

# ── 常量 ─────────────────────────────────────────────────────────────────────
SCRIPT_PATH="$(realpath "$0")"
ALIAS_NAME="dkr"
BASHRC="$HOME/.bashrc"
ZSHRC="$HOME/.zshrc"
TERM_WIDTH=$(tput cols 2>/dev/null || echo 80)

# ── 工具函数 ─────────────────────────────────────────────────────────────────
repeat_char() {
    local char="$1" count="$2"
    printf '%0.s'"$char" $(seq 1 "$count")
}

center_text() {
    local text="$1"
    local plain="${text//$'\033['[0-9;]*m/}"  # 去掉 ANSI 码计算实际长度
    local len=${#plain}
    local pad=$(( (TERM_WIDTH - len) / 2 ))
    printf "%${pad}s%s\n" "" "$text"
}

print_line() {
    echo -e "${DIM}$(repeat_char '─' "$TERM_WIDTH")${RESET}"
}

print_double_line() {
    echo -e "${CYAN}$(repeat_char '═' "$TERM_WIDTH")${RESET}"
}

# ── Banner ────────────────────────────────────────────────────────────────────
print_banner() {
    clear
    print_double_line
    center_text "${BOLD}${CYAN}  🐳  Docker 容器管理工具  ${RESET}"
    center_text "${DIM}${WHITE}dkr.sh — 让容器管理变得简单${RESET}"
    print_double_line
    echo ""
}

# ── 状态徽章 ─────────────────────────────────────────────────────────────────
status_badge() {
    local status="$1"
    case "$status" in
        Up*|running)   echo -e "${BOLD}${GREEN}● 运行中${RESET}" ;;
        Exited*|exited) echo -e "${BOLD}${RED}● 已停止${RESET}" ;;
        Restarting*)   echo -e "${BOLD}${YELLOW}↻ 重启中${RESET}" ;;
        Paused*)       echo -e "${BOLD}${YELLOW}⏸ 已暂停${RESET}" ;;
        Created*)      echo -e "${BOLD}${BLUE}○ 已创建${RESET}" ;;
        *)             echo -e "${DIM}? 未知${RESET}" ;;
    esac
}

# ── 检查 Docker ───────────────────────────────────────────────────────────────
check_docker() {
    if ! command -v docker &>/dev/null; then
        echo -e "\n${RED}${BOLD}✗ 错误：未找到 docker 命令${RESET}"
        echo -e "${DIM}  请先安装 Docker：https://docs.docker.com/engine/install/${RESET}\n"
        exit 1
    fi
    if ! docker info &>/dev/null; then
        echo -e "\n${RED}${BOLD}✗ 错误：Docker 守护进程未运行${RESET}"
        echo -e "${DIM}  请运行：sudo systemctl start docker${RESET}\n"
        exit 1
    fi
}

# ── 列出容器（带编号，供选择用）────────────────────────────────────────────
list_containers() {
    local filter="${1:-all}"  # all | running | stopped
    local docker_args=()

    case "$filter" in
        running) docker_args=() ;;          # 默认只列运行中
        stopped) docker_args=(--filter "status=exited") ;;
        all)     docker_args=(-a) ;;
    esac

    # 获取容器列表
    mapfile -t CONTAINER_IDS < <(docker ps "${docker_args[@]}" --format '{{.ID}}')
    mapfile -t CONTAINER_NAMES < <(docker ps "${docker_args[@]}" --format '{{.Names}}')
    mapfile -t CONTAINER_IMAGES < <(docker ps "${docker_args[@]}" --format '{{.Image}}')
    mapfile -t CONTAINER_STATUS < <(docker ps "${docker_args[@]}" --format '{{.Status}}')
    mapfile -t CONTAINER_PORTS < <(docker ps "${docker_args[@]}" --format '{{.Ports}}')

    CONTAINER_COUNT=${#CONTAINER_IDS[@]}
}

# ── 显示容器表格 ──────────────────────────────────────────────────────────────
show_container_table() {
    local filter="${1:-all}"
    list_containers "$filter"

    echo ""
    if [[ $CONTAINER_COUNT -eq 0 ]]; then
        center_text "${YELLOW}⚠  没有找到任何容器${RESET}"
        echo ""
        return
    fi

    # 表头
    printf "${BOLD}${BG_BLUE}${WHITE}  %-4s  %-24s  %-22s  %-14s  %-20s${RESET}\n" \
        "编号" "容器名" "镜像" "状态" "端口"
    print_line

    # 行
    for i in "${!CONTAINER_IDS[@]}"; do
        local num=$(( i + 1 ))
        local name="${CONTAINER_NAMES[$i]}"
        local image="${CONTAINER_IMAGES[$i]}"
        local status="${CONTAINER_STATUS[$i]}"
        local ports="${CONTAINER_PORTS[$i]}"
        local badge
        badge=$(status_badge "$status")

        # 截断过长字段
        [[ ${#name}  -gt 24 ]] && name="${name:0:21}..."
        [[ ${#image} -gt 22 ]] && image="${image:0:19}..."
        [[ ${#ports} -gt 20 ]] && ports="${ports:0:17}..."

        # 交替行颜色
        if (( num % 2 == 0 )); then
            printf "${DIM}  %-4s  %-24s  %-22s  " "$num" "$name" "$image"
        else
            printf "  %-4s  %-24s  %-22s  " "$num" "$name" "$image"
        fi

        printf "%-22b  %-20s\n" "$badge" "$ports"
    done
    print_line
    echo -e "  ${DIM}共 ${CONTAINER_COUNT} 个容器${RESET}"
    echo ""
}

# ── 选择容器 ──────────────────────────────────────────────────────────────────
select_container() {
    local prompt="${1:-请选择容器编号}"
    local filter="${2:-all}"

    show_container_table "$filter"

    if [[ $CONTAINER_COUNT -eq 0 ]]; then
        return 1
    fi

    while true; do
        echo -ne "${CYAN}${BOLD}  ➤ ${prompt} (1-${CONTAINER_COUNT}，q 返回): ${RESET}"
        read -r choice
        [[ "$choice" == "q" || "$choice" == "Q" ]] && return 1
        if [[ "$choice" =~ ^[0-9]+$ ]] && (( choice >= 1 && choice <= CONTAINER_COUNT )); then
            SELECTED_IDX=$(( choice - 1 ))
            SELECTED_ID="${CONTAINER_IDS[$SELECTED_IDX]}"
            SELECTED_NAME="${CONTAINER_NAMES[$SELECTED_IDX]}"
            return 0
        fi
        echo -e "  ${RED}无效输入，请输入 1 到 ${CONTAINER_COUNT} 之间的数字${RESET}"
    done
}

# ── 进入容器 (exec) ───────────────────────────────────────────────────────────
cmd_exec() {
    echo -e "\n${BOLD}${CYAN}▶ 进入容器${RESET}"
    echo -e "  ${DIM}将在容器内启动交互式 shell${RESET}\n"

    if ! select_container "选择要进入的容器" "running"; then
        return
    fi

    echo ""
    echo -e "  ${GREEN}${BOLD}正在进入容器：${SELECTED_NAME}${RESET}"
    echo -e "  ${DIM}(输入 exit 或按 Ctrl+D 退出容器)${RESET}"
    print_line
    echo ""

    # 尝试常见 shell
    for shell in bash sh zsh; do
        if docker exec -it "$SELECTED_ID" "$shell" 2>/dev/null; then
            break
        fi
    done

    echo ""
    echo -e "  ${GREEN}✓ 已退出容器 ${BOLD}${SELECTED_NAME}${RESET}"
    sleep 1
}

# ── 启动容器 ──────────────────────────────────────────────────────────────────
cmd_start() {
    echo -e "\n${BOLD}${CYAN}▶ 启动容器${RESET}"
    echo -e "  ${DIM}启动已停止的容器${RESET}\n"

    list_containers "stopped"

    if [[ $CONTAINER_COUNT -eq 0 ]]; then
        echo -e "  ${YELLOW}⚠  没有已停止的容器${RESET}\n"
        sleep 1; return
    fi

    if ! select_container "选择要启动的容器" "stopped"; then
        return
    fi

    echo ""
    echo -ne "  ${YELLOW}正在启动 ${BOLD}${SELECTED_NAME}${RESET}${YELLOW}...${RESET} "
    if docker start "$SELECTED_ID" &>/dev/null; then
        echo -e "${GREEN}${BOLD}✓ 启动成功${RESET}"
    else
        echo -e "${RED}${BOLD}✗ 启动失败${RESET}"
        echo -e "  ${DIM}请运行 'docker logs ${SELECTED_NAME}' 查看日志${RESET}"
    fi
    sleep 1
}

# ── 停止容器 ──────────────────────────────────────────────────────────────────
cmd_stop() {
    echo -e "\n${BOLD}${CYAN}▶ 停止容器${RESET}"
    echo -e "  ${DIM}优雅地停止运行中的容器（SIGTERM，超时后 SIGKILL）${RESET}\n"

    if ! select_container "选择要停止的容器" "running"; then
        return
    fi

    echo -ne "  ${YELLOW}正在停止 ${BOLD}${SELECTED_NAME}${RESET}${YELLOW}...${RESET} "
    if docker stop "$SELECTED_ID" &>/dev/null; then
        echo -e "${GREEN}${BOLD}✓ 已停止${RESET}"
    else
        echo -e "${RED}${BOLD}✗ 停止失败${RESET}"
    fi
    sleep 1
}

# ── 重启容器 ──────────────────────────────────────────────────────────────────
cmd_restart() {
    echo -e "\n${BOLD}${CYAN}▶ 重启容器${RESET}"

    if ! select_container "选择要重启的容器" "all"; then
        return
    fi

    echo -ne "  ${YELLOW}正在重启 ${BOLD}${SELECTED_NAME}${RESET}${YELLOW}...${RESET} "
    if docker restart "$SELECTED_ID" &>/dev/null; then
        echo -e "${GREEN}${BOLD}✓ 重启成功${RESET}"
    else
        echo -e "${RED}${BOLD}✗ 重启失败${RESET}"
    fi
    sleep 1
}

# ── 设置重启策略 ──────────────────────────────────────────────────────────────
cmd_restart_policy() {
    echo -e "\n${BOLD}${CYAN}▶ 设置重启策略（开机常开 / 自动重试）${RESET}"
    echo -e "  ${DIM}控制 Docker 如何在容器退出或系统重启时处理容器${RESET}\n"

    if ! select_container "选择要配置的容器" "all"; then
        return
    fi

    echo ""
    echo -e "  ${BOLD}容器：${CYAN}${SELECTED_NAME}${RESET}"
    echo ""
    echo -e "  ${BOLD}请选择重启策略：${RESET}"
    echo ""
    echo -e "  ${CYAN}1)${RESET} ${BOLD}always${RESET}          — 总是重启（系统重启后也自动启动）${GREEN} 【推荐常开】${RESET}"
    echo -e "  ${CYAN}2)${RESET} ${BOLD}unless-stopped${RESET}  — 除非手动停止，否则总是重启"
    echo -e "  ${CYAN}3)${RESET} ${BOLD}on-failure[:N]${RESET}  — 仅在异常退出时重启，可限制最大次数"
    echo -e "  ${CYAN}4)${RESET} ${BOLD}no${RESET}              — 不自动重启 ${RED}【关闭自动重启】${RESET}"
    echo ""

    while true; do
        echo -ne "  ${CYAN}${BOLD}  ➤ 选择策略 (1-4，q 返回): ${RESET}"
        read -r policy_choice

        case "$policy_choice" in
            q|Q) return ;;
            1) POLICY="always" ;;
            2) POLICY="unless-stopped" ;;
            3)
                echo -ne "  ${CYAN}  ➤ 最大重试次数（留空=不限）: ${RESET}"
                read -r max_retry
                if [[ -n "$max_retry" && "$max_retry" =~ ^[0-9]+$ ]]; then
                    POLICY="on-failure:${max_retry}"
                else
                    POLICY="on-failure"
                fi
                ;;
            4) POLICY="no" ;;
            *) echo -e "  ${RED}无效输入${RESET}"; continue ;;
        esac
        break
    done

    echo ""
    echo -ne "  ${YELLOW}正在设置 ${BOLD}${SELECTED_NAME}${RESET}${YELLOW} → ${BOLD}${POLICY}${RESET}${YELLOW}...${RESET} "
    if docker update --restart "$POLICY" "$SELECTED_ID" &>/dev/null; then
        echo -e "${GREEN}${BOLD}✓ 设置成功${RESET}"
        echo ""
        echo -e "  ${DIM}当前策略：${RESET}${BOLD}${POLICY}${RESET}"

        # 显示当前实际策略
        actual=$(docker inspect --format '{{.HostConfig.RestartPolicy.Name}}' "$SELECTED_ID")
        max_ret=$(docker inspect --format '{{.HostConfig.RestartPolicy.MaximumRetryCount}}' "$SELECTED_ID")
        echo -e "  ${DIM}Docker 确认：${RESET}${actual}$([ "$max_ret" != "0" ] && echo ":$max_ret")"
    else
        echo -e "${RED}${BOLD}✗ 设置失败${RESET}"
    fi
    sleep 2
}

# ── 查看日志 ──────────────────────────────────────────────────────────────────
cmd_logs() {
    echo -e "\n${BOLD}${CYAN}▶ 查看容器日志${RESET}\n"

    if ! select_container "选择要查看日志的容器" "all"; then
        return
    fi

    echo ""
    echo -e "  ${BOLD}日志选项：${RESET}"
    echo -e "  ${CYAN}1)${RESET} 最近 50 行"
    echo -e "  ${CYAN}2)${RESET} 最近 200 行"
    echo -e "  ${CYAN}3)${RESET} 实时跟踪 (tail -f，Ctrl+C 退出)"
    echo -e "  ${CYAN}4)${RESET} 最近 50 行 + 实时跟踪"
    echo ""
    echo -ne "  ${CYAN}${BOLD}  ➤ 选择 (1-4，q 返回): ${RESET}"
    read -r log_choice

    print_line
    echo -e "  ${DIM}容器：${SELECTED_NAME} | 按 Ctrl+C 退出${RESET}"
    print_line
    echo ""

    case "$log_choice" in
        1) docker logs --tail 50 "$SELECTED_ID" ;;
        2) docker logs --tail 200 "$SELECTED_ID" ;;
        3) docker logs -f "$SELECTED_ID" ;;
        4) docker logs -f --tail 50 "$SELECTED_ID" ;;
        q|Q) return ;;
        *) docker logs --tail 50 "$SELECTED_ID" ;;
    esac

    echo ""
    echo -e "  ${DIM}— 日志结束 —${RESET}"
    echo ""
    echo -ne "  ${DIM}按 Enter 返回菜单...${RESET}"
    read -r
}

# ── 查看容器详情 ───────────────────────────────────────────────────────────────
cmd_inspect() {
    echo -e "\n${BOLD}${CYAN}▶ 容器详细信息${RESET}\n"

    if ! select_container "选择要查看详情的容器" "all"; then
        return
    fi

    echo ""
    print_line

    local name image status created ports restart_policy ip_addr mem_limit cpu_shares
    name=$(docker inspect --format '{{.Name}}' "$SELECTED_ID" | tr -d '/')
    image=$(docker inspect --format '{{.Config.Image}}' "$SELECTED_ID")
    status=$(docker inspect --format '{{.State.Status}}' "$SELECTED_ID")
    created=$(docker inspect --format '{{.Created}}' "$SELECTED_ID" | cut -d'T' -f1)
    restart_policy=$(docker inspect --format '{{.HostConfig.RestartPolicy.Name}}' "$SELECTED_ID")
    ip_addr=$(docker inspect --format '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' "$SELECTED_ID")
    mem_limit=$(docker inspect --format '{{.HostConfig.Memory}}' "$SELECTED_ID")

    # 格式化内存
    if [[ "$mem_limit" == "0" ]]; then
        mem_display="不限制"
    else
        mem_display="$(( mem_limit / 1024 / 1024 )) MB"
    fi

    echo ""
    echo -e "  ${BOLD}${WHITE}基本信息${RESET}"
    echo -e "  ${DIM}容器名称${RESET}      ${BOLD}${name}${RESET}"
    echo -e "  ${DIM}容器 ID  ${RESET}      ${SELECTED_ID}"
    echo -e "  ${DIM}使用镜像${RESET}      ${CYAN}${image}${RESET}"
    echo -e "  ${DIM}运行状态${RESET}      $(status_badge "$status")"
    echo -e "  ${DIM}创建时间${RESET}      ${created}"
    echo -e "  ${DIM}IP 地址  ${RESET}      ${ip_addr:-无}"
    echo ""
    echo -e "  ${BOLD}${WHITE}配置${RESET}"
    echo -e "  ${DIM}重启策略${RESET}      ${BOLD}${restart_policy}${RESET}"
    echo -e "  ${DIM}内存限制${RESET}      ${mem_display}"

    # 端口映射
    echo ""
    echo -e "  ${BOLD}${WHITE}端口映射${RESET}"
    local port_info
    port_info=$(docker inspect --format '{{range $k,$v := .NetworkSettings.Ports}}{{if $v}}{{(index $v 0).HostPort}}→{{$k}} {{end}}{{end}}' "$SELECTED_ID")
    if [[ -n "$port_info" ]]; then
        for p in $port_info; do
            echo -e "  ${DIM}  ${RESET}${CYAN}${p}${RESET}"
        done
    else
        echo -e "  ${DIM}  无端口映射${RESET}"
    fi

    # 挂载卷
    echo ""
    echo -e "  ${BOLD}${WHITE}挂载卷${RESET}"
    local mounts
    mounts=$(docker inspect --format '{{range .Mounts}}  {{.Source}} → {{.Destination}}{{"\n"}}{{end}}' "$SELECTED_ID")
    if [[ -n "$mounts" ]]; then
        echo -e "${DIM}${mounts}${RESET}"
    else
        echo -e "  ${DIM}  无挂载${RESET}"
    fi

    print_line
    echo -ne "  ${DIM}按 Enter 返回菜单...${RESET}"
    read -r
}

# ── 批量操作 ───────────────────────────────────────────────────────────────────
cmd_batch() {
    echo -e "\n${BOLD}${CYAN}▶ 批量操作${RESET}\n"
    echo -e "  ${CYAN}1)${RESET} 启动所有已停止的容器"
    echo -e "  ${CYAN}2)${RESET} 停止所有运行中的容器"
    echo -e "  ${CYAN}3)${RESET} 重启所有运行中的容器"
    echo -e "  ${CYAN}4)${RESET} 清理所有已停止的容器"
    echo ""
    echo -ne "  ${CYAN}${BOLD}  ➤ 选择操作 (1-4，q 返回): ${RESET}"
    read -r batch_choice

    case "$batch_choice" in
        q|Q) return ;;
        1)
            echo -e "\n  ${YELLOW}正在启动所有已停止的容器...${RESET}"
            stopped_ids=$(docker ps -aq --filter "status=exited")
            if [[ -z "$stopped_ids" ]]; then
                echo -e "  ${DIM}没有已停止的容器${RESET}"
            else
                echo "$stopped_ids" | xargs docker start
                echo -e "  ${GREEN}${BOLD}✓ 完成${RESET}"
            fi
            ;;
        2)
            echo -ne "\n  ${RED}${BOLD}⚠ 将停止所有运行中的容器，确认？(y/N): ${RESET}"
            read -r confirm
            if [[ "$confirm" =~ ^[Yy]$ ]]; then
                running_ids=$(docker ps -q)
                if [[ -z "$running_ids" ]]; then
                    echo -e "  ${DIM}没有运行中的容器${RESET}"
                else
                    echo "$running_ids" | xargs docker stop
                    echo -e "  ${GREEN}${BOLD}✓ 已停止所有容器${RESET}"
                fi
            fi
            ;;
        3)
            echo -e "\n  ${YELLOW}正在重启所有运行中的容器...${RESET}"
            running_ids=$(docker ps -q)
            if [[ -z "$running_ids" ]]; then
                echo -e "  ${DIM}没有运行中的容器${RESET}"
            else
                echo "$running_ids" | xargs docker restart
                echo -e "  ${GREEN}${BOLD}✓ 完成${RESET}"
            fi
            ;;
        4)
            echo -ne "\n  ${RED}${BOLD}⚠ 将删除所有已停止的容器，确认？(y/N): ${RESET}"
            read -r confirm
            if [[ "$confirm" =~ ^[Yy]$ ]]; then
                docker container prune -f
                echo -e "  ${GREEN}${BOLD}✓ 清理完成${RESET}"
            fi
            ;;
    esac
    sleep 1
}

# ── Alias 检查与设置 ───────────────────────────────────────────────────────────
check_and_setup_alias() {
    local alias_line="alias ${ALIAS_NAME}='bash ${SCRIPT_PATH}'"
    local configured=false
    local rc_files=()

    # 检测用户用的是哪个 shell 配置文件
    [[ -f "$BASHRC" ]] && rc_files+=("$BASHRC")
    [[ -f "$ZSHRC" ]]  && rc_files+=("$ZSHRC")

    for rc in "${rc_files[@]}"; do
        if grep -qF "alias ${ALIAS_NAME}=" "$rc" 2>/dev/null; then
            configured=true
            break
        fi
    done

    if $configured; then
        return 0
    fi

    # 未配置 alias，询问用户
    echo ""
    print_line
    echo -e "  ${BOLD}${YELLOW}💡 快捷启动提示${RESET}"
    echo ""
    echo -e "  检测到你还没有设置 ${BOLD}${CYAN}${ALIAS_NAME}${RESET} 快捷命令。"
    echo -e "  设置后可以直接在任意目录输入 ${BOLD}${CYAN}${ALIAS_NAME}${RESET} 来启动此工具。"
    echo ""
    echo -e "  将在以下文件中添加："
    for rc in "${rc_files[@]}"; do
        echo -e "  ${DIM}  ${rc}${RESET}"
    done
    echo ""
    echo -ne "  ${CYAN}${BOLD}  ➤ 是否添加 alias？(Y/n): ${RESET}"
    read -r answer

    if [[ "$answer" =~ ^[Nn]$ ]]; then
        echo -e "  ${DIM}已跳过，下次启动时会再次询问。${RESET}"
        echo -e "  ${DIM}如需手动添加，请在 ~/.bashrc 中添加：${RESET}"
        echo -e "  ${DIM}  ${alias_line}${RESET}"
        print_line
        echo ""
        sleep 2
        return
    fi

    local added=false
    for rc in "${rc_files[@]}"; do
        echo "" >> "$rc"
        echo "# Docker 管理工具 dkr.sh" >> "$rc"
        echo "$alias_line" >> "$rc"
        added=true
        echo -e "  ${GREEN}✓ 已添加到 ${rc}${RESET}"
    done

    if $added; then
        echo ""
        echo -e "  ${GREEN}${BOLD}✓ 设置成功！${RESET}"
        echo -e "  ${DIM}请运行以下命令使 alias 立即生效：${RESET}"
        for rc in "${rc_files[@]}"; do
            echo -e "  ${CYAN}  source ${rc}${RESET}"
        done
        echo -e "  ${DIM}之后即可直接输入 ${BOLD}${CYAN}${ALIAS_NAME}${DIM} 启动工具。${RESET}"
    fi
    print_line
    echo ""
    sleep 2
}

# ── 主菜单 ────────────────────────────────────────────────────────────────────
main_menu() {
    while true; do
        print_banner

        # 快速状态概览
        running_count=$(docker ps -q | wc -l)
        total_count=$(docker ps -aq | wc -l)
        stopped_count=$(( total_count - running_count ))

        echo -e "  ${BOLD}系统概览  ${RESET}${GREEN}●${RESET} 运行中 ${BOLD}${running_count}${RESET}   ${RED}●${RESET} 已停止 ${BOLD}${stopped_count}${RESET}   总计 ${BOLD}${total_count}${RESET}"
        echo ""
        print_line

        echo ""
        echo -e "  ${BOLD}容器操作${RESET}"
        echo ""
        echo -e "  ${BOLD}${CYAN}1)${RESET}  🚪  ${BOLD}进入容器${RESET}        ${DIM}exec -it（交互式 shell）${RESET}"
        echo -e "  ${BOLD}${CYAN}2)${RESET}  ▶   ${BOLD}启动容器${RESET}        ${DIM}docker start${RESET}"
        echo -e "  ${BOLD}${CYAN}3)${RESET}  ■   ${BOLD}停止容器${RESET}        ${DIM}docker stop（优雅退出）${RESET}"
        echo -e "  ${BOLD}${CYAN}4)${RESET}  ↻   ${BOLD}重启容器${RESET}        ${DIM}docker restart${RESET}"
        echo ""
        echo -e "  ${BOLD}配置与信息${RESET}"
        echo ""
        echo -e "  ${BOLD}${CYAN}5)${RESET}  🔁  ${BOLD}重启策略${RESET}        ${DIM}always / on-failure:N / no ...${RESET}"
        echo -e "  ${BOLD}${CYAN}6)${RESET}  📋  ${BOLD}查看日志${RESET}        ${DIM}docker logs（支持实时跟踪）${RESET}"
        echo -e "  ${BOLD}${CYAN}7)${RESET}  🔍  ${BOLD}容器详情${RESET}        ${DIM}端口 / 挂载 / 网络 / 配置${RESET}"
        echo -e "  ${BOLD}${CYAN}8)${RESET}  📊  ${BOLD}查看所有容器${RESET}    ${DIM}列表总览${RESET}"
        echo ""
        echo -e "  ${BOLD}批量与其他${RESET}"
        echo ""
        echo -e "  ${BOLD}${CYAN}9)${RESET}  ⚡  ${BOLD}批量操作${RESET}        ${DIM}一次性操作多个容器${RESET}"
        echo -e "  ${BOLD}${CYAN}q)${RESET}  ✕   ${BOLD}退出${RESET}"
        echo ""
        print_line
        echo ""
        echo -ne "  ${CYAN}${BOLD}  ➤ 请选择操作: ${RESET}"
        read -r choice

        case "$choice" in
            1) cmd_exec ;;
            2) cmd_start ;;
            3) cmd_stop ;;
            4) cmd_restart ;;
            5) cmd_restart_policy ;;
            6) cmd_logs ;;
            7) cmd_inspect ;;
            8)
                print_banner
                show_container_table "all"
                echo -ne "  ${DIM}按 Enter 返回菜单...${RESET}"
                read -r
                ;;
            9) cmd_batch ;;
            q|Q|quit|exit)
                echo ""
                echo -e "  ${DIM}再见！${RESET}"
                echo ""
                exit 0
                ;;
            *)
                echo -e "  ${RED}无效输入，请输入 1-9 或 q${RESET}"
                sleep 0.8
                ;;
        esac
    done
}

# ── 入口 ──────────────────────────────────────────────────────────────────────
main() {
    check_docker
    check_and_setup_alias
    main_menu
}

main "$@"
