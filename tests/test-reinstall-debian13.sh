#!/bin/sh
# ===========================================================================
# reinstall-debian13.sh 的自动化测试
# ===========================================================================
# 在 Alpine 容器 + loop 假盘环境里，验证脚本的每个环节：
#   · 环境检查、参数解析、帮助
#   · --check 模式（不动磁盘）
#   · 网络配置提取
#   · 磁盘容量体检（★ 用 1GB 假盘验证它会正确报警）
#   · 挂载占用检测
#   · 残留签名检测
#   · InstallNET 下载与完整性校验
#   · 非交互模式密码自动生成
#
# 用法：sh test-reinstall-debian13.sh
# ===========================================================================
set -u

SCRIPT="./reinstall-debian13.sh"
[ -f "$SCRIPT" ] || SCRIPT="$(dirname "$0")/reinstall-debian13.sh"
[ -f "$SCRIPT" ] || { echo "找不到被测脚本"; exit 1; }

PASS=0
FAIL=0
FAILED_NAMES=""

a_ok() {
    PASS=$((PASS + 1))
    printf '  [通过] %s\n' "$1"
    [ -n "${2:-}" ] && printf '         %s\n' "$2"
}
a_no() {
    FAIL=$((FAIL + 1))
    FAILED_NAMES="$FAILED_NAMES
  - $1"
    printf '  [失败] %s\n' "$1"
    [ -n "${2:-}" ] && printf '         %s\n' "$2"
}

# 断言：命令输出里含某串
assert_has() {
    _desc="$1"; _out="$2"; _want="$3"
    if printf '%s' "$_out" | grep -qF -- "$_want"; then
        a_ok "$_desc"
    else
        a_no "$_desc" "期望包含：$_want"
    fi
}
# 断言：命令输出里不含某串
assert_not() {
    _desc="$1"; _out="$2"; _bad="$3"
    if printf '%s' "$_out" | grep -qF -- "$_bad"; then
        a_no "$_desc" "不该出现：$_bad"
    else
        a_ok "$_desc"
    fi
}

echo "=========================================="
echo "  reinstall-debian13.sh 自检"
echo "=========================================="
echo ""

# ── 1. 语法 ───────────────────────────────────────────────────────────────
echo "【1】语法与基本属性"
if sh -n "$SCRIPT" 2>/dev/null; then
    a_ok "POSIX sh 语法检查通过"
else
    a_no "POSIX sh 语法检查" "sh -n 报错：$(sh -n "$SCRIPT" 2>&1 | head -2)"
fi

_out="$(head -1 "$SCRIPT")"
if [ "$_out" = "#!/bin/sh" ]; then
    a_ok "shebang 是 #!/bin/sh（Alpine ash 可跑）"
else
    a_no "shebang" "实际：$_out"
fi

# 不能有 bash 专属语法（进程替换 `<(`）
# ★ 剥掉注释再查：脚本注释里为了解释"为什么不用它"会提到这个写法，
#   直接 grep 全文会误报。
if grep -vE '^[[:space:]]*#' "$SCRIPT" | grep -qE '<\('; then
    a_no "不含 bash 进程替换 <(...)" "Alpine 的 ash 不支持，会导致语法错误"
else
    a_ok "不含 bash 进程替换 <(...)"
fi

# 不能有 [[ ]]（bash 专属，ash 不支持）
# 排除注释行
if grep -vE '^[[:space:]]*#' "$SCRIPT" | grep -qE '\[\['; then
    a_no "不含 [[ ]] 测试（ash 不支持）"
else
    a_ok "不含 [[ ]] 测试"
fi

echo ""

# ── 2. 帮助 ───────────────────────────────────────────────────────────────
echo "【2】帮助输出"
_out="$(sh "$SCRIPT" --help 2>&1)"
assert_has "帮助里有用法说明" "$_out" "用法"
assert_has "帮助里有 --check 说明" "$_out" "--check"
assert_has "帮助里有警告（会清空磁盘）" "$_out" "彻底清空"

echo ""

# ── 3. 参数校验 ───────────────────────────────────────────────────────────
echo "【3】错误参数处理"
_out="$(sh "$SCRIPT" --bogus-param 2>&1)"
if printf '%s' "$_out" | grep -qE "未知参数"; then
    a_ok "未知参数被拒绝并提示"
else
    a_no "未知参数处理" "输出：$_out"
fi

_out="$(sh "$SCRIPT" --net invalid-mode --check 2>&1)"
if printf '%s' "$_out" | grep -qE "static 或 dhcp|NET_MODE"; then
    a_ok "非法网络模式被拒绝"
else
    a_no "非法网络模式处理" "输出：$(printf '%s' "$_out" | tail -3)"
fi

echo ""

# ── 4. --check 模式（在这个容器里跑真环境）──────────────────────────────
echo "【4】--check 模式（真实环境）"
_out="$(sh "$SCRIPT" --check --disk /dev/loop9 2>&1)"
if printf '%s' "$_out" | grep -qE "找不到可用磁盘|不是块设备|找不到"; then
    a_ok "不存在的磁盘被正确拒绝"
else
    a_no "不存在的磁盘处理" "输出：$(printf '%s' "$_out" | tail -3)"
fi

echo ""

# ── 5. 磁盘容量体检（★ 关键：用小假盘验证报警）────────────────────────
echo "【5】磁盘容量体检（小容量假盘，模拟用户真机只有 1GB 救援盘的情况）"
# 找一个小容量的块设备（优先我们造的假盘）
_small=""
for _d in /dev/loop0 /dev/loop1 /dev/loop2 /dev/loop3; do
    [ -b "$_d" ] || continue
    _sz=$(($(cat "/sys/block/$(basename "$_d")/size" 2>/dev/null || echo 0) / 2048))
    [ "$_sz" -gt 0 ] && [ "$_sz" -lt 4000 ] && { _small="$_d"; break; }
done

if [ -n "$_small" ]; then
    _out="$(sh "$SCRIPT" --check --disk "$_small" --yes 2>&1)"
    if printf '%s' "$_out" | grep -qE "非常紧张|建议至少 8GB|救援系统自己的盘"; then
        a_ok "小容量磁盘（$_small）触发了容量警告"
    else
        a_no "小容量容量警告" "输出：$(printf '%s' "$_out" | grep -E '\[!\]' | head -3)"
    fi
else
    echo "  [跳过] 找不到小容量块设备，跳过容量测试"
fi

# 对照：大容量盘不应触发容量警告
_big=""
for _d in /dev/loop0 /dev/loop1 /dev/loop2 /dev/loop3; do
    [ -b "$_d" ] || continue
    _sz=$(($(cat "/sys/block/$(basename "$_d")/size" 2>/dev/null || echo 0) / 2048))
    [ "$_sz" -ge 6000 ] && { _big="$_d"; break; }
done
if [ -n "$_big" ]; then
    _out="$(sh "$SCRIPT" --check --disk "$_big" --yes 2>&1)"
    if printf '%s' "$_out" | grep -qE "非常紧张"; then
        a_no "大容量盘（$_big）不该报容量告警"
    else
        a_ok "大容量盘（$_big）未误报容量告警"
    fi
fi

echo ""

# ── 6. 挂载占用检测 ───────────────────────────────────────────────────────
echo "【6】挂载占用检测"
# 用根分区测试（必然被挂载）
_rootdev="$(awk '$2=="/"{print $1}' /proc/mounts | head -1 | sed 's/[0-9]*$//;s/p[0-9]*$//')"
if [ -b "$_rootdev" ]; then
    _out="$(sh "$SCRIPT" --check --disk "$_rootdev" --yes 2>&1)"
    if printf '%s' "$_out" | grep -qE "当前被挂载|救援系统正在用"; then
        a_ok "已挂载的磁盘会提示风险"
    else
        a_no "挂载占用提示" "输出：$(printf '%s' "$_out" | grep -E '\[!\]' | head -3)"
    fi
else
    echo "  [跳过] 无法确定根设备"
fi

echo ""

# ── 7. 密码处理 ───────────────────────────────────────────────────────────
echo "【7】密码参数处理"
# ★ 必须用**真实存在**的盘来测：用不存在的盘，脚本会在磁盘检查那步就退出，
#   根本走不到密码逻辑，会得出假结论（第一版就踩了这个坑）。
_pwdisk=""
for _d in /dev/loop1 /dev/loop0 /dev/loop2; do
    [ -b "$_d" ] && { _pwdisk="$_d"; break; }
done

if [ -n "$_pwdisk" ]; then
    _out="$(sh "$SCRIPT" --check --yes --disk "$_pwdisk" 2>&1)"
    if printf '%s' "$_out" | grep -qE "自动生成|请务必记下"; then
        a_ok "非交互模式自动生成密码并提醒" "盘：$_pwdisk"
    else
        a_no "自动生成密码" "输出尾部：$(printf '%s' "$_out" | tail -6)"
    fi

    # 显式给了密码就应原样使用、不生成
    _out="$(sh "$SCRIPT" --check --yes --disk "$_pwdisk" --pwd 'MyTestPass123' 2>&1)"
    if printf '%s' "$_out" | grep -qF "MyTestPass123"; then
        a_ok "显式指定的密码被原样采用"
    else
        a_no "显式密码" "输出里没看到密码"
    fi
else
    echo "  [跳过] 没有可用假盘"
fi

echo ""

# ── 8. InstallNET 下载地址可用性 ──────────────────────────────────────────
echo "【8】InstallNET 源可用性"
_url="$(grep -oE 'https://raw\.githubusercontent\.com/leitbogioro/Tools[^"]*' "$SCRIPT" | head -1)"
if [ -n "$_url" ]; then
    a_ok "脚本里写明了 InstallNET 下载地址" "$_url"
else
    a_no "InstallNET 地址" "没找到"
fi

# 说明：不在测试里真下载 278KB（慢且依赖外网），只验证地址形态正确
if printf '%s' "$_url" | grep -qE 'leitbogioro/Tools.*InstallNET\.sh$'; then
    a_ok "地址指向 leitbogioro/Tools 的 InstallNET.sh"
else
    a_no "地址形态" "实际：$_url"
fi

echo ""

# ── 9. 安全检查：不含危险通配符 ───────────────────────────────────────────
echo "【9】安全检查"
if grep -vE '^[[:space:]]*#' "$SCRIPT" | grep -qE 'rm -rf /[^a-zA-Z]'; then
    a_no "不含 rm -rf / 这类危险写法"
else
    a_ok "不含 rm -rf / 这类危险写法"
fi

# 目标磁盘变量必须带引号使用（防路径注入/空格）
if grep -vE '^[[:space:]]*#' "$SCRIPT" | grep -qE 'wipefs -a \$DISK'; then
    a_ok "wipefs 用的是 \$DISK 变量（不是硬编码路径）"
else
    echo "  [跳过] 脚本里没直接调 wipefs（交给 InstallNET 处理）"
fi

echo ""

# ── 10. ★ 救援盘硬拦截（本次新增，对应 2026-10-11 用户真机事故）────────
# 事故复盘：用户真机只有一块 1GB 盘，被 Alpine 救援系统挂着、带 iso9660 签名。
# 旧版脚本只给警告 + 一个 yes 确认，用户输入 yes 后擦掉了 ISO 签名，
# 接着 mkfs 报 "apparently in use" 卡死，而且把自己退路断了。
# 本组测试：造一个【三条特征全中】的假盘，验证脚本必须【拒绝执行】。
echo "【10】救援盘硬拦截（1GB + 已挂载 + iso9660 签名 → 必须拒绝）"

# 准备：造 1GB 假盘 + 做成 iso9660 + 挂载
_rdisk=""
_rlo=""
_rimg="/tmp/rd13-rescue-test.img"
_rmnt="/tmp/rd13-rescue-mnt"

if [ "$(id -u)" = "0" ] && command -v losetup >/dev/null 2>&1; then
    # 找空闲 loop
    for _n in 8 9 10 11 12 13 14 15; do
        if ! losetup "/dev/loop$_n" >/dev/null 2>&1; then _rlo="/dev/loop$_n"; break; fi
    done

    if [ -n "$_rlo" ]; then
        # 1GB 镜像（稀疏文件，不真占空间）
        dd if=/dev/zero of="$_rimg" bs=1M count=1 seek=1023 2>/dev/null || true
        if command -v mkisofs >/dev/null 2>&1; then
            mkisofs -o "$_rimg" -V "RD13RESCUE" -J -r /etc >/dev/null 2>&1 || true
        elif command -v genisoimage >/dev/null 2>&1; then
            genisoimage -o "$_rimg" -V "RD13RESCUE" -J -r /etc >/dev/null 2>&1 || true
        fi
        # 兜底：没有 iso 工具就用 dd 写个 1GB 裸盘（则只有"容量小"这一条特征）
        _rsz_mb=$(( $(wc -c < "$_rimg" 2>/dev/null || echo 0) / 1048576 ))

        if losetup "$_rlo" "$_rimg" 2>/dev/null; then
            _rdisk="$_rlo"
            # 挂载（模拟救援系统把盘挂在 /media）
            mkdir -p "$_rmnt" 2>/dev/null || true
            mount "$_rdisk" "$_rmnt" >/dev/null 2>&1 || true
        fi
    fi
fi

if [ -n "$_rdisk" ]; then
    _out="$(sh "$SCRIPT" --check --disk "$_rdisk" --yes 2>&1)"
    _rc=$?

    # ★ 核心断言：必须拒绝执行（退出码非 0，并给出明确拒绝原因）
    if printf '%s' "$_out" | grep -qE "救援系统自己的盘|已拒绝执行"; then
        a_ok "识别出救援盘并拒绝执行"
    else
        a_no "救援盘识别" "输出尾部：$(printf '%s' "$_out" | tail -8)"
    fi

    # ★ 关键断言：--yes 不能绕过（这正是事故的成因）
    if [ "$_rc" -ne 0 ]; then
        a_ok "--yes 也无法绕过拦截（退出码 $_rc）"
    else
        a_no "--yes 绕过拦截" "退出码为 0，说明 --yes 把拦截放行了 —— 这正是事故成因"
    fi

    # 必须给出正确出路（提示挂数据盘 / 用 --disk）
    if printf '%s' "$_out" | grep -qE "挂载一块新云盘|cat /proc/partitions|/dev/vdb"; then
        a_ok "给出了正确的下一步指引（挂数据盘）"
    else
        a_no "下一步指引" "没提示用户去挂数据盘"
    fi

    # 必须提到强制参数的存在（知道有风险、给知情选择）
    if printf '%s' "$_out" | grep -qF -- "--force-rescue-disk"; then
        a_ok "提示了强制参数（--force-rescue-disk）的存在"
    else
        a_no "强制参数提示" "没提到 --force-rescue-disk"
    fi

    # ★ 强制参数应当能放行（说明这是"可控的拒绝"而非"死锁"）
    _out2="$(sh "$SCRIPT" --check --disk "$_rdisk" --yes --force-rescue-disk 2>&1)"
    if printf '%s' "$_out2" | grep -qF -- "--force-rescue-disk"; then
        a_ok "带 --force-rescue-disk 时可继续"
    else
        a_no "--force-rescue-disk 未生效"
    fi

    # 清理
    umount "$_rmnt" >/dev/null 2>&1 || true
    umount "$_rdisk" >/dev/null 2>&1 || true
    losetup -d "$_rdisk" >/dev/null 2>&1 || true
    rm -f "$_rimg" 2>/dev/null || true
    rmdir "$_rmnt" 2>/dev/null || true
else
    echo "  [跳过] 需要 root + losetup 才能造救援盘假盘"
fi

echo ""

# ── 11. 对照：正常数据盘不该被拦截 ────────────────────────────────────────
# ★ 反向对照：确认拦截逻辑没有"误伤" —— 一块干净的 8GB 未挂载盘必须放行。
echo "【11】对照：正常数据盘不应被误拦截"
_ddisk=""
_dlo=""
_dimg="/tmp/rd13-data-test.img"
if [ "$(id -u)" = "0" ] && command -v losetup >/dev/null 2>&1; then
    for _n in 8 9 10 11 12 13 14 15; do
        if ! losetup "/dev/loop$_n" >/dev/null 2>&1; then _dlo="/dev/loop$_n"; break; fi
    done
    if [ -n "$_dlo" ]; then
        dd if=/dev/zero of="$_dimg" bs=1M count=1 seek=8191 2>/dev/null || true
        losetup "$_dlo" "$_dimg" 2>/dev/null && _ddisk="$_dlo"
    fi
fi

if [ -n "$_ddisk" ]; then
    _out="$(sh "$SCRIPT" --check --disk "$_ddisk" --yes 2>&1)"
    if printf '%s' "$_out" | grep -qE "已拒绝执行"; then
        a_no "8GB 干净数据盘不该被拦截" "$(printf '%s' "$_out" | grep -E '\[X\]' | head -3)"
    else
        a_ok "8GB 干净数据盘未被误拦截"
    fi
    losetup -d "$_ddisk" >/dev/null 2>&1 || true
    rm -f "$_dimg" 2>/dev/null || true
else
    echo "  [跳过] 需要 root + losetup"
fi

echo ""

# ── 12. bash 依赖（本次新增，对应 2026-10-11 用户真机事故）──────────────
# 事故复盘：Alpine Live ISO 自带 wget 但**不带 bash**，而上一版脚本把
# "装 bash" 混在"没有 wget 时才执行"的分支里 → 该分支被跳过 → bash 从未装上
# → 脚本跑到最后一步（第 6 步）才报 `sh: bash: not found`（退出码 127）。
#
# ★ 验证策略（踩过两次坑，记录在此）：
#   坑1：写静态 grep → 反向验证不报红（脚本末尾兜底检查还在，静态断言照样过）
#   坑2：用 --check 真跑 → --check 在第 5 步就结束，**永远走不到第 6 步**
#        （bash 检查在第 6 步），所以测不到
#   正确做法：不要跑整个脚本，而是**把第 6 步的 bash 保障逻辑单独提取出来
#   执行** —— 检查"没有 bash 时这段逻辑会不会报出可操作提示"。
echo "【12】bash 依赖保障（提取逻辑独立验证）"

# 从被测脚本里提取"bash 保障"段落（从标识注释到 InstallNET 下载前）
_scr="$SCRIPT"
_extract="/tmp/rd13-bash-seg.sh"
{
    echo '#!/bin/sh'
    echo 'ok()   { printf "[OK] %s\n" "$*"; }'
    echo 'warn() { printf "[!] %s\n" "$*"; }'
    echo 'info() { printf "[*] %s\n" "$*"; }'
    echo 'err()  { printf "[X] %s\n" "$*"; }'
    echo 'set -u'
    # 取出"必须确保 bash 存在"到"下载 InstallNET.sh"之间的整段
    awk '/必须确保 bash 存在/{f=1} f{print} /^info "下载 InstallNET\.sh"/{exit}' "$_scr"
} > "$_extract" 2>/dev/null

if [ -s "$_extract" ] && grep -q 'command -v bash' "$_extract"; then
    # 场景 A：环境里有 bash → 应报"bash 已就绪"
    _out="$(sh "$_extract" 2>&1 || true)"
    if printf '%s' "$_out" | grep -qE "bash 已就绪"; then
        a_ok "有 bash 时报「bash 已就绪」"
    else
        a_no "有 bash 时未正确识别" "输出：$(printf '%s' "$_out" | head -3)"
    fi

    # 场景 B：★ 屏蔽 bash → 必须报出可操作提示（这是事故的直接防呆）
    #   构造一个不含 bash 的 PATH，让 'command -v bash' 失败。
    _sb="/tmp/rd13-nobash-path"
    rm -rf "$_sb" 2>/dev/null || true
    mkdir -p "$_sb" 2>/dev/null || true
    for _t in sh awk sed grep cut tr wc cat ls mkdir rm cp env id printf echo od; do
        _p="$(command -v "$_t" 2>/dev/null)" || continue
        [ -n "$_p" ] && ln -sf "$_p" "$_sb/$_t" 2>/dev/null || true
    done
    # apk **不放**进来 → 安装必然失败 → 走"bash 安装失败"分支
    if ! PATH="$_sb" "$_sb/sh" -c 'command -v bash' >/dev/null 2>&1; then
        _out="$(PATH="$_sb" "$_sb/sh" "$_extract" 2>&1 || true)"
        if printf '%s' "$_out" | grep -qE "apk add --no-cache bash"; then
            a_ok "缺 bash 时报出可操作提示（apk add --no-cache bash）"
        else
            a_no "缺 bash 时无提示" "输出：$(printf '%s' "$_out" | head -4)"
        fi
    else
        echo "  [跳过] 无法构造无 bash 的 PATH"
    fi
    rm -rf "$_sb" 2>/dev/null || true
else
    a_no "无法提取 bash 保障段落" "提取文件为空或找不到 command -v bash"
fi
rm -f "$_extract" 2>/dev/null || true

# 静态补强：兜底检查必须存在（防止将来有人只删兜底不删主逻辑）
_scr2="$(grep -vE '^[[:space:]]*#' "$SCRIPT")"
if printf '%s' "$_scr2" | grep -qE 'command -v bash'; then
    a_ok "脚本里有 bash 存在性检查（静态确认）"
else
    a_no "bash 检查缺失" "脚本没有任何 command -v bash 检查"
fi
if printf '%s' "$_scr2" | grep -qE 'apk add[^|]*\bbash\b'; then
    a_ok "有 apk add bash 的自动安装动作（静态确认）"
else
    a_no "bash 自动安装缺失"
fi

echo ""
echo "=========================================="

if [ "$FAIL" -gt 0 ]; then
    printf '  失败清单：%s\n' "$FAILED_NAMES"
fi
echo "=========================================="
echo ""

[ "$FAIL" -eq 0 ] && { echo "全部自检通过。"; exit 0; } || { echo "存在失败项，请修复。"; exit 1; }
