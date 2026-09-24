#!/usr/bin/env bash
# 微信 WMPF 版本漂了以后，自己把新的 hook 偏移算出来并装进 WMPFDebugger —— 不用等上游补配置。
#
# 做的事：从微信实例容器里抠出 WeChatAppEx → 离线反解出 4 个偏移 → 判卷（若上游已有同名配置）
#        → 拷进 hook 容器的 WMPFDebugger 配置目录。
#
# 用法:
#   bash auto_offsets.sh                          # 默认实例名 woc-wx-2ada0225ca
#   bash auto_offsets.sh <实例容器名>
#   bash auto_offsets.sh <实例容器名> --work /root/.wmpf-offsets
#
# 环境变量: HOOK_CONTAINER（默认 woc-hook）、WMPF_DIR（hook 容器里 WMPFDebugger 的路径，默认 /opt/wmpf）
#
# 依赖: 宿主机上有 python3 + capstone + numpy
#   pip install capstone numpy        （Debian/Ubuntu 若报 externally-managed-environment，
#                                      加 --break-system-packages，或 apt install python3-capstone python3-numpy）
set -u

INSTANCE="${1:-${WOC_INSTANCE:-woc-wx-2ada0225ca}}"
WORK="$PWD/.wmpf-offsets"
if [ "${2:-}" = "--work" ] && [ -n "${3:-}" ]; then WORK="$3"; fi
HOOK="${HOOK_CONTAINER:-woc-hook}"
WMPF_DIR="${WMPF_DIR:-/opt/wmpf}"
EXE_IN_CT="/config/wechat/opt/wechat/RadiumWMPF/runtime/WeChatAppEx"
CONFIG_SUB="frida/config/linux"
PY="${PYTHON:-python3}"

here="$(cd "$(dirname "$0")" && pwd)"
say() { printf '%s\n' "$*"; }
die() { printf '[FAIL] %s\n' "$*" >&2; exit 1; }

say "[1/5] 检查依赖"
command -v docker >/dev/null 2>&1 || die "没有 docker"
command -v "$PY" >/dev/null 2>&1 || die "没有 $PY"
"$PY" -c "import capstone, numpy" 2>/dev/null \
  || die "缺依赖。先装：$PY -m pip install capstone numpy"
[ -f "$here/offsets/recover_offsets.py" ] || die "找不到 offsets/recover_offsets.py（请在仓库根目录跑）"

say "[2/5] 从实例 $INSTANCE 里抠 WeChatAppEx"
docker inspect "$INSTANCE" --format '{{.State.Status}}' >/dev/null 2>&1 \
  || die "容器 $INSTANCE 不存在"
mkdir -p "$WORK"
docker cp "$INSTANCE:$EXE_IN_CT" "$WORK/WeChatAppEx" \
  || die "抠文件失败（微信没装好？路径 $EXE_IN_CT）"
say "      $(stat -c%s "$WORK/WeChatAppEx" 2>/dev/null || stat -f%z "$WORK/WeChatAppEx") 字节"

say "[3/5] 反解偏移"
"$PY" "$here/offsets/recover_offsets.py" "$WORK/WeChatAppEx" --out "$WORK/addresses.new.json" \
  || die "反解失败，看上面的报错（多半是锚点变了，见 offsets/README.md 的规则表）"

VER="$("$PY" -c "import json;print(json.load(open('$WORK/addresses.new.json',encoding='utf-8'))['Version'])")"
say "      WMPF 版本 = $VER"

say "[4/5] 判卷（上游若已有这个版本的配置）"
if docker exec "$HOOK" test -f "$WMPF_DIR/$CONFIG_SUB/addresses.$VER.json" 2>/dev/null; then
  docker cp "$HOOK:$WMPF_DIR/$CONFIG_SUB/addresses.$VER.json" "$WORK/upstream.$VER.json"
  "$PY" "$here/offsets/judge.py" "$WORK/addresses.new.json" "$WORK/upstream.$VER.json" \
    || say "      ⚠️ 与上游配置不一致——先把两份 JSON 贴出来看看再装"
else
  say "      上游没有 addresses.$VER.json（这正是要自己算的情形）"
fi

say "[5/5] 装进 hook 容器的 WMPFDebugger"
if docker inspect "$HOOK" >/dev/null 2>&1; then
  docker cp "$WORK/addresses.new.json" "$HOOK:$WMPF_DIR/$CONFIG_SUB/addresses.$VER.json" \
    || die "拷贝失败"
  say "已写入 $HOOK:$WMPF_DIR/$CONFIG_SUB/addresses.$VER.json"
  say ""
  say "接着重启 hook 让它生效："
  say "  bash wmpf/restart_hook.sh"
  say "（它只在启动时 attach 一次，换配置后必须重启）"
else
  say "      hook 容器 $HOOK 不在，跳过；产出在 $WORK/addresses.new.json"
  say "      等 hook 起来后自己拷："
  say "        docker cp $WORK/addresses.new.json $HOOK:$WMPF_DIR/$CONFIG_SUB/addresses.$VER.json"
fi
