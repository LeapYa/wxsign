# -*- coding: utf-8 -*-
"""荣耀商城（honor）登录 / 授权处理 —— 全程按 **结构 + 文案** 定位，**不硬编码任何坐标**。

## 一、「同意」按钮怎么找
荣耀商城「我的」页首次进入会弹**隐私声明**（"荣耀商城服务协议与隐私的声明"）。
实测它的 DOM：

    WX-VIEW   「拒绝\\n同意」  button_box  AuthorizeModal-…  (510,628) 1020x44
    WX-BUTTON 「拒绝」         cancel_btn  AuthorizeModal-…  (258,628) 469x44
    WX-BUTTON 「同意」         confirm_btn AuthorizeModal-…  (762,628) 469x44

判据用类名 **`.confirm_btn`**（与版本无关的结构判据），坐标现场用
`wxdom.clickable()` 取 —— 不写死任何像素。

## 二、⭐ 页面坐标 → 屏幕坐标：**不能用 `outerHeight - innerHeight`**
微信小程序窗口里，**渲染层的 `innerHeight` 已经去掉了底部原生 tabBar**，
所以 `outerHeight - innerHeight` = **标题栏 + tabBar**，把它整个当「顶部偏移」会
**整体点低一个 tabBar 的高度**。荣耀商城实测：

    窗口          1022x810 @ (129,107)      （渲染层 screenX/screenY）
    渲染层 innerH  709        ← ctx=11「我的」页
    → outerH - innerH = 101  ❌（= 标题栏 44 + tabBar 55）
    逻辑层 getSystemInfoSync():
        safeArea.top    = 44   ← ✅ 标题栏高度，才是真正的顶部偏移
        windowHeight    = 764  （= 808 - 44）
        764 - 709       = 55   ← 底部 tabBar

所以本模块自己算原点：**`页面原点 = 窗口原点(screenX/screenY) + safeArea.top`**，
`safeArea.top` 从**逻辑层** `wx.getSystemInfoSync()` 拿（结构量，不是魔法数）。
实测这样算出 (129, 151)，与截图里按钮的真实位置吻合；用 101 则会点空。

## 三、取身份：凭证是 **cookie**，签到是**纯 HTTP**
荣耀商城的签到**不在小程序原生页** —— 小程序本身（卖手机那个）**没有签到**。
签到在**任务中心 H5**（`www.honor.com/cn/msale/mp/jobcenter.html`），
由小程序页面 `packageActivity/pages/login4Qxmp/login4Qxmp`（标题「任务页面」）用 web-view 承载。

但**签到不需要打开那个 H5**（2026-09-28 实测，全链路纯 HTTP）：
  · **`activityCode`（每期会变的那个）** —— 一次普通 GET 拿 H5 的 HTML，
    正则提 `data-activity-code` 就行。**不需要 cookie、不需要打开页面。**
  · **凭证全在 `.honor.com` 的 cookie 里**：`euid` / `encryptRtNew` / `CSRF-TOKEN`。
    而 `Network.getAllCookies` 是 **browser 级**命令 —— attach 到**任意** target
    （哪怕只是小程序的 `page-frame.html`）就能读到整份 cookie jar
    → **不需要打开 H5 就能取身份**。
  · 剩下就是两个 POST（纯 HTTP），见 `wxsign.py` 的 `do_sign_honor`。

所以本模块只干两件事：
  ① `ident` —— 从 CDP 读 `.honor.com` 的 cookie jar（交给引擎纯 HTTP 用）
  ② 首次接入的**一次性**授权（`login`）+ 排障用的 UI 工具（`status/agree/personal/tap`）

## 四、用法（容器内跑）
    python3 wxhonor.py ident    # 【引擎用】读 cookie jar → 打印 HONOR_IDENT={...}
    python3 wxhonor.py status   # 当前在哪一页 / 隐私弹窗在不在 / 登录态
    python3 wxhonor.py origin   # 打印页面原点与它的推导过程
    python3 wxhonor.py agree    # 点掉隐私声明的「同意」（加 --dry 只看不点）
    python3 wxhonor.py login    # 点「点击账号登录」，触发微信手机号授权（一辈子一次）
    python3 wxhonor.py personal # 逻辑层 wx.switchTab 跳到「我的」页
    python3 wxhonor.py tap <文案>  # 按文案点（现取坐标）
"""
import json
import subprocess
import sys
import time

sys.path.insert(0, "/tmp")
import wxcdp
import wxdom
import wxqm_auth          # 复用它的 find_allow_btn（微信原生授权框的唯一判据）
import wxreg

# ── 结构判据 ──
CONFIRM_SELECTOR = ".confirm_btn"                       # 「同意」按钮
CONFIRM_SELECTORS = (".confirm_btn", "[class*=confirm_btn]")
MODAL_SELECTORS = (".confirm_btn", "[class*=AuthorizeModal]")   # 隐私声明弹窗
LOGIN_WORDS = ("点击账号登录", "立即登录", "去登录")     # 未登录时的登录入口

APPID = "wx06a8d8c84a18be25"                            # 荣耀商城
TAB_PERSONAL = "/pages/personal/personal"
TASK_PAGE = "/packageActivity/pages/login4Qxmp/login4Qxmp"   # 任务中心（内嵌签到 H5）
H5_KEY = "jobcenter"                                    # H5 target 的 URL 特征
DISPLAY = ":1"
DRY = "--dry" in sys.argv
JSON_OUT = "--json" in sys.argv


def _ws():
    ws = wxcdp.WS(timeout=25)
    ws.send({"id": 1, "method": "Runtime.enable"})
    time.sleep(0.5)
    return ws


def _broadcast_pick(ws, expr, limit=80, timeout=12.0, want="1"):
    """盲撒 ctx 求值，返回 [(ctx, value)]。**先全发再收** —— 逐个等超时太慢。"""
    for c in range(1, limit + 1):
        ws.send({"id": 7000 + c, "method": "Runtime.evaluate",
                 "params": {"expression": expr, "contextId": c, "returnByValue": True}})
    out = []
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            o = json.loads(ws.recv_msg())
        except Exception:
            break
        i = o.get("id")
        if i and i >= 7000:
            v = (o.get("result") or {}).get("result", {}).get("value")
            if v is not None and str(v) != "null":
                out.append((i - 7000, v))
    return out


def pick_ctx(ws, selectors, visible=False):
    """**先说要找什么，再看哪个 ctx 有**（本项目最深的坑：不能先挑「元素最多的 ctx」）。"""
    hits = wxdom.find_ctx_with(ws, list(selectors), visible=visible)
    return max(hits, key=lambda k: hits[k]) if hits else 0


def logic_info(ws):
    """逻辑层 ctx + `wx.getSystemInfoSync()`。逻辑层判据 = 有 `wx.getSystemInfoSync`。"""
    expr = ("(function(){try{return JSON.stringify(wx.getSystemInfoSync())}"
            "catch(e){return 'ERR'}})()")
    best = 0
    info = None
    for ctx, v in _broadcast_pick(ws, expr):
        if isinstance(v, str) and v.startswith("{") and "windowHeight" in v:
            best, info = ctx, json.loads(v)
            break
    return best, info


def origin(ws, dom_ctx):
    """页面 (0,0) 的屏幕坐标 (ox, oy, dpr, note)。

    = 渲染层窗口原点 (screenX/screenY) + 逻辑层 safeArea.top（标题栏高度）。
    ⚠️ 见模块 docstring 第二节：**不要**用 outerHeight - innerHeight ——
       当渲染面是「底部有原生 tabBar 的页面」时（innerHeight 已经扣掉了 tabBar），
       那个差值会把 tabBar 也算成顶部偏移。实测「我的」页 ctx=11 的 innerHeight=709，
       oh-ih=101 会点低 55px；而首页 ctx=6 的 innerHeight=765，oh-ih=45 恰好正确 ——
       所以「同一个 wxreg 判据，换个 ctx 结论就变了」是它的隐患。
    """
    g = json.loads(wxdom.evaluate(
        ws, "JSON.stringify({sx:screenX,sy:screenY,dpr:devicePixelRatio})",
        ctx=dom_ctx, timeout=8.0))
    _lc, info = logic_info(ws)
    top = 0
    if info:
        top = int((info.get("safeArea") or {}).get("top") or 0) or int(info.get("statusBarHeight") or 0)
    note = "窗口原点 (%s,%s) + safeArea.top %s" % (g["sx"], g["sy"], top)
    return int(g["sx"]), int(g["sy"]) + top, float(g.get("dpr") or 1), note


def set_page(ws, dom_ctx):
    """把 `wxreg._PAGE` 设成**正确**的原点，好让 `wxreg.grab()` / `wxqm_auth.find_allow_btn()`
    和点击落在同一个坐标系里（三者必须自洽）。原点同样用 safeArea.top 修。"""
    ox, oy, dpr, note = origin(ws, dom_ctx)
    g = json.loads(wxdom.evaluate(
        ws, "JSON.stringify({iw:innerWidth,ih:innerHeight})", ctx=dom_ctx, timeout=8.0))
    wxreg._PAGE.update(x=ox, y=oy, w=int(g["iw"]), h=int(g["ih"]), dpr=dpr, src="cdp")
    return note


def click_page(ox, oy, dpr, x, y):
    """点**页面坐标** (x,y)：换算成屏幕坐标后走真实鼠标事件。"""
    sx = ox + int(round(x * dpr))
    sy = oy + int(round(y * dpr))
    subprocess.run(["xdotool", "mousemove", "--sync", str(sx), str(sy)])
    time.sleep(0.45)
    subprocess.run(["xdotool", "click", "1"])
    return sx, sy


def cmd_origin():
    ws = _ws()
    ctx = pick_ctx(ws, CONFIRM_SELECTORS) or pick_ctx(ws, ("[class*=AuthorizeModal]",)) or wxdom.find_dom_ctx(ws, 80)
    ox, oy, dpr, note = origin(ws, ctx)
    _lc, info = logic_info(ws)
    print("[honor] 渲染层 ctx=%d" % ctx)
    print("[honor] 页面原点 = (%d,%d)  dpr=%s   ← %s" % (ox, oy, dpr, note))
    if info:
        print("[honor] 逻辑层：windowHeight=%s screenH=%s safeArea.top=%s statusBarHeight=%s" % (
            info.get("windowHeight"), info.get("screenHeight"),
            (info.get("safeArea") or {}).get("top"), info.get("statusBarHeight")))
    return 0


def cmd_status():
    ws = _ws()
    ctx = pick_ctx(ws, MODAL_SELECTORS) or pick_ctx(ws, ("[class*=AuthorizeModal]",))
    vis = wxdom.find_ctx_with(ws, list(MODAL_SELECTORS), visible=True)
    modal = bool(vis)
    d = wxdom.scan(ws, ctx) if ctx else None
    t = (wxdom.page_text(d) if d else "") or ""
    print("[honor] 渲染面 ctx=%s" % (ctx or "-"))
    print("[honor] 隐私声明弹窗：%s" % ("**在**" if modal else "不在"))
    print("[honor] 登录态：%s" % ("未登录" if ("未登录" in t or "点击账号登录" in t) else "已登录"))
    print("[honor] 页面文字：%s" % t[:200])
    return 0


def cmd_agree():
    ws = _ws()
    ctx = pick_ctx(ws, MODAL_SELECTORS)
    if not ctx:
        print("[honor] 没找到隐私声明弹窗（可能已经同意过了）")
        return 1
    c = wxdom.clickable(ws, ctx, CONFIRM_SELECTOR)
    if not c or not c["items"]:
        print("[honor] ctx=%d 里没有 `%s`" % (ctx, CONFIRM_SELECTOR))
        return 1
    it = c["items"][0]
    ox, oy, dpr, note = origin(ws, ctx)
    print("[honor] 命中「同意」：%s %s 页面坐标 (%d,%d) %dx%d" % (
        it["tag"], it["cls"][:32], it["cx"], it["cy"], it["w"], it["h"]))
    print("[honor] 页面原点 (%d,%d) ← %s" % (ox, oy, note))
    if DRY:
        print("[honor] --dry：不点击")
        return 0
    sx, sy = click_page(ox, oy, dpr, it["cx"], it["cy"])
    print("[honor] 已点「同意」→ 屏幕 (%d,%d)" % (sx, sy))
    time.sleep(2.0)
    vis = wxdom.find_ctx_with(ws, list(MODAL_SELECTORS), visible=True)
    print("[honor] 复查：弹窗 %s" % ("仍可见（没点中？）" if vis else "已关闭 ✅"))
    return 0


def find_allow_btn_fullscreen():
    """微信原生授权框的主按钮（「允许」/「同意」）→ **屏幕坐标**；没有返回 None。

    ⚠️ 为什么不能「按 x 列统计 + 间隙合并」（第一版就是这么写的，踩了两个坑）：
      1. 微信原生授权框的「允许」在白卡里，但**微信客户端自己的绿色「搜索」按钮**
         也在同一屏（宽 ≈60）；
      2. 两处绿色元素在 **x 方向相邻**时，列统计会把它们**连成一段**
         （实测连成 x=548..823、y 跨 72..786 的一条“巨段”，宽高全废）。
    所以改成 **逐行扫描 + 按 x 中心聚类成块**：
      · 每行只取「宽 100~260」的绿色横段（微信搜索按钮 ≈60 被排除、整页背景被排除）；
      · 同一 x 中心（±30）的相邻行归为同一个块；
      · 块的高度落在 28~80 之间才算按钮（授权框主按钮 ≈ 115×44 或 155×52）。
    于是「哪一行、哪一段」都保留了几何，不会再跨元素粘在一起。

    颜色判据在这里是**合规**的：README 明确写「微信原生弹窗是**唯一**允许用颜色的地方
    —— 它的文案与配色由微信客户端定死，不随品牌变」。
    """
    W, H = wxreg.display_size()
    buf = wxreg.grab(W, H, win=False)

    def row_runs(y, min_w, max_w):
        out, start = [], None
        for x in range(W):
            i = (y * W + x) * 3
            if wxreg.is_green((buf[i], buf[i + 1], buf[i + 2])):
                if start is None:
                    start = x
            else:
                if start is not None and min_w <= x - 1 - start <= max_w:
                    out.append((start, x - 1))
                start = None
        if start is not None and min_w <= W - 1 - start <= max_w:
            out.append((start, W - 1))
        return out

    blocks = []            # [cx, x0, x1, y0, y1]
    for y in range(0, H, 2):
        for x0, x1 in row_runs(y, 100, 260):
            cx = (x0 + x1) // 2
            for b in blocks:
                if abs(b[0] - cx) <= 30:
                    b[0] = (b[0] + cx) // 2
                    b[1], b[2] = min(b[1], x0), max(b[2], x1)
                    b[4] = y
                    break
            else:
                blocks.append([cx, x0, x1, y, y])

    ok = [b for b in blocks if 28 <= (b[4] - b[3]) <= 80]
    if not ok:
        return None
    ok.sort(key=lambda b: (b[4] - b[3]) * (b[2] - b[1]), reverse=True)
    cx, x0, x1, y0, y1 = ok[0]
    print("[honor]   绿色候选 %d 个，选中 (%d,%d) %dx%d" % (
        len(ok), cx, (y0 + y1) // 2, x1 - x0, y1 - y0))
    return (cx, (y0 + y1) // 2)


def cmd_login():
    """点「点击账号登录」→ **立刻**抓微信原生「允许」框并点它。

    ⚠️ 必须一气呵成：那个授权框是**微信原生**的（不在小程序 DOM 里），而且
      **会自动超时消失**（实测弹出来约半分钟不理会就没了）。实测它还**有延迟**：
      点完登录入口后不是立刻出现，所以这一轮要等得久一点（最多 ~24s）。

    ⚠️ 登录凭证只活 **3 天**（`euid`/`encryptRtNew` 的 expires = 登录时刻 + 3 天，
      不滑动），所以这个命令**不是「一辈子一次」** —— 引擎在凭证过期时会自动再跑它
      （见 wxsign.py 的 `do_sign_honor`）。
    ⚠️ 它**会先自己切到「我的」页**：登录入口只在那页上，引擎带着过期 cookie 进来时
      小程序往往停在首页。
    """
    ws = _ws()
    ctx = pick_ctx(ws, ("[class*=u-login]", "[class*=login]"))
    d = wxdom.rect_of(ws, ctx, LOGIN_WORDS) if ctx else None
    if not d or not d["items"]:
        # 不在「我的」页（或该页还没渲染出登录入口）→ 切过去再找一次
        print("[honor] 没看到登录入口 → 先切到「我的」页再找")
        if cmd_personal() != 0:
            return 1
        time.sleep(4.0)
        ws = _ws()
        ctx = pick_ctx(ws, ("[class*=u-login]", "[class*=login]"))
        d = wxdom.rect_of(ws, ctx, LOGIN_WORDS) if ctx else None
    if not d or not d["items"]:
        print("[honor] 还是没有「%s」这类登录入口 → 可能已经登录" % "/".join(LOGIN_WORDS))
        return 1
    btn = d["items"][0]                     # rect_of 已按面积升序：最小的是按钮本身
    ox, oy, dpr, note = origin(ws, ctx)
    print("[honor] 登录入口「%s」页面坐标 (%d,%d) %dx%d  ← %s" % (
        btn["text"][:14], btn["cx"], btn["cy"], btn["w"], btn["h"], note))
    if DRY:
        print("[honor] --dry：不点击")
        return 0
    sx, sy = click_page(ox, oy, dpr, btn["cx"], btn["cy"])
    print("[honor] 已点登录入口 → 屏幕 (%d,%d)，等「允许」框…" % (sx, sy))

    for i in range(12):                     # 12 × 2s = 24s，覆盖它的延迟
        time.sleep(2.0)
        r = find_allow_btn_fullscreen()
        if r:
            print("[honor] 第 %d 轮抓到「允许」（屏幕坐标 %d,%d）" % (i + 1, r[0], r[1]))
            subprocess.run(["xdotool", "mousemove", "--sync", str(r[0]), str(r[1])])
            time.sleep(0.45)
            subprocess.run(["xdotool", "click", "1"])
            print("[honor] 已点「允许」")
            return 0
    print("[honor] 没等到微信「允许」框（没弹出来 / 已超时消失）")
    return 1


def cmd_personal():
    """跳「我的」：逻辑层 `wx.switchTab`（用 appId 认逻辑层，防止点错小程序）。"""
    ws = _ws()
    probe = ("(typeof wx!=='undefined'&&typeof wx.switchTab==='function')"
             "?((function(){try{return wx.getAccountInfoSync().miniProgram.appId}"
             "catch(e){return 'ERR'}})()):'no'")
    for ctx, v in _broadcast_pick(ws, probe, timeout=15.0):
        if isinstance(v, str) and v.startswith("wx"):
            if v != APPID:
                print("[honor] ⚠️ appId=%s ≠ 荣耀商城 %s" % (v, APPID))
            expr = "wx.switchTab({url:'%s'})||'ok'" % TAB_PERSONAL
            ws.send({"id": 8000, "method": "Runtime.evaluate",
                     "params": {"expression": expr, "contextId": ctx, "returnByValue": True}})
            print("[honor] 逻辑层 ctx=%d → %s" % (ctx, expr))
            return 0
    print("[honor] 找不到逻辑层 ctx")
    return 1


def cmd_tap():
    """按**文案**点页面元素（现取坐标，不用缓存）：`wxhonor.py tap 邀请有礼 [更多文案…]`

    ⚠️ 为什么要有它：`scan()` 报的坐标是**当时**的；页面一滚动就全失效
      （实测按旧坐标点「邀请有礼」会点到「我的积分」上）。所以点击必须
      「**当场取坐标 → 立刻点**」，中间不能隔别的命令。
    """
    words = tuple(a for a in sys.argv[2:] if not a.startswith("--"))
    if not words:
        print("[honor] 用法：tap <文案> [更多文案…]")
        return 2
    ws = _ws()
    hits = wxdom.find_ctx_with(ws, list(CONFIRM_SELECTORS))
    ctx = max(hits, key=lambda k: hits[k]) if hits else 0
    if not ctx:
        ctx = wxdom.find_ctx_by_text(ws, list(words)) or wxdom.find_dom_ctx(ws, 80)
    d = wxdom.rect_of(ws, ctx, words)
    if not d or not d["items"]:
        print("[honor] ctx=%d 里没有「%s」" % (ctx, "/".join(words)))
        return 1
    it = d["items"][0]
    ox, oy, dpr, note = origin(ws, ctx)
    print("[honor] 命中「%s」：%s %s 页面坐标 (%d,%d) %dx%d  ← %s" % (
        it["text"][:14], it["tag"], it["cls"][:26], it["cx"], it["cy"], it["w"], it["h"], note))
    if DRY:
        print("[honor] --dry：不点击")
        return 0
    sx, sy = click_page(ox, oy, dpr, it["cx"], it["cy"])
    print("[honor] 已点 → 屏幕 (%d,%d)" % (sx, sy))
    return 0


# ══════════════════════════════════════════════════════════════════════════
#  取身份：荣耀的会话在 **cookie** 里（不在小程序 storage）
# ══════════════════════════════════════════════════════════════════════════
# ⚠️ 关键事实：**不用打开任何 H5 就能读到整份 cookie**。
#    `Network.getAllCookies` 是 **browser 级**命令 —— attach 到**任意** target
#    （哪怕只是小程序渲染层的 `page-frame.html`）都能拿到全部 cookie jar。
#    所以「取身份」这一步**完全不驱动界面**，只要小程序开着。
#
# 为什么是 cookie：
#   · `.honor.com` 的 `euid`（用户 id，httpOnly）+ `encryptRtNew`（加密 refresh token）
#     + `CSRF-TOKEN` 就是全部凭证 —— 签到请求只要带它们就通（2026-09-28 实测）。
#   · `window.csrftoken` 与 cookie 里的 `CSRF-TOKEN` **是同一个值**
#     （`csrftoken.js` 只是个按域名返回硬编码串的函数），所以不必去读页面变量。
#   · `variedData`（设备指纹，2081 字节）也在 cookie 里。
#   ⚠️ 磁盘上 `radium/web/profiles/webview_<hash>/Cookies` 里确实有这些 cookie，
#     但值是 Chromium 的 `v10` AES-GCM 密文（解它要 keyring key）—— 不值得。
#     走 CDP 读出来就是明文，一步到位。
#
# `python3 wxhonor.py ident` → 打印一行 `HONOR_IDENT={...}`，引擎解析后用纯 HTTP 签到。

HONOR_DOMAIN = "honor.com"
# 登录凭证。实测 `expires` = **登录时刻 + 3 天**，且**不随请求滑动**
# （2026-09-28：登录后半小时内发了十几次业务请求，expires 一直钉在登录时间 + 3 天）。
# 所以引擎靠 `expires_in` 判断「要不要重新登录」—— 别等接口报错才发现。
CRIT_COOKIES = ("euid", "encryptRtNew")
_ID = [9000]


def _cdp_send(ws, method, params=None, sid=None):
    _ID[0] += 1
    ws.send({"id": _ID[0], "method": method, "params": params or {}}, sessionId=sid)
    return _ID[0]


def _cdp_wait(ws, want, timeout=12.0, sid=None):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            o = json.loads(ws.recv_msg())
        except Exception:
            return None
        if sid and o.get("sessionId") not in (None, sid):
            continue
        if o.get("id") == want:
            return o
    return None


def pick_miniapp_target(ws):
    """挑一个**小程序的** page target。先按 appId 匹配，再退到任意 servicewechat 页。

    ⚠️ 刻意**不要求**是承载签到的那个 webview：cookie 读取是 browser 级的，在哪都行，
       所以「取身份」不必先把任务中心 H5 打开（那是上一版实现绕的远路）。
    """
    wid = _cdp_send(ws, "Target.getTargets")
    infos = (((_cdp_wait(ws, wid) or {}).get("result")) or {}).get("targetInfos") or []
    for t in infos:
        if t.get("type") == "page" and ("servicewechat.com/%s" % APPID) in (t.get("url") or ""):
            return t
    for t in infos:
        if t.get("type") == "page" and "servicewechat.com" in (t.get("url") or ""):
            return t
    return None


def read_jar():
    """读 `.honor.com` 的 cookie jar。

    返回 `{"ok":True, "target":…, "jar":{name:value}, "exps":{name:expires}}`；
    失败返回 `{"ok":False, "err":…}`。
    """
    out = {"ok": False}
    ws = wxcdp.WS(timeout=25)
    _cdp_send(ws, "Target.setDiscoverTargets", {"discover": True})
    time.sleep(1.0)
    t = pick_miniapp_target(ws)
    if not t:
        out["err"] = "no-miniapp-target"
        return out
    wid = _cdp_send(ws, "Target.attachToTarget", {"targetId": t["targetId"], "flatten": True})
    sid = (((_cdp_wait(ws, wid) or {}).get("result")) or {}).get("sessionId")
    if not sid:
        out["err"] = "attach-failed"
        return out
    # 🔑 browser 级命令 —— 返回的是**整份** cookie jar（所有域名），与 attach 到谁无关
    wid = _cdp_send(ws, "Network.getAllCookies", {}, sid=sid)
    r = _cdp_wait(ws, wid, timeout=15.0, sid=sid)
    cookies = (((r or {}).get("result")) or {}).get("cookies") or []
    jar, exps = {}, {}
    for c in cookies:
        if not (c.get("domain") or "").endswith(HONOR_DOMAIN):
            continue
        n, v = c.get("name"), c.get("value")
        if v is None:
            continue
        if n in jar and len(c.get("path") or "") < jar[n][0]:
            continue                      # 同名多条时取 path 更具体的那条
        jar[n] = (len(c.get("path") or ""), v)
        exps[n] = c.get("expires")
    out.update({"ok": True, "target": (t.get("url") or "")[:120],
                "jar": {k: v for k, (_, v) in jar.items()}, "exps": exps})
    return out


def _summarize(jar, exps):
    """把 cookie jar 压成引擎要的那几个字段。"""
    crit = [e for k, e in (exps or {}).items()
            if k in CRIT_COOKIES and isinstance(e, (int, float)) and e > 0]
    return {
        "cookie": "; ".join("%s=%s" % (k, v) for k, v in jar.items()),
        "jar": jar,
        "csrf": jar.get("CSRF-TOKEN", ""),
        "has_login": bool(jar.get("euid")) and bool(jar.get("encryptRtNew")),
        # 登录凭证还剩多少秒（None = 拿不到过期时间）。引擎用它决定要不要先续期。
        "expires_in": (int(min(crit) - time.time()) if crit else None),
        "uid": jar.get("uid", ""),
        "user": jar.get("user", ""),
    }


def cmd_ident():
    """读 `.honor.com` 的 cookie jar → 打印 `HONOR_IDENT={...}`。

    输出：`{"ok":true, "appid":…, "target":…, "cookie":"k=v; k=v; …", "jar":{…},
            "csrf":…, "has_login":bool, "expires_in":秒|null, "uid":…, "user":…}`

    引擎把 `cookie` 直接当 `Cookie:` 头用 —— 签到全流程纯 HTTP（见 wxsign.py）。
    """
    r = read_jar()
    if not r.get("ok"):
        print("[honor] 读不到 cookie：%s（荣耀商城开着吗？hook 通吗？）" % r.get("err"))
        print("HONOR_IDENT=" + json.dumps({"ok": False, "err": r.get("err")}))
        return 1
    jar = r["jar"]
    out = dict(_summarize(jar, r["exps"]), ok=True, appid=APPID, target=r["target"])
    print("[honor] appId=%s  取自 %s" % (APPID, r["target"][:70]))
    print("[honor] cookie %d 条：euid=%s  encryptRtNew=%s  CSRF-TOKEN=%s  variedData=%s" % (
        len(jar),
        "有" if jar.get("euid") else "**无**",
        "有" if jar.get("encryptRtNew") else "**无**",
        "有" if jar.get("CSRF-TOKEN") else "**无**",
        ("%d 字节" % len(jar.get("variedData", ""))) if jar.get("variedData") else "**无**"))
    ei = out["expires_in"]
    ttl = ""
    if ei is not None:
        ttl = ("；凭证还剩 %.1f 天" % (ei / 86400.0)) if ei > 0 else "；**凭证已过期**"
    print("[honor] 登录态：%s%s%s" % (
        "已登录 ✅" if out["has_login"] else "**未登录**",
        "   账号 %s / uid %s" % (out["user"], out["uid"]) if out["user"] else "",
        ttl))
    print("HONOR_IDENT=" + json.dumps(out, ensure_ascii=False))
    return 0


def cmd_sso(open_wait=20.0):
    """让 `.honor.com` 的 cookie 借 **华为账号 SID** 自动补回来 —— **不需要任何弹窗**。

    ⭐ 原理（2026-09-28 实测）：荣耀这边有**两套会话**，别搞混 ——

        · **华为账号会话**：`id1.cloud.huawei.com` 的 `sid` / `hwid_cas_sid`（很持久。
          它才是「你是谁」的根，也是小程序「我的」页显示已登录的依据）
        · **业务会话**：`.honor.com` 的 `euid` / `encryptRtNew`（**只活 3 天**，签到用它）

    只要 SID 还在，**打开一次 honor.com 的页面**（任务中心 H5 一加载就会调 `queryUserInfo`）
    → 服务端发现「本人已登录、但业务会话没了」→ **自动下发一套新的 `.honor.com` cookie**。
    实测：把 `.honor.com` 的 6 个登录 cookie 全删掉后再打开一次，
    `euid` / `encryptRtNew` / `CSRF-TOKEN` **全部回来（且是新值）**。

    ✅ 所以这是**比 `login` 可靠得多**的自愈路径：不点按钮、不依赖微信原生弹窗、
       不碰 xdotool —— 只要一句逻辑层 `navigateTo` + 等它加载。
    ⚠️ 前提是 **SID 也还活着**。SID 都没了才需要 `login`（那一步要点「允许」框，见 cmd_login）。
    """
    ws = _ws()
    probe = ("(function(){try{return (typeof wx!=='undefined'&&typeof wx.navigateTo==='function')"
             "?(wx.getAccountInfoSync().miniProgram.appId+'|'+(getCurrentPages().slice(-1)[0]||{}).route)"
             ":'no'}catch(e){return 'ERR'}})()")
    hit = None
    for ctx, v in _broadcast_pick(ws, probe, timeout=15.0):
        if isinstance(v, str) and v.startswith("wx"):
            app, _, route = v.partition("|")
            if app != APPID:
                print("[honor] ⚠️ 当前小程序 appId=%s ≠ 荣耀商城 %s" % (app, APPID))
                return 1
            hit = (ctx, route)
            break
    if not hit:
        print("[honor] 找不到逻辑层 ctx（荣耀商城没开着？）")
        return 1
    ctx, route = hit
    print("[honor] 逻辑层 ctx=%d route=%s" % (ctx, route or "-"))
    if not (route or "").startswith("packageActivity/pages/login4Qxmp"):
        expr = "wx.navigateTo({url:'%s'})||'ok'" % TASK_PAGE
        ws.send({"id": 8600, "method": "Runtime.evaluate",
                 "params": {"expression": expr, "contextId": ctx, "returnByValue": True}})
        print("[honor] → navigateTo 任务中心（目的不是看页面，是让 honor.com 域发请求触发 SSO）")
    else:
        print("[honor] 已在任务中心页（H5 在 onShow 时会自己请求）")
    print("[honor] 等 %ds 让 H5 加载 + SSO 下发 cookie…" % int(open_wait))
    time.sleep(open_wait)

    r = read_jar()
    if not r.get("ok"):
        print("[honor] 读 cookie 失败：%s" % r.get("err"))
        return 1
    s = _summarize(r["jar"], r["exps"])
    print("[honor] 复查：euid=%s  encryptRtNew=%s  CSRF-TOKEN=%s" % (
        "有" if r["jar"].get("euid") else "**无**",
        "有" if r["jar"].get("encryptRtNew") else "**无**",
        "有" if r["jar"].get("CSRF-TOKEN") else "**无**"))
    if s["has_login"] and (s["expires_in"] is None or s["expires_in"] > 0):
        print("[honor] SSO 成功 ✅ 业务 cookie 已恢复（账号 %s，还剩 %.1f 天）" % (
            s["user"] or "?", (s["expires_in"] or 0) / 86400.0))
        print("HONOR_IDENT=" + json.dumps(dict(s, ok=True, appid=APPID), ensure_ascii=False))
        return 0
    print("[honor] SSO 没能恢复 cookie（SID 可能也没了 → 退回 wxhonor.py login）")
    return 1


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    fn = {"status": cmd_status, "origin": cmd_origin, "agree": cmd_agree,
          "login": cmd_login, "personal": cmd_personal, "tap": cmd_tap,
          "ident": cmd_ident, "sso": cmd_sso}.get(cmd)
    if not fn:
        print(__doc__)
        return 2
    return fn()


if __name__ == "__main__":
    sys.exit(main())
