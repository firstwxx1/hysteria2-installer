#!/usr/bin/env bash
# 演练第 1 阶段：新手交互式安装（自签证书，全程按回车走默认）
set -u
export LOG_DIR=/tmp/rehearsal
mkdir -p "$LOG_DIR"
source /repo/harness.sh

banner "阶段 1：新手交互式安装（自签 / 一路回车）"

step 'a01-脚本语法'            bash -n /repo/install.sh
step 'a02-bash版本'            bash -c 'echo $BASH_VERSION'
step 'a03-root检查'            docker_marker_skip_true 2>/dev/null || true

# 真跑：把「一路按回车」的答案序列喂进去。
# 序列含义：菜单选 1（安装）→ 证书类型选 1（自签）→ 端口回车 →
#           商城版 API Key 回车 → 完装后「按回车返回主菜单」→ 退出
# 本项目固定商城版（Agent），安装时不再询问运行模式。
step 'a04-交互安装(默认答案)' bash -c '
  printf "1\n1\n\n\n\n\n" | timeout 900 bash /repo/install.sh
'

summary