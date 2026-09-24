"""批量跑：对 wmpf-bin 下所有 WeChatAppEx* 反解偏移，并与上游已有配置逐字段判卷。

用法: python verify_batch.py [二进制目录] [上游配置目录]
"""
import glob
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
BIN_DIR = sys.argv[1] if len(sys.argv) > 1 else HERE
CFG_DIR = sys.argv[2] if len(sys.argv) > 2 else os.path.join(
    HERE, "..", "WMPFDebugger", "frida", "config", "linux")
PY = sys.executable


def upstream_versions():
    out = {}
    for p in glob.glob(os.path.join(CFG_DIR, "addresses.*.json")):
        m = re.search(r"addresses\.(\d+)\.json$", p)
        if m:
            out[int(m.group(1))] = p
    return out


def main():
    up = upstream_versions()
    print(f"上游 linux 配置: {sorted(up)}\n")

    bins = sorted(glob.glob(os.path.join(BIN_DIR, "WeChatAppEx*")))
    bins = [b for b in bins if os.path.isfile(b) and os.path.getsize(b) > 50 * 1024 * 1024]
    if not bins:
        print(f"{BIN_DIR} 下没有找到 WeChatAppEx 二进制（>50MB）。")
        print("用法: python verify_batch.py <放二进制的目录> [上游配置目录]")
        return 2
    rows = []
    for b in bins:
        name = os.path.basename(b)
        print("=" * 90)
        print(f"### {name}  ({os.path.getsize(b):,} 字节)")
        out_json = os.path.join(BIN_DIR, f"{name}.offsets.json")
        r = subprocess.run([PY, os.path.join(HERE, "recover_offsets.py"), b,
                            "--out", out_json],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        tail = (r.stdout or "").strip().splitlines()
        # 只挑关键几行展示
        for line in tail:
            if re.match(r"^\s*(release 串|WMPF 版本|LoadStartHookOffset =|"
                        r"SceneOffsets =|CDPFilterHookOffset =|CastToJsonHookOffset =|"
                        r"  ❌|  ⚠️)", line):
                print("   " + line.strip())
        if r.returncode != 0:
            print(f"   >>> 工具退出码 {r.returncode}（未产出配置）")
            rows.append((name, None, "工具失败"))
            continue

        data = json.load(open(out_json, encoding="utf-8"))
        ver = data.get("Version")
        if ver in up:
            j = subprocess.run([PY, os.path.join(HERE, "judge.py"), out_json, up[ver]],
                               capture_output=True, text=True, encoding="utf-8", errors="replace")
            verdict = "判卷通过" if j.returncode == 0 else "判卷不一致"
            print(f"   >>> 与上游 addresses.{ver}.json {verdict}")
            for line in (j.stdout or "").splitlines():
                if "结论" in line or "❌" in line:
                    print("       " + line.strip())
            rows.append((name, ver, verdict))
        else:
            print(f"   >>> WMPF {ver} 上游没有配置，只能看自检是否通过")
            rows.append((name, ver, "无标准答案"))

    print("\n" + "=" * 90)
    print(f"{'二进制':32} {'WMPF':>8}  结果")
    print("-" * 90)
    for n, v, s in rows:
        print(f"{n:32} {str(v):>8}  {s}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
