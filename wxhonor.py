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

## 三、签到：**不在小程序里，在 H5 里**
荣耀的「签到领积分」是**任务中心 H5**（`www.honor.com/cn/msale/mp/jobcenter.html`），
由小程序页面 `login4Qxmp`（标题「任务页面」）用 web-view 承载。
H5 是**独立 CDP target（type=webview）**，主 page 的 ctx 列表里看不到它 ——
必须 `Target.attachToTarget(flatten=True)` 拿 sessionId 才能操作。

签到链路（全部从 H5 的 `sign_in_interactive.js` 里读出来的，非猜测）：

    # 幂等判据：服务端背书的「今天签过了」
    POST {openapiDomain}/tdcs/taskcenter/queryTaskCenterInfo
    body {"activityCode":…, "taskPortal":"4", "beCode":"CN"}
    → result.signInInfo.signInToday === true

    # 签到本体
    POST {openapiDomain}/tdcs/taskcenter/taskCenterSignIn
    body {"activityCode", "taskPortal":"4", "agent":navigator.userAgent,
          "oas_refer":location.origin+"/", "variedData":<cookie variedData>}
    → code "0" 成功；"task.center.today.aready.signin"(numCode 3027) = 今日已签

`activityCode` **不在 URL 上**，是页面 HTML 里内联的组件配置：
    <div class="J_mod sign-in-style4 mod-838… mod-sign-in" data-activity-code="QDHDz5SM67T65XPOBMBBQ1">
所以取它的稳定判据 = **`.sign-in-style4[data-activity-code]`**（每期换档期会变，
但属性名不变，不许硬编码那个值）。

⚠️ 认证（`euid` / `encryptRtNew` / `CSRF-TOKEN` / `hasSigned`）在 **`.honor.com` 的
cookie** 里 —— 所以**在 H5 页面内发 fetch**（`credentials:'include'`）最省事，
认证完全交给浏览器自己，我们一个 cookie 都不碰。（另有一个纯 HTTP 变体：
把 cookie 导出来自己带，但没必要。）

## 四、用法（容器内跑）
    python3 wxhonor.py status     # 当前在哪一页 / 隐私弹窗在不在 / 登录态
    python3 wxhonor.py origin     # 打印页面原点与它的推导过程
    python3 wxhonor.py agree      # 点掉隐私声明的「同意」（加 --dry 只看不点）
    python3 wxhonor.py login      # 点「点击账号登录」，触发微信手机号授权
    python3 wxhonor.py personal   # 逻辑层 wx.switchTab 跳到「我的」页
    python3 wxhonor.py tap <文案>  # 按文案点（现取坐标）
    python3 wxhonor.py h5         # 打开（或复用）任务中心 H5，打印它的 target
    python3 wxhonor.py info       # 读 H5：activityCode / 已签天数 / 档期 / 积分
    python3 wxhonor.py signin     # 签到（幂等：已签直接报 415，不重复发请求）
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
    """
    ws = _ws()
    ctx = pick_ctx(ws, ("[class*=u-login]", "[class*=login]"))
    if not ctx:
        print("[honor] 找不到登录入口所在的渲染面")
        return 1
    d = wxdom.rect_of(ws, ctx, LOGIN_WORDS)
    if not d or not d["items"]:
        print("[honor] 没有「%s」这类登录入口 → 可能已经登录" % "/".join(LOGIN_WORDS))
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
#  任务中心 H5（签到在这里）
# ══════════════════════════════════════════════════════════════════════════
_H5_ID = [9000]


def _h5_send(ws, method, params=None, sid=None):
    _H5_ID[0] += 1
    ws.send({"id": _H5_ID[0], "method": method, "params": params or {}}, sessionId=sid)
    return _H5_ID[0]


def _h5_wait(ws, want, timeout=12.0, sid=None):
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


def find_h5(ws):
    """任务中心 H5（webview target）；没有返回 None。
    ⚠️ 它在主 page 的 ctx 列表里**看不见** —— web-view 是独立 target。"""
    wid = _h5_send(ws, "Target.getTargets")
    infos = (((_h5_wait(ws, wid) or {}).get("result")) or {}).get("targetInfos") or []
    for t in infos:
        if t.get("type") == "webview" and H5_KEY in (t.get("url") or ""):
            return t
    return None


def nav_task_page(ws, wait_after=9.0):
    """逻辑层 `wx.navigateTo` 跳到任务中心页（它内嵌签到 H5）。已在则跳过。"""
    probe = ("(function(){try{return (typeof wx!=='undefined'&&typeof wx.navigateTo==='function')"
             "?(wx.getAccountInfoSync().miniProgram.appId+'|'+(getCurrentPages().slice(-1)[0]||{}).route)"
             ":'no'}catch(e){return 'ERR'}})()")
    for ctx, v in _broadcast_pick(ws, probe, timeout=15.0):
        if not (isinstance(v, str) and v.startswith("wx")):
            continue
        app, _, route = v.partition("|")
        if app != APPID:
            print("[honor] ⚠️ 当前小程序 appId=%s ≠ 荣耀商城 %s" % (app, APPID))
            return False
        if route and route.startswith("packageActivity/pages/login4Qxmp"):
            print("[honor] 已在任务中心页（%s），不重复跳转" % route)
            return True
        expr = "wx.navigateTo({url:'%s'})||'ok'" % TASK_PAGE
        ws.send({"id": 8500, "method": "Runtime.evaluate",
                 "params": {"expression": expr, "contextId": ctx, "returnByValue": True}})
        print("[honor] 逻辑层 ctx=%d route=%s → navigateTo %s" % (ctx, route or "-", TASK_PAGE))
        time.sleep(wait_after)
        return True
    print("[honor] 找不到逻辑层 ctx（荣耀商城没开着？）")
    return False


def h5_session(open_if_missing=True, wait=36.0):
    """连上任务中心 H5，返回 (ws, sid, target)；失败 (ws, None, None)。

    先复用已开着的 H5；没有就逻辑层导航开它（H5 加载要几秒，所以轮询等）。
    """
    ws = wxcdp.WS(timeout=25)
    _h5_send(ws, "Target.setDiscoverTargets", {"discover": True})
    time.sleep(1.0)
    t = find_h5(ws)
    if not t and open_if_missing:
        if nav_task_page(ws):
            t0 = time.time()
            while time.time() - t0 < wait:
                time.sleep(2.0)
                t = find_h5(ws)
                if t:
                    break
    if not t:
        return ws, None, None
    wid = _h5_send(ws, "Target.attachToTarget", {"targetId": t["targetId"], "flatten": True})
    sid = (((_h5_wait(ws, wid, timeout=12.0) or {}).get("result")) or {}).get("sessionId")
    if not sid:
        return ws, None, t
    _h5_send(ws, "Runtime.enable", {}, sid=sid)
    _h5_send(ws, "Page.enable", {}, sid=sid)
    time.sleep(0.5)
    return ws, sid, t


def h5_eval(ws, sid, expr, timeout=40.0):
    """在 H5 里求值（Promise 会自动 await），返回值 JSON 化。"""
    wid = _h5_send(ws, "Runtime.evaluate",
                   {"expression": expr, "returnByValue": True, "awaitPromise": True}, sid=sid)
    r = _h5_wait(ws, wid, timeout=timeout, sid=sid)
    if not r:
        return None
    res = r.get("result") or {}
    if res.get("exceptionDetails"):
        return {"__exc__": str(res["exceptionDetails"])[:400]}
    v = (res.get("result") or {}).get("value")
    if isinstance(v, str):
        try:
            return json.loads(v)
        except Exception:
            return v
    return v


# 在 H5 页面内发请求 —— 认证（cookie）由浏览器自己带，我们一个 cookie 都不碰。
# __WANT__ 换成 true/false：true = 未签就签；false = 只查状态。
_H5_JS = r"""
(function(){
  var el = document.querySelector('.sign-in-style4');
  var ac = el ? String(el.getAttribute('data-activity-code') || '') : '';
  var cfg = window.pageConfig || {};
  var base = cfg.openapiDomain || 'https://openapi-cn.c.honor.com';
  var vd = null;
  try {
    var m = String(document.cookie).match(/variedData=([^;]+)/);
    vd = m ? decodeURIComponent(m[1]) : null;
  } catch (e) {}
  var H = {'Content-Type': 'application/json', 'CsrfToken': (window.csrftoken || '')};
  var WANT = __WANT__;
  var out = {activityCode: ac, base: base, hasVariedData: !!vd,
             csrf: String(window.csrftoken || '').slice(0, 8) + '…'};
  if (!ac) { out.err = 'no-activity-code（H5 里没有 .sign-in-style4）'; return JSON.stringify(out); }
  return fetch(base + '/tdcs/taskcenter/queryTaskCenterInfo', {
      method: 'POST', headers: H, credentials: 'include',
      body: JSON.stringify({activityCode: ac, taskPortal: '4', beCode: 'CN'})
    })
    .then(function(r){ return r.json(); })
    .then(function(j){
      var R = j.result || {}, si = R.signInInfo || {};
      out.queryCode = j.code;
      out.signInToday = si.signInToday;
      out.continuousSignIn = si.continuousSignIn;
      out.signInCycle = si.signInCycle;
      out.accumulatePoints = R.accumulatePoints;
      out.endTime = R.endTime;
      var td = (si.cycleSignInInfoList || []).filter(function(x){ return x.today; })[0];
      out.todayEarnPoint = td ? td.earnPoint : null;
      out.alreadySigned = (si.signInToday === true);
      if (out.alreadySigned || !WANT) { return JSON.stringify(out); }
      return fetch(base + '/tdcs/taskcenter/taskCenterSignIn', {
          method: 'POST', headers: H, credentials: 'include',
          body: JSON.stringify({activityCode: ac, taskPortal: '4',
            agent: navigator.userAgent, oas_refer: location.origin + '/', variedData: vd})
        })
        .then(function(r){ return r.json(); })
        .then(function(s){
          out.signCode = s.code; out.signNumCode = s.numCode; out.signMsg = s.msg;
          out.signSuccess = s.success; out.signResult = s.result;
          return JSON.stringify(out);
        });
    });
})()
"""


def h5_signin(want=True, open_if_missing=True):
    """跑一轮「查状态 →（未签则）签到」。返回结果 dict（失败 None）。"""
    ws, sid, t = h5_session(open_if_missing=open_if_missing)
    if not sid:
        print("[honor] 连不上任务中心 H5（页面没打开 / hook 没挂？）")
        return None
    print("[honor] H5 = %s" % (t.get("url") or "")[:110])
    js = _H5_JS.replace("__WANT__", "true" if want else "false")
    return h5_eval(ws, sid, js)


def _report(r):
    # `--json`：给引擎（wxsign.py 的 do_sign_honor）解析用。
    # 约定与 wxoppo.py 的 `OPPO_JSON=` 一致：**一行、机器可读**。
    if JSON_OUT:
        print("HONOR_JSON=" + json.dumps(
            r if isinstance(r, dict) else {"raw": str(r)}, ensure_ascii=False))
    if not isinstance(r, dict):
        print("[honor] 结果异常：%s" % str(r)[:300])
        return 1
    if r.get("__exc__"):
        print("[honor] 页面异常：%s" % r["__exc__"])
        return 1
    print("[honor] activityCode = %s" % r.get("activityCode"))
    print("[honor] 档期结束     = %s    已连续签到 = %s/%s 天"
          % (r.get("endTime"), r.get("continuousSignIn"), r.get("signInCycle")))
    print("[honor] 累计积分     = %s    今日可得 = %s    今天已签 = %s"
          % (r.get("accumulatePoints"), r.get("todayEarnPoint"), r.get("signInToday")))
    if r.get("alreadySigned"):
        print("[honor] → 今日已签到（幂等，不发签到请求）")
        return 0
    if "signCode" in r:
        print("[honor] 签到返回     = code=%s numCode=%s success=%s msg=%s"
              % (r.get("signCode"), r.get("signNumCode"), r.get("signSuccess"), r.get("signMsg")))
        if r.get("signCode") == "0":
            print("[honor] → 签到成功 ✅  %s" % (r.get("signResult"),))
            return 0
        if "aready.signin" in str(r.get("signCode")):
            print("[honor] → 今日已签到（服务端判定）")
            return 0
        return 1
    return 1


def cmd_h5():
    ws, sid, t = h5_session()
    if not sid:
        print("[honor] 任务中心 H5 没能就绪")
        return 1
    print("[honor] H5 target = %s" % t["targetId"])
    print("[honor] H5 url    = %s" % (t.get("url") or ""))
    print("[honor] sessionId = %s" % sid)
    return 0


def cmd_info():
    return _report(h5_signin(want=False))


def cmd_signin():
    return _report(h5_signin(want=True))


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    fn = {"status": cmd_status, "origin": cmd_origin, "agree": cmd_agree,
          "login": cmd_login, "personal": cmd_personal, "tap": cmd_tap,
          "h5": cmd_h5, "info": cmd_info, "signin": cmd_signin}.get(cmd)
    if not fn:
        print(__doc__)
        return 2
    return fn()


if __name__ == "__main__":
    sys.exit(main())
