// 通用身份采集（在 woc-hook 容器内运行，按 appId / mpId 双重校验挑上下文）
//
// 用法：
//   docker exec woc-hook sh -c 'cd <脚本目录> && ENVFILE=<slug>.env WX_APPID=<appid> \
//     NODE_PATH=/opt/wmpf/node_modules node wxident.js [等待秒数]'
//
// 会把 token / mpId / openId / unionId / gcId / gameId / memberId / cardId / cardNo / thirdShopId
// 以 WX_* 前缀写进 ENVFILE。身份字段一律以小程序 storage 为准；token 取 exp 更晚的那个。
//
// 与辣可可原版的差别：appId / mpId 不再写死，改从环境变量来；env 前缀由 LAKEKE_ 改为 WX_。
const WebSocket = require('ws');
const fs = require('fs');

const ENVFILE = process.env.ENVFILE || './brand.env';
const PORT = Number(process.env.CDP_PORT || 62000);
const WAIT = Number(process.argv[2] || 60);
const APPID = process.env.WX_APPID || '';
const MPID = process.env.WX_MPID || '';
const PREFIX = process.env.WX_PREFIX || 'WX_';
const NEED = ['token', 'mpId', 'openId', 'unionId', 'gameId', 'memberId', 'cardId', 'cardNo', 'thirdShopId', 'gcId'];

const JS = `(function () {
  var need = ${JSON.stringify(NEED)};
  var out = {};
  try { var ai = wx.getAccountInfoSync(); out.__appId = (ai.miniProgram && ai.miniProgram.appId) || ''; } catch (e) { out.__appId = ''; }
  function put(k, v) { if (v === undefined || v === null || v === '') return; if (out[k] === undefined) out[k] = String(v); }
  function coerce(v) {
    if (typeof v === 'string') { var t = v.trim();
      if ((t.charAt(0) === '{' && t.charAt(t.length - 1) === '}') || (t.charAt(0) === '[' && t.charAt(t.length - 1) === ']')) {
        try { return JSON.parse(t); } catch (e) { return v; } } }
    return v; }
  function scan(o, d) {
    if (!o || typeof o !== 'object' || d > 4) return;
    if (Object.prototype.toString.call(o) === '[object Array]') { for (var i = 0; i < o.length && i < 50; i++) scan(o[i], d + 1); return; }
    for (var k in o) { var v = o[k], lk = String(k).toLowerCase();
      if (typeof v === 'string' || typeof v === 'number') { for (var n = 0; n < need.length; n++) if (lk === need[n].toLowerCase()) put(need[n], v); }
      else scan(v, d + 1); } }
  try { wx.getStorageInfoSync().keys.forEach(function (k) {
    try { var raw = wx.getStorageSync(k), lk = String(k).toLowerCase();
      for (var n = 0; n < need.length; n++) if (lk === need[n].toLowerCase() && (typeof raw === 'string' || typeof raw === 'number')) put(need[n], raw);
      scan(coerce(raw), 0); } catch (e) {} }); } catch (e) {}
  try { var app = getApp(); if (app) scan(app.globalData, 0); } catch (e) {}
  try { var pages = (getCurrentPages() || []).map(function (p) { return p.route || ''; }); out.__pages = pages.join(',');
    (getCurrentPages() || []).forEach(function (p) { var d = p.data || {};
      if (d.gameId !== undefined) put('gameId', d.gameId);
      scan(d, 0);
      var mi = d.memberInfo || d.member || {};
      if (mi.id !== undefined) put('memberId', mi.id);
      if (mi.cardId !== undefined) put('cardId', mi.cardId);
      if (mi.cardNo !== undefined) put('cardNo', mi.cardNo);
      if (mi.mcId !== undefined) put('thirdShopId', mi.mcId);
      var bi = d.baseInfo || {};
      if (bi.mcId !== undefined) put('thirdShopId', bi.mcId);
      if (bi.gameId !== undefined) put('gameId', bi.gameId);
      if (bi.mpId !== undefined) put('mpId', bi.mpId);
      scan(mi, 0); scan(bi, 0); }); } catch (e) {}
  // ── JWT 兜底（与模板无关）────────────────────────────────────────────
  // mpId 有时**只存在于登录 token 的 payload 里**，storage 里压根没有 mpId 这个键。
  // 实测小大董（pages/cardhome/home/index2 模板）：storage 只有 authData-<随机串>，
  // 里面那个 JWT 解出来是 {sub:<openId>, appid:<appId>, mpid:<mpId>, exp:<...>}。
  // 凡是登录过的小程序都会有登录 token，所以这条路覆盖面最广。
  // 小程序环境没有 atob，解码放 Node 侧做（见 decodeJwts）。
  try {
    var jwts = [], seenJ = {};
    function grabJwt(s) {
      if (typeof s !== 'string' || s.length < 40) return;
      var m = s.match(/eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{10,}/g);
      if (!m) return;
      for (var i = 0; i < m.length; i++) { if (!seenJ[m[i]]) { seenJ[m[i]] = 1; jwts.push(m[i]); } }
    }
    function walkJwt(o, d) {
      if (d > 4) return;
      if (typeof o === 'string') {
        grabJwt(o);
        if (o.charAt(0) === '{' || o.charAt(0) === '[') { try { walkJwt(JSON.parse(o), d + 1); } catch (e) {} }
        return;
      }
      if (!o || typeof o !== 'object') return;
      for (var k in o) walkJwt(o[k], d + 1);
    }
    try { wx.getStorageInfoSync().keys.forEach(function (k) { try { walkJwt(wx.getStorageSync(k), 0); } catch (e) {} }); } catch (e) {}
    try { var app2 = getApp(); if (app2) walkJwt(app2.globalData, 0); } catch (e) {}
    try { (getCurrentPages() || []).forEach(function (p) { walkJwt(p.data, 0); }); } catch (e) {}
    if (jwts.length) out.__jwts = jwts.slice(0, 12);
  } catch (e) {}
  return JSON.stringify(out);
})()`;

function mask(v) { const s = String(v || ''); return s.length > 8 ? `${s.slice(0, 4)}${'*'.repeat(s.length - 8)}${s.slice(-4)} (${s.length})` : '*'.repeat(s.length); }
function jwtExp(t) { try { return JSON.parse(Buffer.from(t.split('.')[1], 'base64url').toString()).exp; } catch (e) { return null; } }

// 解小程序 storage 里的登录 token，把 payload 当成身份来源。
// 这是**与模板无关**的一条路：storage 里可能没有 mpId 这个键（小大董就没有），
// 但登录 token 的 payload 里必然有 mpid / sub(openId) / appid。
function decodeJwts(list) {
  const out = {};
  let bestExp = 0, bestTok = '';
  for (const t of (list || [])) {
    let d = null;
    try { d = JSON.parse(Buffer.from(String(t).split('.')[1], 'base64url').toString()); } catch (e) { continue; }
    if (!d || typeof d !== 'object') continue;
    if (d.mpid && out.mpId === undefined) out.mpId = String(d.mpid);
    if (d.sub && out.openId === undefined) out.openId = String(d.sub);
    if (d.appid) out.appId = String(d.appid);
    const e = Number(d.exp) || 0;
    if (e > bestExp) { bestExp = e; bestTok = String(t); }
  }
  if (bestTok) out.token = bestTok;
  return out;
}
function loadEnv() {
  const env = {};
  try { for (const l of fs.readFileSync(ENVFILE, 'utf8').split('\n')) { const i = l.indexOf('='); if (i > 0) env[l.slice(0, i).trim()] = l.slice(i + 1).trim(); } } catch (e) {}
  return env;
}

function attempt() {
  return new Promise((resolve) => {
    let pick = null, pickAny = null, pages = '';
    const ws = new WebSocket(`ws://127.0.0.1:${PORT}`);
    let done = false;
    const finish = () => { if (done) return; done = true; try { ws.close(); } catch (e) {} resolve({ pick, pickAny, pages }); };
    ws.on('open', () => {
      ws.send(JSON.stringify({ id: 1, method: 'Runtime.enable', params: {} }));
      for (let cid = 1; cid <= 60; cid++) setTimeout(() => ws.send(JSON.stringify({ id: 1000 + cid, method: 'Runtime.evaluate',
        params: { expression: '(typeof wx)', returnByValue: true, contextId: cid } })), cid * 35);
      setTimeout(finish, 9000);
    });
    ws.on('message', (data) => {
      let m; try { m = JSON.parse(data.toString()); } catch (e) { return; }
      const id = m.id; if (typeof id !== 'number' || id < 1000) return;
      const tag = Math.floor(id / 1000) - 1, cid = id % 1000;
      const val = m.result && m.result.result && m.result.result.value;
      if (!val) return;
      if (tag === 0 && val === 'object') {
        ws.send(JSON.stringify({ id: 2000 + cid, method: 'Runtime.evaluate', params: { expression: JS, returnByValue: true, contextId: cid } }));
      } else if (tag === 1) {
        let d; try { d = JSON.parse(val); } catch (e) { return; }
        if (d.__pages) pages = d.__pages;
        const byApp = APPID && d.__appId === APPID;
        const byMp = MPID && d.mpId === MPID;
        const n = Object.keys(d).filter((k) => !k.startsWith('__')).length;
        console.log(`[scan] ctx ${cid} appId=${d.__appId || '-'} mpId=${mask(d.mpId) || '-'} pages=${d.__pages || '-'} 字段=${n} jwt=${(d.__jwts || []).length}${byApp || byMp ? '  <== 命中' : ''}`);
        if (Array.isArray(d.__jwts)) for (const t of d.__jwts) if (!ALL_JWTS.includes(t)) ALL_JWTS.push(t);
        const fields = {}; for (const k of Object.keys(d)) if (!k.startsWith('__')) fields[k] = d[k];
        if (!Object.keys(fields).length) return;
        // appId 只当**优先项**：brands.json 里的 appId 来自 `--find`，多卡片场景下归属会错位
        // （实测：卡片标题「大董会员商城」实际是 wxfa7ab5e2520cece8，表里却记着另一个）。
        // 拿它当硬门槛会把**正确**的上下文丢掉 → 一律 notoken，且极难查。
        if (byApp || byMp) {
          if (!pick || Object.keys(fields).length > Object.keys(pick.fields).length) pick = { cid, fields, appId: d.__appId };
        } else {
          if (!pickAny || Object.keys(fields).length > Object.keys(pickAny.fields).length) pickAny = { cid, fields, appId: d.__appId };
        }
      }
    });
    ws.on('error', () => finish());
  });
}

const ALL_JWTS = [];

(async () => {
  const env0 = loadEnv();
  let found = null;
  const end = Date.now() + WAIT * 1000;
  let warned = false;
  while (Date.now() < end) {
    const r = await attempt();
    const chosen = r.pick || r.pickAny;
    if (chosen && !r.pick && !warned) {
      console.log(`[warn] 没有 ctx 的 appId 等于期望值 ${APPID || '(未指定)'}，`
        + `改用实际打开的那个（ctx ${chosen.cid} appId=${chosen.appId}）—— `
        + 'brands.json 里的 appId 多半是 --find 归属错位，建议改成这个值');
      warned = true;
    }
    if (chosen && (!found || Object.keys(chosen.fields).length > Object.keys(found).length)) found = chosen.fields;
    // 只要拿到 openId + mpId 就够了（登录只要这两个）；memberId 对**非会员**永远没有，
    // 卡在 memberId 上会让非会员的号白等到超时（实测）。
    if (found && found.openId && found.mpId) break;
    await new Promise((x) => setTimeout(x, 1500));
  }
  if (!found || !Object.keys(found).length) { console.log('[END] 没抓到目标上下文（确认小程序已打开）'); process.exit(1); }

  // JWT 兜底（与模板无关）：storage 里没有 mpId 键时，从登录 token 的 payload 里取。
  const jf = decodeJwts(ALL_JWTS);
  if (Object.keys(jf).length) {
    const filled = [];
    for (const [k, v] of Object.entries(jf)) {
      if (k === 'token') {
        const eNew = jwtExp(v), eCur = found.token ? jwtExp(found.token) : 0;
        if (!eCur || (eNew && eNew > eCur)) { found.token = v; filled.push('token'); }
      } else if (!found[k]) { found[k] = v; filled.push(k); }
    }
    console.log(`[jwt] 从登录 token payload 解出：补上了 ${filled.join(', ') || '(无新增)'}（共试了 ${ALL_JWTS.length} 个 token）`);
  } else {
    console.log(`[jwt] 没从 storage 里找到可用 token（试了 ${ALL_JWTS.length} 个候选）`);
  }

  const env = Object.assign({}, env0);
  for (const k of ['openId', 'unionId', 'gcId', 'mpId', 'gameId', 'memberId', 'cardId', 'cardNo', 'thirdShopId']) {
    if (found[k]) env[PREFIX + k.toUpperCase()] = found[k];
  }
  if (found.token) {
    const cur = env[PREFIX + 'TOKEN'] || '';
    const eNew = jwtExp(found.token), eCur = cur ? jwtExp(cur) : 0;
    if (!eCur || (eNew && eNew > eCur)) { env[PREFIX + 'TOKEN'] = found.token; console.log('[token] 采用小程序 storage 里的 token'); }
    else console.log(`[token] 保留 env 里的新 token（比 storage 新 ${((eCur - (eNew || 0)) / 60).toFixed(0)} 分钟）`);
  }
  if (APPID) env[PREFIX + 'APPID'] = APPID;
  fs.writeFileSync(ENVFILE, Object.entries(env).filter(([, v]) => v).map(([k, v]) => `${k}=${v}`).join('\n') + '\n');
  console.log(`\n[save] 已写入 ${ENVFILE}`);
  for (const [k, v] of Object.entries(env).filter(([k]) => k.startsWith(PREFIX)).sort()) {
    console.log(`  ${k} = ${/TOKEN|OPENID|UNIONID/.test(k) ? mask(v) : v}`);
  }
  process.exit(0);
})();
