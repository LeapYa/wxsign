#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""在微信实例容器内取「易东系」小程序的身份：`wx.login()` 的 code + 门店 storeid。

为什么非在小程序里跑不可：
  · `code` 是微信客户端按 appId 签发的，只有逻辑层的 `wx.login()` 拿得到；
  · `storeid` 在「第三方平台代管小程序」的 ext 配置里（`wx.getExtConfigSync()`），
    包内没有明文（实测 grep 过整包）。
两者都拿不到，后面换 sessionKey 就无从谈起。

输出（给 wxsign.py 解析）：
    YD_JSON={"appid": "...", "code": "...", "storeid": "...", "url": "...", "version": "..."}

用法（容器内）：DISPLAY=:1 python3 wxyd.py [目标appId] [等待秒数]
退出码：0 = 拿到 code + storeid；1 = 没拿到（原因见打印）
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from wxcdp import WS
except ImportError:
    sys.path.insert(0, "/tmp")
    from wxcdp import WS

# 在逻辑层里跑：能一次抓齐的都抓齐。wx.login 是异步的 → 必须返回 Promise（awaitPromise）。
# ⚠️ 有些 context 里的 `wx` 只是个残壳（比如 wx.getAccountInfoSync is not a function），
#    所以每一项都单独 try，最后按「有没有 code + appid 对不对」挑结果，别拿第一个有值的当准。
PROBE = """
new Promise(function (res) {
  var out = {};
  try { out.appid = wx.getAccountInfoSync().miniProgram.appId; } catch (e) { out.appidErr = '' + e; }
  try { out.ext = wx.getExtConfigSync() || {}; } catch (e) { out.extErr = '' + e; }
  try {
    var g = getApp();
    out.url = g.globalData.url || '';
    out.storeid = g.globalData.storeId || '';
    out.ext_storeid = g.globalData.ext_storeid || '';
    out.version = g.globalData.version || '';
  } catch (e) { out.gErr = '' + e; }
  wx.login({
    success: function (r) { out.code = r.code; res(JSON.stringify(out)); },
    fail: function (e) { out.loginErr = JSON.stringify(e); res(JSON.stringify(out)); }
  });
})
"""


def collect(want_appid="", wait=12.0):
    """对所有 context 求值一次，返回结果 dict（优先 appid 匹配且带 code 的）。"""
    ws = WS()
    got = []
    try:
        ws.send({"id": 1, "method": "Runtime.enable", "params": {}})
        time.sleep(0.4)
        for c in range(1, 81):
            ws.send({"id": 1000 + c, "method": "Runtime.evaluate", "params": {
                "expression": PROBE, "returnByValue": True,
                "contextId": c, "awaitPromise": True}})
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
        if d.get("code") and (not want_appid or d.get("appid") == want_appid):
            return d
    return got[0] if got else None


def main():
    want = sys.argv[1] if len(sys.argv) > 1 else ""
    wait = float(sys.argv[2]) if len(sys.argv) > 2 else 12.0
    d = collect(want, wait)
    if not d:
        print("[yd] 逻辑层没响应 —— 小程序真开着吗？hook 通吗？")
        return 1
    if want and d.get("appid") and d["appid"] != want:
        print("[yd] ⚠️ 当前开着的是 %s，不是目标 %s" % (d["appid"], want))
    ext = d.get("ext") or {}
    store = d.get("storeid") or ext.get("storeid") or ext.get("store_id") or ""
    out = {"appid": d.get("appid", ""), "code": d.get("code", ""),
           "storeid": str(store), "url": d.get("url", ""),
           "version": str(d.get("version", ""))}
    print("[yd] appid=%s storeid=%s code=%s"
          % (out["appid"], out["storeid"], (out["code"] or "")[:12]))
    print("YD_JSON=" + json.dumps(out, ensure_ascii=False))
    return 0 if (out["code"] and out["storeid"]) else 1


if __name__ == "__main__":
    sys.exit(main())
