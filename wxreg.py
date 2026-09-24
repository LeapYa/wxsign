#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""驱动微信 UI 完成「首次注册会员」这一步（容器内运行）。

为什么需要它：注册要走微信原生 UI，抓不到接口 ——
    点签到/参与入口 →（可能）隐私协议弹窗 → 授权说明弹窗 → 微信手机号授权弹窗 →「允许」
    → 页面自己调 registerVip → 注册完通常顺手签到一次
**每个账号只走一次**（注册完就不再弹）。

⚠️ 纠正一个曾经的错误说法：**这一步不需要手机号**。
手机号是**从你微信账号里取的**（微信弹窗授权，脚本点「允许」即可，全自动）。
`WXSIGN_REGISTER_PHONE` 只在两种情况有用：
  · 走 API 直连注册时（`WX_REGISTER_MODE=api`，零 UI，需要明文手机号）；
  · 走 UI 且该账号绑了**多个**号码时，用它（配合 tesseract OCR）挑出正确那一行。
只有 1 个号码时什么都不用设。

识别思路（不依赖 OCR）：
  · 绿色按钮 = 微信主按钮（隐私协议的「同意并继续」、手机号弹窗的「允许」）
  · 橙色按钮 = 小程序自己的「授权」/「参与」按钮
  · 白色卡片 = 有弹窗在（页面背景通常不是通栏白，用宽度区间区分）
  · 手机号列表的行位置 → 用**已勾选号码旁那颗绿色 ✓** 当锚点，按行距往上数

⚠️ 通用性边界：**弹窗链**（微信 + 吾享标准弹窗）是通用的；**怎么走到那个入口**因品牌而异，
所以入口坐标可配（见 WXSIGN_REG_CLICK）。默认取「居中偏下」，与签到/参与按钮常见位置一致。

环境变量：
  WXSIGN_REG_CLICK     入口按钮位置，比例或像素：`0.50,0.64`（默认）或 `640,700`
  WXSIGN_REG_SCROLL    进入前向下滚动的次数（默认 6；设 0 表示不滚）
  WXSIGN_REGISTER_PHONE_INDEX  多号码时选第几个（从 1 开始）
  WXSIGN_REGISTER_PHONE        期望手机号（完整或后 4 位）；装了 tesseract 时逐行 OCR 匹配
  SHOT_DIR             截图目录（默认 /tmp/shots）

用法：python3 wxreg.py [--index N] [--phone X] [--dry-run] [--analyze a.png]
"""
import argparse
import os
import re
import shutil
import subprocess
import sys
import time

DISPLAY = os.environ.get("DISPLAY", ":1")
SHOT_DIR = os.environ.get("SHOT_DIR", "/tmp/shots")
ROW_STEP = 0.045          # 号码行行距（占屏高比例）
CHECK_GAP = 0.030         # ✓ 与「微信绑定号码」小字的间距，用于避开小字


def envv(*names, default=""):
    """按顺序取第一个非空环境变量（新旧名字都认）。"""
    for n in names:
        v = os.environ.get(n)
        if v:
            return v
    return default


# ───────────────────────────── 基础工具 ─────────────────────────────
def run(cmd):
    return subprocess.run(cmd, shell=True, capture_output=True, text=True).stdout


def display_size():
    out = run("DISPLAY=%s xdotool getdisplaygeometry" % DISPLAY).strip().split()
    return int(out[0]), int(out[1])


def grab(W, H):
    cmd = ("DISPLAY=%s ffmpeg -loglevel error -f x11grab -video_size %dx%d -i %s "
           "-frames:v 1 -f rawvideo -pix_fmt rgb24 -" % (DISPLAY, W, H, DISPLAY))
    d = subprocess.run(cmd, shell=True, capture_output=True).stdout
    if len(d) < W * H * 3:
        raise RuntimeError("截屏失败（%d 字节）" % len(d))
    return d


def grab_file(path):
    wh = run("ffprobe -v error -select_streams v -show_entries stream=width,height "
             "-of csv=p=0 %s" % path).strip().split(",")
    W, H = int(wh[0]), int(wh[1])
    d = subprocess.run("ffmpeg -loglevel error -i %s -f rawvideo -pix_fmt rgb24 -" % path,
                       shell=True, capture_output=True).stdout
    if len(d) < W * H * 3:
        raise RuntimeError("读图失败 %s" % path)
    return d, W, H


def grab_png(W, H, name):
    os.makedirs(SHOT_DIR, exist_ok=True)
    p = os.path.join(SHOT_DIR, name)
    run("DISPLAY=%s ffmpeg -loglevel error -y -f x11grab -video_size %dx%d -i %s -frames:v 1 %s"
        % (DISPLAY, W, H, DISPLAY, p))
    return p


def is_green(c):
    """微信绿：主绿 (7,193,96)；隐私协议那颗偏青 (102,196,170)，一并认"""
    return c[1] >= 140 and c[1] - c[0] >= 40 and c[1] - c[2] >= 15


def is_orange(c):
    return c[0] >= 200 and 140 <= c[1] <= 235 and c[2] <= 150 and c[0] - c[2] >= 90


def is_white(c):
    return min(c) > 228


def blob(buf, W, H, pred, x0, x1, y0, y1, step=2, min_hits=3):
    """在区域内找某类颜色像素的 bbox：先按 y 直方图定位主带，再向上下扩张（容许小间隙）"""
    hits_y = {}
    for y in range(y0, y1, step):
        base = y * W * 3
        n = 0
        for x in range(x0, x1, step):
            i = base + x * 3
            if pred((buf[i], buf[i + 1], buf[i + 2])):
                n += 1
        if n >= min_hits:
            hits_y[y] = n
    if not hits_y:
        return None
    peak = max(hits_y, key=hits_y.get)
    ys = sorted(hits_y)
    # 以 peak 为中心向两侧扩张，间隙 > 6 就断
    run = [peak]
    prev = peak
    for y in [v for v in ys if v < peak][::-1]:
        if prev - y <= 6:
            run.append(y)
            prev = y
        else:
            break
    prev = peak
    for y in [v for v in ys if v > peak]:
        if y - prev <= 6:
            run.append(y)
            prev = y
        else:
            break
    y0b, y1b = min(run), max(run)
    xs = []
    for y in range(y0b, y1b + 1, step):
        base = y * W * 3
        for x in range(x0, x1, step):
            i = base + x * 3
            if pred((buf[i], buf[i + 1], buf[i + 2])):
                xs.append(x)
    if not xs:
        return None
    return min(xs), y0b, max(xs), y1b


def white_card(buf, W, H):
    """找居中白色弹窗卡片。
    关键：页面通栏白（有的话）会接近满宽，弹窗白卡只占屏宽的 3~9 成 —— 用宽度区间排除通栏白。"""
    lo, hi = int(W * 0.30), int(W * 0.95)      # 像素宽度区间
    q = {}
    for y in range(int(H * 0.03), int(H * 0.98), 3):
        base = y * W * 3
        n = 0
        for x in range(int(W * 0.02), int(W * 0.98), 4):
            i = base + x * 3
            if is_white((buf[i], buf[i + 1], buf[i + 2])):
                n += 1
        n *= 4                                  # 采样数 → 像素宽度
        if lo <= n <= hi:
            q[y] = n
    if not q:
        return None
    ys = sorted(q)
    best = cur = [ys[0]]
    for y in ys[1:]:
        if y - cur[-1] <= 9:
            cur.append(y)
        else:
            if len(cur) > len(best):
                best = cur
            cur = [y]
    if len(cur) > len(best):
        best = cur
    if len(best) < H * 0.15:          # 太薄不算弹窗
        return None
    y0, y1 = best[0], best[-1]
    xs = []
    for y in range(y0, y1 + 1, 4):
        base = y * W * 3
        for x in range(int(W * 0.02), int(W * 0.98), 4):
            i = base + x * 3
            if is_white((buf[i], buf[i + 1], buf[i + 2])):
                xs.append(x)
    if not xs:
        return None
    return min(xs), y0, max(xs), y1


def text_bands(buf, W, H, y0, y1, x0, x1):
    """区域内「文字行」带（暗像素成带），返回 [(ytop, ybot, x中心)]"""
    bands, cur = [], None
    for y in range(y0, y1):
        base = y * W * 3
        n = 0
        for x in range(x0, x1, 2):
            i = base + x * 3
            if buf[i] + buf[i + 1] + buf[i + 2] < 430:
                n += 1
        if n >= 2:
            cur = [y, y] if cur is None else [cur[0], y]
        else:
            if cur and cur[1] - cur[0] >= 6:
                bands.append(cur)
            cur = None
    if cur and cur[1] - cur[0] >= 6:
        bands.append(cur)
    out = []
    for a, b in bands:
        xs = []
        for y in range(a, b + 1):
            base = y * W * 3
            for x in range(x0, x1, 2):
                i = base + x * 3
                if buf[i] + buf[i + 1] + buf[i + 2] < 430:
                    xs.append(x)
        if xs:
            out.append((a, b, (min(xs) + max(xs)) // 2))
    return out


def click(x, y):
    run("DISPLAY=%s xdotool mousemove %d %d; sleep 0.4; DISPLAY=%s xdotool click 1" % (DISPLAY, x, y, DISPLAY))


def scroll_down(times, W, H):
    run(("DISPLAY=%s xdotool mousemove %d %d; " % (DISPLAY, W // 2, H // 2))
        + "".join("DISPLAY=%s xdotool click 5; sleep 0.25; " % DISPLAY for _ in range(times)))


def ocr_ok():
    return shutil.which("tesseract") is not None


def ocr_digits(png):
    r = subprocess.run("tesseract %s - --psm 7 -c tessedit_char_whitelist=0123456789* 2>/dev/null" % png,
                       shell=True, capture_output=True, text=True)
    return re.sub(r"\D", "", r.stdout or "")


def in_wechat_main(buf, W, H):
    """当前是不是**微信主窗口**（而不是小程序页面）。

    判据：左侧图标列（x 14..52）里「与底色不同的横向带」≥6 个 —— 那是微信侧边栏
    （头像/微信/通讯录/收藏/朋友圈/视频号/搜一搜/小程序…），**小程序窗口没有这一列**。
    作用：在错误的页面上跑这个脚本只会给出误导性的报错，先认出来并说清楚。
    """
    x0, x1 = 14, 52
    samples = [[], [], []]
    for y in range(0, H, 3):
        base = y * W * 3
        for x in range(x0, x1, 3):
            i = base + x * 3
            for c in range(3):
                samples[c].append(buf[i + c])
    bg = [sorted(s)[len(s) // 2] for s in samples]
    rows = []
    for y in range(0, H):
        base = y * W * 3
        n = 0
        for x in range(x0, x1):
            i = base + x * 3
            if abs(buf[i] - bg[0]) + abs(buf[i + 1] - bg[1]) + abs(buf[i + 2] - bg[2]) > 55:
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
    return len([b for b in bands if len(b) >= 8]) >= 6


def color_density(buf, W, pred, box, step=2):
    """bbox 内该颜色的像素占比 —— 用来区分「实心按钮」和「文字/线条/图片」。"""
    x0, y0, x1, y1 = box
    hit = tot = 0
    for y in range(y0, y1 + 1, step):
        base = y * W * 3
        for x in range(x0, x1 + 1, step):
            i = base + x * 3
            tot += 1
            if pred((buf[i], buf[i + 1], buf[i + 2])):
                hit += 1
    return (hit / float(tot)) if tot else 0.0


def is_button(box, buf, W, pred, min_wf=0.25, min_ratio=2.5, min_density=0.45):
    """按钮 = 够宽 + 够扁 + 实心。

    三个条件缺一不可，因为实测踩过：
      · 卡片里的**绿色农田图片**（320×380）会被当成绿按钮 → 用「够扁」(宽高比≥2.5) 排除；
      · 页面上的**橙色文字行**（+1 / 日期）bbox 又宽又扁，会被当成大按钮 → 用「实心」(密度) 排除；
      · 窄的橙色标签（如「打卡签到」110px）→ 用「够宽」(≥0.25W) 排除。
    """
    if not box:
        return False
    x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0
    if w < W * min_wf or h <= 0 or (w / float(h)) < min_ratio:
        return False
    return color_density(buf, W, pred, box) >= min_density


def page_hash(buf, W, H, step=16):
    """页面的稀疏指纹 —— 用来判断「点完之后页面到底有没有变」。

    为什么需要：实测踩过 —— 小程序**刚打开 ~2 秒**时页面已经渲染好（截图看着正常），
    但点击会被丢掉（事件还没就绪）。不验证的话，脚本会以为「点了但没反应」，
    接着一路滚动，最后什么也没发生。
    """
    h = 0
    for y in range(0, H, step):
        base = y * W * 3
        for x in range(0, W, step):
            i = base + x * 3
            h = (h * 31 + buf[i] + buf[i + 1] * 3 + buf[i + 2] * 7) & 0xFFFFFFFF
    return h


def entry_points(W, H):
    """「进入注册流程」要点的候选位置，**按顺序**尝试。

    为什么是多个：吾享的注册入口通常是**两跳** ——
      ① 活动列表页的第一张活动卡（点进去）
      ② 签到/活动页里滚出来的「立即签到 / 参与」按钮
    实测（来菜）：①在约 (0.50, 0.31)，②在约 (0.42, 0.43)。两者位置不固定，
    所以做成候选列表 + 滚动兜底；页面上的**橙色大按钮**会优先被识别并点击（见 main）。

    可用 WXSIGN_REG_CLICK="0.50,0.31;0.42,0.43" 覆盖（分号分隔多个，比例或像素）。
    """
    raw = envv("WXSIGN_REG_CLICK").replace(" ", "")
    pts = []
    if raw:
        for seg in raw.split(";"):
            if not seg:
                continue
            parts = seg.split(",")
            if len(parts) != 2:
                continue
            try:
                x, y = float(parts[0]), float(parts[1])
            except ValueError:
                continue
            pts.append((int(x * W) if x < 1 else int(x),
                        int(y * H) if y < 1 else int(y)))
    if pts:
        return pts
    # 默认只给一个候选（活动列表的第一张卡）。给多个反而危险 ——
    # 页面切换后同一个坐标的含义会变（实测：在签到页点 (0.42,0.43) 会落到日历区）。
    # 剩下交给「橙色大按钮识别 + 滚动」，那两条比猜坐标可靠。
    return [(int(W * 0.50), int(H * 0.31))]


# ───────────────────────────── 检测 ─────────────────────────────
def detect(buf, W, H):
    """返回 dict：卡片/绿按钮/橙按钮/号码行（含 ✓ 锚点）"""
    card = white_card(buf, W, H)
    green = blob(buf, W, H, is_green, 0, W, 0, H)
    orange = blob(buf, W, H, is_orange, int(W * 0.10), int(W * 0.90), int(H * 0.30), int(H * 0.95), min_hits=8)
    # 「绿按钮」必须同时满足：够宽（≥0.15W，排除绿色小头像/图标）+ 够扁（≤0.08H，排除纵向聚合）
    # 只设上界会把微信聊天列表里的绿色头像当成按钮（实测误判过）
    gw = (green[2] - green[0]) if green else 0
    gh = (green[3] - green[1]) if green else 0
    out = {"card": card, "green": green, "orange": orange,
           "phone_popup": bool(green and W * 0.15 <= gw < W * 0.25 and gh <= H * 0.08),
           "check": None, "rows": []}
    if out["phone_popup"]:
        # ✓ 锚点：绿按钮上方的绿色像素（排除按钮本身）
        out["check"] = blob(buf, W, H, is_green, 0, W, 0, green[1] - 6, step=2, min_hits=2)
        if out["check"]:
            cy = (out["check"][1] + out["check"][3]) // 2
            x0, x1 = (card[0], card[2]) if card else (green[0] - int(W * 0.06), green[2] + int(W * 0.30))
            y0, y1 = (card[1], green[1]) if card else (0, green[1])
            bands = text_bands(buf, W, H, y0, y1, x0, x1)
            step = int(H * ROW_STEP)
            for i in range(1, 7):
                y = cy + (i - 1) * step
                near = [b for b in bands if abs((b[0] + b[1]) // 2 - y) <= step * 0.45]
                if near:
                    out["rows"].append((i, y, (near[0][0] + near[0][1]) // 2))
    return out


def describe(tag, d, W, H):
    print("== %s (%dx%d) ==" % (tag, W, H))
    print("   白卡=%s 绿=%s 橙=%s" % (d["card"], d["green"], d["orange"]))
    if not d["green"] and not d["card"]:
        print("   → 无弹窗（页面本体）")
    elif d["phone_popup"]:
        print("   → 手机号弹窗；✓ 锚点=%s；可点行=%s" % (d["check"], d["rows"]))
    elif d["green"]:
        print("   → 隐私协议弹窗（绿按钮宽 %d）" % (d["green"][2] - d["green"][0]))
    elif d["card"]:
        print("   → 授权说明弹窗（或其它白卡弹窗）")


def analyze(path):
    buf, W, H = grab_file(path)
    d = detect(buf, W, H)
    describe(path, d, W, H)
    return d


# ───────────────────────────── 主流程 ─────────────────────────────
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--index", type=int,
                    default=int(envv("WXSIGN_REGISTER_PHONE_INDEX", "REG_PHONE_INDEX") or 0))
    ap.add_argument("--phone", default=envv("WXSIGN_REGISTER_PHONE", "REG_PHONE"))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--analyze", default="")
    a = ap.parse_args()

    if a.analyze:
        analyze(a.analyze)
        return

    W, H = display_size()
    print("[ui] 屏幕 %dx%d INDEX=%s PHONE=%s OCR=%s"
          % (W, H, a.index or "(未设)", a.phone or "(未设)",
             "有" if ocr_ok() else "无"))

    if in_wechat_main(grab(W, H), W, H):
        print("[ui] ✗ 当前是**微信主窗口**，不在小程序页面 ——"
              "注册必须站在目标小程序的签到/活动页上（那种页面没有左侧图标列）")
        grab_png(W, H, "reg_wrong_page.png")
        sys.exit(4)

    scroll = int(envv("WXSIGN_REG_SCROLL", default="0") or 0)
    if scroll:
        scroll_down(scroll, W, H)      # 有些品牌一进来就要先滚（默认不滚，交给轮次逻辑）
        time.sleep(0.6)
    grab_png(W, H, "reg_00_start.png")

    cands = entry_points(W, H)
    print("[ui] 入口候选=%s" % (cands,))
    granted = False          # 是否已点过手机号弹窗的「允许」

    # 能力升级：**优先按文案找元素**（CDP 读小程序渲染层 DOM），像素识别降级为兜底。
    # 原因：颜色 + 宽高比 + 密度的判据跨品牌必然失效 —— 实测调了三轮仍在补漏洞
    # （把卡片里的绿色农田图当按钮、把橙色文字行当按钮）。文案是同一套模板的，可靠得多。
    # 注意边界：**微信原生弹窗不在小程序 DOM 里**（手机号授权的「允许」），那部分仍走像素。
    ws = ctx = None
    try:
        import wxdom
        ws, ctx = wxdom.open_dom()
        print("[ui] CDP DOM %s" % ("可用（ctx=%d）→ 按文案定位按钮" % ctx if ws
                                   else "不可用 → 退回像素识别"))
    except Exception as e:
        print("[ui] wxdom 不可用（%s）→ 退回像素识别" % e)
    clicked = set()
    clicked_px = set()       # 像素捏到的按钮位置，点过就不再点（否则会在同一处反复空点、占着轮次不滚动）

    for step in range(1, 17):
        buf = grab(W, H)
        d = detect(buf, W, H)

        # ① 手机号授权弹窗（最优先 —— 它出现说明流程就快完了）
        if d["phone_popup"]:
            png = grab_png(W, H, "reg_phone_popup.png")
            rows = d["rows"]
            print("[ui] 手机号弹窗：检测到 %d 个可选号码 %s" % (len(rows), [r[1] for r in rows]))
            if not rows:
                print("[FAIL] 没识别出号码行，截图 %s（可人工核对后手工点）" % png)
                sys.exit(3)

            if a.phone:
                if ocr_ok() and len(rows) > 1:
                    hit = 0
                    for i, y, yc in rows:
                        crop = "%s/row_%d.png" % (SHOT_DIR, i)
                        run("ffmpeg -loglevel error -y -i %s -vf crop=%d:34:%d:%d %s"
                            % (png, int(W * 0.55), int(W * 0.12), max(0, yc - 17), crop))
                        got = ocr_digits(crop)
                        print("       第%d行 识别=%s" % (i, ("*" * max(0, len(got) - 4)) + got[-4:] if got else "(空)"))
                        if got and (got == a.phone or got.endswith(a.phone[-4:])):
                            hit = i
                            break
                    if not hit:
                        print("[FAIL] 没有 phone=%s 对应的行；看 %s" % (a.phone, png))
                        sys.exit(2)
                    a.index = hit
                elif len(rows) == 1:
                    print("[ui] 只有 1 个号码 → 直接用（PHONE 仅留档，请核对 %s）" % png)
                    a.index = 1

            if len(rows) > 1 and not a.index:
                print("[FAIL] 该账号有 %d 个可选号码，脚本不猜：请设 WXSIGN_REGISTER_PHONE_INDEX（1~%d）"
                      % (len(rows), len(rows)))
                print("       可选项见 %s" % png)
                sys.exit(2)
            idx = a.index or 1
            if idx > len(rows):
                print("[FAIL] INDEX=%d 超出范围（只有 %d 个）" % (idx, len(rows)))
                sys.exit(2)

            _, y, _ = rows[idx - 1]
            gx = (d["card"][0] + d["card"][2]) // 2 if d["card"] else W // 2
            print("[ui] 选第 %d 个号码（y=%d），再点「允许」" % (idx, y))
            if not a.dry_run:
                click(gx, y)
                time.sleep(0.8)
                g = d["green"]
                click((g[0] + g[2]) // 2, (g[1] + g[3]) // 2)
                granted = True
                time.sleep(6)
            continue

        # ② 小程序自己渲染的元素 → 按**文案**定位（跨品牌通用，完全不看颜色/尺寸）
        if ws:
            # ⚠️ 每轮都要重探 context：页面切换后是新 WebView（新 ctx），旧 ctx 的 DOM 还留着 ——
            #    硬编码 ctx 会一直读到过期页面，然后在错误坐标上反复空点（踩过）。
            now = wxdom.find_dom_ctx(ws, 20)
            if now:
                ctx = now
            dd = wxdom.scan(ws, ctx)
            # 两级策略（反馈：「别人也不一定叫『立即签到』」）：
            #   ① 同义词根命中（面积最小者）  ② 都没命中 → 按「大 + 靠下 + 文案短」评分兜底
            # 弹窗的确认类优先于页面的签到类（弹窗挡在最上层）。
            is_clicked = lambda it: (it["text"], it["cx"], it["cy"]) in clicked      # noqa: E731
            target = (wxdom.pick_action(dd, "confirm", is_clicked)
                      or wxdom.pick_action(dd, "sign", is_clicked))
            if target:
                print("[ui] DOM 文案「%s」→ 点 (%d,%d)"
                      % (target["text"].replace("\n", " ")[:24], target["cx"], target["cy"]))
                if not a.dry_run:
                    click(target["cx"], target["cy"])
                clicked.add((target["text"], target["cx"], target["cy"]))
                time.sleep(3)
                continue

        g = d["green"]
        px = ((g[0] + g[2]) // 2, (g[1] + g[3]) // 2) if is_button(g, buf, W, is_green) else None
        if px and px not in clicked_px:                 # 隐私协议「同意并继续」
            clicked_px.add(px)
            print("[ui] 点「同意并继续」(%d,%d)" % px)
            if not a.dry_run:
                click(px[0], px[1])
                time.sleep(3)
            continue

        o = d["orange"]
        px = (((o[0] + o[2]) // 2, (o[1] + o[3]) // 2)
              if is_button(o, buf, W, is_orange, min_density=0.5) else None)
        if px and px not in clicked_px:
            # 弹窗里的「授权」/ 页面上的「立即签到」「参与」—— 都是同一类大按钮
            clicked_px.add(px)
            print("[ui] 点橙色大按钮「授权 / 立即签到」(%d,%d)" % px)
            if not a.dry_run:
                click(px[0], px[1])
                time.sleep(4)
            continue

        # ④ 没识别到按钮 —— 已点过「允许」且页面安静了，就认为流程结束
        if granted:
            print("[ui] 已点过「允许」，页面不再有弹窗 → 流程结束（结果以接口复查为准）")
            grab_png(W, H, "reg_done.png")
            return

        # 否则：先按候选入口推进（活动卡 → 签到页），候选用完就滚动把按钮露出来
        if step <= len(cands):
            x, y = cands[step - 1]
            print("[ui] 第%d步：无按钮 → 点入口候选#%d (%d,%d)" % (step, step, x, y))
            if not a.dry_run:
                h0 = page_hash(buf, W, H)
                changed = False
                for attempt in range(1, 4):
                    click(x, y)
                    time.sleep(2.5)
                    buf2 = grab(W, H)
                    if page_hash(buf2, W, H) != h0:
                        print("     → 页面已变化（第 %d 次点击生效）" % attempt)
                        changed = True
                        break
                    print("     → 页面没变，重试点击（%d/3）" % attempt)
                if not changed:
                    print("     ⚠️ 点 3 次页面都没变 —— 坐标可能不对，可调 WXSIGN_REG_CLICK")
        else:
            print("[ui] 第%d步：无按钮 → 滚动找「立即签到」" % step)
            if not a.dry_run:
                scroll_down(3, W, H)
        time.sleep(3.5)

    print("[ui] 轮次用尽 —— 若没注册成功，看 %s/reg_*.png 调 WXSIGN_REG_CLICK" % SHOT_DIR)
    grab_png(W, H, "reg_end.png")
    sys.exit(3)


if __name__ == "__main__":
    main()
