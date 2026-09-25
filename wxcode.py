#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""取「当前小程序」的 appId 与 `wx.login()` 的 code —— 后端无关的通用取码器。

为什么非在小程序里跑不可：`code` 是微信客户端**按 appId 签发**的，一次一用、约 5 分钟失效，
只有小程序逻辑层的 `wx.login()` 拿得到。容器外面（宿主、后端服务）无论如何都取不到。

与 wxyd.py 的分工（两者都从逻辑层取 code，别重复造）：
  · wxcode.py —— 只要 (appId, code)。适用于「服务端自己持 appsecret 去 code2session」的后端，
    例如微租林（`saas.funjs.top`：`POST /open/auth/mp/silent-login` 收 `{code}`）。
  · wxyd.py   —— 还要读 ext 配置里的 `storeid`（易东 `zhyx.eingdong.com` 的 `/api/login` 要它）。

输出（给 wxsign.py 解析）：
    WZ_JSON={"appid": "...", "code": "..."}

用法（容器内）：DISPLAY=:1 python3 wxcode.py [目标appId] [等待秒数]
退出码：0 = 拿到 code；1 = 没拿到（原因见打印）
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

# ⚠️ 必须返回 Promise（awaitPromise）：wx.login 是异步的，同步表达式只会拿到 undefined。
#    另：有些 context 里的 `wx` 是残壳（wx.getAccountInfoSync is not a function），
#    所以每项单独 try，最后按「有 code 且 appid 匹配」挑结果。
PROBE = """
new Promise(function (res) {
  var out = {};
  try { out.appid = wx.getAccountInfoSync().miniProgram.appId; } catch (e) { out.appidErr = '' + e; }
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
        print("[code] 逻辑层没响应 —— 小程序真开着吗？hook 通吗？")
        return 1
    if want and d.get("appid") and d["appid"] != want:
        print("[code] ⚠️ 当前开着的是 %s，不是目标 %s" % (d["appid"], want))
    out = {"appid": d.get("appid", ""), "code": d.get("code", "")}
    print("[code] appid=%s code=%s" % (out["appid"], (out["code"] or "")[:12]))
    print("WZ_JSON=" + json.dumps(out, ensure_ascii=False))
    return 0 if out["code"] else 1


if __name__ == "__main__":
    sys.exit(main())
