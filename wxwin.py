# -*- coding: utf-8 -*-
"""窗口几何：把「小程序页面内的坐标」换算成「屏幕绝对坐标」。

背景
----
云微镜像的 openbox 原本有一条通配规则，把所有窗口无条件最大化：

    <application class="*"><maximized>yes</maximized></application>

于是小程序窗口被拉成整个屏幕（1280x1024），页面坐标恰好等于屏幕坐标，
脚本就直接点屏幕坐标了 —— 那是**巧合**，不是设计。

把它改成只最大化「微信」主窗口与面板之后：

    <application name="微信*"><maximized>yes</maximized></application>

小程序窗口恢复成微信自己请求的手机竖版尺寸（实测 410x776），并且**位置不固定在 (0,0)**
（实测 435,124）。于是所有点击、截图都必须加上窗口原点 —— 本模块就是干这个的。

实测事实（窗口 410x776 @ 435,124）
----------------------------------
· openbox **不给**小程序窗口加装饰：frame 与 client 同尺寸同位置（xwininfo 验证）；
· 顶部那条「⌂ 首页 ●●● ─ ⊙」是**微信自己画的**，属于 client 区域内部；
· 页面（WebView 视口）从标题栏下方开始 —— 原点由页面自己报的
  `window.screenX/screenY` 给出（比猜标题栏高度可靠）。

排除主窗口与面板：它们的标题都是「微信」，而小程序窗口标题是各品牌名。
"""
import os
import subprocess

DISPLAY = os.environ.get("DISPLAY", ":1")

# 主窗口与「小程序面板」的标题都叫「微信」；小程序窗口标题是品牌名 → 用它区分
WECHAT_TITLES = ("微信",)


def run(cmd):
    return subprocess.run(cmd, shell=True, capture_output=True, text=True).stdout


def windows():
    """所有可见窗口 → [{'id','title','x','y','w','h'}]（屏幕绝对坐标）。"""
    out = []
    for wid in run("DISPLAY=%s xdotool search --onlyvisible --name '.+'" % DISPLAY).split():
        title = run("DISPLAY=%s xdotool getwindowname %s" % (DISPLAY, wid)).strip()
        g = run("DISPLAY=%s xdotool getwindowgeometry --shell %s" % (DISPLAY, wid))
        d = {}
        for line in g.splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                d[k.strip()] = v.strip()
        try:
            out.append({"id": int(wid), "title": title,
                        "x": int(d["X"]), "y": int(d["Y"]),
                        "w": int(d["WIDTH"]), "h": int(d["HEIGHT"])})
        except (KeyError, ValueError):
            continue
    return out


def miniapp():
    """当前小程序窗口；没有则 None。多个时取面积最大的（正常流程只会有一个）。"""
    cand = [w for w in windows() if w["title"] and w["title"] not in WECHAT_TITLES]
    if not cand:
        return None
    cand.sort(key=lambda w: -(w["w"] * w["h"]))
    return cand[0]


def rect():
    """小程序窗口 → (x, y, w, h)；没有则 None。"""
    m = miniapp()
    return (m["x"], m["y"], m["w"], m["h"]) if m else None


def screen():
    out = run("DISPLAY=%s xdotool getdisplaygeometry" % DISPLAY).strip().split()
    return (int(out[0]), int(out[1])) if len(out) == 2 else (1280, 1024)


if __name__ == "__main__":
    for w in windows():
        print("  %-8s %-16s %4d,%-4d %dx%d"
              % (w["id"], w["title"][:16], w["x"], w["y"], w["w"], w["h"]))
    m = miniapp()
    print("miniapp =", m)
