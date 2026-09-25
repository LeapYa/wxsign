// 通用 token 刷新（在 woc-hook 容器内运行）
//
// 原理：wx.login() 产出的 jsCode 与 appid 绑死，只能在**那个小程序开着**时由微信客户端产生，
//      服务端无法自举。所以签到的 token 必须每 1~2 小时这样换一次。
//
// 用法：
//   docker exec woc-hook sh -c 'cd <脚本目录> && ENVFILE=<slug>.env WX_APPID=<appid> \
//     NODE_PATH=/opt/wmpf/node_modules node wxrefresh.js [force]'
//   force：即使现有 token 还没过期也重换。
//
// 与辣可可原版的差别：appId / mpId / 目标 openId 全部改从环境变量来；env 前缀 LAKEKE_ → WX_。
const WebSocket = require('ws');
const fs = require('fs');
const https = require('https');

const ENVFILE = process.env.ENVFILE || './brand.env';
const PORT = Number(process.env.CDP_PORT || 62000);
const APPID = process.env.WX_APPID || '';
const PREFIX = process.env.WX_PREFIX || 'WX_';
const LOGIN_URL = 'https://wechat.wuuxiang.com/i5xforyou/auth/login';

const IDENT_JS = `(function () {
  var out = {};
  try { var ai = wx.getAccountInfoSync(); out.appId = (ai.miniProgram && ai.miniProgram.appId) || null; } catch (e) {}
  function scan(o, d) {
    if (!o || typeof o !== 'object' || d > 4) return;
    if (Object.prototype.toString.call(o) === '[object Array]') { for (var i = 0; i < o.length && i < 30; i++) scan(o[i], d + 1); return; }
    for (var k in o) {
      var v = o[k], lk = String(k).toLowerCase();
      if (typeof v === 'string' || typeof v === 'number') {
        if (lk === 'mpid' && out.mpId === undefined) out.mpId = String(v);
        if (lk === 'openid' && out.openId === undefined) out.openId = String(v);
        if (lk === 'unionid' && out.unionId === undefined) out.unionId = String(v);
        if (lk === 'gcid' && out.gcId === undefined) out.gcId = String(v);
      } else scan(v, d + 1);
    }
  }
  try {
    wx.getStorageInfoSync().keys.forEach(function (k) {
      try { var v = wx.getStorageSync(k); if (typeof v === 'string') { try { v = JSON.parse(v); } catch (e) {} } scan(v, 0); } catch (e) {}
    });
  } catch (e) { out.__err = e.message; }
  try { (getCurrentPages() || []).forEach(function (p) { scan(p.data, 0); }); } catch (e) {}
  // JWT 兜底：mpId 常常**只在登录 token 的 payload 里**（storage 里没有 mpId 这个键）。
  // 见 wxident.js 同名段落 —— 解码统一放 Node 侧（小程序环境没有 atob）。
  try {
    var jwts = [], seenJ = {};
    function grabJwt(s) {
      if (typeof s !== 'string' || s.length < 40) return;
      var m = s.match(/eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{10,}/g);
      if (!m) return;
      for (var i = 0; i < m.length; i++) { if (!seenJ[m[i]]) { seenJ[m[i]] = 1; jwts.push(m[i]); } }
    }
    wx.getStorageInfoSync().keys.forEach(function (k) {
      try {
        var v = wx.getStorageSync(k);
        var s = (typeof v === 'string') ? v : JSON.stringify(v);
        grabJwt(s);
      } catch (e) {}
    });
    if (jwts.length) out.__jwts = jwts.slice(0, 12);
  } catch (e) {}
  return JSON.stringify(out);
})()`;

const LOGIN_JS = `new Promise(function (resolve) {
  try { wx.login({ success: function (r) { resolve(JSON.stringify({ ok: true, code: r.code })); },
                   fail: function (e) { resolve(JSON.stringify({ ok: false, err: String(e && e.errMsg) })); } }); }
  catch (e) { resolve(JSON.stringify({ ok: false, err: String(e) })); }
})`;

function mask(v) { const s = String(v || ''); return s.length > 8 ? `${s.slice(0, 4)}${'*'.repeat(s.length - 8)}${s.slice(-4)} (${s.length})` : '*'.repeat(s.length); }
function jwtExp(t) { try { return JSON.parse(Buffer.from(t.split('.')[1], 'base64url').toString()).exp; } catch (e) { return null; } }

// 解小程序 storage 里的登录 token，把 payload 当身份来源（与模板无关）。
// storage 里可能没有 mpId 这个键（小大董就没有），但登录 token 的 payload 必有 mpid/sub/appid。
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
function post(url, form) {
  return new Promise((resolve) => {
    const body = new URLSearchParams(form).toString();
    const u = new URL(url);
    const req = https.request({
      hostname: u.hostname, path: u.pathname, method: 'POST', timeout: 15000, rejectUnauthorized: false,
      headers: {
        'Content-Type': 'application/x-www-form-urlencoded',
        'Content-Length': Buffer.byteLength(body),
        apiCaller: 'wxxcx', 'X-Requested-With': 'XMLHttpRequest',
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) '
          + 'Chrome/132.0.0.0 Safari/537.36 MicroMessenger/7.0.20.1781(0x6700143B) NetType/WIFI '
          + 'MiniProgramEnv/Windows WindowsWechat/WMPF XWEB/25715',
      },
    }, (res) => {
      let d = '';
      res.on('data', (c) => { d += c; });
      res.on('end', () => { try { resolve(JSON.parse(d)); } catch (e) { resolve({ status: -2, message: 'bad json: ' + d.slice(0, 120) }); } });
    });
    req.on('error', (e) => resolve({ status: -1, message: String(e.message) }));
    req.on('timeout', () => { req.destroy(); resolve({ status: -1, message: 'timeout' }); });
    req.write(body); req.end();
  });
}

// 单连接内：枚举上下文 → 取身份 → wx.login（同一连接里做，因为 context id 每次连接都重编号）
function collect(seconds) {
  return new Promise((resolve) => {
    const idents = {}, codes = {}, ctxs = [];
    const ws = new WebSocket(`ws://127.0.0.1:${PORT}`);
    let done = false;
    const finish = () => {
      if (done) return; done = true;
      try { ws.close(); } catch (e) {}
      resolve({ ctxs, idents, codes });
    };
    ws.on('open', () => {
      ws.send(JSON.stringify({ id: 1, method: 'Runtime.enable', params: {} }));
      for (let cid = 1; cid <= 60; cid++) {
        setTimeout(() => ws.send(JSON.stringify({ id: 1000 + cid, method: 'Runtime.evaluate',
          params: { expression: '(typeof wx)', returnByValue: true, contextId: cid } })), cid * 40);
      }
      setTimeout(finish, seconds * 1000);
    });
    ws.on('message', (data) => {
      let m; try { m = JSON.parse(data.toString()); } catch (e) { return; }
      const id = m.id; if (typeof id !== 'number' || id < 1000) return;
      const tag = Math.floor(id / 1000) - 1, cid = id % 1000;
      const val = m.result && m.result.result && m.result.result.value;
      if (!val) return;
      if (tag === 0 && val === 'object') {
        ctxs.push(cid);
        ws.send(JSON.stringify({ id: 2000 + cid, method: 'Runtime.evaluate', params: { expression: IDENT_JS, returnByValue: true, contextId: cid } }));
      } else if (tag === 1) {
        try {
          const ident = JSON.parse(val);
          // JWT 兜底：storage 里没有 mpId 键时，从登录 token 的 payload 补
          const jf = decodeJwts(ident.__jwts);
          for (const [k, v] of Object.entries(jf)) {
            if (k === 'token') { const eN = jwtExp(v), eC = ident.token ? jwtExp(ident.token) : 0; if (!eC || eN > eC) ident.token = v; }
            else if (!ident[k]) ident[k] = v;
          }
          delete ident.__jwts;
          idents[cid] = ident;
        } catch (e) {}
        ws.send(JSON.stringify({ id: 3000 + cid, method: 'Runtime.evaluate',
          params: { expression: LOGIN_JS, returnByValue: true, awaitPromise: true, contextId: cid } }));
      } else if (tag === 2) {
        try { codes[cid] = JSON.parse(val); } catch (e) {}
      }
    });
    ws.on('error', () => finish());
  });
}

(async () => {
  const env = loadEnv();
  const targetOpen = env[PREFIX + 'OPENID'] || '';
  const force = process.argv[2] === 'force' || process.env.WX_FORCE === '1';
  if (!force && env[PREFIX + 'TOKEN']) {
    const exp = jwtExp(env[PREFIX + 'TOKEN']);
    if (exp && exp - Date.now() / 1000 > 120) {
      console.log(`[refresh] 现有 token 仍有效（还剩 ${((exp - Date.now() / 1000) / 60).toFixed(0)} 分钟），跳过登录`);
      process.exit(0);
    }
  }
  console.log(`[refresh] 期望 appId=${APPID || '(不限)'} openId=${mask(targetOpen) || '(未指定)'}`);

  let got = null;
  for (let round = 1; round <= 3 && !got; round++) {
    const r = await collect(14);
    console.log(`[enum] 有 wx 的上下文=${JSON.stringify(r.ctxs)}`);
    for (const cid of r.ctxs) {
      const ident = r.idents[cid] || {}, codeinfo = r.codes[cid] || {};
      if (APPID && ident.appId && ident.appId !== APPID) { console.log(`[try] ctx ${cid}: appId=${ident.appId} 跳过（不是目标号）`); continue; }
      if (!codeinfo.ok) { console.log(`[try] ctx ${cid}: 拿不到 code (${codeinfo.err || '-'})`); continue; }
      if (targetOpen && ident.openId && ident.openId !== targetOpen) { console.log(`[try] ctx ${cid}: openId 不是目标账号，跳过`); continue; }
      const mp = ident.mpId || env[PREFIX + 'MPID'] || '';
      if (!mp) { console.log(`[try] ctx ${cid}: 拿不到 mpId，跳过`); continue; }
      console.log(`[try] ctx ${cid}: appId=${ident.appId} mpId=${mask(mp)} openId=${mask(ident.openId)} code=ok`);
      const resp = await post(LOGIN_URL, { code: codeinfo.code, mpid: mp });
      const ok = resp.status === 0 && resp.result && typeof resp.result === 'object';
      console.log(`      /auth/login status=${resp.status}${ok ? '' : ' ' + (resp.message || '')}`);
      if (ok) { got = { ident, result: resp.result }; break; }
    }
  }
  if (!got) { console.log('[FAIL] 未能换到 token（确认目标小程序已打开、且 appId 配置正确）'); process.exit(1); }

  const token = got.result.token || '';
  const exp = jwtExp(token);
  if (exp) console.log(`[refresh] 新 token len=${String(token).length} exp=${new Date(exp * 1000).toLocaleString()}（${((exp - Date.now() / 1000) / 60).toFixed(0)} 分钟后）`);
  env[PREFIX + 'TOKEN'] = token;
  for (const [k, idk] of [['WX_MPID', 'mpId'], ['WX_OPENID', 'openId'], ['WX_UNIONID', 'unionId'], ['WX_GCID', 'gcId']]) {
    if (got.ident[idk] && !env[k]) env[k] = got.ident[idk];
  }
  fs.writeFileSync(ENVFILE, Object.entries(env).filter(([, v]) => v).map(([k, v]) => `${k}=${v}`).join('\n') + '\n');
  console.log(`[save] 已更新 ${ENVFILE}`);
  process.exit(0);
})();
