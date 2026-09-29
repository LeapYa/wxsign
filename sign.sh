#!/usr/bin/env bash
# 微信小程序签到合集 · 青龙面板总入口
#
# 在青龙「定时任务」里写：
#   bash /ql/data/scripts/wxsign/sign.sh                        # 默认 all（brands.json 里 enabled 的）
#   bash /ql/data/scripts/wxsign/sign.sh --apps lakeke,laicai   # 只签这两个（多选）
#   bash /ql/data/scripts/wxsign/sign.sh --exclude jiucun       # 不签这个（多选）
#   bash /ql/data/scripts/wxsign/sign.sh lakeke                 # 位置参数 = 只签它
#   bash /ql/data/scripts/wxsign/sign.sh --ensure-only          # 只自检保活，不签到
#
# 做的事：docker/hook/微信 自检 → 掉登录就点登录 → 逐品牌（开小程序 → 刷 token → 签到）
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
#   WXSIGN_APPS=lakeke,laicai        可选：白名单，只签这几个（默认 all）
#   WXSIGN_EXCLUDE=jiucun            可选：黑名单，永不签这几个（优先级最高）
set -u
export PATH="/usr/bin:/bin:/usr/local/bin:$PATH"

HERE="$(cd "$(dirname "$0")" && pwd)"
PY="${WXSIGN_PYTHON:-python3}"
INSTANCE="${WOC_INSTANCE:-}"
HOOK="${WOC_HOOK:-woc-hook}"

log() { echo "[$(date '+%F %T')] $*"; }
die() { log "FAIL: $1"; exit 1; }

# 脚本在**容器里**的路径 —— wxsign.py 用它给 hook 容器拼 `cd` 和 `ENVFILE=`。
# ⚠️ 不能靠默认值推断：`HOME_C` 不设时会退回**宿主机**路径，而宿主机路径
#    （Windows 下形如 `C:\Users\...`）传进容器的 sh 会把反斜杠当转义吃掉 →
#    `cd: can't cd to C:Users...`，症状是 `[token] 刷新 rc=2`（本机实测踩到）。
#    所以：优先用显式环境变量；没有就**在 hook 容器里探测**（找 wxrefresh.js 所在目录）。
if [ -z "${WXSIGN_HOME_CONTAINER:-}" ]; then
  WXSIGN_HOME_CONTAINER="$(docker exec "$HOOK" sh -c \
    'for d in /work/wxsign /ql/data/scripts/wxsign /wxsign; do [ -f "$d/wxrefresh.js" ] && { echo "$d"; break; }; done' \
    2>/dev/null || true)"
  [ -n "$WXSIGN_HOME_CONTAINER" ] || WXSIGN_HOME_CONTAINER="/work/wxsign"
  log "探测到脚本在 hook 容器里的路径：$WXSIGN_HOME_CONTAINER"
fi
export WXSIGN_HOME_CONTAINER

# 只读命令直接透传，不要求 docker / 微信环境（想在青龙控制台随手查一下时很有用）
case "${1:-}" in
  --list|--find) exec "$PY" "$HERE/wxsign.py" "$@" ;;
esac

log "===== 签到合集 开始 ====="

# 0. ⭐ 先清掉**上一次跑批留下的容器级残留 python**。
#    为什么必须有这一步（2026-09-28 实测）：`wxsign.py` 是通过
#    `docker exec <微信容器> python3 /tmp/wxopen.py` 干活的 ——
#    **宿主机这一侧被 kill / 异常中止时，容器里的那个 python 不会跟着死**，
#    它会继续跑自己的重试循环、继续点卡片、继续开小程序窗口，而日志里什么都看不到。
#    症状就是「跑批明明停了，屏幕上的微信还在自己动 / 冒出莫名其妙的小程序」，
#    紧接着下一次跑批在 clean 阶段被这些残留窗口堵死、第一个品牌就 rc=1 中止。
docker exec "$INSTANCE" sh -c '
n=0
for p in $(ls /proc | grep -E "^[0-9]+$"); do
  [ "$(cat /proc/$p/comm 2>/dev/null)" = "python3" ] && { kill -9 $p 2>/dev/null; n=$((n+1)); }
done
[ "$n" -gt 0 ] && echo "  [preclean] 杀掉 $n 个残留 python"' >/dev/null 2>&1 || true

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
  # ⚠️ `//tmp/...` 双斜杠同上：写单斜杠会被 Git Bash 转成宿主机路径 → 永远 grep 不到 →
  #    每次都误报「hook 日志里没有 script loaded」（而 hook 其实好好的）。本机实测踩到。
  docker exec "$HOOK" grep -q "script loaded" //tmp/wmpf.log 2>/dev/null \
    || log "⚠️ hook 容器在跑，但日志里没有 script loaded —— 刷 token 可能失败（见 docs/DEPLOY.md 第 4 节）"
else
  log "⚠️ hook 容器不存在 → 刷 token 会失败。按 docs/DEPLOY.md 第 4 节建一次（一次性）"
fi

# 3. 微信掉登录就点一下。
#    ⚠️ 点「登录」之后**必须有人在手机上确认**（Linux 版微信没有 Windows 那个「登录免确认」选项），
#       这是一次性配置，不影响日常无人值守；久不确认会变成「必须扫码」。细节见 wxlogin.py 顶部。
# ⚠️ 这里必须把「**早期就会被调用**的脚本」都投一遍 —— 不能只靠 wxsign.py 的
#    `ensure_helpers()`：它要到 `refresh_token` 才跑，而 `clean_leftovers()` 跑得**比它早**
#    （每个品牌的第一步就是 clean）。实测踩到：只 cp 了 wxopen/wxlogin，
#    容器里的 `wxclean.py` 还是**旧版**（不含新加的主窗口让位逻辑），
#    于是"最小化让位"一次都没执行、残留面板照样关不掉。
for _f in wxopen.py wxlogin.py wxclean.py wxfind.py wxcdp.py reopen_miniapp.py; do
  [ -f "$HERE/$_f" ] && docker cp "$HERE/$_f" "$INSTANCE":/tmp/ >/dev/null 2>&1
done
true
# 预筛：微信**已经**是登录状态吗？判据用 wxlogin 自己的（侧边栏图标列 + 全局搜索框）。
# ⚠️ 别再用「绿色像素数 > 300」那种粗判据 —— 登录后的聊天列表里
#    「文件传输助手 / 微信团队 / 微信支付」都是**绿底头像**，实测 g=1661，
#    远超阈值 → 必然把"已登录"误判成"停在登录页"（本机实测踩到）。
if ! docker exec -e DISPLAY=:1 "$INSTANCE" "$PY" -c "
import sys; sys.path.insert(0, '/tmp')
import wxlogin as L
# ⚠️ 主窗口可能被上一轮的 windowunmap 卸在屏幕外 —— 判据（侧边栏 + 搜索框）
#    要求它**在屏幕上**，先放回来并把分辨率钉死，否则必然误判「未登录」。
L.R.run('DISPLAY=%s xrandr -s %dx%d' % (L.R.DISPLAY, L.W, L.H))
L.R.show_main(L.W, L.H)
L.fullscreen_main()
ok, diag = L.login_state(L.R.grab(L.W, L.H), L.W, L.H)
sys.exit(0 if ok else 1)" 2>/dev/null; then
  log "检测到微信不在登录状态 → 点登录（**需要你在手机上确认**），最多等 ${WXSIGN_LOGIN_WAIT:-300}s"
  # ⚠️ 路径必须写 **`//tmp/...`（双斜杠）**：Git Bash(MSYS) 会把以单个 `/` 开头的参数
  #    **自动转成宿主机路径** —— `/tmp/wxlogin.py` 会变成
  #    `C:/Users/<你>/AppData/Local/Temp/wxlogin.py`，容器里的 python 找不到文件，
  #    退出码恰好是 2，又被下面的 case 当成「出现二维码」→ **假报登录过期**（本机实测踩到）。
  #    双斜杠在 MSYS 下不被转换、在 Linux 上等价于单斜杠，两头都对。
  docker exec -e DISPLAY=:1 "$INSTANCE" "$PY" //tmp/wxlogin.py
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
#    参数原样透传给引擎：--apps / --exclude / 位置 slug 都能用（见文件头）。
#    没显式给 --ensure 就补一个 —— 定时任务里要的就是「先开小程序再签」。
CMD=("$PY" "$HERE/wxsign.py")
HAS_ENSURE=""
for a in ${1+"$@"}; do
  [ "$a" = "--ensure" ] && HAS_ENSURE=1
  CMD+=("$a")
done
[ -n "$HAS_ENSURE" ] || CMD+=(--ensure)
"${CMD[@]}"
RC=$?

# 收尾：把微信主窗口**放回屏幕**。
# ⚠️ 运行期用 `xdotool windowunmap` 把它整块移出屏幕（这样小程序面板 1198x673 才不被它遮，
#    否则点放大镜/打字会落到主窗口上 —— 实测最坏情况是"关键词被打进聊天输入框、
#    紧跟的回车把它当消息发给好友"）。结束时**必须还原**，否则 KasmVNC 里看到的是
#    「微信不见了」。
# ⚠️ 别用「按可见窗口列表找主窗口」那套：**未映射的窗口不在 `--onlyvisible` 列表里**，
#    必须用 `xdotool search --name`（不带 --onlyvisible）+ `xprop` 认 WM_CLASS。
# ① 先清掉**容器里残留的 python**。异常中止时它们仍在跑重试循环，
#    会在我们还原之后又把主窗口卸掉 —— 实测第一次加收尾时就是这个症状：
#    日志明明打了"已放回屏幕"，一分钟后去看主窗口又是 IsUnMapped。
docker exec "$INSTANCE" sh -c '
for p in $(ls /proc | grep -E "^[0-9]+$"); do
  [ "$(cat /proc/$p/comm 2>/dev/null)" = "python3" ] && kill -9 $p 2>/dev/null
done' >/dev/null 2>&1 || true
sleep 2
# ② windowmap 放回去，然后**校验**（最多三轮）—— 别只发命令不看结果。
docker exec -e DISPLAY=:1 "$INSTANCE" sh -c '
for r in 1 2 3; do
  for i in $(xdotool search --name "^微信$" 2>/dev/null); do
    case "$(xprop -id $i WM_CLASS 2>/dev/null)" in
      *wechat*) xdotool windowmap $i; xdotool windowactivate $i ;;
    esac
  done
  sleep 1
  ok=1
  for i in $(xdotool search --name "^微信$" 2>/dev/null); do
    case "$(xprop -id $i WM_CLASS 2>/dev/null)" in
      *wechat*) xwininfo -id $i 2>/dev/null | grep -q "IsUnMapped" && ok=0 ;;
    esac
  done
  [ "$ok" = "1" ] && { echo "  [restore] 主窗口已在屏幕上（第 ${r} 轮确认）"; break; }
  echo "  [restore] 第 ${r} 轮仍是 IsUnMapped → 再试"
done' 2>/dev/null || true
log "收尾：已把微信主窗口放回屏幕"

log "===== 结束（退出码 $RC）====="
exit $RC
