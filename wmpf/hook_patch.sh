#!/usr/bin/env bash
# 给 WMPFDebugger 打两个本地补丁（幂等，可反复跑）
#
# 【补丁 1】frida/hook.js —— 场景号白名单
#   hook.js 只在**场景号白名单**内才把 scene 改写成 1101，进而打开小程序的 devtools 通道。
#   从「小程序面板 → 搜索 → 结果卡片」打开小程序时场景号是 **1183**，不在上游白名单里
#   （1145=搜索 / 1256=最近使用 / 1260=我的常用 …）→ 不改写 → 小程序不会连
#   `ws://localhost:9421` → CDP 拿不到身份、刷不了 token、也就签不了到。
#   现象：hook 日志里没有 `[miniapp] miniapp client connected`。
#   补丁做两件事：① 白名单加 1183；② 不在白名单时也把场景号打出来（换版本/换入口时照着加）。
#
# 【补丁 2】src/platform/linux.ts —— 版本探测回退
#   上游只用 `wmpf_release/<tag>_<x.y.z>` 串取版本号（4.1.x 才有）。4.0.x 及更早**没有这个串**，
#   版本号是逗号开头的四段字面量 `\0,2.1.4.11459\0`（邻居是 electron_node 的 `.cc:line` 串）。
#   不加回退的话，老版本上会直接抛 `[frida] error in find wmpf version` 启动失败。
#   补丁加一条回退正则，纯增量，对新版本无影响（新版本仍走 release 串那条路）。
#
# 用法: bash hook_patch.sh [容器名]     默认 woc-hook
set -u
export PATH="/usr/bin:/bin:$PATH"
C="${1:-woc-hook}"
rc=0

# ───────────────────────── 补丁 1：hook.js 场景号白名单 ─────────────────────────
docker exec -i "$C" sh <<'INNER' || rc=1
F=/opt/wmpf/frida/hook.js
if [ ! -f "$F" ]; then
  echo "[patch1] 找不到 $F —— 还没装 WMPFDebugger？先按部署文档第 4 步装"
  exit 1
fi
if grep -q "1183" "$F"; then
  echo "[patch1] hook.js 已打过补丁，跳过"
  exit 0
fi
cp "$F" /tmp/hook.js.orig
# ⚠️ 这里**故意不内嵌上游源码的任何字面量**（既避免分发上游代码片段，也让补丁对上游改写
#    更鲁棒）：全部改成结构无关的插入式改写 ——
#    ① 白名单：在 hook.js 里找到那个「场景号数组」的任何一处字面量，往里插一个 1183；
#    ② 诊断日志：找到「读场景号 + 判断是否在白名单」的那个 includes 调用，在它前面插一行 send。
#    两者都用锚点定位（数组形态 / includes 调用形态），不依赖具体数字与变量名。
node -e '
const fs = require("fs");
// ⚠️ 取最后一个参数，别写 process.argv[1]：
//    `node -e "…" path` 时 argv[1] 就是 path；但换成 `node script.js path` 时
//    argv[1] 是脚本路径、argv[2] 才是 path —— 两种跑法共用一行更稳（实测踩过）。
const p = process.argv[process.argv.length - 1];
let s = fs.readFileSync(p, "utf8");
const orig = s;

// ① 场景号白名单：补一个 1183。
//    定位任意「多个整数组成的数组字面量」（上游那串白名单就长这样），
//    在其中最后一个元素后追加 1183。只改第一处匹配，避免误伤无关数组。
//    ⚠️ 尾部那个 `,?` 不能省：上游数组带**尾逗号**（`[1145, 1256, …, 1308,]`），
//    少了它整个正则匹配不上（实测踩过，补丁会静默不生效）。
let patchedList = false;
s = s.replace(/\[(\s*\d{3,5}\s*(?:,\s*\d{3,5}\s*)+,?)\]/, (m, inner) => {
  if (inner.includes("1183")) { patchedList = true; return m; }
  patchedList = true;
  return "[" + inner.replace(/[\s,]*$/, "") + ", 1183]";
});

// ② 诊断日志：在「判断场景号是否在白名单」那个 includes 调用前插一行 send。
//    锚点是调用形态本身（任意数组变量名 / 任意读出方式），不引用上游的变量名。
let patchedLog = false;
s = s.replace(
  /([ \t]*)(if\s*\(\s*!\s*([A-Za-z_$][\w$]*)\s*\.\s*includes\s*\()/,
  (m, indent, head, arr) => {
    patchedLog = true;
    return indent + "send(\"[hook] scene NOT in whitelist: \" + " + arr + ".readInt());\n" + indent + head;
  }
);

if (!patchedList || !patchedLog) {
  console.log("[patch1] 失败：锚点没找到（清单=" + patchedList + " 日志=" + patchedLog + "），已回滚");
  fs.writeFileSync(p, orig, "utf8");
  process.exit(1);
}
fs.writeFileSync(p, s, "utf8");
console.log("[patch1] hook.js 已打补丁：白名单 +1183，并加上了场景号诊断日志");
' "$F"
if grep -q "1183" "$F"; then
  echo "[patch1] hook.js 已打补丁：白名单 +1183，并加上了场景号诊断日志（原文件备份 /tmp/hook.js.orig）"
else
  echo "[patch1] 失败：hook.js 结构可能已变，已回滚"
  cp /tmp/hook.js.orig "$F"
  exit 1
fi
INNER

# ───────────────────────── 补丁 2：linux.ts 版本探测回退 ─────────────────────────
docker exec -i "$C" sh <<'INNER' || rc=1
F=/opt/wmpf/src/platform/linux.ts
if [ ! -f "$F" ]; then
  echo "[patch2] 找不到 $F，跳过"
  exit 0
fi
if grep -q "WMPF_LEGACY_VERSION_FALLBACK" "$F" || grep -q "const legacy = /" "$F"; then
  echo "[patch2] linux.ts 已打过补丁，跳过"
  exit 0
fi
cp "$F" /tmp/linux.ts.orig
cat > /tmp/patch2.cjs <<'JS'
const fs = require("fs");
const p = "/opt/wmpf/src/platform/linux.ts";
let s = fs.readFileSync(p, "utf8");
// 在 searchWmpfVersionInFile 函数体内、它最后一个 "    return 0;\n}" 之前插入回退
const start = s.indexOf("function searchWmpfVersionInFile");
const end = s.indexOf("export class LinuxPlatform");
if (start < 0 || end <= start) {
  console.log("[patch2] 失败：找不到 searchWmpfVersionInFile / LinuxPlatform，跳过");
  process.exit(1);
}
const seg = s.slice(start, end);
const at = seg.lastIndexOf("    return 0;");
if (at < 0) {
  console.log("[patch2] 失败：函数体内找不到 return 0，跳过");
  process.exit(1);
}
const add = [
  "    // WMPF_LEGACY_VERSION_FALLBACK: 4.0.x 及更早没有 wmpf_release/ 串，",
  "    // 版本号是逗号开头的四段字面量 \\0,2.1.4.11459\\0（邻居是 electron_node 的 .cc:line 串）",
  "    const legacy = /\\0,(\\d+\\.\\d+\\.\\d+\\.\\d{3,6})\\0/.exec(binstr);",
  "    if (legacy && legacy[1]) {",
  '        return Number(legacy[1].split(".").pop());',
  "    }",
  "",
].join("\n");
const abs = start + at;
fs.writeFileSync(p, s.slice(0, abs) + add + s.slice(abs), "utf8");
console.log("[patch2] linux.ts 已加版本探测回退");
JS
if node /tmp/patch2.cjs && grep -q "WMPF_LEGACY_VERSION_FALLBACK" "$F"; then
  echo "[patch2] 完成（原文件备份 /tmp/linux.ts.orig）"
else
  cp /tmp/linux.ts.orig "$F"
  echo "[patch2] 失败，已回滚"
  exit 1
fi
INNER

exit $rc
