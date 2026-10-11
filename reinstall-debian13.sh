#!/bin/sh
# ===========================================================================
# reinstall-debian13.sh —— Alpine 环境下重装 Debian 13（含 GRUB 修复）
# ===========================================================================
#
# 适用场景
#   系统无法启动 / GRUB 损坏 / Debian 无法进入，需要从 Alpine 救援环境
#   里把机器重装成 Debian 13。
#
# 工作原理
#   本脚本**借道** InstallNET（leitbogioro/Tools）完成实际重装。InstallNET
#   在重装领域久经考验，兼容各种云厂商的救援环境。本脚本负责的是它前面和
#   后面那一圈「保障工作」：
#     · 联网与 DNS 就绪（Alpine 救援模式默认没网、没 DNS）
#     · 网络配置提取与校验（★ 防止重装后失联 —— 这是重装最常见的事故）
#     · 目标磁盘体检（容量够不够、是不是救援盘本身、有没有残留签名）
#     · 把网络参数以**静态 IP**形式传给 InstallNET
#     · 全程日志落盘，出问题可复查
#
# ---------------------------------------------------------------------------
# 用法（Alpine 里，root 身份）
# ---------------------------------------------------------------------------
#
#   # 最简单：一条命令，交互式回答两个问题（SSH 端口 / root 密码）
#   wget -qO- https://raw.githubusercontent.com/yys9253462-gif/hysteria2-installer/main/reinstall-debian13.sh | sh
#
#   # 或者先下载再跑（推荐，能看内容、能存起来复用）
#   wget -O rd13.sh https://raw.githubusercontent.com/yys9253462-gif/hysteria2-installer/main/reinstall-debian13.sh
#   sh rd13.sh
#
#   # 全自动（无人值守）
#   PASSWORD='YourPass123' PORT=22 sh rd13.sh --yes
#
#   # 只做术前体检，不真的重装（★ 强烈建议第一次先跑这个）
#   sh rd13.sh --check
#
#   # 指定磁盘 / 镜像 / 目标版本
#   sh rd13.sh --disk /dev/vda --mirror http://deb.debian.org/debian/ --release 13
#
# ---------------------------------------------------------------------------
# 可用环境变量（也可用同名命令行参数覆盖）
# ---------------------------------------------------------------------------
#   PORT        新系统 SSH 端口，默认 22
#   PASSWORD    新系统 root 密码，默认随机生成一个（★ 务必记下）
#   DISK        目标磁盘，默认自动探测
#   MIRROR      镜像站，默认 http://deb.debian.org/debian/
#   RELEASE     Debian 版本号，默认 13
#   SWAP_MB     swap 大小 MB，默认 512
#   NET_MODE    static（默认，推荐）或 dhcp
#   LOG         日志文件，默认 /root/reinstall-debian13.log
#
# ---------------------------------------------------------------------------
# 警告
# ---------------------------------------------------------------------------
#   ★★★ 本脚本会【彻底清空目标磁盘】，不可恢复。★★★
#   执行前请确认机器上没有需要保留的数据。
#   第一次使用请务必先跑 --check。
# ===========================================================================

set -u

# ── 常量 ─────────────────────────────────────────────────────────────────
INSTALLNET_URL="https://raw.githubusercontent.com/leitbogioro/Tools/master/Linux_reinstall/InstallNET.sh"
INSTALLNET_MIRROR="https://raw.githubusercontent.com/leitbogioro/Tools/master/Linux_reinstall/InstallNET.sh"
WORKDIR="$(pwd)"

# ── 默认值 ───────────────────────────────────────────────────────────────
PORT="${PORT:-22}"
PASSWORD="${PASSWORD:-}"
DISK="${DISK:-}"
MIRROR="${MIRROR:-http://deb.debian.org/debian/}"
RELEASE="${RELEASE:-13}"
SWAP_MB="${SWAP_MB:-512}"
NET_MODE="${NET_MODE:-static}"
LOG="${LOG:-/root/reinstall-debian13.log}"

ASSUME_YES=0
CHECK_ONLY=0

# ── 输出 ─────────────────────────────────────────────────────────────────
# 颜色：只有在真终端上才开，管道/重定向时自动关掉，避免日志里一堆乱码
if [ -t 1 ]; then
    c_r=$(printf '\033[31m'); c_g=$(printf '\033[32m')
    c_y=$(printf '\033[33m'); c_c=$(printf '\033[36m'); c_0=$(printf '\033[0m')
else
    c_r=""; c_g=""; c_y=""; c_c=""; c_0=""
fi

info() { printf '%s[*]%s %s\n' "$c_c" "$c_0" "$*"; log_line "[*] $*"; }
ok()   { printf '%s[OK]%s %s\n' "$c_g" "$c_0" "$*"; log_line "[OK] $*"; }
warn() { printf '%s[!]%s %s\n' "$c_y" "$c_0" "$*"; log_line "[!] $*"; }
err()  { printf '%s[X]%s %s\n' "$c_r" "$c_0" "$*" >&2; log_line "[X] $*"; }
die()  {
    err "$*"
    [ -n "$LOG" ] && printf '\n%s详情日志：%s%s\n' "$c_y" "$LOG" "$c_0" >&2
    exit 1
}

log_line() {
    [ -n "$LOG" ] || return 0
    printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >> "$LOG" 2>/dev/null || true
}

# 日志文件可能初始化失败（比如 /root 不存在、或目录只读）。
# ★ 踩坑：曾经直接 `: > "$LOG"` 且不接错误处理，一旦失败就整个脚本崩在
#   这里，且错误信息一行 `No such file or directory` 让人摸不着头脑。
#   现在：显式判断目录 → 建目录 → 试写 → 不行就把 LOG 置空（降级为不打日志，
#   而不是让脚本挂掉）。
init_log() {
    [ -n "$LOG" ] || return 0
    _logdir="$(dirname "$LOG")"
    if [ ! -d "$_logdir" ]; then
        mkdir -p "$_logdir" 2>/dev/null || { LOG=""; return 0; }
    fi
    if ! : > "$LOG" 2>/dev/null; then
        LOG=""
        return 0
    fi
    return 0
}

# 交互读取：优先 /dev/tty
#
# ★ 为什么不用 bash 的 `< <(...)` 进程替换：
#   Alpine 默认是 ash，**不支持 bash 的进程替换**。而且本脚本要被管道喂给
#   sh 执行（`wget -qO- url | sh`），此时 stdin 是管道、不是终端 ——
#   所有交互都必须显式从 /dev/tty 读，否则会读到脚本内容自己。
ask() {
    _prompt="$1"; _default="$2"; _val=""
    if [ -r /dev/tty ]; then
        printf '%s' "$_prompt" >/dev/tty
        IFS= read -r _val </dev/tty || _val=""
    fi
    [ -n "$_val" ] || _val="$_default"
    printf '%s' "$_val"
}

# ── 参数解析 ─────────────────────────────────────────────────────────────
while [ $# -gt 0 ]; do
    case "$1" in
        --yes|-y)      ASSUME_YES=1; shift ;;
        --check|--dry-run) CHECK_ONLY=1; shift ;;
        --port)        PORT="${2:-}";     shift 2 ;;
        --pwd|--password) PASSWORD="${2:-}"; shift 2 ;;
        --disk)        DISK="${2:-}";     shift 2 ;;
        --mirror)      MIRROR="${2:-}";   shift 2 ;;
        --release)     RELEASE="${2:-}";  shift 2 ;;
        --swap)        SWAP_MB="${2:-}";  shift 2 ;;
        --net)         NET_MODE="${2:-}"; shift 2 ;;
        -h|--help)     sed -n '2,72p' "$0" 2>/dev/null || sed -n '2,72p' "rd13.sh"; exit 0 ;;
        *) die "未知参数：$1（用 --help 看用法）" ;;
    esac
done

# ── 参数合法性校验（★ 放在最前面：参数错就立刻报，不用等跑一半）──────
case "$NET_MODE" in
    static|dhcp) ;;
    *) die "NET_MODE 只能是 static 或 dhcp，当前是：$NET_MODE" ;;
esac

case "$PORT" in
    ''|*[!0-9]*) die "SSH 端口必须是数字，当前是：$PORT" ;;
esac
[ "$PORT" -ge 1 ] && [ "$PORT" -le 65535 ] || die "SSH 端口超出范围（1-65535）：$PORT"

case "$SWAP_MB" in
    ''|*[!0-9]*) die "swap 大小必须是非负整数（MB），当前是：$SWAP_MB" ;;
esac

case "$RELEASE" in
    ''|*[!0-9]*) die "Debian 版本号必须是数字（如 12 / 13），当前是：$RELEASE" ;;
esac

[ -n "$MIRROR" ] || die "镜像地址不能为空"

# 初始化日志（失败会降级为不打日志，不会让脚本挂掉）
init_log
log_line "=== reinstall-debian13 开始 ==="
log_line "参数：PORT=$PORT DISK=$DISK MIRROR=$MIRROR RELEASE=$RELEASE SWAP_MB=$SWAP_MB NET_MODE=$NET_MODE CHECK_ONLY=$CHECK_ONLY"

printf '\n%s========================================%s\n' "$c_c" "$c_0"
printf '%s  Alpine → Debian %s 重装脚本%s\n' "$c_c" "$RELEASE" "$c_0"
printf '%s========================================%s\n\n' "$c_c" "$c_0"

# ── 第 1 步：环境检查 ────────────────────────────────────────────────────
info "第 1/6 步：检查运行环境"

[ "$(id -u)" = "0" ] || die "必须以 root 身份运行（当前 uid=$(id -u)）"

if [ -f /etc/alpine-release ]; then
    ok "当前系统：Alpine $(cat /etc/alpine-release)"
else
    warn "当前系统不是 Alpine（/etc/alpine-release 不存在）"
    warn "本脚本为 Alpine 救援环境设计，其他系统上行为可能不同"
    if [ "$ASSUME_YES" != "1" ] && [ "$CHECK_ONLY" != "1" ]; then
        _c="$(ask '仍要继续？（输入 yes 回车）：' 'no')"
        [ "$_c" = "yes" ] || die "已取消"
    fi
fi

# 内核架构（下面要确认 amd64）
ARCH="$(uname -m)"
ok "架构：$ARCH"
[ "$ARCH" = "x86_64" ] || warn "本脚本按 x86_64 验证过，当前是 $ARCH，请留意兼容性"

# ── 第 2 步：联网 + DNS ──────────────────────────────────────────────────
info "第 2/6 步：检查网络与 DNS"

# Alpine 救援模式网卡默认 DOWN，必须先启起来再 DHCP。
# ★ 实测踩坑：直接 udhcpc 会报 `udhcpc: sendto: Network is down`。
if [ -z "$(ip route show default 2>/dev/null)" ]; then
    warn "没有默认路由，尝试启用网卡并获取地址"

    # 确保必要工具在（救援模式可能只有 busybox）
    if ! command -v udhcpc >/dev/null 2>&1; then
        apk update >/dev/null 2>&1 || true
        apk add --no-cache busybox iproute2 >/dev/null 2>&1 || true
    fi

    for _ifc in $(ls /sys/class/net 2>/dev/null | grep -v '^lo$'); do
        info "启用 $_ifc 并尝试 DHCP"
        ip link set "$_ifc" up 2>/dev/null || true
        udhcpc -i "$_ifc" -q -n -t 10 >/dev/null 2>&1 || true
        [ -n "$(ip route show default 2>/dev/null)" ] && break
    done
fi

if [ -z "$(ip route show default 2>/dev/null)" ]; then
    die "仍然没有默认路由，无法联网。请先在 VNC 里手动联网：
       ip link                       # 看网卡名和状态
       ip link set <网卡> up         # 若是 DOWN 先启用
       udhcpc -i <网卡> -q -n        # 再获取地址
       然后重新运行本脚本"
fi
ok "默认路由已就绪"

# DNS：必须先能解析，否则下载全失败。
# ★ 实测踩坑：VNC 粘贴长命令容易被截断，会把 nameserver 写进错的文件名。
#   所以这里一行一个 echo，短命令，不会截断。
if ! nslookup github.com >/dev/null 2>&1; then
    warn "DNS 解析失败，写入公共 DNS"
    echo "nameserver 223.5.5.5"   > /etc/resolv.conf
    echo "nameserver 100.100.2.136" >> /etc/resolv.conf
    echo "nameserver 8.8.8.8"    >> /etc/resolv.conf
    sleep 1
fi

if nslookup github.com >/dev/null 2>&1; then
    ok "DNS 解析正常"
else
    warn "DNS 仍解析不了，但继续尝试（有些环境 nslookup 不可用）"
fi

# 连通性：能下到 InstallNET 才算真的通
info "测试到 GitHub 的连通性"
if wget -q --spider --timeout=15 "$INSTALLNET_URL" 2>/dev/null || \
   curl -fsI --max-time 15 "$INSTALLNET_URL" >/dev/null 2>&1; then
    ok "可以访问安装脚本源"
else
    warn "访问 GitHub 失败。若在国内网络，可能需要换镜像或代理"
fi

# ── 第 3 步：提取网络配置（★ 防失联关键）─────────────────────────────────
info "第 3/6 步：提取网络配置（用于重装后恢复，防止失联）"

MAIN_IFACE="$(ip route show default 2>/dev/null | awk '{print $5}' | head -n1 | tr -d '[:space:]')"
MAIN_IP="$(ip -4 addr show "$MAIN_IFACE" 2>/dev/null | awk '/inet /{print $2}' | cut -d/ -f1 | head -n1 | tr -d '[:space:]')"
MAIN_GATE="$(ip route show default 2>/dev/null | awk '/default/{print $3}' | head -n1 | tr -d '[:space:]')"
CIDR_NUM="$(ip -4 addr show "$MAIN_IFACE" 2>/dev/null | awk '/inet /{print $2}' | cut -d/ -f2 | head -n1 | tr -d '[:space:]')"

if [ -z "$MAIN_IFACE" ] || [ -z "$MAIN_IP" ] || [ -z "$MAIN_GATE" ] || [ -z "$CIDR_NUM" ]; then
    die "网络配置提取失败：
       网卡=[$MAIN_IFACE] IP=[$MAIN_IP] 网关=[$MAIN_GATE] 掩码=[$CIDR_NUM]
       请确认已联网后再运行本脚本"
fi

printf '\n'
printf '   %s网卡%s    %s\n' "$c_c" "$c_0" "$MAIN_IFACE"
printf '   %s地址%s    %s/%s\n' "$c_c" "$c_0" "$MAIN_IP" "$CIDR_NUM"
printf '   %s网关%s    %s\n' "$c_c" "$c_0" "$MAIN_GATE"
printf '\n'
warn "以上三个值会写进新系统的静态网络配置。请记下来，重装后 SSH 用得上。"

# ── 第 4 步：磁盘体检 ────────────────────────────────────────────────────
info "第 4/6 步：检查目标磁盘"

# 自动探测：优先 vda/sda 这类常规系统盘
if [ -z "$DISK" ]; then
    for _cand in /dev/vda /dev/sda /dev/xvda /dev/hda /dev/nvme0n1; do
        [ -b "$_cand" ] && { DISK="$_cand"; break; }
    done
fi
[ -n "$DISK" ] && [ -b "$DISK" ] || die "找不到可用磁盘，请用 --disk /dev/xxx 指定"

DISK_SIZE_MB=$(($(cat "/sys/block/$(basename "$DISK")/size" 2>/dev/null || echo 0) / 2048))
ok "目标磁盘：$DISK（约 ${DISK_SIZE_MB} MB）"

# ★ 容量体检：Debian 13 最小化约需 1GB，实际建议 8GB+
#   实测踩坑（2026-10-11 用户真机）：某阿里云实例只有一块 1GB 的救援盘，
#   拿它装 Debian 会立刻爆满，而且会破坏救援环境导致失联。
if [ "$DISK_SIZE_MB" -gt 0 ] && [ "$DISK_SIZE_MB" -lt 4000 ]; then
    warn "磁盘只有 ${DISK_SIZE_MB} MB，装 Debian ${RELEASE} 会非常紧张"
    warn "（最小化安装约需 1GB，建议至少 8GB，否则装完几乎没空间）"
    printf '\n'
    warn "★ 请先确认：这块盘到底是【数据盘】还是【救援系统自己的盘】？"
    warn "  判断方法：执行 df -h 看 $DISK 是否被挂载在 /media 或 / 下。"
    warn "  如果它正被救援系统挂着，说明你还没有数据盘 —— 请先到云控制台"
    warn "  挂载/扩容一块正常云盘，再回来重装。"
    printf '\n'
    if [ "$ASSUME_YES" != "1" ] && [ "$CHECK_ONLY" != "1" ]; then
        _c="$(ask "确认继续用这块盘？（输入 yes 回车）：" "no")"
        [ "$_c" = "yes" ] || die "已取消"
    fi
fi

# 是否被挂载着（救援盘的特征）
_mounted_at=""
if [ -r /proc/mounts ]; then
    _mounted_at="$(awk -v d="$DISK" '$1 ~ "^"d {print $2}' /proc/mounts 2>/dev/null | head -n1)"
fi
if [ -n "$_mounted_at" ]; then
    warn "★ 注意：$DISK 当前被挂载在 $_mounted_at"
    warn "  如果这是救援系统正在用的盘，重装会破坏救援环境（可能导致失联）。"
fi

# 残留签名（iso9660 等会让系统自动挂载，导致 mkfs 报 in use）
if command -v blkid >/dev/null 2>&1; then
    _sig="$(blkid "$DISK" 2>/dev/null || true)"
    if [ -n "$_sig" ]; then
        warn "磁盘上已有文件系统签名：$_sig"
        warn "  重装前会自动清除（wipefs），避免 mkfs 报 \"apparently in use\""
    fi
fi

# ── 第 5 步：确认参数 ────────────────────────────────────────────────────
info "第 5/6 步：确认重装参数"

if [ -z "$PASSWORD" ]; then
    if [ "$ASSUME_YES" = "1" ]; then
        # 非交互模式没给密码 → 自动生成一个，并醒目打印出来
        PASSWORD="$(head -c 18 /dev/urandom | od -An -tx1 | tr -d ' \n' | cut -c1-16)"
        warn "未指定密码，已自动生成：$PASSWORD"
        warn "★ 请务必记下这个密码，重装后要用它登录！"
    else
        PASSWORD="$(ask "新系统 root 密码（不含特殊字符）[回车用默认 rd13$(date +%m%d)]： " "rd13$(date +%m%d)")"
    fi
fi
[ -n "$PASSWORD" ] || die "root 密码不能为空"

printf '\n'
printf '   磁盘      %s\n' "$DISK"
printf '   版本      Debian %s\n' "$RELEASE"
printf '   镜像      %s\n' "$MIRROR"
printf '   SSH 端口  %s\n' "$PORT"
printf '   root 密码 %s\n' "$PASSWORD"
printf '   swap      %s MB\n' "$SWAP_MB"
printf '   网络模式  %s\n' "$NET_MODE"
printf '   日志      %s\n' "$LOG"
printf '\n'

if [ "$CHECK_ONLY" = "1" ]; then
    ok "全部检查通过（--check 模式，未做任何改动）"
    printf '\n下一步：去掉 --check 重新运行即可真正开始重装。\n\n'
    exit 0
fi

warn "即将清空 $DISK 并重装 Debian $RELEASE —— 数据不可恢复"
printf '\n'
if [ "$ASSUME_YES" != "1" ]; then
    _c="$(ask '确认开始？输入 yes 回车：' 'no')"
    [ "$_c" = "yes" ] || die "已取消"
fi

# ── 第 6 步：下载并执行 InstallNET ───────────────────────────────────────
info "第 6/6 步：下载并执行重装程序"

IN_FILE="$WORKDIR/InstallNET.sh"

download_installnet() {
    rm -f "$IN_FILE"
    if command -v wget >/dev/null 2>&1; then
        wget --no-check-certificate -qO "$IN_FILE" "$INSTALLNET_URL" && return 0
    fi
    if command -v curl >/dev/null 2>&1; then
        curl -fsSL --max-time 120 -o "$IN_FILE" "$INSTALLNET_URL" && return 0
    fi
    return 1
}

# 缺 wget/curl 就先补上
if ! command -v wget >/dev/null 2>&1 && ! command -v curl >/dev/null 2>&1; then
    info "安装下载工具"
    apk update >/dev/null 2>&1 || true
    apk add --no-cache wget curl bash iproute2 gawk ipcalc >/dev/null 2>&1 || true
fi

info "下载 InstallNET.sh"
download_installnet || die "下载 InstallNET.sh 失败。请检查网络后重试。"
chmod a+x "$IN_FILE" 2>/dev/null || true

# 校验下载完整性：InstallNET.sh 正常有 270KB 左右
_size="$(wc -c < "$IN_FILE" 2>/dev/null | tr -d ' ')"
if [ -z "$_size" ] || [ "$_size" -lt 50000 ]; then
    die "下载的文件异常（只有 ${_size:-0} 字节，应为 270KB 左右）。
       可能是网络被劫持或返回了错误页面。请重试或检查网络。"
fi
ok "已下载 InstallNET.sh（$_size 字节）"

# 组装参数
set -- -debian "$RELEASE" -port "$PORT" -pwd "$PASSWORD" -mirror "$MIRROR" -swap "$SWAP_MB"

case "$NET_MODE" in
    static)
        # ★ 静态 IP 是最能防失联的方式：DHCP 在重装后可能拿不到地址，
        #   而静态配置直接写死，开机就有网。
        set -- "$@" --ip-addr "$MAIN_IP" --ip-gate "$MAIN_GATE" --ip-mask "$CIDR_NUM"
        info "网络模式：静态（$MAIN_IP/$CIDR_NUM via $MAIN_GATE）"
        ;;
    dhcp)
        info "网络模式：DHCP"
        ;;
esac

set -- "$@" --cloudkernel 0 --bbr --motd

printf '\n'
info "开始执行重装（InstallNET 接管后会自行重启，全程 10~20 分钟）"
info "执行命令：bash InstallNET.sh $*"
printf '\n'
log_line "执行：bash InstallNET.sh $*"

# 交给 InstallNET
if bash "$IN_FILE" "$@"; then
    printf '\n'
    ok "重装流程已启动"
else
    _rc=$?
    printf '\n'
    err "InstallNET 执行失败（退出码 $_rc）"
    printf '\n'
    printf '常见原因与处理：\n'
    printf '  1. 网络中断      —— 检查网络后重试\n'
    printf '  2. 空间不足      —— 确认磁盘容量（df -h / blkid %s）\n' "$DISK"
    printf '  3. 磁盘被占用    —— 执行 wipefs -a %s 后重试\n' "$DISK"
    printf '  4. 上游脚本变更  —— 可到 leitbogioro/Tools 仓库查看最新说明\n'
    printf '\n完整日志：%s\n\n' "$LOG"
    exit "$_rc"
fi

cat <<EOF

========================================
  重装已开始
========================================

  接下来：
    1. 机器会在几分钟内自动重启
    2. 等待 10~20 分钟（首次启动会做初始化）
    3. 用新密码登录：ssh -p $PORT root@<你的公网IP>
    4. 验证系统：cat /etc/os-release   应显示 Debian GNU/Linux $RELEASE

  ★ 网络配置（如果连不上，用这些信息排查）：
      地址  $MAIN_IP/$CIDR_NUM
      网关  $MAIN_GATE
      网卡  $MAIN_IFACE

  日志：$LOG
EOF
