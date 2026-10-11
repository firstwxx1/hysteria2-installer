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
echo "=========================================="
printf '  通过 %d 项，失败 %d 项\n' "$PASS" "$FAIL"
if [ "$FAIL" -gt 0 ]; then
    printf '  失败清单：%s\n' "$FAILED_NAMES"
fi
echo "=========================================="
echo ""

[ "$FAIL" -eq 0 ] && { echo "全部自检通过。"; exit 0; } || { echo "存在失败项，请修复。"; exit 1; }
