#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""用 CDP 读小程序**渲染层 DOM**，按文案定位元素 —— 不靠颜色/尺寸猜按钮。

## 为什么要有它

像素判据（颜色 + 宽高比 + 像素密度）**跨品牌必然失效**：不同品牌的按钮可能是橙的、
红的、绿的、图片、甚至纯文字链；同一个品牌不同页面也不一样。实测调了三轮阈值仍然
在补漏洞（把卡片里的绿色农田图当按钮、把橙色文字行当按钮……）。

而 CDP 能连到小程序的**渲染层 context**，直接拿到元素的文案与
`getBoundingClientRect()` 中心，坐标系与屏幕一致（2026-09-24 实测：DPR=1、
视口 1280x1025、屏幕 1280x1024）。于是「找按钮」从「猜像素」变成「按文案找元素」，
与主题/颜色/尺寸全都无关。

## 边界（很重要）

**微信原生弹窗不在小程序 DOM 里** —— 比如手机号授权的「允许」、隐私协议的系统层。
那部分读不到，仍要走像素兜底（见 wxreg.py）。
本模块负责的是**小程序自己渲染的东西**：活动卡、签到按钮、「同意并继续」、「授权」。

用法（容器内）：python3 wxdom.py        # 打印当前页面可点元素清单
"""
import json
import os
import socket
import sys
import time

sys.path.insert(0, "/tmp")
import wxcdp          # noqa: E402

SCAN_JS = """(function(){try{
 var out=[], all=document.querySelectorAll('*');
 for(var i=0;i<all.length;i++){
   var e=all[i], t=(e.innerText||'').trim();
   if(!t||t.length>30) continue;
   var r=e.getBoundingClientRect();
   if(r.width<30||r.height<16) continue;
   var cx=r.x+r.width/2, cy=r.y+r.height/2;
   if(cx<0||cx>window.innerWidth||cy<6||cy>window.innerHeight-6) continue;   // 中心必须在视口内
   out.push({tag:e.tagName, cls:String(e.className||'').slice(0,40), text:t,
             cx:Math.round(cx), cy:Math.round(cy),
             w:Math.round(r.width), h:Math.round(r.height),
             area:Math.round(r.width*r.height)});
 }
 return JSON.stringify({vw:window.innerWidth, vh:window.innerHeight, items:out});
}catch(e){return 'ERR:'+(e.message||e)}})()"""

# 找目标用的**词根**（不是精确文案）—— 各品牌文案有差异，所以要宽。
# 反馈：「别人也不一定叫『立即签到』」—— 对，所以这里只留词根，并且可配置覆盖。
# ⚠️ 只收**双字以上**：单字（签/领/抽）会误命中「领取记录」「签到规则」这类相关但非动作的元素（实测踩过）。
SIGN_WORDS = ("签到", "打卡", "参与", "领取", "抽奖", "去参与", "点击参与", "签一下")
CONFIRM_WORDS = ("允许", "同意", "确认", "授权", "继续", "确定", "好的")
# 排除词：含这些的**不点**。它们是「相关但非动作」的元素（记录/规则/导航/其它业务入口），
# 点了就跑到别的页面去了。
EXCLUDE_WORDS = ("记录", "规则", "说明", "明细", "商城", "更多", "上月", "下月", "上个月",
                 "下个月", "排行榜", "历史", "帮助", "客服", "购买", "支付", "下单",
                 "退款", "注销", "退出", "删除", "分享", "邀请", "查看")
# 兼容旧名（wxreg 里还在用）
TARGETS = SIGN_WORDS + CONFIRM_WORDS


def _exclude():
    env = os.environ.get("WXSIGN_REG_EXCLUDE", "").strip()
    if env:
        return tuple(w.strip() for w in env.split(",") if w.strip())
    return EXCLUDE_WORDS


def keywords(mode="sign"):
    """取该模式下的词根。WXSIGN_REG_KEYWORDS 可覆盖（逗号分隔）——
    给「文案完全不在词表里」的品牌留逃生口。"""
    env = os.environ.get("WXSIGN_REG_KEYWORDS", "").strip()
    if env:
        return tuple(w.strip() for w in env.split(",") if w.strip())
    return SIGN_WORDS if mode == "sign" else CONFIRM_WORDS


def pick_action(d, mode="sign", clicked=None, allow_fallback=None):
    """选「最可能是目标按钮」的元素。**两级策略**：

    ① 词根命中（且不含排除词）→ 取**面积最小**的那个（最小 ≈ 最接近真实按钮，
       而不是套着它的容器）
    ② 一个都没命中 → **评分兜底**：挑「面积较大 + 位置靠下」的（主按钮的经验特征）

    为什么要有 ②：不同品牌的按钮文案可能完全在词表之外（"点我"、"去参与"、图标按钮…）。
    ⚠️ 但**默认关闭**（`WXSIGN_REG_FALLBACK=1` 才开）：实测宽松兜底会选到
    「1积分」「已连续签到1天」这类**非按钮**元素，点下去就跑偏了；收紧成「扁+够大+下半屏」
    之后仍会命中文字行。**误点的代价高于不点**，所以默认不启用，宁可由外层去滚动/试固定坐标。
    """
    if not d:
        return None
    clicked = clicked or (lambda it: False)
    if allow_fallback is None:
        allow_fallback = os.environ.get("WXSIGN_REG_FALLBACK", "0") == "1"
    ex = _exclude()
    pool = [it for it in d["items"]
            if not clicked(it)
            # 「已签到」「已连续签到1天」这类是**状态提示**而不是按钮 —— 以「已」开头基本可判。
            and not it["text"].lstrip().startswith("已")
            and not any(w in it["text"] for w in ex)]
    hits = [it for it in pool if any(w in it["text"] for w in keywords(mode))]
    if hits:
        # 命中多个时怎么挑？光用「面积最小」会偏向**说明文字**（如「已连续签到1天」也含「签到」）。
        # 按钮的可靠形态特征是：**扁**（宽高比大）+ **水平居中** + 面积不太大（贴近叶子）。
        # 于是排序键：先「是不是扁按钮」，再「离屏幕中线多近」，最后「面积小者优先」。
        vw = d.get("vw") or 1280

        def _score(it):
            flat = (it["w"] / float(it["h"])) if it["h"] else 0
            return (1 if flat >= 2.5 else 0, -abs(it["cx"] - vw / 2.0), -it["area"])

        hits.sort(key=_score, reverse=True)
        return hits[0]
    if not allow_fallback:
        return None
    # 兜底只接受「**像主按钮**」的元素：扁（宽高比≥2.5）+ 够大 + 位于下半屏。
    # 实测教训：宽松的「面积大优先」会选出「1积分」「已连续签到1天」这类**非按钮**元素，
    # 点下去就跑偏了 —— 宁可这一个都选不出（返回 None，交给滚动/固定坐标兜底），也不要乱点。
    vh = d.get("vh") or 1024
    cand = [it for it in pool
            if it["area"] >= 40000 and it["h"] > 0
            and (it["w"] / float(it["h"])) >= 2.5
            and it["cy"] > vh * 0.35
            and len(it["text"]) <= 14]
    if not cand:
        return None
    cand.sort(key=lambda x: -x["area"])
    return cand[0]


def evaluate(ws, expr, ctx=0, timeout=5.0):
    """执行一段 JS，返回结果（失败/超时返回 None）。"""
    mid = (int(time.time() * 1000) % 100000) + 7
    p = {"expression": expr, "returnByValue": True}
    if ctx:
        p["contextId"] = ctx
    ws.send({"id": mid, "method": "Runtime.evaluate", "params": p})
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            m = json.loads(ws.recv_msg())
        except socket.timeout:
            return None
        except Exception:
            return None
        if m.get("id") == mid:
            return (((m.get("result") or {}).get("result")) or {}).get("value")
    return None


def find_dom_ctx(ws, limit=80):
    """找**小程序页面**的渲染层 contextId。

    ⚠️ 不能只挑「元素最多的」：实测微信自己的搜索页/面板也是个 WebView，元素反而更多
    （285 个 vs 来菜页面的 62 个），会把真正的小程序页面盖过去 —— 踩过。
    小程序页面的特征是它渲染成自定义标签 `wx-view / wx-button / wx-text / wx-image`，
    据此判断；顺便这条也能用来确认「当前在小程序里」。
    """
    cand_js = ("(function(){try{return document.querySelectorAll("
               "'wx-view,wx-button,wx-text,wx-image,wx-scroll-view').length}"
               "catch(e){return -1}})()")
    best, best_n = 0, 0
    for c in range(1, limit + 1):
        try:
            n = int(evaluate(ws, cand_js, ctx=c, timeout=2.0))
        except (TypeError, ValueError):
            continue
        if n > best_n:
            best, best_n = c, n
    return best


def scan(ws, ctx):
    """返回 {"vw","vh","items":[...]}；读不到返回 None。"""
    v = evaluate(ws, SCAN_JS, ctx=ctx, timeout=6.0)
    if not v or str(v).startswith("ERR"):
        return None
    try:
        return json.loads(v)
    except ValueError:
        return None


def find(d, keywords):
    """按文案找元素；**面积小的优先**（更接近真实按钮/叶子节点）。"""
    hits = []
    for it in (d or {}).get("items", []):
        for k in keywords:
            if k in it["text"]:
                hits.append(it)
                break
    hits.sort(key=lambda x: x["area"])
    return hits


def page_text(d):
    """当前页面的全部可见文字（用来判断"我在哪个页面"）。"""
    return " ".join(it["text"] for it in (d or {}).get("items", []))


def open_dom(limit=80):
    """连 CDP 并找到渲染层 context。返回 (ws, ctx)；失败返回 (None, 0)。"""
    try:
        ws = wxcdp.WS()
    except Exception as e:
        print("[dom] 连不上 CDP：%s" % e)
        return None, 0
    try:
        ws.send({"id": 1, "method": "Runtime.enable", "params": {}})
        time.sleep(0.4)
        ctx = find_dom_ctx(ws, limit)
    except Exception as e:
        print("[dom] 探测 context 失败：%s" % e)
        return None, 0
    return (ws, ctx) if ctx else (None, 0)


if __name__ == "__main__":
    ws, ctx = open_dom()
    if not ws:
        print("DOM=不可用")
        sys.exit(1)
    d = scan(ws, ctx)
    if not d:
        print("DOM=读不到内容")
        sys.exit(1)
    print("ctx=%d 视口=%sx%s 可点元素=%d" % (ctx, d["vw"], d["vh"], len(d["items"])))
    for it in d["items"]:
        print("  %-10s %-24s %-24s (%4d,%4d) %dx%d" % (
            it["tag"], it["cls"][:24], it["text"].replace("\n", " ")[:24],
            it["cx"], it["cy"], it["w"], it["h"]))
    ws.close()
