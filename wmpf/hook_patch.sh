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
sed -i 's/1256, 1260, 1302, 1308,/1256, 1260, 1302, 1308, 1183,/' "$F"
sed -i 's|if (!sceneNumberArray.includes(miniappScenePtr.readInt())) {|if (!sceneNumberArray.includes(miniappScenePtr.readInt())) {\n        send("[hook] scene NOT in whitelist: " + miniappScenePtr.readInt());|' "$F"
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
