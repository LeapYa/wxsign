#!/usr/bin/env bash
# 校验微信实例的 WMPF 版本是否有 WMPFDebugger 的 linux 偏移配置。
# 只有版本对得上，frida 才挂得上。
#
# 用法:  bash check_wmpf.sh [实例容器名]
#        实例容器名默认取 $WOC_INSTANCE，再不行用 woc-wx-2ada0225ca
#
# 纯只读：只 docker exec 读容器里的文件，不重启、不影响微信登录态。
set -u

INSTANCE="${1:-${WOC_INSTANCE:-woc-wx-2ada0225ca}}"
WECHAT_ROOT="/config/wechat"
EXE="$WECHAT_ROOT/opt/wechat/RadiumWMPF/runtime/WeChatAppEx"

# 上游离线兜底清单（拉不到 GitHub 时用）；会优先尝试拉最新
FALLBACK="14910 14978 25665"

say() { printf '%s\n' "$*"; }

say "[check] 实例: $INSTANCE"

if ! command -v docker >/dev/null 2>&1; then
  say "[FAIL] 找不到 docker 命令"; exit 2
fi
if ! docker exec "$INSTANCE" true >/dev/null 2>&1; then
  say "[FAIL] 容器 $INSTANCE 不在运行（先 docker start $INSTANCE）"; exit 2
fi

# 1) 面板记录的微信版本
ver="$(docker exec "$INSTANCE" cat "$WECHAT_ROOT/.woc-version" 2>/dev/null | tr -d '\r')"
[ -n "$ver" ] && say "        微信版本: $ver" || say "        微信版本: (读不到 .woc-version)"

# 2) WMPF release 串，例如 xwechat_2026T6_2.5.6
rel="$(docker exec "$INSTANCE" sh -c "grep -aoE 'xwechat_[A-Za-z0-9_.]+' '$EXE' 2>/dev/null | sort -u | head -1" | tr -d '\r')"
if [ -z "$rel" ]; then
  say "[FAIL] 读不到 WMPF 版本串（微信没装好？路径 $EXE）"; exit 2
fi
say "        WMPF release: $rel"

# 3) 候选版本串。注意：直接宽匹配 [0-9]+.[0-9]+.[0-9]+.[0-9]{3,6} 会捞到一堆噪声
#    （DNS 地址、Chromium 版本、显卡驱动版本…），所以先用 release 串里的 x.y.z 锚定，
#    例如 release=xwechat_2026T6_2.5.6 → 只找 2.5.6.<build>。
wmpf_ver="$(printf '%s' "$rel" | sed 's/.*_//')"
cands=""
case "$wmpf_ver" in
  [0-9]*.[0-9]*.[0-9]*)
    pat="$(printf '%s' "$wmpf_ver" | sed 's/\./\\./g')"
    cands="$(docker exec "$INSTANCE" sh -c "grep -aoE '$pat\\.[0-9]{3,6}' '$EXE' 2>/dev/null | sort -u" | tr -d '\r')"
    ;;
esac
if [ -z "$cands" ]; then
  # 兜底：宽匹配 + 成员判定（噪声的最后一段一般不会正好等于配置号）
  say "        ⚠️ 没能由 release 串锚定版本，改用宽匹配"
  cands="$(docker exec "$INSTANCE" sh -c "grep -aoE '[0-9]+\\.[0-9]+\\.[0-9]+\\.[0-9]{3,6}' '$EXE' 2>/dev/null | sort -u" | tr -d '\r')"
fi
if [ -z "$cands" ]; then
  say "[FAIL] 读不到候选版本串"; exit 2
fi
say "        WMPF 版本串: $(printf '%s' "$cands" | tr '\n' ' ')"

# 4) 上游现有 linux 偏移配置
supported=""
if command -v curl >/dev/null 2>&1; then
  supported="$(curl -s --max-time 12 \
    https://api.github.com/repos/evi0s/WMPFDebugger/contents/frida/config/linux \
    | grep -oE 'addresses\.[0-9]+\.json' | grep -oE '[0-9]+' | sort -u | tr '\n' ' ')"
fi
if [ -z "$supported" ]; then
  say "        (拉不到上游清单，用内置兜底)"
  supported="$FALLBACK"
fi
say "        上游 linux 偏移配置: $supported"

# 5) 判定：取候选串第 4 段，看是否在清单里
hit=""
for c in $cands; do
  last="$(printf '%s' "$c" | awk -F. '{print $NF}')"
  for s in $supported; do
    if [ "$last" = "$s" ]; then hit="$s"; break 2; fi
  done
done

say ""
if [ -n "$hit" ]; then
  say "[OK]   WMPF $hit 有对应偏移配置（frida/config/linux/addresses.$hit.json），可以继续。"
  exit 0
fi

say "[FAIL] 这个 WMPF 版本没有对应偏移配置，hook 会挂不上。"
say "       可选处理（成本从低到高）："
say "       1) 把最接近的配置复制成新版本号试挂（同一 WMPF x.y.z 下不同 build 偏移可能相同）"
say "       2) 看看上游有没有人提过这一版的 PR（可能已经修好、只是没合并）："
say "          bash wmpf/check_upstream.sh            # 列出开放的 PR / issue"
say "          bash wmpf/check_upstream.sh 26123      # 带上版本号，直接搜"
say "       3) 自己算偏移（本仓库自带工具，不用等上游补配置）："
say "          bash wmpf/auto_offsets.sh <实例容器名>     # 一条命令：抠二进制 -> 算 -> 装进 WMPFDebugger"
say "          详见 wmpf/offsets/README.md"
say "       4) 把微信钉回已知可用的旧版（本项目 fetch_wechat_deb.sh 按版本下载并校验 sha256）："
say "          bash wmpf/fetch_wechat_deb.sh 4.1.13.23 ./wechat-cdn"
say "          再把该目录挂到静态服务，启动实例时加 -e WECHAT_CDN=https://<你的镜像>/weixin/Universal/Linux"
say "          ⚠️ 钉旧版有下限：微信服务端会拒过旧客户端（实测 4.1.0.13 提示「版本过低」）"
say "       5) 转 Windows 方案（上游 win32 有 53 份配置，linux 只有 3 份）"
exit 1
