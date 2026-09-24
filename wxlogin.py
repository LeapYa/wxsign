#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""微信 Linux 实例的「点登录 + 拉全屏」工具（容器内运行）。

微信重启后界面回登录页（账号已记住，点一下登录即可，多数情况免手机确认）。
登录按钮是微信绿 #07C160，比写死坐标可靠 —— 窗口大小/位置会变。

用法（容器内）：DISPLAY=:1 python3 wxlogin.py
退出码：0 已点登录并按全屏整理窗口；1 没找到登录按钮（可能已经登录了）
"""
import os
import subprocess
import sys
import time

DISPLAY = os.environ.get("DISPLAY", ":1")
W, H = 1280, 1024


def grab(w=W, h=H):
    d = subprocess.run("DISPLAY=%s ffmpeg -loglevel error -f x11grab -video_size %dx%d -i %s "
                       "-frames:v 1 -f rawvideo -pix_fmt rgb24 -" % (DISPLAY, w, h, DISPLAY),
                       shell=True, capture_output=True).stdout
    if len(d) < w * h * 3:
        raise RuntimeError("截屏失败")
    return d


def find_green_button(buf, w, h):
    """微信绿按钮的像素质心：g 明显高于 r/b 且够亮。返回 (x, y, n)。"""
    xs, ys = [], []
    for y in range(0, h, 2):
        base = y * w * 3
        for x in range(0, w, 2):
            i = base + x * 3
            r, g, b = buf[i], buf[i + 1], buf[i + 2]
            if g > 150 and g - r > 60 and g - b > 40:
                xs.append(x)
                ys.append(y)
    if len(xs) < 200:
        return None
    return (sum(xs) // len(xs), sum(ys) // len(ys), len(xs))


def main():
    # 分辨率钉死（KasmVNC 会跟着观看者窗口变）
    subprocess.run("DISPLAY=%s xrandr -s %dx%d" % (DISPLAY, W, H), shell=True)
    time.sleep(1.5)
    buf = grab()
    pt = find_green_button(buf, W, H)
    if not pt:
        print("[wxlogin] 没找到绿色登录按钮 —— 可能已经登录了")
        return 1
    x, y, n = pt
    print("[wxlogin] 登录按钮质心 (%d,%d) 命中 %d 像素 → 点击" % (x, y, n))
    subprocess.run("DISPLAY=%s xdotool mousemove %d %d; sleep 0.4; DISPLAY=%s xdotool click 1"
                   % (DISPLAY, x, y, DISPLAY), shell=True)
    time.sleep(8)
    subprocess.run("DISPLAY=%s ffmpeg -loglevel error -y -f x11grab -video_size %dx%d -i %s "
                   "-frames:v 1 /tmp/wxlogin_after.png" % (DISPLAY, W, H, DISPLAY),
                   shell=True, capture_output=True)

    # 把主窗口拉回 (0,0) 全屏 —— 比例坐标全部按 1280x1024 校准
    ids = subprocess.run("DISPLAY=%s xdotool search --onlyvisible --name '^微信$'" % DISPLAY,
                         shell=True, capture_output=True, text=True).stdout.split()
    for wid in ids:
        cls = subprocess.run("DISPLAY=%s xprop -id %s WM_CLASS" % (DISPLAY, wid),
                             shell=True, capture_output=True, text=True).stdout
        if "wechat" in cls.lower():
            subprocess.run("DISPLAY=%s xdotool windowmove %s 0 0; "
                           "DISPLAY=%s xdotool windowsize %s %d %d; "
                           "DISPLAY=%s xdotool windowactivate %s"
                           % (DISPLAY, wid, DISPLAY, wid, W, H, DISPLAY, wid), shell=True)
            print("[wxlogin] 主窗口 %s 已拉成 %dx%d 全屏" % (wid, W, H))
    time.sleep(2)
    return 0


if __name__ == "__main__":
    sys.exit(main())
