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


def ctx_with_text(ws, kw, limit=24, prefer=None):
    """遍历渲染上下文，返回**第一个**页面文字含 `kw` 的 ctx id；没有返回 0。

    `kw` 可以是字符串（含即命中）或**元组**（全部都要含）——
    后者用于「底部导航那一页」这种要多个词才敢确认的情况（实测：
    首页文字里有『附近门店』，光看『门店』会误判，必须「首页 + 我的 + 权益」一起看）。

    为什么不写死 ctx 号：微信每次打开小程序、每次弹层的 ctx 号都会变
    （实测同一功能这次是 7、上次是 9）；而且弹层常渲染在**另一个渲染面**上，
    `find_dom_ctx()`（谁节点多选谁）必然选到底层页面 —— 见模块 docstring 坑 1。

    ⚠️ 为什么 `prefer` + `timeout=3`：上下文可能有几十个，每个 `evaluate` 都要走一次
    CDP 往返。**首版用 `timeout=6.0` 且从头遍历，最坏 40×6 = 240 秒** ——
    实测就是卡在这里（跑了 4 分钟还没到第 ② 步）。
    现在：先试上次命中的 ctx，单个超时压到 3 秒；
    而且**只要拿到第一个含关键词的就返回**，正常路径 1~2 次往返就够。
    """
    kws = (kw,) if isinstance(kw, str) else tuple(kw)
    order = ([prefer] if prefer else []) + [c for c in range(1, limit + 1) if c != prefer]
    for c in order:
        v = wxdom.evaluate(ws, EXPR_TEXT, ctx=c, timeout=3.0)
        s = str(v or "")
        if not s or s.startswith("ERR"):
            continue
        if all(k in s for k in kws):
            return c
    return 0


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


def wait_allow(ws, wait=20.0):
    """等微信**原生**「允许」框并点掉（复用 wxqm_auth 的像素判据）。

    只有这一步允许用颜色：它的配色由**微信客户端**决定，不随品牌/活动变。
    """
    t0 = time.time()
    while time.time() - t0 < wait:
        W, H = wxreg.win_size()
        try:
            buf = wxreg.grab(W, H, win=True)
        except Exception as e:
            print("[oppo-auth] 截屏失败：%s" % e)
            return False
        hit = wxqm_auth.find_allow_btn(buf, W, H)
        if hit:
            print("[oppo-auth] 点微信原生「允许」(%d,%d)" % hit)
            wxreg.click(hit[0], hit[1])
            time.sleep(2.5)
            return True
        time.sleep(1.0)
    print("[oppo-auth] ⚠️ 等了 %.0fs 没看到原生「允许」框（可能之前已授权过 → 微信静默返回）" % wait)
    return False


def main():
    ws, _ = wxdom.open_dom(limit=80)
    if not ws:
        print("[oppo-auth] ✗ 连不上 CDP（hook 挂上了吗？小程序开着吗？）")
        return 2

    wxreg.raise_miniapp()

    mine_ctx = ctx_with_text(ws, MINE_KW)
    login_ctx = ctx_with_text(ws, LOGIN_KW)
    print("[oppo-auth] 我的页 ctx=%s，登录页 ctx=%s" % (mine_ctx, login_ctx))

    # ── ⓪ 既不在「我的」页也不在登录页 → 先在**首页**点底部导航「我的」 ──
    #    小程序打开时默认停在首页（实测 ctx 4、页面文字 1159 字，全是商品名），
    #    底部导航是「首页 分类 门店 权益 我的」—— 点「我的」才进个人中心。
    #    ⚠️ 关键词要「首页+权益+我的」一起看：首页正文里有「附近门店」，
    #       只看「门店」会误判成已完成。
    if not mine_ctx and not login_ctx:
        home_ctx = ctx_with_text(ws, ("首页", "权益", "我的"))
        if not home_ctx:
            print("[oppo-auth] ✗ 找不到底部导航（首页/权益/我的）—— 小程序开着吗？")
            return 3
        print("[oppo-auth] 当前停在首页（ctx=%d）→ 点底部「我的」" % home_ctx)
        wxreg.refresh_page(ws, home_ctx)
        if not click_text(ws, home_ctx, "我的", what="底部导航「我的」"):
            return 3
        time.sleep(3.5)
        mine_ctx = ctx_with_text(ws, MINE_KW)
        login_ctx = ctx_with_text(ws, LOGIN_KW)
        print("[oppo-auth] 切页后：我的页 ctx=%s，登录页 ctx=%s" % (mine_ctx, login_ctx))

    # ── ① 没有登录页就先从「我的」页点「登录」 ──
    if not login_ctx:
        if not mine_ctx:
            print("[oppo-auth] ✗ 既没有登录页、也没有「个人中心」—— 小程序开着吗？")
            return 3
        wxreg.refresh_page(ws, mine_ctx)
        if not click_text(ws, mine_ctx, "登录", what="「登录」"):
            return 3
        time.sleep(3.0)
        login_ctx = ctx_with_text(ws, LOGIN_KW)
        if not login_ctx:
            print("[oppo-auth] ✗ 点「登录」后没等到登录页")
            return 3
        print("[oppo-auth] 登录页已打开：ctx=%d" % login_ctx)

    # ── ② 勾选协议 + 点「手机号快捷登录」 ──
    wxreg.refresh_page(ws, login_ctx)
    # 勾选框逻辑**复用 wxqm_auth._check_agree**（它含「先看已勾态」那一步 ——
    # 少那一步会把本来就勾着的又点一遍、变成取消）。本文件只传商户自己的文案。
    wxqm_auth._check_agree(ws, login_ctx, agree_words=OPPO_AGREE_WORDS)
    time.sleep(0.6)
    # 勾选后 DOM 会重排，坐标必须重新取
    login_ctx = ctx_with_text(ws, LOGIN_KW) or login_ctx
    if not click_text(ws, login_ctx, LOGIN_KW, what="「手机号快捷登录」"):
        return 3

    # ── ③ 微信原生「允许」框 ──
    wait_allow(ws, wait=20.0)

    # ── ④ 验证：回到「我的」页应能看到账号信息 ──
    time.sleep(3.0)
    mine_ctx = ctx_with_text(ws, MINE_KW)
    txt = ""
    if mine_ctx:
        v = wxdom.evaluate(ws, EXPR_TEXT, ctx=mine_ctx, timeout=8.0)
        txt = str(v or "")
    if mine_ctx and "登录账号" not in txt:
        print("[oppo-auth] ✓ 登录成功（「我的」页已不是「登录账号」状态）")
        return 0
    if mine_ctx:
        print("[oppo-auth] ✗ 「我的」页仍显示「登录账号」→ 未登录")
        print("[oppo-auth]   页面文字前 160 字：%s" % txt[:160])
        return 1
    print("[oppo-auth] ? 找不到「我的」页，无法判断（授权框可能已点，去跑 wxoppo.py 验证会话）")
    return 4


if __name__ == "__main__":
    sys.exit(main())
