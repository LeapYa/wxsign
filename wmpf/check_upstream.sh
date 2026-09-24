#!/usr/bin/env bash
# 查上游 WMPFDebugger：① 现在有哪些 linux 偏移配置；② 有没有人提过「适配某版本」的 PR 还没合并。
#
# 为什么值得先看这个：微信一升级，WMPF 版本就可能超出上游已适配的范围，hook 挂不上。
# 上游常常「已经有人算好了、PR 还开着」—— 先看两眼，能省掉自己算偏移的功夫。
# 实在没有，再走 wmpf/auto_offsets.sh 自己算（那是本仓库自带的能力，不用等上游）。
#
# 用法:
#   bash wmpf/check_upstream.sh              # 看现有配置清单 + 所有开放的 PR
#   bash wmpf/check_upstream.sh 26123        # 额外搜：有没有人提到这个 WMPF 版本号
#   bash wmpf/check_upstream.sh --offline    # 只打印离线兜底清单（不联网）
#
# 纯只读：只发 HTTP GET，不装任何东西、不动容器。
set -u

REPO="evi0s/WMPFDebugger"
API="https://api.github.com"
WANT=""
[ "${1:-}" != "--offline" ] && WANT="${1:-}"

# 拉不到网络时用的兜底（本仓库实测存在过的 linux 配置）
FALLBACK="14910 14978 25665"

TMP="$(mktemp -d 2>/dev/null || echo /tmp/wmpf-up)"
mkdir -p "$TMP"
say() { printf '%s\n' "$*"; }

# 解析 JSON：contents=目录清单 / pulls|issues=编号+状态+标题
parse() {
  if command -v python3 >/dev/null 2>&1; then
    python3 - "$1" "$2" <<'PY'
import json, sys
try:
    d = json.load(open(sys.argv[1], encoding="utf-8"))
except Exception as e:
    print("  (解析失败: %s)" % e); sys.exit(0)
mode = sys.argv[2]
if mode == "contents":
    if isinstance(d, list):
        for it in d:
            print(it.get("name", ""))
    else:
        print("  (上游返回: %s)" % str(d.get("message", d))[:120])
else:
    if not isinstance(d, list):
        print("  (上游返回: %s)" % str(d.get("message", d))[:120]); sys.exit(0)
    if not d:
        print("  （没有开放的条目）")
    for it in d:
        if mode == "issues" and "pull_request" in it:
            continue
        print("  #%-6s %-7s %s" % (it.get("number"), it.get("state", ""), it.get("title", "")[:88]))
PY
  else
    say "  (没有 python3，改用 grep 粗解析)"
    if [ "$2" = "contents" ]; then
      grep -oE '"name": *"[^"]+"' "$1" 2>/dev/null | sed 's/.*"name": *"//; s/"$//'
    else
      grep -oE '"number": *[0-9]+|"title": *"[^"]+"|"state": *"[^"]+"' "$1" 2>/dev/null | head -60
    fi
  fi
}

fetch() {   # $1=url  $2=输出文件
  command -v curl >/dev/null 2>&1 || return 1
  curl -sSL --max-time 25 -H 'Accept: application/vnd.github+json' "$1" -o "$2" 2>/dev/null
}

if [ "${1:-}" = "--offline" ]; then
  say "离线兜底清单（本仓库实测存在过的 linux 配置）: $FALLBACK"
  exit 0
fi

say "上游仓库  https://github.com/$REPO"
[ -n "$WANT" ] && say "要找的版本  $WANT"
say ""

# ── 1. 现有 linux 偏移配置 ──
say "=== 现有 linux 偏移配置 frida/config/linux/ ==="
ok=0
if fetch "$API/repos/$REPO/contents/frida/config/linux" "$TMP/contents.json"; then
  names="$(parse "$TMP/contents.json" contents | grep -oE '[0-9]+' | sort -u | tr '\n' ' ')"
  if [ -n "$names" ]; then
    say "  $names"
    ok=1
  fi
fi
[ "$ok" = 0 ] && say "  (拉不到，用离线兜底: $FALLBACK)"
say ""

# ── 2. 开放的 PR ──
say "=== 开放中的 PR（可能已经有人适配了，只是还没合并）==="
pr_ok=0
if fetch "$API/repos/$REPO/pulls?state=open&per_page=100&sort=created&direction=desc" "$TMP/pulls.json"; then
  parse "$TMP/pulls.json" pulls
  pr_ok=1
fi
[ "$pr_ok" = 0 ] && say "  (拉不到，多半是网络或 GitHub 限流 —— 限流时等一小时再试)"
say ""

# ── 3. 最近更新的 issue（有人报版本、或贴了偏移）──
say "=== 最近更新的 issue ==="
if fetch "$API/repos/$REPO/issues?state=all&per_page=30&sort=updated&direction=desc" "$TMP/issues.json"; then
  parse "$TMP/issues.json" issues
fi
say ""

# ── 4. 按版本号搜 ──
if [ -n "$WANT" ]; then
  say "=== 搜「$WANT」相关（PR + issue 标题/正文）==="
  hit=0
  for f in "$TMP/pulls.json" "$TMP/issues.json"; do
    [ -f "$f" ] || continue
    if grep -q "$WANT" "$f" 2>/dev/null; then
      grep -oE '"number": *[0-9]+|"title": *"[^"]+"' "$f" | head -30 | sed 's/^/  /'
      hit=1
    fi
  done
  [ "$hit" = 0 ] && say "  没搜到提到 $WANT 的 PR/issue —— 大概率要自己算偏移（wmpf/auto_offsets.sh）"
fi

say ""
say "下一步怎么走："
say "  · 上游已有你这一版的配置 → 直接用，什么都不用做"
say "  · 上游有人开了 PR 但没合并 → 去 PR 里把 offsets JSON 抄下来，自己放进"
say "      hook 容器 \$WMPF_DIR/frida/config/linux/addresses.<版本>.json"
say "  · 什么都没有 → 自己算：bash wmpf/auto_offsets.sh \$WX    （见 wmpf/offsets/README.md）"
say "  · 不想算 → 把微信钉回已知可用的旧版：bash wmpf/fetch_wechat_deb.sh 4.1.13.23 ./wechat-cdn"
rm -rf "$TMP" 2>/dev/null
