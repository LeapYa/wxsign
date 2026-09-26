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

# 微信**内部**窗口的标题黑名单 —— 它们不是小程序，但标题也不是「微信」，
# 所以光靠 WECHAT_TITLES 挡不住。实测踩到：打开了「图片和视频」（875x960，
# 看图片/视频的内置窗口），`miniapp()` 按「面积最大」把它当成了小程序，
# 结果 `raise_miniapp()` 去激活它、像素判据也拿它当页面 → 整条链路跑偏。
#   · 「图片和视频」= 微信内置看图/看视频窗口（点聊天里的图片就会开，容器里极易误开）
WECHAT_INNER_TITLES = ("图片和视频",)


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
    """当前小程序窗口；没有则 None。

    判据（按可靠性排序，全结构性）：
      ① 标题非空、且不在微信自身/内部窗口的标题黑名单里；
      ② **不超过半个屏幕宽** —— 真正的小程序窗口是 410x776 的竖窗，而微信主窗口
         是全屏（1276x1024）、内置看图窗口是 825x960。这一条把「尺寸像小程序的」
         和「微信自己的大窗口」分开，比单看标题可靠（标题会随语言/版本变）。
      ③ 多个候选时取**面积最小**的 —— 小程序窗口尺寸固定，而意外开的临时窗口
         通常更大；旧版取「面积最大」正是选错的原因。
    """
    try:
        sw = int(run("DISPLAY=%s xdotool getdisplaygeometry" % DISPLAY).split()[0])
    except Exception:
        sw = 1280
    cand = [w for w in windows()
            if w["title"] and w["title"] not in WECHAT_TITLES
            and w["title"] not in WECHAT_INNER_TITLES
            and w["w"] <= sw // 2]
    if not cand:
        return None
    cand.sort(key=lambda w: (w["w"] * w["h"]))
    return cand[0]


def rect():
    """小程序窗口 → (x, y, w, h)；没有则 None。"""
    m = miniapp()
    return (m["x"], m["y"], m["w"], m["h"]) if m else None


def screen():
    out = run("DISPLAY=%s xdotool getdisplaygeometry" % DISPLAY).strip().split()
    return (int(out[0]), int(out[1])) if len(out) == 2 else (1280, 1024)


# ─────────────────── 标题栏字形探测 ───────────────────
# 标题栏是**微信画的覆盖层**（不在小程序 DOM 里），所以只能按坐标点它 ——
# 但**别写死坐标**：只要「标题栏某端有个图标」这个结构还在，探测就有效，
# 换图标样式（`<` / `⌂`）、换字号、换主题色都不影响。
#
# 算法与 wxopen.probe_close_glyph 同源：逐像素与**本地底色**比
# （同一行左右各 8~16 列的中位数），而不是用整条带的中位数 ——
# 标题栏常常是**整条品牌色**（实测：来菜黄、酒煮江湖橙、签到页深红），
# 全局底色会把浅色药丸和里面的字形并成一整块，直接检不出来。
def _lum(buf, W, x, y):
    o = (y * W + x) * 3
    return (buf[o] + buf[o + 1] + buf[o + 2]) / 3.0


def glyphs(buf, W, H, x0, y0, x1, y1, thr=40, dmin=8, dmax=16,
           min_n=28, max_n=420, min_w=5, max_w=30, min_h=9, max_h=26):
    """在像素矩形 [x0,x1]×[y0,y1] 内找「紧凑字形块」。

    返回 [{'x','y','w','h','n','bbox'}]，按 x 升序（所以 [0] 就是**最靠左**的那个）。

    尺寸筛选（默认 28~420 像素、宽 5~30、高 9~26）是排掉背景噪点用的。
    ⚠️ 门槛别放太松：实测蜀大侠的标题栏里有 6 个对比块，其中 6×7 / 22 像素的那个
    是最靠左的，松门槛会把它当成返回按钮，于是「点回去」点了个空
    （真图标在同一分辨率下是 10×16 / 62 像素）。
    """
    x0, y0 = max(0, int(x0)), max(0, int(y0))
    x1, y1 = min(W - 1, int(x1)), min(H - 1, int(y1))
    if x1 <= x0 or y1 <= y0:
        return []

    fg = set()
    for y in range(y0, y1 + 1):
        for x in range(x0, x1 + 1):
            nb = []
            for d in range(dmin, dmax + 1):
                for xx in (x - d, x + d):
                    if x0 <= xx <= x1:
                        nb.append(_lum(buf, W, xx, y))
            if len(nb) < 6:
                continue
            nb.sort()
            if abs(_lum(buf, W, x, y) - nb[len(nb) // 2]) > thr:
                fg.add((x, y))
    if not fg:
        return []

    seen, out = set(), []
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
        if not (min_n <= len(comp) <= max_n and min_w <= w <= max_w and min_h <= h <= max_h):
            continue
        out.append({"x": (min(xs) + max(xs)) // 2, "y": (min(ys) + max(ys)) // 2,
                    "w": w, "h": h, "n": len(comp),
                    "bbox": (min(xs), min(ys), max(xs), max(ys))})
    out.sort(key=lambda g: g["x"])
    return out


if __name__ == "__main__":
    for w in windows():
        print("  %-8s %-16s %4d,%-4d %dx%d"
              % (w["id"], w["title"][:16], w["x"], w["y"], w["w"], w["h"]))
    m = miniapp()
    print("miniapp =", m)
