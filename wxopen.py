#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
在微信实例容器内运行：把指定的一个小程序打开（用于刷 token / 签到前准备）。

调用契约：由 wxsign.py 通过环境变量传入
    WXSIGN_MINIAPP   目标小程序的**精确名称**（用窗口标题精确匹配来确认开对了）
    WXSIGN_KEYWORD   小程序面板里搜索用的关键词
    WXSIGN_FALLBACK_KEYWORD  可选，旧版兜底路线用；不设就跳过那条路线

同名号很多（商店搜索经常返回一串近名/同名小程序），所以确认一律按**窗口标题精确等于**，
不靠列表顺序、不靠图标长相；开错了就关掉继续试下一张卡片。

主要路径（**不依赖「最近使用过的小程序」，也不依赖当前停在哪个标签**）
  A. 打开小程序面板：侧边栏顶部那组最后一个按钮（= 「小程序」）
  B. 面板右上角放大镜 → 输入关键词 → **按回车** → 面板里开出「关键词_搜索」结果页
  C. 在结果卡片里逐张点：卡片位置由像素定位（一行可能有 3 张，按列切）
       · 开出标题=目标的小程序窗口 → 成功
       · 开出别的窗口               → 点微信自己的关闭按钮关掉，继续下一张
  D. 每次点击前都**重新截图定位**（实测下拉/结果页的纵向布局会漂移几十像素）

备选/兜底
  E. 主窗口搜索 → 输入 → 进「搜一搜」结果页 → 点结果行（默认**不跑**，见下）
  F. 兜底路线（搜另一个号 → 点下拉里的小程序行 → 首页 → banner 跳转），需设 WXSIGN_FALLBACK_KEYWORD

其它要点
  · 开头先把分辨率钉到 1280x1024：KasmVNC 的桌面分辨率会跟随**观看者窗口大小**变化，
    不钉死的话同一套比例坐标在没人观看时会落到别处（这个坑踩过）
  · 点击一律用**窗口坐标系的比例**（绝对坐标 = 窗口 X/Y + 窗口宽高 × 比例），
    并且**点之前先问一句「鼠标底下是谁」**（见 click_expect）—— 不确认就不点
  · **绝不能 `xdotool windowclose` 关微信的窗口**：X 窗口没了但微信内部状态不复位，
    面板/小程序会再也打不开，只能重启微信。要关就点微信自己的按钮。
  · **主窗口标题栏最右那个 ✕ 是整个微信的关闭键**（白 ✕ 压在高饱和红块上），
    位置和面板/小程序窗口的 ✕ 几乎重合（主窗口 0.983W/0.017H、面板 0.979W/0.0205H）——
    所以关窗一律「先认身份、再按窗口自身几何算按钮位置、点前验那儿真有字形」，
    点完复核，**不补第二下**（连点正是「多点一次把微信关掉」的来源），见 close_window
  · 判断不了就**不点**，存截图返回非 0

用法（容器内）：python3 wxopen.py [--check] [--close] [--loose]
  --check  只报告目标小程序现在开着没有
  --close  关掉小程序与面板（释放内存；签完收尾用）
退出码：0 已打开（--close 时为已关闭）；3 没打开（截图在 /tmp/shots）；
        4 点过候选但无法用窗口标题确认 —— `--loose` 模式下如此返回，
        交给外层按 appId 复核（见 wxsign.py 的 ensure_miniapp）。

环境变量：
  WXSIGN_MINIAPP / WXSIGN_KEYWORD  目标小程序名 / 面板搜索关键词（**必填**）
  WXSIGN_FALLBACK_KEYWORD          可选，旧版兜底路线；不设就跳过
  WXSIGN_CHAT_SEARCH=1             额外允许走「主窗口搜索」（默认关闭，原因见 open_via_search）

判据分两层（这层专管「点哪里」，身份确认交给 appId）
  · 本脚本的**入口定位与点击**依赖具体界面布局（见上文路径 A–F），微信改版就要跟着改；
  · 「打开的是不是目标号」最终以 **appId** 为准（读 `wx.getAccountInfoSync().miniProgram.appId`），
    它与窗口形态无关 —— 小程序以后不再独立开窗也照样能确认。
    加 `--loose` 时本脚本在「点了但没等到目标窗口」的情况下返回 4 而不是 3，
    就是为了让外层走这条 appId 复核，别把「界面形态变了」误判成「打开失败」。
"""
import os
import re
import subprocess
import sys
import time

DISPLAY = os.environ.get("DISPLAY", ":1")
SHOT_DIR = os.environ.get("SHOT_DIR", "/tmp/shots")

# 宽松模式：点过候选卡片、但没能用窗口标题确认时，返回 4（而不是 3）让外层按 appId 复核。
# 用途：万一微信改成「小程序不独立开窗」（Windows 新版已经是窗口内右侧栏），
# 窗口标题这条判据会失效，但打开动作其实可能已经成功 —— 此时不该硬判失败。
LOOSE = "--loose" in sys.argv
TOUCHED = [False]        # 是否点过候选卡片（用单元素列表，省去到处 global 声明）
WANT_W, WANT_H = 1280, 1024

# 目标小程序：打开后窗口标题里会出现这个名字。
# ⚠️ 必须按**窗口标题精确等于**判定，不能用子串 —— 商店搜索里经常有多个同名/近名号
#（辣可可就是 `辣可可现炒黄牛肉` 与 `辣可可现炒黄牛肉i` 两个），子串匹配会两个都命中。
# 引擎会通过 WXSIGN_MINIAPP / WXSIGN_KEYWORD 传进来；也兼容辣可可项目原来的 LAKEKE_* 变量名。
TARGET = os.environ.get("WXSIGN_MINIAPP") or os.environ.get("LAKEKE_MINIAPP", "")
KEYWORD = os.environ.get("WXSIGN_KEYWORD") or os.environ.get("LAKEKE_KEYWORD", "")
# 旧版兜底路径（搜另一个号 → 点banner跳转）用的关键词；留空则跳过这条兜底。
FALLBACK_KEYWORD = os.environ.get("WXSIGN_FALLBACK_KEYWORD", "")
if __name__ == "__main__" and (not TARGET or not KEYWORD):
    # 只有「被直接执行」时才强制要这两个变量；被 wxfind.py / wxclean.py import 时
    # 只是借用窗口/点击原语，不需要它们（否则 import 就会炸）
    raise SystemExit("[wxopen] 必须给 WXSIGN_MINIAPP 与 WXSIGN_KEYWORD"
                     "（目标小程序名 + 面板搜索关键词）")

# 比例坐标（1280x1024 实测）
R_RAIL_X = 0.026                    # 侧边栏图标列的横坐标（按钮 y 由像素扫描算出来）
R_RAIL_COL = (14, 52)               # 侧边栏图标列的像素范围（找按钮用）
R_PANEL_MAGNIFIER = (0.974, 0.0625)  # 面板内容区右上角搜索（放大镜）；按钮框比字形大，
                                     # 所以这个点虽然偏右 23px 仍然落在按钮上（实测）
# 「小程序」标签页的紫色图标出现在这条标签条里（扫整条，可能有多个标签）：
R_TABSTRIP = (0.05, 0.45, 0.004, 0.038)      # x0f,x1f,y0f,y1f

# 「关窗」：**先认身份，再按窗口自身的比例算按钮位置**，点前还要验那儿真有字形（见 close_point）。
#   · 小程序窗口：自绘标题栏，最右的 ◎ 在 (0.978W, 0.0435H)
#   · 面板这类微信自己的窗口：顶部 chrome 的 ✕ 在 (0.979W, 0.0205H)
#   · 主窗口的 ✕ 在 (0.983W, 0.017H) —— 和面板几乎重合，这就是危险所在：
#     所以主窗口绝不作为关闭目标，标题叫「微信」的窗口还必须先通过面板芯片校验。
# 旧写法是「盲点 (0.978W,0.041H)，不行再盲点 (0.978W,0.020H)」—— 后面那个正好压在
# 主窗口的红底白✕上，多试一次就是关掉整个微信，已废弃。
R_CLOSE_MINIAPP = (0.978, 0.0435)
R_CLOSE_PANEL = (0.979, 0.0205)

# 主窗口搜索框（**只有「微信」标签下这个框才是全局搜索**）
#   微信标签实测：框 x74..251 / y44..69，占位「搜索」跨度 33
#   收藏标签实测：框更宽（x72..291 / 量到 181），占位「搜索收藏」跨度 58，
#   作用域只有收藏 —— 搜不到小程序，也没有小程序推荐列表。取中间值 45 当分界。
# 扫描区 x 只到 0.20W：再往右是聊天内容区（纯白），会把亮行连成一大片、认不出框
R_SEARCHBOX_SCAN = (0.03, 0.12, 0.05, 0.20)   # y0f,y1f,x0f,x1f 扫描区
PLACEHOLDER_MAX = 45                          # 占位文字跨度超过它 ⇒ 不是全局搜索，不用这个框

# 兜底路径（搜「辣可可甄选」）用到的比例坐标
R_LOGO_AREA = (0.03, 0.45, 0.095, 0.26)    # x0f, x1f, y0f, y1f：下拉里的小程序 logo 区域
R_ROW_CLICK_X = 0.16
R_BANNER = (0.496, 0.517)


# ───────────────────────── 基础工具 ─────────────────────────

def run(cmd, timeout=25):
    try:
        return subprocess.run(cmd, shell=True, capture_output=True, text=True,
                              timeout=timeout).stdout
    except Exception:
        return ""


def size():
    out = run("DISPLAY=%s xdotool getdisplaygeometry" % DISPLAY).strip().split()
    return (int(out[0]), int(out[1])) if len(out) == 2 else (1280, 1024)


def grab(W, H):
    """整个屏幕的原始 RGB 帧（纯标准库解析，容器里没有 numpy/PIL）"""
    d = subprocess.run("DISPLAY=%s ffmpeg -loglevel error -f x11grab -video_size %dx%d -i %s "
                       "-frames:v 1 -f rawvideo -pix_fmt rgb24 -" % (DISPLAY, W, H, DISPLAY),
                       shell=True, capture_output=True).stdout
    if len(d) < W * H * 3:
        raise RuntimeError("截屏失败（%d 字节）" % len(d))
    return d


def png(W, H, name):
    os.makedirs(SHOT_DIR, exist_ok=True)
    p = os.path.join(SHOT_DIR, name)
    run("DISPLAY=%s ffmpeg -loglevel error -y -f x11grab -video_size %dx%d -i %s -frames:v 1 %s"
        % (DISPLAY, W, H, DISPLAY, p))
    return p


def click(x, y, wait=0.7):
    run("DISPLAY=%s xdotool mousemove %d %d; sleep 0.35; DISPLAY=%s xdotool click 1"
        % (DISPLAY, int(x), int(y), DISPLAY))
    time.sleep(wait)


def key(k, wait=0.6):
    run("DISPLAY=%s xdotool key %s" % (DISPLAY, k))
    time.sleep(wait)


def type_text(s):
    run('DISPLAY=%s xdotool type --delay 130 "%s"' % (DISPLAY, s))
    time.sleep(1.5)


def px(buf, W, x, y):
    i = (y * W + x) * 3
    return buf[i], buf[i + 1], buf[i + 2]


# ───────────────────── 窗口：查找 / 身份 / 点击安全 ─────────────────────

def windows():
    """可见窗口 [(id, 标题)]"""
    out = []
    for wid in run("DISPLAY=%s xdotool search --onlyvisible --name '.+' 2>/dev/null"
                   % DISPLAY).split():
        out.append((wid, run("DISPLAY=%s xdotool getwindowname %s 2>/dev/null"
                             % (DISPLAY, wid)).strip()))
    return out


def title_of(wid):
    return run("DISPLAY=%s xdotool getwindowname %s 2>/dev/null" % (DISPLAY, wid)).strip()


def win_class(wid):
    """取窗口的 WM_CLASS（主窗口是 wechat；面板/视频号/搜一搜这类窗口没有这个属性）"""
    out = run("DISPLAY=%s xprop -id %s WM_CLASS 2>/dev/null" % (DISPLAY, wid))
    if "not found" in out or "=" not in out:
        return ""
    return out.split("=", 1)[1].strip().strip('"')


def geo(wid):
    """窗口几何 {X,Y,WIDTH,HEIGHT}（屏幕绝对坐标）"""
    d = {}
    for line in run("DISPLAY=%s xdotool getwindowgeometry --shell %s" % (DISPLAY, wid)).splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            d[k.strip()] = v.strip()
    return d


def is_main_window(wid):
    """微信主窗口（WM_CLASS 含 wechat）。
    ⚠️ 它的标题栏最右是**整个微信**的关闭键，任何情况下都不许点。"""
    return "wechat" in win_class(wid).lower()


def find_main_window():
    """微信主窗口 = 标题「微信」且 WM_CLASS 含 wechat 的那个。
    ⚠️ 不能用「id 最小」来猜：小程序面板窗口的 id 可能比主窗口还小（实测过）。"""
    for wid, title in windows():
        if title.strip() == "微信" and is_main_window(wid):
            return wid
    return None


def find_target_window():
    """目标小程序已经开着？（窗口标题就是小程序名，按**精确相等**匹配 ——
    同名号里 `辣可可现炒黄牛肉` 和 `辣可可现炒黄牛肉i` 是两个不同的小程序）"""
    for wid, title in windows():
        if title.strip() == TARGET:
            return wid, title
    return None, None


def raise_window(wid):
    """把窗口提到最前。windowactivate 走 EWMH（要窗口管理器配合），windowraise 走
    XRaiseWindow，两个都发一遍最稳 —— 有别的窗口挡着时，点侧边栏/面板都会落到别人身上
    （实测踩过：面板在前台时，点侧边栏其实点到了面板窗口上）。"""
    run("DISPLAY=%s xdotool windowactivate %s" % (DISPLAY, wid))
    run("DISPLAY=%s xdotool windowraise %s" % (DISPLAY, wid))
    time.sleep(0.8)


def win_under(x, y):
    """把鼠标移到 (x,y)，返回该点最上层窗口（在我们窗口列表里的那个）id，认不出返回 None。
    getmouselocation 报的常常是窗口管理器（Openbox）的**框架**窗口，客户窗口是它的子窗口，
    所以要再下一层找；没被 reparent 时它直接就报客户窗口。
    用途：**每次点击前先问一句「我鼠标底下是谁」**，不是预期目标就不点。"""
    run("DISPLAY=%s xdotool mousemove %d %d" % (DISPLAY, int(x), int(y)))
    time.sleep(0.35)
    wid = ""
    for line in run("DISPLAY=%s xdotool getmouselocation --shell" % DISPLAY).splitlines():
        if line.startswith("WINDOW="):
            wid = line.split("=", 1)[1].strip()
    if not wid:
        return None
    known = {str(w) for w, _ in windows()}
    if wid in known:
        return wid
    for line in run("DISPLAY=%s xwininfo -id %s -tree" % (DISPLAY, wid)).splitlines()[1:]:
        m = re.match(r"\s+(0x[0-9a-fA-F]+)\b", line)
        if m:
            dec = str(int(m.group(1), 16))
            if dec in known:
                return dec
    return None


def top_window(W=None, H=None):
    """当前最上层的窗口 id（在屏幕中心探一下）"""
    if W is None:
        W, H = size()
    return win_under(int(W * 0.5), int(H * 0.5))


def click_expect(wid, x, y, wait=0.7, what=""):
    """点之前先确认「鼠标底下就是 wid」，不是就不点。"""
    under = win_under(x, y)
    if str(under) != str(wid):
        print("[reopen] ⛔ (%d,%d) 底下是窗口 %s，不是预期目标 %s → 不点%s"
              % (x, y, under or "（认不出）", wid, (" " + what) if what else ""))
        return False
    click(x, y, wait)
    return True


def click_in(wid, fx, fy, wait=0.7, what=""):
    """在**窗口坐标系**里按比例点：绝对坐标 = 窗口 X/Y + 窗口宽高 × 比例。
    （老写法直接把 `宽×比例` 当屏幕坐标用，窗口只要不在 (0,0)，点就会落到别的窗口上。）"""
    g = geo(wid)
    try:
        X, Y, Wd, Hd = int(g["X"]), int(g["Y"]), int(g["WIDTH"]), int(g["HEIGHT"])
    except (KeyError, ValueError):
        return False
    return click_expect(wid, X + int(Wd * fx), Y + int(Hd * fy), wait, what)


# ───────────────────── 关闭窗口：先认身份，再取按钮位置 ─────────────────────

def probe_close_glyph(buf, W, H, X, Y, Wd, Hd):
    """**探测**标题栏右端最靠右的那个「紧凑字形块」（关闭键在控制组的最右），
    返回其中心 (x, y)；没探测到返回 None。不依赖任何按钮比例坐标。

    怎么探测：逐像素和**本地底色**比（同一行左边 8..16 列 + 右边 8..16 列的中位数），
    差别大的算前景；再做 4 邻域连通域，取「面积 16..420、宽 5..30、高 5..26、
    宽高比 0.45..2.4」的块里最靠右的那个。

    为什么不用「整条条带的中位数」当底色：小程序窗口的标题栏是**整条品牌色**
    （实测正牌是黄 250,209,78、开错的那个是暗红 141,13,25），全局底色会把
    浅色药丸和里面的深色字形并成一大块，直接检不出来（实测：候选数 0）。
    本地底色能穿过这类「整条有色」的标题栏。

    ⚠️ 探测的**局限**（实测过，所以不能只靠它）：小程序窗口的药丸右端圆角处，
    右侧邻居落到标题栏上，会被误判成前景，于是「最靠右的块」可能是药丸右帽
    (1264,42) 而不是 ◎ (1250,42) —— 哪个字形是关闭键属于**语义**，
    几个字形（··· ─ ◎ ▢ ✕）在像素上长得太像，纯视觉分不出来。
    所以本函数只负责「落在字形上」，是关闭键这件事由 R_CLOSE_* 的实测比例确定。
    """
    x0, x1 = max(0, X + int(Wd * 0.86)), min(W - 1, X + Wd - 1)
    y0, y1 = max(0, Y + max(1, int(Hd * 0.002))), min(H - 1, Y + int(Hd * 0.065))

    def lum(xx, yy):
        c = px(buf, W, xx, yy)
        return (c[0] + c[1] + c[2]) / 3.0

    fg = set()
    for y in range(y0, y1 + 1):
        for x in range(x0, x1 + 1):
            nb = []
            for d in range(8, 17):
                for xx in (x - d, x + d):
                    if x0 <= xx <= x1:
                        nb.append(lum(xx, y))
            if len(nb) < 6:
                continue
            nb.sort()
            if abs(lum(x, y) - nb[len(nb) // 2]) > 40:
                fg.add((x, y))
    if not fg:
        return None

    seen, best = set(), None
    for p in fg:
        if p in seen:
            continue
        stack, comp = [p], []
        seen.add(p)
        while stack:
            cx, cy = stack.pop()
            comp.append((cx, cy))
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                q = (cx + dx, cy + dy)
                if q in fg and q not in seen:
                    seen.add(q)
                    stack.append(q)
        xs = [c[0] for c in comp]
        ys = [c[1] for c in comp]
        w, h = max(xs) - min(xs) + 1, max(ys) - min(ys) + 1
        if not (16 <= len(comp) <= 420 and 5 <= w <= 30 and 5 <= h <= 26):
            continue
        if not (0.45 <= w / float(h) <= 2.4):
            continue
        cx, cy = (min(xs) + max(xs)) // 2, (min(ys) + max(ys)) // 2
        if best is None or cx > best[0]:
            best = (cx, cy, w, h, len(comp))
    return (best[0], best[1]) if best else None


def _glyph_present(buf, W, H, x, y, box=16):
    """候选点附近有没有「字形级」的对比（±box 的小方块里，与方块底色明显不同的像素占比）。"""
    hist = {}
    for yy in range(max(0, y - box), min(H, y + box + 1)):
        for xx in range(max(0, x - box), min(W, x + box + 1)):
            c = px(buf, W, xx, yy)
            k = (c[0] // 24, c[1] // 24, c[2] // 24)
            hist[k] = hist.get(k, 0) + 1
    if not hist:
        return False
    k = max(hist, key=hist.get)
    bgc = (k[0] * 24 + 12, k[1] * 24 + 12, k[2] * 24 + 12)
    tot = diff = 0
    for yy in range(max(0, y - box), min(H, y + box + 1)):
        for xx in range(max(0, x - box), min(W, x + box + 1)):
            c = px(buf, W, xx, yy)
            tot += 1
            if abs(c[0] - bgc[0]) + abs(c[1] - bgc[1]) + abs(c[2] - bgc[2]) > 120:
                diff += 1
    return tot > 0 and diff >= tot * 0.015


def close_point(wid, W, H, kind):
    """取这个窗口「关闭按钮」的屏幕坐标，返回 (x, y)；判定不了返回 None。

    分工（这是实测逼出来的分工，不是偷懒）：
      · **探测负责落在字形上**：probe_close_glyph 在标题栏右端找紧凑字形块，
        位置精确到像素级，且窗口挪动/缩放后依然有效。
      · **测量负责「哪个字形是关闭键」**：这是语义 —— ··· ─ ◎ ▢ ✕ 在像素上几乎等价，
        纯视觉分不出来（实测：小程序窗口里最靠右的块其实是药丸右帽，不是 ◎）。
        所以用实测比例当**语义锚点**：小程序窗口 (0.978W, 0.0435H)、
        面板这类微信自己的窗口 (0.979W, 0.0205H)。
      · 探测结果**只在锚点附近（±10px）才采信**（说明它认出的确实是那个字形）；
        太远说明认错了字形，退回锚点，但要求锚点那儿**真有字形**（对比度校验），
        没有就是不点。

    ⚠️ 为什么要这么小心：主窗口（整个微信）的 ✕ 在 (0.983W, 0.017H)，
    和面板的 (0.979W, 0.0205H) 几乎重合 —— 认不出身份时按比例点就是赌命。
    身份那两道防线在 close_window 里，先于本函数执行。
    · 不假设窗口在 (0,0)：一切按窗口自身几何算。"""
    fx, fy = R_CLOSE_PANEL if kind == "panel" else R_CLOSE_MINIAPP
    g = geo(wid)
    try:
        X, Y, Wd, Hd = int(g["X"]), int(g["Y"]), int(g["WIDTH"]), int(g["HEIGHT"])
    except (KeyError, ValueError):
        return None
    hx, hy = X + int(Wd * fx), Y + int(Hd * fy)          # 语义锚点
    if not (0 <= hx < W and 0 <= hy < H):
        return None
    buf = grab(W, H)
    cand = probe_close_glyph(buf, W, H, X, Y, Wd, Hd)
    if cand and abs(cand[0] - hx) <= 10 and abs(cand[1] - hy) <= 10:
        if _glyph_present(buf, W, H, cand[0], cand[1]):
            if (cand[0], cand[1]) != (hx, hy):
                print("[reopen] 探测到关闭字形 (%d,%d)（语义锚点 %d,%d）" % (cand[0], cand[1], hx, hy))
            return cand
    if _glyph_present(buf, W, H, hx, hy):
        print("[reopen] 探测没给出可信字形，退回语义锚点 (%d,%d)" % (hx, hy))
        return (hx, hy)
    print("[reopen] ⚠️ %s 的关闭按钮位置 (%d,%d) 附近没有字形（界面变了？）→ 不点" % (wid, hx, hy))
    return None


def close_window(wid, W=None, H=None, kind=None):
    """关掉一个微信窗口（小程序窗口 / 面板窗口）。⚠️ 必须走微信**自己的**关闭按钮，
    不能用 `xdotool windowclose`：后者销毁了 X 窗口但微信内部状态不复位，
    之后这个小程序（面板则是整个面板）就再也点不开了，只能重启微信。

    三道防线，任一不满足就不点：
      ① **身份**：WM_CLASS 含 wechat（主窗口）⇒ 那是整个微信的关闭键，拒不关闭；
      ② **结构**：标题叫「微信」的窗口（主窗口恰好也叫这个名！）只有
         `kind="panel"` 且顶部图标确实是小程序的紫色图标时才许关；
      ③ **位置**：按窗口自身几何算按钮坐标 + 那一小块里得真有字形，
         算不出/验不过 ⇒ 不点。
    点完**复核窗口是否真的消失**；没消失就**不再补第二下**
    （连点才是「多点一次把微信关掉」的来源），直接报失败交给外层。
    `kind` 只该填 "panel"（面板）或 "miniapp"（小程序窗口）。"""
    if W is None:
        W, H = size()
    if is_main_window(wid) or str(wid) == str(find_main_window()):
        print("[reopen] ⛔ %s 是微信主窗口 → 拒不关闭" % wid)
        return False
    raise_window(wid)          # 提到最前：像素判据要看得到它，点击也要鼠标底下是它
    if title_of(wid) == "微信" and not (kind == "panel" and has_miniapp_tab(wid, W, H)):
        print("[reopen] ⛔ 窗口 %s 标题是「微信」但确认不了是小程序面板 → 拒不关闭" % wid)
        return False
    pt = close_point(wid, W, H, kind)
    if not pt:
        print("[reopen] ⚠️ 拿不到 %s 的关闭按钮位置 → 不点（宁可不关）" % wid)
        return False
    x, y = pt
    print("[reopen] 点 %s 的关闭按钮 (%d,%d)" % (wid, x, y))
    if not click_expect(wid, x, y, 3.5, "（关闭按钮）"):
        return False
    if str(wid) not in dict(windows()):
        print("[reopen] 已关闭 %s" % wid)
        return True
    print("[reopen] ⚠️ 没关掉 %s（不硬杀、也不补点，避免误伤别的窗口）" % wid)
    return False


# ───────────────────── 侧边栏 / 小程序面板 ─────────────────────

def find_rail_buttons(W, H):
    """侧边栏按钮的 y 中心：图标列里"与底色不同"的行聚成带。
    返回全部按钮中心（含顶部头像与底部固定图标），按 y 升序。"""
    x0, x1 = R_RAIL_COL
    buf = grab(W, H)

    # 底色取该列的中位数（逐通道），采样即可
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
            if (abs(buf[i] - bg[0]) + abs(buf[i + 1] - bg[1]) + abs(buf[i + 2] - bg[2])) > 55:
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
    return [(b[0] + b[-1]) // 2 for b in bands if len(b) >= 8]


def looks_like_avatar(buf, W, H, yc, half=20):
    """侧边栏最上面那个按钮是账号头像（彩色照片），不是功能图标（单色线条）。
    据此判断要不要跳过它 —— 点到头像会弹出资料卡。"""
    n = 0
    for y in range(max(0, yc - half), min(H, yc + half)):
        base = y * W * 3
        for x in range(R_RAIL_COL[0], R_RAIL_COL[1], 2):
            i = base + x * 3
            r, g, b = buf[i], buf[i + 1], buf[i + 2]
            if max(r, g, b) - min(r, g, b) > 45 and max(r, g, b) > 90:
                n += 1
    return n >= 40


def has_miniapp_tab(wid, W, H):
    """这个「浏览器式」窗口里有没有**小程序的标签页**（顶部标签条里的紫色 S 图标）。

    实测（同一位置的像素计数）：小程序 蓝=44；搜一搜 红=82；视频号 蓝=0 红=0 ——
    三种窗口标题**都叫「微信」且都没有 WM_CLASS**，只看标题分不出来，所以看图标。
    ⚠️ 标签条里可能有**多个**标签（实测：已经有搜一搜标签时，点侧边栏「小程序」
       不会新开窗口，而是往同一个窗口里加一个「小程序」标签），所以这里扫整条标签条，
       而不是只看某一个固定位置。"""
    g = geo(wid)
    try:
        X, Y, Wd, Hd = int(g["X"]), int(g["Y"]), int(g["WIDTH"]), int(g["HEIGHT"])
    except (KeyError, ValueError):
        return False
    buf = grab(W, H)
    blue = 0
    for y in range(Y + int(Hd * R_TABSTRIP[2]), Y + int(Hd * R_TABSTRIP[3])):
        for x in range(X + int(Wd * R_TABSTRIP[0]), X + int(Wd * R_TABSTRIP[1])):
            if not (0 <= x < W and 0 <= y < H):
                continue
            r_, g_, b_ = px(buf, W, x, y)
            if b_ - r_ >= 40 and b_ - g_ >= 25:
                blue += 1
    return blue >= 5


def reload_panel(W, H):
    """把面板的 webview 重载一次（Ctrl+R），成功返回面板窗口 id。

    为什么需要这一步（实测踩过一整轮）：小程序面板是个 **Chromium 页面**，
    它会掉成「没有连接到网络 / 重新加载」的错误页 —— 此时窗口还在、标签条里的
    「小程序」紫色图标也在，但页面是空的、**一个小程序运行时都没有**
    （CDP 侧的表现是 `[enum] 有 wx 的上下文=[]`）。
    而这时候点侧边栏只会把它**切换关闭**，于是外部看到的就是
    「面板打不开 rc=3」→ 之前一路升级到「硬清运行时 / 重启微信」，全都没必要。

    ⚠️ 判据要落在**行为**上：`has_miniapp_tab()` 只证明标签条有那个图标，
       证明不了页面是活的。所以这里以「重载后能拿到面板窗口」为准，
       真正的可用性由后续 `[enum]` 有没有上下文来判。
    """
    cands = [wid for wid, t in windows()
             if (t or "").strip() == "微信" and str(wid) != str(find_main_window())]
    if not cands:
        print("[reopen] 没有标题为「微信」的副窗口可重载")
        return None
    for wid in cands:
        raise_window(wid)
        key("ctrl+r", 5.0)
        if has_miniapp_tab(wid, W, H):
            print("[reopen] 面板重载成功（Ctrl+R）→ 窗口 %s" % wid)
            return wid
    print("[reopen] Ctrl+R 重载后仍未确认面板")
    return None


def open_panel(W, H):
    """打开（或置顶）小程序面板：点侧边栏顶部那组里的**最后一个**按钮（=「小程序」）。

    身份不靠「标题叫微信、没有 WM_CLASS」去猜 —— 实测「视频号」「搜一搜」也是
    标题「微信」+ 无 WM_CLASS + 同样的窗口尺寸，猜就会猜错。改成**行为判据**：
    谁因为我点了这个按钮而跑到最前，谁就是面板；再看一眼它的标签条里有没有
    「小程序」的紫色图标。判不出就返回 None（宁可不做，也不对着别的窗口乱点）。
    ⚠️ 实测：已经有搜一搜/视频号这类窗口时，点侧边栏「小程序」**不会新开窗口**，
       而是往同一个窗口里加一个「小程序」标签 —— 所以判据是「标签条里有紫色图标」，
       不是「窗口是不是新出现的」。"""
    main = find_main_window()
    if not main:
        print("[reopen] 找不到微信主窗口")
        return None
    for attempt in (1, 2):
        raise_window(main)                      # 有别的窗口挡着时，侧边栏根本点不到
        buttons = find_rail_buttons(W, H)
        top = [y for y in buttons if y < H * 0.6]     # 顶部那组（排除底部固定图标）
        if len(top) < 2:
            print("[reopen] 侧边栏按钮没找全：%s" % buttons)
            return None
        y = top[-1]
        print("[reopen] 第%d次点侧边栏「小程序」按钮 y=%d" % (attempt, y))
        if not click_in(main, R_RAIL_X, y / float(H), 3.0, "（侧边栏·小程序）"):
            return None
        for _ in range(6):
            time.sleep(1.5)
            wid = top_window(W, H)
            if not wid or str(wid) == str(main):
                continue
            if win_class(wid) or title_of(wid) != "微信":
                break                            # 顶上不是微信自己的窗口，另说
            if has_miniapp_tab(wid, W, H):
                print("[reopen] 面板就位（窗口 %s）" % wid)
                return wid
            print("[reopen] 顶上这个「微信」窗口的标签条里没有「小程序」（图标不对）")
            break
    # 点侧边栏判不出来 —— 先把**面板 webview 自己**救一次（Ctrl+R），最便宜且常有奇效。
    print("[reopen] 侧边栏判不出来 → 试 Ctrl+R 重载面板 webview")
    wid = reload_panel(W, H)
    if wid:
        return wid
    print("[reopen] 没能确认小程序面板 → 不继续（不乱点）")
    return None


def panel_search_once(W, H, panel):
    """在面板里：放大镜 → 输入 → **回车**（用户确认：回车就会进搜索页）→
    搜索结果页里逐张卡片点，用窗口标题验证；**开错了就关掉继续试下一张**。
    成功返回 True。"""
    raise_window(panel)
    if str(top_window(W, H)) != str(panel):
        print("[reopen] 面板不在最前（有别的窗口挡着）→ 不输入，退出")
        return False
    if not click_in(panel, R_PANEL_MAGNIFIER[0], R_PANEL_MAGNIFIER[1], 1.5, "（面板放大镜）"):
        return False
    type_text(KEYWORD)
    print("[reopen] 面板搜索框回车")
    key("Return", 6.0)                      # 面板搜索：回车进「关键词_搜索」结果页
    time.sleep(2)
    png(W, H, "reopen_panel_search.png")

    baseline = {wid for wid, _ in windows()}
    tried = []                               # 已经试过的卡片位置，避免重复点

    for attempt in (1, 2, 3, 4):
        raise_window(panel)
        buf = grab(W, H)                     # 每次重新定位：布局会随标签栏/滚动变化
        cards = find_cards(buf, W, H)
        print("[reopen] 第%d次定位：找到 %d 张卡片 %s" % (attempt, len(cards), cards))
        progress = False
        for cx, cy in cards:
            if any(abs(cx - px_) < 30 and abs(cy - py_) < 30 for px_, py_ in tried):
                continue                     # 这张已经试过
            tried.append((cx, cy))
            progress = True
            print("[reopen] 点卡片 (%d,%d)" % (cx, cy))
            TOUCHED[0] = True
            if not click_expect(panel, cx, cy, 4.0, "（结果卡片）"):
                continue                     # 面板没在最前就不点，重新提面板
            wid, title = find_target_window()
            if wid:
                print("[reopen] 打开成功：%s" % title)
                png(W, H, "reopen_done.png")
                return True
            for wid_, title_ in windows():
                if wid_ in baseline or title_.strip() == TARGET:
                    continue
                # 只关「刚开出来的那个小程序窗口」——它的标题是小程序名。
                # 标题叫「微信」的新窗口是**微信自己的**窗口（面板/视频号/搜一搜，实测都叫这个名字），
                # 一律不动：免得把面板本身、甚至整个微信关掉。
                if title_.strip() == "微信":
                    print("[reopen] 新窗口 %s 标题也是「微信」（微信自己的窗口），不动它" % wid_)
                    continue
                print("[reopen] 开错了（%s），关掉" % title_)
                if not close_window(wid_, W, H, kind="miniapp"):
                    return False             # 关不掉说明状态异常，交给外层重来
                break                        # 关掉后重新截图定位再继续
        if not progress:
            break                            # 所有卡片都试过了
        time.sleep(3)
    return False


def find_cards(buf, W, H):
    """在搜索结果页里定位**所有**结果卡片：卡片左侧有一块方形 logo（彩色/深色）。
    返回可点中心 [(x, y)]，按阅读顺序（先上后左）。
    注意一**行里有多张卡**（实测一行 3 张），必须按列切开，不能只取最左那张。"""
    x0, x1 = int(W * 0.17), int(W * 0.75)      # 整行都扫，覆盖到第 2、3 列
    rows = {}
    for y in range(int(H * 0.08), int(H * 0.80)):
        base = y * W * 3
        n = 0
        for x in range(x0, x1, 2):
            i = base + x * 3
            r, g, b = buf[i], buf[i + 1], buf[i + 2]
            if max(r, g, b) - min(r, g, b) > 45 or max(r, g, b) < 190:
                n += 1
        if n >= 4:
            rows[y] = n
    if not rows:
        return []
    ys = sorted(rows)
    bands, cur = [], [ys[0]]
    for y in ys[1:]:
        if y - cur[-1] <= 10:
            cur.append(y)
        else:
            bands.append(cur); cur = [y]
    bands.append(cur)

    out = []
    for band in bands:
        if len(band) < 24:                     # logo 方块约 55~70px
            continue
        yc = (band[0] + band[-1]) // 2
        xs = set()
        for y in band[::3]:
            base = y * W * 3
            for x in range(x0, x1, 2):
                i = base + x * 3
                r, g, b = buf[i], buf[i + 1], buf[i + 2]
                if max(r, g, b) - min(r, g, b) > 45 or max(r, g, b) < 190:
                    xs.add(x)
        if not xs:
            continue
        xs = sorted(xs)
        # 按列切开：同一张卡的 logo 是连续区间，列间会有明显空隙
        cols, start, prev = [], xs[0], xs[0]
        for x in xs[1:]:
            if x - prev > int(W * 0.04):
                cols.append((start, prev)); start = x
            prev = x
        cols.append((start, prev))
        for lo, hi in cols:
            if hi - lo < 8:                    # 太窄，是图标碎片
                continue
            out.append((lo + int(W * 0.02), yc))
    return out


def open_via_panel(W, H):
    """主路径：小程序面板 → 搜索 → 点结果卡片（精确匹配）→ 用窗口标题验证。"""
    for round_no in (1, 2):
        print("[reopen] 第%d轮：走小程序面板搜索" % round_no)
        panel = open_panel(W, H)
        if not panel:
            return False
        if panel_search_once(W, H, panel):
            return True
    return False


# ───────────────────────── 备选：主窗口搜索 → 结果页 ─────────────────────────

def ensure_chat_tab(W, H):
    """把主窗口切回「微信」标签：**只有这个标签下左上角那个框才是全局搜索**。
    收藏标签那里框上写的是「搜索收藏」（作用域只有收藏，搜不到小程序、也没有小程序推荐）；
    通讯录等同理。侧边栏最上面第 1 个是账号头像（点了弹资料卡），第 2 个才是「微信」。"""
    main = find_main_window()
    if not main:
        return False
    raise_window(main)
    buttons = find_rail_buttons(W, H)
    if len(buttons) < 2:
        return False
    buf = grab(W, H)
    i = 1 if looks_like_avatar(buf, W, H, buttons[0]) else 0
    y = buttons[i]
    print("[reopen] 点侧边栏第%d个按钮 y=%d（切回「微信」标签）" % (i + 1, y))
    return click_in(main, R_RAIL_X, y / float(H), 2.5, "（侧边栏·微信）")


def find_search_box(W, H):
    """找主窗口左上角的**全局**搜索输入框（近白圆角框），返回 (点击x, 点击y, 框宽, 占位文字跨度)。
    不写死坐标：微信标签实测 框 x74..251 / y44..69、占位「搜索」跨度 33；
    收藏标签 框更宽（x72..291）、占位「搜索收藏」跨度 ~77 —— 用跨度就能分辨作用域。"""
    buf = grab(W, H)
    x_lo, x_hi = int(W * R_SEARCHBOX_SCAN[2]), int(W * R_SEARCHBOX_SCAN[3])
    rows = {}
    for y in range(int(H * R_SEARCHBOX_SCAN[0]), int(H * R_SEARCHBOX_SCAN[1])):
        xs = [x for x in range(x_lo, x_hi) if min(px(buf, W, x, y)) >= 248]
        if len(xs) >= 80:
            rows[y] = (xs[0], xs[-1])
    if not rows:
        return None
    ys = sorted(rows)
    bands, cur = [], [ys[0]]
    for y in ys[1:]:
        if y - cur[-1] <= 3:
            cur.append(y)
        else:
            bands.append(cur); cur = [y]
    bands.append(cur)
    for band in bands:
        if not (14 <= len(band) <= 40):        # 输入框高约 26px
            continue
        ymid = (band[0] + band[-1]) // 2
        left, right = rows[ymid]
        if not (100 <= right - left <= 400):
            continue
        # 占位文字（灰）：只量框内靠左那一段，右端要避开框右边的 ⊕ 按钮
        gx = [x for y in band for x in range(left + 16, right - 4)
              if max(px(buf, W, x, y)) <= 215 and (max(px(buf, W, x, y)) - min(px(buf, W, x, y))) <= 14]
        span = (max(gx) - min(gx)) if gx else -1
        return (left + 32, ymid, right - left, span)
    return None


def do_search(W, H, box):
    """主窗口搜索框 → 输入 → 点下拉里的「搜索网络结果」→ 落到搜一搜结果页。
    ⚠️ 打字是发给**当前有键盘焦点的窗口**的，所以输入前必须确认主窗口在最前
       （否则字会落到别的窗口，甚至在聊天输入框里回车就直接发出去）；
       框没确认之前也一个字都不输入。"""
    main = find_main_window()
    bx, by, bw, bspan = box
    print("[reopen] 搜索框 (%d,%d) 宽=%d 占位跨度=%d 输入「%s」" % (bx, by, bw, bspan, KEYWORD))
    raise_window(main)
    if str(top_window(W, H)) != str(main):
        print("[reopen] 主窗口不在最前（有别的窗口挡着）→ 不输入，退出这条路径")
        return False
    if not click_expect(main, bx, by, 0.6, "（全局搜索框）"):
        return False
    key("ctrl+a"); key("Delete")
    type_text(KEYWORD)
    time.sleep(2.0)
    buf = grab(W, H)
    png(W, H, "reopen_search_dropdown.png")
    ny = find_netsearch_row(buf, W, H)
    if not ny:
        # 定位不到就**什么都不按**退出（老代码这里按 Down+回车，
        # 万一焦点还在聊天输入框，那一回车就是把关键词发出去）
        print("[reopen] 没定位到「搜索网络结果」行 → 不按回车，直接退出这条路径")
        return False
    print("[reopen] 点「搜索网络结果」y=%d" % ny)
    click(int(W * 0.12), ny, 4.0)
    png(W, H, "reopen_search_page.png")
    return True


def find_netsearch_row(buf, W, H):
    """找搜索下拉里「搜索网络结果」那一行的 y。
    判据用**图标宽度**：它是窄的 ✳（只占 x≈89..98，约 9px），
    而小程序行的 logo 占 x≈76..98（约 22px）—— 颜色不可靠（logo 里也有浅粉像素），
    宽度可靠。（🔍 建议行的放大镜是灰色，不参与。）"""
    x0, x1 = int(W * 0.054), int(W * 0.086)      # 69..110
    rows = {}
    for y in range(int(H * 0.05), int(H * 0.65)):
        base = y * W * 3
        xs = []
        for x in range(x0, x1):
            i = base + x * 3
            r, g, b = buf[i], buf[i + 1], buf[i + 2]
            if max(r, g, b) - min(r, g, b) > 45:          # 彩色像素（排除灰色图标/文字）
                xs.append(x)
        if xs:
            rows[y] = (min(xs), max(xs))
    if not rows:
        return None
    ys = sorted(rows)
    bands, cur = [], [ys[0]]
    for y in ys[1:]:
        if y - cur[-1] <= 6:
            cur.append(y)
        else:
            bands.append(cur); cur = [y]
    bands.append(cur)
    thin = max(int(W * 0.012), 12)                        # 窄于 ~15px 视为 ✳
    for band in bands:
        if len(band) < 8:                                 # 太薄，不是图标
            continue
        lo = min(rows[y][0] for y in band)
        hi = max(rows[y][1] for y in band)
        if hi - lo <= thin:
            return (band[0] + band[-1]) // 2
    return None


def find_rows(buf, W, H):
    """找结果行的 y 中心：结果行左侧有一块方形 logo（高饱和或深色），
    按 y 扫描 logo 列，聚成连续带即为一行。返回行中心 y 列表。"""
    x0, x1 = int(W * 0.17), int(W * 0.24)
    scores = {}
    for y in range(int(H * 0.10), int(H * 0.97)):
        base = y * W * 3
        n = 0
        for x in range(x0, x1, 2):
            i = base + x * 3
            r, g, b = buf[i], buf[i + 1], buf[i + 2]
            if max(r, g, b) - min(r, g, b) > 45 or max(r, g, b) < 200:   # 彩色 或 深色
                n += 1
        if n >= 4:
            scores[y] = n
    if not scores:
        return []
    ys = sorted(scores)
    bands, cur = [], [ys[0]]
    for y in ys[1:]:
        if y - cur[-1] <= 8:
            cur.append(y)
        else:
            bands.append(cur); cur = [y]
    bands.append(cur)
    # logo 方块大约 40~60px 高；太薄的带是文字/分隔线，丢掉
    return [(b[0] + b[-1]) // 2 for b in bands if len(b) >= 24]


def open_via_search(W, H):
    """备选：主窗口搜索 → 搜一搜结果页逐行点。
    ⚠️ 默认**不跑**（main() 里要 WXSIGN_CHAT_SEARCH=1 才走这条）：这版微信上实测
    输入框点不中、结果页也点不开，白花时间；更要紧的是，输入框没确认就敲字的话，
    关键词会落进聊天输入框、回车就直接发出去。"""
    for round_no in (1, 2):
        if not ensure_chat_tab(W, H):          # 非「微信」标签下那个框是局部搜索
            return False
        box = find_search_box(W, H)
        if not box:
            print("[reopen] 没找到全局搜索框（可能不在「微信」标签）→ 不输入、不回车")
            return False
        if box[3] > PLACEHOLDER_MAX:
            print("[reopen] 搜索框占位文字跨度 %d（像是「搜索收藏」这种局部搜索）→ 不用它" % box[3])
            return False
        if not do_search(W, H, box):
            return False
        rows = find_rows(grab(W, H), W, H)
        print("[reopen] 第%d轮：结果页找到 %d 行：%s" % (round_no, len(rows), rows))
        if not rows:
            continue
        main = find_main_window()
        baseline = {wid for wid, _ in windows()}
        for i, y in enumerate(rows[:6], 1):
            print("[reopen] 第%d轮 试第 %d 行 y=%d" % (round_no, i, y))
            TOUCHED[0] = True
            click(int(W * 0.28), y, 3.0)
            wid, title = find_target_window()
            if wid:
                print("[reopen] 打开成功：%s" % title)
                png(W, H, "reopen_done.png")
                return True
            for wid_, title_ in windows():        # 开错了就关掉，别越堆越多
                if wid_ not in baseline and TARGET not in title_ and title_.strip() != "微信":
                    print("[reopen] 开错了（%s），关掉" % title_)
                    close_window(wid_, W, H, kind="miniapp")
            raise_window(main)                    # 页面可能被带走了，拉回主窗口
    print("[reopen] 两轮都扫完还没打开")
    return False


# ───────────────────────── 兜底：甄选 → banner 跳转 ─────────────────────────

def do_jump_from_zx_home(W, H):
    """甄选首页：点签到 banner，等跳转弹窗，点允许"""
    spots = [(0.191, 0.806), (0.50, 0.517), (0.25, 0.517)]
    for i, (fx, fy) in enumerate(spots, 1):
        x, y = int(W * fx), int(H * fy)
        print("[reopen] 第%d次点签到 banner (%d,%d)" % (i, x, y))
        click(x, y)
        for _ in range(3):
            time.sleep(2)
            g = find_green(grab(W, H), W, H)
            if g:
                gx, gy = (g[0] + g[2]) // 2, (g[1] + g[3]) // 2
                print("[reopen] 点「允许」(%d,%d)" % (gx, gy))
                click(gx, gy)
                time.sleep(8)
                png(W, H, "reopen_done.png")
                return True
    p = png(W, H, "reopen_nodialog.png")
    print("[reopen] 三次都没等到跳转弹窗 → 截图 %s" % p)
    return False


def red_ratio(buf, W, H):
    """画面中段被红色 banner 占的比重（甄选首页特征）"""
    hit = tot = 0
    for y in range(int(H * 0.25), int(H * 0.75), 6):
        base = y * W * 3
        for x in range(int(W * 0.05), int(W * 0.95), 6):
            i = base + x * 3
            r, g, b = buf[i], buf[i + 1], buf[i + 2]
            tot += 1
            if r > 175 and g < 130 and b < 130:
                hit += 1
    return hit / max(1, tot)


def find_green(buf, W, H):
    """微信绿按钮的 bbox"""
    ys = {}
    for y in range(0, H, 2):
        base = y * W * 3
        n = 0
        for x in range(0, W, 2):
            i = base + x * 3
            c = (buf[i], buf[i + 1], buf[i + 2])
            if c[1] >= 140 and c[1] - c[0] >= 40 and c[1] - c[2] >= 15:
                n += 1
        if n > 3:
            ys[y] = n
    if not ys:
        return None
    peak = max(ys, key=ys.get)
    band = [y for y in ys if abs(y - peak) <= 40]
    y0, y1 = min(band), max(band)
    xs = []
    for y in range(y0, y1 + 1, 2):
        base = y * W * 3
        for x in range(0, W, 2):
            i = base + x * 3
            c = (buf[i], buf[i + 1], buf[i + 2])
            if c[1] >= 140 and c[1] - c[0] >= 40 and c[1] - c[2] >= 15:
                xs.append(x)
    return (min(xs), y0, max(xs), y1) if xs else None


def find_logo_row(buf, W, H):
    """下拉里小程序那一行的 y：找一块高饱和彩色 logo，取最上面那条连续带"""
    x0, x1 = int(W * R_LOGO_AREA[0]), int(W * R_LOGO_AREA[1])
    y0, y1 = int(H * R_LOGO_AREA[2]), int(H * R_LOGO_AREA[3])
    hits = {}
    for y in range(y0, y1):
        base = y * W * 3
        n = 0
        for x in range(x0, x1, 2):
            i = base + x * 3
            r, g, b = buf[i], buf[i + 1], buf[i + 2]
            if max(r, g, b) - min(r, g, b) > 60 and max(r, g, b) > 90:
                n += 1
        if n >= 3:
            hits[y] = n
    if not hits:
        return None
    ys = sorted(hits)
    band = [ys[0]]
    for y in ys[1:]:
        if y - band[-1] <= 6:
            band.append(y)
        else:
            break
    if len(band) < 8:
        return None
    return (band[0] + band[-1]) // 2


def open_via_zhenxuan(W, H):
    """旧路线：搜兜底关键词 → 点下拉里的小程序行 → 首页 → banner 跳转。
    没设 WXSIGN_FALLBACK_KEYWORD 就直接放弃（这条路本来只是给某个特定品牌准备的老路）。
    注意：同样先切「微信」标签、确认搜到的是全局搜索框、确认主窗口在最前（打字发给焦点窗口），
    否则一个字都不输入。"""
    if not FALLBACK_KEYWORD:
        print("[reopen] 未设 WXSIGN_FALLBACK_KEYWORD → 跳过兜底路线")
        return False
    if not ensure_chat_tab(W, H):
        return False
    box = find_search_box(W, H)
    if not box or box[3] > PLACEHOLDER_MAX:
        print("[reopen] 没找到全局搜索框 → 不输入（免得落进聊天输入框）")
        return False
    main = find_main_window()
    bx, by = box[0], box[1]
    print("[reopen] 兜底：点搜索框 (%d,%d) 输入「%s」" % (bx, by, FALLBACK_KEYWORD))
    raise_window(main)
    if str(top_window(W, H)) != str(main):
        print("[reopen] 主窗口不在最前（有别的窗口挡着）→ 不输入")
        return False
    if not click_expect(main, bx, by, 1.2, "（全局搜索框）"):
        return False
    key("ctrl+a"); key("Delete")
    type_text(FALLBACK_KEYWORD)
    time.sleep(4)
    buf = grab(W, H)
    png(W, H, "reopen_search.png")
    ry = find_logo_row(buf, W, H)
    if not ry:
        print("[reopen] 下拉里没找到小程序行")
        return False
    click(int(W * R_ROW_CLICK_X), ry)
    time.sleep(8)
    png(W, H, "reopen_after_row.png")
    if red_ratio(grab(W, H), W, H) < 0.35:
        print("[reopen] 点完那行后没看到甄选首页")
        return False
    return do_jump_from_zx_home(W, H)


# ───────────────────────────── main ─────────────────────────────

def find_panel_for_close(W, H):
    """找一个可以关掉的「小程序面板」窗口，找不到返回 None。

    判据是**三层**（因为这三种窗口标题都叫「微信」、都没有 WM_CLASS，只看标题会认错）：
      ① 标题 = 微信 且 没有 WM_CLASS（视频号/搜一搜/朋友圈 也是这个长相）；
      ② **提到最前**之后（只 raise，不点任何东西 —— 免得为了省内存反而把面板打开、
         白白拉起一次小程序运行时），它确实是当前最上层；
      ③ 顶部那一栏的图标是**小程序**的紫色图标。
    认不出就不动 —— 宁可少关一个窗口，也不把视频号/搜一搜当面板关掉。"""
    cands = [w for w, t in windows() if t.strip() == "微信" and not win_class(w)]
    top = top_window(W, H)
    if top in cands:
        cands.remove(top)
        cands.insert(0, top)
    for c in cands:
        raise_window(str(c))
        if str(top_window(W, H)) != str(c):
            continue
        if has_miniapp_tab(c, W, H):
            return c
        print("[reopen] 窗口 %s 不是小程序面板（顶部图标不对）→ 不动它" % c)
    return None


def do_close(W, H):
    """关掉小程序与面板，释放运行时内存（下次签到会自动重开）。
    都走微信自己的关闭按钮 —— 绝不用 xdotool windowclose（会把微信状态搞坏）；
    主窗口一概不碰（close_window 里还有身份 / 结构 / 像素三道防线）。"""
    names = []
    wid, t = find_target_window()
    if wid and close_window(wid, W, H, kind="miniapp"):
        names.append(t)
    panel = find_panel_for_close(W, H)
    if panel and close_window(panel, W, H, kind="panel"):
        names.append("小程序面板")
    print("[reopen] 已关闭：%s" % ("、".join(names) if names else "（本来就没开着）"))


def main():
    W, H = size()
    if (W, H) != (WANT_W, WANT_H):
        print("[reopen] 屏幕 %dx%d → 钉到 %dx%d（坐标按此校准）" % (W, H, WANT_W, WANT_H))
        run("DISPLAY=%s xrandr -s %dx%d" % (DISPLAY, WANT_W, WANT_H))
        time.sleep(2)
        W, H = size()

    if "--close" in sys.argv:
        do_close(W, H)
        return 0

    if "--check" in sys.argv:
        wid, t = find_target_window()
        print("[reopen] 目标窗口：%s" % (t or "（未打开）"))
        return 0 if wid else 3

    wid, t = find_target_window()
    if wid:
        print("[reopen] 已经打开：%s" % t)
        return 0

    print("[reopen] 主路径：小程序面板 → 搜索 → 点结果卡片")
    if open_via_panel(W, H):
        return 0

    if os.environ.get("WXSIGN_CHAT_SEARCH") == "1" or os.environ.get("LAKEKE_CHAT_SEARCH") == "1":
        print("[reopen] 面板路径失败，改走主窗口搜索（WXSIGN_CHAT_SEARCH=1）")
        if open_via_search(W, H):
            return 0

    print("[reopen] 改走甄选兜底")
    if open_via_zhenxuan(W, H):
        return 0

    p = png(W, H, "reopen_fail.png")
    if LOOSE and TOUCHED[0]:
        print("[reopen] 点过候选卡片但没等到目标窗口（界面形态可能变了）→ 返回 4，"
              "请用 `node cdp_eval.js --probe` 按 appId 复核，截图 %s" % p)
        return 4
    print("[reopen] 没能打开 → 截图 %s（需人工打开一次）" % p)
    return 3


if __name__ == "__main__":
    sys.exit(main())
