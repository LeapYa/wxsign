#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""OPPO 商城：**备选**方式发现当前档期的签到 activityId（驱动 H5 + CDP hook 抓包）。

为什么要有这条备选：
  主路径是「纯 HTTP 拉 H5 的 HTML，解析 SSR 内联的 DSL」（见 wxsign.py 的
  `oppo_discover_activity`）—— 快（~0.3s）、不碰 UI，是首选。
  但万一 OPPO 前端改版：
    · 不再 SSR（DSL 改由 XHR 拿）→ HTML 里就没有 `SignIn_*` 组件了；
    · 或字段名/结构变了 → 正则失效；
  这时用这条**物理兜底**：真把 H5 打开、注入 hook 抓它实际发的请求，从
  `getSignInDetail?activityId=<id>` 里取 —— 更不容易被前端改版弄坏。

⚠️ 必须知道的三件事（不知道就会以为"抓不到"）：
  1. H5 是 **web-view = 独立 CDP target**（`type=webview`）——主 page 的 ctx 列表里**没有**它
     （只有 ctx 1=微信浮层、ctx N=小程序页面），主 page 上 `Network.enable` 也抓不到它的请求。
     必须 `Target.attachToTarget({targetId, flatten:True})` 拿 sessionId，之后命令/事件都带它。
  2. **初始请求发生在 attach 之前** → 只 attach 抓不到；
     而 `Page.reload` 刷的是**整个 target**，等于**把 H5 关掉**（webview 变回 page-frame.html）。
     ✅ 正确做法：`Page.addScriptToEvaluateOnNewDocument` 注入 XHR/fetch hook，
        再在 H5 **内部** `Runtime.evaluate("location.reload()")`。
  3. 签到页的入口是「我的」页右上角那个**红色图片按钮**（`WX-MINE-ACCOUNT` 里的
     `WX-RIGHT-BTN`，76x34）—— **它没有文字**，按文案找不到，要用 CSS 选择器。

用法（容器内跑）：
    python3 wxoppo_act.py [等待秒数，默认 90]
输出（宿主机解析这一行）：
    OPPO_ACT=<activityId>          # 成功
    OPPO_ACT=                      # 失败（日志里有原因）
"""
import json
import re
import sys
import time

sys.path.insert(0, "/tmp")
import wxdom
import wxreg

try:
    import wxcdp
except Exception as e:                                    # noqa: BLE001
    print("[act] 导入 wxcdp 失败：%s" % e)
    print("OPPO_ACT=")
    sys.exit(1)


HOOK = """
(function(){
  if(window.__hooked) return; window.__hooked=1; window.__reqs=[];
  try{
    var oOpen=XMLHttpRequest.prototype.open, oSend=XMLHttpRequest.prototype.send;
    XMLHttpRequest.prototype.open=function(m,u){this.__u=u;this.__m=m;return oOpen.apply(this,arguments)};
    XMLHttpRequest.prototype.send=function(b){
      try{window.__reqs.push([this.__m||'?',String(this.__u||''),String(b||'').slice(0,600)])}catch(e){}
      return oSend.apply(this,arguments)};
    var of=window.fetch;
    if(of){window.fetch=function(u,o){
      try{window.__reqs.push([(o&&o.method)||'GET',String((u&&u.url)||u),String((o&&o.body)||'').slice(0,600)])}catch(e){}
      return of.apply(this,arguments)}};
  }catch(e){}
})();
"""


def wait_id(ws, want, timeout=20):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            m = json.loads(ws.recv_msg())
        except Exception:
            return None
        if m.get("id") == want:
            return m
    return None


def find_h5(ws, wid):
    """找承载签到页的 webview target；没有返回 None。"""
    ws.send({"id": wid, "method": "Target.getTargets", "params": {}})
    infos = (((wait_id(ws, wid) or {}).get("result")) or {}).get("targetInfos") or []
    for t in infos:
        if t.get("type") == "webview" and "opposhop" in (t.get("url") or ""):
            return t
    return None


def open_h5_by_ui():
    """走 UI 把 H5 打开：「我的」页 → 点右上角签到图片按钮。返回 True/False。"""
    wsd, _ = wxdom.open_dom(limit=80)
    if not wsd:
        print("[act] open_dom 失败（CDP 连不上？小程序开着吗？）")
        return False
    wxreg.raise_miniapp()
    time.sleep(0.5)
    wxdom.dismiss_dialogs(wsd, tag="[act]", quiet=True)     # 一打开就弹的「订阅提示」会吃掉点击
    got = wxdom._broadcast(wsd, wxdom.TEXT_JS, 40, 8.0, kind="str")
    mine = next((c for c in sorted(got) if "个人中心" in got[c]), None)
    if not mine:
        print("[act] 不在「我的」页 → 点底部「我的」")
        wxreg.refresh_page()
        wxreg.click(352, 741)
        time.sleep(3.5)
        got = wxdom._broadcast(wsd, wxdom.TEXT_JS, 40, 8.0, kind="str")
        mine = next((c for c in sorted(got) if "个人中心" in got[c]), None)
    if not mine:
        print("[act] 进不去「我的」页；当前 ctx 文字：")
        for c in sorted(got):
            print("        ctx %-3d %s" % (c, got[c][:80]))
        return False

    wxreg.refresh_page(wsd, mine)
    raw = wxdom.evaluate(wsd, """(function(){try{
      var sels=['wx-mine-account wx-right-btn','wx-right-btn','[class*=right-btn]'];
      for(var s=0;s<sels.length;s++){
        var all=document.querySelectorAll(sels[s]);
        for(var i=0;i<all.length;i++){
          var e=all[i];var r=e.getBoundingClientRect();
          if(r.width<=0||r.height<=0) continue;
          return JSON.stringify({w:Math.round(r.width),h:Math.round(r.height),
                                 x:Math.round(r.x),y:Math.round(r.y)});
        }
      }
      return '';
    }catch(e){return 'ERR:'+(e.message||e)}})()""", ctx=mine, timeout=8.0)
    if not raw or str(raw).startswith("ERR") or not str(raw).strip():
        print("[act] 「我的」页上找不到右上角签到按钮（wx-right-btn）")
        return False
    btn = json.loads(raw)
    cx, cy = btn["x"] + btn["w"] // 2, btn["y"] + btn["h"] // 2
    print("[act] 点签到按钮（页面 %d,%d）" % (cx, cy))
    wxreg.click(cx, cy)
    time.sleep(7.0)
    return True


def main():
    budget = float(sys.argv[1]) if len(sys.argv) > 1 else 90.0
    t_start = time.time()

    ws = wxcdp.WS(timeout=25)
    try:
        ws.send({"id": 1, "method": "Target.setDiscoverTargets", "params": {"discover": True}})
        time.sleep(1.0)

        # ① H5 已开着就复用；否则走 UI 打开它
        h5 = find_h5(ws, 2)
        if h5:
            print("[act] H5 已开着 → 直接复用")
        else:
            if not open_h5_by_ui():
                print("OPPO_ACT=")
                return 1
            h5 = find_h5(ws, 3)
        if not h5:
            print("[act] 没能打开承载签到页的 webview（H5）")
            print("OPPO_ACT=")
            return 1
        print("[act] webview target = %s" % h5["targetId"])
        print("[act] H5 url = %s" % (h5.get("url") or "")[:150])

        # ② attach（flatten 才有 sessionId）
        ws.send({"id": 4, "method": "Target.attachToTarget",
                 "params": {"targetId": h5["targetId"], "flatten": True}})
        sid = ((wait_id(ws, 4) or {}).get("result") or {}).get("sessionId")
        if not sid:
            print("[act] attachToTarget 失败")
            print("OPPO_ACT=")
            return 1
        print("[act] sessionId = %s" % sid)

        # ③ 注入 hook（要在页面 JS 之前生效）+ 开 Network
        ws.send({"id": 5, "method": "Page.enable", "params": {}}, sessionId=sid)
        ws.send({"id": 6, "method": "Runtime.enable", "params": {}}, sessionId=sid)
        ws.send({"id": 7, "method": "Network.enable", "params": {}}, sessionId=sid)
        ws.send({"id": 8, "method": "Page.addScriptToEvaluateOnNewDocument",
                 "params": {"source": HOOK}}, sessionId=sid)
        time.sleep(0.8)

        # ④ H5 **内部**重载（⚠️ 不是 Page.reload —— 那会把 H5 关掉）
        print("[act] H5 内部 location.reload() → 开始抓请求")
        ws.send({"id": 9, "method": "Runtime.evaluate",
                 "params": {"expression": "location.reload()"}}, sessionId=sid)

        # ⑤ 收请求，找 getSignInDetail?activityId=
        found = ""
        collect = []
        deadline = min(time.time() - t_start + 25, budget - (time.time() - t_start))
        t0 = time.time()
        while time.time() - t0 < max(5.0, deadline):
            try:
                m = json.loads(ws.recv_msg())
            except Exception:
                break
            if m.get("method") != "Network.requestWillBeSent":
                continue
            if m.get("sessionId") != sid:
                continue
            q = (m.get("params") or {}).get("request") or {}
            u = q.get("url") or ""
            collect.append(u)
            mm = re.search(r"cumulativeSignIn/getSignInDetail\?activityId=(\d{15,20})", u)
            if mm:
                found = mm.group(1)
                print("[act] ★ 抓到 getSignInDetail?activityId=%s" % found)
                break

        # ⑥ 兜底：hook 记录的 __reqs（万一 Network 事件漏了）
        if not found:
            print("[act] Network 事件里没找到 → 读 hook 记录的 window.__reqs")
            ws.send({"id": 10, "method": "Runtime.evaluate",
                     "params": {"expression": "JSON.stringify((window.__reqs||[]).slice(0,80))",
                                "returnByValue": True}}, sessionId=sid)
            r = wait_id(ws, 10, 20)
            val = ((r or {}).get("result") or {}).get("result", {}).get("value")
            if val:
                mm = re.search(r"getSignInDetail\?activityId=(\d{15,20})", str(val))
                if mm:
                    found = mm.group(1)
                    print("[act] ★ __reqs 里抓到 activityId=%s" % found)
                else:
                    print("[act] __reqs 内容（前 800 字）：%s" % str(val)[:800])
            else:
                print("[act] __reqs 读不到（hook 可能没生效）")

        if not found:
            print("[act] 本次抓到的请求（共 %d 条，去重后前 20）：" % len(collect))
            seen = []
            for u in collect:
                if u in seen or "data:" in u:
                    continue
                seen.append(u)
                if len(seen) <= 20:
                    print("        %s" % u[:170])
        print("OPPO_ACT=%s" % found)
        return 0 if found else 1
    finally:
        try:
            ws.close()
        except Exception:
            pass


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:                                # noqa: BLE001
        print("[act] 异常：%s" % e)
        print("OPPO_ACT=")
        sys.exit(1)
