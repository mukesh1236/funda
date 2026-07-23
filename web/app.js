const API = ''; // same origin

// Catch all uncaught JS errors and show them visibly so we can diagnose
window.onerror = (msg, src, line, col, err) => {
  const d = document.getElementById('content') || document.body;
  d.innerHTML = `<div style="color:#f87171;background:#1e293b;padding:20px;border-radius:8px;margin:20px;font-family:monospace">
    <b>JS Error (line ${line}):</b> ${msg}<br><pre>${err?.stack || ''}</pre></div>`;
};
window.onunhandledrejection = (e) => {
  const d = document.getElementById('content') || document.body;
  d.innerHTML = `<div style="color:#f87171;background:#1e293b;padding:20px;border-radius:8px;margin:20px;font-family:monospace">
    <b>Unhandled Promise Error:</b> ${e.reason?.message || e.reason}<br><pre>${e.reason?.stack || ''}</pre></div>`;
};

const $ = (s) => document.querySelector(s);
const el = (html) => { const t = document.createElement('template'); t.innerHTML = html.trim(); return t.content.firstChild; };
const esc = (s) => String(s == null ? '' : s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

let view = 'feed';
const detailCache = {};
let _chatSymbol = null;   // stock the user last expanded — gives the bot focus
let _feedStocks = [];     // last fetched feed stocks — used for client-side sort
let _feedSort = { col: null, dir: 1 }; // col: return key or 'consensus_score', dir: 1=desc -1=asc

// Thrown on 401 so callers can decide whether to prompt for login. Public
// views ignore it; watchlist actions catch it and open the auth overlay.
class AuthError extends Error { constructor() { super('Not authenticated'); this.auth = true; } }

async function getJSON(path) {
  const res = await fetch(API + path, { cache: 'no-store' });
  if (res.status === 401) throw new AuthError();
  if (!res.ok) throw new Error(`${path} → ${res.status}`);
  return res.json();
}
async function postJSON(path, body) {
  const opts = { method: 'POST' };
  if (body !== undefined) {
    opts.headers = { 'Content-Type': 'application/json' };
    opts.body = JSON.stringify(body);
  }
  const res = await fetch(API + path, opts);
  if (res.status === 401) throw new AuthError();
  if (!res.ok) {
    let detail = `${path} → ${res.status}`;
    try { const j = await res.json(); if (j.detail) detail = j.detail; } catch (e) {}
    throw new Error(detail);
  }
  return res.json();
}

function scoreBadge(n) {
  const cls = n > 0 ? 'score-pos' : n < 0 ? 'score-neg' : 'score-zero';
  return `<span class="badge ${cls}">${n > 0 ? '+' : ''}${n}</span>`;
}
function countsCell(s) {
  // Buy-vs-sell strength meter under the counts: proportional fill with a
  // 2px surface gap between the two segments (dataviz spacer rule).
  const total = s.buy_count + s.sell_count;
  const meter = total > 0 ? `
    <span class="meter" title="${s.buy_count} buy vs ${s.sell_count} sell">
      <span class="m-buy" style="width:${(s.buy_count / total * 100).toFixed(1)}%"></span>
      <span class="m-gap"></span>
      <span class="m-sell" style="width:${(s.sell_count / total * 100).toFixed(1)}%"></span>
    </span>` : '';
  return `<span class="counts">
    <span class="pill b">${s.buy_count} B</span>
    <span class="pill h">${s.hold_count} H</span>
    <span class="pill s">${s.sell_count} S</span>${meter}</span>`;
}
function ret(v) {
  if (v == null) return '<span class="muted">—</span>';
  return `<span class="${v >= 0 ? 'r-pos' : 'r-neg'}">${v >= 0 ? '+' : ''}${v}%</span>`;
}
function confBadge(c) {
  if (!c) return '<span class="muted">—</span>';
  const cls = { High: 'cf-high', Medium: 'cf-med', Low: 'cf-low' }[c.label] || 'cf-med';
  return `<span class="conf ${cls}" title="${esc(c.rationale)}">${c.label} ${Math.round(c.score)}</span>`;
}
const _AVATAR_HUES = [212, 158, 32, 265, 130, 350, 20, 190];   // deterministic per ticker
function tickAvatar(sym) {
  let h = 0;
  for (const ch of sym) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
  const hue = _AVATAR_HUES[h % _AVATAR_HUES.length];
  return `<span class="tick-avatar" style="background:hsl(${hue} 55% 38%)">${esc(sym.slice(0, 3))}</span>`;
}
function stockCell(s) {
  return `<span class="caret">▶</span>${tickAvatar(s.symbol)}<span class="tick">${esc(s.symbol)}</span>
    <span class="name">${esc(s.company_name || '')}</span> ${scoreBadge(s.consensus_score)}`;
}
function statusChip(o) {
  if (!o || !o.status) return '<span class="muted">—</span>';
  const map = { hit: 'st-hit', missed: 'st-missed', pending: 'st-pending', expired: 'st-expired' };
  const pct = (o.pct_to_target != null) ? ` (${o.pct_to_target > 0 ? '+' : ''}${o.pct_to_target}%)` : '';
  return `<span class="status-chip ${map[o.status] || 'st-pending'}">${o.status}${pct}</span>`;
}

function themeTags(themes) {
  if (!themes || !themes.length) return '<span class="muted">—</span>';
  return `<span class="themes">${themes.map(t => `<span class="theme-tag">${esc(t)}</span>`).join('')}</span>`;
}

function currentMarket() { return $('#market').value || 'us'; }

async function loadThemes() {
  try {
    const data = await getJSON(`/api/themes?market=${currentMarket()}`);
    const sel = $('#theme');
    sel.innerHTML = '<option value="">All segments</option>';
    data.themes.forEach(t =>
      sel.appendChild(el(`<option value="${esc(t.name)}">${esc(t.name)} (${t.ticker_count})</option>`)));
  } catch (e) { /* best-effort */ }
}

async function loadStats() {
  try {
    const h = await getJSON('/api/health');
    $('#stats').innerHTML = '';
    const lastUpd = h.last_updated || 'never';
    const sched = h.scheduler ? `on · ${h.daily_run_time || ''}` : 'off';
    const boxes = [
      ['Stocks tracked', h.universe_size, 'Number of stocks we collect analyst ratings for'],
      ['Data sources', (h.sources || []).length, `Active sources: ${(h.sources || []).join(', ') || 'none'}`],
      ['Auto-refresh', sched, h.scheduler ? `Runs daily at ${h.daily_run_time} server time` : 'Daily job is off'],
      ['Last updated', lastUpd, 'Date the daily collect + validation job last ran'],
    ];
    boxes.forEach(([l, v, tip]) =>
      $('#stats').appendChild(el(
        `<div class="stat" title="${esc(tip)}"><div class="v">${esc(String(v))}</div><div class="l">${esc(l)}</div></div>`)));
  } catch (e) { /* best-effort */ }
}

function renderHighlights(h) {
  const box = $('#highlights');
  if (!h || (!h.top_buzzed?.length && !h.top_buy && !h.top_sell && !h.top_movers?.length)) { box.innerHTML = ''; return; }
  const card = (cls, label, s, meta) => s ? `
    <div class="hl ${cls}">
      <div class="label">${label}</div>
      <div class="sym">${s.symbol} ${scoreBadge(s.consensus_score)}</div>
      <div class="meta">${meta}</div>
    </div>` : '';

  const fmtPct = (p) => p >= 0 ? `+${p.toFixed(2)}%` : `${p.toFixed(2)}%`;
  const newsLink = (sym) =>
    `<a class="why-link" href="https://finance.yahoo.com/quote/${encodeURIComponent(sym)}/news" target="_blank" rel="noopener" title="See news for ${sym}">📰 why?</a>`;

  // Today's analyst catalysts — the "why" behind moves
  const catalysts = (h.today_catalysts || []).length ? `
    <div class="hl catalysts">
      <div class="label">📣 Today's analyst calls <span class="hl-hint">click ticker for analyst view</span></div>
      <ol class="buzzlist catalyst-list">${h.today_catalysts.map(c => {
        const pct = c.day_change_pct != null
          ? `<span class="${c.day_change_pct >= 0 ? 'pct-up' : 'pct-down'}">${fmtPct(c.day_change_pct)}</span>`
          : '';
        const firm = c.firm ? `<span class="muted">· ${esc(c.firm)}</span>` : '';
        const tgt  = c.target_price ? `<span class="muted">PT $${c.target_price}</span>` : '';
        const act  = `<span class="act-badge">${esc(c.action.toUpperCase())}</span>`;
        return `<li>
          <button class="sym-link" onclick="openSymbol('${esc(c.symbol)}')">${esc(c.symbol)}</button>
          ${act} ${firm} ${tgt} ${pct} ${newsLink(c.symbol)}
        </li>`;
      }).join('')}</ol>
    </div>` : '';

  const movers = (h.top_movers || []).length ? `
    <div class="hl movers">
      <div class="label">🚀 Today's movers <span class="hl-hint">click ticker for analyst view</span></div>
      <ol class="buzzlist">${h.top_movers.map(s => {
        const pct = s.day_change_pct != null ? `<span class="pct-up">${fmtPct(s.day_change_pct)}</span>` : '';
        return `<li>
          <button class="sym-link" onclick="openSymbol('${esc(s.symbol)}')">${esc(s.symbol)}</button>
          ${pct} ${scoreBadge(s.consensus_score)} ${newsLink(s.symbol)}
        </li>`;
      }).join('')}</ol>
    </div>` : '';

  const buzz = (h.top_buzzed || []).length ? `
    <div class="hl buzz">
      <div class="label">🔥 Most analyst coverage</div>
      <ol class="buzzlist">${h.top_buzzed.map(s =>
        `<li><b>${esc(s.symbol)}</b> <span class="muted">${s.total_count} analysts</span> ${scoreBadge(s.consensus_score)}</li>`).join('')}</ol>
    </div>` : '';

  box.innerHTML = catalysts + movers + buzz +
    card('buy', '⬆ Strongest buy', h.top_buy,
      h.top_buy ? `${h.top_buy.buy_count} buys${h.top_buy.avg_target ? ' · target $' + h.top_buy.avg_target : ''}` : '') +
    card('sell', '⬇ Strongest sell', h.top_sell,
      h.top_sell ? `${h.top_sell.sell_count} sells vs ${h.top_sell.buy_count} buys` : '');
}

function ownCell(o) {
  if (!o || (o.inst_pct == null && !o.top_buyer)) return '<span class="muted">—</span>';
  const inst = o.inst_pct != null ? `${Math.round(o.inst_pct)}% inst` : '';
  const funds = o.fund_holders ? `${o.fund_holders} funds` : '';
  const top = [inst, funds].filter(Boolean).join(' · ');
  const name = o.top_buyer ? (o.top_buyer.length > 18 ? o.top_buyer.slice(0, 17) + '…' : o.top_buyer) : '';
  const buyer = name ? `<div class="buyer" title="recently increased its stake">↑ ${esc(name)}</div>` : '';
  return `<div class="own">${top}${buyer}</div>`;
}

let _lastFeedTime = null;   // Date of last successful feed fetch
let _autoRefreshTimer = null;

function _isMarketOpen(market) {
  const now = new Date();
  const day = now.getUTCDay(); // 0=Sun, 6=Sat
  if (day === 0 || day === 6) return false;
  if (market === 'in') {
    // IST = UTC+5:30
    const h = now.getUTCHours(), m = now.getUTCMinutes();
    const mins = (h * 60 + m + 330) % (24 * 60); // +330 = +5h30
    return mins >= 9 * 60 + 15 && mins < 15 * 60 + 30;
  }
  // US ET ≈ UTC-4 (summer) / UTC-5 (winter). Use UTC-4 as approximation.
  const etMins = (now.getUTCHours() * 60 + now.getUTCMinutes() - 240 + 1440) % 1440;
  return etMins >= 9 * 60 + 30 && etMins < 16 * 60;
}

function _scheduleAutoRefresh() {
  if (_autoRefreshTimer) clearTimeout(_autoRefreshTimer);
  const market = currentMarket();
  // 15 min during market hours, 60 min outside
  const interval = _isMarketOpen(market) ? 15 * 60 * 1000 : 60 * 60 * 1000;
  _autoRefreshTimer = setTimeout(async () => {
    if (view === 'feed') await loadFeed().catch(() => {});
    _scheduleAutoRefresh();
  }, interval);
}

function _fmtTimestamp(d) {
  const date = d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
  const time = d.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' });
  return `${date}, ${time}`;
}

function _updateFeedTimestamp() {
  _lastFeedTime = new Date();
  const stamp = _fmtTimestamp(_lastFeedTime);
  const el = $('#feedUpdated');
  if (el) el.textContent = `Updated ${stamp}`;
  if (window._feedTick) clearInterval(window._feedTick);
  window._feedTick = setInterval(() => {
    if (!_lastFeedTime) return;
    const el = $('#feedUpdated');
    if (!el) return;
    const mins = Math.round((Date.now() - _lastFeedTime) / 60000);
    const rel = mins < 1 ? 'just now' : `${mins} min ago`;
    el.textContent = `Updated ${stamp} · ${rel}`;
  }, 60000);
}

const _SORT_COLS = {
  consensus: s => s.consensus_score,
  ret1m:  s => s.returns?.one_month   ?? -Infinity,
  ret3m:  s => s.returns?.three_month ?? -Infinity,
  ret6m:  s => s.returns?.six_month   ?? -Infinity,
  ret12m: s => s.returns?.twelve_month ?? -Infinity,
};

function _sortArrow(col) {
  if (_feedSort.col !== col) return `<span class="sort-arrow">↕</span>`;
  return _feedSort.dir === 1
    ? `<span class="sort-arrow active">↓</span>`
    : `<span class="sort-arrow active">↑</span>`;
}

function _sortedStocks() {
  if (!_feedSort.col || !_SORT_COLS[_feedSort.col]) return _feedStocks;
  const key = _SORT_COLS[_feedSort.col];
  return [..._feedStocks].sort((a, b) => _feedSort.dir * (key(b) - key(a)));
}

function _renderFeedRows() {
  const sorted = _sortedStocks();
  const r = s => s.returns || {};
  const rows = sorted.map(s => `
    <tr class="row" data-sym="${s.symbol}">
      <td class="stockcell">${stockCell(s)}</td>
      <td>${countsCell(s)}</td>
      <td>${confBadge(s.confidence)}</td>
      <td>${s.avg_target != null ? '$' + s.avg_target : '<span class="muted">—</span>'}</td>
      <td>${ret(r(s).one_month)}</td>
      <td>${ret(r(s).three_month)}</td>
      <td>${ret(r(s).six_month)}</td>
      <td>${ret(r(s).twelve_month)}</td>
      <td>${ownCell(s.ownership)}</td>
      <td>${statusChip(s.outcome)}</td>
      <td>${themeTags(s.themes)}</td>
    </tr>
    <tr class="expand" data-for="${s.symbol}" style="display:none"><td colspan="11"><div class="expand-inner" data-body="${s.symbol}"></div></td></tr>`).join('');

  const th = (col, label) =>
    `<th class="sortable${_feedSort.col === col ? ' sorted' : ''}" data-scol="${col}">${label} ${_sortArrow(col)}</th>`;

  $('#content').innerHTML = `
    <table><thead><tr>
      <th>Stock</th>
      ${th('consensus','Consensus')}
      <th>Confidence</th><th>Avg target</th>
      ${th('ret1m','1M')}${th('ret3m','3M')}${th('ret6m','6M')}${th('ret12m','12M')}
      <th>Big investors / funds</th><th>Target status</th><th>Segments</th>
    </tr></thead><tbody>${rows}</tbody></table>`;

  $('#content').querySelectorAll('th.sortable').forEach(th =>
    th.addEventListener('click', () => {
      const col = th.dataset.scol;
      if (_feedSort.col === col) {
        _feedSort.dir = _feedSort.dir === 1 ? -1 : 1; // toggle direction
      } else {
        _feedSort = { col, dir: 1 }; // new col → start descending
      }
      _renderFeedRows();
    }));
  $('#content').querySelectorAll('tr.row').forEach(tr =>
    tr.addEventListener('click', () => toggleExpand(tr)));
}

async function loadFeed() {
  const days = $('#days').value;
  const theme = $('#theme').value;
  const market = currentMarket();
  $('#status').textContent = 'Loading feed…';
  const data = await getJSON(`/api/recommendations/feed?days=${days}&market=${market}${theme ? '&theme=' + encodeURIComponent(theme) : ''}`);
  renderHighlights(data.highlights);
  _updateFeedTimestamp();
  _scheduleAutoRefresh();
  _feedStocks = data.stocks;
  _feedSort = { col: null, dir: 1 }; // reset sort on fresh load
  $('#status').textContent = `${data.stocks.length} stocks · click a row to see which analysts and why`;
  if (!data.stocks.length) {
    $('#content').innerHTML = `<div class="empty">No recommendations yet.<br/>Click "Refresh now" to fetch today's analyst calls.</div>`;
    return;
  }
  _renderFeedRows();
}

async function openSymbol(sym) {
  // Switch to feed, ensure it's loaded, then open the detail panel for sym.
  if (view !== 'feed') { view = 'feed'; await loadFeed().catch(() => {}); }
  const tr = document.querySelector(`tr.row[data-sym="${sym}"]`);
  if (!tr) { return showStockOverview(sym); }   // not tracked → generic stock page
  tr.scrollIntoView({ behavior: 'smooth', block: 'center' });
  const exp = document.querySelector(`tr.expand[data-for="${sym}"]`);
  if (exp && exp.style.display === 'none') await toggleExpand(tr);
}

async function showStockOverview(sym) {
  // Generic finance-site page for ANY ticker — shown when a global-search hit
  // isn't in the tracked analyst universe, instead of a silent dead end.
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  $('#highlights').innerHTML = '';
  $('#status').textContent = '';
  $('#content').innerHTML = `<div class="loading">Loading ${esc(sym)}…</div>`;
  _chatSymbol = sym;
  try {
    const d = await getJSON('/api/stocks/' + encodeURIComponent(sym));
    const r = d.returns || {};
    const retChip = (label, v) => v == null ? '' :
      `<span class="ov-ret"><span class="muted">${label}</span> ${ret(v)}</span>`;
    const news = (d.news || []).slice(0, 6).map(n => `
      <div class="news">${n.url ? `<a href="${esc(n.url)}" target="_blank" rel="noopener">${esc(n.title)}</a>`
                                  : esc(n.title)}
        ${n.source ? `<span class="src"> — ${esc(n.source)}</span>` : ''}</div>`).join('');
    const trades = (d.insider_trades || []).slice(0, 5).map(t => `
      <div class="news">${esc(t.insider)}${t.role ? ` <span class="src">(${esc(t.role)})</span>` : ''}
        <span class="${t.action === 'Buy' ? 'r-pos' : 'r-neg'}">${esc(t.action)}</span>
        ${t.shares ? esc(String(t.shares)) + ' sh' : ''} <span class="src">${esc(t.date || '')}</span></div>`).join('');
    $('#content').innerHTML = `
      <div class="stock-overview">
        <button class="ghost-btn ov-back" id="ovBack">← Back to feed</button>
        <div class="ov-head">
          ${tickAvatar(d.symbol)}
          <div>
            <div class="ov-name">${esc(d.company_name || d.symbol)}</div>
            <div class="muted">${esc(d.symbol)}
              ${d.fundamentals && d.fundamentals.sector ? ' · ' + esc(d.fundamentals.sector) : ''}
              ${d.fundamentals && d.fundamentals.industry ? ' · ' + esc(d.fundamentals.industry) : ''}</div>
          </div>
          <div class="ov-price">${d.price != null ? '$' + d.price : ''}</div>
        </div>
        <div class="ov-rets">
          ${retChip('1M', r.one_month)}${retChip('3M', r.three_month)}
          ${retChip('6M', r.six_month)}${retChip('1Y', r.twelve_month)}
        </div>
        ${d.tracked ? '' : `<p class="sre-note">ℹ ${esc(d.symbol)} isn't in the tracked analyst universe, so
          there's no buy/sell consensus here — this is its general profile. You can still add it
          to your watchlist, and ask the AI about it.</p>`}
        ${d.fundamentals ? renderFundamentals(d.fundamentals) : ''}
        ${d.ownership ? renderOwnership(d.ownership) : ''}
        ${news ? `<div class="ovsec"><h4>Recent news</h4>${news}</div>` : ''}
        ${trades ? `<div class="ovsec"><h4>Insider activity</h4>${trades}</div>` : ''}
      </div>`;
    const back = document.getElementById('ovBack');
    if (back) back.addEventListener('click', () => { view = 'feed'; render(); });
  } catch (e) {
    $('#content').innerHTML = `<div class="empty">Could not load ${esc(sym)}: ${esc(e.message)}</div>`;
  }
}

async function toggleExpand(tr) {
  const sym = tr.dataset.sym;
  const exp = $(`tr.expand[data-for="${sym}"]`);
  const open = exp.style.display !== 'none';
  if (open) { exp.style.display = 'none'; tr.classList.remove('open'); return; }
  tr.classList.add('open');
  exp.style.display = '';
  _chatSymbol = sym;   // focus the chat bot on the stock just opened
  const body = exp.querySelector('.expand-inner');
  if (body.dataset.loaded) return;
  body.innerHTML = `<div class="loading">Loading analysts…</div>`;
  try {
    const d = detailCache[sym] || (detailCache[sym] = await getJSON(`/api/recommendations/${sym}`));
    body.innerHTML = renderDetail(d);
    body.dataset.loaded = '1';
  } catch (e) {
    body.innerHTML = `<div class="loading">Could not load: ${esc(e.message)}</div>`;
  }
}

function renderSummary(sm) {
  if (!sm) return '';
  const reasons = (sm.reasons || []).map(r => `<li>${esc(r)}</li>`).join('');
  const narrative = sm.narrative ? `<p class="sm-narr">${esc(sm.narrative)}</p>` : '';
  return `<div class="summary">
    <h4>Why analysts recommend it</h4>
    <div class="sm-head">${esc(sm.headline)}</div>
    ${narrative}
    <ul class="sm-reasons">${reasons}</ul>
  </div>`;
}

function renderOwnership(o) {
  if (!o || (o.inst_pct == null && !o.funds?.length && !o.recent_buyers?.length)) return '';
  const head = `Institutions hold ${o.inst_pct != null ? o.inst_pct + '%' : '—'} of the company`
    + (o.insider_pct != null ? `, insiders ${o.insider_pct}%` : '') + '.';
  const row = h => `<div class="analyst">
    <span class="firm">${esc(h.holder)}</span>
    <span class="note">${h.pct_held != null ? h.pct_held + '% of company' : ''}</span>
    <span class="tgt">${h.change_pct != null ? (h.change_pct >= 0 ? '+' : '') + h.change_pct + '%' : ''} ${esc(h.date || '')}</span></div>`;
  const buyers = (o.recent_buyers || []).length
    ? `<h5>Recently increased their stake</h5>${o.recent_buyers.map(row).join('')}` : '';
  const funds = (o.funds || []).length
    ? `<h5>Top fund / ETF holders</h5>${o.funds.map(row).join('')}` : '';
  return `<div class="ownsec">
    <h4>Big investors &amp; funds</h4>
    <p class="muted">${head} <em>% shown is each holder's share of the company — not the stock's weight inside the fund.</em></p>
    ${buyers}${funds}</div>`;
}

function fmtCap(n) {
  if (n == null) return '—';
  if (n >= 1e12) return '$' + (n / 1e12).toFixed(2) + 'T';
  if (n >= 1e9) return '$' + (n / 1e9).toFixed(2) + 'B';
  if (n >= 1e6) return '$' + (n / 1e6).toFixed(0) + 'M';
  return '$' + n;
}

function renderFundamentals(f) {
  if (!f) return '';
  const stat = (label, v) => `<div class="fund-stat"><div class="fl">${label}</div><div class="fv">${v == null ? '<span class="muted">—</span>' : v}</div></div>`;
  const grid = [
    stat('P/E', f.pe_ratio), stat('Forward P/E', f.forward_pe), stat('PEG', f.peg_ratio),
    stat('EPS', f.eps != null ? '$' + f.eps : null), stat('Market cap', fmtCap(f.market_cap)),
    stat('Rev. growth', f.revenue_growth != null ? f.revenue_growth + '%' : null),
    stat('Profit margin', f.profit_margin != null ? f.profit_margin + '%' : null),
    stat('ROE', f.roe != null ? f.roe + '%' : null),
    stat('Debt/Equity', f.debt_to_equity), stat('Dividend yield', f.dividend_yield != null ? f.dividend_yield + '%' : null),
    stat('Beta', f.beta), stat('Price/Book', f.price_to_book),
    stat('52w range', (f.week52_low != null && f.week52_high != null) ? `$${f.week52_low}–$${f.week52_high}` : null),
  ].join('');
  const notes = (f.notes || []).map(n => `<li>${esc(n)}</li>`).join('');
  const sector = f.sector || f.industry ? `<p class="muted">${esc([f.sector, f.industry].filter(Boolean).join(' · '))}</p>` : '';
  return `<div class="fundsec">
    <h4>📊 Stock Fundamentals</h4>
    ${sector}
    <div class="fund-grid">${grid}</div>
    ${notes ? `<ul class="sm-reasons">${notes}</ul>` : ''}
  </div>`;
}

function renderDetail(d) {
  const named = d.recommendations.filter(r => r.firm);
  const analysts = named.length ? named.map(r => `
    <div class="analyst">
      <span class="firm">${esc(r.firm)}</span>
      <span class="pill ${r.action[0]}">${r.action}</span>
      <span class="note">${esc(r.note || r.source)}</span>
      <span class="tgt">${r.target_price != null ? 'PT $' + r.target_price : ''} ${esc(r.entry_date || '')}</span>
    </div>`).join('')
    : `<div class="muted">No named-analyst detail available — counts come from aggregate sources (${esc(d.consensus.sources.join(', '))}).</div>`;

  const news = (d.news || []).length ? `
    <div class="news"><h4>Recent news / context</h4><ul>
      ${d.news.map(n => `<li><a href="${esc(n.url || '#')}" target="_blank" rel="noopener">${esc(n.title)}</a> <span class="src">${esc(n.publisher || '')}</span></li>`).join('')}
    </ul></div>` : '';

  return `${renderSummary(d.summary)}${renderFundamentals(d.fundamentals)}${renderOwnership(d.ownership)}<h4>Which analysts recommended ${esc(d.symbol)} (${named.length})</h4>${analysts}${news}`;
}

async function loadLeaderboard() {
  $('#highlights').innerHTML = '';
  $('#status').textContent = 'Loading leaderboard…';
  const data = await getJSON(`/api/recommendations/leaderboard?metric=consensus&limit=50&market=${currentMarket()}`);
  $('#status').textContent = `Ranked by ${data.metric}`;
  if (!data.entries.length) { $('#content').innerHTML = `<div class="empty">Nothing ranked yet.</div>`; return; }
  const rows = data.entries.map((e, i) => `
    <tr class="row" data-sym="${e.symbol}">
      <td class="muted">#${i + 1}</td>
      <td><span class="caret">▶</span> <span class="sym">${e.symbol}</span></td>
      <td>${scoreBadge(e.consensus_score)}</td>
      <td>${e.total_count}</td>
      <td>${e.hit_rate != null ? (e.hit_rate * 100).toFixed(0) + '%' : '<span class="muted">—</span>'}</td>
      <td class="muted">${e.resolved_count}</td>
    </tr>
    <tr class="expand" data-for="${e.symbol}" style="display:none"><td colspan="6"><div class="expand-inner" data-body="${e.symbol}"></div></td></tr>`).join('');
  $('#content').innerHTML = `
    <table><thead><tr>
      <th>Rank</th><th>Stock</th><th>Score</th><th>Analysts</th><th>Hit rate</th><th>Resolved</th>
    </tr></thead><tbody>${rows}</tbody></table>`;
  $('#content').querySelectorAll('tr.row').forEach(tr =>
    tr.addEventListener('click', () => toggleExpand(tr)));
}

function sparkline(daily) {
  const closes = (daily || []).map(d => d.close).filter(c => c != null);
  if (closes.length < 2) return '<span class="muted">—</span>';
  const w = 110, h = 28, min = Math.min(...closes), max = Math.max(...closes);
  const span = (max - min) || 1;
  const pts = closes.map((c, i) =>
    `${(i / (closes.length - 1) * w).toFixed(1)},${(h - (c - min) / span * h).toFixed(1)}`).join(' ');
  const up = closes[closes.length - 1] >= closes[0];
  const col = up ? 'var(--buy)' : 'var(--sell)';
  return `<svg class="spark" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}">
    <polyline points="${pts}" fill="none" stroke="${col}" stroke-width="1.5"/></svg>`;
}

let _wlSearchTimer = null;
let _wlSelectedSym = null; // symbol chosen from dropdown

async function loadWatchlist() {
  $('#highlights').innerHTML = '';
  if (!_currentUser) {
    $('#status').textContent = '';
    $('#content').innerHTML = `
      <div class="empty signin-gate">
        <p>★ Your watchlist is private to your account.</p>
        <p class="muted">Sign in (free) to pin stocks and track daily variation since the day you added them.</p>
        <button id="wlSignIn" class="auth-submit" style="max-width:240px;margin:14px auto 0">Sign in to start</button>
      </div>`;
    $('#wlSignIn').addEventListener('click', () => showAuth());
    return;
  }
  const market = currentMarket();
  const mLabel = market === 'in' ? '🇮🇳 India (NSE)' : '🇺🇸 US';
  $('#status').textContent = `${mLabel} watchlist — daily variation since the day you added them.`;
  let data;
  try {
    data = await getJSON(`/api/watchlist?market=${market}`);
  } catch (e) {
    if (e.auth) { _currentUser = null; updateAuthUI(); return loadWatchlist(); }
    throw e;
  }

  const ph = market === 'in'
    ? 'Search company or ticker e.g. HDFC Bank, Infosys, TCS…'
    : 'Search company or ticker e.g. Amazon, Apple, Nvidia…';
  const form = `
    <div class="wl-add">
      <div class="wl-search-wrap">
        <input id="wlSym" placeholder="${ph}" maxlength="60" autocomplete="off" />
        <div id="wlDropdown" class="wl-dropdown"></div>
      </div>
      <input id="wlGrp" placeholder="Group (optional)" />
      <button id="wlAdd">★ Pin to watchlist</button>
    </div>`;

  let body;
  if (!data.items.length) {
    const eg = market === 'in' ? 'HDFC Bank, Infosys, TCS' : 'Amazon, Apple, Nvidia';
    body = `<div class="empty">No ${mLabel} stocks pinned yet. Search a company name above to add one (e.g. ${eg}).</div>`;
  } else {
    const rows = data.items.map(it => `
      <tr>
        <td class="stockcell"><span class="name">${esc(it.company_name || it.symbol)}</span> <span class="tick">${esc(it.symbol)}</span></td>
        <td class="muted">${esc(it.group)}</td>
        <td class="muted">${esc(it.pin_date)}</td>
        <td title="Average analyst price target when pinned">${it.pin_price != null ? '$' + it.pin_price : '—'}</td>
        <td>${it.current_price != null ? '$' + it.current_price : '—'}</td>
        <td title="Current price vs the analyst target">${ret(it.change_since_pin_pct)}</td>
        <td>${ret(it.day_change_pct)}</td>
        <td>${sparkline(it.daily)}</td>
        <td><button class="wl-rm" data-sym="${esc(it.symbol)}" data-grp="${esc(it.group)}" title="Remove">✕</button></td>
      </tr>`).join('');
    body = `<table><thead><tr>
      <th>Stock</th><th>Group</th><th>Pinned on</th><th>Analyst target</th><th>Current</th>
      <th>Current vs target</th><th>Today</th><th>Trend</th><th></th>
    </tr></thead><tbody>${rows}</tbody></table>`;
  }
  $('#content').innerHTML = form + body;

  // Autocomplete search
  const inp = $('#wlSym');
  const drop = $('#wlDropdown');
  _wlSelectedSym = null;

  inp.addEventListener('input', () => {
    _wlSelectedSym = null;
    clearTimeout(_wlSearchTimer);
    const q = inp.value.trim();
    if (q.length < 2) { drop.innerHTML = ''; drop.classList.remove('open'); return; }
    _wlSearchTimer = setTimeout(async () => {
      try {
        const res = await getJSON(`/api/search?q=${encodeURIComponent(q)}&market=${currentMarket()}`);
        const hits = res.results || [];
        if (!hits.length) { drop.innerHTML = ''; drop.classList.remove('open'); return; }
        drop.innerHTML = hits.map(h =>
          `<div class="wl-hit" data-sym="${esc(h.symbol)}">
            <span class="wl-hit-sym">${esc(h.symbol)}</span>
            <span class="wl-hit-name">${esc(h.name)}</span>
            <span class="wl-hit-ex">${esc(h.exchange)}</span>
          </div>`).join('');
        drop.classList.add('open');
        drop.querySelectorAll('.wl-hit').forEach(d => {
          d.addEventListener('mousedown', e => {
            e.preventDefault();
            _wlSelectedSym = d.dataset.sym;
            inp.value = d.dataset.sym + ' — ' + d.querySelector('.wl-hit-name').textContent;
            drop.innerHTML = ''; drop.classList.remove('open');
          });
        });
      } catch (_) { drop.innerHTML = ''; drop.classList.remove('open'); }
    }, 300);
  });

  inp.addEventListener('blur', () => setTimeout(() => { drop.innerHTML = ''; drop.classList.remove('open'); }, 150));
  inp.addEventListener('keydown', e => { if (e.key === 'Enter') { drop.innerHTML = ''; drop.classList.remove('open'); addToWatchlist(); } });
  $('#wlAdd').addEventListener('click', addToWatchlist);
  $('#content').querySelectorAll('.wl-rm').forEach(b =>
    b.addEventListener('click', () => removeFromWatchlist(b.dataset.sym, b.dataset.grp)));
}

async function addToWatchlist() {
  // Use the symbol chosen from dropdown; fall back to raw input (uppercase).
  let symbol = _wlSelectedSym || ($('#wlSym').value || '').split(' — ')[0].trim().toUpperCase();
  const group = ($('#wlGrp').value || '').trim();
  if (!symbol) return;

  const market = currentMarket();
  // Auto-append .NS for India market if the user forgot the suffix.
  if (market === 'in' && !symbol.endsWith('.NS') && !symbol.endsWith('.BO')) {
    symbol = symbol + '.NS';
  }
  // Warn if they're trying to add an India ticker in US market view.
  if (market === 'us' && (symbol.endsWith('.NS') || symbol.endsWith('.BO'))) {
    $('#status').textContent = `Switch market to 🇮🇳 India to pin ${symbol}`;
    return;
  }

  $('#status').textContent = `Pinning ${symbol}…`;
  try {
    await postJSON('/api/watchlist', group ? { symbol, group } : { symbol });
    $('#status').textContent = '';
    $('#wlSym').value = '';
    _wlSelectedSym = null;
    loadWatchlist();
  } catch (e) {
    if (e.auth) { _currentUser = null; updateAuthUI(); showAuth(); return; }
    // Surface the server's message (e.g. "'XYZ' not found. Check the ticker…").
    $('#status').textContent = e.message;
  }
}

async function removeFromWatchlist(symbol, group) {
  try {
    const res = await fetch(`/api/watchlist/${encodeURIComponent(symbol)}?group=${encodeURIComponent(group)}`,
      { method: 'DELETE' });
    if (res.status === 401) { _currentUser = null; updateAuthUI(); showAuth(); return; }
    loadWatchlist();
  } catch (e) { $('#status').textContent = 'Remove failed: ' + e.message; }
}

async function loadDigest() {
  $('#highlights').innerHTML = '';
  const market = currentMarket();
  $('#status').textContent = 'Loading macro digest…';
  let data;
  try {
    data = await getJSON(`/api/market/digest?market=${market}`);
  } catch (e) {
    $('#status').textContent = 'Could not load digest: ' + e.message;
    $('#content').innerHTML = `<div class="empty">Digest unavailable — check the server logs.</div>`;
    return;
  }
  const srcLabel = market === 'in'
    ? 'Yahoo Finance, Economic Times, Moneycontrol, Business Standard'
    : 'Yahoo Finance, CNBC, MarketWatch';
  $('#status').textContent = `${data.headline_count} headlines · sources: ${srcLabel}`;

  const narrative = data.narrative
    ? `<div class="digest-narr"><h4>🤖 AI Briefing</h4><p>${esc(data.narrative)}</p></div>`
    : '';

  const items = (data.headlines || []).map(h => `
    <div class="digest-item">
      <a href="${esc(h.url || '#')}" target="_blank" rel="noopener" class="digest-title">${esc(h.title)}</a>
      <span class="digest-meta">${esc(h.source || '')}${h.published ? ' · ' + esc(String(h.published).slice(0, 16)) : ''}</span>
    </div>`).join('');

  $('#content').innerHTML = `
    <div class="digest-wrap">
      <div class="digest-header">
        <h3>📰 Today's Macro &amp; Market Digest</h3>
        <p class="muted">What Warren Buffett reads every morning — macro &amp; market commentary from top financial news sources.</p>
      </div>
      ${narrative}
      <div class="digest-list">${items || '<div class="empty">No headlines fetched yet — check your internet connection.</div>'}</div>
    </div>`;
}

async function loadAdmin() {
  $('#highlights').innerHTML = '';
  if (!_currentUser || _currentUser.role !== 'admin') {
    $('#status').textContent = '';
    $('#content').innerHTML = `<div class="empty">Admin access required.</div>`;
    return;
  }
  $('#status').textContent = 'Loading admin statistics…';
  let stats, users;
  try {
    [stats, users] = await Promise.all([
      getJSON('/api/admin/stats'),
      getJSON('/api/admin/users'),
    ]);
  } catch (e) {
    if (e.auth) { _currentUser = null; updateAuthUI(); showAuth(); return; }
    $('#status').textContent = 'Could not load admin stats: ' + e.message;
    return;
  }
  $('#status').textContent = 'Usage statistics · live from the database';

  const u = stats.users, en = stats.engagement, cov = stats.coverage, tr = stats.traffic;
  const card = (label, val, sub) =>
    `<div class="kpi"><div class="kpi-v">${val}</div><div class="kpi-l">${label}</div>${sub ? `<div class="kpi-s">${sub}</div>` : ''}</div>`;

  const roleStr = Object.entries(u.by_role || {}).map(([r, n]) => `${n} ${r}`).join(' · ') || '—';
  const kpis = [
    card('Members', u.total, roleStr),
    card('New (7d)', u.signups_7d, `${u.signups_30d} in 30d`),
    card('App opens', tr.hits_total, `${tr.hits_7d} in 7d`),
    card('Unique visitors', tr.visitors_total),
    card('Watchlist pins', en.watchlist_pins, `${en.users_with_pins} users pinning`),
    card('Stocks covered', cov.symbols, `${cov.recommendations} recs`),
    card('Target hit rate', cov.hit_rate_pct != null ? cov.hit_rate_pct + '%' : '—',
      `${(cov.outcomes.hit || 0)} hit · ${(cov.outcomes.missed || 0)} missed · ${(cov.outcomes.pending || 0)} pending`),
  ].join('');

  const topPins = (en.top_pinned || []).length
    ? `<table class="mini"><thead><tr><th>Most-pinned</th><th>Users</th></tr></thead><tbody>${
        en.top_pinned.map(p => `<tr><td>${esc(p.symbol)}</td><td>${p.pins}</td></tr>`).join('')}</tbody></table>`
    : '<div class="muted">No pins yet.</div>';

  const daily = (tr.daily || []).length
    ? `<table class="mini"><thead><tr><th>Day</th><th>Opens</th><th>New visitors</th></tr></thead><tbody>${
        tr.daily.map(d => `<tr><td>${esc(d.day)}</td><td>${d.hits}</td><td>${d.visitors}</td></tr>`).join('')}</tbody></table>`
    : '<div class="muted">No traffic recorded yet.</div>';

  const roles = ['user', 'beta', 'admin'];
  const userRows = users.map(usr => `
    <tr>
      <td>${usr.id}</td>
      <td>${esc(usr.display_name || '—')}</td>
      <td>${esc(usr.email)}</td>
      <td>
        <select class="role-sel" data-uid="${usr.id}">
          ${roles.map(r => `<option value="${r}" ${usr.role === r ? 'selected' : ''}>${r}</option>`).join('')}
        </select>
      </td>
    </tr>`).join('');

  $('#content').innerHTML = `
    <div class="admin-kpis">${kpis}</div>
    <div class="admin-grid">
      <div class="admin-box"><h4>📈 Daily traffic (14d)</h4>${daily}</div>
      <div class="admin-box"><h4>★ Top pinned stocks</h4>${topPins}</div>
    </div>
    <div class="admin-box">
      <h4>👥 Members (${users.length}) — change a role to grant beta / admin access</h4>
      <table class="mini wide"><thead><tr><th>ID</th><th>Name</th><th>Email</th><th>Role</th></tr></thead>
        <tbody>${userRows}</tbody></table>
      <div id="adminMsg" class="muted"></div>
    </div>`;

  $('#content').querySelectorAll('.role-sel').forEach(sel =>
    sel.addEventListener('change', async () => {
      const uid = sel.dataset.uid, role = sel.value;
      $('#adminMsg').textContent = `Updating user ${uid}…`;
      try {
        await fetch(`/api/admin/users/${uid}/role`, {
          method: 'PATCH', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ role }),
        }).then(r => { if (!r.ok) throw new Error('Update failed'); });
        $('#adminMsg').textContent = `✓ User ${uid} is now ${role}.`;
      } catch (e) { $('#adminMsg').textContent = 'Could not update role: ' + e.message; }
    }));
}

const VIEWS = { feed: loadFeed, leaderboard: loadLeaderboard, watchlist: loadWatchlist, digest: loadDigest, admin: loadAdmin };

function render() {
  document.querySelectorAll('.tab').forEach(t => t.classList.toggle('active', t.dataset.view === view));
  (VIEWS[view] || loadFeed)().catch(e => $('#status').textContent = 'Error: ' + e.message);
}

document.querySelectorAll('.tab').forEach(t =>
  t.addEventListener('click', () => {
    // Keep detailCache across tab switches — a stock you already opened this
    // session should stay instant when you come back to it. Only "Refresh
    // now" and logout actually invalidate it (data genuinely changed).
    if (view !== t.dataset.view) { _chatSymbol = null; }
    view = t.dataset.view; render();
  }));
$('#days').addEventListener('change', () => { if (view === 'feed') loadFeed(); });
$('#theme').addEventListener('change', () => { view = 'feed'; render(); });
$('#market').addEventListener('change', () => { _chatSymbol = null; loadThemes(); view = 'feed'; render(); });
$('#refresh').addEventListener('click', async () => {
  $('#status').textContent = 'Triggering refresh…';
  try {
    const r = await postJSON('/api/recommendations/refresh');
    $('#status').textContent = r.message;
    for (const k in detailCache) delete detailCache[k];
    setTimeout(render, 45000);
  } catch (e) { $('#status').textContent = 'Refresh failed: ' + e.message; }
});

// ── Auth ──────────────────────────────────────────────────────────────────────
let _authMode = 'login';
let _currentUser = null;

function showAuth() {
  $('#authOverlay').style.display = 'flex';
  $('#authEmail').focus();
}
function hideAuth() {
  $('#authOverlay').style.display = 'none';
  $('#authError').textContent = '';
}

// Reflect login state in the header: "Sign in" button vs. the user menu.
function updateAuthUI() {
  const isAdmin = _currentUser && _currentUser.role === 'admin';
  // Operations (SRE dashboard + Admin) is admin-only, not for every visitor.
  const opsNav = document.getElementById('opsNav');
  if (opsNav) opsNav.hidden = !isAdmin;
  $('#adminTab').hidden = !isAdmin;
  const sreTab = document.getElementById('sreTab');
  if (sreTab) sreTab.hidden = !isAdmin;
  if (!isAdmin && (view === 'admin' || view === 'sre')) { view = 'feed'; render(); }
  if (_currentUser) {
    $('#userName').textContent = _currentUser.display_name || _currentUser.email;
    $('#userMenu').hidden = false;
    $('#signIn').hidden = true;
  } else {
    $('#userMenu').hidden = true;
    $('#signIn').hidden = false;
  }
}

function setAuthMode(mode) {
  _authMode = mode;
  document.querySelectorAll('.auth-tab').forEach(t =>
    t.classList.toggle('active', t.dataset.mode === mode));
  $('#nameField').hidden = mode !== 'signup';
  $('#authSubmit').textContent = mode === 'signup' ? 'Create account' : 'Log in';
  $('#authPassword').setAttribute('autocomplete',
    mode === 'signup' ? 'new-password' : 'current-password');
  $('#authError').textContent = '';
}

document.querySelectorAll('.auth-tab').forEach(t =>
  t.addEventListener('click', () => setAuthMode(t.dataset.mode)));

$('#signIn').addEventListener('click', () => showAuth());
$('#authClose').addEventListener('click', () => hideAuth());
$('#authOverlay').addEventListener('click', (e) => {
  if (e.target === $('#authOverlay')) hideAuth();   // click backdrop to dismiss
});

$('#authForgot').addEventListener('click', async () => {
  const email = $('#authEmail').value.trim();
  if (!email) { $('#authError').textContent = 'Enter your email above first.'; return; }
  try {
    const r = await postJSON('/api/auth/forgot-password', { email });
    $('#authError').textContent = '';
    $('#authError').style.color = 'var(--buy)';
    $('#authError').textContent = r.detail || 'If that email is registered, a reset link was sent.';
  } catch (err) {
    $('#authError').textContent = err.message || 'Could not send reset link.';
  }
});

$('#authForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  $('#authError').style.color = '';
  $('#authError').textContent = '';
  const email = $('#authEmail').value.trim();
  const password = $('#authPassword').value;
  const body = { email, password };
  const path = _authMode === 'signup' ? '/api/auth/register' : '/api/auth/login';
  if (_authMode === 'signup') body.display_name = $('#authName').value.trim();
  try {
    const user = await postJSON(path, body);
    onLoggedIn(user);
  } catch (err) {
    $('#authError').textContent = err.message || 'Something went wrong.';
  }
});

$('#logout').addEventListener('click', async () => {
  try { await postJSON('/api/auth/logout'); } catch (e) {}
  _currentUser = null;
  updateAuthUI();
  for (const k in detailCache) delete detailCache[k];
  if (view === 'watchlist') render();   // swap to the sign-in gate
});

// ── Connect WhatsApp ──────────────────────────────────────────────────────────
(function initWhatsApp() {
  const btn = document.getElementById('waConnect');
  const overlay = document.getElementById('waOverlay');
  const closeBtn = document.getElementById('waClose');
  if (!btn || !overlay) return;

  const close = () => { overlay.hidden = true; };
  closeBtn.addEventListener('click', close);
  overlay.addEventListener('click', (e) => { if (e.target === overlay) close(); });

  btn.addEventListener('click', async () => {
    overlay.hidden = false;
    const body = document.getElementById('waBody');
    body.innerHTML = '<div class="loading">Generating your link code…</div>';
    try {
      const d = await postJSON('/api/whatsapp/link-code');
      const steps = [];
      if (d.whatsapp_number) {
        if (d.sandbox_join) {
          steps.push(`First-time join: send <b>${esc(d.sandbox_join)}</b> to <b>${esc(d.whatsapp_number)}</b> on WhatsApp.`);
        }
        steps.push(`Then send this 6-digit code to <b>${esc(d.whatsapp_number)}</b>:`);
      } else {
        steps.push('Send this 6-digit code to our WhatsApp number:');
      }
      body.innerHTML = `
        ${d.already_linked ? '<p class="wa-linked">✅ This account is already connected. Generating a new code re-links a different phone.</p>' : ''}
        <ol class="wa-steps">${steps.map(s => `<li>${s}</li>`).join('')}</ol>
        <div class="wa-code">${esc(d.code)}</div>
        <p class="wa-expiry">Expires in ${d.expires_minutes} minutes.</p>
        ${d.wa_link ? `<a class="wa-open" href="${esc(d.wa_link)}" target="_blank" rel="noopener">Open WhatsApp with this code pre-filled →</a>` : ''}`;
    } catch (e) {
      body.innerHTML = `<div class="empty">Couldn't generate a code: ${esc(e.message)}</div>`;
    }
  });
})();

function onLoggedIn(user) {
  _currentUser = user;
  hideAuth();
  $('#authForm').reset();
  updateAuthUI();
  if (view === 'watchlist') render();   // refresh the now-accessible watchlist
}

// ── Ask-AI chat ─────────────────────────────────────────────────────────────
function chatScopeLabel() {
  const mkt = currentMarket() === 'in' ? '🇮🇳' : '🇺🇸';
  return _chatSymbol ? `${mkt} · ${_chatSymbol}` : `${mkt} · ${view}`;
}
function addChatMsg(text, who) {
  const log = $('#chatLog');
  const div = el(`<div class="chat-msg ${who}"></div>`);
  div.textContent = text;
  log.appendChild(div);
  log.scrollTop = log.scrollHeight;
  return div;
}
function toggleChat(open) {
  const panel = $('#chatPanel');
  const show = open ?? panel.hidden;
  panel.hidden = !show;
  $('#chatFab').hidden = show;
  if (show) { $('#chatScope').textContent = chatScopeLabel(); $('#chatText').focus(); }
}

$('#chatFab').addEventListener('click', () => toggleChat(true));
$('#chatClose').addEventListener('click', () => toggleChat(false));
$('#chatForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  const input = $('#chatText');
  const q = input.value.trim();
  if (!q) return;
  addChatMsg(q, 'user');
  input.value = '';
  $('#chatScope').textContent = chatScopeLabel();
  const thinking = addChatMsg('Thinking…', 'bot pending');
  $('#chatSend').disabled = true;
  // Inactivity timeout: abort only if NO data arrives for this long. It resets
  // on every streamed chunk, so a slow-but-progressing answer is never cut off
  // (free LLM tiers can be slow) — only a genuinely hung request aborts.
  const STALL_MS = 45000;
  const ctrl = new AbortController();
  let timer = setTimeout(() => ctrl.abort(), STALL_MS);
  const resetStall = () => {
    clearTimeout(timer);
    timer = setTimeout(() => ctrl.abort(), STALL_MS);
  };
  let gotText = false;
  let source = null;
  try {
    const body = { question: q, market: currentMarket() };
    if (_chatSymbol) body.symbol = _chatSymbol;
    const raw = await fetch(API + '/api/chat/stream', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body), signal: ctrl.signal,
    });
    if (!raw.ok) {
      let detail = `chat → ${raw.status}`;
      try { const j = await raw.json(); if (j.detail) detail = j.detail; } catch (e2) {}
      throw new Error(detail);
    }
    // Server-Sent Events: read+decode the response body as it arrives and
    // append each chunk's text immediately, instead of waiting for the
    // whole answer before showing anything.
    const reader = raw.body.getReader();
    const decoder = new TextDecoder();
    let buf = '';
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      resetStall();   // data is flowing — keep the stream alive
      buf += decoder.decode(value, { stream: true });
      let sep;
      while ((sep = buf.indexOf('\n\n')) !== -1) {
        const frame = buf.slice(0, sep);
        buf = buf.slice(sep + 2);
        const line = frame.split('\n').find((l) => l.startsWith('data:'));
        if (!line) continue;
        let evt;
        try { evt = JSON.parse(line.slice(5).trim()); } catch (e2) { continue; }
        if (evt.delta) {
          if (!gotText) { thinking.classList.remove('pending'); thinking.textContent = ''; }
          gotText = true;
          thinking.textContent += evt.delta;
          $('#chatLog').scrollTop = $('#chatLog').scrollHeight;
        }
        if (evt.done) source = evt.source;
      }
    }
    thinking.classList.remove('pending');
    if (!gotText) thinking.textContent = '(no answer)';
    // Make fallback answers visibly fallbacks — if the AI didn't answer,
    // the user should know they got a quick data lookup instead.
    if (source && source !== 'llm') {
      const tag = document.createElement('div');
      tag.className = 'chat-src';
      tag.textContent = source === 'rule' ? '⚡ quick data answer (AI unavailable)'
        : source === 'fund-data' ? '⚡ fund data (AI unavailable)'
        : source === 'out-of-scope' ? '🛈 outside AlphaFunds’ scope'
        : 'ℹ data overview';
      thinking.appendChild(tag);
    }
  } catch (err) {
    // Never show a raw/technical error to the user — degrade to a calm,
    // reassuring message and invite a retry. (The server already serves a
    // grounded data answer on AI failure; this only fires if the request
    // itself was aborted or the network dropped.) Real errors are in the
    // server logs / SRE dashboard for the admin.
    thinking.classList.remove('pending');
    if (!gotText) {
      thinking.textContent = 'I couldn’t get an answer just now — please try again in a moment.';
    }
  } finally {
    clearTimeout(timer);
    $('#chatSend').disabled = false;
    $('#chatLog').scrollTop = $('#chatLog').scrollHeight;
  }
});

// ── Welcome popup (shown once per browser) ───────────────────────────────
(function initWelcome() {
  if (localStorage.getItem('seen_welcome')) return;
  const popup = $('#welcomePopup');
  if (!popup) return;
  popup.hidden = false;
  const dismiss = () => { popup.hidden = true; localStorage.setItem('seen_welcome', '1'); };
  const closeBtn = $('#welcomeClose'), gotItBtn = $('#welcomeGotIt');
  if (closeBtn) closeBtn.addEventListener('click', dismiss);
  if (gotItBtn) gotItBtn.addEventListener('click', dismiss);
})();

async function boot() {
  // Public-first: render the dashboard for everyone, then check session in the
  // background to flip the header into logged-in mode if a cookie is present.
  loadStats();
  loadThemes();
  render();
  try {
    _currentUser = await getJSON('/api/auth/me');
  } catch (e) {
    _currentUser = null;                // 401 = just a guest; keep browsing
  }
  updateAuthUI();
}

boot();

// ── Fund Tracker tab ──────────────────────────────────────────────────────────

function _expBadge(ratio) {
  if (ratio == null) return '<span class="muted">—</span>';
  const cls = ratio < 0.10 ? 'exp-green' : ratio < 0.50 ? 'exp-amber' : 'exp-red';
  return `<span class="exp-badge ${cls}">${ratio.toFixed(2)}%/yr</span>`;
}

function _cagr(v) {
  if (v == null) return '<span class="muted">—</span>';
  return `<span class="${v >= 0 ? 'r-pos' : 'r-neg'}">${v >= 0 ? '+' : ''}${v}%</span>`;
}

function _fundCard(f) {
  const m = f.metrics || {};
  const name = m.name || f.symbol;
  const isAdded = !!f.added_at;
  const rmBtn = isAdded
    ? `<button class="fund-rm" data-sym="${esc(f.symbol)}" title="Remove from portfolio">✕</button>`
    : '';
  return `
    <div class="fund-card" id="fc-${esc(f.symbol)}">
      <div class="fund-card-head">
        <div>
          <span class="fund-sym">${esc(f.symbol)}</span>
          <span class="fund-name">${esc(name)}</span>
          ${m.category ? `<span class="fund-cat">${esc(m.category)}</span>` : ''}
        </div>
        <div class="fund-card-actions">
          ${rmBtn}
        </div>
      </div>
      <div class="fund-metrics">
        <div class="fund-metric"><div class="fm-val">${_expBadge(m.expense_ratio)}</div><div class="fm-lbl">Expense ratio</div></div>
        <div class="fund-metric"><div class="fm-val">${_cagr(m.cagr_1y)}</div><div class="fm-lbl">1Y CAGR</div></div>
        <div class="fund-metric"><div class="fm-val">${_cagr(m.cagr_3y)}</div><div class="fm-lbl">3Y CAGR</div></div>
        <div class="fund-metric"><div class="fm-val">${_cagr(m.cagr_5y)}</div><div class="fm-lbl">5Y CAGR</div></div>
        <div class="fund-metric"><div class="fm-val">${_cagr(m.since_inception_cagr)}</div><div class="fm-lbl">Since inception</div></div>
      </div>
      <button class="fund-detail-btn" data-sym="${esc(f.symbol)}">Details ▾</button>
      <div class="fund-detail-panel" id="fdp-${esc(f.symbol)}" style="display:none"></div>
    </div>`;
}

async function _loadDrivers(sym, period, isRetry) {
  const body = document.querySelector(`#drv-${CSS.escape(sym)} .drv-body`);
  if (!body) return;
  if (!isRetry) body.innerHTML = '<div class="loading">Analyzing holdings…</div>';
  try {
    const d = await getJSON(`/api/funds/${encodeURIComponent(sym)}/drivers?period=${period}`);
    if (d.status === 'computing') {
      body.innerHTML = `<div class="loading">Fetching the fund's complete SEC portfolio
        and pricing every holding — this first run takes ~30s…</div>`;
      setTimeout(() => _loadDrivers(sym, period, true), 12000);
      return;
    }
    if (d.status !== 'ready' || !d.items.length) {
      body.innerHTML = `<div class="empty">${esc((d.notes || []).join(' ') || 'No driver data available.')}</div>`;
      return;
    }
    const maxAbs = Math.max(...d.items.map(i => Math.abs(i.contribution)), 0.001);
    const shown = d.items.slice(0, 25);
    const rows = shown.map(i => `
      <div class="drv-row ${i.pareto ? 'pareto' : ''}"
           title="${i.weight}% weight × ${i.ret_pct}% return = ${i.contribution}pp of fund return">
        <span class="drv-tick">${esc(i.ticker)}</span>
        <span class="drv-bar-wrap">
          <span class="drv-bar ${i.contribution >= 0 ? 'pos' : 'neg'}"
                style="width:${(Math.abs(i.contribution) / maxAbs * 100).toFixed(1)}%"></span>
        </span>
        <span class="drv-val ${i.contribution >= 0 ? 'r-pos' : 'r-neg'}">${i.contribution >= 0 ? '+' : ''}${i.contribution.toFixed(2)}pp</span>
        <span class="drv-cum muted">${i.cum_pct != null ? i.cum_pct.toFixed(0) + '%' : '—'}</span>
      </div>`).join('');
    const more = d.items.length > 25
      ? `<div class="muted" style="font-size:11px;padding:4px 0">…and ${d.items.length - 25} more holdings</div>` : '';
    body.innerHTML = `
      <p class="drv-headline">${esc(d.headline || '')}</p>
      <div class="drv-cols muted"><span>Ticker</span><span>Contribution to fund return</span><span></span><span>cum.</span></div>
      ${rows}${more}
      ${(d.notes || []).length ? `<p class="muted" style="font-size:11px">${esc(d.notes.join(' · '))}</p>` : ''}
      <p class="muted" style="font-size:11px">Source: ${d.source === 'nport' ? `SEC N-PORT complete portfolio (as of ${esc(d.as_of || '?')})` : 'top disclosed holdings only'}</p>`;
  } catch (e) {
    body.innerHTML = `<div class="empty">Could not analyze: ${esc(e.message)}</div>`;
  }
}

async function _toggleFundDetail(sym) {
  const panel = document.getElementById('fdp-' + sym);
  if (!panel) return;
  if (panel.style.display !== 'none') { panel.style.display = 'none'; return; }
  panel.style.display = 'block';
  if (panel.dataset.loaded) return;
  panel.innerHTML = '<div class="loading">Loading…</div>';
  try {
    const d = await getJSON('/api/funds/' + encodeURIComponent(sym));
    const holdings = (d.holdings || []).map(h =>
      `<tr><td>${esc(h.ticker || '—')}</td><td>${esc(h.name)}</td><td class="r-pos">${h.weight}%</td></tr>`
    ).join('');
    const sectors = Object.entries(d.sector_weights || {})
      .sort((a, b) => b[1] - a[1]).slice(0, 6)
      .map(([k, v]) => `<div class="sector-row"><span>${esc(k)}</span><span>${v.toFixed(1)}%</span></div>`)
      .join('');
    const inception = d.metrics.inception_date
      ? `<p class="muted" style="font-size:12px">Inception: ${esc(d.metrics.inception_date)}</p>` : '';
    panel.innerHTML = `
      <div class="fund-detail-inner">
        ${inception}
        <div class="fund-detail-cols">
          ${holdings ? `<div><h5>Top holdings</h5><table class="mini">${holdings}</table></div>` : ''}
          ${sectors ? `<div><h5>Sector weights</h5>${sectors}</div>` : ''}
        </div>
        ${d.data_notes.length ? `<p class="muted" style="font-size:12px">${esc(d.data_notes.join(' · '))}</p>` : ''}
        <div class="drv-section" id="drv-${esc(sym)}">
          <div class="drv-head">
            <h5>Return drivers <span class="muted">(Pareto 80/20)</span></h5>
            <span class="drv-periods">
              <button data-p="3mo">3M</button><button data-p="6mo">6M</button>
              <button data-p="1y" class="active">1Y</button>
            </span>
          </div>
          <div class="drv-body"><div class="loading">Analyzing holdings…</div></div>
        </div>
      </div>`;
    panel.dataset.loaded = '1';
    const drv = document.getElementById('drv-' + sym);
    drv.querySelectorAll('.drv-periods button').forEach(b =>
      b.addEventListener('click', () => {
        drv.querySelectorAll('.drv-periods button').forEach(x => x.classList.toggle('active', x === b));
        _loadDrivers(sym, b.dataset.p);
      }));
    _loadDrivers(sym, '1y');
  } catch (e) {
    panel.innerHTML = `<div class="loading">Could not load: ${esc(e.message)}</div>`;
  }
}

async function _addFund() {
  const inp = document.getElementById('fundSymInput');
  const sym = (inp.value || '').trim().toUpperCase().split(' ')[0];
  if (!sym) return;
  if (!_currentUser) { showAuth(); return; }
  inp.disabled = true;
  $('#status').textContent = `Adding ${sym}…`;
  try {
    await postJSON('/api/funds', { symbol: sym });
    inp.value = '';
    await loadFunds();
    $('#status').textContent = `${sym} added to your fund portfolio.`;
  } catch (e) {
    if (e.auth) { _currentUser = null; updateAuthUI(); showAuth(); return; }
    $('#status').textContent = 'Could not add fund: ' + e.message;
  } finally {
    inp.disabled = false;
  }
}

async function _removeFund(sym) {
  try {
    await fetch('/api/funds/' + encodeURIComponent(sym), { method: 'DELETE' });
    const card = document.getElementById('fc-' + sym);
    if (card) card.remove();
    $('#status').textContent = `${sym} removed.`;
  } catch (e) {
    if (e.auth) { _currentUser = null; updateAuthUI(); showAuth(); }
    else $('#status').textContent = 'Remove failed: ' + e.message;
  }
}

async function _runCompare() {
  const a = (document.getElementById('cmpA').value || '').trim().toUpperCase();
  const b = (document.getElementById('cmpB').value || '').trim().toUpperCase();
  if (!a || !b) { $('#status').textContent = 'Enter two fund symbols to compare.'; return; }
  const out = document.getElementById('compareOut');
  out.innerHTML = '<div class="loading">Comparing…</div>';
  try {
    const d = await getJSON(`/api/funds/compare?a=${encodeURIComponent(a)}&b=${encodeURIComponent(b)}`);
    const fa = d.fund_a, fb = d.fund_b;
    const rows = [
      ['Expense ratio', _expBadge(fa.expense_ratio), _expBadge(fb.expense_ratio)],
      ['1Y CAGR', _cagr(fa.cagr_1y), _cagr(fb.cagr_1y)],
      ['3Y CAGR', _cagr(fa.cagr_3y), _cagr(fb.cagr_3y)],
      ['5Y CAGR', _cagr(fa.cagr_5y), _cagr(fb.cagr_5y)],
      ['Since inception', _cagr(fa.since_inception_cagr), _cagr(fb.since_inception_cagr)],
      ['Inception date', esc(fa.inception_date || '—'), esc(fb.inception_date || '—')],
      ['Category', esc(fa.category || '—'), esc(fb.category || '—')],
    ].map(([l, va, vb]) => `<tr><td class="muted">${l}</td><td>${va}</td><td>${vb}</td></tr>`).join('');

    const sharedRows = (d.shared || []).slice(0, 10).map(h =>
      `<tr><td>${esc(h.ticker || '—')}</td><td>${esc(h.name)}</td><td>${h.weight_a}%</td><td>${h.weight_b}%</td></tr>`
    ).join('');

    out.innerHTML = `
      <div class="cmp-result">
        <table class="mini cmp-table">
          <thead><tr><th>Metric</th><th>${esc(fa.symbol)} · ${esc(fa.name)}</th><th>${esc(fb.symbol)} · ${esc(fb.name)}</th></tr></thead>
          <tbody>${rows}</tbody>
        </table>
        <div class="cmp-overlap">
          <h5>Holdings overlap — ${d.overlap_count} shared</h5>
          <p class="muted" style="font-size:12px">
            Overlap weight: ${esc(a)} ${d.overlap_weight_a}% · ${esc(b)} ${d.overlap_weight_b}%
          </p>
          ${sharedRows ? `<table class="mini"><thead><tr><th>Ticker</th><th>Name</th><th>${esc(a)} wt</th><th>${esc(b)} wt</th></tr></thead><tbody>${sharedRows}</tbody></table>` : '<p class="muted">No shared holdings found (yfinance returns top-10 only).</p>'}
        </div>
      </div>`;
  } catch (e) {
    out.innerHTML = `<div class="loading">Compare failed: ${esc(e.message)}</div>`;
  }
}

async function loadFunds() {
  $('#highlights').innerHTML = '';
  $('#status').textContent = 'Fund Tracker — add ETFs & mutual funds to compare and track.';

  const addBar = `
    <div class="fund-add-bar">
      <input id="fundSymInput" placeholder="Ticker e.g. SPY, QQQ, VTI…" maxlength="20" autocomplete="off" />
      <button id="fundAddBtn">+ Add fund</button>
    </div>`;

  let portfolioHtml = '';
  if (_currentUser) {
    try {
      const items = await getJSON('/api/funds');
      if (items.length) {
        portfolioHtml = `<div class="fund-grid">${items.map(f => _fundCard(f)).join('')}</div>`;
      } else {
        portfolioHtml = '<div class="empty">No funds tracked yet. Add one above.</div>';
      }
    } catch (e) {
      if (e.auth) { _currentUser = null; updateAuthUI(); }
      portfolioHtml = '<div class="empty">Could not load portfolio.</div>';
    }
  } else {
    portfolioHtml = `
      <div class="empty signin-gate">
        <p>📊 Track your fund portfolio here.</p>
        <p class="muted">Sign in (free) to add funds and track them across sessions.</p>
        <button id="fundSignIn" class="auth-submit" style="max-width:240px;margin:14px auto 0">Sign in to track funds</button>
      </div>`;
  }

  const compareSection = `
    <div class="cmp-section">
      <h4>Compare two funds</h4>
      <div class="cmp-inputs">
        <input id="cmpA" placeholder="Fund A e.g. SPY" maxlength="10" />
        <span class="muted">vs</span>
        <input id="cmpB" placeholder="Fund B e.g. QQQ" maxlength="10" />
        <button id="cmpBtn">Compare</button>
      </div>
      <div id="compareOut"></div>
    </div>`;

  $('#content').innerHTML = addBar + '<h4 style="padding:0 0 8px">My tracked funds</h4>' + portfolioHtml + compareSection;

  document.getElementById('fundAddBtn').addEventListener('click', _addFund);
  document.getElementById('fundSymInput').addEventListener('keydown', e => { if (e.key === 'Enter') _addFund(); });
  document.getElementById('cmpBtn').addEventListener('click', _runCompare);

  const fundSignIn = document.getElementById('fundSignIn');
  if (fundSignIn) fundSignIn.addEventListener('click', () => showAuth());

  $('#content').querySelectorAll('.fund-detail-btn').forEach(btn =>
    btn.addEventListener('click', () => _toggleFundDetail(btn.dataset.sym)));
  $('#content').querySelectorAll('.fund-rm').forEach(btn =>
    btn.addEventListener('click', () => _removeFund(btn.dataset.sym)));
}

// Wire funds into the view system
VIEWS.funds = loadFunds;

// Refresh funds on login so the portfolio appears immediately
const _onLoggedIn_prev = onLoggedIn;
onLoggedIn = function(user) {
  _onLoggedIn_prev(user);
  if (view === 'funds') loadFunds();
};

// ═════════════════════════════════════════════════════════════════════════════
// Enterprise UI additions: SRE dashboard view, global search, refresh
// animation, and Ask-AI suggestion chips. Additive — nothing above changes.
// ═════════════════════════════════════════════════════════════════════════════

// ── SRE dashboard (REAL data from the app's own telemetry) ───────────────────
function _sreLineChart(values, { fmt = (v) => v.toFixed(0), height = 74 } = {}) {
  const w = 300, h = height, pad = 4;
  const max = Math.max(...values) * 1.15 || 1, min = 0;
  const x = (i) => pad + (i / (values.length - 1)) * (w - 2 * pad);
  const y = (v) => h - pad - ((v - min) / (max - min)) * (h - 2 * pad);
  const pts = values.map((v, i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(' ');
  const gridY = [0.25, 0.5, 0.75].map((f) =>
    `<line class="grid" x1="${pad}" x2="${w - pad}" y1="${(h * f).toFixed(1)}" y2="${(h * f).toFixed(1)}"/>`).join('');
  const last = values[values.length - 1];
  return `<svg class="sre-chart" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" role="img"
       aria-label="trend, latest ${fmt(last)}">${gridY}
    <polyline class="line" points="${pts}"/>
    <circle cx="${x(values.length - 1).toFixed(1)}" cy="${y(last).toFixed(1)}" r="3" fill="var(--series-1)"/>
  </svg>`;
}

function _sreHeatCell(v, max) {
  // single-hue sequential ramp (blue), light→dark with magnitude
  const t = max > 0 ? Math.min(1, v / max) : 0;
  const alpha = 0.06 + t * 0.85;
  return `<span class="cell" style="background:rgba(57,135,229,${alpha.toFixed(2)})"
    title="${v.toFixed(2)}% errors"></span>`;
}

function _fmtUptime(seconds) {
  if (seconds == null) return '—';
  const d = Math.floor(seconds / 86400), h = Math.floor((seconds % 86400) / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  return d > 0 ? `${d}d ${h}h` : h > 0 ? `${h}h ${m}m` : `${m}m`;
}

async function loadSRE() {
  $('#highlights').innerHTML = '';
  // Defense in depth: the nav item is hidden for non-admins, but guard the
  // renderer too in case the view is reached some other way.
  if (!_currentUser || _currentUser.role !== 'admin') {
    $('#status').textContent = '';
    $('#content').innerHTML =
      '<div class="empty">🔒 The SRE dashboard is available to admins only.</div>';
    return;
  }
  $('#status').textContent = 'Site reliability — live telemetry from this app’s own traffic and AI usage.';
  $('#content').innerHTML = '<div class="loading">Loading live telemetry…</div>';

  let d;
  try {
    d = await getJSON('/api/admin/sre-metrics');
  } catch (e) {
    $('#content').innerHTML = `<div class="empty">Could not load SRE metrics: ${esc(e.message)}</div>`;
    return;
  }
  const req = d.requests || {}, fresh = d.freshness || {}, budget = d.ai_budget || {};
  const chat = d.chat_sources || {}, alerts = d.alerts || [];

  const slo = (label, actual, target, pct, cls) => `
    <div class="slo-row">
      <div class="slo-top"><span>${label}</span>
        <span><b>${actual}</b> <span class="muted">/ ${target}</span></span></div>
      <div class="slo-bar"><div class="slo-fill ${cls}" style="width:${Math.min(100, pct)}%"></div></div>
    </div>`;

  const err5 = req.error_rate_5xx != null ? (req.error_rate_5xx * 100) : null;
  const maxHourly = Math.max(...(req.hourly_requests || [0]), 1);

  // Budget gauge (calls; tokens too when a token budget is configured)
  const burn = budget.call_burn_pct;
  const burnCls = burn == null ? '' : burn >= 1 ? 'bad' : burn >= 0.8 ? 'warn' : '';
  const budgetHtml = budget.call_budget ? slo(
    'Daily AI call budget', `${budget.calls_today}`, `${budget.call_budget}`,
    (burn || 0) * 100, burnCls) : '<p class="muted">No AI budget configured (AI_DAILY_CALL_BUDGET).</p>';
  const tokenHtml = budget.token_budget ? slo(
    'Daily token budget', (budget.tokens_today || 0).toLocaleString(),
    budget.token_budget.toLocaleString(),
    (budget.token_burn_pct || 0) * 100,
    (budget.token_burn_pct || 0) >= 0.8 ? 'warn' : '') : '';

  // Answer-source mix → fallback rate (the AI feature's real health metric)
  const srcTotal = chat.total || 0;
  const srcRow = (label, n, cls) => srcTotal ? `
    <div class="slo-row"><div class="slo-top"><span>${label}</span><b>${n}</b></div>
      <div class="slo-bar"><div class="slo-fill ${cls}" style="width:${(n / srcTotal * 100).toFixed(1)}%"></div></div>
    </div>` : '';
  const bySrc = chat.by_source || {};
  const fbRate = chat.fallback_rate;

  const alertRows = alerts.length ? alerts.map((a) => `
    <div class="row"><span class="ts">${a.ts.slice(5, 16).replace('T', ' ')}</span>
      <b>${esc(a.key)}</b> — ${esc(a.message)}</div>`).join('')
    : '<p class="empty">No alerts fired. Thresholds: AI success <90%, budget ≥80%, daily job missed, 5xx >5%.</p>';

  const slowRows = (req.slowest_endpoints || []).map((s) => `
    <tr><td>${esc(s.endpoint)}</td><td>${s.count}</td><td>${Math.round(s.p95_ms)}ms</td></tr>`).join('')
    || '<tr><td colspan="3" class="empty">Not enough traffic yet.</td></tr>';

  $('#content').innerHTML = `
    <div class="sre-grid">
      <div class="sre-card">
        <h4>Service (24h · live)</h4>
        <div class="sre-big">${req.requests || 0}<span class="mini"> requests</span></div>
        <div class="tile"><span>Process uptime</span><b>${_fmtUptime(req.process_uptime_seconds)}</b></div>
        <div class="tile"><span>5xx error rate</span>
          <b class="${err5 != null && err5 > 5 ? 'r-neg' : ''}">${err5 != null ? err5.toFixed(2) + '%' : '—'}</b></div>
        <div class="tile"><span>4xx rate</span><b>${req.error_rate_4xx != null ? (req.error_rate_4xx * 100).toFixed(2) + '%' : '—'}</b></div>
      </div>

      <div class="sre-card">
        <h4>API latency (24h · live)</h4>
        <div class="sre-big">${req.p95_ms != null ? Math.round(req.p95_ms) + '<span class="mini"> ms p95</span>' : '—'}</div>
        ${req.p95_series && req.p95_series.length > 1 ? _sreLineChart(req.p95_series) : ''}
        <div class="tile"><span>p50 / p99</span>
          <b>${req.p50_ms != null ? Math.round(req.p50_ms) + 'ms' : '—'} / ${req.p99_ms != null ? Math.round(req.p99_ms) + 'ms' : '—'}</b></div>
      </div>

      <div class="sre-card">
        <h4>Data freshness SLO</h4>
        <div class="sre-big">${fresh.breach ? '<span class="r-neg">STALE</span>' : fresh.ran_today ? '<span class="r-pos">FRESH</span>' : 'PENDING'}</div>
        <div class="tile"><span>Last collection run</span><b>${esc(fresh.last_run || 'never')}</b></div>
        <div class="tile"><span>Scheduled</span><b>${esc(fresh.scheduled || '—')} +${fresh.grace_hours}h grace</b></div>
      </div>

      <div class="sre-card">
        <h4>AI budget burn (today)</h4>
        ${budgetHtml}${tokenHtml}
        <p class="muted" style="font-size:11px">At 100% the chat degrades to rule fallbacks — the 80% alert fires first.</p>
      </div>
    </div>

    <div class="sre-grid" style="margin-top:12px">
      <div class="sre-card">
        <h4>Traffic by hour (24h, UTC · live)</h4>
        <div class="heat">${(req.hourly_requests || []).map((v) => _sreHeatCell(v, maxHourly)).join('')}</div>
        <div class="heat-legend">00h ${_sreHeatCell(0.05 * maxHourly, maxHourly)} low
          ${_sreHeatCell(0.9 * maxHourly, maxHourly)} high · 23h</div>
      </div>

      <div class="sre-card">
        <h4>Chat answer sources (7d) ${fbRate != null ? `· fallback rate <b class="${fbRate > 0.2 ? 'r-neg' : 'r-pos'}">${Math.round(fbRate * 100)}%</b>` : ''}</h4>
        ${srcTotal ? `${srcRow('🤖 LLM (reasoned)', bySrc.llm || 0, '')}
          ${srcRow('⚡ Rule fallback', bySrc.rule || 0, 'warn')}
          ${srcRow('ℹ Overview fallback', bySrc.overview || 0, 'warn')}
          ${srcRow('📊 Fund data', bySrc['fund-data'] || 0, '')}
          ${srcRow('🛈 Out of scope (guardrail)', bySrc['out-of-scope'] || 0, '')}`
          : '<p class="empty">No chat answers recorded yet — ask the AI something.</p>'}
      </div>

      <div class="sre-card">
        <h4>Slowest endpoints (p95, 24h)</h4>
        <table class="sre-inc-table"><thead><tr><th>Endpoint</th><th>Calls</th><th>p95</th></tr></thead>
        <tbody>${slowRows}</tbody></table>
      </div>
    </div>

    <div class="sre-grid" style="margin-top:12px">
      <div class="sre-card" style="grid-column: 1 / -1">
        <h4>Alerts (checked every 15 min)</h4>
        <div class="mini-log">${alertRows}</div>
      </div>
    </div>
    <div id="aiUsage" style="margin-top:12px"><div class="loading">Loading AI usage…</div></div>`;

  _loadAIUsage();
}

async function _loadAIUsage() {
  // REAL data (unlike the demo panels above): every LLM call is recorded
  // with provider, model, tokens, latency, and outcome.
  const box = document.getElementById('aiUsage');
  if (!box) return;
  try {
    const d = await getJSON('/api/admin/ai-stats');
    const rate = d.success_rate != null ? Math.round(d.success_rate * 100) + '%' : '—';
    const modelRows = (d.by_model || []).map((m) => `
      <tr><td>${esc(m.provider)}</td><td>${esc(m.model || '—')}</td>
        <td>${m.calls}</td>
        <td>${m.calls ? Math.round((m.ok_calls / m.calls) * 100) + '%' : '—'}</td>
        <td>${m.avg_latency_ms != null ? Math.round(m.avg_latency_ms) + 'ms' : '—'}</td>
        <td>${(m.tokens || 0).toLocaleString()}</td></tr>`).join('')
      || '<tr><td colspan="6" class="empty">No LLM calls recorded yet — ask the AI something.</td></tr>';
    const errs = (d.recent_errors || []).map((e) => `
      <div class="row"><span class="ts">${e.ts.slice(5, 16).replace('T', ' ')}</span>
        <b>${esc(e.provider)}</b> ${esc(e.model || '')}: ${esc(e.error || '')}</div>`).join('')
      || '<p class="empty">No recent failures.</p>';
    box.innerHTML = `
      <div class="sre-grid">
        <div class="sre-card"><h4>AI calls (7d · today)</h4>
          <div class="sre-big">${d.calls} <span class="mini">· ${d.calls_today} today</span></div>
          <div class="tile"><span>Success rate</span><b>${rate}</b></div>
          <div class="tile"><span>Provider</span><b>${esc(d.provider_configured)}</b></div>
        </div>
        <div class="sre-card"><h4>Tokens (7d · today)</h4>
          <div class="sre-big">${((d.prompt_tokens || 0) + (d.completion_tokens || 0)).toLocaleString()}</div>
          <div class="tile"><span>Prompt / completion</span>
            <b>${(d.prompt_tokens || 0).toLocaleString()} / ${(d.completion_tokens || 0).toLocaleString()}</b></div>
          <div class="tile"><span>Today</span><b>${(d.tokens_today || 0).toLocaleString()}</b></div>
        </div>
        <div class="sre-card"><h4>AI response time</h4>
          <div class="sre-big">${d.avg_latency_ms != null ? Math.round(d.avg_latency_ms) + '<span class="mini"> ms avg</span>' : '—'}</div>
          ${d.latency_series && d.latency_series.length > 1 ? _sreLineChart(d.latency_series) : ''}
          <div class="tile"><span>Slowest (7d)</span><b>${d.max_latency_ms != null ? Math.round(d.max_latency_ms) + 'ms' : '—'}</b></div>
        </div>
      </div>
      <div class="sre-grid" style="margin-top:12px">
        <div class="sre-card"><h4>By model (7d)</h4>
          <table class="sre-inc-table"><thead>
            <tr><th>Provider</th><th>Model</th><th>Calls</th><th>OK</th><th>Avg</th><th>Tokens</th></tr>
          </thead><tbody>${modelRows}</tbody></table>
        </div>
        <div class="sre-card"><h4>Recent AI failures</h4>
          <div class="mini-log">${errs}</div>
          ${d.last_error ? `<p class="muted" style="font-size:11px">last_error: ${esc(d.last_error)}</p>` : ''}
        </div>
      </div>`;
  } catch (e) {
    box.innerHTML = `<div class="empty">Could not load AI usage: ${esc(e.message)}</div>`;
  }
}
VIEWS.sre = loadSRE;

// ── Global search (topbar) ───────────────────────────────────────────────────
// Visible "memory" of searched items — a per-browser recently-searched list
// on top of the server-side detail/overview caches (the invisible speed
// win). Clicking a recent item re-opens a symbol that's very likely still
// warm in those server caches, so it feels instant.
const _RECENT_KEY = 'alpha_recent_searches';
function _getRecentSearches() {
  try { return JSON.parse(localStorage.getItem(_RECENT_KEY) || '[]'); }
  catch (e) { return []; }
}
function _rememberSearch(sym, name) {
  if (!sym) return;
  const list = _getRecentSearches().filter((r) => r.symbol !== sym);
  list.unshift({ symbol: sym, name: name || '' });
  try { localStorage.setItem(_RECENT_KEY, JSON.stringify(list.slice(0, 8))); } catch (e) {}
}

(function initGlobalSearch() {
  const inp = document.getElementById('globalSearch');
  const drop = document.getElementById('globalSearchDrop');
  if (!inp || !drop) return;
  let timer = null;

  function close() { drop.innerHTML = ''; drop.classList.remove('open'); }

  function renderHits(hits, label) {
    if (!hits.length) { close(); return; }
    const heading = label ? `<div class="search-drop-label">${esc(label)}</div>` : '';
    drop.innerHTML = heading + hits.map((r) => `
      <button class="search-hit" data-sym="${esc(r.symbol)}" data-name="${esc(r.name || '')}">
        <span class="sym">${esc(r.symbol)}</span>
        <span class="nm">${esc(r.name || '')}</span></button>`).join('');
    drop.classList.add('open');
    drop.querySelectorAll('.search-hit').forEach((b) =>
      b.addEventListener('mousedown', (e) => {
        e.preventDefault();
        _rememberSearch(b.dataset.sym, b.dataset.name);
        close(); inp.value = '';
        openSymbol(b.dataset.sym);
      }));
  }

  inp.addEventListener('focus', () => {
    if (!inp.value.trim()) {
      const recent = _getRecentSearches();
      if (recent.length) renderHits(recent, 'Recently searched');
    }
  });

  inp.addEventListener('input', () => {
    clearTimeout(timer);
    const q = inp.value.trim();
    if (q.length < 2) {
      const recent = _getRecentSearches();
      if (recent.length) renderHits(recent, 'Recently searched'); else close();
      return;
    }
    timer = setTimeout(async () => {
      try {
        const data = await getJSON(`/api/search?q=${encodeURIComponent(q)}&market=${currentMarket()}`);
        renderHits((data.results || []).slice(0, 8), null);
      } catch (e) { close(); }
    }, 250);
  });
  inp.addEventListener('blur', () => setTimeout(close, 160));
  inp.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') { close(); inp.blur(); }
    if (e.key === 'Enter') {
      e.preventDefault();
      const first = drop.querySelector('.search-hit');
      if (first) {
        _rememberSearch(first.dataset.sym, first.dataset.name);
        close(); inp.value = ''; openSymbol(first.dataset.sym);
      }
    }
  });
})();

// ── Refresh button working animation ─────────────────────────────────────────
(function initRefreshSpin() {
  const btn = document.getElementById('refresh');
  if (!btn) return;
  btn.addEventListener('click', () => {
    btn.classList.add('working');
    setTimeout(() => btn.classList.remove('working'), 45000);
  });
})();

// ── Ask-AI suggestion chips ──────────────────────────────────────────────────
(function initChatSuggest() {
  const box = document.getElementById('chatSuggest');
  if (!box) return;
  box.querySelectorAll('button').forEach((b) =>
    b.addEventListener('click', () => {
      const input = document.getElementById('chatText');
      input.value = b.dataset.q;
      document.getElementById('chatForm').requestSubmit();
    }));
})();
