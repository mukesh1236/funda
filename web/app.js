const API = ''; // same origin

const $ = (s) => document.querySelector(s);
const el = (html) => { const t = document.createElement('template'); t.innerHTML = html.trim(); return t.content.firstChild; };
const esc = (s) => String(s == null ? '' : s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

let view = 'feed';
const detailCache = {};
let _chatSymbol = null;   // stock the user last expanded — gives the bot focus

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
  return `<span class="counts">
    <span class="pill b">${s.buy_count} B</span>
    <span class="pill h">${s.hold_count} H</span>
    <span class="pill s">${s.sell_count} S</span></span>`;
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
function stockCell(s) {
  return `<span class="caret">▶</span>
    <span class="name">${esc(s.company_name || s.symbol)}</span>
    <span class="tick">${esc(s.symbol)}</span> ${scoreBadge(s.consensus_score)}`;
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
  if (!h || (!h.top_buzzed?.length && !h.top_buy && !h.top_sell)) { box.innerHTML = ''; return; }
  const card = (cls, label, s, meta) => s ? `
    <div class="hl ${cls}">
      <div class="label">${label}</div>
      <div class="sym">${s.symbol} ${scoreBadge(s.consensus_score)}</div>
      <div class="meta">${meta}</div>
    </div>` : '';
  const buzz = (h.top_buzzed || []).length ? `
    <div class="hl buzz">
      <div class="label">🔥 Top 5 buzzing today</div>
      <ol class="buzzlist">${h.top_buzzed.map(s =>
        `<li><b>${esc(s.symbol)}</b> <span class="muted">${s.total_count} analysts</span> ${scoreBadge(s.consensus_score)}</li>`).join('')}</ol>
    </div>` : '';
  box.innerHTML = buzz +
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

async function loadFeed() {
  const days = $('#days').value;
  const theme = $('#theme').value;
  const market = currentMarket();
  $('#status').textContent = 'Loading feed…';
  const data = await getJSON(`/api/recommendations/feed?days=${days}&market=${market}${theme ? '&theme=' + encodeURIComponent(theme) : ''}`);
  renderHighlights(data.highlights);
  $('#status').textContent = `${data.stocks.length} stocks · click a row to see which analysts and why`;
  if (!data.stocks.length) {
    $('#content').innerHTML = `<div class="empty">No recommendations yet.<br/>Click “Refresh now” to fetch today’s analyst calls.</div>`;
    return;
  }
  const r = s => s.returns || {};
  const rows = data.stocks.map(s => `
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
  $('#content').innerHTML = `
    <table><thead><tr>
      <th>Stock</th><th>Consensus</th><th>Confidence</th><th>Avg target</th>
      <th>1M</th><th>3M</th><th>6M</th><th>12M</th><th>Big investors / funds</th>
      <th>Target status</th><th>Segments</th>
    </tr></thead><tbody>${rows}</tbody></table>`;
  $('#content').querySelectorAll('tr.row').forEach(tr =>
    tr.addEventListener('click', () => toggleExpand(tr)));
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
    if (view !== t.dataset.view) { for (const k in detailCache) delete detailCache[k]; _chatSymbol = null; }
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
  $('#adminTab').hidden = !isAdmin;
  if (_currentUser) {
    $('#userName').textContent = _currentUser.display_name || _currentUser.email;
    $('#userMenu').hidden = false;
    $('#signIn').hidden = true;
  } else {
    $('#userMenu').hidden = true;
    $('#signIn').hidden = false;
    if (view === 'admin') { view = 'feed'; render(); }   // drop admin view on logout
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
  try {
    const body = { question: q, market: currentMarket() };
    if (_chatSymbol) body.symbol = _chatSymbol;
    const res = await postJSON('/api/chat', body);
    thinking.classList.remove('pending');
    thinking.textContent = res.answer;
  } catch (err) {
    thinking.classList.remove('pending');
    thinking.classList.add('err');
    thinking.textContent = err.message || 'Could not reach the AI.';
  } finally {
    $('#chatSend').disabled = false;
    $('#chatLog').scrollTop = $('#chatLog').scrollHeight;
  }
});

// ── Welcome popup (shown once per browser) ───────────────────────────────
(function initWelcome() {
  if (localStorage.getItem('seen_welcome')) return;
  const popup = $('#welcomePopup');
  popup.hidden = false;
  const dismiss = () => { popup.hidden = true; localStorage.setItem('seen_welcome', '1'); };
  $('#welcomeClose').addEventListener('click', dismiss);
  $('#welcomeGotIt').addEventListener('click', dismiss);
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
