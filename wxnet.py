#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""拦小程序逻辑层的网络请求，把「服务端下发的身份」捞回来。

为什么需要它
------------
换 token 必须带 `mpId`（登录 body 里是 `mpid`，业务请求头里是 `crm7-mpId`）。
而 mpId **不在小程序包内**（实测全盘 grep 490 个 wxapkg，零命中），它是服务端下发的。
`storage` 里有没有它取决于**模板**：
  · 过桥缘游戏中心（pages/home/index）→ storage 里有 mpId ✅
  · 小大董（pages/cardhome/home/index2）→ 已进首页，storage 里就是没有 ❌
所以「开号 → 读 storage 拿 mpId」这条路天生只能覆盖其中一类模板。

mpId 在**协议层**必然出现（请求头 / 请求体 / 响应体），与模板无关 —— 本脚本就拦这里。

用法（微信实例容器内，DISPLAY 已有）
-----------------------------------
    DISPLAY=:1 python3 wxnet.py --appid wx933b3959ab358507 --wait 45
    DISPLAY=:1 python3 wxnet.py --appid wx... --reload        # 顺手 reLaunch 触发一轮请求
    DISPLAY=:1 python3 wxnet.py --dump                        # 只把已拦到的全量打出来

退出码：0 = 至少捞到一条含 gh_ 的线索（或 --dump 有内容）；1 = 什么都没有。
"""
import json
import os
import re
import sys
import time

sys.path.insert(0, "/tmp")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wxcdp  # noqa: E402

MPID_RE = re.compile(r"gh_[0-9a-f]{12}")

# 安装拦截器。要点：
#  1) 把 wx.request 换成包装版 —— 小程序自己的代码通常就是调 wx.request(...)，
#     少数会把函数存进变量复用（那样就拦不到，属已知边界）。
#  2) 请求头、请求 URL、请求体、**响应体**四处都扫 —— mpId 可能出现在任一处。
#  3) 只留最近 80 条，避免长跑把内存吃满。
INSTALL_JS = r"""
(function () {
  var W = (typeof window !== 'undefined') ? window
        : ((typeof global !== 'undefined') ? global : this);
  if (W.__wxsHooked) { return 'already'; }
  W.__wxsHooked = true;
  W.__wxsNet = [];
  W.__wxsHits = [];
  var MAX = 80;
  function push(arr, o) { try { arr.push(o); if (arr.length > MAX) arr.shift(); } catch (e) {} }
  function brief(o, n) {
    try {
      if (o === null || o === undefined) { return ''; }
      var s = (typeof o === 'string') ? o : JSON.stringify(o);
      if (typeof s !== 'string') { s = String(s); }
      return s.length > n ? s.slice(0, n) + '…' : s;
    } catch (e) { return ''; }
  }
  function hunt(tag, blob) {
    try {
      if (!blob) { return; }
      var s = (typeof blob === 'string') ? blob : brief(blob, 4000);
      var m = String(s).match(/gh_[0-9a-f]{12}/g);
      if (!m) { return; }
      var seen = {}, out = [];
      for (var i = 0; i < m.length; i++) { if (!seen[m[i]]) { seen[m[i]] = 1; out.push(m[i]); } }
      push(W.__wxsHits, { tag: tag, mpIds: out.join(','), sample: String(s).slice(0, 500) });
    } catch (e) {}
  }
  var orig = wx.request;
  wx.request = function (opt) {
    try {
      opt = opt || {};
      push(W.__wxsNet, {
        url: brief(opt.url, 300), method: opt.method || 'GET',
        header: brief(opt.header, 700), body: brief(opt.data, 400)
      });
      hunt('req.header', opt.header);
      hunt('req.url', opt.url);
      hunt('req.body', opt.data);
      if (typeof opt.success === 'function') {
        var s = opt.success;
        opt.success = function (res) {
          try { hunt('resp.body', res && res.data); } catch (e) {}
          return s.apply(this, arguments);
        };
      }
    } catch (e) {}
    return orig.apply(this, arguments);
  };
  return 'installed';
})()
"""

RELOAD_JS = r"""
(function () {
  try {
    var apps = getCurrentPages() || [];
    var route = apps.length ? apps[apps.length - 1].route : '';
    var all = [];
    try {
      var cfg = (typeof __wxConfig !== 'undefined') ? __wxConfig : null;
      if (cfg && cfg.pages) { all = cfg.pages; }
    } catch (e) {}
    var target = route || (all.length ? all[0] : '');
    if (!target) { return 'NO_ROUTE'; }
    wx.reLaunch({ url: '/' + target });
    return 'reLaunch:/' + target + (all.length ? (' pages=' + all.length) : '');
  } catch (e) { return 'ERR:' + e; }
})()
"""

DUMP_JS = r"""
(function () {
  var W = (typeof window !== 'undefined') ? window
        : ((typeof global !== 'undefined') ? global : this);
  return JSON.stringify({
    hooked: !!W.__wxsHooked,
    net: W.__wxsNet || [],
    hits: W.__wxsHits || []
  });
})()
"""


def eval_in(ws, ctx, expr, wait=3.0):
    """在指定 context 里跑一段 JS，返回 (结果, 是否 Promise)。"""
    mid = int(time.time() * 1000) % 900000 + 700000
    ws.send({"id": mid, "method": "Runtime.evaluate",
             "params": {"expression": expr, "returnByValue": True,
                        "awaitPromise": True, "contextId": ctx}})
    deadline = time.time() + wait
    while time.time() < deadline:
        try:
            m = json.loads(ws.recv_msg())
        except Exception:
            return None, False
        if m.get("id") != mid:
            continue
        res = (m.get("result") or {})
        if res.get("exceptionDetails"):
            return "EXC:%s" % str(res["exceptionDetails"])[:160], False
        r = res.get("result") or {}
        return r.get("value"), False
    return None, False


def find_logic_ctx(ws, appid="", limit=80, wait=7.0):
    """找出**逻辑层** context（能被 eval `wx.request` 的那个）。

    判据是 `typeof wx === 'object' && typeof wx.request === 'function'`；
    给了 appid 就再用 `wx.getAccountInfoSync()` 复核，避免选到别的号。
    """
    ws.send({"id": 1, "method": "Runtime.enable", "params": {}})
    time.sleep(0.4)
    probe = ("(function(){try{return JSON.stringify({has:(typeof wx==='object'&&"
             "typeof wx.request==='function'),appId:(wx.getAccountInfoSync().miniProgram.appId||''),"
             "pages:(getCurrentPages()||[]).map(function(p){return p.route}).join('>')})}catch(e){return ''}})()")
    for c in range(1, limit + 1):
        ws.send({"id": 1000 + c, "method": "Runtime.evaluate",
                 "params": {"expression": probe, "returnByValue": True, "contextId": c}})
    found, deadline = 0, time.time() + wait
    while time.time() < deadline:
        try:
            m = json.loads(ws.recv_msg())
        except Exception:
            break
        mid = m.get("id")
        if not (isinstance(mid, int) and 1000 < mid < 2000):
            continue
        v = ((m.get("result") or {}).get("result") or {}).get("value")
        if not v:
            continue
        try:
            d = json.loads(v)
        except Exception:
            continue
        a = d.get("appId") or ""
        if not d.get("has"):
            continue
        print("[net] ctx %d 逻辑层 appId=%s pages=%s" % (mid - 1000, a or "-", d.get("pages") or "-"))
        if appid and a != appid:
            continue
        found = mid - 1000
    return found


def main():
    argv = sys.argv[1:]
    appid = ""
    wait = 45
    do_reload = "--reload" in argv
    do_dump = "--dump" in argv
    for i, a in enumerate(argv):
        if a == "--appid" and i + 1 < len(argv):
            appid = argv[i + 1]
        elif a.startswith("--appid="):
            appid = a.split("=", 1)[1]
        elif a == "--wait" and i + 1 < len(argv):
            wait = int(argv[i + 1])
        elif a.startswith("--wait="):
            wait = int(a.split("=", 1)[1])

    try:
        ws = wxcdp.WS(timeout=10)
    except Exception as e:
        print("[net] 连不上 CDP：%s" % e)
        return 2

    try:
        if do_dump:
            ctx = find_logic_ctx(ws, appid)
            if not ctx:
                print("[net] 没找到逻辑层 context")
                return 1
            v = eval_in(ws, ctx, DUMP_JS)[0]
            print(v if v else "(读不到)")
            return 0

        ctx = find_logic_ctx(ws, appid)
        if not ctx:
            print("[net] 没找到逻辑层 context（小程序没开？appId 不对？）")
            return 1
        print("[net] 用 ctx=%d 装拦截器" % ctx)
        r = eval_in(ws, ctx, INSTALL_JS)[0]
        print("[net] install -> %s" % r)

        # 拦到的存量（装之前就发过的请求看不见，所以先看一次现状）
        v = eval_in(ws, ctx, DUMP_JS)[0]
        try:
            base = json.loads(v) if isinstance(v, str) else {}
        except Exception:
            base = {}
        print("[net] 装好后已拦到 %d 条请求 / %d 条线索"
              % (len(base.get("net") or []), len(base.get("hits") or [])))

        if do_reload:
            rr = eval_in(ws, ctx, RELOAD_JS, wait=4)[0]
            print("[net] 触发 reLaunch -> %s" % rr)

        # 等请求自己冒出来
        deadline = time.time() + wait
        last_n = len(base.get("net") or [])
        while time.time() < deadline:
            time.sleep(5)
            v = eval_in(ws, ctx, DUMP_JS)[0]
            try:
                d = json.loads(v) if isinstance(v, str) else {}
            except Exception:
                continue
            net, hits = d.get("net") or [], d.get("hits") or []
            if len(net) != last_n:
                print("[net] …已拦到 %d 条请求 / %d 条线索" % (len(net), len(hits)))
                last_n = len(net)
            if hits:
                break

        v = eval_in(ws, ctx, DUMP_JS)[0]
        d = json.loads(v) if isinstance(v, str) else {}
        net, hits = d.get("net") or [], d.get("hits") or []

        print("\n===== 线索（含 gh_ 的） =====")
        mpids = set()
        for h in hits:
            print("  [%s] %s\n      %s" % (h.get("tag"), h.get("mpIds"), h.get("sample", "")[:260]))
            for x in MPID_RE.findall(h.get("mpIds", "")):
                mpids.add(x)
        if not hits:
            print("  (没有)")
        print("\n===== 拦到的请求（最近 12 条） =====")
        for r_ in net[-12:]:
            print("  %-6s %s\n         header=%s\n         body=%s"
                  % (r_.get("method"), r_.get("url", "")[:150],
                     r_.get("header", "")[:180], r_.get("body", "")[:120]))
        if not net:
            print("  (没有 —— 说明窗口内小程序没发新请求)")
        print("\nMPID_CANDIDATES=%s" % ",".join(sorted(mpids)))
        return 0 if (hits or net) else 1
    finally:
        ws.close()


if __name__ == "__main__":
    sys.exit(main())
