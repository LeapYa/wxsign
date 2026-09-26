#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""在正确的 ctx（9）里把授权层的**结构 + 几何**完整倒出来。

已确认（2026-09-26）：
  · 授权层 = `WX-STD-AUTHORIZATION#authorization`，在 **ctx=9**（不是 wxdom 挑的 ctx=6）
  · 文案：「欢迎加入<品牌> / 手机号一键登录 / 暂时跳过 / 允许我们在必要场景下…」
  · **不是原生浮层** —— 就是小程序自己渲染的 view 组件（源码
    `onAuthorization(){this.selectComponent("#authorization").show()}`）

所以正确定位法 = **按结构（标签/id/class）+ 文案**查，**不问颜色**。
本脚本把每个可点节点的 tag/id/class/文案/矩形全列出来，供写正式实现时定判据。

⚠️ 注意 `WX-STD-AUTHORIZATION` 自身报的是 `w:0 h:0, x:0 y:832` —— 它是**组件根**，
  真实盒子在里面的子节点上。所以要往下钻，不能直接用根节点矩形。
"""
import json
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
for _p in (_HERE, os.path.join(_HERE, ".."), "/tmp"):
    if os.path.isfile(os.path.join(_p, "wxcdp.py")):
        sys.path.insert(0, _p)
        break
import wxcdp  # noqa: E402

CTX = 9

TREE_JS = r"""
(function(){try{
  var root=document.querySelector('#authorization');
  if(!root) return JSON.stringify({err:'no #authorization'});
  var out=[];
  var walk=function(e,d){
    if(!e||d>9) return;
    var r=e.getBoundingClientRect();
    var t=(e.innerText||'').replace(/\s+/g,' ').trim();
    // 只留「有尺寸 或 有文案」的节点，砍掉噪音
    if((r.width>0&&r.height>0)||t){
      out.push({d:d,tag:e.tagName,id:e.id||'',cls:String(e.className||'').slice(0,70),
                t:t.slice(0,70),x:Math.round(r.x),y:Math.round(r.y),
                w:Math.round(r.width),h:Math.round(r.height),
                click:(e.onclick?'y':''), pe:(getComputedStyle(e).pointerEvents||'')});
    }
    var ch=e.children||[];
    for(var i=0;i<ch.length;i++) walk(ch[i],d+1);
  };
  walk(root,0);
  return JSON.stringify({vw:window.innerWidth,vh:window.innerHeight,n:out.length,items:out});
}catch(e){return 'ERR:'+e}})()
"""


def ev(ws, expr, cid, timeout=25.0):
    mid = (int(time.time() * 1000) % 90000) + 701
    ws.send({"id": mid, "method": "Runtime.evaluate",
             "params": {"expression": expr, "returnByValue": True, "contextId": cid}})
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            m = json.loads(ws.recv_msg())
        except Exception:
            return None
        if m.get("id") != mid or "error" in m:
            continue
        return (((m.get("result") or {}).get("result")) or {}).get("value")
    return None


def main():
    ws = wxcdp.WS(timeout=25)
    ws.send({"id": 1, "method": "Runtime.enable", "params": {}})
    time.sleep(0.4)
    v = ev(ws, TREE_JS, CTX)
    if not v or str(v).startswith("ERR"):
        print("读不到：%s" % v)
        return 1
    d = json.loads(v)
    if d.get("err"):
        print("err=%s" % d["err"])
        return 1
    print("视口 %sx%s，节点 %d 个" % (d["vw"], d["vh"], d["n"]))
    print("%-3s %-22s %-30s %-30s %s" % ("d", "tag", "id/cls", "文案", "rect(x,y,w,h)"))
    for it in d["items"]:
        key = (it["id"] or "") + ("." + it["cls"] if it["cls"] else "")
        print("%-3d %-22s %-30s %-30s (%d,%d,%d,%d)%s%s" % (
            it["d"], it["tag"][:22], key[:30], it["t"][:30],
            it["x"], it["y"], it["w"], it["h"],
            " [onclick]" if it["click"] else "",
            " [pe=%s]" % it["pe"] if it["pe"] and it["pe"] != "auto" else ""))
    ws.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
