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
  return JSON.stringify(out);
})()`;

function mask(v) { const s = String(v || ''); return s.length > 8 ? `${s.slice(0, 4)}${'*'.repeat(s.length - 8)}${s.slice(-4)} (${s.length})` : '*'.repeat(s.length); }
function jwtExp(t) { try { return JSON.parse(Buffer.from(t.split('.')[1], 'base64url').toString()).exp; } catch (e) { return null; } }
function loadEnv() {
  const env = {};
  try { for (const l of fs.readFileSync(ENVFILE, 'utf8').split('\n')) { const i = l.indexOf('='); if (i > 0) env[l.slice(0, i).trim()] = l.slice(i + 1).trim(); } } catch (e) {}
  return env;
}

function attempt() {
  return new Promise((resolve) => {
    let pick = null, pages = '';
    const ws = new WebSocket(`ws://127.0.0.1:${PORT}`);
    let done = false;
    const finish = () => { if (done) return; done = true; try { ws.close(); } catch (e) {} resolve({ pick, pages }); };
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
        console.log(`[scan] ctx ${cid} appId=${d.__appId || '-'} mpId=${mask(d.mpId) || '-'} pages=${d.__pages || '-'} 字段=${n}${byApp || byMp ? '  <== 命中' : ''}`);
        if (!(byApp || byMp || (!APPID && !MPID))) return;
        const fields = {}; for (const k of Object.keys(d)) if (!k.startsWith('__')) fields[k] = d[k];
        if (!pick || Object.keys(fields).length > Object.keys(pick.fields).length) pick = { cid, fields, appId: d.__appId };
      }
    });
    ws.on('error', () => finish());
  });
}

(async () => {
  const env0 = loadEnv();
  let found = null;
  const end = Date.now() + WAIT * 1000;
  while (Date.now() < end) {
    const r = await attempt();
    if (r.pick && (!found || Object.keys(r.pick.fields).length > Object.keys(found).length)) found = r.pick.fields;
    if (found && found.openId && found.unionId && found.memberId) break;
    await new Promise((x) => setTimeout(x, 1500));
  }
  if (!found || !Object.keys(found).length) { console.log('[END] 没抓到目标上下文（确认小程序已打开）'); process.exit(1); }

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
