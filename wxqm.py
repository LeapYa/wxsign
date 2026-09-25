#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""在微信实例容器内取「企迈（qmai）」小程序的身份：Qm-User-Token + 商户 storeId。

两条路，优先第一条：

  ① **读 storage**（快）：小程序打开时会自己完成一次静默登录，有些商户把它
     持久化成 `loginData = {token, store:{id,name}, user:{eOpenId,eMobile}}`。
     → 直接读，不用发任何请求。

  ② **抓真实请求头**（兜底）：**有些商户不持久化** —— 实测呷哺呷哺的 storage 里
     干干净净（`loginData` 是空串），token 只活在内存里、每个请求现取现用。
     这时只能开 CDP 的 Network domain，逼它发一次请求，从它自己的请求头里读
     `Qm-User-Token` 与 `store-id`。顺带还能读到 `Qm-From-Type`（业务线），
     连 `qm_biz` 都不用配。

输出（给 wxsign.py 解析）：
    QM_JSON={"appid": "...", "token": "...", "storeId": "...", "storeName": "...",
             "openId": "", "mobile": "", "userId": "", "biz": "catering",
             "from": "storage|network"}

用法（容器内）：DISPLAY=:1 python3 wxqm.py [目标appId] [等待秒数]
退出码：0 = 拿到 token + storeId；1 = 没拿到（原因见打印）
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

# ── 路线 ①：storage 探针（同步）。每项单独 try，别让一项失败带崩整个探针。 ──
PROBE = """
(function () {
  var out = {};
  try { out.appid = wx.getAccountInfoSync().miniProgram.appId; } catch (e) { out.appidErr = '' + e; }
  try {
    var d = wx.getStorageSync('loginData') || {};
    var st = d.store || {}, u = d.user || {};
    out.token = d.token || '';
    out.storeId = String(st.id || '');
    out.storeName = st.name || '';
    out.openId = u.eOpenId || '';
    out.mobile = u.eMobile || '';
  } catch (e) { out.err = '' + e; }
  try {
    var uli = wx.getStorageSync('userLoginInfo') || {};
    out.userId = String(uli.userId || '');
  } catch (e) { out.uliErr = '' + e; }
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


def collect_by_storage(want_appid="", wait=8.0):
    """路线 ①：读 storage 的 loginData。返回 dict 或 None。"""
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
        if d.get("token") and d.get("storeId") and (not want_appid or d.get("appid") == want_appid):
            d["from"] = "storage"
            return d
    return None


def collect_by_network(want_appid="", wait=20.0):
    """路线 ②：抓小程序**真实请求头**里的 Qm-User-Token + store-id（+ 业务线）。

    为什么非此不可：有些商户（实测呷哺呷哺）**不把登录态写进 storage** ——
    token 只在内存里，每个请求现取现用，读 storage 永远是空的。
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
            h = {k.lower(): v for k, v in (q.get("headers") or {}).items()}
            tok = h.get("qm-user-token")
            sid = h.get("store-id")
            if not (tok and sid):
                continue
            url = q.get("url") or ""
            ref = h.get("referer") or ""
            mm = re.search(r"servicewechat\.com/(wx[0-9a-f]+)", ref + " " + url)
            # 路径形如 /web/<biz>/... —— 顺手把业务线抠出来，省得配 qm_biz
            mb = re.search(r"/web/([a-z0-9\-]+)/", url)
            got = {"token": tok, "storeId": str(sid), "storeName": "",
                   "appid": (mm.group(1) if mm else ""), "openId": "", "mobile": "",
                   "userId": "", "biz": (h.get("qm-from-type") or (mb.group(1) if mb else "")),
                   "from": "network", "sample": url}
    finally:
        ws.close()
    if got and want_appid and got.get("appid") and got["appid"] != want_appid:
        print("[qm] ⚠️ 抓到的请求属于 %s，不是目标 %s" % (got["appid"], want_appid))
    return got


def main():
    want = sys.argv[1] if len(sys.argv) > 1 else ""
    wait = float(sys.argv[2]) if len(sys.argv) > 2 else 10.0

    d = collect_by_storage(want, min(wait, 10.0))
    if d:
        print("[qm] 走 storage：拿到 loginData")
    else:
        print("[qm] storage 里没有登录态（这个商户可能不持久化）→ 改抓真实请求头")
        d = collect_by_network(want, max(wait, 20.0))

    if not d:
        print("[qm] 两条路都没拿到 —— 小程序真开着吗？hook 通吗？")
        return 1

    out = {"appid": d.get("appid", ""), "token": d.get("token", ""),
           "storeId": d.get("storeId", ""), "storeName": d.get("storeName", ""),
           "openId": d.get("openId", ""), "mobile": d.get("mobile", ""),
           "userId": d.get("userId", ""), "biz": d.get("biz", ""),
           "from": d.get("from", "")}
    print("[qm] appid=%s storeId=%s%s token=%s… biz=%s source=%s"
          % (out["appid"], out["storeId"],
             ("(%s)" % out["storeName"]) if out["storeName"] else "",
             (out["token"] or "")[:10], out["biz"] or "?", out["from"]))
    print("QM_JSON=" + json.dumps(out, ensure_ascii=False))
    return 0 if (out["token"] and out["storeId"]) else 1


if __name__ == "__main__":
    sys.exit(main())
