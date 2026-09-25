#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把小程序开屏的「隐私保护提示」弹层点掉（容器内运行，走 CDP DOM）。

为什么必须做这一步：
    小程序第一次打开时会先弹「用户隐私保护提示——《xxx 隐私保护指引》」，
    在用户点「同意」之前，**小程序的页面根本没有初始化** ——
    于是 wx.login 的 code 拿得到（appId 也读得到），但 storage 里**还没有 mpId**，
    刷 token 就会报「拿不到 mpId / invalid code」。

实测症状（一整批 53 个号里 80% 死在这）：
    [ensure] 小大董 → rc=0 (已就绪)          ← 小程序明明开成了
    [enum] 有 wx 的上下文=[3,6,8]
    [try] ctx 3: 拿不到 mpId，跳过
    ...
    [!] token 刷新失败

    而 wxident.js 报的页面是 `pages/system/privacyRights/privacyRightsPopup` ——
    就是这张弹层。`--find` 之所以能读到 appId，是因为 appId 在逻辑层随时可取，
    不需要页面初始化；mpId 在 storage 里，才需要。

判据/安全性：
  · 文案走 wxdom.pick_action(mode="confirm")，词表是 允许/同意/确认/授权/继续/确定/好的，
    并且**显式排除以「不」开头的**（「不同意」里含「同意」，被误点会把隐私协议拒掉，流程作废）。
  · 只在小程序自己的渲染层 DOM 里点，不碰微信主窗口。
  · 每轮点完等 1.5s 再看，最多 4 轮（实测多数品牌 1~2 轮就干净了）。

用法（容器内）：DISPLAY=:1 python3 wxagree.py [轮数]
退出码：0 = 点了至少一下（或本来就没有弹层）；1 = 连不上渲染层
"""
import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import wxdom
    import wxreg
except ImportError:
    sys.path.insert(0, "/tmp")
    import wxdom
    import wxreg


def raise_miniapp(ox, oy):
    """把小程序窗口**激活并置顶**。

    ⚠️ 没有这一步，后面所有点击都会被吞掉（2026-09-25 实测，卡了最久的一个 bug）：
    小程序是从「小程序面板」里点开的，面板窗口是**全屏**且往往仍在最前 ——
    于是点小程序页面坐标算出来的屏幕点，实际落在了**面板**上。
    症状极具误导性：弹层纹丝不动、同一点连点 N 次都没反应，
    看起来像「坐标算错」或「合成点击无效」，**实际是点击根本没送到目标窗口**。
    实测：`windowactivate --sync` + `windowraise` 之后，同一个点**一次就成了**。

    怎么找窗口：按「窗口位置 == CDP 报的页面原点」匹配（标题是小程序名，但我们不一定知道）。
    """
    try:
        p = subprocess.run("DISPLAY=%s xdotool search --name '.+' 2>/dev/null" % wxreg.DISPLAY,
                           shell=True, capture_output=True, text=True, timeout=20)
        for wid in (p.stdout or "").split():
            g = subprocess.run("DISPLAY=%s xdotool getwindowgeometry %s" % (wxreg.DISPLAY, wid),
                               shell=True, capture_output=True, text=True, timeout=10).stdout
            m = re.search(r"Position:\s*(-?\d+),(-?\d+)", g or "")
            if not m:
                continue
            if abs(int(m.group(1)) - ox) <= 2 and abs(int(m.group(2)) - oy) <= 2:
                subprocess.run(
                    "DISPLAY=%s xdotool windowactivate --sync %s; "
                    "DISPLAY=%s xdotool windowraise %s" % (wxreg.DISPLAY, wid, wxreg.DISPLAY, wid),
                    shell=True, capture_output=True, text=True, timeout=15)
                print("[agree] 已激活并置顶小程序窗口 %s（页面原点 %d,%d）" % (wid, ox, oy))
                return True
        print("[agree] ⚠️ 按位置 %d,%d 没找到小程序窗口 → 点击可能被上层窗口吃掉" % (ox, oy))
    except Exception as e:
        print("[agree] 置顶窗口异常：%s" % e)
    return False

# 读窗口装饰尺寸：页面坐标 → 屏幕坐标要补的就是 (outer-inner)
WIN_JS = ("JSON.stringify({ow:window.outerWidth,oh:window.outerHeight,"
          "iw:window.innerWidth,ih:window.innerHeight})")


def window_offset(ws, ctx):
    """页面坐标 → 屏幕坐标 还需要补的「窗口装饰」偏移 → (dx, dy)。

    为什么必须有这一步（2026-09-25 实测，卡了很久）：
    小程序用**自定义导航栏**时 `window.innerHeight < window.outerHeight`，
    也就是**视口不是从窗口顶部开始的**。实测「小大董」窗口 410x776，
    但 innerHeight 只有 **715** —— 差 61px。
    不加这个偏移，点击会落到目标**上方 61px**：我连点 4 次「同意并继续」，
    实际全点在了它上方的《隐私保护指引》链接上，**连开 4 个文档标签页、
    弹层却纹丝不动**，看起来像「点击没生效」，实际是点错位置。

    ⚠️ 偏移**因小程序而异**（有的页面 innerHeight ≥ outerHeight，偏移为 0），
    所以必须**每次动态读**，不能写死。
    """
    v = wxdom.evaluate(ws, WIN_JS, ctx=ctx)
    try:
        d = json.loads(v)
        return max(0, (int(d["ow"]) - int(d["iw"])) // 2), max(0, int(d["oh"]) - int(d["ih"]))
    except Exception:
        return 0, 0


def agree(rounds=4, wait=1.5):
    """返回点掉的次数；-1 表示连不上渲染层。"""
    import time
    hits = 0
    # ⚠️ 必须先 refresh_page()：wxreg 的坐标换算依赖模块级的 _PAGE，
    #    它的初值是 {"x":0,"y":0,...}。不刷新的话 origin() 永远返回 (0,0)，
    #    而小程序窗口实际在 (435,124) → 点击全落在微信主窗口上。
    if not wxreg.refresh_page():
        print("[agree] ⚠️ refresh_page 失败，坐标可能不准")
    for r in range(rounds):
        ws, ctx = wxdom.open_dom()
        if not ws:
            print("[agree] 连不上渲染层（CDP）")
            return -1
        d = wxdom.scan(ws, ctx)
        if not d:
            print("[agree] 第%d轮：读不到 DOM" % (r + 1))
            break
        it = wxdom.pick_action(d, mode="confirm")
        if not it:
            print("[agree] 第%d轮：没有待确认的按钮 → 干净了" % (r + 1))
            break
        ox, oy = wxreg.origin()
        dx, dy = window_offset(ws, ctx)
        px = it.get("x", it.get("cx", 0))
        py = it.get("y", it.get("cy", 0))
        sx, sy = ox + dx + px, oy + dy + py
        print("[agree] 第%d轮：点「%s」(页面 %d,%d + 装饰 %d,%d → 屏幕 %d,%d)"
              % (r + 1, it.get("text", "")[:12], px, py, dx, dy, sx, sy))
        raise_miniapp(ox, oy)          # ← 关键：不置顶，点击会被面板窗口吃掉
        wxreg.click(sx, sy, raw=True)
        hits += 1
        time.sleep(wait)
        wxreg.refresh_page()          # 点完页面可能变，重探一次
    return hits


def main():
    rounds = int(sys.argv[1]) if len(sys.argv) > 1 else 4
    n = agree(rounds)
    print("[agree] 共点 %d 下" % n)
    return 1 if n < 0 else 0


if __name__ == "__main__":
    sys.exit(main())
