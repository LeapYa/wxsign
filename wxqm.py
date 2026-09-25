#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""在微信实例容器内取「企迈（qmai）」小程序的身份：Qm-User-Token + 商户 storeId。

为什么非在小程序里跑不可：
  · `Qm-User-Token` 是企迈服务端按「静默登录」签发的会话，存在**逻辑层的 storage** 里
    （`loginData.token`）—— 包内、宿主机都拿不到；
  · 商户号 `storeId` 同样在 `loginData.store.id` 里（也来自服务端下发的 config）。
  两者都拿不到，后面调签到接口就无从谈起。

⚠️ 与易东（wxyd.py）的关键差别：**企迈只要 token，不要 `wx.login` 的 code**。
   小程序打开时会自己完成一次静默登录并写进 storage，我们直接读现成的即可；
   所以这里**不做异步**（普通表达式，不是 Promise）。

输出（给 wxsign.py 解析）：
    QM_JSON={"appid": "...", "token": "...", "storeId": "...", "storeName": "...",
             "openId": "...", "mobile": "...", "userId": "..."}

用法（容器内）：DISPLAY=:1 python3 wxqm.py [目标appId] [等待秒数]
退出码：0 = 拿到 token + storeId；1 = 没拿到（原因见打印）
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

# 每一项都单独 try：有些 context 里的 `wx` 只是残壳，别让一项失败带崩整个探针。
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
    out.storeType = String(st.store_type || '');
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


def collect(want_appid="", wait=10.0):
    """对所有 context 求值一次，返回结果 dict（优先 appid 匹配且带 token 的）。"""
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
        if d.get("token") and (not want_appid or d.get("appid") == want_appid):
            return d
    return got[0] if got else None


def main():
    want = sys.argv[1] if len(sys.argv) > 1 else ""
    wait = float(sys.argv[2]) if len(sys.argv) > 2 else 10.0
    d = collect(want, wait)
    if not d:
        print("[qm] 逻辑层没响应 —— 小程序真开着吗？hook 通吗？")
        return 1
    if want and d.get("appid") and d["appid"] != want:
        print("[qm] ⚠️ 当前开着的是 %s，不是目标 %s" % (d["appid"], want))
    out = {"appid": d.get("appid", ""), "token": d.get("token", ""),
           "storeId": d.get("storeId", ""), "storeName": d.get("storeName", ""),
           "openId": d.get("openId", ""), "mobile": d.get("mobile", ""),
           "userId": d.get("userId", "")}
    print("[qm] appid=%s storeId=%s(%s) token=%s… mobile=%s"
          % (out["appid"], out["storeId"], out["storeName"],
             (out["token"] or "")[:10], out["mobile"] or "(未绑定)"))
    print("QM_JSON=" + json.dumps(out, ensure_ascii=False))
    return 0 if (out["token"] and out["storeId"]) else 1


if __name__ == "__main__":
    sys.exit(main())
