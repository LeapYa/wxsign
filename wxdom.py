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
SIGN_WORDS = ("签到", "打卡", "参与", "领取", "抽奖", "去参与", "点击参与",
              # 「绘图区域」是**吾享活动页里游戏区（转盘/抽奖 canvas）的占位文字** ——
              # 实测农耕记的活动页上只剩「绘图区域」「我的奖品」这几个词，
              # 没有它就没有任何可点入口；点它 = 点游戏区 = 触发「注册后才能玩」的授权流程。
              # 蜀大侠的活动页也有它（那句「绘图区域 注册后可获取当前游戏抽奖机会」）。
              "绘图区域")
CONFIRM_WORDS = ("允许", "同意", "确认", "授权", "继续", "确定", "好的")
# 「登录/注册」入口 —— 专门用来区分**小程序自己的注册弹层**和**微信原生的手机号授权弹窗**。
# 为什么需要：像素判据（白卡 + 卡内通栏绿按钮）会把小程序自己的「注册登录」底部弹层
# 也认成手机号弹窗。实测绿茵阁西餐厅：弹层是「Hi，神秘食客 / 更好的会员服务，注册登录后
# 即可体验」+ 一颗绿色「立即登录」，被判成手机号弹窗 → 走到「没识别到白卡不敢盲点」的
# 安全阀 → rc=3 直接放弃。而那条边界很清楚：**微信原生弹窗不在小程序 DOM 里**，
# 所以「DOM 里还能找到登录/注册入口」= 这是小程序自己的弹层，点它就对了。
LOGIN_WORDS = ("立即登录", "一键登录", "去登录", "登录", "立即注册", "免费注册", "注册",
               "成为会员", "开通会员", "立即开通")
# 「注册表单的**提交**按钮」——比 LOGIN_WORDS 更进一步：登录入口只是「打开表单」，
# 这个是「把表单交上去」。实测绿茵阁西餐厅的注册表单（wx-user-info 组件弹出）
# 底部是一颗 WX-BUTTON「确认授权开通并绑定会员」—— 手机号此时**已经授权到手**
# （表单里已显示 190****7634），点它就是最后一步，点完 member/single 就从 401 变 200。
# 为什么要单独一个词表：它是**兜底优先级最高**的一类，必须在「找登录入口」之前试，
# 否则表单已经打开时还会去点底层那个「立即登录」。
SUBMIT_WORDS = ("确认授权", "开通并绑定", "绑定会员", "开通会员", "确认开通", "立即注册",
                "完成注册", "注册并", "提交", "绑定")
# 排除词：含这些的**不点**。它们是「相关但非动作」的元素（记录/规则/导航/其它业务入口），
# 点了就跑到别的页面去了。
EXCLUDE_WORDS = ("记录", "规则", "说明", "明细", "商城", "更多", "上月", "下月", "上个月",
                 "下个月", "排行榜", "历史", "帮助", "客服", "购买", "支付", "下单",
                 "退款", "注销", "退出", "删除", "分享", "邀请", "查看",
                 # 「再累计签到15天得好券」这类**进度说明**也含「签到」，实测被误点过
                 "得好券", "奖励", "累计",
                 # 「签到成功」是**签到结果的状态条**（今天已签），不是可点的动作；
                 # 它也含「签到」词根，实测被选中并连点 4 次白费轮次。
                 # 通用规律：带「成功」的通常是"结果/状态"，动作按钮一般写「立即/去/领取/参与」。
                 "成功")
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
    if mode == "confirm":
        return CONFIRM_WORDS
    if mode == "login":
        return LOGIN_WORDS
    if mode == "submit":
        return SUBMIT_WORDS
    return SIGN_WORDS


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
    # 动作按钮**不可能**以这些字开头：
    #   「已」→ 状态提示（已签到 / 已连续签到1天）
    #   「不」→ 否定按钮（不同意）。⚠️ 「不同意」里含「同意」词根，实测被当成确认键点过 ——
    #          那一下会把隐私协议**拒掉**，整个注册流程就废了，属于危险误点。
    NOT_ACTION = ("已", "不")
    # 文案长度上限：**说明文字往往是一整句**，按钮/标签的文案很短。
    # 实测被误点的纯说明：「每个星期三,期间11:30-16:00可参与」(18 字，含「参与」)。
    # ⚠️ 但**含这些词的例外**：实测蜀大侠的活动页里，触发注册的入口恰恰是长文案
    #    「注册后可获取当前游戏抽奖机会」（它所在的抽奖交互区才是可点区域）——
    #    一律按长度砍会把这个入口也砍掉，流程就卡死在活动页。
    LONG_OK = ("注册", "授权")
    pool = [it for it in d["items"]
            if not clicked(it)
            # 顶部 55px 是小程序的**标题栏/导航条**，那里的文字点了没意义（实测会浪费轮次）
            and it["cy"] > 55
            and not it["text"].lstrip().startswith(NOT_ACTION)
            and (len(it["text"]) <= 10 or any(w in it["text"] for w in LONG_OK))
            and not any(w in it["text"] for w in ex)]
    hits = [it for it in pool if any(w in it["text"] for w in keywords(mode))]
    if hits:
        # 命中多个时怎么挑？光用「面积最小」会偏向**说明文字**（如「已连续签到1天」也含「签到」）。
        # 按钮的可靠形态特征是：**扁**（宽高比大）+ **水平居中** + 面积不太大（贴近叶子）。
        # 于是排序键：先「是不是扁按钮」，再「离屏幕中线多近」，最后「面积小者优先」。
        vw = d.get("vw") or 1280

        def _score(it):
            flat = (it["w"] / float(it["h"])) if it["h"] else 0
            # ⚠️ 第二项「文案越短越像按钮」是踩出来的：
            #    LONG_OK 那个例外（「注册/授权」允许长文案）本来是为蜀大侠的长入口加的，
            #    结果绿茵阁西餐厅那层弹层的**整句说明**
            #    「Hi，神秘食客 更好的会员服务，注册登录后即可体验」（25 字，含「注册」）
            #    也通过了长度过滤，而且又扁又宽、评分高过真正的「立即登录」按钮 →
            #    整整几轮都在点那行说明文字，弹层永远不消失，注册自然没发生。
            #    按钮文案通常很短（2~6 字），说明文字才长 —— 用长度当第二判据最稳。
            return (1 if flat >= 2.5 else 0, -len(it["text"]),
                    -abs(it["cx"] - vw / 2.0), -it["area"])

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


def evaluate(ws, expr, ctx=0, timeout=8.0):
    """执行一段 JS，返回结果（失败/超时返回 None）。

    ⚠️ **必须跳过 error 响应**（2026-09-26 修正，这是本模块一度整体失效的真因）：
    WMPFDebugger 代理会把每条请求回**两次** —— 一条
    `{"id":N,"error":{"code":-32600,"message":"Duplicate \\`id\\` in protocol request"}}`、
    一条正常的 `{"id":N,"result":…}`。原实现「id 匹配就返回」，于是经常撞上 error 那条，
    `result` 取不到 → 一律返回 None，整个模块报 `DOM=不可用`。
    逻辑层的轻量调用恰好常拿到 result 那条，所以那边一直没暴露。
    """
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
        if m.get("id") != mid:
            continue
        if "error" in m:
            continue                      # ⚠️ 代理重复回的那条 error，继续等真正的 result
        return (((m.get("result") or {}).get("result")) or {}).get("value")
    return None


def find_ctx_with(ws, selectors, limit=80, timeout=12.0, visible=False):
    """找**哪个渲染层 ctx 里出现了这些选择器**。返回 {ctx: 命中数}（命中数 > 0 才有）。

    ⭐ 为什么必须有这个函数（2026-09-26 的关键教训）：

    同一个逻辑层页面**可以有多个渲染面**。实测呷哺签到页 + 授权层弹出时：
        ctx=6  302 节点 / 161 个 wx-* 标签 / **没有授权层**  ← 底层页面（「我的」页）
        ctx=9  221 节点 / 152 个 wx-* 标签 / **有授权层**    ← `#authorization` 在这儿
    两者 url 一样、title 都是 `Page-Frame`。

    `find_dom_ctx` 是「谁元素多选谁」，于是**必然选到 ctx=6**，再去里面找授权层当然找不到 ——
    我一度因此断定「授权层不在渲染层」，是错的。**授权层一直是普通 view 组件**，
    源码里就是 `onAuthorization(){this.selectComponent("#authorization").show()}`
    （`pluginMarketing/components/authorization-4e03c673`）。

    所以正确姿势不是「先挑一个 ctx 再在里面找」，而是**反过来：先说要找什么，再看哪个 ctx 有**。
    `selectors` 传 CSS 选择器列表（如 `["#authorization"]`）。

    ⚠️ `visible=True` 时只数**当前真的可见**的 —— 很多组件 hide 之后**节点仍留在 DOM 里**，
    只按「选择器在不在」判断会误以为它还开着。实测：呷哺授权层关闭后 `#authorization`
    一直在树上，只有矩形会塌成 0。

    ⚠️⚠️ **但不能只看命中节点自己的矩形**（2026-09-26 实测踩到，这条很反直觉）：
    很多组件是「**容器根塌陷、内容在子节点**」。授权层就是典型 ——
        `WX-STD-AUTHORIZATION#authorization`  自身 w=0 h=0 x=0 y=1188（视口 410x779）
        但它的子树里有 27 个可见节点，其中一个 410x779（铺满整屏）
    如果只判根节点矩形，就会把**明明弹着的授权层判成不可见** → 后续整条链路全错。
    所以这里的判据是「**命中节点自身或其任一子孙可见**」。
    """
    if visible:
        # 自身可见 → 计 1；否则看它的子孙里有没有可见的（容器根塌陷的情况，见上面的 ⚠️⚠️）
        js = ("(function(){try{var n=0;var s=%s;"
              "var ok=function(e){var r=e.getBoundingClientRect();"
              "if(r.width<6||r.height<6)return false;"
              "var cx=r.x+r.width/2,cy=r.y+r.height/2;"
              "return cx>=0&&cx<=window.innerWidth&&cy>=0&&cy<=window.innerHeight};"
              "for(var i=0;i<s.length;i++){var ns=document.querySelectorAll(s[i]);"
              "for(var j=0;j<ns.length;j++){"
              "if(ok(ns[j])){n++;continue}"
              "var kids=ns[j].querySelectorAll('*');"
              "for(var k=0;k<kids.length;k++){if(ok(kids[k])){n++;break}}"
              "}}return n}catch(e){return -1}})()"
              % json.dumps(list(selectors), ensure_ascii=False))
    else:
        js = ("(function(){try{var n=0;var s=%s;"
              "for(var i=0;i<s.length;i++){try{n+=document.querySelectorAll(s[i]).length}"
              "catch(e){}}return n}catch(e){return -1}})()"
              % json.dumps(list(selectors), ensure_ascii=False))
    return _broadcast(ws, js, limit, timeout, kind="int")


TEXT_JS = ("(function(){try{return ((document.body&&document.body.innerText)||'')"
           ".replace(/\\s+/g,' ')}catch(e){return 'ERR'}})()")


def find_ctx_by_text(ws, kws, limit=40, timeout=8.0, prefer=None):
    """找**页面文字里同时包含全部关键词**的 ctx。`kws` 传字符串或元组。返回 ctx id，没有返回 0。

    ⭐ 为什么必须用**广播**、不能逐个 ctx `evaluate`（2026-09-27 血泪实测）：
    逐个问的话，**不存在的 ctx 根本不回包**，每次都要干等到 socket 超时。
    实测「24 个 ctx × 3s 超时」→ 一次搜索就要 70 秒；脚本连着搜三次 →
    **250 秒都没打出第一行**，被外层 `timeout` 杀掉（rc=124）时**日志一片空白** ——
    现场看起来像「连不上 CDP」，其实只是慢死，极难判断。
    `_broadcast` 是「一口气全发、统一收」，**不管有多少 ctx 都只要一轮（~3 秒）**。

    多关键词（元组）用于「要多个词才敢确认是哪一页」的情况 —— 实测 OPPO 首页正文里
    有「附近门店」，光看「门店」会误判，必须「首页 + 权益 + 我的」一起看。
    """
    kws = (kws,) if isinstance(kws, str) else tuple(kws)
    if not kws:
        return 0
    got = _broadcast(ws, TEXT_JS, limit, timeout, kind="str")
    if prefer and all(k in got.get(prefer, "") for k in kws):
        return prefer
    for c in sorted(got):
        if all(k in got[c] for k in kws):
            return c
    return 0


def _broadcast(ws, js, limit, timeout, kind="int"):
    """把一段 JS 盲撒给 ctx 1..limit，返回 {ctx: 值}。

    `kind`：
      · `"int"` —— 值当**正整数**看，>0 才收（`find_ctx_with` 用）；
      · `"str"` —— 值当**字符串**看，非空且不以 `ERR` 开头就收（`find_ctx_by_text` 用，
        典型是页面文字 innerText）。

    ⚠️ **必须「批量发、统一收」**（2026-09-26 修正）：原实现是「逐个 ctx 发一条、立刻等 2 秒」，
    在手机竖版窗口形态下**一条都收不到**（响应比 2 秒慢），于是整模块报「DOM=不可用」——
    而实际上渲染层好好地在 ctx 6/8/9/12（探针实测 wxTags=119/140/120/52）。
    改成一口气发完再统一收（和 `survey/qm_eval.js` 同一套路）。
    ```
    """
    base = 5000
    for c in range(1, limit + 1):
        ws.send({"id": base + c, "method": "Runtime.evaluate",
                 "params": {"expression": js, "returnByValue": True, "contextId": c}})
    out = {}
    t0 = time.time()
    # 收集窗口 12 秒：这轮要一口气收 80 条响应，收不完的话**残留响应会拖慢后面每一次读取**
    # （evaluate 得先把它们读掉才能等到自己那条）—— 实测 6 秒不够，会连锁失败。
    #
    # ⭐ 但**别干等满**（2026-09-26 修正）：CDP 对不存在的 ctx 是不回包的，而 `recv_msg`
    #    会一直阻塞到 socket 超时（`WS()` 默认 8s）才抛 `socket.timeout`。真机上表现为
    #    「每次定位先卡十来秒、像卡死了」。这里两处一起收紧：
    #      · 用 socket 自身的超时把「静默」翻译成异常 —— 一断就 break，不等满 timeout；
    #      · 顺手把 socket 超时临时压到 2.5s（调用方之后自己会重置）。
    #    正常情况（响应 200~600ms 到齐、静默 2.5s 后断）整个函数从 12s 降到 ~3s。
    old_to = None
    try:
        old_to = ws.s.gettimeout()
        ws.s.settimeout(2.5)
    except Exception:
        pass
    try:
        while time.time() - t0 < timeout:
            try:
                m = json.loads(ws.recv_msg())
            except Exception:
                break
            mid = m.get("id")
            if not (isinstance(mid, int) and base < mid <= base + limit):
                continue
            v = ((m.get("result") or {}).get("result") or {}).get("value")
            if v is None:
                continue
            if kind == "str":
                s = str(v).strip()
                if s and not s.startswith("ERR"):
                    out[mid - base] = s
                continue
            try:
                n = int(v)
            except (TypeError, ValueError):
                continue
            if n > 0:
                out[mid - base] = n
    finally:
        if old_to is not None:
            try:
                ws.s.settimeout(old_to)
            except Exception:
                pass
    return out


def find_dom_ctx(ws, limit=80):
    """找**小程序页面**的渲染层 contextId（默认那个 —— 一般就是底层页面）。

    ⚠️ 不能只挑「元素最多的」：实测微信自己的搜索页/面板也是个 WebView，元素反而更多
    （285 个 vs 来菜页面的 62 个），会把真正的小程序页面盖过去 —— 踩过。
    小程序页面的特征是它渲染成自定义标签 `wx-view / wx-button / wx-text / wx-image`，
    据此判断；顺便这条也能用来确认「当前在小程序里」。

    ⚠️ 多个渲染面时它只会返回**元素最多的那个**（实测是底层页面，不是弹层）。
    要找弹层/覆盖层请用 `find_ctx_with()` —— 见那个函数的注释。
    """
    cand_js = ("(function(){try{return document.querySelectorAll("
               "'wx-view,wx-button,wx-text,wx-image,wx-scroll-view').length}"
               "catch(e){return -1}})()")
    hits = _broadcast(ws, cand_js, limit, 12.0)

    # ⭐ 最终判据是「**能不能真扫出内容**」，不是「自定义标签多不多」。
    #    实测（2026-09-26 云微、手机竖版窗口）：ctx 6 的自定义标签最多（140 个）却扫不出内容，
    #    真正可用的是 ctx 12（119 个）。只按数量挑就会稳定选错，整模块报「DOM=读不到内容」。
    for c, _n in sorted(hits.items(), key=lambda kv: -kv[1]):
        if scan(ws, c):
            return c
    return 0


def scan(ws, ctx):
    """返回 {"vw","vh","items":[...]}；读不到返回 None。

    超时给到 30 秒：SCAN_JS 要遍历整棵渲染树逐个 `getBoundingClientRect()`，本身就重；
    而且这个等待窗口**必须大于连接的 socket 超时**（`open_dom` 给的是 25 秒），
    否则慢响应还没到、循环就先因为「等够久了」退出去，照样拿到 None。
    """
    v = evaluate(ws, SCAN_JS, ctx=ctx, timeout=30.0)
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


# ⭐ 按**结构**取元素几何 —— 不猜颜色、不假设只有一层渲染面。
#
# 判据是「这个文案/这些选择器所在的**最小盒子**」，因此：
#   · 与主题色无关（呷哺橙 / 李先生蓝 / 将来任何商户色，同一套代码通吃）
#   · 与窗口形态无关（坐标系是页面自己的 CSS 像素，`wxreg.click` 再换算成屏幕）
#   · 不受「多个渲染面」影响（由调用方先用 find_ctx_with 挑对 ctx）
#
# why 不用 `querySelector('#authorization')` 直接拿根节点矩形：实测组件根节点
# 报的是 `w:0 h:0, x:0 y:832`（组件根没有自己的盒子，真盒子在子节点上），
# 所以必须**往下找最小的、真有尺寸的、文案匹配的叶子**。
RECT_JS = r"""(function(){
  var WANT = %s;          // 要匹配的文案片段
  var SEL  = %s;          // 限定在这些选择器之下找（空 = 全文档）
  try{
    var root = document;
    if (SEL && SEL.length) root = document.querySelector(SEL) || document;
    var all = [root];
    var nodes = root.querySelectorAll ? root.querySelectorAll('*') : [];
    for (var i = 0; i < nodes.length; i++) all.push(nodes[i]);
    var out = [];
    for (var j = 0; j < all.length; j++) {
      var e = all[j];
      var t = (e.innerText || '').replace(/\s+/g, ' ').trim();
      if (!t) continue;
      var ok = false;
      for (var k = 0; k < WANT.length; k++) {
        if (t.indexOf(WANT[k]) >= 0) { ok = true; break; }
      }
      if (!ok) continue;
      var r = e.getBoundingClientRect();
      if (r.width < 8 || r.height < 8) continue;      // 丢掉组件根那种零尺寸壳子
      // ⚠️ 还要排除「正在播离场动画」的节点 —— 它们的矩形会塌成 0 或移到视口外，
      //    但节点**还在 DOM 里**。实测踩过：授权层关闭动画期间 `暂时跳过` 还在树上，
      //    拿它的假坐标去点会点到 (6,590) 这种离谱位置。判据：中心必须在视口内。
      var cx = r.x + r.width / 2, cy = r.y + r.height / 2;
      if (cx < 0 || cx > window.innerWidth || cy < 0 || cy > window.innerHeight) continue;
      out.push({tag: e.tagName, id: e.id || '', cls: String(e.className || '').slice(0, 60),
                text: t.slice(0, 40),
                cx: Math.round(cx), cy: Math.round(cy),
                w: Math.round(r.width), h: Math.round(r.height),
                area: Math.round(r.width * r.height)});
    }
    return JSON.stringify({vw: window.innerWidth, vh: window.innerHeight, items: out});
  }catch(e){ return 'ERR:' + (e.message || e); }
})()"""


def rect_of(ws, ctx, want, selector="", timeout=20.0):
    """在 `ctx` 里按**文案**取元素矩形，返回按面积升序的列表。

    `want` = 文案片段列表（如 `("手机号一键登录","一键登录")`）；
    `selector` = 限定范围（如 `"#authorization"`，空 = 整页）。
    返回的 `items` 与 `scan()` 同构（`cx/cy/w/h/area/text/tag/cls`），可直接喂 `find` 之外的排序逻辑。

    失败返回 None。**这是替代「按颜色找按钮」的正式手段。**
    """
    v = evaluate(ws, RECT_JS % (json.dumps(list(want), ensure_ascii=False),
                                json.dumps(selector)), ctx=ctx, timeout=timeout)
    if not v or str(v).startswith("ERR"):
        return None
    try:
        d = json.loads(v)
    except ValueError:
        return None
    d["items"].sort(key=lambda x: x["area"])
    return d


def clickable(ws, ctx, selector, timeout=20.0):
    """取某个选择器下**所有有尺寸的元素**的几何（不给文案也行）。

    用来找「没有文案的可点元素」—— 最典型的是**授权协议的勾选框**（它就是个圆圈，
    里面没有文字）。之前只能靠「它旁边那行小字的左边界往左推 CHECK_GAP 像素」，
    本质还是在猜偏移量；有了这个函数就能**直接按选择器定位那个圆圈本身**。
    """
    js = ("(function(){try{var ns=document.querySelectorAll(%s);var out=[];"
          "for(var i=0;i<ns.length;i++){var e=ns[i];var r=e.getBoundingClientRect();"
          "if(r.width<6||r.height<6)continue;"
          "var cx=r.x+r.width/2,cy=r.y+r.height/2;"
          "if(cx<0||cx>window.innerWidth||cy<0||cy>window.innerHeight)continue;"   # 排除离场动画中的节点
          "out.push({tag:e.tagName,id:e.id||'',cls:String(e.className||'').slice(0,60),"
          "cx:Math.round(cx),cy:Math.round(cy),"
          "w:Math.round(r.width),h:Math.round(r.height),"
          "area:Math.round(r.width*r.height)});}"
          "return JSON.stringify({vw:window.innerWidth,vh:window.innerHeight,items:out});"
          "}catch(e){return 'ERR:'+(e.message||e)}})()" % json.dumps(selector))
    v = evaluate(ws, js, ctx=ctx, timeout=timeout)
    if not v or str(v).startswith("ERR"):
        return None
    try:
        d = json.loads(v)
    except ValueError:
        return None
    d["items"].sort(key=lambda x: x["area"])
    return d


def page_text(d):
    """当前页面的全部可见文字（用来判断"我在哪个页面"）。"""
    return " ".join(it["text"] for it in (d or {}).get("items", []))


def open_dom(limit=80):
    """连 CDP 并找到渲染层 context。返回 (ws, ctx)；失败返回 (None, 0)。

    ⚠️ socket 超时给到 25 秒（`wxcdp.WS` 默认只有 8 秒）—— **这是本模块一度整体失效的真因**：
    渲染层的 `Runtime.evaluate` 响应可能超过 8 秒（SCAN_JS 要遍历整棵渲染树），
    8 秒一到 `recv_msg()` 抛 socket.timeout，`evaluate` 就把它当「读不到」返回 None，
    于是 `find_dom_ctx` 里所有候选都判失败 → 整个模块报 `DOM=不可用/读不到内容`。
    而逻辑层的轻量调用（读 appId、调页面方法）很快，所以那边一直没暴露这个问题。
    """
    try:
        ws = wxcdp.WS(timeout=25)
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
