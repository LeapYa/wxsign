#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把企迈小程序从「活动页」退回去 —— 排查/联调用。

背景：呷哺打开时常自动弹一个营销活动页（`pluginMarketing/lottery/index/index`），
它会**盖住底部 tabbar 和返回按钮**，人在外面点不回去。而企迈的签到/登录态
需要在正式页面（`pages/index/index` 或「我的」页）才发请求。

用法（容器内）：DISPLAY=:1 python3 qm_back.py
"""
import json
import sys
import time

from wxcdp import WS


def ev(ws, js, cid=3, mid=9001, wait=8.0):
    ws.send({"id": mid, "method": "Runtime.evaluate",
             "params": {"expression": js, "returnByValue": True, "contextId": cid}})
    t0 = time.time()
    while time.time() - t0 < wait:
        try:
            m = json.loads(ws.recv_msg())
        except Exception:
            return None
        if m.get("id") == mid:
            return ((m.get("result") or {}).get("result") or {}).get("value")
    return None


def main():
    ws = WS(timeout=20)
    ws.send({"id": 1, "method": "Runtime.enable", "params": {}})
    time.sleep(0.4)

    stack_js = "getCurrentPages().map(function(x){return x.route}).join(' -> ')"
    print("before:", ev(ws, stack_js, mid=9001))

    back_js = ("(function(){try{var p=getCurrentPages();var c=p[p.length-1];"
               "if(c.route.indexOf('lottery')<0)return 'not-lottery:'+c.route;"
               "wx.navigateBack({delta:1});return 'back-from:'+c.route}"
               "catch(e){return 'ERR:'+e}})()")
    print("back  :", ev(ws, back_js, mid=9002))
    time.sleep(2.5)
    print("after :", ev(ws, stack_js, mid=9003))
    ws.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
