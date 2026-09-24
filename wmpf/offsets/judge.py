"""机器判卷：把自动恢复的偏移配置与上游人工适配的配置逐字段比对。

用法: python judge.py <恢复出的.json> <上游的.json>
"""
import json
import sys


def norm(v):
    if isinstance(v, list):
        return list(v)
    if isinstance(v, str):
        return int(v, 16)
    return v


def main():
    got = json.load(open(sys.argv[1], encoding="utf-8"))
    want = json.load(open(sys.argv[2], encoding="utf-8"))
    print(f"自动恢复: {sys.argv[1]}")
    print(f"上游配置: {sys.argv[2]}\n")
    print(f"{'字段':24} {'上游':<24} {'自动恢复':<24} 一致")
    print("-" * 84)

    ok = bad = 0
    for k, wv in want.items():
        if k not in got:
            print(f"{k:24} {str(wv):<24} {'(缺失)':<24} ❌")
            bad += 1
            continue
        gv = got[k]
        same = norm(gv) == norm(wv)
        ok += same
        bad += not same
        print(f"{k:24} {str(wv):<24} {str(gv):<24} {'✅' if same else '❌'}")

    extra = [k for k in got if k not in want and k != "Version"]
    if extra:
        print()
        for k in extra:
            print(f"  （上游没有的字段）{k} = {got[k]}   ← 上游这份配置没用到，不代表错")

    print(f"\n结论: {ok} 个字段一致，{bad} 个不一致")
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
