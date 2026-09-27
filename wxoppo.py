#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""在微信实例容器内取「OPPO 商城」小程序的身份：NEWOPPOSID + openid。

OPPO 商城的会话是 `NEWOPPOSID` 请求头 + `openid` 请求头（s_channel=program_wx），
由小程序自己用 wx.login 的 code 去 OPPO 服务端换 —— 我们拿不到也不需要 code，
只要它换完之后**自己发请求时带出来的那两个头**。所以主路是抓真实请求头。

两条路，优先第一条（快、不发请求）：

  ① **读 storage**：有些版本会把会话持久化，键名不固定，扫一遍 storage 找
     形如 NEWOPPOSID / token / sid 的值。找到就用，不用逼它发请求。

  ② **抓真实请求头**（主路，2026-07 的 get_token.py 已验证可行）：开 CDP 的
     Network domain，reLaunch 逼小程序重发请求，从发往 opposhop.cn 的请求头里
     读 `NEWOPPOSID` 与 `openid`。顺带从 referer 里抠出 appId 做交叉校验。

输出（给 wxsign.py 解析）：
    OPPO_JSON={"appid": "...", "sid": "...", "openid": "...", "from": "storage|network"}

用法（容器内）：DISPLAY=:1 python3 wxoppo.py [目标appId] [等待秒数]
退出码：0 = 拿到 sid + openid；1 = 没拿到（原因见打印）
"""
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from wxcdp import WS
except ImportError:
    sys.path.insert(0, "/tmp")
    from wxcdp import WS

# ── 路线 ①：storage 探针（同步）。键名不固定，整库扫一遍找会话字段。 ──
PROBE = """
(function () {
  var out = { sid: '', openid: '', appid: '' };
  try { out.appid = wx.getAccountInfoSync().miniProgram.appId; } catch (e) {}
  try {
    var info = wx.getStorageInfoSync();
    var keys = info.keys || [];
    for (var i = 0; i < keys.length; i++) {
      var v = null;
      try { v = wx.getStorageSync(keys[i]); } catch (e) { continue; }
      if (v === null || v === undefined || v === '') continue;
      var s = (typeof v === 'string') ? v : JSON.stringify(v);
      // 会话字段可能是裸串，也可能埋在某个 JSON 里；两种都试
      var m = s.match(/NEWOPPOSID["']?\\s*[:=]\\s*["']?([A-Za-z0-9_\\-\\.]{8,})/i);
      if (m && !out.sid) out.sid = m[1];
      var mo = s.match(/openid["']?\\s*[:=]\\s*["']?([A-Za-z0-9_\\-]{8,})/i);
      if (mo && !out.openid) out.openid = mo[1];
      // 键名直接叫 sid / newopposid / token 的也认
      var k = keys[i].toLowerCase();
      if (!out.sid && /sid|newopposid|token/.test(k) && typeof v === 'string' && v.length > 8) {
        out.sid = v;
      }
    }
  } catch (e) { out.err = '' + e; }
  return JSON.stringify(out);
})()
"""

# 触发一次重载，逼小程序重新发请求（reLaunch 到当前页 —— 比写死首页路径通用）
RELOAD = """
(function () {
  try {
    var p = getCurrentPages();
    var c = p[p.length - 1];
    if (!c) return 'no-page';
    wx.reLaunch({ url: '/' + c.route });
    return 'reload:' + c.route;
  } catch (e) { return 'ERR:' + e; }
})()
"""


def collect_by_storage(want_appid="", wait=6.0):
    """路线 ①：扫 storage 找 NEWOPPOSID / openid。返回 dict 或 None。"""
    ws = WS()
    got = []
    try:
        ws.send({"id": 1, "method": "Runtime.enable", "params": {}})
        time.sleep(0.4)
        for c in range(1, 81):
            ws.send({"id": 1000 + c, "method": "Runtime.evaluate", "params": {
                "expression": PROBE, "returnByValue": True, "contextId": c}})
        t0 = time.time()
        while time.time() - t0 < wait:
            try:
                m = json.loads(ws.recv_msg())
            except Exception:
                break
            mid = m.get("id")
            if isinstance(mid, int) and 1000 < mid < 2000:
                v = ((m.get("result") or {}).get("result") or {}).get("value")
                if isinstance(v, str) and v.startswith("{"):
                    try:
                        got.append(json.loads(v))
                    except ValueError:
                        pass
    finally:
        ws.close()
    for d in got:
        if d.get("sid") and (not want_appid or not d.get("appid") or d.get("appid") == want_appid):
            d["from"] = "storage"
            return d
    return None


def collect_by_network(want_appid="", wait=20.0):
    """路线 ②：抓发往 opposhop.cn 的请求头里的 NEWOPPOSID + openid。

    为什么是主路：OPPO 的会话是小程序用 wx.login 的 code 现换的，我们拿不到 code；
    但它换完之后每个业务请求都会把 NEWOPPOSID / openid 放请求头里 —— 读它自己的头最准。
    （2026-07 的 get_token.py 就是被动监听这两个头验证可行的。）
    """
    ws = WS()
    got = None
    try:
        ws.send({"id": 1, "method": "Runtime.enable", "params": {}})
        ws.send({"id": 2, "method": "Network.enable", "params": {}})
        time.sleep(0.6)
        ws.send({"id": 3, "method": "Runtime.evaluate",
                 "params": {"expression": RELOAD, "returnByValue": True}})
        t0 = time.time()
        while time.time() - t0 < wait and not got:
            try:
                m = json.loads(ws.recv_msg())
            except Exception:
                break
            if m.get("method") != "Network.requestWillBeSent":
                continue
            p = m.get("params") or {}
            q = p.get("request") or {}
            url = q.get("url") or ""
            if "opposhop.cn" not in url:
                continue
            h = {k.lower(): v for k, v in (q.get("headers") or {}).items()}
            sid = h.get("newopposid")
            openid = h.get("openid")
            if not sid:
                continue
            ref = h.get("referer") or ""
            mm = re.search(r"servicewechat\.com/(wx[0-9a-f]+)", ref + " " + url)
            got = {"sid": sid, "openid": openid or "",
                   "appid": (mm.group(1) if mm else ""), "from": "network", "sample": url}
    finally:
        ws.close()
    if got and want_appid and got.get("appid") and got["appid"] != want_appid:
        print("[oppo] ⚠️ 抓到的请求属于 %s，不是目标 %s" % (got["appid"], want_appid))
    return got


def main():
    want = sys.argv[1] if len(sys.argv) > 1 else ""
    wait = float(sys.argv[2]) if len(sys.argv) > 2 else 10.0

    d = collect_by_storage(want, min(wait, 6.0))
    if d and d.get("sid"):
        print("[oppo] 走 storage：扫到会话字段")
    else:
        print("[oppo] storage 里没有会话 → 抓真实请求头")
        d = collect_by_network(want, max(wait, 20.0))

    if not d or not d.get("sid"):
        print("[oppo] 两条路都没拿到 NEWOPPOSID —— 小程序真开着吗？hook 通吗？")
        return 1

    out = {"appid": d.get("appid", ""), "sid": d.get("sid", ""),
           "openid": d.get("openid", ""), "from": d.get("from", "")}
    print("[oppo] appid=%s openid=%s sid=%s… source=%s"
          % (out["appid"], out["openid"] or "(空)", out["sid"][:10], out["from"]))
    print("OPPO_JSON=" + json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
