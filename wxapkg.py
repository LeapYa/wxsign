#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""解 PC 微信的小程序包（wxapkg）并在里面搜东西 —— **不用打开小程序就能逆向**。

## 包在哪
```
/config/.xwechat/radium/users/<用户哈希>/applet/packages/<appid>/<包版本>/*.wxapkg
```
（`<用户哈希>` 是微信号对应的目录，可能有多个；包版本如 `302`。）

## 为什么能解（关键，别再怀疑方法失效）
`V1MMWX` 是 PC 微信给 wxapkg 加的**加密头**，但**只有首 `6+1024` 字节**是密文，
**其余正文整体只做了单字节 XOR**，密钥就是 `ord(appid[-2])`：
```
明文 = data[1030:] 逐字节 ^ ord(appid[-2])
```
实测在**蜜雪冰城**（`wx7696c66d2245d107` → key=48=`'0'`）、吾享系、企迈系都成立，
纯标准库就能还原出明文 JS。
（因为只有尾部做 XOR，任何"按标准 wxapkg 头解析"的写法都会在 `fileCount` 处报
 `struct.error` —— 那不是格式变了，是**没走 XOR 这条路**。）

## 用法（容器内跑）
```
python3 wxapkg.py files <appid>                      # 列出包内文件（含分包名，分包名本身就暴露功能）
python3 wxapkg.py grep  <appid> '<正则>' [--ctx N] [--max N]
python3 wxapkg.py feat  <appid>                      # 判厂商 + 找签到/抽奖/积分商城
```
"""
import glob
import os
import re
import sys
from collections import Counter

PKG_ROOT_GLOB = "/config/.xwechat/radium/users/*/applet/packages"

# 厂商判定（域名特征）
DOMAIN_NEEDLES = [b"wuuxiang.com", b"crm7game-api", b"i5xforyou", b"qmai.cn",
                  b"eingdong.com", b"funjs.top", b"mxbc.net"]
# 功能关键词
FEATURE_NEEDLES = {
    "签到": ["签到", "每日签到", "立即签到", "签到日历", "/signin", "signIn", "daySign", "signAddCoin"],
    "抽奖/游戏": ["抽奖", "大转盘", "刮刮乐", "幸运转盘", "lottery", "luckdraw", "turntable"],
    "积分商城": ["积分商城", "积分兑换", "scoreMall", "pointMall", "雪王币", "积分"],
    "会员/等级": ["会员权益", "等级", "会员日", "levelRights"],
}
SUBPKG_HINTS = {
    "sign": ["sign", "signin", "checkin"],
    "lottery": ["lot", "lottery", "turntable", "wheel", "scratch", "prize", "draw"],
    "scoreMall": ["score", "point", "mall", "exchange", "goods"],
    "member": ["cardhome", "card", "member", "vip", "level"],
    "coupon": ["coupon"],
    "customer-center": ["customer-center", "customercenter"],
}


def xor_tail(data, appid):
    """V1MMWX → 明文；非 V1MMWX 直接返回。"""
    if data[:6] != b"V1MMWX":
        return data
    key = ord(appid[-2]) if len(appid) >= 2 else 0
    return b"\x00" * 1030 + bytes(b ^ key for b in data[1030:])


def find_pkgs(appid):
    out = []
    for root in glob.glob(PKG_ROOT_GLOB):
        d = os.path.join(root, appid)
        if not os.path.isdir(d):
            continue
        out += sorted(glob.glob(os.path.join(d, "*", "*.wxapkg")))
    return out


def load_text(appid, meta=False):
    """把该 appid 下所有 wxapkg（含分包）解成一段可 grep 的文本。"""
    txt, names, pkgs = [], [], find_pkgs(appid)
    for p in pkgs:
        names.append(os.path.basename(p)[:-len(".wxapkg")])
        try:
            with open(p, "rb") as f:
                raw = f.read()
            txt.append(xor_tail(raw, appid).decode("utf-8", "replace"))
        except Exception as e:
            print("  ! 读不了 %s: %s" % (p, e))
    joined = "\n".join(txt)
    return (joined, names, pkgs) if meta else joined


def cmd_files(appid):
    """列包内文件。

    ⚠️ 不能按常规 wxapkg 索引段解析：V1MMWX 的正文布局与标准 wxapkg 不同
    （`__APP__.wxapkg` 解开头那 0xBE 索引段后会直接越界）。
    所以改成**从明文里正则提取路径字面量** —— 页面/组件/工具模块的路径都会在 JS 里出现，
    效果等价（而且不受分包边界影响）。
    """
    pkgs = find_pkgs(appid)
    if not pkgs:
        print("%s 未缓存（先在微信里打开过这个小程序）" % appid)
        return 1
    txt, names, _ = load_text(appid, meta=True)
    print("%s：%d 个包" % (appid, len(pkgs)))
    for p in pkgs:
        print("  %-38s %8d B" % (os.path.basename(p), os.path.getsize(p)))
    found = set(re.findall(r"""['"]([A-Za-z0-9_\-/\.@]+\.(?:js|json|wxml|wxss))['"]""", txt))
    pages = sorted({m for m in found if "/" in m and not m.startswith("@")})
    print()
    print("--- 路径字面量（%d 个，取含目录的）---" % len(pages))
    for m in pages[:80]:
        print("   %s" % m)
    if len(pages) > 80:
        print("   …（共 %d 个）" % len(pages))
    return 0


def cmd_grep(appid, rx, ctx=0, mx=40):
    txt = load_text(appid)
    if not txt:
        print("%s 未缓存" % appid)
        return 1
    try:
        cre = re.compile(rx)
    except re.error as e:
        print("正则错: %s" % e)
        return 2
    hits = Counter()
    for m in cre.finditer(txt):
        s = m.group(0)
        if ctx:
            a = max(0, m.start() - ctx // 2)
            s = re.sub(r"\s+", " ", txt[a:m.end() + ctx // 2])
        hits[s] += 1
    print("%s  命中 %d，唯一 %d" % (appid, sum(hits.values()), len(hits)))
    for s, c in hits.most_common(mx):
        print("  x%-4d %s" % (c, s[:1500]))
    return 0


def cmd_feat(appid):
    txt, names, _ = load_text(appid, meta=True)
    if not names:
        print("%s 未缓存" % appid)
        return 1
    print("%s：%d 个包" % (appid, len(names)))
    print("  分包: %s" % (", ".join(n for n in names if n != "__APP__") or "-"))
    dom = sorted({d.decode() for d in DOMAIN_NEEDLES if d.decode().encode() in txt.encode("utf-8", "replace")})
    print("  域名特征: %s" % (", ".join(dom) or "-"))
    for k, words in FEATURE_NEEDLES.items():
        hit = [w for w in words if w in txt]
        if hit:
            print("  %-8s 命中: %s" % (k, ", ".join(hit[:6])))
    low = " ".join(names).lower()
    sub = [k for k, keys in SUBPKG_HINTS.items() if any(x in low for x in keys)]
    if sub:
        print("  分包线索: %s" % ", ".join(sub))
    return 0


def main():
    a = sys.argv[1:]
    if len(a) < 2:
        print(__doc__)
        return 2
    mode, appid = a[0], a[1]
    if mode == "files":
        return cmd_files(appid)
    if mode == "grep":
        if len(a) < 3:
            print("用法: grep <appid> '<正则>' [--ctx N] [--max N]")
            return 2
        return cmd_grep(appid, a[2],
                        ctx=int(a[a.index("--ctx") + 1]) if "--ctx" in a else 0,
                        mx=int(a[a.index("--max") + 1]) if "--max" in a else 40)
    if mode == "feat":
        return cmd_feat(appid)
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
