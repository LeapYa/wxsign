#!/usr/bin/env bash
# 按版本下载并校验微信 Linux 安装包 —— 用来把微信钉死在「已知能挂 hook」的那一版。
#
# ⚠️ 钉旧版有下限：微信服务端会拒绝过旧的客户端登录。实测 4.1.0.13 点登录直接弹
#    「当前微信版本过低，暂无法登录」。建议钉在 4.1.1.x 或更新的版本上。
#
# 为什么需要它：
#   官方 CDN 只有一条不带版本号的直链（`.../WeChatLinux_x86_64.deb`），永远给最新版；
#   微信一升级，WMPF 版本就可能超出 WMPFDebugger 的偏移配置范围，hook 挂不上。
#   Rodert/wechat-linux-versions 按版本归档了官方安装包，每个 Release 还带 sha256 清单，
#   所以可以把版本钉住不动。
#
# 用法:
#   bash fetch_wechat_deb.sh                      # 默认版本，输出到 ./wechat-cdn
#   bash fetch_wechat_deb.sh 4.1.13.9 ./www
#   ARCH=arm64 bash fetch_wechat_deb.sh
#   bash fetch_wechat_deb.sh --list               # 看归档里有哪些版本
#
# 产出:
#   <输出目录>/WeChatLinux_x86_64.deb
#   文件名不要改 —— 实例下载时拼的就是这个名字。
#
# 接着怎么用:
#   把这个目录挂到任意静态服务（nginx / 对象存储 / 自己的服务器都行），
#   启动云微实例时加环境变量（`WECHAT_CDN` 指的是**目录**，不是文件）：
#     -e WECHAT_CDN=https://<你的域名>/<该目录路径>
set -euo pipefail

ARCH="${ARCH:-x86_64}"
REPO="Rodert/wechat-linux-versions"
DEFAULT_VER="4.1.13.23"          # 实测可挂 hook 的版本（WMPF 25665）

VER="${1:-$DEFAULT_VER}"
OUTDIR="${2:-./wechat-cdn}"

say() { printf '%s\n' "$*"; }
die() { printf '[FAIL] %s\n' "$*" >&2; exit 1; }

if [ "$VER" = "--list" ] || [ "$VER" = "-l" ]; then
  say "归档仓库 $REPO 现有版本："
  curl -sSL --max-time 20 "https://api.github.com/repos/$REPO/releases?per_page=100" \
    | grep -oE '"tag_name": *"[^"]+"' | sed 's/.*"v\{0,1\}//; s/"$//' | sort -V -r
  exit 0
fi

command -v curl >/dev/null 2>&1 || die "没有 curl"
command -v sha256sum >/dev/null 2>&1 || die "没有 sha256sum"

FILE="WeChatLinux_${ARCH}.deb"
BASE="https://github.com/$REPO/releases/download/v${VER}"
mkdir -p "$OUTDIR"

say "版本   $VER"
say "架构   $ARCH"
say "输出   $OUTDIR/$FILE"

# ── 1. 拿 sha256 清单（每个 Release 一份，含该版所有文件的哈希）──
SUM_TXT="$(curl -fsSL --max-time 60 "$BASE/WeChatLinux-${VER}.sha256" 2>/dev/null || true)"
if [ -z "$SUM_TXT" ]; then
  say ""
  say "这个版本没有找到（或拉不到 sha256 清单）。归档里现有这些版本："
  curl -sSL --max-time 20 "https://api.github.com/repos/$REPO/releases?per_page=100" \
    | grep -oE '"tag_name": *"[^"]+"' | sed 's/.*"v\{0,1\}//; s/"$//' | sort -V -r | sed 's/^/  /'
  die "请换一个版本重试"
fi

WANT="$(printf '%s\n' "$SUM_TXT" \
  | awk -v f="$FILE" '$1=="File:" && $2==f {found=1; next} found && $1=="Sha256:" {print $2; exit}')"
[ -n "$WANT" ] || die "清单里没有 $FILE 的哈希（这个架构可能没归档）"
say "官方哈希 $WANT"

# ── 2. 已存在且哈希一致就跳过 ──
if [ -f "$OUTDIR/$FILE" ]; then
  have="$(sha256sum "$OUTDIR/$FILE" | awk '{print $1}')"
  if [ "$have" = "$WANT" ]; then
    say ""
    say "[OK] 文件已存在且哈希一致，无需重新下载。"
    exit 0
  fi
  say "本地文件哈希不符，重新下载"
fi

# ── 3. 下载（先落 .part，校验通过再改名，避免半截文件被当成好的）──
say ""
say "开始下载（约 200~300 MB）…"
curl -fL --retry 5 --retry-all-errors --retry-delay 3 \
  -o "$OUTDIR/$FILE.part" "$BASE/$FILE" \
  || die "下载失败（网络问题？也可以先手动下好再放进 $OUTDIR）"

GOT="$(sha256sum "$OUTDIR/$FILE.part" | awk '{print $1}')"
if [ "$GOT" != "$WANT" ]; then
  rm -f "$OUTDIR/$FILE.part"
  die "哈希不一致，已删除半截文件。期望 $WANT，实际 $GOT"
fi
mv "$OUTDIR/$FILE.part" "$OUTDIR/$FILE"

say ""
say "[OK] 已下载并校验通过: $OUTDIR/$FILE"
say ""
say "接着启动实例时加上（注意是目录，最后不要带文件名）："
say "  -e WECHAT_CDN=https://<你的域名>/<指向 $OUTDIR 的路径>"
