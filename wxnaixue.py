#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""在微信实例容器内取「奈雪点单」小程序的登录态：accessToken（Bearer）。

奈雪点单（`wxab7430e6e8b9a4ab`）是**品道自研**（pin-dao.cn），不是企迈 ——
包里虽然出现 `qmai.cn/naixue-login` 等路径（企迈只承担登录/会员的一部分），
但**签到走自研后端** `https://tm-api.pin-dao.cn`：
页面 `pkgBasics/pages/signInReminder/signInReminder` 一 onShow 就
`POST /user/sign/save {signDate:"YYYY-M-D"}`，头 `Authorization: Bearer <accessToken>`。

会话存在 `getApp().globalData.accessToken`（JWT，iss=pd-passport，有效期 120 天），
由小程序启动时 `silentLogin()` 用 `wx.login` 的 code 换得 —— 我们不需要 code，
读它换完的结果就行（和 OPPO 读 NEWOPPOSID 同一个思路）。

输出（给 wxsign.py 解析）：
    NX_JSON={"appid":"…","token":"…","openid":"…","userId":"…","from":"globalData|storage"}

用法（容器内）：DISPLAY=:1 python3 wxnaixue.py [目标appId] [等待秒数]
退出码：0 = 拿到 token；1 = 没拿到
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

# 先读 globalData（主路，小程序自己维护的当前态）；没有再扫 storage 兜底。
PROBE = """
(function () {
  var out = { appid: '', token: '', openid: '', userId: '', src: '' };
  try { out.appid = wx.getAccountInfoSync().miniProgram.appId; } catch (e) {}
  try {
    var g = getApp().globalData || {};
    out.token = g.accessToken || '';
    out.openid = g.openId || '';
    out.userId = (g.userInfo || {}).userId || '';
    if (out.token) out.src = 'globalData';
  } catch (e) {}
  if (!out.token) {
    try {
      var t = wx.getStorageSync('accessToken');
      if (t) {
        out.token = (typeof t === 'string') ? t : (t.accessToken || '');
        if (out.token) out.src = 'storage';
      }
    } catch (e) {}
  }
  return JSON.stringify(out);
})()
"""


def collect(want_appid="", wait=8.0):
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
        if d.get("token") and (not want_appid or not d.get("appid")
                               or d.get("appid") == want_appid):
            return d
    return None


def main():
    want = sys.argv[1] if len(sys.argv) > 1 else ""
    wait = float(sys.argv[2]) if len(sys.argv) > 2 else 8.0
    d = collect(want, wait)
    if not d or not d.get("token"):
        print("[naixue] 没读到 accessToken —— 小程序真开着吗？当前页登录了吗？（hook 通吗）")
        return 1
    out = {"appid": d.get("appid", ""), "token": d.get("token", ""),
           "openid": d.get("openid", ""), "userId": d.get("userId", ""),
           "from": d.get("src", "")}
    print("[naixue] appid=%s userId=%s openid=%.12s… token=%.18s…（%s，%d 字符）"
          % (out["appid"], out["userId"] or "(空)", out["openid"] or "(空)",
             out["token"], out["from"], len(out["token"])))
    print("NX_JSON=" + json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
