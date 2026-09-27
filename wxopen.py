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
#   · 小程序窗口：自绘标题栏，最右的 ◎ 在 (0.93W, 0.045H)
#     ⚠️ 2026-09-26 修正：旧值 (0.978W, 0.0435H) 是按**更宽的窗口**实测的，
#     小程序窗口实测只有 410x776（◎ 胶囊中心在 x≈0.929W），0.978×410=401
#     会贴到窗口右缘 20px 开外 → 落在 ◎ 热区边缘甚至之外，
#     这就是「close_window 经常点不中 → 退化到 force_close_miniapp 的
#     xdotool windowclose → WeChatAppEx 状态机损坏 → 之后所有小程序打不开」
#     这条断链的起点。锚点修正后胶囊一击即中（已手工验证两次）。
#   · 面板这类微信自己的窗口：顶部 chrome 的 ✕ 在 (0.979W, 0.0205H)
#   · 主窗口的 ✕ 在 (0.983W, 0.017H) —— 和面板几乎重合，这就是危险所在：
#     所以主窗口绝不作为关闭目标，标题叫「微信」的窗口还必须先通过面板芯片校验。
# 旧写法是「盲点 (0.978W,0.041H)，不行再盲点 (0.978W,0.020H)」—— 后面那个正好压在
# 主窗口的红底白✕上，多试一次就是关掉整个微信，已废弃。
R_CLOSE_MINIAPP = (0.93, 0.045)
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


def _frame_swallows_first_click(x, y):
    """这个点上的窗口是不是「整屏尺寸的 openbox frame」（会吃掉第一次 click）。

    判据：`win_under()` 拿到的窗口虽然**无名**，但它的尺寸 **= 整个屏幕**，
    且它不是我们列表里的具名窗口（微信 client 有标题「微信」/小程序有标题）。
    满足就是「frame 盖住了 client」的情形 —— 此时第一次 click 只用来把焦点交给 frame。

    ⚠️ 为什么不无脑双击：某些位置的点击**不能重复** —— 实测踩过的
      「退出登录？确定/取消」确认框就在侧边栏左下角附近，连点两下可能点到「确定」
      直接掉登录。所以只在**确知**第一次会被吞时才补第二下。
    """
    uw = win_under(x, y)
    if uw is None:
        return False
    try:
        g = geo(uw)
        Wd, Hd = int(g["WIDTH"]), int(g["HEIGHT"])
    except (KeyError, TypeError, ValueError):
        return False
    sw, sh = size()
    # ⚠️ 2026-09-26 二轮定案：**保持严格判据 `Wd >= sw`**（= 恒 False、单击模式）。
    # 曾放宽为 `>= sw-4`，结果主窗口 client（实测 1276~1279 宽）全部命中 →
    # 每一次点击都变双击：搜索框被双击 = 下拉开了又关、条目被双击 = 开错窗口，
    # 比「frame 偶发吞掉第一次 click」危害大得多。而单击模式正是
    # 辣可可/过桥缘成功路径实测使用的行为（18:42、19:35 两次验证）。
    # frame 真吞点击的场景（刚最大化后第一次交互）由 close_anon_modal /
    # force_close_miniapp 里各自的「补第二下」就地处理，不做全局双击。
    return Wd >= sw and Hd >= sh


def click(x, y, wait=0.7):
    """在屏幕绝对坐标 (x,y) 点一下。

    ⚠️ 2026-09-26 实测定案：微信窗口带 openbox frame，**frame 若是整屏尺寸**
    （窗口被最大化成 1280x1024 时就是这样）会**吃掉第一次 click** ——
    第一次只用于把焦点交给 frame，第二次才真正落到 client。
    症状极具迷惑性：`mousemove` 的 hover 高亮**正常**（EnterNotify 走得到），
    但 click 毫无反应（实测：点击前后面板 0 像素变化）；连点两次立刻生效。

    窗口**非全屏**（如刚登录时的 880x640）时 frame 与 client 同尺寸同位置重合，
    一次就够 —— 所以**按需补第二下**（`_frame_swallows_first_click` 判定），
    不无脑双击，避免在「退出登录？」这类确认框上连点两下点到「确定」。
    """
    run("DISPLAY=%s xdotool mousemove %d %d; sleep 0.35" % (DISPLAY, int(x), int(y)))
    xdotool_click = "DISPLAY=%s xdotool click 1" % DISPLAY
    run(xdotool_click)
    if _frame_swallows_first_click(x, y):
        time.sleep(0.20)
        run(xdotool_click)
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
    """微信**主窗口** —— WM_CLASS 含 wechat **且标题是「微信」**。
    它的标题栏最右是**整个微信**的关闭键，任何情况下都不许点。

    ⚠️ 2026-09-27 治本：旧实现只看 `WM_CLASS 含 wechat`，而**朋友圈窗口的 WM_CLASS 同样是
    wechat**（实测 `WM_CLASS(STRING) = "wechat", "wechat"`）→ 朋友圈被当成主窗口**保护**起来：
      · `wxclean.leftover()` 认为它是主窗口 → 永不清理；
      · `close_window()` 的防线 ① 认为它是主窗口 → 拒绝关闭。
    结果朋友圈窗口一旦被误开就只能人工关（实测：批跑中途冒出朋友圈窗口，
    接着 liuyishou 的面板打不开、`[ensure] rc=3` 直接放弃）。
    真主窗口的标题恒为「微信」；朋友圈/视频号/搜一搜的标题是它们自己的名字，
    所以「WM_CLASS + 标题」两条一起看才能把它们区分开。
    """
    return "wechat" in win_class(wid).lower() and title_of(wid).strip() == "微信"


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


def ensure_main_fullscreen(W, H):
    """⛔ **已弃用，别再调用**（2026-09-26 实测定案）—— 保留只为兼容旧调用点。

    它当初是为了绕开「侧边栏扫描用屏幕绝对坐标、窗口不全屏就全空」而写的补丁。
    但**那是治错了**：

    ① 真相：`find_rail_buttons()` 的 `R_RAIL_COL=(14,52)` 是**屏幕绝对坐标**，
       而微信主窗口**不一定全屏**（刚登录/刚重启时是 `880x640@(200,192)`）。
       全屏补丁把窗口拉大，只是**碰巧**让 x=14..52 落回窗口内 —— 坐标算错才是病根。

    ② 代价：实测把窗口拉成 1280x1024 之后，**openbox 给窗口套的 frame 变成整屏尺寸**，
       `getmouselocation` 在屏幕任意位置都返回那个 frame，`xdotool click` 的
       **首次点击会被 frame 吃掉**（要点两次才生效）——
       等于把一个「侧边栏扫不到」的 bug 换成一个**更难查的「点不动」** bug。

    正解 = `find_rail_buttons()` 按主窗口实际几何扫描（`_rail_scan_geom`），
    窗口多大都不影响定位。本函数**保留定义但不再被调用**，留作这段历史的记录。
    """
    main = find_main_window()
    if not main:
        return False
    g = geo(main)
    try:
        X, Y, Wd, Hd = int(g["X"]), int(g["Y"]), int(g["WIDTH"]), int(g["HEIGHT"])
    except (KeyError, ValueError):
        return False
    if X == 0 and Y == 0 and Wd == W and Hd == H:
        return True
    run("DISPLAY=%s xdotool windowmove %s 0 0" % (DISPLAY, main))
    run("DISPLAY=%s xdotool windowsize %s %d %d" % (DISPLAY, main, W, H))
    time.sleep(1.0)
    print("[reopen] ⚠️ ensure_main_fullscreen 已弃用却被调用：主窗口 %dx%d@(%d,%d) → 拉成 %dx%d@(0,0)"
          % (Wd, Hd, X, Y, W, H))
    return True


def raise_window(wid):
    """把窗口提到最前。windowactivate 走 EWMH（要窗口管理器配合），windowraise 走
    XRaiseWindow，两个都发一遍最稳 —— 有别的窗口挡着时，点侧边栏/面板都会落到别人身上
    （实测踩过：面板在前台时，点侧边栏其实点到了面板窗口上）。

    ⚠️ `windowactivate` **必须带 `--sync`，而且必须校验结果**（2026-09-25 踩到）：
    不带 `--sync` 是「发完就返回」，Openbox 可能还没完成聚焦；实测日志里出现**一整片**
      `⛔ (x,y) 底下是窗口 <主窗口>，不是预期目标 <面板> → 不点`
    —— `click_expect` 那道理所当然的安全阀把**每一次点击**都拒了，
    外部症状是「卡片定位坏了 / 找到了 0 张卡片」，真实原因是**窗口根本没抬起来**。
    手工验证：加 `--sync` 之后 `top_window()` 立刻从主窗口变回面板。
    所以这里改成「反复抬 + 每次校验」，抬不起来就如实返回 False（让调用方别继续瞎点）。
    """
    for _ in range(3):
        run("DISPLAY=%s xdotool windowactivate --sync %s" % (DISPLAY, wid))
        run("DISPLAY=%s xdotool windowraise %s" % (DISPLAY, wid))
        time.sleep(0.6)
        top = top_window(WANT_W, WANT_H)
        if top is None:
            # ⚠️ **认不出顶上是谁 ≠ 没抬起来**。
            #    `top_window()` 依赖「那个窗口在我们按标题筛的列表里」，而
            #    **硬清运行时之后微信重建的窗口是无名的**（面板/运行时的窗口都叫不出名字），
            #    `win_under()` 于是返回 None。若把它当失败，会连锁成
            #    「主窗口抬不起来 → 不点侧边栏 → 面板永远打不开」的**假死**
            #    （实测踩到：硬清一次之后 open_panel 直接放弃）。
            #    所以这里**乐观放行** —— 真被别的窗口挡着时，后面 `click_expect`
            #    那道理所当然的安全阀仍会拦下来（它按点问「鼠标底下是不是目标」）。
            return True
        if str(top) == str(wid):
            return True
    print("[reopen] ⚠️ 抬不起窗口 %s（顶上始终是 %s）"
          % (wid, top_window(WANT_W, WANT_H)))
    return False


def win_under(x, y):
    """把鼠标移到 (x,y)，返回该点最上层窗口（在我们窗口列表里的那个）id，认不出返回 None。
    getmouselocation 报的常常是窗口管理器（Openbox）的**框架**窗口，客户窗口是它的子窗口，
    所以要再下一层找；没被 reparent 时它直接就报客户窗口。
    用途：**每次点击前先问一句「我鼠标底下是谁」**，不是预期目标就不点。

    ⚠️ 还要**往上走**：模态框（如「退出登录？」）是**无名、无 WM_CLASS 的独立顶层窗口**，
    它盖住屏幕中心时，不加这一步就会返回 None —— 于是 top_window() 认不出任何东西，
    连带「遣散弹窗」的判断也短路（实测：正因为这个，有模态框时反而遣散不了）。
    """
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
    # 往下：框架窗口 → 客户窗口
    for line in run("DISPLAY=%s xwininfo -id %s -tree" % (DISPLAY, wid)).splitlines()[1:]:
        m = re.match(r"\s+(0x[0-9a-fA-F]+)\b", line)
        if m:
            dec = str(int(m.group(1), 16))
            if dec in known:
                return dec
    # 往上：独立顶层窗口（模态框）→ 它的父/祖先里找我们认识的窗口
    seen = set()
    cur = wid
    for _ in range(6):
        if cur in seen:
            break
        seen.add(cur)
        parent = ""
        for line in run("DISPLAY=%s xwininfo -id %s" % (DISPLAY, cur)).splitlines():
            m = re.match(r"\s*Parent window id:\s*(0x[0-9a-fA-F]+)", line)
            if m:
                parent = m.group(1)
                break
        if not parent or parent == "0x0":
            break
        if str(int(parent, 16)) in known:
            return str(int(parent, 16))
        cur = parent
    return None


def top_window(W=None, H=None):
    """当前最上层的窗口 id（在屏幕中心探一下）"""
    if W is None:
        W, H = size()
    return win_under(int(W * 0.5), int(H * 0.5))


def click_expect(wid, x, y, wait=0.7, what=""):
    """点之前先确认「鼠标底下就是 wid」，不是就不点。

    ⚠️ `win_under()` 返回 None 时**放行**（不是拒绝）—— 它返回 None 有两种情况：
      ① 鼠标底下是个**无名的**顶层窗口（硬清运行时之后微信重建的窗口就是这样，
         面板/运行时都叫不出名字），这时它必然「认不出」；
      ② 窗口管理器结构异常。
    这两种情况下 `wid` 很可能**确实是**最上层（我们刚 raise 过），
    把 None 当失败会让所有点击被拒 → 表现为「面板打不开 / 卡片定位坏了」的**假死**
    （实测：硬清一次之后整个流程卡死，日志一片 `⛔ … 不是预期目标`）。
    安全阀仍然有效：只要**认出了**是别的窗口，就照样拒绝。
    """
    under = win_under(x, y)
    if under is not None and str(under) != str(wid):
        print("[reopen] ⛔ (%d,%d) 底下是窗口 %s，不是预期目标 %s → 不点%s"
              % (x, y, under, wid, (" " + what) if what else ""))
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


def click_rail(wid, y_abs, wait=2.5, what="（侧边栏）"):
    """点侧边栏按钮 —— `y_abs` 是**屏幕绝对 y**（`find_rail_buttons()` 返回的就是绝对值）。

    x 仍按窗口宽取比例（侧边栏竖条贴着窗口左边），**y 直接用绝对值**。

    ⚠️ 2026-09-27 治本：旧写法是
        `click_in(main, R_RAIL_X, y / float(H))`   # H = **屏幕**高
    把**已经是绝对的 y** 又除以**屏幕高**当比例，而 `click_in` 内部再乘**窗口高** ——
    等于做了两次换算。窗口 ≈ 全屏（1276x1024）时两者数值接近、蒙对了；
    窗口变 759x674 后：114 → `674 × (114/1024)` = **75**，正落在**微信头像**上
    （头像占 y 42..82）。实测现象是「先点击我的微信头像两遍」（第一遍悬停出
    资料卡挡住后续操作）就是这个。侧边栏第 2 个按钮才是「微信」标签，
    点错到头像上就永远切不回去 → 后面所有坐标全歪。
    """
    g = geo(wid)
    try:
        X, Wd = int(g["X"]), int(g["WIDTH"])
    except (KeyError, TypeError, ValueError):
        return False
    return click_expect(wid, X + int(Wd * R_RAIL_X), y_abs, wait, what)


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


def force_close_miniapp(wid, title=""):
    """硬关一个**小程序窗口** —— 点它自己的 ◎ 胶囊（正常关闭流程），**不用 windowclose**。

    ⚠️ 2026-09-26 实测定案：`xdotool windowclose` 对 WeChatAppEx 是**累积性损坏** ——
    X 窗口销毁了，但微信内部的小程序状态机不复位（运行时认为小程序「半开着」），
    损坏一两次看不出来，连着签几个品牌后彻底僵死：之后**任何**小程序都打不开
    （hook 日志 20 分钟没有任何 miniapp connect 尝试，微信不再拉起新实例），
    只能 pkill WeChatAppEx 全家再重 attach hook —— 这就是「每个品牌前都要杀运行时」
    的根因。而点 ◎ 胶囊走的是微信自己的退出流程，运行时保持健康空闲，
    实测关完立刻能连贯打开下一个（已验证：过桥缘 → 辣可可 连续两次成功）。

      · 小程序窗口是 WeChatAppEx 的**独立 X 顶层窗口**（标题=小程序名），
        `windowactivate --sync` 后盲点 ◎（R_CLOSE_MINIAPP 锚点）两下即可
        （第一下可能只把焦点交给 openbox frame，见 click() 的说明）。
      · **绝不能**对主窗口或面板用 —— 那会破坏微信内部状态（详见 close_window 的告诫）。
        所以这里再加一道守卫：标题为「微信」或等于主窗口 id 的，一律拒关。
      · 只在小程序窗口保留「微信自己的关闭按钮点不中」之后作为兜底，正常路径仍优先
        走 close_window。windowclose 已从本函数**移除**。
    """
    if not wid or not title:
        return False
    if title.strip() == "微信" or is_main_window(wid) or str(wid) == str(find_main_window()):
        print("[reopen] ⛔ %s（%s）不是小程序窗口 → 不用硬关" % (wid, title))
        return False
    print("[reopen] 兜底关小程序窗口 %s（%s）：盲点 ◎ 胶囊" % (wid, title))
    run("DISPLAY=%s xdotool windowactivate --sync %s" % (DISPLAY, wid))
    time.sleep(0.5)
    g = geo(wid)
    try:
        X, Y, Wd, Hd = int(g["X"]), int(g["Y"]), int(g["WIDTH"]), int(g["HEIGHT"])
    except (KeyError, TypeError, ValueError):
        return False
    cx, cy = X + int(Wd * R_CLOSE_MINIAPP[0]), Y + int(Hd * R_CLOSE_MINIAPP[1])
    for i in (1, 2):                     # 第一下可能只交焦点给 frame，补第二下
        run("DISPLAY=%s xdotool mousemove %d %d; sleep 0.3" % (DISPLAY, cx, cy))
        run("DISPLAY=%s xdotool click 1" % DISPLAY)
        time.sleep(1.2 if i == 1 else 0.8)
        if str(wid) not in dict(windows()):
            print("[reopen] 已正常关闭 %s（第 %d 下）" % (wid, i))
            return True
    print("[reopen] ⚠️ 点 ◎ 也没关掉 %s —— 不再用 windowclose（会损坏运行时）" % wid)
    return False


# ───────────────────── 侧边栏 / 小程序面板 ─────────────────────

def _rail_scan_geom(W, H):
    """侧边栏扫描区域 `(x0, x1, y0, y1)` —— **按主窗口实际几何算**，不用屏幕绝对坐标。

    侧边栏是主窗口最左边一条（图标列大约在窗口内 x=14..52）。
    主窗口可能不全屏（实测刚登录时是 `880x640@(200,192)`），所以必须：
      ① 取主窗口的 `X/Y/WIDTH/HEIGHT`；
      ② 扫 `X+14 .. X+52` 这段（即窗口内左起 14~52 像素）；
      ③ y 限定在窗口内，避免扫到窗口外的桌面。
    主窗口认不出来时退回旧的屏幕绝对坐标（`0,0` 全屏态下与旧行为完全一致）。
    """
    main = find_main_window()
    g = geo(main) if main else None
    try:
        X, Y = int(g["X"]), int(g["Y"])
        Wd, Hd = int(g["WIDTH"]), int(g["HEIGHT"])
    except (TypeError, KeyError, ValueError):
        return R_RAIL_COL[0], R_RAIL_COL[1], 0, H
    return X + R_RAIL_COL[0], X + R_RAIL_COL[1], Y, Y + Hd


def find_rail_buttons(W, H):
    """侧边栏按钮的 y 中心：图标列里"与底色不同"的行聚成带。
    返回全部按钮中心（含顶部头像与底部固定图标），按 y 升序。

    ⚠️ 2026-09-26 实测定案：**不能用屏幕绝对坐标 `R_RAIL_COL=(14,52)` 扫**。
    微信主窗口**不一定是全屏** —— 刚登录/刚重启时它是 `880x640@(200,192)`，
    此时屏幕 x=14..52 落在**窗口左边的黑桌面**上，逐通道取中位数得到 `bg=[0,0,0]`，
    于是"与底色差 >55"的行一个都没有 → 返回 `[]` → 上层报「侧边栏按钮没找全」→ rc=3。
    现象极像「面板坏了 / 得重启微信」，其实只是**窗口没铺满屏幕**。

    旧解法是 `ensure_main_fullscreen()` 把窗口强拉成 1280x1024 —— **那是错的**：
    实测全屏后 openbox 给窗口套的 frame 会变成整屏尺寸，`xdotool click` 首次点击
    被 frame 吃掉（要连点两次才生效），把一个「算错坐标」的 bug 换成了「点不动」的 bug。
    正解 = **按主窗口实际几何扫描**（见 `_rail_scan_geom`），窗口多大就扫多大。
    """
    x0, x1, y_off, y_end = _rail_scan_geom(W, H)
    buf = grab(W, H)
    x0 = max(0, min(x0, W - 1))
    x1 = max(x0 + 1, min(x1, W))
    y_off = max(0, min(y_off, H - 1))
    y_end = max(y_off + 1, min(y_end, H))

    # 底色取该列的中位数（逐通道），采样即可
    samples = [[], [], []]
    for y in range(y_off, y_end, 3):
        base = y * W * 3
        for x in range(x0, x1, 3):
            i = base + x * 3
            for c in range(3):
                samples[c].append(buf[i + c])
    bg = [sorted(s)[len(s) // 2] for s in samples]

    rows = []
    for y in range(y_off, y_end):
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


def close_stray_wechat_windows(main, W, H):
    """关掉**误开的微信窗口**（搜一搜 / 视频号 / **朋友圈** / 误开的小程序等）。

    为什么需要：点侧边栏图标时若点错（图标列表是动态的，序号会变），会开出这类窗口，
    它们会一直挡在最上层，导致后续所有点击都落到它们身上、`top_window()` 永远返回它
    → 面板永远判不出来（实测踩到：点错一次，之后试遍所有图标全无反应）。

    判据（2026-09-27 修订：**非主窗口 + 不是小程序面板** → 就算误开）：
    ⚠️ 旧判据多了一条「标题必须是『微信』」，于是**朋友圈窗口被整条漏掉**
    （它的标题就是「朋友圈」、WM_CLASS 也是 wechat）—— 实测批跑中途开出朋友圈后
    没人清理它，紧接着 liuyushou 的面板打不开、`[ensure] rc=3` 直接放弃这个号。
    在本函数的语境里要保的只有一个（那块带小程序 tab 的面板），其余顶层窗口都是误开。

    关法：`close_window`（微信自己的 ✕），按标题选锚点 ——
      标题「微信」（搜一搜/视频号这类全屏浏览器式窗口）→ `kind="panel"`；
      其他标题（朋友圈 / 误开的小程序）→ `kind="miniapp"`。
    ⚠️ **不再用 `xdotool windowclose`**：微信自绘窗口对 WM_DELETE_WINDOW 不可靠
    （实测朋友圈靠它关不掉、只能人工关；而 `close_window` 一击即中：
     `探测到关闭字形 (826,256) → 已关闭`）。也**不再用 windowclose 的另一个理由**：
    它对小程序窗口会损坏 WeChatAppEx 状态机（见 force_close_miniapp 的说明）。
    """
    killed = []
    for wid, t in windows():
        t = (t or "").strip()
        if str(wid) == str(main) or str(wid) == str(find_main_window()):
            continue
        if is_main_window(wid):
            continue
        if not t:
            continue                     # 无名的辅助窗口不碰
        if has_miniapp_tab(wid, W, H):
            continue                     # 这是真正的小程序面板，留着
        kind = "panel" if t == "微信" else "miniapp"
        print("[reopen] 关掉误开的窗口 %s（%s）" % (wid, t))
        if close_window(wid, W, H, kind=kind):
            killed.append(wid)
            time.sleep(0.8)
    return killed


def _rail_top_group(buttons):
    """从侧边栏按钮列表里切出**顶部那组**（图标等距排列的那一段）。

    ⚠️ 2026-09-27 治本：**不要再用 `y < H * 0.6`** —— 那个 H 是**屏幕**高（1024），
    而侧边栏按钮的 y 是**窗口内**坐标。窗口不全屏时两者差很远：
    实测窗口高 674、按钮 = `[62,114,162,210,258,306,354,402,590]`，
    按屏幕高算阈值 614 → 底部的 **590 也被算进「顶部组」**，而试错循环是
    `reversed(top[1:])`（从下往上试）→ **590 被第一个点** →
    实测一点就开出**「朋友圈」窗口**；它挡在最上层后，后续品牌的面板打不开、
    `[ensure] rc=3` 直接放弃（这就是「莫名冒出朋友圈窗口」的根因）。

    判据改成**结构性**的（与窗口多大无关）：顶部图标等距排列（实测间距 ≈48px），
    底部图标与它们之间会出现**间距突变**（实测 402→590 = 188 ≈ 3.9 倍）。
    以「间距 > 1.8 × 中位间距」为分界，取前面那一段 —— 中位数而不是均值，
    这样个别间隔偏大也不会把分界整体带偏。
    """
    if len(buttons) < 2:
        return list(buttons)
    gaps = [b - a for a, b in zip(buttons, buttons[1:])]
    mid = sorted(gaps)[len(gaps) // 2]          # 中位间距（实测 48）
    for i, g in enumerate(gaps):
        if mid > 0 and g > mid * 1.8:           # 间距突变 = 换了一组（实测 188 > 86）
            return list(buttons[:i + 1])
    return list(buttons)


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

    # ⚠️ 这里**不要**再把主窗口拉全屏了（旧代码调 `ensure_main_fullscreen`）。
    #    侧边栏检测已经改成按主窗口实际几何扫描（见 `_rail_scan_geom`），
    #    窗口多大都找得着；强行拉全屏反而会让 openbox frame 变成整屏尺寸、
    #    首次点击被 frame 吃掉（要连点两次）。详见 `find_rail_buttons` 的说明。

    # ⭐ 面板**已经开着就直接复用，绝不点侧边栏**（2026-09-25 踩到，代价很重）。
    #    侧边栏那个「小程序」按钮是**切换**语义：面板开着时点它 = **把它关掉**；
    #    关掉之后微信内部「面板已打开」的状态又不复位，于是再点又变成「开」——
    #    实测点了两次、状态全乱。更糟的是那一刻**主窗口在最前**（面板还在窗口列表里，
    #    只是没被抬起来），两次点击都落在**主窗口的侧边栏**上乱点，
    #    最后弹出了「退出登录？确定 / 取消」确认框 —— 鼠标就悬在「确定」上，
    #    真点下去立刻掉登录（要手机确认、拖久了只能扫码）。
    #    所以：先找面板、找到就用；找不到才去点侧边栏。
    for wid, t in windows():
        if str(wid) == str(main):
            continue
        if win_class(wid) or (t or "").strip() != "微信":
            continue
        # ⚠️ 必须**先把它抬到最前、再判** —— `has_miniapp_tab()` 是**抓屏**判据，
        #    面板被别的窗口盖着时抓到的就是别人，会给出**假否**。
        #    实测踩到：`clean_leftovers()` 为了扫主窗口上的弹窗会把**主窗口**抬到最前，
        #    紧接着 open_panel 在这里判 → has_miniapp_tab 假否 → 一路走到点侧边栏 → rc=3，
        #    日志表现为「面板打不开」，而面板其实好着（手工验证 top_window=面板、
        #    has_miniapp_tab=True）。
        raise_window(wid)
        if has_miniapp_tab(wid, W, H):
            print("[reopen] 面板已经开着（窗口 %s）→ 直接复用，不点侧边栏" % wid)
            return wid

    # ⚠️ 2026-09-26 实测定案：**不能只点「顶部组最后一个」**（旧写法 `y = top[-1]`）。
    #    那个写法的前提是「小程序图标恰好是顶部组最后一个」—— 实测**不成立**：
    #    侧边栏图标是**动态列表**，会随使用习惯增减（实测多出过「搜一搜」）。
    #    880x640 窗口下扫到 `[254,306,359,402,450,498,546,594,748]`，
    #    第 7 个(546)=小程序、第 8 个(594)=搜一搜，`top[-1]=594` 点成了**搜一搜**
    #    → 弹出的窗口标题也叫「微信」、全屏、无 WM_CLASS，`has_miniapp_tab()` 判否
    #    → 日志表现成「面板打不开」，其实是**点错了图标**。
    #    更坏的是：在侧边栏乱点可能点到左下角的「退出登录」区，弹出确认框（实测踩到过）。
    #
    #    所以改成：**从下往上逐个试顶部组按钮**，每点一个立刻验证「顶上是否出现
    #    带小程序标签的面板」—— 认出来了就返回，认不出就试下一个。
    #    这样不依赖图标序号/形状，图标列表怎么变都能找到小程序那一个。
    for attempt in (1, 2):
        # 点侧边栏前必须确认**主窗口真的在最前**，否则点击会落到别的窗口上乱点
        # （上面那个「退出登录」框就是这么点出来的）。
        if not raise_window(main):
            print("[reopen] 主窗口抬不到最前（顶上不是它）→ 不点侧边栏，免得误点")
            return None
        buttons = find_rail_buttons(W, H)
        # ⚠️ 2026-09-27：切顶部组改走 `_rail_top_group()`（按**间距突变**判，
        #    与窗口多大无关）。旧写法 `y < H * 0.6` 用的是**屏幕**高，
        #    会把底部的「朋友圈」图标当成顶部组、还被第一个试 → 实测开出朋友圈窗口。
        top = _rail_top_group(buttons)
        if len(top) < 2:
            print("[reopen] 侧边栏按钮没找全：%s" % buttons)
            return None
        # ⚠️ 2026-09-27 治本：**只试顶部组里最靠后的 3 个**，不要逐个试全部。
        #    实测顶部组 = [头像, 微信, 通讯录, 收藏, **朋友圈**, …, 小程序面板]，
        #    里面**混着朋友圈**！一旦小程序面板按钮打不开（微信状态不好时就是这样），
        #    「逐个试」就会试到朋友圈 → 开出朋友圈窗口 → 它挡在最上层，
        #    后续品牌全部 `rc=3` 放弃（实测：402 打不开之后，试到 258 = 朋友圈）。
        #    为什么只试最后 3 个：面板按钮实测总在靠后位置 ——
        #      窗口高 674 时顶部组 8 个、面板 = 402（最后一个）；
        #      窗口高 640 时 8 个、面板 = 546（倒数第二）、搜一搜 = 594（最后一个）。
        #    宁可试不到（放弃这个号）也**不要乱点**：点朋友圈/收藏这类按钮毫无收益、
        #    只会污染窗口栈。试完这 3 个还不行 → 交给下面的 Ctrl+R / 放弃。
        cands = list(reversed(top[1:]))[:3]
        for n, y in enumerate(cands, 1):
            # ⚠️ **每轮开始前先关掉上一轮误开的窗口**（如搜一搜/视频号）—— 它们是
            #    全屏顶层窗口，会一直挡在上面，导致后面所有点击都落到它身上、
            #    `top_window` 永远返回它 → 全部尝试都判否（实测踩到：点错一次开成
            #    搜一搜，之后试遍所有图标都没反应）。
            close_stray_wechat_windows(main, W, H)
            if not raise_window(main):
                return None
            print("[reopen] 第%d轮·试第%d个侧边栏按钮 y=%d" % (attempt, n, y))
            if not click_rail(main, y, 2.5, "（侧边栏·试小程序）"):
                return None
            for _ in range(4):
                time.sleep(1.2)
                wid = top_window(W, H)
                if not wid or str(wid) == str(main):
                    continue
                if win_class(wid) or title_of(wid) != "微信":
                    break                        # 顶上不是微信自己的窗口，另说
                if has_miniapp_tab(wid, W, H):
                    print("[reopen] 面板就位（窗口 %s，侧边栏 y=%d）" % (wid, y))
                    return wid
                break                            # 这个图标开的不是小程序面板，换下一个
    # 点侧边栏判不出来 —— 先把**面板 webview 自己**救一次（Ctrl+R），最便宜且常有奇效。
    print("[reopen] 侧边栏判不出来 → 试 Ctrl+R 重载面板 webview")
    wid = reload_panel(W, H)
    if wid:
        return wid
    print("[reopen] 没能确认小程序面板 → 不继续（不乱点）")
    return None


def panel_search_once(W, H, panel):
    """在面板里：放大镜 → 输入 → **回车**（实测：回车就会进搜索页）→
    搜索结果页里逐张卡片点，用窗口标题验证；**开错了就关掉继续试下一张**。
    成功返回 True。"""
    # ⭐ 每次搜索前先把面板 webview 重载一次 —— 这不是保守，是**必需**。
    #    实测（2026-09-25）踩了一整轮：面板停在「绿茵_搜索」标签、搜索框里**残留着
    #    上一次的关键词**，而 `type_text` 是把字符**追加**进去、不是替换 ——
    #    于是搜出来的是「绿茵<新关键词>」的混合结果，逐张卡片试开全是同族的错号
    #    （实测开出「绿茵约战」「绿茵聚落」，两个都关不掉）→ 窗口越堆越多、整批卡死。
    #    重载一次同时解决三件事：① 清掉残留搜索词；② 清掉累积的「xxx_搜索」标签
    #    （标签一多，放大镜/卡片位置都会漂）；③ 顺带治好 webview 掉成
    #    「没有连接到网络」的错误页（那次也是靠 Ctrl+R 救回来的）。
    rp = reload_panel(W, H)
    if rp:
        panel = rp
    raise_window(panel)
    if str(top_window(W, H)) != str(panel):
        print("[reopen] 面板不在最前（有别的窗口挡着）→ 不输入，退出")
        return False
    if not click_in(panel, R_PANEL_MAGNIFIER[0], R_PANEL_MAGNIFIER[1], 1.5, "（面板放大镜）"):
        return False
    # 双保险：万一重载没生效、或者点放大镜没真正聚焦，先全选清空再输。
    # （重载后搜索框本该是空的，这一步是零成本的兜底。）
    key("ctrl+a", 0.3)
    key("Delete", 0.5)
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
    return click_rail(main, y, 2.5, "（侧边栏·微信）")


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


def do_search(W, H, box, goto_net=False):
    """主窗口搜索框 → 输入 →（可选）点「搜索网络结果」落到搜一搜结果页。

    **默认停在下拉**（goto_net=False）：实测小程序条目就在「输入后的下拉」里
    （「最近使用过的小程序」那一段），根本不用跳页面 —— 手工点开刘一手就是这么点的；
    而老实现一路点到「搜一搜网络结果页」，那一页**没有小程序列表**，`find_rows` 自然是 0 行
    （2026-09-25 实测：`结果页找到 0 行`，白跑两轮）。
    goto_net=True 保留老行为，只作为「下拉里确实没有小程序条目」时的退路。

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
    if not goto_net:
        return True                     # 就停在下拉，交给调用方去点小程序条目
    ny = find_netsearch_row(buf, W, H, box)
    if not ny:
        # 定位不到就**什么都不按**退出（老代码这里按 Down+回车，
        # 万一焦点还在聊天输入框，那一回车就是把关键词发出去）
        print("[reopen] 没定位到「搜索网络结果」行 → 不按回车，直接退出这条路径")
        return False
    # ⚠️ 2026-09-27（实测定案）：进搜索页要点的是 **✳ 行下面第一条搜索建议**，
    #    不是 ✳ 那一行字本身（点它没有任何反应，症状是「迟迟进不去搜索页」）。
    sy = find_first_suggestion(buf, W, H, box, ny)
    if not sy:
        print("[reopen] ✳ 行（y=%d）下面没扫到搜索建议行 → 不点（宁可不做也不乱点）" % ny)
        return False
    print("[reopen] 点搜索建议第 1 行 y=%d（「搜索网络结果」那行字点了没反应，跳过）" % sy)
    click(int(W * 0.12), sy, 4.0)
    png(W, H, "reopen_search_page.png")
    return True


def dropdown_logo_cols(W, box=None):
    """搜索下拉里「图标列」的 x 范围（屏幕绝对坐标）—— 按**搜索框左缘**算。

    ⚠️ 2026-09-27 治本：**不能再写死屏幕比例**。
    旧值 `(int(W*0.054), int(W*0.086))` = 69..110 是**按屏幕宽 1280** 算的，
    而下拉是**在主窗口里**、图标相对**搜索框左缘**定位。窗口一旦不是全屏，两者就对不上。

    实测（主窗口 759x674、搜索框左缘 x=74、搜索框返回的点击 x=106）：
      · ✳「搜索网络结果」图标      x≈88..99
      · 小程序条目 logo            x≈108..130（宽 22px）
      · 搜索建议行的蓝色放大镜      x≈108..120
    全部落在 **左缘 +14 .. +56** 这个带里；旧列 69..110 只覆盖到左半截 ——
    小程序 logo 只剩 3 个像素能命中 → band 判不过 → 判成「下拉里没有小程序条目」
    → 退化去点「搜索网络结果」（实测现象正是「为什么一直点搜索网络结果」）。

    `box` 是 `find_search_box()` 的返回值 `(点击x, 点击y, 框宽, 占位跨度)`，
    它的 x 在框内偏左约 32px，所以左缘 = `box[0] - 32`（实测 106-32=74 ✓）。
    """
    if box:
        left = box[0] - 32
        return left + 12, left + 60
    return int(W * 0.054), int(W * 0.086)     # 没有 box 时退回旧值（兼容旧调用）


def find_netsearch_row(buf, W, H, box=None):
    """找搜索下拉里「搜索网络结果」那一行的 y。
    判据用**图标宽度**：它是窄的 ✳（只占 x≈89..98，约 9px），
    而小程序行的 logo 占 x≈76..98（约 22px）—— 颜色不可靠（logo 里也有浅粉像素），
    宽度可靠。（🔍 建议行的放大镜是灰色，不参与。）

    ⚠️ 2026-09-27：扫描列改由 `dropdown_logo_cols()` 按搜索框左缘算（见其说明）。
    """
    x0, x1 = dropdown_logo_cols(W, box)
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


def find_dropdown_miniapps(buf, W, H, box=None):
    """找搜索下拉里**小程序条目**的 y 中心列表（按 y 升序）。

    ⚠️ 2026-09-27 重写（依据实测：主窗口 759x674 / 搜索框左缘 x=74）：

    ① **扫描列**：旧值写死 `69..110`（按屏幕宽算），而图标实际在 x≈88..130
       （相对搜索框左缘 +14..+56）→ 旧列只覆盖左半截，小程序 logo（宽 22px）
       只剩 3 个像素命中 → band 判不过 → **判成「下拉里没有小程序条目」** →
       退化去点「搜索网络结果」。改成 `dropdown_logo_cols()` 按左缘算。

    ② **y 范围**：只在「搜索网络结果」行的**上方**找。下拉布局是
           [最近使用过的小程序]      ← 有匹配时才出现这一段
             小程序条目…
           ✳ 搜索网络结果
             [搜索建议] 建议行…
       建议行的放大镜与条目 logo **落在同一列、文字同样绿色**，靠 x/颜色分不开；
       但位置关系稳定：**条目必在 ✳ 行之上**。
       实测（输入未匹配到小程序的诊断那次）：✳ 在最上（y=87），下面全是建议行
       （从 y=115 起）—— 不限制 y 就会把建议行当成小程序条目、点开一堆无关搜索页。

    判据与 find_netsearch_row 同源、方向相反：两类行的图标都落在同一条竖列里，
    但**小程序行的 logo 横向跨度约 22px**，而「搜索网络结果」的 ✳ 只有约 9px ——
    颜色不可靠（logo 里也有浅粉像素），**宽度可靠**。
    """
    x0, x1 = dropdown_logo_cols(W, box)
    y_top = (box[1] + 20) if box else int(H * 0.05)     # 搜索框下方
    net_y = find_netsearch_row(buf, W, H, box)
    y_bot = net_y if net_y else int(H * 0.65)           # ✳ 行之上
    rows = {}
    for y in range(max(0, y_top), max(y_top + 1, y_bot)):
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
        return []
    ys = sorted(rows)
    bands, cur = [], [ys[0]]
    for y in ys[1:]:
        if y - cur[-1] <= 6:
            cur.append(y)
        else:
            bands.append(cur); cur = [y]
    bands.append(cur)
    thin = max(int(W * 0.012), 12)                        # 窄于 ~15px 的是 ✳，不是小程序
    out = []
    for band in bands:
        if len(band) < 8:                                 # 太薄，不是图标
            continue
        lo = min(rows[y][0] for y in band)
        hi = max(rows[y][1] for y in band)
        if hi - lo > thin:
            out.append((band[0] + band[-1]) // 2)
    return out


def find_first_suggestion(buf, W, H, box=None, net_y=None):
    """找搜索下拉里**搜索建议列表的第一条**的 y 中心 —— 进搜索页要点就是这一条。

    ⚠️ 2026-09-27 实测定案（这条是布局知识，别再改回去）：
      ① 点「✳ 搜索网络结果」那一行字**没有任何反应** —— 所以不能点它（旧实现就点的它，
         症状是「迟迟进不去搜索页」，最后只能退化成面板路径）；
      ② 「最近使用过的小程序」里的条目**也不该点** —— 同名号会点错（九村烤脑花
         VIP / YX 就是），且那段有没有、排在哪都随历史变化；
      ③ **点 ✳ 行下面的第一条搜索建议**才会真正执行搜索、进搜索页。
         实测：点 y=121（✳ 行在 y=87）→ 立刻开出搜一搜窗口 1279x1023，
         页面第一条就是目标小程序（带「小程序」标记）。

    布局（实测，主窗口 759x674、搜索框左缘 x=74）：
        [最近使用过的小程序]              ← 不点
          小程序条目…
        ✳ 搜索网络结果        y≈87        ← 不点（点了没反应）
        建议行 1              y≈121       ← ✅ 点这条
        建议行 2              y≈154
        建议行 3              y≈187
    """
    if net_y is None:
        net_y = find_netsearch_row(buf, W, H, box)
    if net_y is None:
        return None
    x0, x1 = dropdown_logo_cols(W, box)
    y0 = net_y + 10                                    # 跳过 ✳ 行自身
    y1 = min(int(H * 0.85), net_y + 150)               # 只看紧邻的几行
    rows = {}
    for y in range(y0, y1):
        base = y * W * 3
        xs = []
        for x in range(x0, x1):
            i = base + x * 3
            r, g, b = buf[i], buf[i + 1], buf[i + 2]
            if max(r, g, b) - min(r, g, b) > 40:       # 彩色（建议行的放大镜是蓝的、文字是绿的）
                xs.append(x)
        if len(xs) >= 3:
            rows[y] = (min(xs), max(xs))
    if not rows:
        return None
    ys = sorted(rows)
    bands, cur = [], [ys[0]]
    for y in ys[1:]:
        if y - cur[-1] <= 8:
            cur.append(y)
        else:
            bands.append(cur); cur = [y]
    bands.append(cur)
    for b in bands:
        # 一行约 13~15px 高（实测 117..129），逐行扫描下应有 ≥8 个采样点；
        # ⚠️ 别用 step=2 + 更低的阈值：行矮一点就会漏（自己静态自测踩过）。
        if len(b) >= 8:
            return (b[0] + b[-1]) // 2
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


# ───────────────────── 匿名模态框：点不动东西时先查它 ─────────────────────

# 判据：无名字 + 尺寸像对话框的顶层窗口。主窗口/面板都是 1280x1024，
# 辅助窗口是 1x1 / 10x10 / 200x200 —— 都被这个区间过滤掉。
ANON_MODAL_W = (100, 500)
ANON_MODAL_H = (80, 400)


def find_anon_modal():
    """找微信的**匿名模态框**（「退出登录？确定/取消」这类），返回 (wid, x, y, w, h) 或 None。

    为什么值得单列一条判据：它是个**独立匿名顶层窗口**（无名、无 WM_CLASS、parent 是 root），
    既不在 `windows()`（按标题筛）里，也不属于主窗口 —— 但它是 **Qt 模态**，
    **存在期间主窗口收不到任何点击**。于是症状全都伪装成别的东西：
    「小程序面板打不开 / 侧边栏点不动 / 坐标算错了 / 分辨率把输入映射搞乱了」。

    ⚠️ 它**会在运行过程中由某次点击冒出来**（2026-09-25 实测：一开始没有，点了几次之后才出现），
    所以只在每轮开头查一次是不够的 —— 见 close_anon_modal 的调用点。
    """
    out = run("DISPLAY=%s xwininfo -root -children" % DISPLAY)
    W, H = size()
    for line in out.splitlines():
        m = re.match(r"\s+(0x[0-9a-fA-F]+) \(has no name\):\s+\(\)\s+"
                     r"(\d+)x(\d+)\+(-?\d+)\+(-?\d+)", line)
        if not m:
            continue
        w, h, x, y = int(m.group(2)), int(m.group(3)), int(m.group(4)), int(m.group(5))
        if not (ANON_MODAL_W[0] <= w <= ANON_MODAL_W[1]
                and ANON_MODAL_H[0] <= h <= ANON_MODAL_H[1]):
            continue
        # ⚠️ 再加一道「**必须居中**」—— 这条是踩了坑才补的（2026-09-25）：
        #    主窗口搜索框的**下拉浮层**同样是「无名顶层窗口 + 尺寸正好落在这个区间」
        #    （实测 320x298 @ 73,72），但它**贴在左上角**；真正的模态框是**屏幕居中**的
        #    （实测 282x170：1280x1024 下在 499,427、1024x768 下在 370,298 —— 两次中心都精确等于屏幕中心）。
        #    只按尺寸判的后果：搜索流程**每点一行结果之前都去「关」自己的下拉**，
        #    把点卡片的坐标带歪，现象是「点过候选卡片但没等到目标窗口」——
        #    看起来像微信改了界面，其实是自己的判据误伤了自己。
        if abs((x + w / 2.0) - W / 2.0) > W * 0.15 or abs((y + h / 2.0) - H / 2.0) > H * 0.15:
            continue
        return m.group(1), x, y, w, h
    return None


def close_anon_modal(W, H, verbose=True):
    """有匿名模态框就关掉它（返回是否关掉了）。

    ⚠️ **必须先 activate 再点**：这种原生对话框不吃「未激活状态下的合成点击」——
    实测直接 click 连点三次框纹丝不动，而「先 `xdotool windowactivate --sync` 再点同一坐标」
    一次就成（Esc / Tab+Return 实测也无效）。

    按钮在框内的相对位置是固定的（实测 282x170 的框：「确定」≈(0.23W,0.68H)、
    「取消」≈(0.69W,0.68H)）—— 这里**只点右侧那颗**（确定在左、取消在右），
    绝不碰绿色那颗（那是「确定退出登录」，点下去就掉登录，要手机确认、拖久了只能扫码）。
    """
    got = find_anon_modal()
    if not got:
        return False
    wid, x, y, w, h = got
    if verbose:
        print("[anon] 发现匿名模态框 %s (%dx%d @ %d,%d) → 先激活再点右侧「取消」"
              % (wid, w, h, x, y))
    run("DISPLAY=%s xdotool windowactivate --sync %s" % (DISPLAY, wid))
    time.sleep(0.6)
    click(int(x + w * 0.69), int(y + h * 0.68), 1.5)
    if find_anon_modal() is None:
        if verbose:
            print("[anon] 已关掉（之前所有点击都被它吃了）")
        return True
    if verbose:
        print("[anon] 没关掉 → 存图 %s" % png(W, H, "anon_modal.png"))
    return False


def open_via_search(W, H):
    """⛔ **已弃用（2026-09-27 实测定案），别再调用** —— 保留定义只为留档。

    它做的是「输入关键词 → 在下拉里逐行点**小程序条目**（即『最近使用过的小程序』那段）」。
    两个问题：
      · 那段条目会**点错号** —— 同名不同号就在同一段里（九村烤脑花 VIP / YX、
        辣可可现炒黄牛肉 / …i），谁排前面全看使用历史；
      · 正确做法是「点 ✳ 下面第一条搜索建议 → 进搜索页」，
        搜索页布局固定、第一条结果恒是目标小程序（见 `open_via_souyisou`）。

    另外它内部依赖的 `find_dropdown_miniapps()` 现在只在「✳ 行之上」找条目，
    语义也已与这里不再匹配 —— 留着只会误导，真要复活请先读 `open_via_souyisou` 的说明。

    历史沿革：这函数曾被当作「面板失败后的替代主路径」（2026-09-26 一度排在面板之前）。
    它当初之所以看起来是"主路径"，是因为**旧版坐标算错**（扫描列按屏幕比例、
    侧边栏 y 二次换算）导致它必然失败并退化成面板 —— 面板跑通给了它"兜底有效"的假象。
    """
    for round_no in (1, 2):
        close_anon_modal(W, H)                 # 框会吞点击：动手前先清掉
        if not ensure_chat_tab(W, H):          # 非「微信」标签下那个框是局部搜索
            return False
        box = find_search_box(W, H)
        if not box:
            print("[reopen] 没找到全局搜索框（可能不在「微信」标签）→ 不输入、不回车")
            return False
        if box[3] > PLACEHOLDER_MAX:
            print("[reopen] 搜索框占位文字跨度 %d（像是「搜索收藏」这种局部搜索）→ 不用它" % box[3])
            return False
        if not do_search(W, H, box):           # 默认停在「输入后的下拉」
            return False
        # 小程序条目就在下拉里（手工点开刘一手点的就是这里）；下拉里没有（比如这个词
        # 没匹配到号）才退回搜一搜结果页 —— 两套坐标体系不同，别混用。
        rows = find_dropdown_miniapps(grab(W, H), W, H, box)
        click_x = int(W * 0.12)
        is_dropdown = True               # 下拉是临时弹层：点开真实条目就收；搜一搜页不收
        print("[reopen] 第%d轮：下拉里找到 %d 个小程序条目：%s" % (round_no, len(rows), rows))
        if not rows:
            print("[reopen] 下拉里没有小程序条目 → 退回搜一搜结果页")
            if not do_search(W, H, box, goto_net=True):
                continue
            rows = find_rows(grab(W, H), W, H)
            click_x = int(W * 0.28)
            is_dropdown = False
            print("[reopen] 第%d轮：结果页找到 %d 行：%s" % (round_no, len(rows), rows))
        if not rows:
            continue
        main = find_main_window()
        baseline = {wid for wid, _ in windows()}
        # ⚠️ 2026-09-26：试点行数从固定 6 提到可配置（默认 10）。
        #    为什么：keyword 一旦偏宽（如过桥缘用「过桥缘」而非全名「过桥缘游戏中心」），
        #    下拉里会挤进一堆同品牌/近名条目，目标被顶到第 7 行之后 → 原来 rows[:6] 根本试不到，
        #    症状是「两轮都扫完还没打开」却看不出原因（实测：过桥缘 4 项里目标掉在外面，
        #    换成完整名 keyword 后落到第 2 行立刻命中）。
        #    纯上限放宽，命中即返回，不会因为多试几行而变慢（没命中才多花时间）。
        max_rows = int(os.environ.get("WXSIGN_TRY_ROWS", "10") or 10)
        tried = []                               # 已试过的行 y（±10 容差判重，防重建后重复点开同一条）
        while len(tried) < max_rows:
            cand = None
            for y in rows:
                if all(abs(y - t) > 10 for t in tried):
                    cand = y
                    break
            if cand is None:
                break
            tried.append(cand)
            close_anon_modal(W, H)             # 逐行点之前都清一次：框一冒出来后面全是白点
            print("[reopen] 第%d轮 试第 %d 行 y=%d" % (round_no, len(tried), cand))
            TOUCHED[0] = True
            click(click_x, cand, 3.0)
            wid, title = find_target_window()
            if wid:
                print("[reopen] 打开成功：%s" % title)
                png(W, H, "reopen_done.png")
                return True
            for wid_, title_ in windows():        # 开错了就关掉，别越堆越多
                if wid_ not in baseline and TARGET not in title_ and title_.strip() != "微信":
                    print("[reopen] 开错了（%s），关掉" % title_)
                    # ⚠️ 2026-09-26：先走「微信自己的关闭按钮」（close_window）。
                    #    那个按钮在这台环境**经常点不中**（字形探测判否 → 直接放弃），
                    #    于是错误的窗口一直堆着，把主窗口盖住、搜索框都点不着，
                    #    整轮搜索就废了（实测：打开「辣可可现炒黄牛肉」≠目标「…i」，
                    #    关不掉 → 第2轮下拉窜成 6 行 → 全 miss）。
                    #    失败后再补一条**只针对小程序窗口**的硬关：`xdotool windowclose`。
                    #    为什么这里可以用：小程序窗口是 WeChatAppEx 的独立 X 窗口，
                    #    `windowactivate --sync` 后 `windowclose` 实测能干净关掉，
                    #    且**不影响**再来一次搜索重开（已手工验证）。
                    #    绝不对「面板 / 主窗口」用这条（那才会破坏微信内部状态）。
                    if not close_window(wid_, W, H, kind="miniapp"):
                        force_close_miniapp(wid_, title_)
            raise_window(main)                    # 页面可能被带走了，拉回主窗口
            # ⚠️ 2026-09-26 治本：下拉是**临时弹层** —— 点开任何真实条目（不管对错）
            #    它就收掉了。旧实现继续按旧 y 盲点下一行，点击全落在主窗口聊天列表上
            #    （jiucun 实测：VIP 在 y=135 被点开收掉下拉，目标 YX 在 y=199 永远试不到，
            #    两轮白跑直到外层超时被 SIGTERM，还连累 hook 被 restart_hook 杀死）。
            #    正解：每行点完只要没开成，就探一次下拉是否还在（「搜索网络结果」行在 = 下拉在），
            #    不在就 do_search 重建并重扫，再从未试过的行继续；搜一搜结果页是持久页面，不用重建。
            if is_dropdown and not find_netsearch_row(grab(W, H), W, H, box):
                print("[reopen] 下拉已收 → 重新展开再试下一行")
                if not do_search(W, H, box):
                    break
                rows = find_dropdown_miniapps(grab(W, H), W, H, box)
                print("[reopen] 重建下拉：%d 个条目 %s" % (len(rows), rows))
    print("[reopen] 两轮都扫完还没打开")
    return False


def open_via_souyisou(W, H):
    """搜一搜结果页路径：主窗口搜索 → 点搜索建议第 1 行进搜一搜 → 点第一行小程序结果。

    # ╔══════════════════════════════════════════════════════════════════════════╗
    # ║ ⛔ **2026-09-27 停用**：这条路先不做，改用小程序面板搜索（已跑通）      ║
    # ║   `main()` 里对它的调用已注释掉，代码与配套修复全部保留，日后有精力再续。║
    # ╚══════════════════════════════════════════════════════════════════════════╝

    **为什么停用（实测现象）**：这条路能进搜索页，但会把环境搞坏 ——
    实测现象：走几次之后**小程序面板就进不去了**（侧边栏的小程序/搜一搜按钮
    点了无响应）。面板是本环境唯一跑通全流程的路径，把它搞坏等于断了后路。
    此外它还会冒出「朋友圈」等额外窗口（2026-09-27 01:52 手工关掉）。
    而面板路径**已跑通**：2026-09-27 00:52 那批 lakeke / guoqiaoyuan 都是它签到的。

    **已经做对的修复（别丢，续做时直接能用）**：
      · `click_rail()`：侧边栏 y 不再被二次换算（旧写法在 759x674 窗口下把 114 算成 75
        → 点中微信头像；这条**面板路径也用**，必须留着）；
      · `dropdown_logo_cols()`：下拉图标列改按**搜索框左缘**算（旧值按屏幕宽写死，
        小窗口下只覆盖半截 → 漏判「下拉里没有小程序条目」）；
      · `find_first_suggestion()`：点 ✳ 行**下方第一条搜索建议**才进搜索页
        （点「搜索网络结果」那行字**没有任何反应** —— 这条是实测得出的布局知识）。
      2026-09-27 01:47 端到端验证：这三条**都生效了**（日志显示点侧边栏 y=114、
      点建议行 y=216、搜一搜窗口就位）。

    **续做时要解决的问题（已定位但没修）**：
      进了搜索页后 `find_rows()` 扫出的行点不中目标小程序。
      实测 01:47：扫到 `[371, 618, 689, 853, 925]`（逐行点全部失败），
      而手工观察到目标小程序卡片（带「小程序」标记）在 **y≈237..344** —— 差约 80px。
      怀疑：`find_rows` 的扫描列 `x0,x1 = int(W*0.17), int(W*0.24)` 同样是**按屏幕宽写死**，
      而搜索页窗口尺寸/位置不一定等于屏幕（屏幕被 `xrandr` 钉成 1280x1024，
      主窗口却只有 1024x768）。修法参照 `dropdown_logo_cols()`：
      **按搜索页窗口自己的几何算扫描列**，别用屏幕比例。
      另外注意：屏幕分辨率是**动态的**（云微网页端打开时会被改成 767x731 之类），
      `main()` 里的 `xrandr` 钉屏只是补救，任何按屏幕比例算的坐标都不牢靠。

    **2026-09-26 新增时的初衷（保留参考）**：为什么当初想走这条路 ——
    搜索下拉的布局是**动态的** —— 搜索建议行数随历史/热词变化（实测同一关键词，
    「最近使用过的小程序」条目一次在 y=133、一次被 5 行建议挤到 y=338），
    任何「固定 y / 前 N 行试错」都会漂；`find_dropdown_miniapps` 的彩色-logo 扫描
    也会被建议区/标题区干扰（实测返回 [60,243,337,377]，目标在 337 但前两行
    都是误判，点错还可能误触退出登录框）。
    而**搜一搜结果页布局固定**：第一个「小程序」结果恒在页面第一行
    （2026-09-26 实测 2/2：过桥缘、辣可可均一击即中，窗口标题精确匹配）。

    步骤：do_search(goto_net=True) 进搜一搜 → find_rows 扫行 → 逐行点 + 标题复核，
    开错用 force_close_miniapp（◎ 胶囊正常关闭，不损坏运行时）关掉试下一行。
    """
    main = find_main_window()
    for round_no in (1, 2):
        close_anon_modal(W, H)
        if not ensure_chat_tab(W, H):
            return False
        box = find_search_box(W, H)
        if not box:
            print("[reopen] 没找到全局搜索框 → 搜一搜路径退出")
            return False
        if box[3] > PLACEHOLDER_MAX:
            print("[reopen] 搜索框占位文字跨度 %d（局部搜索）→ 不用" % box[3])
            return False
        baseline = {wid for wid, _ in windows()}
        if not do_search(W, H, box, goto_net=True):      # 点「搜索网络结果」进搜一搜
            continue
        time.sleep(3)
        # 搜一搜页 = 「标题=微信、无 WM_CLASS」的副窗口。优先用本轮新开的；
        # 已有一个现成的（上次搜索留下的）就直接复用 —— 实测微信对
        # 「搜索网络结果」是**复用**已有搜一搜窗口而非新开。
        sousou = None
        for wid, t in windows():
            if wid in baseline or str(wid) == str(main):
                continue
            if (t or "").strip() == "微信" and not win_class(wid):
                sousou = wid
                break
        if not sousou:
            for wid, t in windows():
                if str(wid) == str(main):
                    continue
                if (t or "").strip() == "微信" and not win_class(wid):
                    sousou = wid
                    break
        if not sousou:
            print("[reopen] 没等到搜一搜窗口 → 重试")
            continue
        print("[reopen] 搜一搜窗口 %s 就位，扫结果行" % sousou)
        raise_window(sousou)
        rows = find_rows(grab(W, H), W, H)
        print("[reopen] 第%d轮：结果页 %d 行：%s" % (round_no, len(rows), rows))
        for i, y in enumerate(rows[:6], 1):
            close_anon_modal(W, H)
            raise_window(sousou)
            print("[reopen] 第%d轮 试结果页第 %d 行 y=%d" % (round_no, i, y))
            TOUCHED[0] = True
            click(int(W * 0.25), y, 5.0)
            wid, title = find_target_window()
            if wid:
                print("[reopen] 打开成功：%s" % title)
                png(W, H, "reopen_done.png")
                return True
            for wid_, title_ in windows():               # 开错了就正常关掉，别堆
                if wid_ not in baseline and wid_ != sousou \
                        and TARGET not in title_ and title_.strip() != "微信":
                    print("[reopen] 开错了（%s），关掉" % title_)
                    if not close_window(wid_, W, H, kind="miniapp"):
                        force_close_miniapp(wid_, title_)
        # 本轮没成：留着搜一搜窗口（下轮复用），别重复开窗口
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

    # ⚠️ 2026-09-27 定案：**主路径 = 小程序面板搜索**（这是本环境唯一跑通全流程的路径）。
    #    「点搜索建议行 → 搜一搜结果页」那条（open_via_souyisou）已于同日**停用**，
    #    原因见该函数的停用说明：它会把环境搞成「小程序面板进不去」。
    print("[reopen] 主路径：小程序面板搜索")
    if open_via_panel(W, H):
        return 0

    # 失败可能是**匿名模态框把点击全吃了** —— 症状与「面板打不开」一模一样。
    # 先清一次框再试一次：很便宜，能救回「其实只是被框挡着」的情况。
    if close_anon_modal(W, H):
        print("[reopen] 清掉匿名模态框后重试面板")
        if open_via_panel(W, H):
            return 0

    # ────────────────────────────────────────────────────────────────────
    # 搜索页路径（2026-09-27 停用，代码保留待续）
    #   if open_via_souyisou(W, H):
    #       return 0
    # 详见 open_via_souyisou 的停用说明。
    # ────────────────────────────────────────────────────────────────────

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
