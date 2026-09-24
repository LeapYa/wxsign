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

用法（容器内）：DISPLAY=:1 python3 wxclean.py [--restart-runtime]
  --restart-runtime  常规手段都关不掉时，**杀掉小程序运行时进程（WeChatAppEx）**。
                     ⚠️ 这会清掉所有小程序窗口，而且 hook 需要跟着重启（WMPFDebugger 是
                     启动时 attach 一次的）—— 主进程 wechat 与登录态**不受影响**，
                     比「重启微信」轻得多。实测这招能清掉用关闭按钮点不掉的卡死窗口。
退出码：0 = 全部清掉（或本来就没有）；1 = 有没关掉的
"""
import os
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


def dismiss_popups(wid, W, H):
    """先遣散挡在窗口上的弹窗/悬浮提示，否则关闭按钮点不中（实测踩过）。

    做法：找窗口内容区里那颗**微信绿**药丸按钮（隐私弹窗的「同意」），点它左边那颗
    （「拒绝」）—— 我们不授权、只关窗。找不到绿按钮就只发一次 Esc。
    """
    R.raise_window(wid)
    time.sleep(0.6)
    buf = R.grab(W, H)
    xs, ys = [], []
    # 内容区中央带状范围里找微信绿 #07C160（避开标题栏与底栏）
    for y in range(int(H * 0.30), int(H * 0.62), 2):
        base = y * W * 3
        for x in range(int(W * 0.15), int(W * 0.85), 2):
            i = base + x * 3
            r, g, b = buf[i], buf[i + 1], buf[i + 2]
            if g > 150 and g - r > 60 and g - b > 40:
                xs.append(x)
                ys.append(y)
    if len(xs) >= 300:                       # 够大才算按钮，不是图标
        gx, gy = sum(xs) // len(xs), sum(ys) // len(ys)
        click_x = gx - int(W * 0.109)        # 「拒绝」在「同意」左边约 0.109W
        print("[clean] 检测到弹窗绿色按钮 (%d,%d) → 点它左边的「拒绝」(%d,%d)" % (gx, gy, click_x, gy))
        R.click(click_x, gy, 1.5)
        return True
    print("[clean] 没检测到弹窗，发一次 Esc 清掉悬浮提示")
    R.key("Escape", 1.0)
    return False


def main():
    W, H = R.size()
    if (W, H) != (R.WANT_W, R.WANT_H):
        R.run("DISPLAY=%s xrandr -s %dx%d" % (R.DISPLAY, R.WANT_W, R.WANT_H))
        time.sleep(2)
        W, H = R.size()

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
