#!/usr/bin/env bash
# 吾享签到合集 · 青龙面板总入口
#
# 在青龙「定时任务」里写：
#   bash /ql/data/scripts/wxsign/sign.sh              # 所有启用品牌
#   bash /ql/data/scripts/wxsign/sign.sh lakeke        # 只签某一个
#   bash /ql/data/scripts/wxsign/sign.sh --ensure-only # 只自检保活，不签到
#
# 它做的事：docker/hook 自检 → 微信掉登录就点登录 → 投放开小程序脚本
#          → 逐个品牌（开小程序 → 刷 token → 签到 → 关小程序）
#
# 需要的青龙环境变量：
#   WOC_INSTANCE=<微信实例容器名>    WOC_HOOK=woc-hook
#   WXSIGN_PYTHON=/usr/bin/python3   （青龙镜像自带 python3）
#   LAKEKE_HOOK_DIR=<辣可可项目目录> （可选，用于复用 hook_up.sh / wxlogin.py）
set -u
export PATH="/usr/bin:/bin:/usr/local/bin:$PATH"

HERE="$(cd "$(dirname "$0")" && pwd)"
PY="${WXSIGN_PYTHON:-python3}"
INSTANCE="${WOC_INSTANCE:-}"
HOOK="${WOC_HOOK:-woc-hook}"
# 复用已有的微信运维脚本（hook_up.sh / wxlogin.py）——通常来自辣可可那个项目
OPS="${LAKEKE_HOOK_DIR:-}"
# 容器内放「打开指定小程序」脚本的位置
MINIAPP_PY="${WXSIGN_MINIAPP_PY:-}"

log() { echo "[$(date '+%F %T')] $*"; }
die() { log "FAIL: $1"; exit 1; }

log "===== 吾享签到合集 开始 ====="

# 1. docker
docker ps >/dev/null 2>&1 || die "docker 引擎不可用（青龙需挂 /var/run/docker.sock 与 docker 二进制）"
[ -n "$INSTANCE" ] || die "未设 WOC_INSTANCE（微信实例容器名）"
docker ps --format '{{.Names}}' | grep -qx "$INSTANCE" || die "微信实例容器 $INSTANCE 未运行"

# 2. hook（刷 token 必需）
if ! docker ps --format '{{.Names}}' | grep -qx "$HOOK"; then
  log "hook 容器不在，尝试重建"
  [ -n "$OPS" ] && [ -f "$OPS/hook_up.sh" ] && bash "$OPS/hook_up.sh" "$INSTANCE" || die "hook 重建失败（缺 hook_up.sh？）"
fi
docker exec "$HOOK" grep -q "script loaded" /tmp/wmpf.log 2>/dev/null \
  || log "⚠️ hook 日志里没有 script loaded —— 刷 token 可能失败，检查 hook_up.sh"

# 3. 微信掉登录就点一下（脚本按微信绿按钮的像素定位，不写死坐标）
if [ -n "$OPS" ] && [ -f "$OPS/wxlogin.py" ]; then
  if docker exec "$INSTANCE" sh -c 'DISPLAY=:1 python3 -c "
import subprocess,sys
d=subprocess.run(\"DISPLAY=:1 ffmpeg -loglevel error -f x11grab -video_size 1280x1024 -i :1 -frames:v 1 -f rawvideo -pix_fmt rgb24 -\",shell=True,capture_output=True).stdout
g=sum(1 for i in range(0,len(d),6) if d[i+1]>150 and d[i+1]-d[i]>60 and d[i+1]-d[i+2]>40)
sys.exit(0 if g>300 else 1)"' 2>/dev/null; then
    log "检测到微信停在登录页 → 点登录"
    docker cp "$OPS/wxlogin.py" "$INSTANCE":/tmp/wxlogin.py >/dev/null 2>&1
    docker exec -e DISPLAY=:1 "$INSTANCE" "$PY" /tmp/wxlogin.py || log "⚠️ 自动登录失败，看容器截图"
  fi
fi

# 4. 投放「打开指定小程序」脚本
if [ -n "$MINIAPP_PY" ] && [ -f "$MINIAPP_PY" ]; then
  docker cp "$MINIAPP_PY" "$INSTANCE":/tmp/reopen_miniapp.py >/dev/null 2>&1 && log "已投放 reopen_miniapp.py"
else
  log "未提供 WXSIGN_MINIAPP_PY → 跳过自动开小程序（需小程序已在运行）"
fi

[ "${1:-}" = "--ensure-only" ] && { log "仅保活，退出"; exit 0; }

# 5. 签到
if [ -n "${1:-}" ]; then
  "$PY" "$HERE/wxsign.py" "$1" --ensure
else
  "$PY" "$HERE/wxsign.py" --all --ensure
fi
RC=$?
log "===== 结束（退出码 $RC）====="
exit $RC
