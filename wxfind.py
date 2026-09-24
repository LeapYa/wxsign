#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""按品牌关键词自动发现小程序 appId（在微信实例容器里运行）。

做的是人工会做的那件事，只是自动：
    打开小程序面板 → 搜索关键词 → 逐张结果卡片点开
    → 从逻辑层读 appId（与窗口标题无关，比标题硬）→ 关掉
    → 再检查每个 appId 的小程序包：是不是吾享、有没有签到能力、属于哪类载体

用法（容器内）：
    DISPLAY=:1 python3 wxfind.py <关键词> [最多点开几张=6]
输出：一行 JSON（stdout 最后一行，前缀 FIND_JSON=），字段见 emit()
依赖（都在同目录）：reopen_miniapp.py（窗口/点击原语）、wxcdp.py（读 appId）、pkgprobe.py（判包）
"""
import glob
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, "/tmp")
import reopen_miniapp as R      # noqa: E402
import wxcdp                    # noqa: E402
import pkgprobe as P            # noqa: E402

# 吾享「游戏型」（签到/大转盘载体）的命名规律 —— 用来给候选排序
SIGN_NAME_HINTS = ["活动号", "活动入口", "互动", "趣玩", "转盘", "YX", "游戏", "+"]
# 微商城型 / 会员卡型的命名规律
MALL_NAME_HINTS = ["甄选", "甄选商城", "铺子", "商城", "零售", "积分"]
CARD_NAME_HINTS = ["会员", "点餐", "智慧餐厅", "VIP", "聚合", "卡"]


def log(*a):
    print("[find] " + " ".join(str(x) for x in a), flush=True)


def blob_of(appid):
    b = b""
    for root in glob.glob(P.PKG_ROOT_GLOB):
        for p in glob.glob(os.path.join(root, appid, "*", "*.wxapkg")):
            try:
                b += P.xor_tail(p, appid)
            except Exception:
                pass
    return b


def inspect(appid):
    """给一个 appId 定性：是不是吾享、有没有签到能力、属于哪类。"""
    r = P.probe(appid)
    if not r:
        return {"cached": False}
    b = blob_of(appid)
    out = {
        "cached": True,
        "wuuxiang": bool(r["domains"]),
        "domains": r["domains"],
        "pkgs": r["pkgs"],
        "sign_api": b"game/sign/signIn" in b,          # 有专用签到模块
        "sign_page": (b"pages/sign" in b) or ("签到".encode() in b),
        "lot_api": b"/api/game/lot/list" in b,          # 有活动壳（大转盘/活动型签到）
        "member_register": b"/api/member/register" in b,
        "subpkgs": r["subpkgs"][:12],
    }
    if not out["wuuxiang"]:
        out["carrier"] = "非吾享"
    elif out["sign_api"]:
        out["carrier"] = "sign"      # 专用签到模块
    elif out["lot_api"]:
        out["carrier"] = "lot"       # 活动壳，签到是其中一种活动
    else:
        out["carrier"] = "other"     # 吾享的会员卡/商城型，没有签到
    return out


def name_score(name):
    for i, h in enumerate(SIGN_NAME_HINTS):
        if h in name:
            return 100 - i
    for h in CARD_NAME_HINTS:
        if h in name:
            return 50
    for h in MALL_NAME_HINTS:
        if h in name:
            return 30
    return 60


def wait_new_window(baseline, timeout=25):
    t0 = time.time()
    while time.time() - t0 < timeout:
        for wid, title in R.windows():
            t = title.strip()
            if wid in baseline or not t or t == "微信":
                continue
            time.sleep(1.5)
            return wid, R.title_of(wid).strip() or t
        time.sleep(0.8)
    return None, None


def main():
    if len(sys.argv) < 2:
        log("用法：wxfind.py <关键词> [最多点开几张]")
        return 2
    kw = sys.argv[1]
    max_cards = int(sys.argv[2]) if len(sys.argv) > 2 else 6

    W, H = R.size()
    if (W, H) != (R.WANT_W, R.WANT_H):
        R.run("DISPLAY=%s xrandr -s %dx%d" % (R.DISPLAY, R.WANT_W, R.WANT_H))
        time.sleep(2)
        W, H = R.size()

    # 先清掉残留的小程序窗口：否则 appId 基线里会混进旧上下文，新窗口也可能被挡住
    try:
        import wxclean
        wxclean.main()
    except Exception as e:
        log("清理残留窗口跳过：%s" % e)

    panel = R.open_panel(W, H)
    if not panel:
        # open_panel 靠「标签条里有小程序的紫色图标」认面板，而实测这个图标我这次拿到的是
        # 深色的（渲染/主题差异），判据没命中 —— 但面板确实就在最上层。
        # 回退：最上层是标题「微信」且没有 WM_CLASS 的浏览器式窗口，就当作面板用；
        # 后面点放大镜 → 输入 → 回车，能不能出结果由 find_cards 兜底验证。
        top = R.top_window(W, H)
        t = R.title_of(top).strip() if top else ""
        if top and t == "微信" and not R.win_class(top):
            log("open_panel 没认出来，但最上层就是「微信」浏览器式窗口 → 直接当面板用（窗口 %s）" % top)
            panel = top
        else:
            log("打不开小程序面板 → 退出（最上层窗口：%r）" % (t or "认不出"))
            return 3
    R.raise_window(panel)
    if str(R.top_window(W, H)) != str(panel):
        log("面板不在最前 → 不输入")
        return 3
    if not R.click_in(panel, R.R_PANEL_MAGNIFIER[0], R.R_PANEL_MAGNIFIER[1], 1.5, "（放大镜）"):
        return 3
    R.key("ctrl+a")
    R.key("Delete")
    R.type_text(kw)
    log("搜索「%s」" % kw)
    R.key("Return", 6.0)
    time.sleep(2)

    cards0 = R.find_cards(R.grab(W, H), W, H)
    log("结果页定位到 %d 张卡片" % len(cards0))
    if not cards0:
        log("搜索没出结果卡片 → 面板状态可能不对（截图 /tmp/shots/）")
        R.png(W, H, "wxfind_noresult.png")
        return 3

    found, tried = [], []
    for _ in range(max_cards):
        R.raise_window(panel)
        time.sleep(0.6)
        cur = R.find_cards(R.grab(W, H), W, H)
        target = None
        for cx, cy in cur:
            if all(abs(cx - a) >= 30 or abs(cy - b) >= 30 for a, b in tried):
                target = (cx, cy)
                break
        if not target:
            break
        tried.append(target)
        cx, cy = target
        baseline = {w for w, _ in R.windows()}
        # appId 也要取基线差：probe_appids() 返回的是**当前所有开着的小程序**，
        # 前面没关掉的窗口会把辣可可、别的品牌一起带进来（实测踩过，会认错号）
        base_ids = set(wxcdp.probe_appids() or [])
        if not R.click_expect(panel, cx, cy, 0.6, "（结果卡片）"):
            continue
        wid, title = wait_new_window(baseline)
        if not wid:
            log("点了卡片但没等到新窗口")
            continue
        now_ids = wxcdp.probe_appids() or []
        new_ids = [a for a in now_ids if a not in base_ids]
        log("%-24s → 新开 %s（窗口内共 %d 个上下文）"
            % (title, ",".join(new_ids) or "(读不到)", len(now_ids)))
        for a in (new_ids or []):
            info = inspect(a)
            info["appid"] = a
            info["name"] = title
            found.append(info)
        if not R.close_window(wid, W, H, kind="miniapp"):
            log("⚠️ 关不掉 %s，可能影响后续" % title)
        time.sleep(1)

    # 关面板（下个关键词从干净状态开始）
    try:
        p = R.find_panel_for_close(W, H)
        if p:
            R.close_window(p, W, H, kind="panel")
    except Exception:
        pass

    # 去重（同名号可能被读到两次）
    uniq, seen = [], set()
    for f in found:
        key = f.get("appid") or f.get("name")
        if key in seen:
            continue
        seen.add(key)
        uniq.append(f)
    uniq.sort(key=lambda f: (0 if f.get("carrier") in ("sign", "lot") else 1, -name_score(f.get("name", ""))))
    print("FIND_JSON=" + json.dumps({"keyword": kw, "found": uniq}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
