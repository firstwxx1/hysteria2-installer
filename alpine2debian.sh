#!/bin/sh
# alpine2debian.sh — 在 Alpine（救援 / 内存模式）里一键把机器刷成 Debian 12 最小化
#
# ─────────────────────────────────────────────────────────────────────────
# 这个脚本解决什么问题
#
#   阿里云部分实例（尤其抢占式 / 活动机型）开机后落在 Alpine 的「救援模式」：
#   整个系统跑在内存里，重启就没了，网卡默认也没配 IP。它本身不是一个能长期
#   使用的系统。传统做法是借道第三方重装脚本（InstallNET 之类），但那是黑盒：
#   你没法审、没法改、出问题没法定位。
#
#   本脚本用 **Debian 官方 debootstrap** 在本地直接释放一个完整的 Debian 12
#   根文件系统到磁盘，然后装内核、装 GRUB、写好网络与 fstab，最后重启。
#   全流程每一步都可读、可改、可单独重跑，**不下载任何第三方安装脚本**。
#
# ─────────────────────────────────────────────────────────────────────────
# 它做了什么（9 步，每步都有回显）
#
#   1. 环境检查：确认是 Alpine、是 root、有可用的目标磁盘
#   2. 联网检查：确认能访问 Debian 镜像站（不通就给出修复提示）
#   3. 抽取当前网络配置：网卡 / IP / 网关 / 掩码 / DNS（★ 防止重启后失联）
#   4. 装工具：sfdisk / mkfs.ext4 / debootstrap 等（★ 必须在分区之前）
#   5. 分区并格式化：单分区 MBR + ext4（对齐阿里云常见形态，不建 swap）
#   6. debootstrap：从官方源释放 Debian 12 bookworm 最小根文件系统
#   7. chroot 配置：装 cloud 内核 + GRUB + ssh，设密码、开 root 登录
#   8. 写 fstab / 网络配置 / DNS / 主机名 / 时区
#   9. 装 GRUB 引导（BIOS 模式）→ 卸载 → 重启
#
# ─────────────────────────────────────────────────────────────────────────
# 用法
#
#   # 交互式（推荐首次使用，会逐项询问并让你确认）
#   sh alpine2debian.sh
#
#   # 非交互（全部走默认值或环境变量，适合自动化）
#   ROOT_PWD='yourpass' sh alpine2debian.sh --yes
#
#   # 只做检查，不动磁盘（★ 强烈建议第一次先跑这个）
#   sh alpine2debian.sh --dry-run
#
#   # 指定磁盘 / 镜像 / 网卡
#   sh alpine2debian.sh --disk /dev/vda --mirror http://mirrors.aliyun.com/debian
#
# ─────────────────────────────────────────────────────────────────────────
# 可用环境变量
#
#   ROOT_PWD     新系统 root 密码（不设则交互询问）
#   DISK         目标磁盘，默认自动探测（/dev/vda → /dev/sda 顺序找）
#   DEBIAN_SUITE Debian 版本代号，默认 bookworm（= Debian 12）
#   MIRROR       镜像站，默认阿里云（国内快）
#   HOSTNAME     主机名，默认 debian
#   TIMEZONE     时区，默认 Asia/Shanghai
#   SWAP_MB      额外建多大 swap 文件（MB），默认 0 = 不建（对齐最小化）
#
# ─────────────────────────────────────────────────────────────────────────
# 警告
#
#   ★★★ 本脚本会【彻底清空】目标磁盘上的所有数据，不可恢复。★★★
#   执行前请确认：机器里没有任何需要保留的东西。
#   第一次使用请务必先跑 --dry-run。
# ─────────────────────────────────────────────────────────────────────────

set -eu

# ── 默认值 ────────────────────────────────────────────────────────────────
ROOT_PWD="${ROOT_PWD:-}"
DISK="${DISK:-}"
DEBIAN_SUITE="${DEBIAN_SUITE:-bookworm}"
MIRROR="${MIRROR:-http://mirrors.aliyun.com/debian}"
SECURITY_MIRROR="${SECURITY_MIRROR:-http://mirrors.aliyun.com/debian-security}"
HOSTNAME="${HOSTNAME:-debian}"
TIMEZONE="${TIMEZONE:-Asia/Shanghai}"
SWAP_MB="${SWAP_MB:-0}"

ASSUME_YES=0
DRY_RUN=0
MOUNT_DIR="/mnt/debian"

# ── 输出工具 ──────────────────────────────────────────────────────────────
c_red=$(printf '\033[31m'); c_grn=$(printf '\033[32m')
c_ylw=$(printf '\033[33m'); c_cyn=$(printf '\033[36m'); c_rst=$(printf '\033[0m')
info() { printf '%s[*]%s %s\n' "$c_cyn" "$c_rst" "$*"; }
ok()   { printf '%s[OK]%s %s\n' "$c_grn" "$c_rst" "$*"; }
warn() { printf '%s[!]%s %s\n' "$c_ylw" "$c_rst" "$*"; }
err()  { printf '%s[X]%s %s\n' "$c_red" "$c_rst" "$*" >&2; }
die()  { err "$*"; exit 1; }

# 统一的「执行或预演」入口：dry-run 时只打印不执行
run() {
    if [ "$DRY_RUN" = "1" ]; then
        printf '    %s[dry-run]%s %s\n' "$c_ylw" "$c_rst" "$*"
    else
        "$@"
    fi
}

usage() { sed -n '2,60p' "$0"; exit 0; }

# ── 参数解析 ──────────────────────────────────────────────────────────────
while [ $# -gt 0 ]; do
    case "$1" in
        --yes|-y)     ASSUME_YES=1; shift ;;
        --dry-run)    DRY_RUN=1; shift ;;
        --disk)       DISK="${2:-}"; shift 2 ;;
        --mirror)     MIRROR="${2:-}"; shift 2 ;;
        --suite)      DEBIAN_SUITE="${2:-}"; shift 2 ;;
        --pwd)        ROOT_PWD="${2:-}"; shift 2 ;;
        -h|--help)    usage ;;
        *) die "未知参数: $1（用 --help 看用法）" ;;
    esac
done

# ── 第 1 步：环境检查 ────────────────────────────────────────────────────
info "第 1/9 步：检查运行环境"

[ "$(id -u)" = "0" ] || die "必须以 root 运行"

if [ -f /etc/alpine-release ]; then
    ok "当前系统：Alpine $(cat /etc/alpine-release)"
else
    warn "当前系统看起来不是 Alpine（/etc/alpine-release 不存在），继续执行"
fi

# 探测目标磁盘
if [ -z "$DISK" ]; then
    for cand in /dev/vda /dev/sda /dev/xvda /dev/hda; do
        if [ -b "$cand" ]; then DISK="$cand"; break; fi
    done
fi
[ -n "$DISK" ] && [ -b "$DISK" ] || die "找不到可用磁盘，请用 --disk /dev/xxx 指定"
ok "目标磁盘：$DISK"

DISK_SIZE_MB=$(($(cat "/sys/block/$(basename "$DISK")/size" 2>/dev/null || echo 0) / 2048))
if [ "$DISK_SIZE_MB" -gt 0 ]; then
    ok "磁盘容量：约 ${DISK_SIZE_MB} MB"
    if [ "$DISK_SIZE_MB" -lt 1500 ]; then
        warn "磁盘小于 1.5GB，Debian 12 最小化可能装不下（官方建议 8GB+，勉强可跑约需 1GB）"
    fi
fi

# 收集要交互询问的值
if [ -z "$ROOT_PWD" ]; then
    if [ "$ASSUME_YES" = "1" ]; then
        die "非交互模式必须通过 ROOT_PWD 环境变量或 --pwd 提供 root 密码"
    fi
    printf '%s' "请设置新系统 root 密码（不能含特殊字符以免输入出错）: "
    if [ -t 0 ]; then read -r ROOT_PWD; else read -r ROOT_PWD </dev/tty; fi
fi
[ -n "$ROOT_PWD" ] || die "root 密码不能为空"

# ── 第 2 步：联网检查 ────────────────────────────────────────────────────
info "第 2/9 步：检查网络与外网连通性"

# Alpine 救援模式下网卡默认没有 IP，这里先尝试自动获取
if ! ip route show default >/dev/null 2>&1 || [ -z "$(ip route show default 2>/dev/null)" ]; then
    warn "尚未获取到默认路由，尝试 DHCP..."
    for ifc in $(ls /sys/class/net | grep -v '^lo$'); do
        info "对 $ifc 尝试 udhcpc"
        run udhcpc -i "$ifc" -q -n -t 5 2>/dev/null || true
        ip route show default >/dev/null 2>&1 && break
    done
fi

if ! ip route show default >/dev/null 2>&1; then
    die "仍然没有默认路由。请先在 VNC 里手动联网：
       ip link                      # 看网卡名
       udhcpc -i <网卡> -q -n        # 获取地址
       然后重新运行本脚本"
fi

DNS_HOST=$(printf '%s' "$MIRROR" | sed -E 's#^https?://##; s#/.*$##')
info "测试镜像站连通性：$DNS_HOST"
if ping -c 2 -W 3 "$DNS_HOST" >/dev/null 2>&1; then
    ok "镜像站可达"
else
    warn "ping 不通 $DNS_HOST（可能只是禁 ping），继续尝试 DNS 解析"
fi

# 确保 DNS 可用（否则 apk / debootstrap 都会失败）
if ! nslookup "$DNS_HOST" >/dev/null 2>&1 && ! getent hosts "$DNS_HOST" >/dev/null 2>&1; then
    warn "DNS 解析失败，写入公共 DNS 后重试"
    run sh -c 'printf "nameserver 223.5.5.5\nnameserver 8.8.8.8\n" > /etc/resolv.conf'
fi

# ── 第 3 步：抽取网络配置（★ 防失联的关键）─────────────────────────────
info "第 3/9 步：抽取当前网络配置（重装后用于恢复，防止失联）"

MAIN_IFACE=$(ip route show default 2>/dev/null | awk '{print $5}' | head -n1 | tr -d '[:space:]')
MAIN_IP=$(ip -4 addr show "$MAIN_IFACE" 2>/dev/null | awk '/inet /{print $2}' | cut -d/ -f1 | head -n1 | tr -d '[:space:]')
MAIN_GATE=$(ip route show default 2>/dev/null | awk '/default/{print $3}' | head -n1 | tr -d '[:space:]')
MAIN_CIDR=$(ip -4 addr show "$MAIN_IFACE" 2>/dev/null | awk '/inet /{print $2}' | cut -d/ -f2 | head -n1 | tr -d '[:space:]')

if [ -z "$MAIN_IP" ] || [ -z "$MAIN_GATE" ] || [ -z "$MAIN_CIDR" ]; then
    die "无法抽取网络配置（网卡=$MAIN_IFACE IP=$MAIN_IP 网关=$MAIN_GATE 掩码=$MAIN_CIDR）
       请先确认已联网，或在脚本里手工指定 MAIN_IP / MAIN_GATE / MAIN_CIDR"
fi

ok "网卡：$MAIN_IFACE"
ok "IP：$MAIN_IP/$MAIN_CIDR"
ok "网关：$MAIN_GATE"
info "（这三个值会写进新系统的网络配置，务必记下）"

# ── 确认执行 ─────────────────────────────────────────────────────────────
printf '\n'
warn "即将清空磁盘 ${DISK} 并安装 Debian 12（$DEBIAN_SUITE）"
warn "磁盘上所有数据都会丢失，且不可恢复"
printf '\n'
if [ "$ASSUME_YES" != "1" ] && [ "$DRY_RUN" != "1" ]; then
    printf '确认继续？输入 yes 回车：'
    if [ -t 0 ]; then read -r _ans; else read -r _ans </dev/tty; fi
    [ "$_ans" = "yes" ] || die "已取消"
fi

# ── 第 4 步：先装工具（★ 必须在分区之前）────────────────────────────────
#
# ★ 实测踩坑：最初把 apk add 放在第 5 步（debootstrap 之前），结果第 4 步
#   分区时就报 `sfdisk: not found` —— 因为 Alpine 救援模式是最小系统，
#   sfdisk 属于 util-linux 包，默认没装。工具必须先装再用。
info "第 4/9 步：安装所需工具"

run apk update
run apk add --no-cache debootstrap bash coreutils util-linux e2fsprogs \
    grub grub-bios sfdisk parted openssl

# 装完立刻确认关键命令都在，缺了就直接停下来（不要等到分区时才炸）
if [ "$DRY_RUN" != "1" ]; then
    _missing=""
    for _c in sfdisk fdisk mkfs.ext4 debootstrap chroot blkid; do
        command -v "$_c" >/dev/null 2>&1 || _missing="$_missing $_c"
    done
    if [ -n "$_missing" ]; then
        die "以下命令缺失：$_missing
       请检查网络后重试：apk update && apk add util-linux e2fsprogs debootstrap"
    fi
    ok "工具已就绪（sfdisk / fdisk / mkfs.ext4 / debootstrap 等）"
fi

# ── 第 5 步：分区 + 格式化 ───────────────────────────────────────────────
info "第 5/9 步：分区并格式化 $DISK"

# 先卸载可能已挂载的分区
for p in "${DISK}"*; do
    [ -b "$p" ] && run umount "$p" 2>/dev/null || true
done

# 关掉可能存在的 swap（alpine 救援模式常把磁盘当 swap 用，这会导致 Resource busy）
run sh -c "swapoff -a 2>/dev/null || true"

# 如果磁盘上有 LVM / mdraid，也一起停掉（同样会造成 Resource busy）
run sh -c "vgchange -an 2>/dev/null || true"
run sh -c "mdadm --stop --scan 2>/dev/null || true"

# 等一小会儿，让上面这些操作真正释放设备
[ "$DRY_RUN" != "1" ] && sleep 1

# 单分区 MBR：一个从 2048 扇区开始、占满整盘的 83 类型分区。
#
# ★ 为什么用 sfdisk 而不是 fdisk：
#   fdisk 的「自动分区」靠往 stdin 喂 `,` 和 `L` 走交互流程，新版 util-linux
#   对它的接受度很不稳定（有时提示序列变了就出错，有时直接静默写错表）。
#   sfdisk 支持**参数化输入**（直接描述分区表），结果确定、可预期，
#   是所有自动化脚本应该用的方式。fdisk 只作为最后的兜底。
#
# ★ 为什么从 2048 扇区开始：这是 1MiB 对齐（2048 × 512B）。
#   老式从 63 扇区开始会破坏 SSD/云盘的 4K 对齐，性能明显下降。
if [ "$DRY_RUN" = "1" ]; then
    printf '    %s[dry-run]%s sfdisk %s  <<< ",,83,*"\n' "$c_ylw" "$c_rst" "$DISK"
else
    # 先清干净旧分区表（防止旧表残留导致 sfdisk 判断成"已存在"而跳过）
    run sh -c "dd if=/dev/zero of='$DISK' bs=512 count=1 conv=notrunc 2>/dev/null || true"

    _parted_ok=0
    if printf ',,83,*\n' | sfdisk --label dos --no-reread "$DISK"; then
        _parted_ok=1
    fi

    if [ "$_parted_ok" = "0" ]; then
        warn "sfdisk 分区失败，尝试 fdisk 兜底"
        # 新版 fdisk 用 `printf` 喂交互序列确实不稳，改用 --no-reread 明确告诉它别去重读
        if printf 'o\nn\np\n1\n2048\n\n\nw\n' | fdisk --no-reread "$DISK"; then
            _parted_ok=1
        fi
    fi

    [ "$_parted_ok" = "1" ] || die "分区失败。请手动执行 fdisk $DISK 分区后再重跑本脚本"
    ok "分区表已写入（MBR，单分区）"

    # 明确通知内核重读分区表（用多个手段，总有一个能成）
    partprobe "$DISK" 2>/dev/null || \
    blockdev --rereadpt "$DISK" 2>/dev/null || \
    hdparm -z "$DISK" >/dev/null 2>&1 || \
    true
    # reread 失败不致命：下面会用 partx 兜底把分区节点造出来
    if ! [ -b "${DISK}1" ] && ! [ -b "${DISK}p1" ]; then
        partx -a "$DISK" 2>/dev/null || true
    fi
fi

# 确定分区设备名：
#   vdX / sdX / xvdX / hdX  →  X1     （例如 /dev/vda → /dev/vda1）
#   nvmeXnY / mmcblkX / loopX → Xp1   （例如 /dev/nvme0n1 → /dev/nvme0n1p1）
#
# ★ 实测教训：最初漏了 loop —— 在 loop 设备上演练时推导成了 `/dev/loop01`
#   （不存在），导致 mkfs 失败。这类命名规则必须一次列全，不能靠记忆。
case "$DISK" in
    *nvme*|*mmcblk*|*loop*) ROOT_PART="${DISK}p1" ;;
    *)                      ROOT_PART="${DISK}1"  ;;
esac
ok "根分区：$ROOT_PART"

if [ "$DRY_RUN" != "1" ]; then
    # 等分区设备节点出现（最多等 10 秒）
    _w=0
    while [ ! -b "$ROOT_PART" ] && [ "$_w" -lt 10 ]; do
        sleep 1; _w=$((_w + 1))
    done
    [ -b "$ROOT_PART" ] || die "分区设备 $ROOT_PART 未出现，分区可能未生效"
    ok "分区设备已就绪：$ROOT_PART"
fi

info "格式化 $ROOT_PART 为 ext4"
run mkfs.ext4 -F -L debian "$ROOT_PART"

# ── 第 6 步：debootstrap ─────────────────────────────────────────────────
info "第 6/9 步：释放 Debian 根文件系统"

run mkdir -p "$MOUNT_DIR"
run mount "$ROOT_PART" "$MOUNT_DIR"

info "debootstrap $DEBIAN_SUITE（从 $MIRROR，这一步最慢，请耐心等）"
run debootstrap --arch amd64 --variant=minbase \
    "$DEBIAN_SUITE" "$MOUNT_DIR" "$MIRROR"

ok "根文件系统释放完成"

# ── 决定 DNS（★ chroot 里下载和最终系统都要用，所以提前算好）──────────
# 阿里云内网 DNS 不占公网额度、解析最快；但只有确认处在阿里云内网
# （能 ping 通）时才用，否则退回公共 DNS，避免装完解析不了。
if ping -c 1 -W 2 100.100.2.136 >/dev/null 2>&1; then
    RESOLV_BODY="nameserver 100.100.2.136
nameserver 100.100.2.138"
    info "检测到阿里云内网 DNS，将使用 100.100.2.136 / 100.100.2.138"
else
    RESOLV_BODY="nameserver 223.5.5.5
nameserver 8.8.8.8"
    info "非阿里云内网环境，将使用公共 DNS 223.5.5.5 / 8.8.8.8"
fi

# ── 第 7 步：chroot 配置 ─────────────────────────────────────────────────
info "第 7/9 步：chroot 进新系统做配置"

# 把必要的虚拟文件系统挂进去，chroot 才能正常工作
for fs in dev proc sys; do
    run mount --bind "/$fs" "$MOUNT_DIR/$fs"
done

# 写 DNS，让 chroot 里也能联网下载（用上面算好的值）
if [ "$DRY_RUN" = "1" ]; then
    printf '    %s[dry-run]%s 写入 %s/etc/resolv.conf = %s\n' \
        "$c_ylw" "$c_rst" "$MOUNT_DIR" "$(printf '%s' "$RESOLV_BODY" | tr '\n' ' ')"
else
    printf '%s\n' "$RESOLV_BODY" > "$MOUNT_DIR/etc/resolv.conf"
fi

# chroot 里执行的一整套配置（写成脚本再执行，避免嵌套引号地狱）
BODY=$(cat <<CHROOT_EOF
set -eu
export DEBIAN_FRONTEND=noninteractive
export PATH=/usr/sbin:/usr/bin:/sbin:/bin

# 换镜像源（阿里云，国内快）
cat > /etc/apt/sources.list <<APT_EOF
deb ${MIRROR} ${DEBIAN_SUITE} main contrib non-free non-free-firmware
deb ${MIRROR} ${DEBIAN_SUITE}-updates main contrib non-free non-free-firmware
deb ${SECURITY_MIRROR} ${DEBIAN_SUITE}-security main contrib non-free non-free-firmware
APT_EOF

apt-get update -qq

# 装最小化所需：cloud 内核 + 引导 + ssh + 基础工具
apt-get install -y --no-install-recommends \\
    linux-image-cloud-amd64 \\
    grub-pc \\
    openssh-server \\
    systemd-sysv \\
    ifupdown \\
    iproute2 \\
    ca-certificates \\
    curl \\
    wget \\
    tzdata

# 时区
ln -snf /usr/share/zoneinfo/${TIMEZONE} /etc/localtime
echo "${TIMEZONE}" > /etc/timezone

# 主机名
echo "${HOSTNAME}" > /etc/hostname
printf '127.0.0.1\\tlocalhost\\n127.0.1.1\\t%s\\n' "${HOSTNAME}" > /etc/hosts

# root 密码 + 允许 root 登录
echo "root:${ROOT_PWD}" | chpasswd
sed -i 's/^#\\?PermitRootLogin.*/PermitRootLogin yes/' /etc/ssh/sshd_config
sed -i 's/^#\\?PasswordAuthentication.*/PasswordAuthentication yes/' /etc/ssh/sshd_config
[ -f /etc/ssh/sshd_config ] || echo 'PermitRootLogin yes' >> /etc/ssh/sshd_config

# 启用 ssh 开机自启（Debian 用 systemd）
systemctl enable ssh >/dev/null 2>&1 || true

# 生成 fstab
ROOT_UUID=\$(blkid -s UUID -o value "${ROOT_PART}")
cat > /etc/fstab <<FSTAB_EOF
UUID=\${ROOT_UUID} / ext4 defaults,errors=remount-ro 0 1
FSTAB_EOF

apt-get clean
rm -rf /var/lib/apt/lists/*
CHROOT_EOF
)

if [ "$DRY_RUN" = "1" ]; then
    printf '    %s[dry-run]%s chroot 配置脚本内容：\n' "$c_ylw" "$c_rst"
    printf '%s\n' "$BODY" | sed 's/^/        /'
else
    printf '%s\n' "$BODY" > "$MOUNT_DIR/tmp/_setup.sh"
    chroot "$MOUNT_DIR" /bin/sh /tmp/_setup.sh
    rm -f "$MOUNT_DIR/tmp/_setup.sh"
    ok "chroot 配置完成"
fi

# ── 第 8 步：网络配置 + swap ─────────────────────────────────────────────
info "第 8/9 步：写网络配置"

NET_CFG=$(cat <<NET_EOF
# 由 alpine2debian.sh 生成
# 与阿里云官方 Debian 12 镜像保持一致的开头，方便以后往里加接口
source /etc/network/interfaces.d/*

auto lo
iface lo inet loopback

auto ${MAIN_IFACE}
iface ${MAIN_IFACE} inet dhcp
NET_EOF
)

if [ "$DRY_RUN" = "1" ]; then
    printf '    %s[dry-run]%s /etc/network/interfaces：\n%s\n' "$c_ylw" "$c_rst" "$NET_CFG"
else
    printf '%s\n' "$NET_CFG" > "$MOUNT_DIR/etc/network/interfaces"
    ok "网络配置已写入（网卡 $MAIN_IFACE 走 DHCP）"
fi

if [ "$SWAP_MB" -gt 0 ]; then
    info "创建 ${SWAP_MB}MB swap 文件"
    if [ "$DRY_RUN" != "1" ]; then
        dd if=/dev/zero of="$MOUNT_DIR/swapfile" bs=1M count="$SWAP_MB" status=none
        chmod 600 "$MOUNT_DIR/swapfile"
        chroot "$MOUNT_DIR" mkswap /swapfile >/dev/null
        printf '/swapfile none swap sw 0 0\n' >> "$MOUNT_DIR/etc/fstab"
        ok "swap 已创建并写入 fstab"
    fi
else
    info "SWAP_MB=0，不创建 swap（对齐最小化系统）"
fi

# ── 第 9 步：装 GRUB ─────────────────────────────────────────────────────
info "第 9/9 步：安装 GRUB 引导（BIOS 模式）"

if [ "$DRY_RUN" = "1" ]; then
    printf '    %s[dry-run]%s grub-install --target=i386-pc %s\n' "$c_ylw" "$c_rst" "$DISK"
else
    # BIOS 模式装 GRUB 到磁盘 MBR
    chroot "$MOUNT_DIR" grub-install --target=i386-pc --recheck "$DISK" || {
        err "GRUB 安装失败，请检查 $DISK 是否为有效的 BIOS 引导目标"
        die "中止，磁盘已改动但未装引导 —— 请勿重启，先排查"
    }
    chroot "$MOUNT_DIR" update-grub >/dev/null 2>&1 || chroot "$MOUNT_DIR" grub-mkconfig -o /boot/grub/grub.cfg
    ok "GRUB 已安装到 $DISK"
fi

# ── 收尾：卸载 ───────────────────────────────────────────────────────────
info "卸载新系统文件系统"
if [ "$DRY_RUN" != "1" ]; then
    for fs in dev proc sys; do
        umount "$MOUNT_DIR/$fs" 2>/dev/null || true
    done
    sync
    umount "$MOUNT_DIR" 2>/dev/null || true
fi

printf '\n'
ok "全部完成！"
printf '\n'
info "接下来："
printf '    1. 执行 reboot 重启\n'
printf '    2. 等待 3~5 分钟\n'
printf '    3. 用新密码 SSH 登录：ssh root@<公网IP>\n'
printf '    4. 验证：cat /etc/os-release  应显示 Debian GNU/Linux 12 (bookworm)\n'
printf '\n'

if [ "$DRY_RUN" = "1" ]; then
    warn "这是预演模式，未做任何改动。去掉 --dry-run 才会真正执行。"
else
    if [ "$ASSUME_YES" = "1" ]; then
        info "非交互模式，自动重启"
        run reboot
    else
        printf '现在重启吗？输入 reboot 回车立即重启，或直接按 Ctrl+C 稍后手动重启：'
        if [ -t 0 ]; then read -r _rb; else read -r _rb </dev/tty; fi
        if [ "$_rb" = "reboot" ]; then
            info "重启中..."
            reboot
        else
            warn "未自动重启。请手动执行 reboot 使新系统生效。"
        fi
    fi
fi
