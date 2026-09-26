#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""关掉所有「残留的小程序窗口」（容器内运行）。

为什么需要它：批量跑多个品牌时，只要有一次关窗失败（比如窗口还没渲染完就去点），
那个小程序窗口就会一直盖在微信主窗口上 —— 之后点侧边栏会点在它身上，
`open_panel` 就再也认不出小程序面板（实测踩过：两个残留窗口挡着，面板连开两次都失败）。

判据（与 reopen_miniapp 一致，绝不误伤）：
  · 只关「有窗口标题、且不是『微信』、且没有 WM_CLASS(wechat)」的窗口 = 小程序窗口
  · 微信主窗口（WM_CLASS 含 wechat）一律不碰
  · 关窗走微信自己的关闭按钮（三道防线），**绝不 xdotool windowclose**

还会**无条件**先扫一遍模态弹窗（`clear_modals`）—— 模态框会吞掉之后所有点击，
症状伪装成「小程序面板打不开」，很难联想到，所以每轮都查一次。

用法（容器内）：DISPLAY=:1 python3 wxclean.py [--restart-runtime]
  --restart-runtime  常规手段都关不掉时，**杀掉小程序运行时进程（WeChatAppEx）**。
                     ⚠️ 这会清掉所有小程序窗口，而且 hook 需要跟着重启（WMPFDebugger 是
                     启动时 attach 一次的）—— 主进程 wechat 与登录态**不受影响**，
                     比「重启微信」轻得多。实测这招能清掉用关闭按钮点不掉的卡死窗口。
退出码：0 = 全部清掉（或本来就没有）；1 = 有没关掉的
"""
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import wxopen as R
except ImportError:
    sys.path.insert(0, "/tmp")
    import wxopen as R


def leftover(wid, title):
    t = title.strip()
    if not t or t == "微信":
        return False
    if R.is_main_window(wid):        # WM_CLASS 含 wechat
        return False
    return True


def _px(buf, W, x, y):
    i = (y * W + x) * 3
    return buf[i], buf[i + 1], buf[i + 2]


def green_button(buf, W, H):
    """找「微信绿」**药丸按钮**的包围盒 → (x0, y0, x1, y1)；找不到返回 None。

    ⚠️ 加了形状判据（宽 60~240、高 18~70）：只按颜色找的话，**聊天窗口里自己的绿色
       气泡**（#95EC69）也会中招，于是「遣散弹窗」变成在聊天记录上乱点。
    ⚠️ 也刻意不取「绿色像素的质心」：质心会被附近别的小块绿色带偏，再按固定比例
       往左推一颗，布局一变就点到空白（旧实现就是这么错的）。
    """
    minx, maxx, miny, maxy, n = W, -1, H, -1, 0
    for y in range(int(H * 0.25), int(H * 0.70), 2):
        for x in range(int(W * 0.15), int(W * 0.85), 2):
            r, g, b = _px(buf, W, x, y)
            if g > 150 and g - r > 60 and g - b > 40:
                n += 1
                minx, maxx = min(minx, x), max(maxx, x)
                miny, maxy = min(miny, y), max(maxy, y)
    if n < 250:
        return None
    w, h = maxx - minx + 1, maxy - miny + 1
    if not (60 <= w <= 240 and 18 <= h <= 70):
        return None
    return minx, miny, maxx, maxy


def row_runs(buf, W, y, tol=6, min_len=6, merge_gap=30):
    """把一行像素切成「同色段」，并把被文字切断的段合回去。
    → [(x0, x1, (r,g,b)), ...]（按钮里的字会把它切两半，必须合，否则认不出按钮）"""
    runs = []
    for x in range(W):
        c = _px(buf, W, x, y)
        if runs and max(abs(c[k] - runs[-1][2][k]) for k in range(3)) <= tol:
            runs[-1][1] = x
        else:
            runs.append([x, x, c])
    merged = []
    for r in runs:
        if (merged and r[0] - merged[-1][1] <= merge_gap
                and max(abs(r[2][k] - merged[-1][2][k]) for k in range(3)) <= tol):
            merged[-1][1] = r[1]
        else:
            merged.append(r)
    return [r for r in merged if r[1] - r[0] + 1 >= min_len]


def _sibling_on_row(buf, W, box, y):
    """在指定那一行上找绿按钮的兄弟按钮 → (x0, x1) 或 None。判据见 sibling_button。"""
    x0, _, x1, _ = box
    cands = []
    for a, b, c in row_runs(buf, W, y):
        if a <= x1 and b >= x0:                            # 与绿按钮重叠 → 就是它自己
            continue
        if not (34 <= b - a + 1 <= 260):                   # 药丸按钮的合理宽度
            continue
        if max(c) - min(c) > 5 or not 205 <= sum(c) // 3 <= 245:
            continue
        if (b < x0 and x0 - b > 60) or (a > x1 and a - x1 > 60):
            continue                                       # 太远，不是它的兄弟
        cands.append((a, b))
    left = [c for c in cands if c[1] < x0]
    right = [c for c in cands if c[0] > x1]
    return (left[-1] if left else None) or (right[0] if right else None)


def sibling_button(buf, W, box):
    """绿按钮**同一行**上的兄弟按钮中心 → (x, y) 或 None。

    为什么不能固定「往左找一颗」（旧实现的 bug，代价是整轮白跑）：
      隐私弹窗   拒绝(左,灰) / 同意(右,绿)   → 往左 = 拒绝 ✓
      退出登录   确定(左,绿) / 取消(右,灰)   → 往左 = 点在空白上，框关不掉；
                 而它是**模态**的，之后所有点击都被它吞掉。症状伪装成
                 「小程序面板打不开 / 侧边栏点不动」，完全联想不到是这个框在挡
                 （实测因此把整批品牌跑成 notoken）。
    改成：左右都扫，点**不是绿色的那颗** —— 两种布局都成立，而且永远碰不到绿色（危险）那颗。

    三条约束都是从实测像素反推的（不然满屏都是「灰块」）：
      · **扫按钮上/下缘那两行**，不扫正中：按钮标签文字会把灰底切成两半，
        两半各自可能就窄于阈值了；贴着边缘扫读到的是一整条实心药丸
        （实测 y0+4：`644..755` 一整段；而在正中那行会被切成 `644..686` + `713..755`）。
      · **紧邻**：兄弟按钮的边必须在绿按钮 60px 以内，否则左侧栏（x0~60）、
        右侧阴影（x787~823）这些平灰块全都会被当成按钮。
      · **严格平灰**：逐通道极差 ≤ 5，排除聊天列表那种带蓝调、略不平的底色。
    """
    x0, y0, x1, y1 = box
    lines = []
    for y in (y0 + 4, y1 - 4, (y0 + y1) // 2):
        if y not in lines and y0 <= y <= y1:
            lines.append(y)
    for y in lines:
        got = _sibling_on_row(buf, W, box, y)
        if got:
            # ⚠️ 点击用**绿按钮的垂直中心**，而不是「扫到兄弟按钮的那一行」。
            #    **扫描**必须贴边缘（正中那行会被按钮标签文字切成两半），
            #    但**点击**必须落在**按钮中心**才稳 —— 两个按钮是同一行药丸、垂直对齐，
            #    所以用绿按钮的 yc 即可。
            #    实测（退出登录框）：按扫描行 y=538 点会 **miss**（比中心偏高约 12px，
            #    落在圆角/边缘外），框关不掉 → 模态框继续吞掉所有点击；
            #    改点中心 y≈548 立刻成功（手工验证 550 生效）。
            return (got[0] + got[1]) // 2, (y0 + y1) // 2
    return None


def find_anon_dialog():
    """找出「小尺寸匿名顶层窗口」（= 微信原生对话框）的窗口 id，没有则 None。

    判据：无名字 + 尺寸在 100~500 × 80~400 之间的顶层窗口 = 微信原生对话框
    （主窗口/面板都是 1280x1024；辅助窗口是 1x1/10x10/200x200，都被尺寸过滤掉）。
    """
    try:
        out = R.run("DISPLAY=%s xwininfo -root -children" % R.DISPLAY)
    except Exception:
        return None
    for line in out.splitlines():
        m = re.match(r"\s+(0x[0-9a-fA-F]+) \(has no name\):\s+\(\)\s+(\d+)x(\d+)\+", line)
        if not m:
            continue
        w, h = int(m.group(2)), int(m.group(3))
        if 100 <= w <= 500 and 80 <= h <= 400:
            return m.group(1)
    return None


def _activate_popup():
    """把「小尺寸匿名顶层窗口」（= 微信原生对话框）激活到最前。

    为什么**必需**（2026-09-25 实测）：`退出登录？` 框是**独立的匿名顶层窗口**，
    直接对它的坐标发 `xdotool click` **不生效** —— wxclean 连点三次框都不关
    （日志显示"点兄弟按钮（拒绝/取消）"，但框纹丝不动）。
    而「先 `xdotool windowactivate --sync <框>` 再点同一个坐标」**一次就成**。
    实测 Esc、Tab+Return 也都无效 —— 只有「先激活、再点击」有效。
    """
    wid = find_anon_dialog()
    if not wid:
        return False
    R.run("DISPLAY=%s xdotool windowactivate --sync %s" % (R.DISPLAY, wid))
    return True


def dismiss_popups(wid, W, H):
    """遣散挡在窗口上的**模态弹窗**（否则关窗按钮点不中、后续点击全被吞）。

    做法：在内容区找微信绿药丸按钮，点它同一行上的**兄弟按钮** ——
    隐私弹窗那颗是「拒绝」、退出登录那颗是「取消」，都是「不授权 / 不执行」的安全选项。

    ⚠️ **硬前置（2026-09-26 实测加的防线）**：只在一个「匿名原生对话框窗口」**确实存在**
    时才做像素扫描 + 点击。理由：`green_button()` 在主窗口内容区（尤其聊天列表 / 任意小程序
    背景）会把**普通绿元素**误判成「弹窗绿药丸」，于是 `sibling_button` 的点击落到侧边栏的
    「退出登录」那一行 —— **本来没有弹窗，却被点出一个「退出登录？」模态框**，之后所有点击
    全被它吞掉，整批品牌跑废（复现症状：`wxopen` 卡死、`miniapp=None`、日志停在 `[clean]`）。
    加了这道前置后，「无框不点」——没有再自己造框的可能。
    """
    if not find_anon_dialog():
        print("[clean] 没有匿名对话框 → 不做像素扫描（避免误点侧边栏造出弹窗）")
        return False
    R.raise_window(wid)
    time.sleep(0.6)
    buf = R.grab(W, H)
    box = green_button(buf, W, H)
    if not box:
        print("[clean] 没检测到弹窗，发一次 Esc 清掉悬浮提示")
        R.key("Escape", 1.0)
        return False
    gx, gy = (box[0] + box[2]) // 2, (box[1] + box[3]) // 2
    sib = sibling_button(buf, W, box)
    if sib:
        print("[clean] 检测到弹窗绿色按钮 (%d,%d) → 点兄弟按钮（拒绝/取消）(%d,%d)"
              % (gx, gy, sib[0], sib[1]))
        # ⚠️ 先激活弹窗**再**点 —— 原生对话框不接受「未激活状态下的合成点击」（见 _activate_popup）
        if _activate_popup():
            time.sleep(0.5)
        R.click(sib[0], sib[1], 1.5)
        return True
    click_x = gx - int(W * 0.109)          # 兜底：按老经验左移一颗，但绝不点绿色
    print("[clean] 没认出兄弟按钮 → 退到绿按钮左移一颗 (%d,%d)" % (click_x, gy))
    if _activate_popup():
        time.sleep(0.5)
    R.click(click_x, gy, 1.5)
    return True


def clear_modals(W, H):
    """**不管有没有残留窗口**，先扫一遍模态弹窗并遣散。

    为什么必须无条件做：模态框会吞掉之后所有点击，症状是「面板打不开 / 侧边栏点不动」，
    极难联想到弹窗。而旧实现是「没有残留窗口就直接 return」—— 那个分支恰恰**永远不检查弹窗**，
    于是「侧边栏干净 + 一个退出登录框」这个组合直接把整批品牌跑废（实测踩了一整轮）。

    ⚠️ 只对**微信主窗口**做，判据必须是 **WM_CLASS 含 wechat**，不能只看标题 ==「微信」：
    小程序面板的标题**也是「微信」但没有 WM_CLASS**（见 wxopen.has_miniapp_tab 的说明）。
    旧版用标题判，于是在**面板**里也扫「绿药丸按钮」并点它的兄弟按钮 ——
    面板里全是小程序图标，这一下就会点出各种莫名其妙的东西
    （实测：弹出过「退出登录？」确认框，鼠标悬在「确定」上，差一点就把微信登出了）。
    """
    # ⚠️ 不能依赖 top_window()：模态框是**无名、无 WM_CLASS 的普通顶层窗口**
    #    （实测那个「退出登录」框是 282x170 @ 499,427 的匿名窗口），不在 windows() 列表里，
    #    于是 win_under() → None → top_window() → None → 这里直接 return False。
    #    结果正好是反的：**有模态框时反而遣散不了**。
    #    改成：先问鼠标底下；认不出（或不是主窗口）就按 WM_CLASS 找主窗口
    #    —— 模态框本来就挂在主窗口上，直接对主窗口扫就行。
    wid = R.top_window(W, H)
    if not (wid and R.is_main_window(wid)):
        if wid:
            print("[clean] 鼠标底下是窗口 %s（不是主窗口）→ 改用主窗口扫弹窗" % wid)
        wid = R.find_main_window()
    if not wid:
        print("[clean] 找不到微信主窗口，跳过弹窗遣散")
        return False
    hits = 0
    # 先走「结构判据」这条路：直接找匿名顶层窗口（比像素扫描更早命中，也不依赖框内配色）。
    # 两条路互补 —— 结构那条能确认「框在不在」，像素那条能在框内定位按钮。
    if R.close_anon_modal(W, H):
        hits += 1
    for _ in range(3):
        if not dismiss_popups(wid, W, H):
            break
        hits += 1
        time.sleep(0.8)
    return hits > 0


def count_leftovers():
    """只数不关，输出 `[clean] LEFT=<n>`（给调用方决定要不要上硬手段）。

    为什么要单独一个模式：硬清运行时**有代价**（会把小程序面板一起弄没，
    面板本身也是 WeChatAppEx 渲染的），所以调用方得先知道「到底有没有残留」，
    别拿它当万能自愈 —— 实测踩过：没有残留也硬清，结果面板彻底打不开，整批跑废。
    """
    n = len([1 for w, t in R.windows() if leftover(w, t)])
    print("[clean] LEFT=%d" % n)
    return n


def main():
    if "--count" in sys.argv:
        count_leftovers()
        return 0

    W, H = R.size()
    if (W, H) != (R.WANT_W, R.WANT_H):
        R.run("DISPLAY=%s xrandr -s %dx%d" % (R.DISPLAY, R.WANT_W, R.WANT_H))
        time.sleep(2)
        W, H = R.size()

    if clear_modals(W, H):
        print("[clean] 上面遣散了模态弹窗（它会吞掉之后所有点击）")

    targets = [(w, t) for w, t in R.windows() if leftover(w, t)]
    if not targets:
        print("[clean] 没有残留的小程序窗口")
        return 0
    print("[clean] 发现 %d 个残留小程序窗口" % len(targets))
    bad = 0
    for wid, title in targets:
        print("[clean] 处理 %s（%s）" % (title, wid))
        dismiss_popups(wid, W, H)            # 第一遍：也可能是悬浮提示，先试关
        if R.close_window(wid, W, H, kind="miniapp"):
            time.sleep(1)
            continue
        print("[clean] 首次没关掉，换一种遣散方式再试一次")
        dismiss_popups(wid, W, H)
        R.click(int(W * 0.5), int(H * 0.5), 0.8)   # 点一下内容区空白，收掉悬浮层
        if not R.close_window(wid, W, H, kind="miniapp"):
            print("[clean] ⚠️ 还是没关掉 %s（不硬杀、不补点，避免误伤）" % title)
            bad += 1
        time.sleep(1)
    left = [t for w, t in R.windows() if leftover(w, t)]
    if left and "--restart-runtime" in sys.argv:
        print("[clean] 常规手段清不掉 → 杀小程序运行时 WeChatAppEx（主进程与登录态不受影响）")
        killed = 0
        for pid in os.listdir("/proc"):
            if not pid.isdigit():
                continue
            try:
                with open("/proc/%s/comm" % pid) as f:
                    if f.read().strip() == "WeChatAppEx":
                        os.kill(int(pid), 9)
                        killed += 1
            except Exception:
                pass
        print("[clean] 已杀 %d 个 WeChatAppEx 进程" % killed)
        time.sleep(8)
        left = [t for w, t in R.windows() if leftover(w, t)]
        if killed:
            print("[clean] ⚠️ 记得重启 hook 里的 WMPFDebugger（它启动时 attach 一次）")
    if left:
        print("[clean] ⚠️ 仍有残留：%s" % "、".join(left))
        print("[clean]    加 --restart-runtime 可以硬清（会连带需要重启 hook）")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
