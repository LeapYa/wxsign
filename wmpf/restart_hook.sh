#!/usr/bin/env bash
# 重启 hook 容器里的 WMPFDebugger（重新 attach 一遍）。
#
# 为什么需要：它**只在启动时 attach 一次**。之后新拉起的微信运行时（WeChatAppEx）
# 不会被挂上，表现为 CDP 拿不到任何 context —— wxdom/wxcdp 报 `ctx=0`，
# 日志里也没有 `miniapp client connected`。
#
# 什么时候要跑：
#   · 换了微信版本 / 换了 offsets 配置（auto_offsets.sh 装完配置后）
#   · 杀过 WeChatAppEx 清残留窗口之后（`wxclean.py --restart-runtime` 会提示你跑这个）
#   · 微信实例被重启过
#
# 用法:
#   bash wmpf/restart_hook.sh                 # 默认 hook 容器 woc-hook
#   HOOK_CONTAINER=别的名字 bash wmpf/restart_hook.sh
set -u

HOOK="${HOOK_CONTAINER:-${WOC_HOOK:-woc-hook}}"
WMPF_DIR="${WMPF_DIR:-/opt/wmpf}"
WAIT_SECS="${WAIT_SECS:-60}"

say() { printf '%s\n' "$*"; }

command -v docker >/dev/null 2>&1 || { say "[FAIL] 没有 docker"; exit 2; }
docker inspect "$HOOK" >/dev/null 2>&1 || { say "[FAIL] 容器 $HOOK 不存在"; exit 2; }

say "[1/3] 停掉旧的 WMPFDebugger（只杀 node，容器本身不动）"
docker exec "$HOOK" sh -c '
  for p in $(ls /proc | grep -E "^[0-9]+$"); do
    [ "$(cat /proc/$p/comm 2>/dev/null)" = "node" ] && kill $p 2>/dev/null
  done
  sleep 2
  echo "     done"'

say "[2/3] 重新启动（后台，日志写 $WMPF_DIR/../tmp/wmpf.log）"
docker exec -d "$HOOK" sh -c \
  "cd $WMPF_DIR && node node_modules/ts-node/dist/bin.js src/index.ts > /tmp/wmpf.log 2>&1"

say "[3/3] 等它 attach（最多 ${WAIT_SECS}s）"
i=0
while [ "$i" -lt "$WAIT_SECS" ]; do
  sleep 3; i=$((i + 3))
  if docker exec "$HOOK" grep -q "script loaded" /tmp/wmpf.log 2>/dev/null; then
    ver="$(docker exec "$HOOK" grep -oE 'WMPF version: *[0-9.]+' /tmp/wmpf.log 2>/dev/null | tail -1)"
    say ""
    say "[OK] hook 就绪${ver:+（$ver）}"
    say "     现在回微信里把小程序**重开一次** —— 让新实例被挂上，否则 CDP 仍是 0 个 context。"
    exit 0
  fi
done

say ""
say "[FAIL] 等了 ${WAIT_SECS}s 还没看到 'script loaded'。看日志："
docker exec "$HOOK" tail -15 /tmp/wmpf.log 2>/dev/null | sed 's/^/  /'
say ""
say "常见原因：WMPF 版本没有对应 offsets 配置（先跑 wmpf/check_wmpf.sh）。"
exit 1
