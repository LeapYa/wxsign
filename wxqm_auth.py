#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""企迈（qmai）小程序「首次手机号授权」全自动 —— 容器内运行。

什么时候需要：某微信账号**第一次**接入某个企迈小程序（企迈不认这个 openid）。
绑定在**服务端**绑 openid、**永久有效**，所以每个品牌一辈子只需要跑一次；
之后日常签到走 wxsign.py，完全不需要本脚本。

实测链路（2026-09-26 云微容器；**呷哺呷哺 + 李先生牛肉面大王两个品牌都跑通**）：
    ① 站到目标页（页面要有 `popAuthorization` 方法 —— 「我的」页或签到页都行）
    ② CDP 调 `popAuthorization()`   → 弹「欢迎加入<品牌>」授权层
    ③ 点复选框                       → 不勾选的话，下一步会被「请阅读并同意」拦下
    ④ 点「手机号一键登录」            → 触发 wx.getPhoneNumber
    ⑤ 微信原生「允许」框 → 点「允许」 → **只有首次才出现**；已授权过则微信静默返回，本步跳过

## 定位策略：**一个硬编码坐标都没有、一次颜色判断都没有**

③④ 走 **`wxdom` 按结构 + 文案找元素**（`find_ctx_with` / `rect_of` / `clickable`）：

    · 授权层是**普通 view 组件** —— 源码写得很清楚：
      `onAuthorization(){ this.selectComponent("#authorization").show() }`
      （`pluginMarketing/components/authorization-4e03c673`），
      渲染成 `<wx-std-authorization id="authorization">`。
      所以「按 `#authorization` 选择器 + 文案」就能定位，
      与主题色、按钮形状、商户自定义样式**全都无关** ——
      呷哺（橙）/ 李先生（蓝）/ 将来任何商户，同一套代码通吃。

    ⚠️ **曾经的错误做法（已废弃，别走回头路）**：
      1. 早期按**像素颜色**找按钮 —— 换一家商户就失效（企迈几百家商户，主题跟各自活动走）。
         我在这上面犯了三次，2026-09-26 被明确否决。
      2. 后来以为「授权层不在渲染层 DOM 里」→ 又回头写 `is_orange()` —— **这个前提是错的**。
         真相：授权层一直在渲染层，只是它渲染在**另一个渲染面**上。
         实测呷哺签到页弹授权层时：
             ctx=6  302 节点 / 161 个 wx-* 标签 / **没有授权层**   ← 底层页面
             ctx=9  221 节点 / 152 个 wx-* 标签 / **有授权层**     ← `#authorization` 在这
         两者 url 一样、title 都是 `Page-Frame`。而 `wxdom.find_dom_ctx()` 是「谁元素多选谁」，
         **必然选到 ctx=6** → 在里面找授权层当然找不到 → 我就误判成「不在渲染层」。
         **教训：不是「找不到」，是「找错了地方」。**

    ⭐ 所以正式姿势是 `find_ctx_with(["#authorization"])` ——
      **先说要找什么，再看哪个 ctx 有**，而不是「先挑一个 ctx 再在里面找」。

    · 勾选框**按选择器 `.i-circle` 直接定位**（它自己没有文案）。
      之前是「用旁边那行小字的左边界往左推 CHECK_GAP 像素」，本质还是猜偏移量。

⑤ 走像素 —— 微信**原生**框确实不在小程序 DOM 里（这一条**是**真的，与授权层不同），
  只能看颜色找绿按钮。但它也不靠写死坐标：判据是「白卡里的**绿色横向主段**」。
  **只有这一步允许用颜色**，因为微信原生框的配色由微信客户端决定，不随品牌/活动变。

**坐标系一律问页面自己**（`wxreg.refresh_page()` 读 CDP 的
`window.screenX/screenY/innerWidth/innerHeight`），不是猜窗口位置。
这一点决定了**侧边栏 / 分栏形态也成立** —— 小程序将来不再独立开窗、嵌进主窗口里时，
「按窗口标题找独立小程序窗口」会整个失效，但页面自己照样报得出位置与大小
（README「未来形态：侧边栏 / 分栏也兼容」）。

用法（容器内）：
    DISPLAY=:1 python3 wxqm_auth.py --title 呷哺呷哺          # 跑完整流程
    DISPLAY=:1 python3 wxqm_auth.py --title 呷哺呷哺 --dry-run # 只弹授权层，不点
退出码：0 = 走完（含「已授权过、静默跳过」）；3 = 没找到授权层/页面不对；4 = 连不上渲染层
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import wxdom
    import wxreg
    import wxwin
except ImportError:
    sys.path.insert(0, "/tmp")
    import wxdom                                      # noqa: E402
    import wxreg                                      # noqa: E402
    import wxwin                                      # noqa: E402

from wxcdp import WS                                  # noqa: E402

# 授权层主按钮的**文案**（不是坐标、不是颜色）。各品牌文案可能略有差异，所以给几个变体。
LOGIN_WORDS = ("手机号一键登录", "一键登录", "手机号登录", "授权登录", "立即登录", "登录")
# 授权层根节点的**选择器**。企迈统一用 `#authorization`（源码 `selectComponent("#authorization")`），
# 但换个活动类型也可能是别的 id，所以给几个候选 + 文案兜底。
AUTH_SELECTORS = ("#authorization", "[class*=authorization]", "[class*=auth-pop]",
                  "[class*=auth-layer]")
# 授权层的**标志文案** —— 用来确认「它真弹出来了」，也用来兜底找它的 ctx。
AUTH_MARKS = ("手机号一键登录", "一键登录", "暂时跳过", "欢迎加入")
# 勾选框的**选择器**（它自己没文案，所以只能按结构找）。
# ⚠️⚠️ 2026-09-26 实测：勾选框的**两态是两个不同的类名**，不是一个类的两种样式 ——
#   未勾选 = `.i-circle`（空心圆），勾选后 = `.i-xuanze_xuanzhong`（选择_选中，实心勾）。
#   所以只写 `.i-circle` 会有个**隐蔽的坑**：如果它**本来就已勾上**（上一次弹层留下的状态、
#   或用户手点过），`.i-circle` 命中 0 个 → 脚本误以为「选择器都不行」→ 掉进下面那个
#   「按说明文字反推」的兜底，反推出 `(6,590)` 这种明显错误的坐标**乱点**。
#   实测就是这样：跑出来 `[auth] 勾选框（兜底推算）(页面坐标 6,590)`。
#   → 因此两态都要认：`CHECKED_SELECTORS` 用来判断「已经勾好了、跳过即可」。
CHECK_SELECTORS = (".i-circle", "[class*=circle][class*=check]", "[class*=agree-icon]",
                   "[class*=checkbox]")
# 已勾选态（`i-xuanze_xuanzhong` = 选择_选中）。命中的意思是「无需再点」。
CHECKED_SELECTORS = (".i-xuanze_xuanzhong", "[class*=xuanze][class*=xuanzhong]")
# 兜底：勾选框没有可识别选择器时，用「同意说明那行小字的左边界往左推」。
AGREE_WORDS = ("允许我们在必要场景", "合理使用您的个人信息", "请阅读并同意")
CHECK_GAP = 22          # 该兜底路径下，勾选框圆心到那行小字左边界的距离（视口像素）


def appid_of(title):
    """窗口标题 → appId（查 brands.json 的 miniapp / name / keyword）。查不到返回 ''。"""
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "brands.json")
    try:
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
    except Exception:
        return ""
    bs = d.get("brands", d) if isinstance(d, dict) else d
    for b in (bs if isinstance(bs, list) else bs.values()):
        for k in ("miniapp", "name", "keyword"):
            if b.get(k) and b.get(k) == title:
                return b.get("appid", "")
    return ""


def cdp_pop(want_appid="", wait=6.0):
    """在小程序逻辑层调 popAuthorization()，让授权层弹出来。

    返回 'ok:<route>' / 'no-method:<route>' / 'ERR:…'。

    ⚠️ 两个必须过滤的地方（都是实测踩出来的）：

    1. **contextId 1..80 是盲撒的**，渲染层那些 ctx 里没有 `wx`，会回
       `ERR:ReferenceError: getCurrentPages is not defined` → 只认逻辑层那种
       `ok:` / `no-method:` 开头的返回，别的当兜底。
    2. **同开两个小程序时，ctx 里会同时有它们各自的逻辑层**（实测：呷哺在签到页、
       李先生停在「我的」页，结果先命中李先生的 `no-method:pages/user/index`，
       脚本照样报「本站不是签到页」）。所以**必须按 appId 过滤**。
    """
    guard = ""
    if want_appid:
        guard = ("if(wx.getAccountInfoSync().miniProgram.appId!==%s)return 'skip';"
                 % json.dumps(want_appid))
    js = ("(function(){try{" + guard +
          "var p=getCurrentPages();var c=p[p.length-1];"
          "if(typeof c.popAuthorization!=='function')return 'no-method:'+c.route;"
          "c.popAuthorization();return 'ok:'+c.route}catch(e){return 'ERR:'+e}})()")

    ws = WS()
    fallback = ""
    try:
        ws.send({"id": 1, "method": "Runtime.enable", "params": {}})
        time.sleep(0.4)
        for c in range(1, 81):
            ws.send({"id": 1000 + c, "method": "Runtime.evaluate",
                     "params": {"expression": js, "returnByValue": True, "contextId": c}})
        t0 = time.time()
        while time.time() - t0 < wait:
            try:
                m = json.loads(ws.recv_msg())
            except Exception:
                break
            mid = m.get("id")
            if isinstance(mid, int) and 1000 < mid < 2000:
                v = ((m.get("result") or {}).get("result") or {}).get("value")
                if isinstance(v, str) and v:
                    if v.startswith("ok:") or v.startswith("no-method:"):
                        return v
                    if not fallback:
                        fallback = v
    finally:
        ws.close()
    return fallback


def find_allow_btn(buf, W, H):
    """找微信原生手机号授权框的「允许」→ (cx, cy) **页面坐标**；没框返回 None。

    判据：白卡里的**绿色横向主段**。

    ⚠️ 两条实测教训（第一版都踩了）：
      1. **不能取绿色 bbox 的中心** —— bbox 会把右边「取消」的左半连进来，
         中心正好落在两个按钮**中间的空隙**上（点了没反应）。
      2. **不能用「占窗口宽」当阈值** —— 第一版用 `0.12 * W` 太松，把呷哺签到页上
         那颗绿色「接受预定」小按钮当成了「允许」，点下去从签到页跳到门店列表页，
         而脚本还报「✓ 授权完成」（静默跑偏）。要用「占**卡片**宽」。
    """
    card = wxreg.white_card(buf, W, H)
    if not card:
        return None
    y0, y1 = card[1], card[3]

    col = {}
    for x in range(card[0], card[2]):
        n = 0
        for y in range(y0, y1, 2):
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
        # 间隙容差 30：把「允许」内部被白字切断的绿列合并回来（实测断成 119+24）。
        # 而「允许」与右侧「取消」之间隔约 49px 灰底 → 不会被误并。
        if x - cur[-1] <= 30:
            cur.append(x)
        else:
            runs.append(cur)
            cur = [x]
    runs.append(cur)
    best = max(runs, key=len)                 # 最宽的绿色横段 = 「允许」（「取消」在右、是灰的）

    # 阈值 0.25 同时覆盖微信的两种布局：**并排**（允许+取消，绿段≈0.34 卡宽）
    # 与**通栏**（号码列表那种，≈0.53）；而页面上的小绿按钮只有 ≈0.10。
    card_w = card[2] - card[0]
    if (best[-1] - best[0]) < card_w * 0.25:
        return None

    ys = []
    for x in best:
        for y in range(y0, y1, 2):
            i = (y * W + x) * 3
            if wxreg.is_green((buf[i], buf[i + 1], buf[i + 2])):
                ys.append(y)
    if not ys:
        return None
    return ((best[0] + best[-1]) // 2, (min(ys) + max(ys)) // 2)


def find_auth_ctx(ws, limit=80, visible=False):
    """找**授权层所在的**渲染层 ctx —— 先按选择器，再按标志文案兜底。返回 0 = 没找到。

    ⭐ 这个函数是整条链路的枢纽。踩过的坑（务必别再踩）：

      · **不能**用 `wxdom.find_dom_ctx()` —— 它挑「元素最多的那个 ctx」，
        而授权层所在的那个 ctx **元素更少**（实测 221 vs 底层 302），必被漏掉。
      · 授权层在弹出时**新建/启用了一个渲染面**，与底层页面 url 相同、title 相同
        （都是 `Page-Frame`），只有里面的节点不同。所以只能「按内容找 ctx」。

    ⚠️ `visible=True` 时只认**当前真的可见**的（`#authorization` 关闭后节点**永久留在
    DOM 里**，只按选择器找会误判成「授权层还开着」）。判断「有没有弹出」要用这个。

    ⚠️ **选择器要一次全传进去**（2026-09-26 修正性能坑）：`find_ctx_with` 本来就收列表，
    而每次调用都要**盲撒 80 个 ctx 再等 12 秒收响应** —— 早先写成 `for sel in ...` 逐个查，
    4 个选择器就是 4 次全量广播 ≈ 48 秒，在真机上表现为「脚本像卡死了」。
    同一个 ctx 内这些选择器是**或**的关系，合起来查语义完全一样、只花一次的钱。
    """
    hits = wxdom.find_ctx_with(ws, AUTH_SELECTORS, limit=limit, visible=visible)
    if hits:
        ctx = max(hits, key=hits.get)
        print("[auth] 授权层 ctx=%d（选择器命中 %d 处%s）"
              % (ctx, hits[ctx], "（可见）" if visible else ""))
        return ctx
    if visible:
        return 0
    # 兜底：谁能扫出授权层的标志文案，就是它
    hits = wxdom.find_ctx_with(
        ws, ["[class*=std-popup]", "[class*=popup]"], limit=limit)
    for ctx in sorted(hits, key=hits.get, reverse=True):
        d = wxdom.scan(ws, ctx)
        if d and any(w in wxdom.page_text(d) for w in AUTH_MARKS):
            print("[auth] 授权层 ctx=%d（按标志文案命中）" % ctx)
            return ctx
    return 0


def auth_layer_visible(ws, deep=False):
    """授权层**此刻**是不是真的可见。返回 (visible, ctx)。

    用来区分两种「找不到按钮」：
      · 授权层压根没弹出来 → 该报错
      · 授权层弹了但已在关闭动画里 → 说明流程其实已经走完（比如已授权），不该再点

    ⚠️ **默认只走快路径**（一次选择器广播）。`deep=True` 才启兜底（按主按钮文案反推），
    因为兜底要为每个候选 ctx 各跑一次 `rect_of`，很贵 —— 实测在真机上会把脚本拖到超时。
    """
    hits = wxdom.find_ctx_with(ws, AUTH_SELECTORS, visible=True)
    if hits:
        return True, max(hits, key=hits.get)
    if not deep:
        return False, 0
    # 兜底：主按钮可见就算（选择器广播只做一次，别放进循环里重复付 12 秒）
    hits = wxdom.find_ctx_with(ws, ["wx-button", "wx-view"], visible=True)
    for ctx in sorted(hits, key=hits.get, reverse=True)[:3]:
        d = wxdom.rect_of(ws, ctx, LOGIN_WORDS, timeout=6.0)
        if d and d["items"]:
            return True, ctx
    return False, 0


def click_auth_layer(ws, ctx=0, dry_run=False):
    """按**结构 + 文案**点掉授权层：先勾选，再点「手机号一键登录」。

    全程用**页面坐标**（`wxreg.click` 自动换算成屏幕），所以窗口位置、
    以及将来变成侧边栏都不影响。**全程不问颜色** —— 见模块 docstring。

    `ctx` 由调用方先确认「授权层可见」后传进来（见 `auth_layer_visible`）。
    """
    ctx = ctx or find_auth_ctx(ws, visible=True)
    if not ctx:
        print("[auth] ✗ 找不到可见的授权层 —— 它弹出来了吗？（选择器 %s 都没命中）"
              % ", ".join(AUTH_SELECTORS))
        return False

    wxreg.raise_miniapp()
    wxreg.refresh_page(ws, ctx)            # ⭐ 问页面自己 → 侧边栏形态也成立

    # ── ④ 主按钮：按文案取「最小盒子」──
    # 同文案会命中好几层（组件根 410x379、内层容器…），**面积最小的才是按钮本身**。
    # ⚠️ `rect_of` 已排除「矩形塌成 0 / 中心在视口外」的节点 —— 那是在播**离场动画**。
    d = wxdom.rect_of(ws, ctx, LOGIN_WORDS)
    if not d or not d["items"]:
        print("[auth] 授权层里没有「可点的%s」→ 它可能正在关闭/已经关掉了" % LOGIN_WORDS[0])
        return False
    btn = d["items"][0]
    print("[auth] 主按钮：%s「%s」(%d,%d) %dx%d" % (
        btn["tag"], btn["text"][:14], btn["cx"], btn["cy"], btn["w"], btn["h"]))

    # ── ③ 先勾选（不勾下一步会被「请阅读并同意」拦下）──
    if not _check_agree(ws, ctx, dry_run):
        print("[auth] ⚠️ 没能定位勾选框 → 直接点登录，被拦下会自动重试")

    # 勾选后授权层会重建节点，坐标必刷一次（实测勾选会让 DOM 重排）
    if not dry_run:
        time.sleep(0.9)
        ctx = find_auth_ctx(ws, visible=True) or ctx
        d = wxdom.rect_of(ws, ctx, LOGIN_WORDS)
        if d and d["items"]:
            btn = d["items"][0]

    print("[auth] 点「%s」(页面坐标 %d,%d)" % (btn["text"][:14], btn["cx"], btn["cy"]))
    if not dry_run:
        wxreg.click(btn["cx"], btn["cy"])
    return True


def _check_agree(ws, ctx, dry_run=False):
    """勾选同意协议。**优先按选择器直接找那个圆圈**，选择器都没有才退回猜偏移量。

    为什么优先选择器：勾选框是个**没有文案的圆圈**，之前只能「拿旁边那行说明文字的
    左边界，往左推 22 像素」—— 那 22 是个魔数，换个字号/内边距就偏。
    实测企迈的圆圈是 `.i-circle`，`clickable()` 能直接拿到它的真实盒子
    （`(25,714) 19x52`，19 是圆的直径、52 是行高）。

    ⚠️⚠️ 2026-09-26 补：**两态是两个类名**，所以顺序必须是「先看已勾 → 再找未勾 → 才兜底」。
      未勾 = `.i-circle`（空心），已勾 = `.i-xuanze_xuanzhong`（实心勾）。
      少了第 ⓪ 步（已勾判断）时，若它本来就勾着，第 ① 步会在「未勾选择器」上命中 0 个 →
      掉进第 ② 步兜底 → 用离场/异常几何反推出 `(6,590)` 这种坐标**乱点**。
    """
    # ⓪ 先看**是不是已经勾好了** —— 命中即无需再点（这是上面那个坑的正面修法）
    for sel in CHECKED_SELECTORS:
        c = wxdom.clickable(ws, ctx, sel)
        if c and c["items"]:
            it = c["items"][0]
            print("[auth] 勾选框（%s）**已经是勾选态** → 跳过点击 (页面坐标 %d,%d)"
                  % (sel, it["cx"], it["cy"]))
            return True

    # ① 未勾选态：选择器直取（推荐路径）
    for sel in CHECK_SELECTORS:
        c = wxdom.clickable(ws, ctx, sel)
        if c and c["items"]:
            it = c["items"][0]
            print("[auth] 勾选框（%s）(页面坐标 %d,%d) %dx%d" % (
                sel, it["cx"], it["cy"], it["w"], it["h"]))
            if not dry_run:
                wxreg.click(it["cx"], it["cy"])
                time.sleep(0.8)
            return True

    # ② 兜底：用说明文字反推。
    # ⚠️ 兜底**必须先确认那行说明文字本身是可见的** —— 否则会拿离场动画里的假坐标乱点。
    #    实测踩过：授权层在播 `std-bottom-leave-to` 时说明文字还在树上、矩形塌成 0，
    #    反推出的坐标是 (6,590)，点下去完全是误操作。
    d = wxdom.rect_of(ws, ctx, AGREE_WORDS)
    if d and d["items"]:
        a = max(d["items"], key=lambda x: x["w"])
        # 说明文字在底部（视口下 2/3），这是授权层的固有形态；不满足就认为不是它
        if a["cy"] < d["vh"] * 0.6:
            print("[auth] ⚠️ 说明文字位置异常（y=%d / vh=%d）→ 不勾选，交给被拦后重试"
                  % (a["cy"], d["vh"]))
            return False
        # ⚠️ 反推出来的坐标必须**落在视口内**才算数。
        #    实测踩过：授权层在播 `std-bottom-leave-to`（离场）时说明文字还在树上、
        #    矩形塌成 0，反推出来是 `(6,590)` —— 那个 `6` 就是下面这行的 `max(6, …)` 夹出来的，
        #    点下去完全是误操作。所以宁可**不勾选**（交给「被拦后重试」），也别乱点。
        cxx = a["cx"] - a["w"] // 2 - CHECK_GAP
        if not (a["w"] > 0) or cxx < 4 or cxx > d["vw"] - 4:
            print("[auth] ⚠️ 兜底反推的勾选框坐标越界（x=%d / vw=%d）→ 不勾选，"
                  "交给被拦后重试" % (cxx, d["vw"]))
            return False
        print("[auth] 勾选框（兜底推算）(页面坐标 %d,%d)" % (cxx, a["cy"]))
        if not dry_run:
            wxreg.click(cxx, a["cy"])
            time.sleep(0.8)
        return True
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--title", default="", help="小程序窗口标题（同开多个小程序时指定目标）")
    ap.add_argument("--dry-run", action="store_true", help="只弹授权层，不做任何点击")
    ap.add_argument("--wait-allow", type=float, default=8.0, help="等原生授权框出现的秒数")
    a = ap.parse_args()

    if a.title:
        cand = [w for w in wxwin.windows() if w["title"] == a.title]
        m = cand[0] if cand else None
        if not m:
            print("[auth] ✗ 没找到窗口「%s」" % a.title)
            return 3
    else:
        m = wxwin.miniapp()
        if not m:
            print("[auth] ✗ 没有小程序窗口 —— 先让引擎把小程序打开")
            return 3
    print("[auth] 目标：%s" % m["title"])

    want = appid_of(m["title"])
    if not want:
        print("[auth] ⚠️ 标题「%s」在 brands.json 里查不到 appId → 不过滤，可能认错小程序"
              % m["title"])
    else:
        print("[auth] appId=%s" % want)

    # 连 CDP（**不预先挑 ctx** —— 挑 ctx 是 click_auth_layer 里按授权层内容找的）
    try:
        ws = WS(timeout=25)
    except Exception as e:
        print("[auth] ✗ 连不上 CDP：%s —— hook 通吗？" % e)
        return 4
    ws.send({"id": 1, "method": "Runtime.enable", "params": {}})
    time.sleep(0.4)

    # ② 弹授权层
    r = cdp_pop(want)
    print("[auth] popAuthorization → %s" % (r or "(无响应)"))
    if r.startswith("no-method"):
        print("[auth] ✗ 这个页面没有 popAuthorization —— 先站到「我的」页或签到页")
        return 3
    if r.startswith("ERR"):
        print("[auth] ✗ 调用失败：%s" % r)
        return 3
    time.sleep(2.0)

    if a.dry_run:
        vis, vctx = auth_layer_visible(ws)
        print("[auth] --dry-run：授权层可见=%s（ctx=%s），未做点击" % (vis, vctx or "?"))
        return 0

    # ③④ 点掉授权层。
    # ⚠️ 先确认它**真的可见** —— `#authorization` 组件关闭后节点会**永久留在 DOM 里**，
    #    只按选择器找会误判成「还开着」，然后拿离场动画里的假坐标乱点（实测踩过：
    #    点到 (6,590)，完全是误操作）。所以要分三种情况处理：
    vis, vctx = auth_layer_visible(ws)
    if not vis:
        show_ctx = find_auth_ctx(ws)          # 节点在、但不可见 = 已在关闭动画里
        if show_ctx:
            print("[auth] 授权层节点还在 ctx=%d 但已不可见 → 它正在关闭（该账号应已授权）" % show_ctx)
        else:
            print("[auth] 授权层没能弹出 → 该账号可能已授权，或页面/活动不对")
        print("[auth] 按「无需处理」继续走第 ⑤ 步（万一原生框真弹了还能兜住）")
        return _wait_allow(ws, a, want)

    # ③ 勾选 → ④ 「手机号一键登录」
    if not click_auth_layer(ws, ctx=vctx):
        return 3

    return _wait_allow(ws, a, want)


def verify_bound(ws, want_appid="", wait=6.0):
    """验证**服务端**是否真的绑上了手机号。返回 (bool, 说明)。

    ⚠️⚠️ 为什么必须有这一步 —— 这是本脚本曾经**假成功**的根因：
      旧版把「微信原生框没弹」当成「已授权过 → 成功」，但那只证明**微信侧**有记录，
      **完全不能证明企迈服务端把这个 openid 认成了会员**。实测踩到：
      脚本报「已授权过、静默返回」，而企迈服务端 `loginData.user.eMobile` **是空串**，
      签到接口回 `100027 当前渠道不能参与活动`（=「你不是本渠道有效会员」的委婉说法）。
      → 于是「第 11 个品牌跑通」的结论是错的，白白绕了一大圈。
      **教训：绑定动作的成败只能由「被绑的那一方」来确认，不能由「我方有没有弹窗」推断。**

    判据用**两层**，从权威到兜底：
      ① 服务端接口（最权威）：`userSignStatistics` 不再是 `100027`。
         —— 但这一步需要 token，而 token 由 wxqm.py 另取，耦合较重，故不在此做。
      ② storage 的 `loginData.user.eMobile` **非空**（本函数采用）。
         实测：绑定成功后它从 `""` 变成 `"Fyf8yS3lcAnSSmjhFBUjTg=="`。
         ⚠️ 它是 **base64 密文、不是明文手机号** —— 所以判据只能是「非空」，别去解它、
         也别指望拿它当手机号用。
    """
    JS = ("(function(){try{"
          "var d=wx.getStorageSync('loginData')||{};var u=d.user||{};"
          "var appid='';try{appid=wx.getAccountInfoSync().miniProgram.appId}catch(e){}"
          "return JSON.stringify({appid:appid,openId:u.eOpenId||'',"
          "mobile:u.eMobile||'',storeId:String((d.store||{}).id||'')})"
          "}catch(e){return 'ERR:'+e}})()")
    best = {}
    t0 = time.time()
    while time.time() - t0 < wait:
        for c in range(1, 41):
            ws.send({"id": 5000 + c, "method": "Runtime.evaluate",
                     "params": {"expression": JS, "returnByValue": True, "contextId": c}})
        got = {}
        t1 = time.time()
        while time.time() - t1 < 2.5:
            try:
                m = json.loads(ws.recv_msg())
            except Exception:
                break
            mid = m.get("id")
            if isinstance(mid, int) and 5000 < mid <= 5040:
                v = ((m.get("result") or {}).get("result") or {}).get("value")
                if isinstance(v, str) and v.startswith("{"):
                    try:
                        got = json.loads(v)
                    except ValueError:
                        pass
        if got and (not want_appid or got.get("appid") == want_appid):
            best = got
            if got.get("mobile"):
                return True, "服务端已绑定（eOpenId=%s…，eMobile 非空）" \
                    % (got.get("openId") or "")[:20]
        time.sleep(1.0)
    if not best:
        return False, "读不到 loginData（页面在吗？hook 通吗？）"
    return False, ("服务端**仍未绑定**：eMobile 为空（eOpenId=%s…）" % (best.get("openId") or "")[:20])


def _wait_allow(ws, a, want_appid=""):
    """第 ⑤ 步：等微信**原生**「允许」框并点掉。**只有首次接入才有。**

    ⚠️ 这一步**是**真的读不到 DOM（微信原生框不在小程序里，与授权层性质完全不同），
    只能用像素。但只有这一步允许用颜色，理由：它的配色由**微信客户端**决定，
    不随品牌/活动变 —— 与「品牌按钮不能按颜色找」不矛盾。

    ⚠️ 出口判据 = **服务端绑定结果**（见 `verify_bound`），不再是「原生框有没有弹」。
    没弹有两种可能，必须分开报，否则又回到「假成功」：
      (a) 原本就已绑过 → 真正成功；
      (b) 弹窗被遮挡/没点中 → 其实没绑上（旧版在这里误报成功）。
    """
    clicked = False
    t0 = time.time()
    while time.time() - t0 < a.wait_allow:
        wxreg.raise_miniapp()
        wxreg.refresh_page(ws, find_auth_ctx(ws, visible=True))
        W, H = wxreg.win_size()
        try:
            buf = wxreg.grab(W, H, win=True)      # 抓**页面区域**（几何来自页面自己）
        except Exception as e:
            print("[auth] ✗ 截屏失败：%s" % e)
            return 4
        hit = find_allow_btn(buf, W, H)
        if hit:
            print("[auth] 点原生「允许」(页面坐标 %d,%d)" % hit)
            wxreg.click(hit[0], hit[1])
            clicked = True
            time.sleep(2.5)
            break
        time.sleep(1.0)

    # ── 出口：一律以**服务端绑定结果**为准 ──
    ok, why = verify_bound(ws, want_appid)
    if ok:
        print("[auth] ✓ 手机号授权完成 —— %s" % why)
        return 0
    if clicked:
        print("[auth] ✗ 点了「允许」但服务端仍未绑定 —— %s" % why)
        print("[auth]   可能是点了但没生效（窗口被遮挡？），或该号已在别的 openid 上")
        return 3
    print("[auth] ✗ 没出现原生授权框，且服务端仍未绑定 —— %s" % why)
    print("[auth]   注：若该账号**本该已绑**，说明旧版「静默返回=成功」的判据是错的，"
          "需要人工确认一次")
    return 3


if __name__ == "__main__":
    sys.exit(main())
