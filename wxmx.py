#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""读蜜雪冰城小程序运行时的全局数据（找「每日抽奖」入口用）。

蜜雪的 accessToken / 弹窗配置都在逻辑层的 `getApp()` 上，纯 CDP 只读，
不点任何按钮。输出 `MX_JSON=<json>` 供宿主机解析。
"""
import json
import os
import sys

sys.path.insert(0, "/tmp")
import wxcdp      # noqa: E402
import wxdom      # noqa: E402
import wxnet      # noqa: E402

APPID = "wx7696c66d2245d107"

EXPR = r"""(function () {
  try {
    var a = getApp();
    var g = a.globalData || {};
    var c = a.configData || {};
    var o = {
      tokenLen: ((a.accessToken) || "").length,
      token: ((a.accessToken) || ""),
      configKeys: Object.keys(c),
      globalKeys: Object.keys(g),
      menuPagePopup: c.menuPagePopup || null,
      homePagePopup: c.homePagePopup || null,
      rightsDescPageUrl: c.rightsDescPageUrl || null,
      payPopupOrder: c.payPopupOrder || null,
      isJuniorMember: g.isJuniorMember,
      userInfo: g.userInfo || null,
      memberRightsInfo: g.memberRightsInfo || null,
      usedMemberRightsInfo: g.usedMemberRightsInfo || null,
      appKeys: Object.keys(a)
    };
    return JSON.stringify(o).slice(0, 6000);
  } catch (e) { return "ERR:" + e.message; }
})()"""


def main():
    ws = wxcdp.WS()
    try:
        ctx = wxnet.find_logic_ctx(ws, APPID)
        print("logic ctx = %s" % ctx)
        if not ctx:
            print("没找到逻辑层 ctx")
            return 1
        v = wxdom.evaluate(ws, EXPR, ctx=ctx, timeout=15.0)
        print("MX_JSON=" + (v if isinstance(v, str) else json.dumps(v)))
        return 0
    finally:
        try:
            ws.close()
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
