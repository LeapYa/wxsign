#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""在小程序包缓存里找证据：wuuxiang 域名 + 签到/游戏/积分商城功能（纯标准库）。

两种信号，都不需要 AES：
  1. **分包名**（就是 wxapkg 的文件名）—— 微信把分包按页面路径命名，
     `_pages_sign_.wxapkg` 就说明这个号有签到页。免费且几乎不会误判。
  2. **包内文本**（V1MMWX 只有首 1024 字节是 AES，正文全在 XOR 尾巴里，
     所以直接 XOR 就能 grep 域名与关键词）。

用法（容器内）：
    python3 pkgprobe.py <appid> [更多 appid...]
    python3 pkgprobe.py --scan-all
退出码：0 = 至少一个命中 wuuxiang；1 = 无
"""
import glob
import os
import sys

PKG_ROOT_GLOB = "/config/.xwechat/radium/users/*/applet/packages"

DOMAIN_NEEDLES = [b"wuuxiang.com", b"crm7game-api", b"i5xforyou"]
# 内容关键词（正文里的字面串）
FEATURE_NEEDLES = {
    "签到": [b"game/sign/signIn", b"game/sign/detail", b"pages/sign", "每日签到".encode(),
             "立即签到".encode(), "签到日历".encode()],
    "抽奖/游戏": [b"game/lot", b"lottery", b"luckdraw", "大转盘".encode(), "刮刮乐".encode(),
                  "幸运转盘".encode(), "抽奖".encode()],
    "积分商城": ["积分商城".encode(), "scoreMall".encode(), "pointMall".encode(),
                 b"score/mall", b"mall/score", "积分兑换".encode()],
}
# 分包名关键词（wxapkg 文件名）
SUBPKG_HINTS = {
    "sign": ["sign", "signin", "checkin"],
    "lottery": ["lot", "lottery", "turntable", "wheel", "scratch", "prize", "draw"],
    "scoreMall": ["score", "point", "mall", "exchange", "goods"],
    "cardhome": ["cardhome", "card", "member", "vip"],
    "coupon": ["coupon"],
}


def xor_tail(path, appid):
    with open(path, "rb") as f:
        data = f.read()
    if data[:6] != b"V1MMWX":
        return data
    key = ord(appid[-2]) if len(appid) >= 2 else 0
    return b"\x00" * 1024 + bytes(b ^ key for b in data[6 + 1024:])


def probe(appid):
    dirs = []
    for root in glob.glob(PKG_ROOT_GLOB):
        d = os.path.join(root, appid)
        if os.path.isdir(d):
            dirs.append(d)
    if not dirs:
        return None
    pkgs, subpkg_names, dom, feats = 0, [], set(), {}
    for d in dirs:
        for p in sorted(glob.glob(os.path.join(d, "*", "*.wxapkg"))):
            pkgs += 1
            base = os.path.basename(p)[:-len(".wxapkg")]
            subpkg_names.append(base)
            try:
                blob = xor_tail(p, appid)
            except Exception as e:
                print("  ! 读不了 %s: %s" % (p, e))
                continue
            for n in DOMAIN_NEEDLES:
                if n in blob:
                    dom.add(n.decode())
            for k, needles in FEATURE_NEEDLES.items():
                for n in needles:
                    if n in blob:
                        feats.setdefault(k, set()).add(n.decode("utf-8", "replace"))
    # 从分包名推功能
    sub_hits = set()
    low = " ".join(subpkg_names).lower()
    for k, keys in SUBPKG_HINTS.items():
        for kw in keys:
            if kw in low:
                sub_hits.add(k)
                break
    return {"appid": appid, "pkgs": pkgs, "domains": sorted(dom),
            "features": {k: sorted(v) for k, v in feats.items()},
            "subpkg_hints": sorted(sub_hits),
            "subpkgs": sorted(set(subpkg_names))}


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if "--scan-all" in sys.argv:
        args = []
        for root in glob.glob(PKG_ROOT_GLOB):
            args += sorted(os.listdir(root))
    if not args:
        print("用法: pkgprobe.py <appid>... 或 --scan-all")
        return 2
    any_hit = False
    for a in args:
        r = probe(a)
        if r is None:
            print("%-20s  未缓存" % a)
            continue
        if r["domains"]:
            any_hit = True
        verdict = "★wuuxiang" if r["domains"] else "非wuuxiang"
        print("%-20s %-11s 包=%-2d %s" % (a, verdict, r["pkgs"],
                                          ",".join(r["domains"]) or "-"))
        if r["subpkg_hints"] or r["features"]:
            print("     分包线索: %s" % (",".join(r["subpkg_hints"]) or "-"))
            for k, v in r["features"].items():
                print("     内容命中·%s: %s" % (k, ",".join(v[:4])))
        subs = [s for s in r["subpkgs"] if s != "__APP__"]
        if subs:
            print("     分包: %s" % ",".join(subs[:14]))
    return 0 if any_hit else 1


if __name__ == "__main__":
    sys.exit(main())
