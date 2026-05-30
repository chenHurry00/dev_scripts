#!/usr/bin/env bash
# conda_menu_helper.sh
# 用法：
#   bash conda_menu_helper.sh --install   # 安装并写入 ~/.bashrc
#   source ~/.bashrc
#   cx                                  # 进入数字菜单
#
# 重要：为了让 conda activate 作用在当前终端，本脚本必须被 source。
# 安装后 cx 是一个 shell 函数：cx(){ source ~/.local/share/conda-menu/conda-menu.sh "$@"; }

# -------------------------------
# 基础配置
# -------------------------------
_CMH_NAME="Conda Menu Helper"
_CMH_VERSION="2026.05.30-menu-clean-onoff-v8"
_CMH_INSTALL_DIR="${HOME}/.local/share/conda-menu"
_CMH_INSTALL_FILE="${_CMH_INSTALL_DIR}/conda-menu.sh"
_CMH_STATE_DIR="${HOME}/.local/state/conda-menu"
_CMH_LOG_FILE="${_CMH_STATE_DIR}/.conda-menu.log"
_CMH_RECENT_FILE="${_CMH_STATE_DIR}/.recent_envs"
_CMH_ROOT_FILE="${_CMH_STATE_DIR}/.conda_root"
_CMH_BACKUP_DIR="${_CMH_STATE_DIR}/backups"
_CMH_BASHRC="${HOME}/.bashrc"
_CMH_MARK_BEGIN="# >>> conda-menu-helper >>>"
_CMH_MARK_END="# <<< conda-menu-helper <<<"
_CMH_CONDA_INIT_BEGIN="# >>> conda initialize >>>"
_CMH_CONDA_INIT_END="# <<< conda initialize <<<"

# 不要 clear：用户明确要求保留上方执行信息。
# 本脚本全程不调用 clear / reset。

# -------------------------------
# 样式
# -------------------------------
_cmh_has_tput() { command -v tput >/dev/null 2>&1 && [[ -t 1 ]]; }
if _cmh_has_tput; then
  _CMH_BOLD="$(tput bold 2>/dev/null || true)"
  _CMH_DIM="$(tput dim 2>/dev/null || true)"
  _CMH_RESET="$(tput sgr0 2>/dev/null || true)"
  _CMH_GREEN="$(tput setaf 2 2>/dev/null || true)"
  _CMH_YELLOW="$(tput setaf 3 2>/dev/null || true)"
  _CMH_BLUE="$(tput setaf 4 2>/dev/null || true)"
  _CMH_RED="$(tput setaf 1 2>/dev/null || true)"
  _CMH_CYAN="$(tput setaf 6 2>/dev/null || true)"
else
  _CMH_BOLD=""; _CMH_DIM=""; _CMH_RESET=""; _CMH_GREEN=""; _CMH_YELLOW=""; _CMH_BLUE=""; _CMH_RED=""; _CMH_CYAN=""
fi

_cmh_ok()   { printf "%b\n" "${_CMH_GREEN}✔${_CMH_RESET} $*"; }
_cmh_warn() { printf "%b\n" "${_CMH_YELLOW}⚠${_CMH_RESET} $*"; }
_cmh_err()  { printf "%b\n" "${_CMH_RED}✘${_CMH_RESET} $*"; }
_cmh_info() { printf "%b\n" "${_CMH_CYAN}➜${_CMH_RESET} $*"; }

_cmh_hr() {
  printf "%b\n" "${_CMH_BLUE}────────────────────────────────────────────────────────${_CMH_RESET}"
}

_cmh_now() { date '+%F %T'; }
_cmh_mkdirs() { mkdir -p "${_CMH_STATE_DIR}" "${_CMH_BACKUP_DIR}" "${_CMH_INSTALL_DIR}"; }
_cmh_log() {
  _cmh_mkdirs
  printf '[%s] %s\n' "$(_cmh_now)" "$*" >> "${_CMH_LOG_FILE}"
}

_cmh_pause() {
  printf "\n"
  read -r -p "按 Enter 返回菜单..." _cmh_dummy
}

_cmh_confirm() {
  local prompt="${1:-确认继续?}"
  local ans
  read -r -p "${prompt} [y/N]: " ans
  [[ "${ans}" == "y" || "${ans}" == "Y" || "${ans}" == "yes" || "${ans}" == "YES" ]]
}

# -------------------------------
# 判断是否 source
# -------------------------------
_cmh_is_sourced() {
  [[ "${BASH_SOURCE[0]}" != "$0" ]]
}

# -------------------------------
# 安装与 bashrc 注册
# -------------------------------
_cmh_install() {
  _cmh_mkdirs

  local src="${BASH_SOURCE[0]}"
  if [[ ! -f "$src" ]]; then
    _cmh_err "找不到当前脚本文件：$src"
    return 1
  fi

  cp -f "$src" "${_CMH_INSTALL_FILE}"
  chmod +x "${_CMH_INSTALL_FILE}"

  local block
  block="$(_cmh_bashrc_block)"

  if [[ -f "${_CMH_BASHRC}" ]] && grep -Fq "${_CMH_MARK_BEGIN}" "${_CMH_BASHRC}"; then
    _cmh_info "检测到 ~/.bashrc 已注册 conda-menu-helper，准备更新注册块。"
    local tmp
    tmp="$(mktemp)"
    awk -v begin="${_CMH_MARK_BEGIN}" -v end="${_CMH_MARK_END}" '
      $0 == begin {skip=1; next}
      $0 == end {skip=0; next}
      skip != 1 {print}
    ' "${_CMH_BASHRC}" > "$tmp"
    printf '\n%s\n' "$block" >> "$tmp"
    cp "$tmp" "${_CMH_BASHRC}"
    rm -f "$tmp"
  else
    printf '\n%s\n' "$block" >> "${_CMH_BASHRC}"
  fi

  _cmh_ok "已安装到：${_CMH_INSTALL_FILE}"
  _cmh_ok "已注册到：${_CMH_BASHRC}"
  _cmh_info "执行：source ~/.bashrc"
  _cmh_info "然后输入：cx"
}

_cmh_bashrc_block() {
  cat <<EOS
${_CMH_MARK_BEGIN}
# Conda Menu Helper: 数字菜单式 conda 环境助手。
# 注意：必须 source 脚本，才能让 conda activate 作用于当前终端。
cx() {
  source "${_CMH_INSTALL_FILE}" "\$@"
}
alias cenv='cx'
alias cmenu='cx'

# 可选快速别名：直接进切换环境菜单。
ca() {
  source "${_CMH_INSTALL_FILE}" activate "\$@"
}

# 环境名补全：cx activate <Tab> / ca <Tab>
# 这里也走助手自身的 conda 发现逻辑，所以 conda 不在 ~/miniconda3 也能补全。
_cmh_env_complete() {
  local cur envs
  cur="\${COMP_WORDS[COMP_CWORD]}"
  envs="\$(source "${_CMH_INSTALL_FILE}" __env_names 2>/dev/null || true)"
  COMPREPLY=(\$(compgen -W "\${envs}" -- "\${cur}"))
}
complete -F _cmh_env_complete ca 2>/dev/null || true
complete -F _cmh_env_complete cx 2>/dev/null || true
${_CMH_MARK_END}
EOS
}

_cmh_self_check_registration() {
  # 只在菜单启动时提醒，不强制写入，避免污染用户配置。
  if [[ ! -f "${_CMH_BASHRC}" ]] || ! grep -Fq "${_CMH_MARK_BEGIN}" "${_CMH_BASHRC}"; then
    _cmh_warn "当前未在 ~/.bashrc 注册快捷调用。"
    if _cmh_confirm "是否自动注册 cx/cenv/cmenu/ca 到 ~/.bashrc？"; then
      _cmh_install
      _cmh_warn "当前终端还没加载新的 ~/.bashrc；可执行 source ~/.bashrc，之后直接输入 cx。"
    else
      _cmh_info "本次继续运行菜单；以后可执行：bash ${BASH_SOURCE[0]} --install"
    fi
  fi
}

# -------------------------------
# conda 初始化 / 安装目录发现
# -------------------------------
_cmh_realpath() {
  # 兼容没有 readlink -f 的系统；Linux 上优先解析软链接。
  local p="$1"
  if [[ -z "$p" ]]; then
    return 1
  fi
  if command -v readlink >/dev/null 2>&1; then
    readlink -f "$p" 2>/dev/null && return 0
  fi
  if command -v python3 >/dev/null 2>&1; then
    python3 -c 'import os,sys; print(os.path.realpath(os.path.expanduser(sys.argv[1])))' "$p" 2>/dev/null && return 0
  fi
  printf '%s\n' "$p"
}

_cmh_add_candidate_file() {
  # 用法：_cmh_add_candidate_file array_name file_path
  local __arr="$1"
  local f="$2"
  local rf
  [[ -z "$f" ]] && return 0
  f="${f/#\~/$HOME}"
  rf="$(_cmh_realpath "$f" 2>/dev/null || printf '%s' "$f")"
  if [[ -f "$rf" || -x "$rf" ]]; then
    eval "$__arr+=(\"$rf\")"
  fi
}

_cmh_add_candidate_root() {
  # 用法：_cmh_add_candidate_root root_array sh_array exe_array root_path
  local __roots="$1" __shs="$2" __exes="$3" root="$4" rr
  [[ -z "$root" ]] && return 0
  root="${root/#\~/$HOME}"
  rr="$(_cmh_realpath "$root" 2>/dev/null || printf '%s' "$root")"
  [[ -d "$rr" ]] || return 0
  eval "$__roots+=(\"$rr\")"
  _cmh_add_candidate_file "$__shs" "$rr/etc/profile.d/conda.sh"
  _cmh_add_candidate_file "$__exes" "$rr/bin/conda"
  _cmh_add_candidate_file "$__exes" "$rr/condabin/conda"
}

_cmh_root_from_conda_exe() {
  local exe="$1" dir base
  [[ -z "$exe" ]] && return 1
  exe="$(_cmh_realpath "$exe" 2>/dev/null || printf '%s' "$exe")"
  dir="$(dirname "$exe")"
  case "$(basename "$dir")" in
    bin|condabin)
      base="$(dirname "$dir")"
      ;;
    *)
      base=""
      ;;
  esac
  [[ -n "$base" && -d "$base" ]] && printf '%s\n' "$base"
}

_cmh_scan_profile_for_conda() {
  # 从 bashrc/profile 里找 conda init 写过的真实路径。
  local f
  for f in "$HOME/.bashrc" "$HOME/.bash_profile" "$HOME/.profile" "/etc/profile"; do
    [[ -r "$f" ]] || continue
    grep -Eo '(/[^[:space:]]+/(miniconda3|anaconda3|miniforge3|mambaforge|conda)[^[:space:]]*/(bin|condabin)/conda)' "$f" 2>/dev/null || true
    grep -Eo '(/[^[:space:]]+/(miniconda3|anaconda3|miniforge3|mambaforge|conda)[^[:space:]]*/etc/profile\.d/conda\.sh)' "$f" 2>/dev/null || true
  done
  # /etc/profile.d 本身也可能已有全局 conda 初始化脚本。
  [[ -f /etc/profile.d/conda.sh ]] && printf '%s\n' /etc/profile.d/conda.sh
}

_cmh_unique_lines() {
  awk 'NF && !seen[$0]++ {print}'
}

_cmh_save_conda_root() {
  local root="$1"
  [[ -z "$root" ]] && return 1
  root="${root/#\~/$HOME}"
  root="$(_cmh_realpath "$root" 2>/dev/null || printf '%s' "$root")"
  if [[ -d "$root" ]]; then
    _cmh_mkdirs
    printf '%s\n' "$root" > "${_CMH_ROOT_FILE}"
    _cmh_log "conda root saved: ${root}"
    _cmh_ok "已记住 Conda 安装目录：$root"
    return 0
  fi
  _cmh_err "目录不存在：$root"
  return 1
}

_cmh_try_source_conda_sh() {
  local sh="$1"
  [[ -f "$sh" ]] || return 1
  # shellcheck disable=SC1090
  source "$sh" >/dev/null 2>&1 || return 1
  # 只要求 conda 被初始化成 shell function。
  # 某些 conda 版本下 `conda activate --help` 可能返回非 0，不能用它作为初始化失败依据。
  [[ "$(type -t conda 2>/dev/null || true)" == "function" ]]
}

_cmh_try_conda_exe_hook() {
  local exe="$1" hook base
  [[ -x "$exe" || -f "$exe" ]] || return 1
  exe="$(_cmh_realpath "$exe" 2>/dev/null || printf '%s' "$exe")"

  # 最稳妥：直接让 conda 输出 shell hook，eval 后 conda activate 会作用于当前 shell。
  hook="$($exe shell.bash hook 2>/dev/null || true)"
  if [[ -n "$hook" ]]; then
    eval "$hook" >/dev/null 2>&1 || true
    # 只要求 conda 被初始化成 shell function；不要再用 activate --help 做二次判定。
    if [[ "$(type -t conda 2>/dev/null || true)" == "function" ]]; then
      return 0
    fi
  fi

  # 兜底：用 conda info --base 找 base，再 source conda.sh。
  base="$($exe info --base 2>/dev/null || true)"
  if [[ -n "$base" && -f "$base/etc/profile.d/conda.sh" ]]; then
    _cmh_try_source_conda_sh "$base/etc/profile.d/conda.sh" && return 0
  fi

  return 1
}

_cmh_load_conda() {
  # 只有 conda 是 shell 函数时，conda activate 才能真正改变当前终端环境。
  # 如果已经是 function，直接认为可用；不同 conda 版本对 `conda activate --help` 的返回码不完全一致。
  if [[ "$(type -t conda 2>/dev/null || true)" == "function" ]]; then
    return 0
  fi

  local roots=()
  local sh_candidates=()
  local exe_candidates=()
  local p root f

  # 0) 用户手动保存过的安装目录，优先级最高。
  if [[ -s "${_CMH_ROOT_FILE}" ]]; then
    while IFS= read -r root; do
      _cmh_add_candidate_root roots sh_candidates exe_candidates "$root"
    done < "${_CMH_ROOT_FILE}"
  fi

  # 1) Conda 自己的环境变量。
  [[ -n "${CONDA_PREFIX:-}" ]] && _cmh_add_candidate_root roots sh_candidates exe_candidates "${CONDA_PREFIX}"
  [[ -n "${CONDA_EXE:-}" ]] && {
    _cmh_add_candidate_file exe_candidates "${CONDA_EXE}"
    root="$(_cmh_root_from_conda_exe "${CONDA_EXE}" 2>/dev/null || true)"
    [[ -n "$root" ]] && _cmh_add_candidate_root roots sh_candidates exe_candidates "$root"
  }
  [[ -n "${MAMBA_ROOT_PREFIX:-}" ]] && _cmh_add_candidate_root roots sh_candidates exe_candidates "${MAMBA_ROOT_PREFIX}"

  # 2) PATH 里已有 conda 可执行文件。
  p="$(type -P conda 2>/dev/null || true)"
  if [[ -n "$p" ]]; then
    _cmh_add_candidate_file exe_candidates "$p"
    root="$(_cmh_root_from_conda_exe "$p" 2>/dev/null || true)"
    [[ -n "$root" ]] && _cmh_add_candidate_root roots sh_candidates exe_candidates "$root"
  fi

  # 3) 常见目录。这里会解析软链接，例如 ~/miniconda3 -> /data/app/miniconda3。
  for root in \
    "$HOME/miniconda3" "$HOME/anaconda3" "$HOME/miniforge3" "$HOME/mambaforge" \
    "$HOME/.conda" "$HOME/.miniconda3" \
    "/opt/conda" "/opt/miniconda3" "/opt/anaconda3" "/opt/miniforge3" "/opt/mambaforge" \
    "/usr/local/conda" "/usr/local/miniconda3" "/usr/local/anaconda3" \
    "/data/conda" "/data/miniconda3" "/data/anaconda3" \
    "/mnt/conda" "/mnt/miniconda3" "/mnt/anaconda3"
  do
    _cmh_add_candidate_root roots sh_candidates exe_candidates "$root"
  done

  # 4) 从用户 shell 配置里解析 conda init 写入过的路径。
  while IFS= read -r f; do
    [[ -z "$f" ]] && continue
    case "$f" in
      */etc/profile.d/conda.sh)
        _cmh_add_candidate_file sh_candidates "$f"
        _cmh_add_candidate_root roots sh_candidates exe_candidates "${f%/etc/profile.d/conda.sh}"
        ;;
      */bin/conda|*/condabin/conda)
        _cmh_add_candidate_file exe_candidates "$f"
        root="$(_cmh_root_from_conda_exe "$f" 2>/dev/null || true)"
        [[ -n "$root" ]] && _cmh_add_candidate_root roots sh_candidates exe_candidates "$root"
        ;;
    esac
  done < <(_cmh_scan_profile_for_conda | _cmh_unique_lines)

  # 去重。
  mapfile -t sh_candidates < <(printf '%s\n' "${sh_candidates[@]}" | _cmh_unique_lines)
  mapfile -t exe_candidates < <(printf '%s\n' "${exe_candidates[@]}" | _cmh_unique_lines)

  # 先 source conda.sh，再尝试 conda 可执行文件的 shell hook。
  for p in "${sh_candidates[@]}"; do
    if _cmh_try_source_conda_sh "$p"; then
      root="${p%/etc/profile.d/conda.sh}"
      _cmh_save_conda_root "$root" >/dev/null 2>&1 || true
      return 0
    fi
  done

  for p in "${exe_candidates[@]}"; do
    if _cmh_try_conda_exe_hook "$p"; then
      root="$(_cmh_root_from_conda_exe "$p" 2>/dev/null || conda info --base 2>/dev/null || true)"
      [[ -n "$root" ]] && _cmh_save_conda_root "$root" >/dev/null 2>&1 || true
      return 0
    fi
  done

  return 1
}

_cmh_manual_set_conda_root() {
  _cmh_hr
  printf "%b手动设置 Conda 安装位置%b\n" "${_CMH_BOLD}" "${_CMH_RESET}"
  printf "可以输入以下任意一种：\n"
  printf "  1) Miniconda/Anaconda 根目录，例如 /data/apps/miniconda3\n"
  printf "  2) conda 可执行文件，例如 /data/apps/miniconda3/bin/conda\n"
  printf "  3) conda 初始化脚本，例如 /data/apps/miniconda3/etc/profile.d/conda.sh\n"
  printf "\n"
  local input root
  read -r -p "请输入路径；直接回车取消：" input
  [[ -z "$input" ]] && return 1
  input="${input/#\~/$HOME}"
  input="$(_cmh_realpath "$input" 2>/dev/null || printf '%s' "$input")"

  if [[ -d "$input" ]]; then
    root="$input"
  elif [[ "$input" == */etc/profile.d/conda.sh && -f "$input" ]]; then
    root="${input%/etc/profile.d/conda.sh}"
  elif [[ "$input" == */bin/conda || "$input" == */condabin/conda ]]; then
    root="$(_cmh_root_from_conda_exe "$input" 2>/dev/null || true)"
  else
    _cmh_err "路径格式不对或不存在：$input"
    return 1
  fi

  [[ -n "$root" ]] || { _cmh_err "无法从该路径推断 Conda 根目录。"; return 1; }
  _cmh_save_conda_root "$root" || return 1
  if _cmh_load_conda; then
    _cmh_ok "Conda 初始化成功。"
    return 0
  fi
  _cmh_err "已保存路径，但仍无法初始化 conda。请确认目录内存在 etc/profile.d/conda.sh 或 bin/conda。"
  return 1
}

_cmh_guess_conda_root_quiet() {
  # 只做“路径发现”，不初始化 conda。
  local p root rr

  if [[ -s "${_CMH_ROOT_FILE}" ]]; then
    while IFS= read -r root; do
      [[ -z "$root" ]] && continue
      root="${root/#\~/$HOME}"
      rr="$(_cmh_realpath "$root" 2>/dev/null || printf '%s' "$root")"
      [[ -d "$rr" ]] && { printf '%s\n' "$rr"; return 0; }
    done < "${_CMH_ROOT_FILE}"
  fi

  if [[ -n "${CONDA_EXE:-}" ]]; then
    root="$(_cmh_root_from_conda_exe "${CONDA_EXE}" 2>/dev/null || true)"
    [[ -n "$root" && -d "$root" ]] && { printf '%s\n' "$root"; return 0; }
  fi

  p="$(type -P conda 2>/dev/null || true)"
  if [[ -n "$p" ]]; then
    root="$(_cmh_root_from_conda_exe "$p" 2>/dev/null || true)"
    [[ -n "$root" && -d "$root" ]] && { printf '%s\n' "$root"; return 0; }
  fi

  for root in "$HOME/miniconda3" "$HOME/anaconda3" "$HOME/miniforge3"               "/opt/conda" "/opt/miniconda3" "/usr/local/miniconda3"               "/data/miniconda3" "/mnt/miniconda3"; do
    rr="$(_cmh_realpath "$root" 2>/dev/null || printf '%s' "$root")"
    if [[ -d "$rr" && ( -f "$rr/etc/profile.d/conda.sh" || -f "$rr/bin/conda" || -f "$rr/condabin/conda" ) ]]; then
      printf '%s\n' "$rr"
      return 0
    fi
  done
  return 1
}

_cmh_diagnose_conda_paths() {
  _cmh_hr
  printf "%bConda 发现诊断%b\n" "${_CMH_BOLD}" "${_CMH_RESET}"
  printf "当前 PATH 中 conda：%s\n" "$(type -P conda 2>/dev/null || printf '未找到')"
  printf "CONDA_EXE：%s\n" "${CONDA_EXE:-未设置}"
  printf "CONDA_PREFIX：%s\n" "${CONDA_PREFIX:-未设置}"
  printf "已保存安装目录："
  if [[ -s "${_CMH_ROOT_FILE}" ]]; then
    paste -sd ', ' "${_CMH_ROOT_FILE}"
  else
    printf "未设置\n"
  fi

  local guessed_root=""
  guessed_root="$(_cmh_guess_conda_root_quiet 2>/dev/null || true)"
  if [[ -n "$guessed_root" ]]; then
    printf "自动推断安装目录：%s\n" "$guessed_root"
    if [[ ! -s "${_CMH_ROOT_FILE}" ]]; then
      _cmh_save_conda_root "$guessed_root" >/dev/null 2>&1 || true
      printf "已自动记住安装目录：%s\n" "$guessed_root"
    fi
  fi

  printf "\n常见路径检查：\n"
  local root rr
  for root in "$HOME/miniconda3" "$HOME/anaconda3" "$HOME/miniforge3" "/opt/conda" "/opt/miniconda3" "/usr/local/miniconda3" "/data/miniconda3" "/mnt/miniconda3"; do
    rr="$(_cmh_realpath "$root" 2>/dev/null || printf '%s' "$root")"
    if [[ -e "$root" || -e "$rr" ]]; then
      printf "  %-32s -> %s\n" "$root" "$rr"
    fi
  done
}

_cmh_need_conda() {
  if _cmh_load_conda; then
    return 0
  fi

  local guessed_root=""
  guessed_root="$(_cmh_guess_conda_root_quiet 2>/dev/null || true)"
  if [[ -n "$guessed_root" ]]; then
    # 已经发现目录时，不再继续追问“手动指定”。先记住它，再给出真正的问题：初始化失败。
    _cmh_save_conda_root "$guessed_root" >/dev/null 2>&1 || true
    _cmh_err "已发现 Conda 安装目录，但初始化失败：$guessed_root"
    printf "通常是该目录下的 shell hook 或 conda.sh 无法被当前 bash 加载。你可以检查：\n"
    printf "  %s/etc/profile.d/conda.sh\n" "$guessed_root"
    printf "  %s/bin/conda shell.bash hook\n" "$guessed_root"
    printf "\n下面是诊断信息：\n"
    _cmh_diagnose_conda_paths
    return 1
  fi

  _cmh_err "未找到可用的 conda 初始化脚本或 conda 可执行文件。"
  printf "脚本现在会检查：PATH、CONDA_EXE、已保存路径、~/miniconda3 软链接解析、常见 /opt /usr/local /data /mnt 路径，以及 ~/.bashrc 里的 conda init 路径。\n"
  _cmh_diagnose_conda_paths

  if [[ -t 0 && -t 1 ]]; then
    printf "\n"
    if _cmh_confirm "是否现在手动指定 Conda 安装目录/conda 路径并记住？"; then
      _cmh_manual_set_conda_root && return 0
    fi
  fi

  printf "\n你也可以手动执行：\n"
  printf "  cx set-root\n"
  printf "然后输入真实路径，例如：/data/apps/miniconda3\n"
  return 1
}

# -------------------------------
# 环境列表与最近记录
# -------------------------------
_cmh_env_names_from_info() {
  # conda info --envs / conda env list 的格式在不同版本中略有差异；这里只取每行第一个“环境名或路径”字段。
  # prefix 环境可能直接以 /path/to/env 开头，此时保留路径，conda activate /path/to/env 可用。
  awk '
    /^#/ || NF==0 {next}
    {
      name=$1
      if (name == "*") name=$2
      if (name != "") print name
    }
  '
}

_cmh_env_names_from_dirs() {
  # 兜底：当 conda info/env list 输出异常时，直接扫描 base/envs 和 ~/.conda/envs。
  local base d e
  base="$(conda info --base 2>/dev/null || _cmh_guess_conda_root_quiet 2>/dev/null || true)"
  if [[ -n "$base" && -d "$base" ]]; then
    printf 'base
'
    d="$base/envs"
    if [[ -d "$d" ]]; then
      for e in "$d"/*; do
        [[ -d "$e" ]] || continue
        [[ -f "$e/conda-meta/history" || -d "$e/conda-meta" ]] || continue
        basename "$e"
      done
    fi
  fi

  d="$HOME/.conda/envs"
  if [[ -d "$d" ]]; then
    for e in "$d"/*; do
      [[ -d "$e" ]] || continue
      [[ -f "$e/conda-meta/history" || -d "$e/conda-meta" ]] || continue
      basename "$e"
    done
  fi
}

_cmh_env_names() {
  _cmh_need_conda >/dev/null 2>&1 || return 1
  {
    conda info --envs 2>/dev/null | _cmh_env_names_from_info || true
    conda env list 2>/dev/null | _cmh_env_names_from_info || true
    _cmh_env_names_from_dirs || true
  } | awk 'NF && !seen[$0]++ {print}'
}

_cmh_env_exists() {
  local env="$1"
  [[ -z "$env" ]] && return 1
  _cmh_env_names | grep -Fxq "$env"
}

_cmh_record_recent() {
  local env="$1"
  [[ -z "$env" ]] && return 0
  _cmh_mkdirs
  {
    printf '%s
' "$env"
    [[ -f "${_CMH_RECENT_FILE}" ]] && cat "${_CMH_RECENT_FILE}"
  } | awk 'NF && !seen[$0]++ {print}' | head -n 20 > "${_CMH_RECENT_FILE}.tmp"
  mv "${_CMH_RECENT_FILE}.tmp" "${_CMH_RECENT_FILE}"
  _cmh_log "recent: ${env}"
}

_cmh_print_recent_inline() {
  if [[ -s "${_CMH_RECENT_FILE}" ]]; then
    head -n 5 "${_CMH_RECENT_FILE}" | paste -sd ', ' -
  else
    printf "暂无"
  fi
}

_cmh_is_active_env() {
  local env="$1"
  [[ -z "$env" ]] && return 1
  [[ "$env" == "${CONDA_DEFAULT_ENV:-}" ]] && return 0
  [[ "$env" == "${CONDA_PREFIX:-}" ]] && return 0
  [[ -n "${CONDA_PREFIX:-}" && "$env" == "$(basename "$CONDA_PREFIX")" ]] && return 0
  return 1
}

_cmh_env_in_loaded_array() {
  local env="$1" x
  for x in "${_cmh_env_arr[@]:-}"; do
    [[ "$x" == "$env" ]] && return 0
  done
  return 1
}

_cmh_load_env_arrays() {
  mapfile -t _cmh_env_arr < <(_cmh_env_names)
  mapfile -t _cmh_recent_arr < <(
    if [[ -s "${_CMH_RECENT_FILE}" ]]; then
      awk 'NF && !seen[$0]++ {print}' "${_CMH_RECENT_FILE}" | head -n 6
    fi
  )
}

_cmh_list_envs_numbered_from_zero() {
  local i env
  if [[ ${#_cmh_env_arr[@]} -eq 0 ]]; then
    _cmh_warn "没有读取到 conda 环境。下面打印原始诊断，方便排查。"
    printf "
--- conda info --envs ---
"
    conda info --envs 2>&1 | sed 's/^/  /'
    printf "
--- conda env list ---
"
    conda env list 2>&1 | sed 's/^/  /'
    return 1
  fi

  printf "%b全部环境，数字从 0 开始%b
" "${_CMH_BOLD}" "${_CMH_RESET}"
  for i in "${!_cmh_env_arr[@]}"; do
    env="${_cmh_env_arr[$i]}"
    if _cmh_is_active_env "$env"; then
      printf "  [%d] %s  %b当前%b
" "$i" "$env" "${_CMH_GREEN}" "${_CMH_RESET}"
    else
      printf "  [%d] %s
" "$i" "$env"
    fi
  done
}

_cmh_list_recent_lettered() {
  local letters=(A B C D E F)
  local idx=0 env
  printf "
%b最近环境，字母 A-F%b
" "${_CMH_BOLD}" "${_CMH_RESET}"
  if [[ ${#_cmh_recent_arr[@]} -eq 0 ]]; then
    printf "  暂无
"
    return 0
  fi
  for env in "${_cmh_recent_arr[@]}"; do
    [[ $idx -ge 6 ]] && break
    if _cmh_env_in_loaded_array "$env"; then
      printf "  [%s] %s
" "${letters[$idx]}" "$env"
    else
      printf "  [%s] %s  %b已不存在或未被当前 conda 发现%b
" "${letters[$idx]}" "$env" "${_CMH_YELLOW}" "${_CMH_RESET}"
    fi
    ((idx++))
  done
}

_cmh_choose_env() {
  # 这个函数通过 stdout 返回最终环境名，所以所有界面输出必须走 stderr，
  # 否则 env="$(_cmh_choose_env)" 会把菜单内容吞掉，看起来就像“什么都没有”。
  local title="${1:-选择环境}"
  local n env idx upper

  _cmh_load_env_arrays
  {
    _cmh_hr
    printf "%b%s%b\n" "${_CMH_BOLD}" "$title" "${_CMH_RESET}"
  } >&2
  _cmh_list_envs_numbered_from_zero >&2 || return 1
  _cmh_list_recent_lettered >&2
  printf "\n" >&2

  read -r -p "输入环境序号 / 最近环境字母 / 环境名；q 返回：" n
  [[ "$n" == "q" || "$n" == "Q" || -z "$n" ]] && return 1

  if [[ "$n" =~ ^[0-9]+$ ]]; then
    if (( n >= 0 && n < ${#_cmh_env_arr[@]} )); then
      env="${_cmh_env_arr[$n]}"
    else
      _cmh_err "序号无效：$n" >&2
      return 1
    fi
  elif [[ "$n" =~ ^[A-Fa-f]$ ]]; then
    upper="${n^^}"
    case "$upper" in
      A) idx=0 ;; B) idx=1 ;; C) idx=2 ;; D) idx=3 ;; E) idx=4 ;; F) idx=5 ;;
    esac
    if (( idx >= 0 && idx < ${#_cmh_recent_arr[@]} )); then
      env="${_cmh_recent_arr[$idx]}"
    else
      _cmh_err "最近环境序号无效：$upper" >&2
      return 1
    fi
  else
    env="$n"
  fi

  if ! _cmh_env_exists "$env"; then
    _cmh_err "环境不存在或当前 conda 未发现：$env" >&2
    return 1
  fi

  printf '%s' "$env"
}
# -------------------------------
# 功能：激活、新建、删除、克隆
# -------------------------------
_cmh_activate_env() {
  _cmh_need_conda || return 1

  local env="${1:-}"
  if [[ -z "$env" ]]; then
    env="$(_cmh_choose_env "进入 / 切换 Conda 环境")" || return 1
    printf "\n"
  fi

  if ! _cmh_env_exists "$env"; then
    _cmh_err "环境不存在：$env"
    return 1
  fi

  _cmh_info "正在进入环境：$env"
  if conda activate "$env"; then
    _cmh_record_recent "$env"
    _cmh_ok "已进入：$env"
    _cmh_log "activate: ${env}"
    return 0
  else
    _cmh_err "进入失败：$env"
    _cmh_log "activate failed: ${env}"
    return 1
  fi
}

_cmh_deactivate_env() {
  _cmh_need_conda || return 1
  conda deactivate
  _cmh_ok "已执行 conda deactivate"
  _cmh_log "deactivate"
}

_cmh_create_env() {
  _cmh_need_conda || return 1
  local name pyver install_basic
  read -r -p "新环境名称：" name
  if [[ -z "$name" ]]; then
    _cmh_err "环境名不能为空。"
    return 1
  fi
  if _cmh_env_exists "$name"; then
    _cmh_err "环境已存在：$name"
    return 1
  fi

  _cmh_hr
  printf "选择 Python 版本：\n"
  printf "  1) 3.8\n"
  printf "  2) 3.9\n"
  printf "  3) 3.10\n"
  printf "  4) 3.11\n"
  printf "  5) 3.12\n"
  printf "  6) 3.13\n"
  printf "  0) 不指定 Python 版本\n"
  local c
  read -r -p "请选择 [0-6]：" c
  case "$c" in
    1) pyver="3.8" ;;
    2) pyver="3.9" ;;
    3) pyver="3.10" ;;
    4) pyver="3.11" ;;
    5) pyver="3.12" ;;
    6) pyver="3.13" ;;
    0|"") pyver="" ;;
    *) _cmh_err "选择无效。"; return 1 ;;
  esac

  if _cmh_confirm "是否顺便安装 pip ipython jupyter？"; then
    install_basic="pip ipython jupyter"
  else
    install_basic="pip"
  fi

  local cmd=(conda create -n "$name" -y)
  [[ -n "$pyver" ]] && cmd+=("python=${pyver}")
  [[ -n "$install_basic" ]] && cmd+=($install_basic)

  _cmh_info "执行：${cmd[*]}"
  if "${cmd[@]}"; then
    _cmh_ok "创建完成：$name"
    _cmh_log "create: ${name} python=${pyver:-default} basic=${install_basic}"
    if _cmh_confirm "是否立即进入该环境？"; then
      _cmh_activate_env "$name"
    fi
  else
    _cmh_err "创建失败：$name"
    _cmh_log "create failed: ${name}"
    return 1
  fi
}

_cmh_remove_env() {
  _cmh_need_conda || return 1
  local env
  env="$(_cmh_choose_env "删除 Conda 环境")" || return 1
  printf "\n"
  if [[ "$env" == "base" ]]; then
    _cmh_err "拒绝删除 base 环境。"
    return 1
  fi
  if _cmh_confirm "确认删除环境 ${env}？该操作不可逆"; then
    conda env remove -n "$env" -y && _cmh_ok "已删除：$env" && _cmh_log "remove: ${env}"
  else
    _cmh_warn "已取消删除。"
  fi
}

_cmh_clone_env() {
  _cmh_need_conda || return 1
  local src dst
  src="$(_cmh_choose_env "选择要克隆的源环境")" || return 1
  printf "\n"
  read -r -p "新环境名称：" dst
  [[ -z "$dst" ]] && { _cmh_err "新环境名不能为空。"; return 1; }
  if _cmh_env_exists "$dst"; then
    _cmh_err "目标环境已存在：$dst"
    return 1
  fi
  _cmh_info "执行：conda create -n ${dst} --clone ${src} -y"
  conda create -n "$dst" --clone "$src" -y && _cmh_ok "克隆完成：$src -> $dst" && _cmh_log "clone: ${src} -> ${dst}"
}


_cmh_create_clone_menu() {
  _cmh_need_conda || return 1
  _cmh_hr
  printf "%b新建 / 克隆环境%b\n" "${_CMH_BOLD}" "${_CMH_RESET}"
  printf "  [1] 新建环境，可选择 Python 版本\n"
  printf "  [2] 从已有环境克隆\n"
  printf "  [0] 返回主菜单\n"
  printf "\n"
  local c
  read -r -p "请选择 [0-2]：" c
  case "$c" in
    1) _cmh_create_env ;;
    2) _cmh_clone_env ;;
    0|"") return 0 ;;
    *) _cmh_err "选择无效：$c"; return 1 ;;
  esac
}

_cmh_shell_single_quote() {
  # 输出可安全放进单引号里的 shell 字符串。
  # 例如：abc'def -> 'abc'\''def'
  local x="$1"
  printf "'"
  printf "%s" "$x" | sed "s/'/'\\\\''/g"
  printf "'"
}

_cmh_find_conda_exe() {
  local root exe p

  if [[ -n "${CONDA_EXE:-}" ]]; then
    exe="$(_cmh_realpath "${CONDA_EXE}" 2>/dev/null || printf '%s' "${CONDA_EXE}")"
    [[ -f "$exe" || -x "$exe" ]] && { printf '%s\n' "$exe"; return 0; }
  fi

  root="$(_cmh_guess_conda_root_quiet 2>/dev/null || true)"
  if [[ -n "$root" ]]; then
    for p in "$root/bin/conda" "$root/condabin/conda"; do
      p="$(_cmh_realpath "$p" 2>/dev/null || printf '%s' "$p")"
      [[ -f "$p" || -x "$p" ]] && { printf '%s\n' "$p"; return 0; }
    done
  fi

  if _cmh_need_conda >/dev/null 2>&1; then
    root="$(conda info --base 2>/dev/null || true)"
    if [[ -n "$root" ]]; then
      for p in "$root/bin/conda" "$root/condabin/conda"; do
        p="$(_cmh_realpath "$p" 2>/dev/null || printf '%s' "$p")"
        [[ -f "$p" || -x "$p" ]] && { printf '%s\n' "$p"; return 0; }
      done
    fi
  fi

  exe="$(type -P conda 2>/dev/null || true)"
  if [[ -n "$exe" ]]; then
    exe="$(_cmh_realpath "$exe" 2>/dev/null || printf '%s' "$exe")"
    [[ -f "$exe" || -x "$exe" ]] && { printf '%s\n' "$exe"; return 0; }
  fi

  return 1
}

_cmh_bashrc_without_conda_init() {
  # stdout 输出删除官方 conda initialize 块之后的 bashrc 内容。
  # 只删除 Conda 官方块，不碰 conda-menu-helper 自己的 cx 注册块。
  local bashrc="${1:-${_CMH_BASHRC}}"
  if [[ ! -f "$bashrc" ]]; then
    return 0
  fi
  awk -v begin="${_CMH_CONDA_INIT_BEGIN}" -v end="${_CMH_CONDA_INIT_END}" '
    $0 == begin {skip=1; next}
    $0 == end {skip=0; next}
    skip != 1 {print}
  ' "$bashrc"
}

_cmh_has_conda_init_block() {
  [[ -f "${_CMH_BASHRC}" ]] && grep -Fq "${_CMH_CONDA_INIT_BEGIN}" "${_CMH_BASHRC}"
}

_cmh_conda_init_block() {
  local exe="$1" root qexe qsh qbin
  root="$(_cmh_root_from_conda_exe "$exe" 2>/dev/null || true)"
  [[ -z "$root" ]] && root="$(_cmh_guess_conda_root_quiet 2>/dev/null || true)"

  qexe="$(_cmh_shell_single_quote "$exe")"
  qsh="$(_cmh_shell_single_quote "$root/etc/profile.d/conda.sh")"
  qbin="$(_cmh_shell_single_quote "$root/bin")"

  cat <<EOS
${_CMH_CONDA_INIT_BEGIN}
# !! Contents within this block are managed by 'conda init' / conda-menu-helper !!
__conda_setup="\$(${qexe} 'shell.bash' 'hook' 2> /dev/null)"
if [ \$? -eq 0 ]; then
    eval "\$__conda_setup"
else
    if [ -f ${qsh} ]; then
        . ${qsh}
    else
        export PATH=${qbin}:"\$PATH"
    fi
fi
unset __conda_setup
${_CMH_CONDA_INIT_END}
EOS
}

_cmh_backup_bashrc() {
  _cmh_mkdirs
  if [[ -f "${_CMH_BASHRC}" ]]; then
    local b="${_CMH_BACKUP_DIR}/bashrc.$(date '+%Y%m%d_%H%M%S').bak"
    cp "${_CMH_BASHRC}" "$b"
    _cmh_info "已备份 ~/.bashrc 到：$b"
  fi
}

_cmh_conda_enable_init() {
  local exe tmp
  exe="$(_cmh_find_conda_exe 2>/dev/null || true)"
  if [[ -z "$exe" ]]; then
    _cmh_err "没有找到 conda 可执行文件，无法写入 conda 初始化块。"
    _cmh_info "可先在 [7] -> [5] 手动指定 Conda 安装目录。"
    return 1
  fi

  _cmh_backup_bashrc
  tmp="$(mktemp)"
  _cmh_bashrc_without_conda_init "${_CMH_BASHRC}" > "$tmp"
  {
    printf '\n'
    _cmh_conda_init_block "$exe"
  } >> "$tmp"
  cp "$tmp" "${_CMH_BASHRC}"
  rm -f "$tmp"

  _cmh_ok "已启用 Conda 初始化。新终端会自动加载 conda 命令。"
  _cmh_info "写入位置：${_CMH_BASHRC}"
  _cmh_info "当前终端已经由 cx 临时加载 conda；新配置可执行 source ~/.bashrc 或重开终端生效。"
  _cmh_log "conda init enable exe=${exe}"
}

_cmh_conda_disable_init() {
  if ! _cmh_has_conda_init_block; then
    _cmh_warn "~/.bashrc 中没有检测到官方 conda initialize 块，无需停用。"
    _cmh_info "这不会影响 cx；cx 会在需要时临时加载 conda。"
    return 0
  fi

  _cmh_backup_bashrc
  local tmp
  tmp="$(mktemp)"
  _cmh_bashrc_without_conda_init "${_CMH_BASHRC}" > "$tmp"
  cp "$tmp" "${_CMH_BASHRC}"
  rm -f "$tmp"

  _cmh_ok "已停用 Conda 自动初始化，并保留 cx 菜单注册。"
  _cmh_info "新终端默认不会自动加载 conda；需要环境时输入 cx 再选择即可。"
  _cmh_log "conda init disable"
}

_cmh_builtin_auto_activate_base() {
  _cmh_need_conda || return 1
  local val="$1"
  conda config --set auto_activate_base "$val"
  _cmh_ok "已设置 auto_activate_base=${val}。重新打开终端或 source ~/.bashrc 后生效。"
  _cmh_log "auto_activate_base=${val}"
}

_cmh_conda_command_loaded() {
  # 判断当前 shell 里 conda 是否已经可直接使用。
  # 注意：type -t 可能返回 function / file / alias。
  local t
  t="$(type -t conda 2>/dev/null || true)"
  [[ "$t" == "function" || "$t" == "file" || "$t" == "alias" ]]
}

_cmh_strip_path_under_root() {
  # 从当前 PATH 中移除 Conda 根目录下的 bin/condabin/scripts 等入口。
  # 只影响当前 shell，不修改任何配置文件。
  local root="$1" entry real_entry newpath sep
  [[ -z "$root" ]] && return 0
  root="$(_cmh_realpath "$root" 2>/dev/null || printf '%s' "$root")"
  [[ -d "$root" ]] || return 0

  newpath=""
  sep=""
  local oldifs="$IFS"
  IFS=':'
  for entry in $PATH; do
    IFS="$oldifs"
    [[ -z "$entry" ]] && continue
    real_entry="$(_cmh_realpath "$entry" 2>/dev/null || printf '%s' "$entry")"
    case "$real_entry" in
      "$root"/bin|"$root"/condabin|"$root"/Scripts|"$root"/Library/bin|"$root"/envs/*/bin)
        ;;
      *)
        newpath="${newpath}${sep}${entry}"
        sep=":"
        ;;
    esac
    IFS=':'
  done
  IFS="$oldifs"
  export PATH="$newpath"
  hash -r 2>/dev/null || true
}

_cmh_conda_runtime_on() {
  # 当前终端启用 conda：加载 hook / conda.sh。
  # 不写 ~/.bashrc，不进入 base。
  if _cmh_need_conda; then
    local root
    root="$(conda info --base 2>/dev/null || _cmh_guess_conda_root_quiet 2>/dev/null || true)"
    [[ -n "$root" ]] && _cmh_save_conda_root "$root" >/dev/null 2>&1 || true
    _cmh_ok "Conda ON"
    [[ -n "$root" ]] && printf "根目录：%s\n" "$root"
    _cmh_log "runtime conda on root=${root:-unknown}"
    return 0
  fi
  return 1
}

_cmh_conda_runtime_off() {
  # 当前终端停用 conda：退出已激活环境，移除本 shell 中的 conda 函数和 PATH 入口。
  # 不写 ~/.bashrc。
  local root shlvl guard
  root="$(conda info --base 2>/dev/null || _cmh_guess_conda_root_quiet 2>/dev/null || true)"

  if _cmh_conda_command_loaded; then
    shlvl="${CONDA_SHLVL:-0}"
    guard=0
    while [[ "${shlvl:-0}" =~ ^[0-9]+$ && "${shlvl:-0}" -gt 0 && "$guard" -lt 20 ]]; do
      conda deactivate >/dev/null 2>&1 || break
      shlvl="${CONDA_SHLVL:-0}"
      guard=$((guard + 1))
    done
  fi

  [[ -n "$root" ]] && _cmh_strip_path_under_root "$root"

  # conda activate 依赖 shell function；这里移除当前 shell 中的 function/alias。
  unset -f conda 2>/dev/null || true
  unalias conda 2>/dev/null || true

  unset CONDA_DEFAULT_ENV CONDA_PREFIX CONDA_PREFIX_1 CONDA_PREFIX_2 CONDA_PREFIX_3 CONDA_SHLVL CONDA_EXE CONDA_PYTHON_EXE _CE_M _CE_CONDA
  hash -r 2>/dev/null || true

  _cmh_ok "Conda OFF"
  _cmh_log "runtime conda off root=${root:-unknown}"
}

_cmh_current_conda_status() {
  local root status env
  if _cmh_conda_command_loaded; then
    status="ON"
  else
    status="OFF"
  fi
  env="${CONDA_DEFAULT_ENV:-未激活}"
  root="$(conda info --base 2>/dev/null || _cmh_guess_conda_root_quiet 2>/dev/null || true)"

  printf "状态：%s\n" "$status"
  printf "当前环境：%s\n" "$env"
  printf "根目录：%s\n" "${root:-未发现}"
}

_cmh_conda_onoff_help() {
  _cmh_hr
  printf "%bConda on/off 帮助%b\n" "${_CMH_BOLD}" "${_CMH_RESET}"
  cat <<'EOF'
ON  ：当前终端加载 conda hook，让 conda activate 可用；不修改 ~/.bashrc。
OFF ：当前终端退出 conda 环境，并移除本 shell 中的 conda 函数和 PATH 入口；不修改 ~/.bashrc。

切换环境时不需要先进入 base：
  cx 会自动 ON，然后直接 conda activate 目标环境。
EOF
}

_cmh_conda_onoff_menu() {
  _cmh_hr
  printf "%bConda on/off%b\n" "${_CMH_BOLD}" "${_CMH_RESET}"
  _cmh_current_conda_status
  printf "\n"
  printf "  [1] ON\n"
  printf "  [2] OFF\n"
  printf "  [h] 帮助\n"
  printf "  [0] 返回\n"
  printf "\n"
  local c
  read -r -p "请选择 [0-2/h]：" c
  case "$c" in
    1) _cmh_conda_runtime_on ;;
    2) _cmh_conda_runtime_off ;;
    h|H|\?) _cmh_conda_onoff_help ;;
    0|"") return 0 ;;
    *) _cmh_err "选择无效：$c"; return 1 ;;
  esac
}
_cmh_show_envs() {
  _cmh_need_conda || return 1
  _cmh_hr
  printf "%bConda 环境列表%b\n" "${_CMH_BOLD}" "${_CMH_RESET}"
  conda env list
}

_cmh_recent_menu() {
  _cmh_need_conda || return 1
  _cmh_hr
  printf "%b最近使用的环境%b\n" "${_CMH_BOLD}" "${_CMH_RESET}"
  if [[ ! -s "${_CMH_RECENT_FILE}" ]]; then
    _cmh_warn "暂无最近环境记录。"
    return 0
  fi

  local i=1 line choice env
  mapfile -t _cmh_recent_arr < "${_CMH_RECENT_FILE}"
  for line in "${_cmh_recent_arr[@]}"; do :; done 2>/dev/null || true
  # 上面的变量大小写无意义，保留兼容；下面正式输出。
  mapfile -t _cmh_recent_arr < <(awk 'NF {print}' "${_CMH_RECENT_FILE}" | head -n 20)
  for env in "${_cmh_recent_arr[@]}"; do
    if _cmh_env_exists "$env"; then
      printf "  %2d) %s\n" "$i" "$env"
    else
      printf "  %2d) %s %b已不存在%b\n" "$i" "$env" "${_CMH_YELLOW}[" "${_CMH_RESET}]"
    fi
    ((i++))
  done

  printf "\n"
  read -r -p "输入序号进入环境；d 清空记录；q 返回：" choice
  case "$choice" in
    q|Q|"") return 0 ;;
    d|D)
      : > "${_CMH_RECENT_FILE}"
      _cmh_ok "已清空最近记录。"
      return 0
      ;;
  esac

  if [[ "$choice" =~ ^[0-9]+$ ]] && (( choice >= 1 && choice <= ${#_cmh_recent_arr[@]} )); then
    env="${_cmh_recent_arr[$((choice-1))]}"
    if _cmh_env_exists "$env"; then
      _cmh_activate_env "$env"
    else
      _cmh_err "环境已不存在：$env"
    fi
  else
    _cmh_err "选择无效。"
  fi
}

# -------------------------------
# 镜像源管理
# -------------------------------
_cmh_backup_condarc() {
  _cmh_mkdirs
  if [[ -f "${HOME}/.condarc" ]]; then
    local b="${_CMH_BACKUP_DIR}/condarc.$(date '+%Y%m%d_%H%M%S').bak"
    cp "${HOME}/.condarc" "$b"
    _cmh_info "已备份当前 ~/.condarc 到：$b"
  fi
}

_cmh_write_condarc_for_base() {
  local base_url="$1"
  local name="$2"
  _cmh_backup_condarc
  cat > "${HOME}/.condarc" <<YAML
channels:
  - defaults
show_channel_urls: true
default_channels:
  - ${base_url}/pkgs/main
  - ${base_url}/pkgs/r
  - ${base_url}/pkgs/msys2
custom_channels:
  conda-forge: ${base_url}/cloud
  pytorch: ${base_url}/cloud
  nvidia: ${base_url}/cloud
YAML
  _cmh_ok "已切换 Conda 镜像源：${name}"
  _cmh_info "配置文件：${HOME}/.condarc"
  _cmh_log "mirror set: ${name} ${base_url}"
  if _cmh_need_conda >/dev/null 2>&1; then
    _cmh_info "清理索引缓存：conda clean -i -y"
    conda clean -i -y >/dev/null 2>&1 || true
  fi
}

_cmh_restore_official() {
  _cmh_backup_condarc
  cat > "${HOME}/.condarc" <<'YAML'
channels:
  - defaults
show_channel_urls: true
YAML
  _cmh_ok "已恢复为官方 defaults 源。"
  _cmh_log "mirror restore official"
  if _cmh_need_conda >/dev/null 2>&1; then
    conda clean -i -y >/dev/null 2>&1 || true
  fi
}

_cmh_speed_one() {
  local url="$1"
  local tool=""
  if command -v curl >/dev/null 2>&1; then
    tool="curl"
  elif command -v wget >/dev/null 2>&1; then
    tool="wget"
  else
    printf "NO_TOOL"
    return 1
  fi

  # 测试 linux-64 repodata，能更接近 conda 实际访问。
  local test_url="${url}/pkgs/main/linux-64/repodata.json"
  local t
  if [[ "$tool" == "curl" ]]; then
    t="$(curl -L -o /dev/null -s -w '%{time_total}' --connect-timeout 4 --max-time 10 "$test_url" 2>/dev/null || true)"
  else
    local start end
    start="$(date +%s%3N 2>/dev/null || date +%s)"
    wget -q --timeout=10 --tries=1 -O /dev/null "$test_url" >/dev/null 2>&1 || true
    end="$(date +%s%3N 2>/dev/null || date +%s)"
    t="$((end-start))ms"
  fi

  if [[ -z "$t" || "$t" == "0.000000" ]]; then
    printf "FAIL"
    return 1
  fi

  if [[ "$t" =~ ^[0-9]+\.[0-9]+$ ]]; then
    awk -v s="$t" 'BEGIN{printf "%.0fms", s*1000}'
  else
    printf "%s" "$t"
  fi
}

_cmh_mirror_help() {
  _cmh_hr
  printf "%b镜像源帮助%b\n" "${_CMH_BOLD}" "${_CMH_RESET}"
  cat <<'EOF'
测速对象：各源的 pkgs/main/linux-64/repodata.json。
用途：只判断当前网络下的可访问性和大致响应时间。
选择：测速完成后仍由你手动选择，不自动切换最快源。
恢复：选择 [9] 可写回官方 defaults 配置。
EOF
}

_cmh_mirror_menu() {
  _cmh_hr
  printf "%b镜像源测速与切换%b\n" "${_CMH_BOLD}" "${_CMH_RESET}"
  printf "\n"

  local names=(
    "清华 TUNA"
    "上海交大 SJTUG"
    "北外 BFSU"
    "阿里云 Aliyun"
    "中科大 USTC"
    "南京大学 NJU"
    "官方 repo.anaconda.com"
  )
  local urls=(
    "https://mirrors.tuna.tsinghua.edu.cn/anaconda"
    "https://mirror.sjtu.edu.cn/anaconda"
    "https://mirrors.bfsu.edu.cn/anaconda"
    "https://mirrors.aliyun.com/anaconda"
    "https://mirrors.ustc.edu.cn/anaconda"
    "https://mirrors.nju.edu.cn/anaconda"
    "https://repo.anaconda.com"
  )

  local i result
  for i in "${!names[@]}"; do
    printf "  [%d] %-22s %s ... " "$((i+1))" "${names[$i]}" "${urls[$i]}"
    result="$(_cmh_speed_one "${urls[$i]}")"
    if [[ "$result" == "FAIL" || "$result" == "NO_TOOL" ]]; then
      printf "%b%s%b\n" "${_CMH_RED}" "$result" "${_CMH_RESET}"
    else
      printf "%b%s%b\n" "${_CMH_GREEN}" "$result" "${_CMH_RESET}"
    fi
  done

  printf "\n"
  printf "  [8] 查看 ~/.condarc\n"
  printf "  [9] 恢复官方源\n"
  printf "  [h] 帮助\n"
  printf "  [0] 返回\n"
  printf "\n"
  local choice
  read -r -p "请选择 [0-9/h]：" choice
  case "$choice" in
    0|"") return 0 ;;
    8) _cmh_show_condarc; return 0 ;;
    9) _cmh_restore_official; return 0 ;;
    h|H|\?) _cmh_mirror_help; return 0 ;;
  esac

  if [[ "$choice" =~ ^[0-9]+$ ]] && (( choice >= 1 && choice <= ${#names[@]} )); then
    local idx=$((choice-1))
    if [[ "${names[$idx]}" == "官方 repo.anaconda.com" ]]; then
      _cmh_restore_official
    else
      _cmh_write_condarc_for_base "${urls[$idx]}" "${names[$idx]}"
    fi
  else
    _cmh_err "选择无效。"
  fi
}

_cmh_show_condarc() {
  _cmh_hr
  printf "%b当前 ~/.condarc%b\n" "${_CMH_BOLD}" "${_CMH_RESET}"
  if [[ -f "${HOME}/.condarc" ]]; then
    sed 's/^/  /' "${HOME}/.condarc"
  else
    _cmh_warn "~/.condarc 不存在。Conda 会使用默认配置。"
  fi
}

_cmh_show_logs() {
  _cmh_hr
  printf "%b隐藏日志文件%b\n" "${_CMH_BOLD}" "${_CMH_RESET}"
  printf "日志路径：%s\n" "${_CMH_LOG_FILE}"
  printf "最近环境：%s\n" "${_CMH_RECENT_FILE}"
  printf "配置备份：%s\n" "${_CMH_BACKUP_DIR}"
  printf "\n最近 30 条日志：\n"
  if [[ -s "${_CMH_LOG_FILE}" ]]; then
    tail -n 30 "${_CMH_LOG_FILE}" | sed 's/^/  /'
  else
    _cmh_warn "暂无日志。"
  fi
}

# -------------------------------
# 主菜单
# -------------------------------
_cmh_header() {
  _cmh_hr
  printf "%b%s%b  %b%s%b\n" "${_CMH_BOLD}" "${_CMH_NAME}" "${_CMH_RESET}" "${_CMH_DIM}" "v${_CMH_VERSION}" "${_CMH_RESET}"
  _cmh_hr
  printf "当前环境：%b%s%b\n" "${_CMH_GREEN}" "${CONDA_DEFAULT_ENV:-未激活}" "${_CMH_RESET}"
  printf "最近环境：%s\n" "$(_cmh_print_recent_inline)"
  printf "配置日志：%s\n" "${_CMH_LOG_FILE}"
  _cmh_hr
}

_cmh_menu_once() {
  _cmh_header
  printf "  [1] 切换环境\n"
  printf "  [2] 新建 / 克隆\n"
  printf "  [3] Conda on/off\n"
  printf "  [4] 删除环境\n"
  printf "  [5] 换源测速\n"
  printf "  [6] 恢复官方源\n"
  printf "  [7] 配置 / 日志\n"
  printf "  [h] 帮助\n"
  printf "  [0] 退出\n"
  printf "\n"
}
_cmh_config_log_menu() {
  _cmh_hr
  printf "%b配置 / 日志%b\n" "${_CMH_BOLD}" "${_CMH_RESET}"
  printf "  [1] 查看 ~/.condarc\n"
  printf "  [2] 查看日志\n"
  printf "  [3] Conda 诊断\n"
  printf "  [4] 原始环境列表\n"
  printf "  [5] 指定 Conda 目录\n"
  printf "  [6] 更新 cx 注册\n"
  printf "  [0] 返回\n"
  printf "\n"
  local c
  read -r -p "请选择 [0-6]：" c
  case "$c" in
    1) _cmh_show_condarc ;;
    2) _cmh_show_logs ;;
    3) _cmh_diagnose_conda_paths ;;
    4) _cmh_need_conda && { printf "\n--- conda info --envs ---\n"; conda info --envs; printf "\n--- conda env list ---\n"; conda env list; } ;;
    5) _cmh_manual_set_conda_root ;;
    6) _cmh_install ;;
    0|"") return 0 ;;
    *) _cmh_err "选择无效。" ;;
  esac
}
_cmh_main_menu() {
  _cmh_mkdirs
  _cmh_self_check_registration
  _cmh_need_conda || return 1
  _cmh_log "menu start"

  local choice
  while true; do
    printf "\n"
    _cmh_menu_once
    read -r -p "请选择 [0-7/h]：" choice
    printf "\n"
    case "$choice" in
      1) _cmh_activate_env; _cmh_pause ;;
      2) _cmh_create_clone_menu; _cmh_pause ;;
      3) _cmh_conda_onoff_menu; _cmh_pause ;;
      4) _cmh_remove_env; _cmh_pause ;;
      5) _cmh_mirror_menu; _cmh_pause ;;
      6) _cmh_restore_official; _cmh_pause ;;
      7) _cmh_config_log_menu; _cmh_pause ;;
      h|H|\?) _cmh_help; _cmh_pause ;;
      0|q|Q)
        _cmh_log "menu exit current_env=${CONDA_DEFAULT_ENV:-none}"
        _cmh_ok "已退出菜单。当前环境保持为：${CONDA_DEFAULT_ENV:-未激活}"
        break
        ;;
      *)
        _cmh_err "无效选择：$choice"
        _cmh_pause
        ;;
    esac
  done
}

_cmh_help() {
  cat <<HELP
${_CMH_NAME} v${_CMH_VERSION}

常用：
  cx              进入数字菜单
  ca              进入环境选择
  ca ENV_NAME     直接激活环境

安装 / 更新：
  bash conda_menu_helper.sh --install
  source ~/.bashrc

设计：
  - cx/ca 通过 source 运行，所以 conda activate 会保留在当前终端。
  - 菜单不执行 clear/reset，会保留上方输出。
  - Conda 可在 PATH、CONDA_EXE、软链接、~/.bashrc、/opt、/data、/mnt 等位置自动发现。
  - 进入目标环境不需要先进入 base；脚本会加载 conda hook 后直接 activate。

菜单：
  [1] 切换环境：数字选全部环境，A-F 选最近环境。
  [2] 新建 / 克隆：创建环境或从已有环境克隆。
  [3] Conda on/off：当前终端启用 / 停用 conda。
  [5] 换源测速：测速后手动选择镜像源。
  [7] 配置 / 日志：查看 condarc、日志、诊断信息、指定 Conda 目录。
HELP
}
_cmh_dispatch() {
  local cmd="${1:-menu}"
  case "$cmd" in
    --install|install)
      _cmh_install
      ;;
    -h|--help|help)
      _cmh_help
      ;;
    activate|a)
      shift || true
      _cmh_need_conda || return 1
      if [[ -n "${1:-}" ]]; then
        _cmh_activate_env "$1"
      else
        _cmh_activate_env
      fi
      ;;
    __env_names)
      _cmh_env_names
      ;;
    doctor|diagnose)
      _cmh_diagnose_conda_paths
      ;;
    set-root)
      _cmh_manual_set_conda_root
      ;;
    on|conda-on)
      _cmh_conda_runtime_on
      ;;
    off|conda-off)
      _cmh_conda_runtime_off
      ;;
    menu|"")
      _cmh_main_menu
      ;;
    *)
      # 兼容：cx myenv 直接激活环境；cx 仍进入菜单。
      _cmh_need_conda || return 1
      if _cmh_env_exists "$cmd"; then
        _cmh_activate_env "$cmd"
      else
        _cmh_err "未知参数或环境不存在：$cmd"
        _cmh_info "输入 cx 进入菜单，或 cx help 查看帮助。"
        return 1
      fi
      ;;
  esac
}

# -------------------------------
# 入口
# -------------------------------
if _cmh_is_sourced; then
  _cmh_dispatch "$@"
  # 被 source 时不能 exit，否则会关闭用户当前 shell。
  return $?
else
  case "${1:-}" in
    --install|install|-h|--help|help)
      _cmh_dispatch "$@"
      exit $?
      ;;
    *)
      _cmh_warn "你正在直接执行脚本。为了让 conda activate 保留在当前终端，请先安装后用 cx 调用。"
      printf "\n推荐执行：\n"
      printf "  bash %s --install\n" "$0"
      printf "  source ~/.bashrc\n"
      printf "  cx\n\n"
      printf "临时体验菜单也可以执行：\n"
      printf "  source %s\n" "$0"
      exit 0
      ;;
  esac
fi
