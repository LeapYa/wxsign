#!/usr/bin/env bash
# 微信小程序签到合集 · 青龙面板总入口
#
# 在青龙「定时任务」里写：
#   bash /ql/data/scripts/wxsign/sign.sh              # 所有启用品牌
#   bash /ql/data/scripts/wxsign/sign.sh <slug>       # 只签某一个
#   bash /ql/data/scripts/wxsign/sign.sh --ensure-only # 只自检保活，不签到
#
# 做的事：docker/hook/微信 自检 → 掉登录就点登录 → 逐个品牌（开小程序 → 刷 token → 签到）
#
# ⚠️ 登录是一次性配置：首次扫码登录一次，之后只要不重启就一直有效，运行期全自动。
#    唯一要留意的是**别让微信掉登录** —— 一旦因重启掉登录，点完「登录」还需**在手机上确认**
#    （Linux 版微信没有 Windows 的「登录免确认」选项），久不确认会过期 → 只能扫码。
#    见 docs/DEPLOY.md 第 6 节。
#
# 需要的青龙环境变量：
#   WOC_INSTANCE=<微信实例容器名>    WOC_HOOK=woc-hook
#   WXSIGN_PYTHON=/usr/bin/python3
#   WXSIGN_HOME_CONTAINER=/ql/data/scripts/wxsign   （脚本在容器里的路径）
set -u
export PATH="/usr/bin:/bin:/usr/local/bin:$PATH"

HERE="$(cd "$(dirname "$0")" && pwd)"
PY="${WXSIGN_PYTHON:-python3}"
INSTANCE="${WOC_INSTANCE:-}"
HOOK="${WOC_HOOK:-woc-hook}"

log() { echo "[$(date '+%F %T')] $*"; }
die() { log "FAIL: $1"; exit 1; }

log "===== 签到合集 开始 ====="

# 1. docker
docker ps >/dev/null 2>&1 || die "docker 引擎不可用（青龙需挂 /var/run/docker.sock 与 docker 二进制）"
[ -n "$INSTANCE" ] || die "未设 WOC_INSTANCE（微信实例容器名）"
docker ps --format '{{.Names}}' | grep -qx "$INSTANCE" || die "微信实例容器 $INSTANCE 未运行"

# 2. hook（刷 token 必需）。它是部署时一次性建好的容器，这里只做校验与拉起。
if ! docker ps --format '{{.Names}}' | grep -qx "$HOOK"; then
  log "$HOOK 没在跑，尝试 docker start"
  docker start "$HOOK" >/dev/null 2>&1 || true
  sleep 3
fi
if docker ps --format '{{.Names}}' | grep -qx "$HOOK"; then
  docker exec "$HOOK" grep -q "script loaded" /tmp/wmpf.log 2>/dev/null \
    || log "⚠️ hook 容器在跑，但日志里没有 script loaded —— 刷 token 可能失败（见 docs/DEPLOY.md 第 4 节）"
else
  log "⚠️ hook 容器不存在 → 刷 token 会失败。按 docs/DEPLOY.md 第 4 节建一次（一次性）"
fi

# 3. 微信掉登录就点一下。
#    ⚠️ 点「登录」之后**必须有人在手机上确认**（Linux 版微信没有 Windows 那个「登录免确认」选项），
#       这是一次性配置，不影响日常无人值守；久不确认会变成「必须扫码」。细节见 wxlogin.py 顶部。
docker cp "$HERE/wxopen.py"  "$INSTANCE":/tmp/ >/dev/null 2>&1 || true
docker cp "$HERE/wxlogin.py" "$INSTANCE":/tmp/ >/dev/null 2>&1 || true
if docker exec "$INSTANCE" sh -c 'DISPLAY=:1 python3 -c "
import subprocess,sys
d=subprocess.run(\"DISPLAY=:1 ffmpeg -loglevel error -f x11grab -video_size 1280x1024 -i :1 -frames:v 1 -f rawvideo -pix_fmt rgb24 -\",shell=True,capture_output=True).stdout
g=sum(1 for i in range(0,len(d),6) if d[i+1]>150 and d[i+1]-d[i]>60 and d[i+1]-d[i+2]>40)
sys.exit(0 if g>300 else 1)"' 2>/dev/null; then
  log "检测到微信停在登录页 → 点登录（**需要你在手机上确认**），最多等 ${WXSIGN_LOGIN_WAIT:-300}s"
  docker exec -e DISPLAY=:1 "$INSTANCE" "$PY" /tmp/wxlogin.py
  LOGIN_RC=$?
  case "$LOGIN_RC" in
    0) log "✅ 微信已登录" ;;
    1) log "本来就是登录状态" ;;
    2) log "❌ 出现二维码：登录状态多半已过期，现在**只能扫码**。"
       log "   打开 KasmVNC 网页 → 进实例 → 手机扫码登录，然后重跑本任务。"
       exit 2 ;;
    3) log "❌ 登录没完成：点了按钮但没人确认（或界面异常）。"
       log "   请在手机上点确认后重跑；**久不确认会过期，变成必须扫码**。"
       exit 3 ;;
    *) log "⚠️ 登录状态未知（rc=$LOGIN_RC），继续尝试签到" ;;
  esac
fi

[ "${1:-}" = "--ensure-only" ] && { log "仅保活，退出"; exit 0; }

# 4. 签到（其余辅助脚本由 wxsign.py 的 ensure_helpers() 自动投递到微信容器）
if [ -n "${1:-}" ]; then
  "$PY" "$HERE/wxsign.py" "$1" --ensure
else
  "$PY" "$HERE/wxsign.py" --all --ensure
fi
RC=$?
log "===== 结束（退出码 $RC）====="
exit $RC
