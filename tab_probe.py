# -*- coding: utf-8 -*-
"""受控探针：开搜一搜窗(354)后截标签条，判断有无小程序 tab。"""
import sys
import time
sys.path.insert(0, "/tmp")
import wxwin
import wxopen as R

W, H = wxwin.screen()
ws = wxwin.windows()
print("windows:", [(w["id"], w["title"], w["w"], w["h"]) for w in ws])
sec = [w for w in ws if w["title"].strip() == "微信" and w["w"] > 1000]
if not sec:
    print("NO secondary window")
    sys.exit(0)
w = sec[0]
R.raise_window(w["id"])
time.sleep(0.6)
R.png(W, H, "tabstrip.png")
print("sec window", w["id"], "geo", w["x"], w["y"], w["w"], w["h"],
      "miniapp_tab=", R.has_miniapp_tab(w["id"], W, H))
