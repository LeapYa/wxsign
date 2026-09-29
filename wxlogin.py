#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""微信 Linux 实例的「点登录 → 等手机确认 → 主窗口拉满屏」工具（容器内运行）。

⚠️⚠️ 事实纠正（2026-09-24，由使用者指出；此前本项目一直写错）⚠️⚠️
  点「登录」按钮**不等于**登录成功 —— Linux 版微信点完后**必须在手机上确认**
  （手机微信会弹一条登录确认）。之前误以为「点一下就免确认自动进」，
  实际是手机端点了确认，只是当时点得快、没看出来。

  两个重要推论，直接决定本工具能干什么：
    1. **登录是一次性配置，不影响「无人值守」这个定位**：首次扫码登录一次（任何方案都躲不开），
       之后只要微信不重启就一直有效；只有**因重启掉登录**时才需要人拿手机确认（这一步替代不了）。
    2. 手机久不确认，微信会提示「**登录状态已过期**」，之后就**只能扫码登录**——
       也就是说「没及时处理」会把难度从「手机点一下」升级成「扫码」，
       而扫码必须能看到容器画面（KasmVNC 网页）。

  另：Windows 版微信有「登录免确认」选项（可做到纯自动），**Linux 版没有这个选项**
  （不是藏在设置里，是客户端没提供）。所以「配一次免确认就一劳永逸」这条路在
  Linux 容器里走不通 —— 结论只能是「尽量别让微信掉登录」（容器别重启）。

用法（容器内）：
  DISPLAY=:1 python3 wxlogin.py                点登录并等手机确认（默认最多等 300 秒）
  DISPLAY=:1 python3 wxlogin.py --wait 600     改等待上限（秒）
  DISPLAY=:1 python3 wxlogin.py --watch 20     只看不点：观察 20 秒并报状态（调试用）

退出码：
  0  已登录（并已把主窗口归位到 (0,0) 并拉成 1280x1024 —— 登录判据需要它足够宽）
  1  本来就已经登录（没找到绿色登录按钮，也没在等确认）
  2  画面里出现二维码 → **需要扫码**（多半是「登录状态已过期」）
  3  点了登录但等待超时（手机没确认，或没人管）
  两种失败都会存一张截图到 /tmp/wxlogin_after.png 供人工看。
"""
import os
import subprocess
import sys
import time

sys.path.insert(0, "/tmp")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wxopen as R          # noqa: E402  （与 wxfind / wxclean 共用同一套窗口原语）

DISPLAY = os.environ.get("DISPLAY", ":1")
W, H = 1280, 1024
SHOT = "/tmp/wxlogin_after.png"


# ───────────────────────── 判据 ─────────────────────────

def find_green_button(buf, w, h):
    """微信绿按钮（#07C160）的像素质心：g 明显高于 r/b 且够亮。返回 (x, y, n)。
    比写死坐标可靠 —— 窗口大小/位置会变。"""
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


def login_state(buf, w, h):
    """返回 (是否已登录, 诊断串)。

    已登录的判据 = **侧边栏图标列（≥6 个按钮）且 左上角有全局搜索框**。
    两者缺一不可：登录页左侧没有图标列、也没有那个搜索框，
    所以这条在登录页不会误命中（2026-09-24 实测已登录态：9 个按钮 + 搜索框存在）。
    """
    rail = R.find_rail_buttons(w, h)
    box = R.find_search_box(w, h)
    ok = len(rail) >= 6 and box is not None
    return ok, "侧边栏 %d 个按钮 / 搜索框 %s" % (len(rail), "有" if box else "无")


def find_qrcode(buf, w, h):
    """画面中央有没有二维码：找行、列**两个方向**都是高频黑白跳变的区域。

    只在登录流程里当「可能需要扫码」的信号用，且报错文案一律说「疑似」——
    它只能说明「屏幕上有二维码」，不能单独断定「已过期」（等确认页也可能带二维码）。
    """
    x0, x1 = int(w * 0.28), int(w * 0.72)
    y0, y1 = int(h * 0.10), int(h * 0.95)

    def val(x, y):
        i = (y * w + x) * 3
        return (buf[i] + buf[i + 1] + buf[i + 2]) / 3.0

    def jumps(seq):
        n, prev = 0, None
        for v in seq:
            cur = 0 if v > 190 else (1 if v < 110 else None)
            if cur is None:
                continue
            if prev is not None and cur != prev:
                n += 1
            prev = cur
        return n

    hot = [y for y in range(y0, y1, 2)
           if jumps(val(x, y) for x in range(x0, x1, 2)) >= 13]
    if len(hot) < 55:
        return None
    ys, ye = hot[0], hot[-1]
    cols = sum(1 for x in range(x0 + 40, x1 - 40, 20)
               if jumps(val(x, y) for y in range(ys, ye, 2)) >= 13)
    return (x0, ys, x1, ye) if cols >= 8 else None


# ───────────────────────── 动作 ─────────────────────────

def shot(path=SHOT):
    subprocess.run("DISPLAY=%s ffmpeg -loglevel error -y -f x11grab -video_size %dx%d -i %s "
                   "-frames:v 1 %s" % (DISPLAY, W, H, DISPLAY, path),
                   shell=True, capture_output=True)


def fullscreen_main():
    """把主窗口**归位到 (0,0) 并拉成整屏** —— 这一步是**必须的**。

    ⚠️ 2026-09-28 走过一次弯路，记录清楚免得再犯：
      我一度把这里的 `windowsize 1280 1024` 删掉，理由是"满屏会遮住小程序面板"。
      **那是错的**，直接后果是：
        · `login_state()` 的判据是「**侧边栏图标列 ≥6 个** + 左上角有全局搜索框」，
          它要求主窗口**足够宽**才能稳定认出来；主窗口一窄（实测 496px），
          侧边栏/搜索框判据直接失效 → 被判成「**未登录**」→ 跑去点登录 → 失败退出（rc=3），
          整批签到**连第一个品牌都没跑到**就终止。
        · 另外侧边栏按钮的 y 坐标也依赖一个尺寸稳定的主窗口。

    → **正确分工（两个阶段分开，互不干扰）**：
        · **登录自检阶段**（本文件）→ 主窗口**满屏**、位置 (0,0)：判据稳定、坐标可预测。
        · **开小程序阶段**（`wxopen.py`）→ `move_main_aside()` 把主窗口**挪出屏幕**，
          面板/小程序窗口因此**彻底不被遮**（"遮一半也是遮"，缩小窗口是没用的）。
      所以这里**保持满屏**，别再动它。遮挡问题在 wxopen 那边解决。
    """
    # ⭐ **先把它放回屏幕**：`wxopen.hide_main()` 运行期用 `xdotool windowunmap` 把主窗口
    #    整块移出屏幕（这样小程序面板才不被遮），**未映射的窗口不在 `--onlyvisible` 列表里**
    #    → 下面那句 search 会一个都找不到 → 返回 False → `login_state()` 抓到的是面板/小程序，
    #    判据（侧边栏图标列 + 全局搜索框）全部落空 → 误判「未登录」→ rc=3 整批终止
    #    （2026-09-28 实测踩到：上一次运行把主窗口留在屏幕外，下一次连登录自检都过不去）。
    R.show_main(W, H)
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
            return True
    return False


def main():
    args = sys.argv[1:]
    wait = int(os.environ.get("WXSIGN_LOGIN_WAIT", "300"))
    if "--wait" in args:
        wait = int(args[args.index("--wait") + 1])
    watch = int(args[args.index("--watch") + 1]) if "--watch" in args else 0

    subprocess.run("DISPLAY=%s xrandr -s %dx%d" % (DISPLAY, W, H), shell=True)
    time.sleep(1.5)
    # ⭐ **先**把主窗口拉满屏，**再**判登录 —— 判据（侧边栏图标列 + 全局搜索框）
    #    要求主窗口**足够宽**。顺序反了会变成一个死锁：
    #      判失败 → 不拉满屏 → 更判不出来 → 一直判「未登录」→ rc=3 直接退出整批。
    #    （2026-09-28 实测踩到：主窗口被收窄到 496px 后，`搜索框 无` → 判未登录。）
    fullscreen_main()
    time.sleep(1.0)
    buf = R.grab(W, H)
    ok, diag = login_state(buf, W, H)

    if watch:
        print("[wxlogin] --watch %ds：只看不点" % watch)
        for i in range(watch):
            ok, diag = login_state(R.grab(W, H), W, H)
            print("[wxlogin] t=%2ds 登录=%s (%s)" % (i, "是" if ok else "否", diag))
            time.sleep(1)
        return 0

    if ok:
        print("[wxlogin] 已经是登录状态（%s）" % diag)
        fullscreen_main()
        return 1

    pt = find_green_button(buf, W, H)
    if not pt:
        print("[wxlogin] ⚠️ 既没找到绿色登录按钮，也没认出已登录界面（%s）" % diag)
        print("[wxlogin]    可能是加载中、异常界面、或窗口不在最前 → 截图 %s" % SHOT)
        shot()
        return 3

    x, y, n = pt
    print("[wxlogin] 登录按钮质心 (%d,%d) 命中 %d 像素 → 点击" % (x, y, n))
    subprocess.run("DISPLAY=%s xdotool mousemove %d %d; sleep 0.4; DISPLAY=%s xdotool click 1"
                   % (DISPLAY, x, y, DISPLAY), shell=True)
    print("[wxlogin] ⏳ 已点登录，**请在手机上确认**（Linux 版微信没有免确认选项）；"
          "最多等 %d 秒" % wait)

    t0 = time.time()
    while time.time() - t0 < wait:
        time.sleep(3)
        buf = R.grab(W, H)
        ok, diag = login_state(buf, W, H)
        spent = int(time.time() - t0)
        if ok:
            print("[wxlogin] ✅ 登录成功（等待 %ds）—— %s" % (spent, diag))
            fullscreen_main()
            return 0
        qr = find_qrcode(buf, W, H)
        if qr:
            print("[wxlogin] ⚠️ 画面上出现二维码（区域 %s）" % (qr,))
            print("[wxlogin]    大概率是**登录状态已过期**，现在只能扫码登录："
                  "打开 KasmVNC 网页 → 进实例 → 手机扫这个码")
            print("[wxlogin]    （若你刚点了手机确认，也可能只是等确认页自带的码，看一眼 %s）" % SHOT)
            shot()
            return 2
        print("[wxlogin]    等待手机确认… %ds/%ds（%s）" % (spent, wait, diag))

    print("[wxlogin] ❌ 等了 %ds 仍没登录 —— 手机上没确认（或没人处理）" % wait)
    print("[wxlogin]    注意：久不确认微信会报「登录状态已过期」，之后就只能扫码了")
    shot()
    return 3


if __name__ == "__main__":
    sys.exit(main())
