# -*- coding: utf-8 -*-
"""面板搜索逐张开卡，CDP 认 appId；命中目标就保持打开并退出。不跑 wxclean、不抬主窗口。"""
import sys
import time
sys.path.insert(0, "/tmp")
import wxopen as R
import wxcdp

TARGET = "wx9c825da1a7ba062e"   # OPPO商城
KW = "OPPO商城"

W, H = R.size()
panel = R.open_panel(W, H)
if not panel:
    print("PROBE FAIL: no panel")
    sys.exit(3)
R.raise_window(panel)
if not R.click_in(panel, R.R_PANEL_MAGNIFIER[0], R.R_PANEL_MAGNIFIER[1], 1.5, "（放大镜）"):
    sys.exit(3)
R.key("ctrl+a"); R.key("Delete")
R.type_text(KW)
R.key("Return", 6.0)
time.sleep(2)

tried = []
for i in range(8):
    R.raise_window(panel)
    time.sleep(0.6)
    cur = R.find_cards(R.grab(W, H), W, H)
    target = None
    for cx, cy in cur:
        if all(abs(cx - a) >= 30 or abs(cy - b) >= 30 for a, b in tried):
            target = (cx, cy)
            break
    if not target:
        print("PROBE: no more cards")
        break
    tried.append(target)
    base = set(wxcdp.probe_appids() or [])
    if not R.click_expect(panel, target[0], target[1], 0.6, "（结果卡片）"):
        continue
    # 等新窗口
    wid = None
    for _ in range(10):
        time.sleep(1.0)
        now = wxcdp.probe_appids() or []
        new = [a for a in now if a not in base]
        if new:
            wid = new
            break
    if not wid:
        print("PROBE: card %d opened but no new appid" % (i + 1))
        continue
    print("PROBE card %d appids=%s" % (i + 1, wid))
    if TARGET in wid:
        print("PROBE FOUND TARGET, kept open")
        R.png(W, H, "target_open.png")
        sys.exit(0)
    # 不是目标：关掉刚开的小程序窗口
    for w, t in R.windows():
        t = (t or "").strip()
        if t and t != "微信" and not R.is_main_window(w):
            R.force_close_miniapp(w, t)
    time.sleep(1)

print("PROBE FAIL: target not among cards")
sys.exit(1)
