#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""OPPO 商城「手机号快捷登录」全自动 —— 容器内运行。

什么时候需要：OPPO 商城的会话（`NEWOPPOSID` + `openid`）是**登录后**才有的；
未登录时小程序照常能开、页面照常渲染，但它**不会**在任何请求头里带会话字段，
所以 `wxoppo.py` 的两条路都拿不到东西（实测：`两条路都没拿到 NEWOPPOSID`）。
绑定在**服务端**、按 openid 记，所以每个微信号一辈子只需跑一次本脚本；
之后日常取会话走 `wxoppo.py`，完全不需要它。

实测链路（2026-09-27 云微容器，OPPO 商城 wx9c825da1a7ba062e）：
    ① 「我的」页（ctx 文字含「个人中心」）→ 点「登录」
    ② 登录页（ctx 文字含「手机号快捷登录」）→ **先勾选协议**（不勾点不动）→ 点「手机号快捷登录」
    ③ 微信**原生** getPhoneNumber 授权框 → 点「允许」
    ④ 回到「我的」页、出现用户信息 = 登录成功

## 两个必须记住的坑

**坑 1：登录页/弹层在另一个渲染面。**
`wxdom.find_dom_ctx()` 是「谁元素多选谁」—— 在「个人中心」页开着的时候它必然选到
**个人中心那个 ctx**（实测 ctx 5，63 个节点），而**登录页在另一个 ctx**（实测 ctx 7）。
在里面找「手机号快捷登录」当然找不到 —— 这不是「元素不存在」，是「找错了地方」。
（与 `wxqm_auth` 里踩的是同一个坑，那边是 ctx=6 / ctx=9。）
所以本脚本一律用 `ctx_with_text()` **按页面文字动态找 ctx**，不写死 ctx 号 ——
ctx 号每次打开都会变。

**坑 2：只有「微信原生允许框」能用像素，其余一律按文案。**
原生框不在小程序 DOM 里，只能像素定位（复用 `wxqm_auth.find_allow_btn`：
它的判据是「白卡里的绿色横向主段」，且阈值按**卡片宽**算而不是窗口宽）。
而小程序自己的按钮/勾选框**绝不按颜色找** —— 那是 2026-09-26 被明确否决的做法
（换一家商户/换一次活动主题色就失效）。
"""
import sys
import time

sys.path.insert(0, "/tmp")
import wxdom
import wxqm_auth
import wxreg

LOGIN_KW = "手机号快捷登录"      # 登录页的招牌文字
MINE_KW = "个人中心"             # 「我的」页的招牌文字

# 协议行的兜底关键词（实测文案：「勾选表示您已同意OPPO商城使用协议、隐私政策。」）。
# 勾选框逻辑交给 `wxqm_auth._check_agree` —— **只留那一份实现**，
# 本文件只提供商户自己的文案（选择器是通用的，不需要覆盖）。
OPPO_AGREE_WORDS = ("已同意", "使用协议", "隐私政策")

EXPR_TEXT = ("(function(){try{return ((document.body&&document.body.innerText)||'')"
             ".replace(/\\s+/g,' ')}catch(e){return 'ERR'}})()")


def ctx_with_text(ws, kw, prefer=None):
    """找页面文字含 `kw` 的 ctx（`kw` = 字符串，或「全部都要含」的元组）。返回 0 = 没有。

    ⚠️ 实现**只有一份，在 `wxdom.find_ctx_by_text`** —— 那里用**广播**：一口气全发、统一收，
    不管有多少 ctx 都只要一轮（~3 秒）。

    这里曾经自己写过一份「逐个 ctx `evaluate` + 3 秒超时」的版本，**是个性能陷阱**：
    不存在的 ctx CDP **根本不回包**，每次都要干等到 socket 超时。
    24 个 ctx 搜一次 ≈ 70 秒，脚本连续搜三次 → **250 秒都没打出第一行**，
    被外层 `timeout` 杀掉（rc=124）时**日志一片空白** ——
    现场看起来像「连不上 CDP」，其实只是慢死，极难判断。
    """
    return wxdom.find_ctx_by_text(ws, kw, prefer=prefer)


def click_text(ws, ctx, want, what="", pick_last=False):
    """在 `ctx` 里按**文案**点一个元素；成功返回 True。

    ⚠️ 同文案会命中好几层（组件根 / 内层容器 / 叶子），`rect_of` 已按**面积升序**排，
    所以默认取 `items[0]`（最小盒子 = 最接近真按钮）。
    `pick_last=True` 时取最大的那个 —— 用于「整行可点、文字在行内」的情况。
    """
    r = wxdom.rect_of(ws, ctx, (want,))
    items = (r or {}).get("items") or []
    if not items:
        print("[oppo-auth] ✗ 在 ctx %d 里找不到「%s」%s" % (ctx, want, ("（" + what + "）") if what else ""))
        return False
    it = items[-1] if pick_last else items[0]
    print("[oppo-auth] 点%s「%s」(%s %dx%d @%d,%d)"
          % (what or "", it["text"][:16], it["tag"], it["w"], it["h"], it["cx"], it["cy"]))
    wxreg.click(it["cx"], it["cy"])
    return True


def _diff_ratio(a, b):
    """两张 RGB bytes 的画面差异比例（0~1）。每 7 个像素采一次 —— 够用且快。"""
    if not a or not b or len(a) != len(b):
        return 1.0
    step = 3 * 7
    n = d = 0
    for i in range(0, len(a) - 3, step):
        n += 1
        if (abs(a[i] - b[i]) + abs(a[i + 1] - b[i + 1]) + abs(a[i + 2] - b[i + 2])) > 24:
            d += 1
    return d / float(n or 1)


def snapshot():
    """抓一帧小程序窗口。给「原生层是否出现」的画面差异判据用。失败返回 None。"""
    try:
        W, H = wxreg.win_size()
        return wxreg.grab(W, H, win=True)
    except Exception:
        return None


def _diff_box(a, b, W, H, thr=24):
    """求两帧差异的**包围盒**（= 新出现的原生弹窗）。返回 (x0,y0,x1,y1) 或 None。

    这是**结构性**判据：弹窗是画面上唯一新出现的东西，不需要任何颜色/品牌假设。
    """
    x0, x1, y0, y1 = W, -1, H, -1
    for y in range(0, H, 2):
        base = y * W * 3
        left = right = None
        n = 0
        for x in range(0, W, 2):
            i = base + x * 3
            if (abs(a[i] - b[i]) + abs(a[i + 1] - b[i + 1]) + abs(a[i + 2] - b[i + 2])) > thr:
                n += 1
                if left is None:
                    left = x
                right = x
        if left is None or n * 2 < W * 0.05:      # 该行变化太少 → 当噪声跳过
            continue
        y0 = min(y0, y)
        y1 = max(y1, y)
        x0 = min(x0, left)
        x1 = max(x1, right)
    if x1 < 0 or y1 < 0 or (y1 - y0) < 10:
        return None
    return (x0, y0, x1, y1)


def find_allow_in(buf, W, H, box):
    """在 `box` 内找**绿色横向主段**（微信原生「允许」按钮），返回页面坐标或 None。

    ⚠️ 为什么必须把范围锁在 `box` 内：`wxqm_auth.find_allow_btn` 的判据是
    「白卡里的绿色横段」，而 **OPPO 登录页的绿色圆 logo 也满足它**
    （页面背景是白的，穿过 logo 的行白度正好落在 `white_card` 的 30%~95% 区间，
    logo 自身就是绿色横段）→ 两次实测它都返回**logo 中心**，脚本去点 logo
    （现场表现：「他点了上面的 OPPO 图标，按道理不应该要点的」）。
    `box` 是「两帧差异」求出来的**新出现区域**，logo 在 box 之外，天然被排除。
    """
    if not box:
        return None
    bx0, by0, bx1, by1 = box
    col = {}
    for x in range(bx0, bx1 + 1):
        n = 0
        for y in range(by0, by1 + 1, 2):
            i = (y * W + x) * 3
            if wxreg.is_green((buf[i], buf[i + 1], buf[i + 2])):
                n += 1
        if n >= 2:
            col[x] = n
    if not col:
        return None
    xs = sorted(col)
    runs, cur = [], [xs[0]]
    for x in xs[1:]:
        if x - cur[-1] <= 30:                     # 按钮上的白字会把绿段切断，合并回来
            cur.append(x)
        else:
            runs.append(cur)
            cur = [x]
    runs.append(cur)
    best = max(runs, key=len)                     # 最宽的绿段 = 「允许」（「取消」在右、是灰的）
    if (best[-1] - best[0]) < (bx1 - bx0) * 0.20:
        return None
    ys = []
    for x in best:
        for y in range(by0, by1 + 1, 2):
            i = (y * W + x) * 3
            if wxreg.is_green((buf[i], buf[i + 1], buf[i + 2])):
                ys.append(y)
    if not ys:
        return None
    return ((best[0] + best[-1]) // 2, (min(ys) + max(ys)) // 2)


def wait_allow(ws, wait=20.0, before=None):
    """等微信**原生**「允许」框并点掉。

    ⚠️⚠️ 2026-09-27 治本：**不能一上来就拿像素找「允许」** —— 实测在 OPPO 登录页上
    `wxqm_auth.find_allow_btn` 会**误判**：OPPO 的**绿色圆形 logo** 正好构成
    「白卡里的绿色横段」（页面背景是白的 → 穿过 logo 的那些行，白度落在
    `white_card` 的 30%~95% 宽度区间 → 被当成白卡；logo 自身就是绿色横段，
    宽度也过得了 0.25×卡宽 的阈值）→ 于是它返回 **logo 中心**，脚本去点 logo
    （现场表现：「他点了上面的 OPPO 图标，按道理不应该要点的」）。
    颜色判据跨品牌必然失效 —— 2026-09-26 已经栽过一次，这是第二次。

    改法：**先用画面差异确认原生层真的出现了，再去找按钮**。
    原生弹窗会大面积盖住页面（实测约占窗口 18% 面积），画面差异是**结构性信号**，
    不依赖任何颜色/品牌的假设；没有差异就说明原生层还没出现 → 什么都不点。

    `before`：点「手机号快捷登录」**之前**抓的那一帧；不给就跳过这层保护。
    """
    t0 = time.time()
    while time.time() - t0 < wait:
        W, H = wxreg.win_size()
        try:
            buf = wxreg.grab(W, H, win=True)
        except Exception as e:
            print("[oppo-auth] 截屏失败：%s" % e)
            return False
        box = None
        if before is not None:
            r = _diff_ratio(before, buf)
            if r < 0.05:
                time.sleep(1.0)          # 画面几乎没变 → 原生层还没出现，不猜
                continue
            box = _diff_box(before, buf, W, H)
            if not box:
                time.sleep(1.0)
                continue
            print("[oppo-auth] 画面变化 %.0f%% → 原生层包围盒 x%d..%d y%d..%d"
                  % (r * 100, box[0], box[2], box[1], box[3]))
        hit = find_allow_in(buf, W, H, box) if box else wxqm_auth.find_allow_btn(buf, W, H)
        if hit:
            print("[oppo-auth] 点微信原生「允许」(%d,%d)" % hit)
            wxreg.click(hit[0], hit[1])
            time.sleep(2.5)
            return True
        time.sleep(1.0)
    print("[oppo-auth] ⚠️ 等了 %.0fs 没看到原生「允许」框（可能之前已授权过 → 微信静默返回）" % wait)
    return False


def logged_in(ws):
    """是否已登录。True=已登录 / False=仍显示「登录账号」/ None=判不了（找不到「我的」页）。

    ⚠️ 判据用「我的」页的**文字**（未登录时页面上有「登录账号」四个字，登录后会变成
    昵称/手机号）—— **结构性判据**，不依赖颜色，也不依赖坐标。
    """
    ctx = ctx_with_text(ws, MINE_KW)
    if not ctx:
        return None
    t = str(wxdom.evaluate(ws, EXPR_TEXT, ctx=ctx, timeout=6.0) or "")
    if not t or t.startswith("ERR"):
        return None
    return "登录账号" not in t


def probe_login(ws, ctx):
    """把登录页的真实 DOM 结构打出来（**只读，不点击**）。

    用途：定「协议勾选框」和「登录按钮」的定位方式时**先看实际结构**，
    别回到「拿旁边小字的左边界往左推 22 像素」那种猜偏移量的做法 ——
    2026-09-26 已明确否决（换个字号/内边距就偏）。
    """
    t = str(wxdom.evaluate(ws, EXPR_TEXT, ctx=ctx, timeout=6.0) or "")
    print("[probe] === 登录页 ctx=%d ===" % ctx)
    print("[probe] 页面文字：%s" % t[:260])
    print("[probe] --- 按文案取几何 ---")
    for kw in (LOGIN_KW,) + OPPO_AGREE_WORDS:
        r = wxdom.rect_of(ws, ctx, (kw,))
        items = (r or {}).get("items") or []
        print("[probe]   「%s」命中 %d 个（视口 %sx%s）"
              % (kw, len(items), (r or {}).get("vw"), (r or {}).get("vh")))
        for it in items[:5]:
            print("[probe]      %-14s cls=%-34s %dx%d @%d,%d"
                  % (it["tag"], str(it.get("cls"))[:34], it["w"], it["h"], it["cx"], it["cy"]))
    print("[probe] --- 勾选框候选选择器（wxqm_auth._check_agree 用的就是这批）---")
    for sel in ("[class*=checkbox]", "[class*=circle]", "[class*=agree]",
                "[class*=check]", "[class*=xuanze]", "input[type=checkbox]"):
        try:
            c = wxdom.clickable(ws, ctx, sel, timeout=8.0)
        except Exception as e:
            print("[probe]   %-24s 异常 %s" % (sel, e))
            continue
        items = (c or {}).get("items") or []
        print("[probe]   %-24s 命中 %d" % (sel, len(items)))
        for it in items[:5]:
            print("[probe]      %-14s cls=%-34s %dx%d @%d,%d"
                  % (it["tag"], str(it.get("cls"))[:34], it["w"], it["h"], it["cx"], it["cy"]))


def ensure_login_page(ws, page_ctx):
    """走到「手机号快捷登录」页，返回 login_ctx（0 = 失败）。**幂等**，已在登录页就直接返回。

    为什么抽成函数：流程中间要按 Escape 清微信原生层，那一下**有可能把半屏登录页
    也一起关掉**，于是得重走一遍导航。抽出来免得写两份 —— 这项目已经因为
    「同一逻辑两处实现」栽过好几次（面板清理、匿名框判据、投递清单）。

    导航实测（OPPO 商城）：
        首页（ctx 4，innerText 1159 字全是商品名）
          → 点**底部导航「我的」**（注意：底部导航是 `wx-cover-view`，
             **不在 innerText 里**，只能用 `rect_of` 按文案点）
          → 「我的」页（有「个人中心」「登录账号」）
          → 点「登录」→ 登录页（有「手机号快捷登录」，**半屏页**）
    """
    login_ctx = ctx_with_text(ws, LOGIN_KW)
    if login_ctx:
        print("[oppo-auth] 已在登录页：ctx=%d" % login_ctx)
        return login_ctx

    mine_ctx = ctx_with_text(ws, MINE_KW)
    if not mine_ctx:
        nav_ctx = page_ctx or ctx_with_text(ws, "为你推荐")
        if not nav_ctx:
            print("[oppo-auth] ✗ 定位不到页面 ctx —— 小程序开着吗？")
            return 0
        print("[oppo-auth] 当前停在首页（页面 ctx=%d）→ 点底部「我的」" % nav_ctx)
        wxreg.refresh_page(ws, nav_ctx)
        if not click_text(ws, nav_ctx, "我的", what="底部导航「我的」"):
            return 0
        time.sleep(3.5)
        mine_ctx = ctx_with_text(ws, MINE_KW)
        if not mine_ctx:
            print("[oppo-auth] ✗ 点了「我的」也没进个人中心")
            return 0

    print("[oppo-auth] 在「我的」页（ctx=%d）→ 点「登录」" % mine_ctx)
    wxreg.refresh_page(ws, mine_ctx)
    if not click_text(ws, mine_ctx, "登录", what="「登录」"):
        return 0
    time.sleep(3.0)
    login_ctx = ctx_with_text(ws, LOGIN_KW)
    if not login_ctx:
        print("[oppo-auth] ✗ 点「登录」后没等到登录页")
        return 0
    print("[oppo-auth] 登录页已打开：ctx=%d" % login_ctx)
    return login_ctx


def main():
    # ⚠️ `open_dom` 的第二个返回值就是**小程序页面**的渲染 ctx（`find_dom_ctx` 选的）。
    #    点底部导航必须用它，**不能靠 innerText 找底部导航** —— 实测底部导航是
    #    `wx-cover-view`，文字**根本不在 `document.body.innerText` 里**：
    #    首页 innerText 1159 字全是商品名，一个「首页/分类/门店/权益/我的」都没有；
    #    但 `rect_of()` 那套「遍历元素读 textContent」能读到（实测能定位到 20x14 的文字节点）。
    #    所以规矩是：
    #      · 判断「我现在在哪一页」 → 用 innerText（`find_ctx_by_text`）
    #      · 点底部导航这类 cover-view → 在页面 ctx 上用 `rect_of`（`click_text`）
    ws, page_ctx = wxdom.open_dom(limit=80)
    if not ws:
        print("[oppo-auth] ✗ 连不上 CDP（hook 挂上了吗？小程序开着吗？）")
        return 2
    print("[oppo-auth] 小程序页面 ctx=%d" % page_ctx)

    wxreg.raise_miniapp()

    login_ctx = ensure_login_page(ws, page_ctx)
    if not login_ctx:
        return 3

    # ── 探测模式：到登录页就停，把真实 DOM 打出来（只读，不点击）──
    if "--probe" in sys.argv[1:]:
        probe_login(ws, login_ctx)
        return 0

    # ── ⓪.5 清掉可能遗留的微信原生层，拿到**干净的参照帧** ──
    #    ⚠️ 为什么必须做：微信原生弹窗是**画在小程序窗口里面**的（实测不是独立 X 窗口，
    #    `xwininfo -children` 看不到），而且它是**模态**的 ——
    #    上一轮失败时若把它留着，`wait_allow` 的「两帧差异」判据就**整体失效**
    #    （参照帧里已经有弹窗 → 之后怎么等都被判成「没变化」）。实测就卡死在这一步。
    #    实测 **Escape 就能关掉它**（弹窗消失、登录页保留）。
    #    Escape 有极小概率把**半屏登录页**也一起关掉，所以之后重新确认一次；
    #    不在登录页就重走导航（`ensure_login_page` 是幂等的）。
    wxreg.raise_miniapp()
    time.sleep(0.4)
    wxreg.run("DISPLAY=%s xdotool key Escape" % wxreg.DISPLAY)
    time.sleep(1.2)
    if not ctx_with_text(ws, LOGIN_KW):
        print("[oppo-auth] Escape 之后不在登录页了 → 重新走一遍导航")
        login_ctx = ensure_login_page(ws, page_ctx)
        if not login_ctx:
            return 3
    else:
        login_ctx = ctx_with_text(ws, LOGIN_KW)

    # ── ② 点「手机号快捷登录」—— **先不勾协议，先试一次** ──
    #    为什么要「先试再勾」：那个协议勾选框是**两态**的（未勾/已勾是两个类名，而且
    #    圆圈里**没有文案**），我们**无法可靠地知道它当前是哪一态** ——
    #    所以「无条件先点一下勾选框」这种写法迟早会把「本来就勾着」的点成取消。
    #    改成：先点登录 → 没成功才去勾 → 再点一次。这样对两态**完全免疫**。
    wxreg.refresh_page(ws, login_ctx)
    before = snapshot()
    if not click_text(ws, login_ctx, LOGIN_KW, what="「手机号快捷登录」"):
        return 3
    wait_allow(ws, wait=8.0, before=before)

    if not logged_in(ws):
        print("[oppo-auth] 第 1 次点登录没成功 → 多半是协议没勾，补勾后重试")
        # 勾选框逻辑**复用 wxqm_auth._check_agree**（它含「先看已勾态」那一步）。
        # 本文件只提供商户自己的协议文案，选择器跨商户通用。
        wxqm_auth._check_agree(ws, login_ctx, agree_words=OPPO_AGREE_WORDS)
        time.sleep(0.8)
        login_ctx = ctx_with_text(ws, LOGIN_KW) or login_ctx   # 勾选后 DOM 重排 → 坐标重取
        before = snapshot()
        if not click_text(ws, login_ctx, LOGIN_KW, what="「手机号快捷登录」（第 2 次）"):
            return 3
        wait_allow(ws, wait=15.0, before=before)

    # ── ④ 验证 ──
    time.sleep(3.0)
    ok = logged_in(ws)
    if ok:
        print("[oppo-auth] ✓ 登录成功（「我的」页已不是「登录账号」状态）")
        return 0
    if ok is False:
        print("[oppo-auth] ✗ 「我的」页仍显示「登录账号」→ 未登录")
        mctx = ctx_with_text(ws, MINE_KW)
        if mctx:
            t = str(wxdom.evaluate(ws, EXPR_TEXT, ctx=mctx, timeout=8.0) or "")
            print("[oppo-auth]   页面文字前 160 字：%s" % t[:160])
        return 1
    print("[oppo-auth] ? 找不到「我的」页，无法判断（授权框可能已点，去跑 wxoppo.py 验证会话）")
    return 4


if __name__ == "__main__":
    sys.exit(main())
