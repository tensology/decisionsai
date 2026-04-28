// ================================================================
// VOLLMERS BOT — Cloudflare Worker
// ================================================================

const TG_TOKEN   = (typeof process !== 'undefined' && process.env?.TG_TOKEN) || globalThis.TG_TOKEN || '';
const TG_CHAT_ID = (typeof process !== 'undefined' && process.env?.TG_CHAT_ID) || globalThis.TG_CHAT_ID || '';
const BB_KEY     = (typeof process !== 'undefined' && process.env?.BB_KEY) || globalThis.BB_KEY || '';
const BB_SECRET  = (typeof process !== 'undefined' && process.env?.BB_SECRET) || globalThis.BB_SECRET || '';
const BB_BASE    = 'https://api.bybit.com'; // Use https://api-demo.bybit.com for demo
const TG_BASE    = `https://api.telegram.org/bot${TG_TOKEN}`;

const CORS = {
  'Access-Control-Allow-Origin':  '*',
  'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
  'Access-Control-Allow-Headers': 'Content-Type',
  'Content-Type': 'application/json',
};

function json(data, status = 200) {
  return new Response(JSON.stringify(data), { status, headers: CORS });
}
function err(msg, status = 400) {
  return json({ ok: false, error: msg }, status);
}

// ── Bybit signing ────────────────────────────────────────────────
async function signBybit(payload) {
  const ts  = Date.now().toString();
  const rw  = '5000';
  const msg = ts + BB_KEY + rw + payload;
  const enc = new TextEncoder();
  const key = await crypto.subtle.importKey('raw', enc.encode(BB_SECRET),
    { name: 'HMAC', hash: 'SHA-256' }, false, ['sign']);
  const sig = await crypto.subtle.sign('HMAC', key, enc.encode(msg));
  const hex = [...new Uint8Array(sig)].map(b => b.toString(16).padStart(2,'0')).join('');
  return { hex, ts, rw };
}

async function bbGet(path, params = {}) {
  const qs = new URLSearchParams(params).toString();
  const { hex, ts, rw } = await signBybit(qs);
  const res = await fetch(`${BB_BASE}${path}${qs ? '?' + qs : ''}`, {
    headers: {
      'X-BAPI-API-KEY': BB_KEY, 'X-BAPI-SIGN': hex,
      'X-BAPI-SIGN-TYPE': '2', 'X-BAPI-TIMESTAMP': ts, 'X-BAPI-RECV-WINDOW': rw,
    }
  });
  return res.json();
}

async function bbPost(path, body = {}) {
  const js = JSON.stringify(body);
  const { hex, ts, rw } = await signBybit(js);
  const res = await fetch(`${BB_BASE}${path}`, {
    method: 'POST',
    headers: {
      'X-BAPI-API-KEY': BB_KEY, 'X-BAPI-SIGN': hex,
      'X-BAPI-SIGN-TYPE': '2', 'X-BAPI-TIMESTAMP': ts,
      'X-BAPI-RECV-WINDOW': rw, 'Content-Type': 'application/json',
    },
    body: js,
  });
  return res.json();
}

// ── Signal parser ────────────────────────────────────────────────
function parseSignal(txt) {
  const t  = txt.trim();
  const sm = t.match(/#(\w+?)(?:\.P)?\s+(SELL|BUY|SHORT|LONG)/i);
  if (!sm) return null;

  let symbol = sm[1].toUpperCase();
  const suffixes = ['USDT','BUSD','USDC','USD','BTC','ETH','PERP'];
  for (const sfx of suffixes) {
    if (symbol.endsWith(sfx)) { symbol = symbol.slice(0, -sfx.length); break; }
  }

  const side = (['SELL','SHORT'].includes(sm[2].toUpperCase())) ? 'Sell' : 'Buy';

  const em = t.match(/Entry\s*(?:Point|Price)?:?\s*([\d.]+)\s*[-–]\s*([\d.]+)/i)
           || t.match(/Entry\s*(?:Point|Price)?:?\s*([\d.]+)/i);
  if (!em) return null;

  const entryLow  = parseFloat(em[1]);
  const entryHigh = em[2] ? parseFloat(em[2]) : entryLow;
  const entryMid  = +((entryLow + entryHigh) / 2).toFixed(8);

  const tpAll = [...t.matchAll(/Take\s*[Pp]rofit\s*\d+:?\s*([\d.]+)/g)];
  const tps   = tpAll.map(m => parseFloat(m[1]));
  if (!tps.length) return null;

  const slm = t.match(/Stop\s*[Ll]oss:?\s*([\d.]+)/i);
  if (!slm) return null;

  return { symbol, side, entryLow, entryHigh, entryMid, tps, sl: parseFloat(slm[1]) };
}

// ── KV State Management ─────────────────────────────────────────
async function kvGet(env, key) {
  try {
    const val = await env.KV.get(key);
    return val ? JSON.parse(val) : null;
  } catch(e) { return null; }
}

async function kvSet(env, key, value, ttl = null) {
  try {
    const opts = ttl ? { expirationTtl: ttl } : {};
    await env.KV.put(key, JSON.stringify(value), opts);
    return true;
  } catch(e) { return false; }
}

// ── Get balance ──────────────────────────────────────────────────
async function getAvailableBalance() {
  for (const accountType of ['UNIFIED', 'CONTRACT']) {
    const r = await bbGet('/v5/account/wallet-balance', { accountType });
    if (r.retCode !== 0) continue;
    for (const account of (r.result?.list || [])) {
      // First try account-level total balance (most accurate)
      const totalEquity  = parseFloat(account.totalEquity || '0');
      const totalWallet  = parseFloat(account.totalWalletBalance || '0');
      const totalAvail   = parseFloat(account.totalAvailableBalance || '0');
      if (totalAvail > 0) return totalAvail;
      if (totalEquity > 0) return totalEquity;
      if (totalWallet > 0) return totalWallet;

      // Fallback: coin-level USDT
      const coins = account.coin || [];
      const usdt  = coins.find(c => c.coin === 'USDT');
      if (usdt) {
        const avail  = parseFloat(usdt.availableToWithdraw || '0');
        const wallet = parseFloat(usdt.walletBalance || '0');
        const equity = parseFloat(usdt.equity || '0');
        const bal    = Math.max(avail, wallet, equity);
        if (bal > 0) return bal;
      }
    }
  }
  throw new Error('No USDT balance found');
}

// ── Execute trade ────────────────────────────────────────────────
async function executeTrade(sig, riskPct = 5, leverage = 21.5, marginMode = 'ISOLATED') {
  const logs  = [];
  const log   = msg => logs.push(msg);
  const symbol = sig.symbol + 'USDT';
  const tpSplit = 0.20;

  // Balance & qty
  const balance   = await getAvailableBalance();
  // riskPct is used as fixed USDT margin amount (e.g. 50 = $50 margin per trade)
  // Total position value = marginAmount × leverage
  // Qty per TP = (marginAmount × leverage) / entryPrice / numTPs
  const marginAmount = riskPct; // riskPct field now holds fixed USDT margin e.g. 50
  const positionValue = marginAmount * leverage; // total leveraged position size
  const orderUsdt     = marginAmount; // for logging/reporting
  log(`Margin: $${marginAmount} USDT × ${leverage}× = $${positionValue.toFixed(2)} position`);

  // Get instrument info for qty precision
  let qtyStep = 1, minQty = 1;
  try {
    const info = await bbGet('/v5/market/instruments-info', { category: 'linear', symbol });
    const lot  = info?.result?.list?.[0]?.lotSizeFilter;
    if (lot) {
      qtyStep = parseFloat(lot.qtyStep || '1');
      minQty  = parseFloat(lot.minOrderQty || '1');
    }
  } catch(e) {}

  // Total qty based on leveraged position value
  const rawTotalQty   = positionValue / sig.entryMid;
  const rawQtyPerTp   = rawTotalQty * tpSplit;
  const stepDecimals  = (qtyStep.toString().split('.')[1] || '').length;
  const qtyPerTpFmt   = parseFloat(Math.max(minQty, Math.round(rawQtyPerTp / qtyStep) * qtyStep).toFixed(stepDecimals));
  const totalQty      = parseFloat((qtyPerTpFmt * sig.tps.length).toFixed(stepDecimals));
  log(`QtyStep:${qtyStep} PerTP:${qtyPerTpFmt} Total:${totalQty} @ entry ${sig.entryMid}`);

  if (qtyPerTpFmt <= 0 || totalQty <= 0) throw new Error('Order size too small — reduce entry price or increase margin');

  // Check for existing position
  try {
    const posCheck = await bbGet('/v5/position/list', { category: 'linear', symbol, settleCoin: 'USDT' });
    if (posCheck.retCode === 0) {
      const existing = (posCheck.result?.list || []).find(p => parseFloat(p.size) > 0);
      if (existing) throw new Error(`Position already open for ${symbol} — skipping duplicate`);
    }
  } catch(e) {
    if (e.message.includes('skipping duplicate')) throw e;
    log(`Position check skipped: ${e.message}`);
  }

  // Set leverage
  const levR = await bbPost('/v5/position/set-leverage', {
    category: 'linear', symbol,
    buyLeverage: leverage.toString(), sellLeverage: leverage.toString(),
  });
  log(levR.retCode === 0 ? `Leverage: ${leverage}×` : `Leverage note: ${levR.retMsg}`);

  // Set margin mode (1=Isolated, 0=Cross)
  const tradeMode = (marginMode === 'CROSS') ? 0 : 1;
  const isoR = await bbPost('/v5/position/switch-isolated', {
    category: 'linear', symbol, tradeMode,
    buyLeverage: leverage.toString(), sellLeverage: leverage.toString(),
  });
  log(isoR.retCode === 0 ? `Margin: ${tradeMode === 1 ? 'Isolated' : 'Cross'}` : `Margin note: ${isoR.retMsg}`);

  // Market entry with SL
  const entryR = await bbPost('/v5/order/create', {
    category: 'linear', symbol, side: sig.side,
    orderType: 'Market', qty: totalQty.toFixed(stepDecimals),
    timeInForce: 'GoodTillCancel',
    stopLoss: sig.sl.toString(), slTriggerBy: 'LastPrice',
    reduceOnly: false, positionIdx: 0,
  });
  if (entryR.retCode !== 0) throw new Error(`Entry failed: ${entryR.retMsg}`);
  log(`✓ Entry placed: ${entryR.result.orderId}`);

  // TP orders — 20% each
  const closeSide = sig.side === 'Sell' ? 'Buy' : 'Sell';
  const tpOrders  = [];
  let   tpPlaced  = 0;

  for (let i = 0; i < sig.tps.length; i++) {
    // Try GoodTillCancel + reduceOnly first (works on live)
    let tpR = await bbPost('/v5/order/create', {
      category: 'linear', symbol, side: closeSide,
      orderType: 'Limit', qty: qtyPerTpFmt.toFixed(stepDecimals),
      price: sig.tps[i].toString(), timeInForce: 'GoodTillCancel',
      reduceOnly: true, positionIdx: 0,
      orderLinkId: `TP${i+1}_${symbol}_${Date.now()}`,
    });
    // Demo fallback: try PostOnly
    if (tpR.retCode !== 0) {
      tpR = await bbPost('/v5/order/create', {
        category: 'linear', symbol, side: closeSide,
        orderType: 'Limit', qty: qtyPerTpFmt.toFixed(stepDecimals),
        price: sig.tps[i].toString(), timeInForce: 'PostOnly',
        reduceOnly: true, positionIdx: 0,
        orderLinkId: `TP${i+1}b_${symbol}_${Date.now()}`,
      });
    }
    // Demo fallback: try GTC without reduceOnly
    if (tpR.retCode !== 0) {
      tpR = await bbPost('/v5/order/create', {
        category: 'linear', symbol, side: closeSide,
        orderType: 'Limit', qty: qtyPerTpFmt.toFixed(stepDecimals),
        price: sig.tps[i].toString(), timeInForce: 'GoodTillCancel',
        reduceOnly: false, positionIdx: 0,
        orderLinkId: `TP${i+1}c_${symbol}_${Date.now()}`,
      });
    }
    if (tpR.retCode === 0) {
      tpOrders.push({ idx: i, orderId: tpR.result.orderId, price: sig.tps[i], qty: qtyPerTpFmt });
      tpPlaced++;
      log(`✓ TP${i+1} @ ${sig.tps[i]}`);
    } else {
      log(`✕ TP${i+1} failed (${tpR.retCode}): ${tpR.retMsg}`);
    }
  }
  log(`TPs placed: ${tpPlaced}/${sig.tps.length}`);

  return { entryOrderId: entryR.result.orderId, tpOrders, balance, orderUsdt, totalQty, qtyPerTp: qtyPerTpFmt, logs };
}

// ── Update SL ────────────────────────────────────────────────────
async function updateStopLoss(symbol, newSl) {
  const r = await bbPost('/v5/position/trading-stop', {
    category: 'linear', symbol: symbol + 'USDT',
    stopLoss: newSl.toString(), slTriggerBy: 'LastPrice', positionIdx: 0,
  });
  if (r.retCode !== 0) throw new Error(r.retMsg);
  return r;
}

// ── Close position ───────────────────────────────────────────────
async function closePosition(symbol, qty, side) {
  const closeSide = side === 'Sell' ? 'Buy' : 'Sell';
  const r = await bbPost('/v5/order/create', {
    category: 'linear', symbol: symbol + 'USDT', side: closeSide,
    orderType: 'Market', qty: qty.toString(),
    timeInForce: 'GoodTillCancel', reduceOnly: true, positionIdx: 0,
  });
  if (r.retCode !== 0) throw new Error(r.retMsg);
  return r.result;
}

// ── Telegram polling ─────────────────────────────────────────────
async function getTgUpdates(offset = 0) {
  const url = `${TG_BASE}/getUpdates?offset=${offset}&timeout=1&allowed_updates=["message","channel_post"]`;
  const r   = await fetch(url);
  const d   = await r.json();
  if (!d.ok && d.error_code === 409) {
    await fetch(`${TG_BASE}/deleteWebhook?drop_pending_updates=false`);
    const r2 = await fetch(url);
    return r2.json();
  }
  return d;
}

// ── CRON: runs every minute automatically ────────────────────────
async function runCron(env) {
  try {
    // ── READ all state in ONE batch at start ─────────────────────
    const [savedOffset, positions, settings, lastRunTs] = await Promise.all([
      kvGet(env, 'tg_offset').then(v => v || 0),
      kvGet(env, 'positions').then(v => v || {}),
      kvGet(env, 'settings').then(v => v || { marginAmount:50, leverage:21.5, marginMode:'ISOLATED', autoExec:true, trailSl:true }),
      kvGet(env, 'last_run').then(v => v || 0),
    ]);

    // Track what changed — only write KV if something actually changed
    let positionsChanged  = false;
    let offsetChanged     = false;
    let errorsToAdd       = [];
    const now             = Date.now();

    // ── SYNC positions with live Bybit (only if we have tracked positions) ──
    let liveBybitList = [];
    try {
      const livePos = await bbGet('/v5/position/list', { category: 'linear', settleCoin: 'USDT' });
      liveBybitList = (livePos.result?.list || []).filter(p => parseFloat(p.size) > 0);
      const liveSymbols = liveBybitList.map(p => p.symbol.replace('USDT',''));

      // Remove closed positions from KV
      for (const sym of Object.keys(positions)) {
        if (!liveSymbols.includes(sym)) {
          delete positions[sym];
          positionsChanged = true;
        }
      }
      // Add untracked live positions to KV
      for (const pos of liveBybitList) {
        const sym = pos.symbol.replace('USDT','');
        if (!positions[sym]) {
          positions[sym] = {
            side: pos.side, entry: parseFloat(pos.avgPrice || 0),
            tps: [], sl: parseFloat(pos.stopLoss || 0),
            currentSl: parseFloat(pos.stopLoss || 0),
            qty: parseFloat(pos.size), qtyPerTp: parseFloat(pos.size) / 5,
            tpsHit: 0, ts: now,
          };
          positionsChanged = true;
        }
      }
    } catch(e) { /* non-critical */ }

    // ── POLL Telegram for new signals ────────────────────────────
    const d = await getTgUpdates(savedOffset + 1);
    if (!d.ok) {
      // Still save last_run every 5 mins so dashboard knows bot is alive
      if (now - lastRunTs > 5 * 60 * 1000) await kvSet(env, 'last_run', now);
      return;
    }

    let lastId = savedOffset;
    const signals = [];

    for (const upd of d.result) {
      lastId = upd.update_id;
      const msg  = upd.message || upd.channel_post;
      if (!msg) continue;
      const chatId = msg.chat.id.toString();
      const text   = msg.text || msg.caption || '';
      if (chatId !== TG_CHAT_ID) continue;
      if (!text.match(/#\w+.*?(SELL|BUY|SHORT|LONG)/i)) continue;
      const sig = parseSignal(text);
      if (sig) signals.push({ signal: sig });
    }

    // Only save offset if it actually changed
    if (lastId > savedOffset) offsetChanged = true;

    // ── PROCESS signals ──────────────────────────────────────────
    if (settings.autoExec && signals.length > 0) {
      for (const item of signals) {
        const sym  = item.signal.symbol;
        const side = item.signal.side;

        const existing = positions[sym];
        if (existing) {
          if (existing.side === side) continue; // same direction — skip silently
          // Opposite direction — close using LIVE Bybit size (not stale KV qty)
          try {
            const livePos = liveBybitList.find(p => p.symbol === sym + 'USDT');
            const closeQty = livePos ? parseFloat(livePos.size) : existing.qty;
            if (closeQty > 0) {
              await closePosition(sym, closeQty, existing.side);
              delete positions[sym];
              positionsChanged = true;
              await new Promise(r => setTimeout(r, 2000));
            } else {
              // Position already closed on Bybit
              delete positions[sym];
              positionsChanged = true;
            }
          } catch(e) {
            // Log the error so we can see what went wrong
            errorsToAdd.push({ msg: 'Close failed for ' + sym + ': ' + e.message, sym, ts: Date.now() });
            continue;
          }
        }

        try {
          const result = await executeTrade(item.signal, settings.marginAmount, settings.leverage, settings.marginMode);
          positions[sym] = {
            side, entry: item.signal.entryMid,
            tps: item.signal.tps, sl: item.signal.sl,
            currentSl: item.signal.sl,
            qty: result.totalQty, qtyPerTp: result.qtyPerTp,
            tpsHit: 0, ts: now,
          };
          positionsChanged = true;
        } catch(e) {
          errorsToAdd.push({ msg: e.message, sym, ts: now });
        }
      }
    }

    // ── TRAILING SL ──────────────────────────────────────────────
    if (settings.trailSl && liveBybitList.length > 0) {
      for (const [sym, pos] of Object.entries(positions)) {
        const live = liveBybitList.find(p => p.symbol === sym + 'USDT');
        if (!live) continue;
        if (!pos.qtyPerTp || pos.qtyPerTp <= 0 || !pos.tps || pos.tps.length === 0) continue;

        const liveSize  = parseFloat(live.size);
        const tpsHitNow = Math.min(pos.tps.length, Math.round((pos.qty - liveSize) / pos.qtyPerTp));

        if (tpsHitNow > pos.tpsHit) {
          let newSl = null;
          if (tpsHitNow === 2) newSl = pos.entry;
          else if (tpsHitNow === 3) newSl = pos.tps[0];
          else if (tpsHitNow === 4) newSl = pos.tps[1];
          else if (tpsHitNow >= 5)  newSl = pos.tps[2];

          if (newSl && newSl !== pos.currentSl) {
            try { await updateStopLoss(sym, newSl); pos.currentSl = newSl; } catch(e) {}
          }
          pos.tpsHit = tpsHitNow;
          positions[sym] = pos;
          positionsChanged = true;
        }
      }
    }

    // ── WRITE KV — only what changed, in one batch ───────────────
    const writes = [];
    if (positionsChanged)         writes.push(kvSet(env, 'positions', positions));
    if (offsetChanged)            writes.push(kvSet(env, 'tg_offset', lastId));
    // Save last_run only every 5 minutes (saves ~1,400 writes/day → ~288/day)
    if (now - lastRunTs > 5 * 60 * 1000) writes.push(kvSet(env, 'last_run', now));
    if (errorsToAdd.length > 0) {
      const errors = await kvGet(env, 'errors') || [];
      errorsToAdd.forEach(e => errors.unshift(e));
      writes.push(kvSet(env, 'errors', errors.slice(0, 20)));
    }
    await Promise.all(writes);

  } catch(e) {
    // Log cron error — but only write if it's a new error
    try {
      const errors = await kvGet(env, 'errors') || [];
      if (!errors[0] || errors[0].msg !== e.message) {
        errors.unshift({ msg: 'Cron: ' + e.message, sym: 'CRON', ts: Date.now() });
        await kvSet(env, 'errors', errors.slice(0, 20));
      }
    } catch(e2) {}
  }
}

// ── MAIN HANDLER ─────────────────────────────────────────────────
export default {
  // Scheduled cron trigger — runs every minute
  async scheduled(event, env, ctx) {
    ctx.waitUntil(runCron(env));
  },

  async fetch(request, env) {
    if (request.method === 'OPTIONS') return new Response(null, { headers: CORS });

    const url  = new URL(request.url);
    const path = url.pathname;
    let body   = {};
    if (request.method === 'POST') {
      try { body = await request.json(); } catch {}
    }

    // GET /poll
    if (path === '/poll') {
      const offset = parseInt(url.searchParams.get('offset') || '0');
      try {
        const d = await getTgUpdates(offset);
        if (!d.ok) return err('Telegram error: ' + d.description);
        const signals = [];
        let lastId = offset;
        for (const upd of d.result) {
          lastId = upd.update_id;
          const msg  = upd.message || upd.channel_post;
          if (!msg) continue;
          const chatId = msg.chat.id.toString();
          const text   = msg.text || msg.caption || '';

          // Only accept from our configured channel/group
          // Debug confirmed chatId = -1003810685872 which matches TG_CHAT_ID
          if (chatId !== TG_CHAT_ID) continue;

          // Must match signal pattern
          if (!text.match(/#\w+.*?(SELL|BUY|SHORT|LONG)/i)) continue;

          const sig = parseSignal(text);
          if (sig) signals.push({ updateId: upd.update_id, raw: text, signal: sig, chatId });
        }
        return json({ ok: true, lastId, signals, total: d.result.length });
      } catch(e) { return err('Poll failed: ' + e.message); }
    }

    // POST /execute
    if (path === '/execute' && request.method === 'POST') {
      const { signal, riskPct = 5, leverage = 21.5, marginMode = 'ISOLATED' } = body;
      if (!signal) return err('No signal provided');
      try {
        const result = await executeTrade(signal, riskPct, leverage, marginMode);
        // Save to KV so cron knows about this position
        try {
          const positions = await kvGet(env, 'positions') || {};
          positions[signal.symbol] = {
            side:       signal.side,
            entry:      signal.entryMid,
            tps:        signal.tps,
            sl:         signal.sl,
            currentSl:  signal.sl,
            qty:        result.totalQty,
            qtyPerTp:   result.qtyPerTp,
            tpsHit:     0,
            ts:         Date.now(),
          };
          await kvSet(env, 'positions', positions);
        } catch(e) { /* non-critical */ }
        return json({ ok: true, ...result });
      } catch(e) { return err('Execution failed: ' + e.message); }
    }

    // POST /update-sl
    if (path === '/update-sl' && request.method === 'POST') {
      const { symbol, newSl } = body;
      if (!symbol || !newSl) return err('symbol and newSl required');
      try { await updateStopLoss(symbol, newSl); return json({ ok: true }); }
      catch(e) { return err('SL update failed: ' + e.message); }
    }

    // POST /close
    if (path === '/close' && request.method === 'POST') {
      const { symbol, qty, side } = body;
      if (!symbol || !qty || !side) return err('symbol, qty, side required');
      try {
        const result = await closePosition(symbol, qty, side);
        // Remove from KV positions
        try {
          const positions = await kvGet(env, 'positions') || {};
          delete positions[symbol];
          await kvSet(env, 'positions', positions);
        } catch(e) { /* non-critical */ }
        return json({ ok: true, result });
      }
      catch(e) { return err('Close failed: ' + e.message); }
    }

    // GET /balance
    if (path === '/balance') {
      try { const balance = await getAvailableBalance(); return json({ ok: true, balance }); }
      catch(e) { return err('Balance failed: ' + e.message); }
    }

    // GET /bot-status → dashboard reads cron state
    if (path === '/bot-status') {
      try {
        const lastRun   = await kvGet(env, 'last_run');
        const offset    = await kvGet(env, 'tg_offset') || 0;
        const positions = await kvGet(env, 'positions') || {};
        const errors    = await kvGet(env, 'errors') || [];
        const settings  = await kvGet(env, 'settings') || {};
        return json({ ok: true, lastRun, offset, positions, errors, settings });
      } catch(e) { return err('Status failed: ' + e.message); }
    }

    // POST /bot-settings → dashboard saves settings to KV
    if (path === '/bot-settings' && request.method === 'POST') {
      try {
        const { marginAmount, leverage, marginMode, autoExec, trailSl } = body;
        const settings = { marginAmount, leverage, marginMode, autoExec, trailSl };
        await kvSet(env, 'settings', settings);
        return json({ ok: true, settings });
      } catch(e) { return err('Settings save failed: ' + e.message); }
    }

    // GET /bot-offset → get current saved offset
    if (path === '/bot-offset') {
      try {
        const offset = await kvGet(env, 'tg_offset') || 0;
        return json({ ok: true, offset });
      } catch(e) { return err('Offset failed: ' + e.message); }
    }

    // GET /positions
    if (path === '/positions') {
      try {
        const r = await bbGet('/v5/position/list', { category: 'linear', settleCoin: 'USDT' });
        if (r.retCode !== 0) return err(r.retMsg);
        const open = (r.result?.list || []).filter(p => parseFloat(p.size) > 0);
        return json({ ok: true, positions: open });
      } catch(e) { return err('Positions failed: ' + e.message); }
    }

    // GET /orders?symbol=BTC → get open TP orders for a symbol
    if (path === '/orders') {
      const symbol = url.searchParams.get('symbol');
      if (!symbol) return err('symbol required');
      try {
        const r = await bbGet('/v5/order/realtime', {
          category: 'linear',
          symbol: symbol + 'USDT',
          limit: 20,
        });
        if (r.retCode !== 0) return err(r.retMsg);
        const orders = (r.result?.list || []).filter(o =>
          o.reduceOnly && o.orderType === 'Limit'
        ).map(o => ({
          orderId: o.orderId,
          price:   parseFloat(o.price),
          qty:     parseFloat(o.qty),
          side:    o.side,
          status:  o.orderStatus,
        })).sort((a,b) => a.price - b.price);
        return json({ ok: true, orders });
      } catch(e) { return err('Orders failed: ' + e.message); }
    }

    // GET /clear-webhook
    if (path === '/clear-webhook') {
      try {
        const r = await fetch(`${TG_BASE}/deleteWebhook?drop_pending_updates=false`);
        const d = await r.json();
        return json({ ok: true, telegram: d });
      } catch(e) { return err('Clear webhook failed: ' + e.message); }
    }

    // GET /latest-update-id → drains ALL pending updates, returns true latest ID
    if (path === '/latest-update-id') {
      try {
        let lastId = 0;
        let offset = 0;
        // Paginate through ALL pending updates (100 at a time) to find true latest
        for (let i = 0; i < 20; i++) {
          const d = await getTgUpdates(offset);
          if (!d.ok || !d.result || d.result.length === 0) break;
          lastId = d.result[d.result.length - 1].update_id;
          if (d.result.length < 100) break; // less than 100 = reached the end
          offset = lastId + 1;
        }
        // Acknowledge all up to lastId — Telegram won't return them again
        if (lastId > 0) await getTgUpdates(lastId + 1);
        return json({ ok: true, lastId });
      } catch(e) { return err('Latest ID failed: ' + e.message); }
    }

    // GET /debug
    if (path === '/debug') {
      try {
        const d = await getTgUpdates(0);
        if (!d.ok) return err('Telegram error: ' + d.description);
        const last5 = d.result.slice(-5).map(u => ({
          id: u.update_id,
          chatId: (u.message || u.channel_post)?.chat?.id,
          chatType: (u.message || u.channel_post)?.chat?.type,
          text: ((u.message || u.channel_post)?.text || '').slice(0, 100),
        }));
        return json({ ok: true, updates: last5, total: d.result.length });
      } catch(e) { return err('Debug failed: ' + e.message); }
    }
    return err('Unknown endpoint', 404);
  }
};
