#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""驱动微信 UI 完成「首次注册会员」这一步（容器内运行）。

为什么需要它：注册要走微信原生 UI，抓不到接口 ——
    点签到/参与入口 →（可能）隐私协议弹窗 → 授权说明弹窗 → 微信手机号授权弹窗 →「允许」
    → 页面自己调 registerVip → 注册完通常顺手签到一次
**每个账号只走一次**（注册完就不再弹）。

⚠️ 纠正一个曾经的错误说法：**这一步不需要手机号**。
手机号是**从你微信账号里取的**（微信弹窗授权，脚本点「允许」即可，全自动）。
`WXSIGN_REGISTER_PHONE` 只在两种情况有用：
  · 走 API 直连注册时（`WX_REGISTER_MODE=api`，零 UI，需要明文手机号）；
  · 走 UI 且该账号绑了**多个**号码时，用它（配合 tesseract OCR）挑出正确那一行。
只有 1 个号码时什么都不用设。

识别思路（不依赖 OCR）：
  · 绿色按钮 = 微信主按钮（隐私协议的「同意并继续」、手机号弹窗的「允许」）
  · 橙色按钮 = 小程序自己的「授权」/「参与」按钮
  · 白色卡片 = 有弹窗在（页面背景通常不是通栏白，用宽度区间区分）
  · 手机号列表的行位置 → 用**已勾选号码旁那颗绿色 ✓** 当锚点，按行距往上数

⚠️ 通用性边界：**弹窗链**（微信 + 吾享标准弹窗）是通用的；**怎么走到那个入口**因品牌而异，
所以入口坐标可配（见 WXSIGN_REG_CLICK）。默认取「居中偏下」，与签到/参与按钮常见位置一致。

环境变量：
  WXSIGN_REG_CLICK     入口按钮位置，比例或像素：`0.50,0.64`（默认）或 `640,700`
  WXSIGN_REG_SCROLL    进入前向下滚动的次数（默认 6；设 0 表示不滚）
  WXSIGN_REGISTER_PHONE_INDEX  多号码时选第几个（从 1 开始）
  WXSIGN_REGISTER_PHONE        期望手机号（完整或后 4 位）；装了 tesseract 时逐行 OCR 匹配
  SHOT_DIR             截图目录（默认 /tmp/shots）

用法：python3 wxreg.py [--index N] [--phone X] [--dry-run] [--analyze a.png]
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wxwin                                     # noqa: E402

DISPLAY = os.environ.get("DISPLAY", ":1")
SHOT_DIR = os.environ.get("SHOT_DIR", "/tmp/shots")
ROW_STEP = 0.045          # 号码行行距（占屏高比例）
CHECK_GAP = 0.030         # ✓ 与「微信绑定号码」小字的间距，用于避开小字


def envv(*names, default=""):
    """按顺序取第一个非空环境变量（新旧名字都认）。"""
    for n in names:
        v = os.environ.get(n)
        if v:
            return v
    return default


# ───────────────────────────── 基础工具 ─────────────────────────────
def run(cmd):
    return subprocess.run(cmd, shell=True, capture_output=True, text=True).stdout


def display_size():
    """整块虚拟屏的尺寸（主窗口 / 小程序面板用）。"""
    out = run("DISPLAY=%s xdotool getdisplaygeometry" % DISPLAY).strip().split()
    return int(out[0]), int(out[1])


# ───────────────── 小程序窗口：页面坐标 → 屏幕坐标 ─────────────────
# ⚠️ 坐标不再等于屏幕坐标，必须加窗口原点。
#
# 云微镜像的 openbox 原本有一条通配规则把所有窗口无条件最大化
# （`<application class="*"><maximized>yes</maximized></application>`），
# 小程序被拉成 1280x1024 —— 那时「页面坐标 == 屏幕坐标」纯属巧合。
# 改成只最大化「微信」（主窗口 + 面板）之后，小程序窗口恢复成手机竖版
# （实测 410x776 @ 435,124），位置也不再固定在 (0,0)。
#
# 换算是「平移 + 补视口外的 chrome」，**不是**纯平移 —— 参见 `refresh_page` 里的 ⚠️⚠️：
#   · **小程序的根页面**：页面自报 window.screenX/screenY = (435,124) 与窗口原点完全一致，
#     且 innerHeight(779) > outerHeight(776) —— 顶部那条「⌂ 首页 ●●● ─ ⊙」是
#     **覆盖层**，不占视口 → 补 0 ✓（当初就是只看了这一种形态，才写出「简单平移」的结论）。
#   · **半屏页**（如 OPPO 商城的「登录」页）：窗口被撑开、页面外多出一条标题栏，
#     `sy` **仍然是窗口原点**、innerHeight 却比 outerHeight 小 61 → 必须补 61，
#     否则所有点击整体偏上 61px（实测勾选框/登录按钮全点空）。
#   两种形态统一由 `dy = max(0, outerHeight - innerHeight)` 覆盖（DPR=1）。
_PAGE = {"x": 0, "y": 0, "w": 0, "h": 0, "dpr": 1.0, "src": "none"}

_PAGE_JS = ("(function(){try{return JSON.stringify({sx:screenX,sy:screenY,"
            "w:innerWidth,h:innerHeight,oh:outerHeight,dpr:devicePixelRatio});}"
            "catch(e){return 'ERR:'+(e.message||e)}})()")


def refresh_page(ws=None, ctx=0):
    """刷新「页面视口在哪、多大」。拿到返回 True。

    ⭐ **优先问页面自己**（CDP 的 window.screenX/screenY/innerWidth/innerHeight）。
    这一条同时兼容小程序的**两种形态**：

      · **独立窗口**（现在的形态）：screenX/screenY 与 xdotool 报的窗口原点完全一致
        （实测 410x776 @ (435,124)）。
      · **侧边栏 / 分栏**（Windows 版微信那样，小程序嵌在主窗口里、不居中）：
        按「窗口标题不是微信」去找独立小程序窗口会**直接失败**，但页面自己照样
        报得出自己的位置与大小 —— 所以这条是向前兼容的关键。

    CDP 拿不到时回退到「找独立小程序窗口」。两条都拿不到才算不知道页面在哪。
    """
    if ws and ctx:
        try:
            import wxdom
            raw = wxdom.evaluate(ws, _PAGE_JS, ctx=ctx, timeout=3.0)
            if raw and not str(raw).startswith("ERR"):
                g = json.loads(raw)
                if g.get("w") and g.get("h"):
                    # ⚠️⚠️ 2026-09-27 治本：**页面自报的 `sy` 只是「窗口原点」，不含页面外的 chrome**。
                    #    实测 OPPO 商城的**半屏登录页**：窗口 410x776 @ (435,124)，
                    #    页面自报 sx=435 sy=124、innerHeight=715、outerHeight=776 ——
                    #    中间那 61px 是微信给半屏页加的**「登录」标题栏**，它不在视口里，
                    #    而 `sy` 不会把它算进去。于是按 `(sy + 页面y)` 点击会**整体偏上 61px**：
                    #    实测勾选框（页面 y=630，真实屏幕 y=815）点空、登录按钮
                    #    （页面 y=345，真实 y=531）也点空，还误点到了 OPPO logo ——
                    #    三个现象都对得上「少加了 61」。
                    #    判据直接用页面自己报的两个数：`dy = outerHeight - innerHeight`。
                    #    · 半屏登录页：776-715 = **61** ✓
                    #    · 主页面（OPPO 首页/我的页）：776-779 = **-3** → 取 0 ✓
                    #      —— 正好对应老注释里那条观察「innerHeight(779) > outerHeight(776)，
                    #        顶部那条 ⌂ ●●● － ⊙ 是**覆盖层**、不占视口」。
                    dy = int(g.get("oh") or 0) - int(g["h"])
                    if dy < 0 or dy > 200:      # 不合理就当没有：宁可不加，也别乱加
                        dy = 0
                    if dy:
                        print("[wxreg] 页面视口比窗口矮 %dpx（半屏页的标题栏）→ "
                              "页面原点 y 由 %d 修正为 %d"
                              % (dy, int(g["sy"]), int(g["sy"]) + dy))
                    _PAGE.update(x=int(g["sx"]), y=int(g["sy"]) + dy,
                                 w=int(g["w"]), h=int(g["h"]),
                                 dpr=float(g.get("dpr") or 1), src="cdp")
                    return True
        except Exception:
            pass
    r = wxwin.rect()
    if r:
        _PAGE.update(x=r[0], y=r[1], w=r[2], h=r[3], dpr=1.0, src="window")
        return True
    _PAGE.update(src="none")
    return False


def page_rect():
    """页面视口矩形 (x,y,w,h) —— 屏幕坐标 + 页面逻辑尺寸。"""
    p = _PAGE
    return (p["x"], p["y"], p["w"], p["h"])


def win_rect():
    """（兼容旧名）刷新后再返回页面矩形；拿不到返回 None。"""
    return page_rect() if refresh_page() else None


def origin():
    """页面 (0,0) 对应的屏幕坐标。"""
    return (_PAGE["x"], _PAGE["y"])


def win_size():
    """页面逻辑尺寸（CSS 像素，与 DOM 坐标同一空间）；拿不到时退化为整屏。

    DPR=1 时它就等于帧的像素尺寸（实测现在就是 1）。
    """
    p = _PAGE
    return (p["w"], p["h"]) if p["w"] and p["h"] else display_size()


def grab(W=0, H=0, win=True):
    """抓一帧。win=True 抓**页面视口**区域（页面坐标空间），False 抓整屏。

    ⚠️ 用 `_PAGE`（`refresh_page()` 的结果），**不要**在这里重新探测窗口 ——
    那样会把 CDP 报出来的侧边栏布局覆盖掉。所以调用前先 `refresh_page()`。
    """
    if win:
        p = _PAGE
        if not p["w"] or p["src"] == "none":
            raise RuntimeError("不知道页面在哪：没有小程序窗口，也没读到 CDP")
        ox, oy, w, h = p["x"], p["y"], p["w"], p["h"]
        if not W or not H:
            W, H = w, h
        src = "%s+%d,%d" % (DISPLAY, ox, oy)
    else:
        if not W or not H:
            W, H = display_size()
        src = DISPLAY
    cmd = ("DISPLAY=%s ffmpeg -loglevel error -f x11grab -video_size %dx%d -i %s "
           "-frames:v 1 -f rawvideo -pix_fmt rgb24 -" % (DISPLAY, W, H, src))
    d = subprocess.run(cmd, shell=True, capture_output=True).stdout
    if len(d) < W * H * 3:
        raise RuntimeError("截屏失败（%d 字节，期望 %d）" % (len(d), W * H * 3))
    return d


def grab_file(path):
    wh = run("ffprobe -v error -select_streams v -show_entries stream=width,height "
             "-of csv=p=0 %s" % path).strip().split(",")
    W, H = int(wh[0]), int(wh[1])
    d = subprocess.run("ffmpeg -loglevel error -i %s -f rawvideo -pix_fmt rgb24 -" % path,
                       shell=True, capture_output=True).stdout
    if len(d) < W * H * 3:
        raise RuntimeError("读图失败 %s" % path)
    return d, W, H


def grab_png(W=0, H=0, name="shot.png", win=True):
    """存一张 PNG。win=True 抓页面视口区域（页面坐标空间）。"""
    os.makedirs(SHOT_DIR, exist_ok=True)
    p = os.path.join(SHOT_DIR, name)
    if win:
        pg = _PAGE
        if pg["w"] and pg["src"] != "none":
            ox, oy, w, h = pg["x"], pg["y"], pg["w"], pg["h"]
            src = "%s+%d,%d" % (DISPLAY, ox, oy)
            if not W or not H:
                W, H = w, h
        else:
            src = DISPLAY
            if not W or not H:
                W, H = display_size()
    else:
        src = DISPLAY
        if not W or not H:
            W, H = display_size()
    run("DISPLAY=%s ffmpeg -loglevel error -y -f x11grab -video_size %dx%d -i %s -frames:v 1 %s"
        % (DISPLAY, W, H, src, p))
    return p


def is_green(c):
    """微信绿：主绿 (7,193,96)；隐私协议那颗偏青 (102,196,170)，一并认"""
    return c[1] >= 140 and c[1] - c[0] >= 40 and c[1] - c[2] >= 15


def is_orange(c):
    return c[0] >= 200 and 140 <= c[1] <= 235 and c[2] <= 150 and c[0] - c[2] >= 90


def is_white(c):
    return min(c) > 228


def blob(buf, W, H, pred, x0, x1, y0, y1, step=2, min_hits=3):
    """在区域内找某类颜色像素的 bbox：先按 y 直方图定位主带，再向上下扩张（容许小间隙）"""
    hits_y = {}
    for y in range(y0, y1, step):
        base = y * W * 3
        n = 0
        for x in range(x0, x1, step):
            i = base + x * 3
            if pred((buf[i], buf[i + 1], buf[i + 2])):
                n += 1
        if n >= min_hits:
            hits_y[y] = n
    if not hits_y:
        return None
    peak = max(hits_y, key=hits_y.get)
    ys = sorted(hits_y)
    # 以 peak 为中心向两侧扩张，间隙 > 6 就断
    run = [peak]
    prev = peak
    for y in [v for v in ys if v < peak][::-1]:
        if prev - y <= 6:
            run.append(y)
            prev = y
        else:
            break
    prev = peak
    for y in [v for v in ys if v > peak]:
        if y - prev <= 6:
            run.append(y)
            prev = y
        else:
            break
    y0b, y1b = min(run), max(run)
    xs = []
    for y in range(y0b, y1b + 1, step):
        base = y * W * 3
        for x in range(x0, x1, step):
            i = base + x * 3
            if pred((buf[i], buf[i + 1], buf[i + 2])):
                xs.append(x)
    if not xs:
        return None
    return min(xs), y0b, max(xs), y1b


def white_card(buf, W, H):
    """找居中白色弹窗卡片。
    关键：页面通栏白（有的话）会接近满宽，弹窗白卡只占屏宽的 3~9 成 —— 用宽度区间排除通栏白。"""
    lo, hi = int(W * 0.30), int(W * 0.95)      # 像素宽度区间
    q = {}
    for y in range(int(H * 0.03), int(H * 0.98), 3):
        base = y * W * 3
        n = 0
        for x in range(int(W * 0.02), int(W * 0.98), 4):
            i = base + x * 3
            if is_white((buf[i], buf[i + 1], buf[i + 2])):
                n += 1
        n *= 4                                  # 采样数 → 像素宽度
        if lo <= n <= hi:
            q[y] = n
    if not q:
        return None
    ys = sorted(q)
    best = cur = [ys[0]]
    for y in ys[1:]:
        if y - cur[-1] <= 9:
            cur.append(y)
        else:
            if len(cur) > len(best):
                best = cur
            cur = [y]
    if len(cur) > len(best):
        best = cur
    if len(best) * 3 < H * 0.15:      # 太薄不算弹窗
        # ⚠️ 注意 `best` 是**行数**（y 步长 3），而阈值是按**像素**给的 ——
        # 不换算回去的话，实际要求的是 0.45H 像素，比原意严三倍。
        # 实测：农耕记的手机号弹窗白卡高 312px（占屏 0.40），全屏时代勉强能过，
        # 换成手机竖版（779 高）就被判成「太薄」整张丢弃 → 后面（✓ 锚点、号码行）全跟着错。
        return None
    y0, y1 = best[0], best[-1]
    xs = []
    for y in range(y0, y1 + 1, 4):
        base = y * W * 3
        for x in range(int(W * 0.02), int(W * 0.98), 4):
            i = base + x * 3
            if is_white((buf[i], buf[i + 1], buf[i + 2])):
                xs.append(x)
    if not xs:
        return None
    return min(xs), y0, max(xs), y1


def text_bands(buf, W, H, y0, y1, x0, x1):
    """区域内「文字行」带（暗像素成带），返回 [(ytop, ybot, x中心)]"""
    bands, cur = [], None
    for y in range(y0, y1):
        base = y * W * 3
        n = 0
        for x in range(x0, x1, 2):
            i = base + x * 3
            if buf[i] + buf[i + 1] + buf[i + 2] < 430:
                n += 1
        if n >= 2:
            cur = [y, y] if cur is None else [cur[0], y]
        else:
            if cur and cur[1] - cur[0] >= 6:
                bands.append(cur)
            cur = None
    if cur and cur[1] - cur[0] >= 6:
        bands.append(cur)
    out = []
    for a, b in bands:
        xs = []
        for y in range(a, b + 1):
            base = y * W * 3
            for x in range(x0, x1, 2):
                i = base + x * 3
                if buf[i] + buf[i + 1] + buf[i + 2] < 430:
                    xs.append(x)
        if xs:
            out.append((a, b, (min(xs) + max(xs)) // 2))
    return out


def raise_miniapp():
    """把小程序窗口激活并置顶，**并把挡住它的微信主窗口挪走**（**每轮都做**）。

    ⚠️ 为什么必须做：小程序是从**全屏的小程序面板**里点开的，面板仍留在最前，于是
      (a) 抓屏抓到的是**面板** —— 像素判据会把面板里的搜索卡片当成「手机号弹窗」；
      (b) 所有点击都被面板吃掉。
    实测绿茵阁西餐厅的注册就死在这：`reg_phone_popup.png` 里是**面板的搜索卡片列表**、
    根本不是小程序页面 → detect() 误判成手机号弹窗 → rc=3 放弃。
    （同一课在 wxagree.py 里已经吃过一次，那边补了这一步，wxreg.py 这里漏了。）

    ⚠️⚠️ 2026-09-26 实测补丁：**`windowraise` 对微信主窗口无效**。
    微信主窗口是个 **1276x1024 全屏顶层窗口**（`xwininfo` 里它叫 `"微信"`，class
    `wechat`），小程序窗口（410x776）反而在它**下面**。无 WM 干预时 `XRaiseWindow`
    会被微信自己立刻压回来 —— 实测 `windowraise` 前后 `xwininfo -root -children`
    的子窗口顺序**一模一样**，而 `xdotool getmouselocation` 在**屏幕任意位置**都返回
    同一个窗口 id（那个全屏主窗口）。
    症状极具迷惑性：**点击全部无效 / 全落到别的窗口上，但截图和 DOM 都完全正常**。
    有效解法 = **`windowmove` 把它挪出屏幕**（`windowmove` 实测生效，`windowraise` 不生效）。
    挪到屏幕外而不是最小化：最小化会触发微信的重绘/重新置顶，挪走最稳。
    """
    try:
        m = wxwin.miniapp()
    except Exception:
        return False
    if not m:
        return False
    wid = m["id"]
    run("DISPLAY=%s xdotool windowactivate --sync %s" % (DISPLAY, wid))
    run("DISPLAY=%s xdotool windowraise %s" % (DISPLAY, wid))
    _move_wechat_away(m)
    # ⚠️ 必须**验证**结果：置顶失败的症状极具迷惑性 —— 截图和 DOM 都完全正常，
    #    但所有点击都落到别的窗口上（就是上面那段实测记录里的事）。不报出来的话，
    #    后面所有失败看起来都像「坐标算错」，会白查很久。
    try:
        import wxopen
        under = wxopen.win_under(m["x"] + m["w"] // 2, m["y"] + m["h"] // 2)
        if under and str(under) != str(wid):
            print("[wxreg] ⚠️ 小程序置顶失败：小程序中心最上面是 %s（小程序是 %s）→ "
                  "后续点击/截图可能落到它身上" % (under, wid))
    except Exception:
        pass
    return True


def _move_wechat_away(mini):
    """把**盖住小程序**的微信**主窗口**挪出屏幕。

    ⚠️⚠️ 2026-09-27 治本改造 —— 这个函数原本是个**地雷**，实测踩到（现场表现：
    微信主窗口在屏幕上「消失」，只剩右边一条 98px 的边，看着像微信被关掉了）：

      ① 原判据是「盖住小程序中心的窗口」—— 而**小程序面板**正好满足，
         于是面板也被一起挪走，面板的 ✕ 跑到屏幕外，之后**对面板的任何操作都落空**
         （点卡片、点 ✕ 全没反应）。
      ② 本环境的窗口管理器（openbox）会**夹住**窗口不让完全移出屏幕
         （EWMH 规定要留一部分可见）→ 主窗口最后停在 `X=1182`，
         而不是代码里写的 `sw+10 = 1290`。于是既没挪干净、又让主窗口"半在场"。
      ③ 挪完没有任何验证/复原，主窗口就**永久歪在右边**，用户再也看不到微信。

    改法（三条缺一不可）：
      ① **先确认真被遮挡**再动手 —— 小程序中心最上面已经是小程序就直接返回
         （实测本环境的小程序窗口本来就在主窗口之上，压根不需要挪）。
      ② **只挪主窗口** —— 用 `wxopen.is_main_window()`（WM_CLASS + 标题双判据）认人。
         面板/搜一搜/视频号**都没有 WM_CLASS**，天然被排除；不再做「谁盖住挪谁」。
      ③ **挪完必须验证** —— 没挪到屏幕外（被 WM 夹住）就**搬回原位**并报警，
         绝不留下「半在屏幕里」的中间态。
    """
    import wxopen                     # 局部导入：避免模块级循环依赖（wxopen 不 import wxreg）

    mcx, mcy = mini["x"] + mini["w"] // 2, mini["y"] + mini["h"] // 2

    # ① 小程序中心最上面已经是小程序 → 没人挡着，什么都不用做
    try:
        under = wxopen.win_under(mcx, mcy)
    except Exception:
        under = None
    if under and str(under) == str(mini["id"]):
        return False

    try:
        parts = run("DISPLAY=%s xdotool getdisplaygeometry" % DISPLAY).split()
        sw = int(parts[0])
    except Exception:
        sw = 1280

    moved, failed = [], []
    for w in wxwin.windows():
        if w["id"] == mini["id"]:
            continue
        # ② 只认微信**主窗口**（面板没有 WM_CLASS，会被这一步挡掉）
        try:
            if not wxopen.is_main_window(w["id"]):
                continue
        except Exception:
            continue
        # 还必须是**真的盖住**小程序中心的那个（空窗口/装饰窗口尺寸为 0，天然排除）
        if not (w["x"] <= mcx <= w["x"] + w["w"] and w["y"] <= mcy <= w["y"] + w["h"]):
            continue
        ox, oy = w["x"], w["y"]
        run("DISPLAY=%s xdotool windowmove %s %d 0" % (DISPLAY, w["id"], sw + 10))
        time.sleep(0.4)
        g = _win_geom(w["id"]) or {}
        if g.get("X", 0) >= sw:
            moved.append(w["id"])
        else:
            # ③ 被窗口管理器夹住了 → 搬回原位，绝不留下「半在屏幕里」的状态
            run("DISPLAY=%s xdotool windowmove %s %d %d" % (DISPLAY, w["id"], ox, oy))
            print("[wxreg] ⚠️ 主窗口 %s 挪不出屏幕（被 WM 夹在 X=%s，原本 (%d,%d)）"
                  "→ 已搬回原位。挪不动是环境限制，别再重试这条路。"
                  % (w["id"], g.get("X"), ox, oy))
            failed.append(str(w["id"]))
    if moved:
        print("[wxreg] 把盖住小程序的微信主窗口挪到了屏幕外：%s（用完记得归位）"
              % ", ".join(moved))
    return bool(moved)


def _win_geom(wid):
    """窗口几何 → dict(X,Y,WIDTH,HEIGHT)；失败返回 None。"""
    cmd = run("DISPLAY=%s xdotool getwindowgeometry --shell %s" % (DISPLAY, wid))
    d = {}
    for line in cmd.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            d[k.strip()] = v.strip()
    try:
        return {k: int(d[k]) for k in ("X", "Y", "WIDTH", "HEIGHT")}
    except (KeyError, ValueError):
        return None


def blocker_over_miniapp():
    """目标小程序**中心点**最上层那个窗口的几何；认不出返回 None。

    用来回答一个致命问题：**「我抓到的到底是不是目标小程序？」**
    小程序是从**全屏的小程序面板**里点开的，面板一旦留在最前，
    抓屏抓到的就是面板 —— 而面板的卡片里同样有「白卡 + 卡内通栏绿按钮」，
    会被 `detect()` 判成手机号弹窗，接着走「没识别到白卡就退出」的安全阀 → 整轮注册直接废掉。
    （实测绿茵阁西餐厅就是这么失败的：`reg_phone_popup.png` 里是面板的搜索卡片。）
    """
    m = wxwin.miniapp()
    if not m:
        return None
    run("DISPLAY=%s xdotool mousemove %d %d"
        % (DISPLAY, m["x"] + m["w"] // 2, m["y"] + m["h"] // 2))
    time.sleep(0.3)
    wid = ""
    for line in run("DISPLAY=%s xdotool getmouselocation --shell" % DISPLAY).splitlines():
        if line.startswith("WINDOW="):
            wid = line.split("=", 1)[1].strip()
    return _win_geom(wid) if wid else None


def panel_is_blocking(geom):
    """这块几何是不是「全屏的面板」？

    为什么必须区分（而不是简单判「小程序不在最前」）：真正的**微信手机号弹窗**也是
    盖在小程序上的一层 —— 但它是个**小窗**。全屏的才一定是面板。
    """
    if not geom:
        return False
    sw, sh = wxwin.screen()
    return geom["WIDTH"] >= sw * 0.9 and geom["HEIGHT"] >= sh * 0.9


def click(x, y, raw=False):
    """点击。x,y 是**页面坐标**（CSS 像素，与 DOM 坐标同一空间）→ 自动换算成屏幕坐标。
    raw=True 时按屏幕坐标点（给主窗口 / 面板用）。"""
    if not raw:
        p = _PAGE
        x = p["x"] + int(round(x * p["dpr"]))
        y = p["y"] + int(round(y * p["dpr"]))
    run("DISPLAY=%s xdotool mousemove %d %d; sleep 0.4; DISPLAY=%s xdotool click 1"
        % (DISPLAY, x, y, DISPLAY))


def scroll_down(times, W, H):
    """在页面中部滚轮（坐标同样是页面坐标，自动加窗口原点）。"""
    ox, oy = origin()
    run(("DISPLAY=%s xdotool mousemove %d %d; " % (DISPLAY, ox + W // 2, oy + H // 2))
        + "".join("DISPLAY=%s xdotool click 5; sleep 0.25; " % DISPLAY for _ in range(times)))


# ── 标题栏左上角的「回去」按钮 ──
# 它是**微信画的覆盖层**，不在小程序 DOM 里，所以只能按坐标点；
# 但**坐标要探测，不要写死** —— 只要「标题栏某端有个图标」这个结构还在就有效，
# 换图标（`<` / `⌂`）、换字号、换主题色都不影响。
def nav_point(buf=None, W=0, H=0):
    """标题栏左上角「返回 / 回首页」按钮的**页面坐标**。

    ① **探测**：在标题栏带里找**最靠左的紧凑字形块**（`wxwin.glyphs`）。
    ② 探测不到 → 退回实测比例（0.058W / 0.052H；410x776 下 = (23,40)）。
    """
    if buf and W and H:
        # 扫描带只取标题栏**垂直居中**那一段：真图标实测 y≈0.055H（410x776 下 = 43）。
        # 别从 0.010H 起扫 —— 实测蜀大侠那里有个 y=17 的更靠左的噪点，会被误当成返回键。
        g = wxwin.glyphs(buf, W, H, 0, int(H * 0.028), int(W * 0.22), int(H * 0.072))
        if g:
            b = g[0]
            print("[nav] 探测到标题栏左端图标 @(%d,%d) %dx%d（%d 像素 / 共 %d 个候选）"
                  % (b["x"], b["y"], b["w"], b["h"], b["n"], len(g)))
            return (b["x"], b["y"])
        print("[nav] 标题栏左端没探测到图标 → 退回经验比例")
    if not _PAGE["w"]:
        return None
    return (int(_PAGE["w"] * 0.058), int(_PAGE["h"] * 0.052))


def go_back(buf=None, W=0, H=0):
    """点标题栏左上角退回上一层（或回首页）。返回是否发了点击。"""
    p = nav_point(buf, W, H)
    if not p:
        print("[nav] 不知道页面在哪，无法返回")
        return False
    print("[nav] 点标题栏左上角「返回 / 回首页」%s（页面坐标）" % (p,))
    click(p[0], p[1])
    time.sleep(2.5)
    return True


def ocr_ok():
    return shutil.which("tesseract") is not None


def ocr_digits(png):
    r = subprocess.run("tesseract %s - --psm 7 -c tessedit_char_whitelist=0123456789* 2>/dev/null" % png,
                       shell=True, capture_output=True, text=True)
    return re.sub(r"\D", "", r.stdout or "")


def in_wechat_main(buf, W, H):
    """当前是不是**微信主窗口**（而不是小程序页面）。

    判据：左侧图标列（x 14..52）里「与底色不同的横向带」≥6 个 —— 那是微信侧边栏
    （头像/微信/通讯录/收藏/朋友圈/视频号/搜一搜/小程序…），**小程序窗口没有这一列**。
    作用：在错误的页面上跑这个脚本只会给出误导性的报错，先认出来并说清楚。
    """
    x0, x1 = 14, 52
    samples = [[], [], []]
    for y in range(0, H, 3):
        base = y * W * 3
        for x in range(x0, x1, 3):
            i = base + x * 3
            for c in range(3):
                samples[c].append(buf[i + c])
    bg = [sorted(s)[len(s) // 2] for s in samples]
    rows = []
    for y in range(0, H):
        base = y * W * 3
        n = 0
        for x in range(x0, x1):
            i = base + x * 3
            if abs(buf[i] - bg[0]) + abs(buf[i + 1] - bg[1]) + abs(buf[i + 2] - bg[2]) > 55:
                n += 1
        if n >= 3:
            rows.append(y)
    bands, cur = [], []
    for y in rows:
        if cur and y - cur[-1] <= 5:
            cur.append(y)
        else:
            if cur:
                bands.append(cur)
            cur = [y]
    if cur:
        bands.append(cur)
    return len([b for b in bands if len(b) >= 8]) >= 6


def color_density(buf, W, pred, box, step=2):
    """bbox 内该颜色的像素占比 —— 用来区分「实心按钮」和「文字/线条/图片」。"""
    x0, y0, x1, y1 = box
    hit = tot = 0
    for y in range(y0, y1 + 1, step):
        base = y * W * 3
        for x in range(x0, x1 + 1, step):
            i = base + x * 3
            tot += 1
            if pred((buf[i], buf[i + 1], buf[i + 2])):
                hit += 1
    return (hit / float(tot)) if tot else 0.0


def is_button(box, buf, W, pred, min_wf=0.25, min_ratio=2.5, min_density=0.45):
    """按钮 = 够宽 + 够扁 + 实心。

    三个条件缺一不可，因为实测踩过：
      · 卡片里的**绿色农田图片**（320×380）会被当成绿按钮 → 用「够扁」(宽高比≥2.5) 排除；
      · 页面上的**橙色文字行**（+1 / 日期）bbox 又宽又扁，会被当成大按钮 → 用「实心」(密度) 排除；
      · 窄的橙色标签（如「打卡签到」110px）→ 用「够宽」(≥0.25W) 排除。
    """
    if not box:
        return False
    x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0
    if w < W * min_wf or h <= 0 or (w / float(h)) < min_ratio:
        return False
    return color_density(buf, W, pred, box) >= min_density


def page_hash(buf, W, H, step=16):
    """页面的稀疏指纹 —— 用来判断「点完之后页面到底有没有变」。

    为什么需要：实测踩过 —— 小程序**刚打开 ~2 秒**时页面已经渲染好（截图看着正常），
    但点击会被丢掉（事件还没就绪）。不验证的话，脚本会以为「点了但没反应」，
    接着一路滚动，最后什么也没发生。
    """
    h = 0
    for y in range(0, H, step):
        base = y * W * 3
        for x in range(0, W, step):
            i = base + x * 3
            h = (h * 31 + buf[i] + buf[i + 1] * 3 + buf[i + 2] * 7) & 0xFFFFFFFF
    return h


def entry_points(W, H):
    """「进入注册流程」要点的候选位置，**按顺序**尝试。

    为什么是多个：吾享的注册入口通常是**两跳** ——
      ① 活动列表页的第一张活动卡（点进去）
      ② 签到/活动页里滚出来的「立即签到 / 参与」按钮
    实测（来菜）：①在约 (0.50, 0.31)，②在约 (0.42, 0.43)。两者位置不固定，
    所以做成候选列表 + 滚动兜底；页面上的**橙色大按钮**会优先被识别并点击（见 main）。

    可用 WXSIGN_REG_CLICK="0.50,0.31;0.42,0.43" 覆盖（分号分隔多个，比例或像素）。
    """
    raw = envv("WXSIGN_REG_CLICK").replace(" ", "")
    pts = []
    if raw:
        for seg in raw.split(";"):
            if not seg:
                continue
            parts = seg.split(",")
            if len(parts) != 2:
                continue
            try:
                x, y = float(parts[0]), float(parts[1])
            except ValueError:
                continue
            pts.append((int(x * W) if x < 1 else int(x),
                        int(y * H) if y < 1 else int(y)))
    if pts:
        return pts
    # 默认只给一个候选（活动列表的第一张卡）。给多个反而危险 ——
    # 页面切换后同一个坐标的含义会变（实测：在签到页点 (0.42,0.43) 会落到日历区）。
    # 剩下交给「橙色大按钮识别 + 滚动」，那两条比猜坐标可靠。
    return [(int(W * 0.50), int(H * 0.31))]


# ───────────────────────────── 检测 ─────────────────────────────
def detect(buf, W, H):
    """返回 dict：卡片/绿按钮/橙按钮/号码行（含 ✓ 锚点）"""
    card = white_card(buf, W, H)
    green = blob(buf, W, H, is_green, 0, W, 0, H)
    orange = blob(buf, W, H, is_orange, int(W * 0.10), int(W * 0.90), int(H * 0.30), int(H * 0.95), min_hits=8)
    gw = (green[2] - green[0]) if green else 0
    gh = (green[3] - green[1]) if green else 0
    # 手机号弹窗的特征：**一张白色卡片 + 卡内接近通栏的绿按钮**（那个「允许」）。
    # ⚠️ 判据必须用「占**卡片**宽」而不是「占**屏幕**宽」：
    #    早先写的是「绿按钮占屏幕 15%~25%」，那是按 1280 全屏标定的；
    #    窗口变成 410 宽的手机竖版后整个失效 —— 实测把来菜首页里绿色的
    #    「进行中」文字（宽 62px ≈ 0.15W）当成了弹窗按钮，导致流程直接跑挂。
    card_w = (card[2] - card[0]) if card else 0
    if card_w:
        phone_popup = bool(green and gw >= card_w * 0.55 and gh <= H * 0.12)
    else:
        # 没识别出白卡时的兜底：弹窗按钮在窄窗口下几乎是通栏的
        phone_popup = bool(green and gw >= W * 0.45 and gh <= H * 0.12)
    out = {"card": card, "green": green, "orange": orange,
           "phone_popup": phone_popup, "check": None, "rows": []}
    if out["phone_popup"]:
        # ✓ 锚点：绿按钮上方的绿色像素（排除按钮本身）
        out["check"] = blob(buf, W, H, is_green, 0, W, 0, green[1] - 6, step=2, min_hits=2)
        if out["check"]:
            cy = (out["check"][1] + out["check"][3]) // 2
            x0, x1 = (card[0], card[2]) if card else (green[0] - int(W * 0.06), green[2] + int(W * 0.30))
            y0, y1 = (card[1], green[1]) if card else (0, green[1])
            bands = text_bands(buf, W, H, y0, y1, x0, x1)
            step = int(H * ROW_STEP)
            for i in range(1, 7):
                y = cy + (i - 1) * step
                near = [b for b in bands if abs((b[0] + b[1]) // 2 - y) <= step * 0.45]
                if near:
                    out["rows"].append((i, y, (near[0][0] + near[0][1]) // 2))
    return out


def describe(tag, d, W, H):
    print("== %s (%dx%d) ==" % (tag, W, H))
    print("   白卡=%s 绿=%s 橙=%s" % (d["card"], d["green"], d["orange"]))
    if not d["green"] and not d["card"]:
        print("   → 无弹窗（页面本体）")
    elif d["phone_popup"]:
        print("   → 手机号弹窗；✓ 锚点=%s；可点行=%s" % (d["check"], d["rows"]))
    elif d["green"]:
        print("   → 隐私协议弹窗（绿按钮宽 %d）" % (d["green"][2] - d["green"][0]))
    elif d["card"]:
        print("   → 授权说明弹窗（或其它白卡弹窗）")


def analyze(path):
    buf, W, H = grab_file(path)
    d = detect(buf, W, H)
    describe(path, d, W, H)
    return d


# ───────────────────────────── 主流程 ─────────────────────────────
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--index", type=int,
                    default=int(envv("WXSIGN_REGISTER_PHONE_INDEX", "REG_PHONE_INDEX") or 0))
    ap.add_argument("--phone", default=envv("WXSIGN_REGISTER_PHONE", "REG_PHONE"))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--analyze", default="")
    a = ap.parse_args()

    if a.analyze:
        analyze(a.analyze)
        return

    if not refresh_page():
        print("[ui] ✗ 不知道页面在哪 —— 注册必须站在目标小程序的签到/活动页上。"
              "先让引擎开小程序（--ensure），或手工在微信里打开一次。")
        grab_png(0, 0, "reg_wrong_page.png", win=False)
        sys.exit(4)

    W, H = win_size()
    print("[ui] 页面 %dx%d @ %s（来源 %s）| INDEX=%s PHONE=%s OCR=%s"
          % (W, H, origin(), _PAGE["src"],
             a.index or "(未设)", a.phone or "(未设)", "有" if ocr_ok() else "无"))

    scroll = int(envv("WXSIGN_REG_SCROLL", default="0") or 0)
    if scroll:
        scroll_down(scroll, W, H)      # 有些品牌一进来就要先滚（默认不滚，交给轮次逻辑）
        time.sleep(0.6)
    grab_png(W, H, "reg_00_start.png")

    cands = entry_points(W, H)
    print("[ui] 入口候选=%s" % (cands,))
    granted = False          # 是否已点过手机号弹窗的「允许」

    # 能力升级：**优先按文案找元素**（CDP 读小程序渲染层 DOM），像素识别降级为兜底。
    # 原因：颜色 + 宽高比 + 密度的判据跨品牌必然失效 —— 实测调了三轮仍在补漏洞
    # （把卡片里的绿色农田图当按钮、把橙色文字行当按钮）。文案是同一套模板的，可靠得多。
    # 注意边界：**微信原生弹窗不在小程序 DOM 里**（手机号授权的「允许」），那部分仍走像素。
    ws = ctx = None
    try:
        import wxdom
        ws, ctx = wxdom.open_dom()
        print("[ui] CDP DOM %s" % ("可用（ctx=%d）→ 按文案定位按钮" % ctx if ws
                                   else "不可用 → 退回像素识别"))
    except Exception as e:
        print("[ui] wxdom 不可用（%s）→ 退回像素识别" % e)
    if ws:
        # ⭐ 有 CDP 就用**页面自己报的**位置/尺寸，别用窗口探测 ——
        #    这样将来小程序改成侧边栏/分栏（Windows 版那样）时，这条依然成立。
        refresh_page(ws, ctx)
        W, H = win_size()
        print("[ui] 页面视口 %dx%d @ %s（来源 %s）" % (W, H, origin(), _PAGE["src"]))
    clicked = set()
    sheet_clicked = False    # 是否已点过「小程序自己的注册弹层」（点一次就够，防死循环）
    submit_tries = 0         # 点过几次「注册表单提交按钮」（限次，防死循环）
    clicked_px = set()       # 像素捏到的按钮位置，点过就不再点（否则会在同一处反复空点、占着轮次不滚动）
    scroll_rounds = 0        # 连续「没点到任何东西、只能滚」的轮数（用来发现「跑偏了」）
    nav_tries = 0            # 已经「退回上一层重来」了几次（设上限，避免来回绕圈）

    for step in range(1, 17):
        # ⭐ 每轮先把小程序窗口置顶：面板会一直赖在最前，不置顶的话抓屏抓到的是**面板**
        #    （像素判据会把面板的卡片当弹窗），点击也会被面板吃掉。
        raise_miniapp()
        if ws:
            # 每轮刷新页面几何：窗口可能被拖动；侧边栏形态下布局还可能随内容变化
            refresh_page(ws, ctx)
            W, H = win_size()
        buf = grab(W, H)
        d = detect(buf, W, H)

        # ① 手机号授权弹窗（最优先 —— 它出现说明流程就快完了）
        if d["phone_popup"]:
            # ⚠️ 先回答「我抓到的到底是不是目标小程序」。
            #    小程序是从**全屏面板**里点开的，面板一旦留在最前，抓屏抓到的就是面板；
            #    而面板的卡片里也有「白卡 + 卡内通栏绿按钮」，会被判成手机号弹窗，
            #    再走「没识别到白卡不敢盲点」的安全阀直接退出 —— 整轮注册就这么废了
            #    （实测绿茵阁西餐厅：reg_phone_popup.png 里是面板的搜索卡片）。
            #    区分办法：挡住中心的那块窗口**是全屏** → 一定是面板；
            #    真·手机号弹窗是个**小窗**，不满足这条，照常往下走。
            g = blocker_over_miniapp()
            if panel_is_blocking(g):
                print("[ui] 抓到的不是小程序（被全屏面板挡住了，%dx%d）→ 置顶后重试"
                      % (g["WIDTH"], g["HEIGHT"]))
                raise_miniapp()
                time.sleep(1.2)
                continue
            # ⭐ 最优先：**注册表单的提交按钮**。
            #    实测绿茵阁西餐厅（2026-09-25 手工验证通过）：流程走到这里时，页面上
            #    已经是一个**注册表单** —— wx-user-info 组件弹出来的，内容是
            #      「绿茵阁西餐厅Greenery 申请使用 / 获取你的手机号码 190****7634 /
            #       会员头像 / *昵称 神秘顾客 / 设置卡密码 / 邀请码」
            #    底部一颗 WX-BUTTON「**确认授权开通并绑定会员**」。
            #    也就是说：**手机号早就授权到手了，只差点这一下**。点完 member/single
            #    立刻从 401 变 200（真机验证），签到成功拿到 5 积分。
            #
            #    之前几轮的实现在这里去找「微信原生弹窗」的「允许」按钮 —— **目标从一开始
            #    就错了**：那是小程序自己的表单，提交按钮文案是「确认授权开通并绑定会员」，
            #    不是微信的「允许」。
            #
            #    必须**排在「找登录入口」之前**：表单打开后底层那个「立即登录」还在 DOM 里，
            #    先点它只会白费轮次。用 submit_tries 限次防死循环。
            if submit_tries < 2 and ws:
                now = wxdom.find_dom_ctx(ws, 20)
                if now:
                    ctx = now
                dd = wxdom.scan(ws, ctx)
                it = wxdom.pick_action(dd, "submit") if dd else None
                if it:
                    submit_tries += 1
                    sheet_clicked = True          # 视作「小程序自己的层」，别再走下面那条
                    print("[ui] DOM 里发现注册表单的**提交按钮**「%s」→ 点它"
                          "（第 %d 次）" % (it["text"].replace("\n", " ")[:20], submit_tries))
                    if not a.dry_run:
                        h0 = page_hash(buf, W, H)
                        for attempt in range(1, 4):
                            click(it["cx"], it["cy"])
                            time.sleep(2.5)
                            if page_hash(grab(W, H), W, H) != h0:
                                break
                            print("     → 页面没变，重试（%d/3）" % attempt)
                    scroll_rounds = 0
                    time.sleep(1.5)
                    continue
            # ⚠️ 再分辨「这是谁的弹层」。判据只用一条，且是代码里本来就写明的边界：
            #    **微信原生弹窗（含手机号授权）不在小程序 DOM 里**（见文件头那句说明）。
            #    所以「DOM 里还能找到登录/注册入口」= 这是**小程序自己的**注册弹层。
            #
            #    为什么不能靠 `d["card"]` 区分（前一版这么写过，被实测打脸）：
            #    小程序自己的那层**也是「白卡 + 卡内通栏绿按钮」**（实测绿茵阁西餐厅：
            #    「Hi，神秘食客 / 更好的会员服务，注册登录后即可体验」+ 绿色「立即登录」），
            #    于是 `d["card"]` 为真、`d["phone_popup"]` 也为真，
            #    后面就把它当手机号弹窗去点「允许」→ 点的是页面上的按钮，注册根本没发生
            #    （现象：日志显示「点允许」两次，但 `member/single` 仍然 401）。
            #    只点一次就够（sheet_clicked 守卫）：点完这层会消失，
            #    下一轮若真的弹出原生手机号弹窗，DOM 里就没有登录入口了，自然走下面那条路。
            if not sheet_clicked and ws:
                now = wxdom.find_dom_ctx(ws, 20)
                if now:
                    ctx = now
                dd = wxdom.scan(ws, ctx)
                it = wxdom.pick_action(dd, "login") if dd else None
                if it:
                    sheet_clicked = True
                    print("[ui] 白卡+通栏绿按钮，但 DOM 里还有「%s」→ 判定为**小程序自己的**"
                          "注册弹层（不是微信手机号弹窗）→ 点它"
                          % it["text"].replace("\n", " ")[:16])
                    if not a.dry_run:
                        h0 = page_hash(buf, W, H)
                        for attempt in range(1, 4):
                            click(it["cx"], it["cy"])
                            time.sleep(2.5)
                            if page_hash(grab(W, H), W, H) != h0:
                                break
                            print("     → 页面没变，重试（%d/3）" % attempt)
                    scroll_rounds = 0
                    time.sleep(1.5)
                    continue
            png = grab_png(W, H, "reg_phone_popup.png")
            rows = d["rows"]
            print("[ui] 手机号弹窗：检测到 %d 个可选号码 %s" % (len(rows), [r[1] for r in rows]))
            if not rows:
                # **号码行识别不出来时不要放弃**：绝大多数账号只绑了一个号码，
                # 微信此时**默认已经选中它**，直接点「允许」就行，根本不用点号码行。
                # 实测农耕记的弹窗就是这样：那个 ✓ 在号码的**右侧**而不是上方
                # （原实现按「✓ 在按钮上方 + 向下推算行距」找号码，前提就不成立），
                # 加上页面背景大片青绿色会被 is_green 命中、把锚点带偏 ——
                # 死守「先找到号码行」就卡在这里了。
                # 只留一道安全阀：**必须确认这是白色弹窗卡片**，否则不敢盲点。
                if not d["card"]:
                    print("[FAIL] 没识别到白色弹窗卡片，不敢盲点「允许」。截图 %s" % png)
                    sys.exit(3)
                print("[ui] 号码行没识别出，但白卡在 → 按「只绑了一个号码」处理"
                      "（微信已默认选中），直接点「允许」")
                rows = [(1, 0, 0)]

            if a.phone:
                if ocr_ok() and len(rows) > 1:
                    hit = 0
                    for i, y, yc in rows:
                        crop = "%s/row_%d.png" % (SHOT_DIR, i)
                        run("ffmpeg -loglevel error -y -i %s -vf crop=%d:34:%d:%d %s"
                            % (png, int(W * 0.55), int(W * 0.12), max(0, yc - 17), crop))
                        got = ocr_digits(crop)
                        print("       第%d行 识别=%s" % (i, ("*" * max(0, len(got) - 4)) + got[-4:] if got else "(空)"))
                        if got and (got == a.phone or got.endswith(a.phone[-4:])):
                            hit = i
                            break
                    if not hit:
                        print("[FAIL] 没有 phone=%s 对应的行；看 %s" % (a.phone, png))
                        sys.exit(2)
                    a.index = hit
                elif len(rows) == 1:
                    print("[ui] 只有 1 个号码 → 直接用（PHONE 仅留档，请核对 %s）" % png)
                    a.index = 1

            # 号码行检测在手机竖版下不可靠 —— 实测农耕记把「你的手机号码」标题也算成了一行
            # （✓ 锚点先被页面背景的青绿色带偏，再从锚点往下按行距推算，就把标题收进来了）。
            # 但**选错行的代价很低**：列出来的都是**你自己账号绑定的**号码，
            # 选错也只是换成另一个自己的号，注册照样成功 —— 不值得为此中止整个流程。
            # 所以：设了 INDEX 就按它选；没设就**不点行**，用微信默认选中的那个。
            y = rows[a.index - 1][1] if (a.index and a.index <= len(rows)) else 0
            if not y and len(rows) > 1 and not a.index:
                print("[ui] 检测到 %d 行（可能把标题也算进来了）→ 不猜，"
                      "用微信默认选中的号码" % len(rows))
            gx = (d["card"][0] + d["card"][2]) // 2 if d["card"] else W // 2
            if y:
                print("[ui] 选第 %d 个号码（y=%d）" % (idx, y))
                if not a.dry_run:
                    click(gx, y)
                    time.sleep(0.8)
            else:
                # y=0 是「号码行没识别出来」的占位（见上面）→ 不点行，只点「允许」
                print("[ui] 只绑了一个号码且已默认选中 → 跳过选行")
            g = d["green"]
            print("[ui] 点「允许」(%d,%d)" % ((g[0] + g[2]) // 2, (g[1] + g[3]) // 2))
            if not a.dry_run:
                click((g[0] + g[2]) // 2, (g[1] + g[3]) // 2)
                granted = True
                time.sleep(6)
            continue

        # ② 小程序自己渲染的元素 → 按**文案**定位（跨品牌通用，完全不看颜色/尺寸）
        if ws:
            # ⚠️ 每轮都要重探 context：页面切换后是新 WebView（新 ctx），旧 ctx 的 DOM 还留着 ——
            #    硬编码 ctx 会一直读到过期页面，然后在错误坐标上反复空点（踩过）。
            now = wxdom.find_dom_ctx(ws, 20)
            if now:
                ctx = now
            dd = wxdom.scan(ws, ctx)
            # 两级策略（反馈：「别人也不一定叫『立即签到』」）：
            #   ① 同义词根命中（面积最小者）  ② 都没命中 → 按「大 + 靠下 + 文案短」评分兜底
            # 弹窗的确认类优先于页面的签到类（弹窗挡在最上层）。
            is_clicked = lambda it: (it["text"], it["cx"], it["cy"]) in clicked      # noqa: E731
            # ⭐ 四个词表**按「该先点谁」排序**，每一条都对应一段实测踩过的路：
            #   ① submit —— 注册表单的**提交**按钮（「确认授权开通并绑定会员」）。
            #      表单一旦打开，这才是唯一该点的东西；此时底层那个「立即登录」还在
            #      DOM 里，先点它只会白费轮次。
            #   ② confirm —— 小程序的确认/隐私类（「同意并继续」），它挡着别的操作。
            #   ③ login —— ⚠️ **这一步以前整个缺失**（原来只有 confirm + sign），
            #      于是注册流程永远走不到「登录入口」。实测 audit21（我的小板凳街坊火锅）
            #      就卡死在这：最后只在页面上点了一行**说明文字**，什么都没发生，
            #      一路「无按钮 → 滚动找目标」到轮次耗尽、报 rc=3。
            #      文案各品牌不同（绿茵阁「立即登录」/ 板凳「请登录」），所以用**词根**
            #      （`wxdom.LOGIN_WORDS` 含「登录」「注册」）+「短文案优先」排序
            #      （按钮文案短、说明文字长）来选。
            #   ④ sign —— 签到类（正常签到流程）。
            target = (wxdom.pick_action(dd, "submit", is_clicked)
                      or wxdom.pick_action(dd, "confirm", is_clicked)
                      or wxdom.pick_action(dd, "login", is_clicked)
                      or wxdom.pick_action(dd, "sign", is_clicked))
            if target:
                key = (target["text"], target["cx"], target["cy"])
                print("[ui] DOM 文案「%s」→ 点 (%d,%d)"
                      % (target["text"].replace("\n", " ")[:24], target["cx"], target["cy"]))
                if not a.dry_run:
                    # ⚠️ 点完要**确认页面真的变了**，没变就重试。
                    # 小程序刚打开的那一两秒，页面渲染完了但事件还没就绪，**点击会被丢掉**。
                    # 实测农耕记就是被这个坑死的：点了活动卡但没跳转，而它已被记进 clicked，
                    # 于是后面一路「无目标」→ 误判成跑偏 → 空转到轮次耗尽。
                    h0 = page_hash(buf, W, H)
                    for attempt in range(1, 4):
                        click(target["cx"], target["cy"])
                        time.sleep(2.5)
                        if page_hash(grab(W, H), W, H) != h0:
                            if attempt > 1:
                                print("     → 第 %d 次点击才生效" % attempt)
                            break
                        print("     → 页面没变，重试（%d/3）" % attempt)
                clicked.add(key)
                scroll_rounds = 0        # 点到了东西 = 有进展，重置「疑似跑偏」计数
                time.sleep(1.5)
                continue

        g = d["green"]
        px = ((g[0] + g[2]) // 2, (g[1] + g[3]) // 2) if is_button(g, buf, W, is_green) else None
        if px and px not in clicked_px:                 # 隐私协议「同意并继续」
            clicked_px.add(px)
            print("[ui] 点「同意并继续」(%d,%d)" % px)
            if not a.dry_run:
                click(px[0], px[1])
                time.sleep(3)
            continue

        o = d["orange"]
        px = (((o[0] + o[2]) // 2, (o[1] + o[3]) // 2)
              if is_button(o, buf, W, is_orange, min_density=0.5) else None)
        if px and px not in clicked_px:
            # 弹窗里的「授权」/ 页面上的「立即签到」「参与」—— 都是同一类大按钮
            clicked_px.add(px)
            print("[ui] 点橙色大按钮「授权 / 立即签到」(%d,%d)" % px)
            if not a.dry_run:
                click(px[0], px[1])
                time.sleep(4)
            continue

        # ④ 没识别到按钮 —— 已点过「允许」且页面安静了，就认为流程结束
        if granted:
            print("[ui] 已点过「允许」，页面不再有弹窗 → 流程结束（结果以接口复查为准）")
            grab_png(W, H, "reg_done.png")
            return

        # 否则：先按候选入口推进（活动卡 → 签到页），候选用完就滚动把按钮露出来
        if step <= len(cands):
            x, y = cands[step - 1]
            print("[ui] 第%d步：无按钮 → 点入口候选#%d (%d,%d)" % (step, step, x, y))
            if not a.dry_run:
                h0 = page_hash(buf, W, H)
                changed = False
                for attempt in range(1, 4):
                    click(x, y)
                    time.sleep(2.5)
                    buf2 = grab(W, H)
                    if page_hash(buf2, W, H) != h0:
                        print("     → 页面已变化（第 %d 次点击生效）" % attempt)
                        changed = True
                        break
                    print("     → 页面没变，重试点击（%d/3）" % attempt)
                if not changed:
                    print("     ⚠️ 点 3 次页面都没变 —— 坐标可能不对，可调 WXSIGN_REG_CLICK")
        else:
            # 候选用完 → 滚动把按钮露出来。
            # 滚了几轮仍然"这一页上没有任何可点的目标"，就怀疑是**误点跑到别的页面**了 ——
            # 点标题栏左上角退回去重来。那个按钮：页面栈 >1 层时是「< 返回」，
            # 只剩 1 层时是「⌂ 回首页」，**位置相同**，所以不管跑多远，点它总能往回走。
            if scroll_rounds < 3:
                scroll_rounds += 1
                print("[ui] 第%d步：无按钮 → 滚动找目标（第 %d/3 次）" % (step, scroll_rounds))
                if not a.dry_run:
                    scroll_down(3, W, H)
            else:
                if nav_tries >= 2:
                    print("[nav] 已经退回过 2 次、这页仍然没有可点目标 → 停手"
                          "（该账号在这个品牌可能就是没有要办的事）")
                    break
                nav_tries += 1
                print("[nav] 入口点完了、也滚了 3 轮，这页始终没有可点目标 → "
                      "疑似点偏到别的页面，退回上一层重来（第 %d 次）" % nav_tries)
                scroll_rounds = 0
                # ⚠️ 只清「像素点过」的记录，**不清 clicked** ——
                #    否则退回首页后会把同一个「打卡签到」再点一遍、又进签到页、又退回来，
                #    来回绕圈直到轮次耗尽（实测踩过）。
                clicked_px.clear()
                if not a.dry_run and not go_back(buf, W, H):
                    break
        time.sleep(3.5)

    print("[ui] 轮次用尽 —— 若没注册成功，看 %s/reg_*.png 调 WXSIGN_REG_CLICK" % SHOT_DIR)
    grab_png(W, H, "reg_end.png")
    sys.exit(3)


if __name__ == "__main__":
    main()
