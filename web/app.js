const API = ''; // same origin

// Catch all uncaught JS errors and show them visibly so we can diagnose
window.onerror = (msg, src, line, col, err) => {
  const d = document.getElementById('content') || document.body;
  d.innerHTML = `<div style="color:var(--loss);background:var(--surface-2);padding:20px;border-radius:8px;margin:20px;font-family:monospace">
    <b>JS Error (line ${line}):</b> ${msg}<br><pre>${err?.stack || ''}</pre></div>`;
};
window.onunhandledrejection = (e) => {
  const d = document.getElementById('content') || document.body;
  d.innerHTML = `<div style="color:var(--loss);background:var(--surface-2);padding:20px;border-radius:8px;margin:20px;font-family:monospace">
    <b>Unhandled Promise Error:</b> ${e.reason?.message || e.reason}<br><pre>${e.reason?.stack || ''}</pre></div>`;
};

const $ = (s) => document.querySelector(s);
const el = (html) => { const t = document.createElement('template'); t.innerHTML = html.trim(); return t.content.firstChild; };
const esc = (s) => String(s == null ? '' : s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

// Inline SVG icon from the sprite in index.html (Lucide, ISC licence).
const icon = (name, cls = 'ic') => `<svg class="${cls}" aria-hidden="true"><use href="#i-${name}"/></svg>`;

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
async function postJSON(path, body, method) {
  const opts = { method: method || 'POST' };
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

// ── Market facts during waits ────────────────────────────────────────────────
// The slow paths here are network-bound and not optimisable away: SEC EDGAR
// fetches, N-PORT downloads, LLM calls. Filling that wait with something true
// and relevant turns dead time into a moment of learning.
//
// Two rules hold this together:
//   1. A fact NEVER delays a result. It appears only once a wait has already
//      passed _FACT_DELAY_MS; anything quicker renders with no fact at all,
//      because a fact that flashes up and vanishes is worse than none.
//   2. Facts are hand-written and verifiable — structural, regulatory and
//      definitional truths, never performance statistics and never generated.
//      A wrong "fun fact" on a financial product is the same failure as a
//      fabricated number, just in friendlier clothing.

const _FACTS_KEY = 'facts_enabled';
const _FACT_DELAY_MS = 600;      // below this a fact would flash and vanish
const _FACT_ROTATE_MS = 6000;    // fact sheets run 30s+, so one is not enough

const FACTS = {
  factsheet: [
    "Form 497K is the SEC's Summary Prospectus — the short version every US mutual fund must publish alongside the full one.",
    'A prospectus must state objective, strategy, risks and fees, in that order. The SEC prescribes the headings, which is why they all read alike.',
    'Form 485BPOS is the annual update a fund files to keep its registration current — it carries the full prospectus with it.',
    "An N-CSR is a fund's certified shareholder report, filed twice a year and signed by an officer of the fund.",
    "Funds must describe their principal risks specifically — a generic “markets can fall” paragraph doesn't satisfy the rule.",
    'Performance shown in a fund document is after fees, so the chart already has costs taken out of it.',
  ],
  holdings: [
    'Every US mutual fund files its complete portfolio with the SEC four times a year, on Form N-PORT.',
    'Funds disclose their top-10 holdings far more often than the full portfolio — which is why estimates built on top-10 data understate overlap.',
    'An index fund must disclose the index it tracks, and cannot quietly stray from it.',
    'A total-market fund can hold thousands of companies and still have a fifth of its money in the largest ten.',
    'N-PORT weights are reported as a percentage of net assets, and rounding means a full list rarely sums to exactly 100.',
  ],
  xray: [
    'Two S&P 500 index funds from different providers hold essentially the same companies — the difference is nearly all fee and tracking.',
    'Holding three funds is not the same as being diversified across three things: they can own the same companies underneath.',
    'Every US mutual fund files its complete portfolio with the SEC four times a year, on Form N-PORT.',
    'Overlap is about weight, not names: two funds can share most of their holdings but very little of your money.',
    'A fee paid on duplicated exposure buys nothing extra — you own the position already, through the other fund.',
  ],
  drivers: [
    'A market-cap-weighted fund puts more money into a company as it grows, so returns concentrate over time without anyone rebalancing.',
    "Weight and contribution are different things: a small position that doubles can outrun a large one that drifts sideways.",
    'Cap-weighted indexes are rebalanced by the market itself — the weights move whenever prices do.',
  ],
  compare: [
    'One basis point is 0.01%. Fund fees are usually quoted in them, so "4 bps" means 0.04%.',
    "A fund's expense ratio is deducted from NAV daily, which is why the fee never appears as a charge on a statement.",
    'Two funds tracking the same index can still return differently, through fees, sampling, and how they handle dividends.',
  ],
  stock: [
    'An analyst price target is a 12-month view, not a forecast for tomorrow.',
    "A company's fiscal year doesn't have to match the calendar, which is why “Q1” means different months at different companies.",
    'US companies file results on Form 10-Q each quarter and Form 10-K once a year — the 10-K carries the full risk disclosures.',
    'Market capitalisation is share price times shares outstanding, so buybacks move it even when the price does not.',
  ],
  analysts: [
    'Research analysts must disclose whether their firm does banking business with the company they cover.',
    'A consensus rating is an average of individual views — it hides how much the analysts disagree.',
    'Analysts revise price targets far more often than they change ratings, so the number moves even when the label does not.',
  ],
  default: [
    'One basis point is 0.01% — fund fees are usually quoted in them.',
    "A fund's expense ratio is deducted from NAV daily, so you never see it charged on a statement.",
    'Mutual funds price once a day after the close; ETFs trade throughout the day like shares.',
    'NAV is a fund’s assets minus its liabilities, divided by the shares outstanding.',
  ],
};

// localStorage throws in private windows and when site data is blocked, so
// every access is guarded and the feature defaults to ON — a preference lookup
// must never be able to break a loading state.
function _factsOn() {
  try { return localStorage.getItem(_FACTS_KEY) !== '0'; } catch (e) { return true; }
}

function _setFactsOn(on) {
  try { localStorage.setItem(_FACTS_KEY, on ? '1' : '0'); } catch (e) {}
  const btn = document.getElementById('factsToggle');
  if (btn) {
    btn.setAttribute('aria-pressed', on ? 'true' : 'false');
    btn.classList.toggle('off', !on);
    btn.title = on ? 'Market facts while you wait: on' : 'Market facts while you wait: off';
  }
  // Turning off mid-wait clears anything already on screen rather than
  // stranding it there until the request finishes.
  if (!on) document.querySelectorAll('.loading-fact').forEach(el => el.remove());
}

/** A loading block that grows a market fact if the wait outlasts the threshold.
 *  `message` is plain text (escaped here); `topic` selects the fact pool. */
function waitingHtml(message, topic) {
  const msg = `<div class="loading-msg">${esc(message)}</div>`;
  if (!_factsOn()) return `<div class="loading" role="status" aria-live="polite">${msg}</div>`;
  _startFactTicker();
  return `<div class="loading" role="status" aria-live="polite">${msg}` +
    `<div class="loading-fact" data-topic="${esc(topic || 'default')}" ` +
    `data-born="${Date.now()}" hidden></div></div>`;
}

// ONE timer for the whole app, started on demand and stopped as soon as no
// loading slot is left on the page. Callers never manage a lifecycle: every
// call site already replaces innerHTML when its data lands, which removes the
// slot and ends its rotation for free — nothing to leak, nothing to tear down.
let _factTimer = null;

function _startFactTicker() {
  if (_factTimer) return;
  _factTimer = setInterval(_factTick, 400);
}

function _factTick() {
  const slots = document.querySelectorAll('.loading-fact');
  if (!slots.length || !_factsOn()) {
    clearInterval(_factTimer);
    _factTimer = null;
    return;
  }
  const now = Date.now();
  slots.forEach(el => {
    if (now - Number(el.dataset.born || 0) < _FACT_DELAY_MS) return;
    const shown = Number(el.dataset.shown || 0);
    if (shown && now - shown < _FACT_ROTATE_MS) return;

    const pool = FACTS[el.dataset.topic] || FACTS.default;
    let i = Math.floor(Math.random() * pool.length);
    if (pool.length > 1 && String(i) === el.dataset.last) i = (i + 1) % pool.length;
    el.dataset.last = String(i);
    el.dataset.shown = String(now);
    // The rotating text is decorative and aria-hidden: announcing each rotation
    // would spam screen readers, which already heard the status message. The
    // hide button stays outside that, so it remains reachable.
    el.innerHTML =
      `<span class="fact-text" aria-hidden="true">${esc(pool[i])}</span>` +
      `<button class="fact-hide" type="button" aria-label="Hide market facts">Hide</button>`;
    el.hidden = false;
  });
}

document.addEventListener('click', (e) => {
  if (e.target.closest('.fact-hide')) { e.preventDefault(); _setFactsOn(false); }
});

// Reflect the stored preference on load and wire the header toggle. app.js runs
// at the end of <body>, so the button already exists.
(function initFactsToggle() {
  const btn = document.getElementById('factsToggle');
  if (!btn) return;
  _setFactsOn(_factsOn());
  btn.addEventListener('click', () => _setFactsOn(!_factsOn()));
})();

function scoreBadge(n) {
  const cls = n > 0 ? 'score-pos' : n < 0 ? 'score-neg' : 'score-zero';
  return `<span class="badge ${cls}">${n > 0 ? '+' : ''}${n}</span>`;
}
// Proportional buy / hold / sell bar with the score and counts beneath. Hold is
// a neutral track, so the bar still reads when buy and sell are both small.
function consBar(s) {
  const t = Math.max(s.total_count, 1);
  const w = (n) => (n / t * 100).toFixed(1);
  return `<div class="bar" role="img" aria-label="${s.buy_count} buy, ${s.hold_count} hold, ${s.sell_count} sell"><i class="b" style="width:${w(s.buy_count)}%"></i><i class="h" style="width:${w(s.hold_count)}%"></i><i class="s" style="width:${w(s.sell_count)}%"></i></div>`;
}
function consCell(s) {
  const sc = s.consensus_score;
  const cls = sc > 0 ? 'gain' : sc < 0 ? 'loss' : '';
  return `<div class="cons">${consBar(s)}
    <div class="cons-t"><b class="${cls}">${sc > 0 ? '+' : ''}${sc}</b><span class="num">${s.buy_count}B · ${s.hold_count}H · ${s.sell_count}S</span></div></div>`;
}
// Gain/loss is never colour alone: an arrow carries direction too.
function ret(v) {
  if (v == null) return '<span class="na">—</span>';
  const up = v >= 0;
  return `<span class="${up ? 'r-pos' : 'r-neg'}">${icon(up ? 'arrow-up' : 'arrow-down', 'ic xs')}${Math.abs(v)}%</span>`;
}
function confBadge(c) {
  if (!c) return '<span class="na">—</span>';
  const cls = { High: 'cf-high', Medium: 'cf-med', Low: 'cf-low' }[c.label] || 'cf-med';
  return `<span class="conf ${cls}" title="${esc(c.rationale)}"><i></i>${esc(c.label)} <span class="num">${Math.round(c.score)}</span></span>`;
}
// A neutral monogram tile — not a per-ticker hue, which would need a second
// palette to stay legible in dark mode and carries no information anyway.
function tickAvatar(sym) {
  return `<span class="tick-avatar" aria-hidden="true">${esc(sym.slice(0, 3))}</span>`;
}
function stockCell(s) {
  return `<span class="stk">${icon('chevron-right', 'ic xs caret')}${tickAvatar(s.symbol)}
    <span class="stk-text"><b class="tick">${esc(s.symbol)}</b><em>${esc(s.company_name || '')}</em></span></span>`;
}
function statusChip(o) {
  if (!o || !o.status) return '<span class="na">—</span>';
  const map = { hit: 'st-hit', missed: 'st-missed', pending: 'st-pending', expired: 'st-expired' };
  const pct = (o.pct_to_target != null) ? ` (${o.pct_to_target > 0 ? '+' : ''}${o.pct_to_target}%)` : '';
  return `<span class="status-chip ${map[o.status] || 'st-pending'}">${o.status}${pct}</span>`;
}

function themeTags(themes) {
  if (!themes || !themes.length) return '<span class="na">—</span>';
  // Two tags then "+N": three or four long segment names would be wider than
  // the rest of the row put together. The full list stays in the tooltip.
  const shown = themes.slice(0, 2), more = themes.length - shown.length;
  return `<span class="themes" title="${esc(themes.join(', '))}">${shown.map(t => `<span class="theme-tag">${esc(t)}</span>`).join('')}${more > 0 ? `<span class="theme-tag more">+${more}</span>` : ''}</span>`;
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

// The Overview's headline row describes the MARKET, derived only from the feed
// payload already fetched — no extra request and nothing invented. System facts
// (stocks tracked, sources, scheduler) belong to the admin views.
function renderKpis(stocks, h) {
  const box = $('#stats');
  if (!stocks.length) { box.innerHTML = ''; return; }
  const n = stocks.length;
  const netBuy = stocks.filter(s => s.consensus_score > 0).length;
  const buys = stocks.reduce((a, s) => a + s.buy_count, 0);
  const total = stocks.reduce((a, s) => a + s.total_count, 0);
  const top = (h && h.top_buy) || stocks.reduce((a, s) => (s.consensus_score > a.consensus_score ? s : a), stocks[0]);
  const st = stocks.map(s => s.outcome && s.outcome.status);
  const hit = st.filter(x => x === 'hit').length;
  const resolved = hit + st.filter(x => x === 'missed').length;
  const sign = (v) => v > 0 ? `<span class="gain">+${v}</span>` : v < 0 ? `<span class="loss">${v}</span>` : `<span>${v}</span>`;
  const tiles = [
    ['Net-buy names', `${netBuy}<span class="of"> / ${n}</span>`, 'consensus score above zero',
      'Stocks whose buy ratings outnumber their sell ratings'],
    ['Buy ratings', total ? `${Math.round(buys / total * 100)}%` : '—', `of ${total.toLocaleString()} analyst ratings`,
      'Share of all analyst ratings in this view that are buys'],
    ['Strongest consensus', `${esc(top.symbol)} ${sign(top.consensus_score)}`,
      `${top.buy_count} buy · ${top.hold_count} hold · ${top.sell_count} sell`,
      'Highest consensus score: buy ratings minus sell ratings'],
    ['Targets hit', resolved ? `${Math.round(hit / resolved * 100)}%` : '—',
      resolved ? `${hit} of ${resolved} resolved calls` : 'no resolved calls yet',
      'Share of resolved analyst price targets that were reached'],
  ];
  box.innerHTML = tiles.map(([l, v, s, tip]) =>
    `<div class="stat" title="${esc(tip)}"><div class="l">${esc(l)}</div><div class="v">${v}</div><div class="s">${esc(s)}</div></div>`).join('');
}

// Views other than the Overview own the whole page below the header.
function clearOverview() {
  $('#stats').innerHTML = '';
  $('#highlights').innerHTML = '';
}

function _actClass(action) {
  const a = String(action || '').toLowerCase();
  if (/buy|upgrade|outperform|overweight|accumulate/.test(a)) return 'pos';
  if (/sell|downgrade|underperform|underweight|reduce/.test(a)) return 'neg';
  return '';
}

function renderHighlights(h) {
  const box = $('#highlights');
  if (!h || (!h.top_buzzed?.length && !h.top_buy && !h.top_sell && !h.top_movers?.length)) { box.innerHTML = ''; return; }
  const title = (ic, label, hint) =>
    `<h3 class="hl-title">${icon(ic)}${label}${hint ? ` <span class="hl-hint">${hint}</span>` : ''}</h3>`;
  const card = (cls, ic, label, s, meta) => s ? `
    <div class="hl ${cls}">
      ${title(ic, label)}
      <div class="big">${esc(s.symbol)} <span class="v ${s.consensus_score >= 0 ? 'gain' : 'loss'}">${s.consensus_score > 0 ? '+' : ''}${s.consensus_score}</span></div>
      <div class="meta">${meta}</div>
      <div class="cons hl-bar">${consBar(s)}<div class="cons-t"><span class="num">${s.buy_count}B · ${s.hold_count}H · ${s.sell_count}S</span></div></div>
    </div>` : '';

  const fmtPct = (p) => p >= 0 ? `+${p.toFixed(2)}%` : `${p.toFixed(2)}%`;
  const newsLink = (sym) =>
    `<a class="why-link" href="https://finance.yahoo.com/quote/${encodeURIComponent(sym)}/news" target="_blank" rel="noopener" title="See news for ${esc(sym)}" aria-label="News for ${esc(sym)}">${icon('newspaper', 'ic xs')}<span class="why-txt">News</span></a>`;

  // Today's analyst catalysts — the "why" behind moves
  const catalysts = (h.today_catalysts || []).length ? `
    <div class="hl catalysts">
      ${title('megaphone', "Today's analyst calls", 'click a ticker for the analyst view')}
      <ol class="buzzlist catalyst-list">${h.today_catalysts.map(c => {
        const pct = c.day_change_pct != null
          ? `<span class="${c.day_change_pct >= 0 ? 'pct-up' : 'pct-down'}">${fmtPct(c.day_change_pct)}</span>`
          : '';
        const firm = c.firm ? `<span class="firm">${esc(c.firm)}</span>` : '';
        const tgt  = c.target_price ? `<span class="num pt">PT $${esc(String(c.target_price))}</span>` : '';
        const act  = `<span class="act-badge ${_actClass(c.action)}">${esc(c.action)}</span>`;
        return `<li>
          <button class="sym-link" onclick="openSymbol('${esc(c.symbol)}')">${esc(c.symbol)}</button>
          ${act} ${firm} ${tgt} ${pct} ${newsLink(c.symbol)}
        </li>`;
      }).join('')}</ol>
    </div>` : '';

  const movers = (h.top_movers || []).length ? `
    <div class="hl movers">
      ${title('trending-up', "Today's movers", 'click a ticker for the analyst view')}
      <ol class="buzzlist">${h.top_movers.map(s => {
        const pct = s.day_change_pct != null
          ? `<span class="${s.day_change_pct >= 0 ? 'pct-up' : 'pct-down'}">${fmtPct(s.day_change_pct)}</span>` : '';
        return `<li>
          <button class="sym-link" onclick="openSymbol('${esc(s.symbol)}')">${esc(s.symbol)}</button>
          ${pct} ${scoreBadge(s.consensus_score)} ${newsLink(s.symbol)}
        </li>`;
      }).join('')}</ol>
    </div>` : '';

  const buzz = (h.top_buzzed || []).length ? `
    <div class="hl buzz">
      ${title('flame', 'Most analyst coverage')}
      <ol class="buzzlist">${h.top_buzzed.map((s, i) =>
        `<li><span class="rk">${i + 1}</span><button class="sym-link" onclick="openSymbol('${esc(s.symbol)}')">${esc(s.symbol)}</button>
          <span class="firm">${s.total_count} analysts</span>${scoreBadge(s.consensus_score)}</li>`).join('')}</ol>
    </div>` : '';

  box.classList.toggle('has-movers', !!movers);
  box.innerHTML = catalysts + movers + buzz +
    card('buy', 'trending-up', 'Strongest buy', h.top_buy,
      h.top_buy ? `${h.top_buy.buy_count} buys${h.top_buy.avg_target ? ' · target $' + h.top_buy.avg_target : ''}` : '') +
    card('sell', 'trending-down', 'Strongest sell', h.top_sell,
      h.top_sell ? `${h.top_sell.sell_count} sells vs ${h.top_sell.buy_count} buys` : '');
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
    if (view === 'feed') await loadFeed({ quiet: true }).catch(() => {});
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

const _FEED_COLS = 10;   // keep in step with the <th> list below (expand row spans it)

function _renderFeedRows() {
  const sorted = _sortedStocks();
  const r = s => s.returns || {};
  const rows = sorted.map(s => `
    <tr class="row" data-sym="${esc(s.symbol)}" tabindex="0" aria-expanded="false">
      <td class="stockcell">${stockCell(s)}</td>
      <td>${consCell(s)}</td>
      <td class="c-conf">${confBadge(s.confidence)}</td>
      <td class="r num c-tgt">${s.avg_target != null ? '$' + s.avg_target : '<span class="na">—</span>'}</td>
      <td class="r c-ret">${ret(r(s).one_month)}</td>
      <td class="r c-ret">${ret(r(s).three_month)}</td>
      <td class="r c-ret">${ret(r(s).six_month)}</td>
      <td class="r">${ret(r(s).twelve_month)}</td>
      <td class="c-stat">${statusChip(s.outcome)}</td>
      <td class="c-seg">${themeTags(s.themes)}</td>
    </tr>
    <tr class="expand" data-for="${esc(s.symbol)}" style="display:none"><td colspan="${_FEED_COLS}"><div class="expand-inner" data-body="${esc(s.symbol)}"></div></td></tr>`).join('');

  const th = (col, label, cls = '') =>
    `<th class="sortable${cls}${_feedSort.col === col ? ' sorted' : ''}" data-scol="${col}" aria-sort="${_feedSort.col === col ? (_feedSort.dir === 1 ? 'descending' : 'ascending') : 'none'}">${label} ${_sortArrow(col)}</th>`;

  $('#content').innerHTML = `
    <section class="card tbl">
      <div class="tbl-h"><h3>Analyst consensus</h3>
        <span class="muted">${sorted.length} stocks · select a row to see which analysts and why</span></div>
      <div class="scroll"><table><thead><tr>
        <th>Stock</th>
        ${th('consensus', 'Consensus')}
        <th class="c-conf">Confidence</th><th class="r c-tgt">Avg target</th>
        ${th('ret1m', '1M', ' r c-ret')}${th('ret3m', '3M', ' r c-ret')}${th('ret6m', '6M', ' r c-ret')}${th('ret12m', '12M', ' r')}
        <th class="c-stat">Target status</th><th class="c-seg">Segments</th>
      </tr></thead><tbody>${rows}</tbody></table></div>
    </section>`;

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
  $('#content').querySelectorAll('tr.row').forEach(tr => {
    tr.addEventListener('click', () => toggleExpand(tr));
    // Rows are the only way into a stock's detail, so they must work from the keyboard.
    tr.addEventListener('keydown', (e) => {
      if (e.target !== tr) return;
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); toggleExpand(tr); }
    });
  });
}

// ── Loading / empty / error states ───────────────────────────────────────────
function skeletonFeed() {
  const row = `<div class="skel-row"><span class="skel" style="width:34px;height:34px"></span>
    <span class="skel" style="width:22%"></span><span class="skel" style="width:30%"></span>
    <span class="skel" style="width:12%;margin-left:auto"></span></div>`;
  $('#stats').innerHTML = Array(4).fill(
    `<div class="stat"><span class="skel" style="width:50%;height:12px"></span>
     <span class="skel" style="width:40%;height:28px;margin:12px 0 8px"></span><span class="skel" style="width:70%;height:12px"></span></div>`).join('');
  $('#content').innerHTML = `<section class="card tbl" aria-busy="true" aria-label="Loading analyst consensus">
    <div class="tbl-h"><h3>Analyst consensus</h3></div>${row.repeat(8)}</section>`;
}

// One component for "nothing here" and "something broke", so both look designed.
function stateHtml({ icon: ic = 'inbox', title, text, action, error = false }) {
  return `<div class="state${error ? ' is-error' : ''}" role="${error ? 'alert' : 'status'}">
    <div class="state-ic">${icon(ic, 'ic')}</div>
    <div class="state-title">${esc(title)}</div>
    <p class="state-text">${esc(text)}</p>
    ${action ? `<button class="btn-primary" type="button" data-state-action="${esc(action.id)}">${esc(action.label)}</button>` : ''}
  </div>`;
}

document.addEventListener('click', (e) => {
  const b = e.target.closest('[data-state-action]');
  if (!b) return;
  if (b.dataset.stateAction === 'retry') render();
  if (b.dataset.stateAction === 'refresh') $('#refresh').click();
});

async function loadFeed({ quiet = false } = {}) {
  const days = $('#days').value;
  const theme = $('#theme').value;
  const market = currentMarket();
  $('#status').textContent = '';
  // A background auto-refresh keeps the table on screen until fresh data lands;
  // only a user-driven load swaps in the skeleton.
  if (!quiet || !document.querySelector('#content .tbl tbody')) skeletonFeed();
  const data = await getJSON(`/api/recommendations/feed?days=${days}&market=${market}${theme ? '&theme=' + encodeURIComponent(theme) : ''}`);
  if (view !== 'feed') return;   // the user navigated away while this was in flight
  renderHighlights(data.highlights);
  renderKpis(data.stocks, data.highlights);
  _updateFeedTimestamp();
  _scheduleAutoRefresh();
  _feedStocks = data.stocks;
  _feedSort = { col: null, dir: 1 }; // reset sort on fresh load
  if (!data.stocks.length) {
    $('#content').innerHTML = `<section class="card">${stateHtml({
      title: 'No recommendations yet',
      text: "Fetch today's analyst calls to populate the feed.",
      action: { id: 'refresh', label: 'Fetch now' } })}</section>`;
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
  clearOverview();
  $('#status').textContent = '';
  $('#content').innerHTML = waitingHtml(`Loading ${sym}…`, 'stock');
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
  if (open) { exp.style.display = 'none'; tr.classList.remove('open'); tr.setAttribute('aria-expanded', 'false'); return; }
  tr.classList.add('open');
  tr.setAttribute('aria-expanded', 'true');
  exp.style.display = '';
  _chatSymbol = sym;   // focus the chat bot on the stock just opened
  const body = exp.querySelector('.expand-inner');
  if (body.dataset.loaded) return;
  body.innerHTML = waitingHtml('Loading analysts…', 'analysts');
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
  clearOverview();
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
  clearOverview();
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
        // No mutual funds here: the watchlist pins an entry price and tracks
        // the daily move, which a once-daily NAV can't support.
        const res = await getJSON(`/api/search?q=${encodeURIComponent(q)}&market=${currentMarket()}&include_funds=false`);
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
  clearOverview();
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
  clearOverview();
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
  (VIEWS[view] || loadFeed)().catch(e => {
    $('#status').textContent = '';
    clearOverview();   // drop any skeleton tiles left behind by the failed load
    $('#content').innerHTML = `<section class="card">${stateHtml({
      error: true, icon: 'triangle-alert', title: "Couldn't load this view",
      text: e.message || 'Something went wrong.', action: { id: 'retry', label: 'Try again' } })}</section>`;
  });
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
$('#market').addEventListener('change', () => { _chatSymbol = null; loadThemes(); renderChatSuggest(); view = 'feed'; render(); });
$('#refresh').addEventListener('click', async () => {
  $('#status').textContent = 'Triggering refresh…';
  try {
    const r = await postJSON('/api/recommendations/refresh');
    $('#status').textContent = r.message;
    for (const k in detailCache) delete detailCache[k];
    setTimeout(render, 45000);
  } catch (e) { $('#status').textContent = 'Refresh failed: ' + e.message; }
});

// ── Header controls: segmented filters, theme toggle, account menu ─────────────
// The segmented buttons are a view over the hidden <select>s. The select stays
// the single source of truth (every loader reads it), so changing a segment just
// sets the select and fires its own 'change' — no second path to keep in sync.
document.querySelectorAll('.seg[data-for]').forEach((seg) => {
  const sel = document.getElementById(seg.dataset.for);
  const sync = () => seg.querySelectorAll('button').forEach((b) =>
    b.setAttribute('aria-pressed', b.dataset.v === sel.value ? 'true' : 'false'));
  seg.addEventListener('click', (e) => {
    const b = e.target.closest('button[data-v]');
    if (!b || b.dataset.v === sel.value) return;
    sel.value = b.dataset.v;
    sel.dispatchEvent(new Event('change'));
    sync();
  });
  sync();
});

// Theme: nothing saved → follow the OS (CSS handles it, no attribute). A click
// saves an explicit light/dark choice, applied by the inline script in <head> on
// the next load. Storage can throw, so it is guarded; the toggle still works for
// the session without it.
(function initThemeToggle() {
  const btn = document.getElementById('themeToggle');
  if (!btn) return;
  const root = document.documentElement;
  const mq = window.matchMedia ? window.matchMedia('(prefers-color-scheme: dark)') : null;
  const effective = () => root.getAttribute('data-theme') || (mq && mq.matches ? 'dark' : 'light');
  const reflect = () => {
    const dark = effective() === 'dark';
    btn.setAttribute('aria-label', dark ? 'Switch to light theme' : 'Switch to dark theme');
    btn.title = dark ? 'Switch to light theme' : 'Switch to dark theme';
    // The <meta theme-color> pair is media-query driven; an explicit choice
    // overrides it so the mobile browser chrome matches the page.
    document.querySelectorAll('meta[name="theme-color"]').forEach((m) => m.remove());
    const m = document.createElement('meta');
    m.name = 'theme-color';
    m.content = dark ? '#0b0f1a' : '#f6f7fb';
    document.head.appendChild(m);
  };
  btn.addEventListener('click', () => {
    const next = effective() === 'dark' ? 'light' : 'dark';
    root.setAttribute('data-theme', next);
    try { localStorage.setItem('theme', next); } catch (e) {}
    reflect();
  });
  if (mq && mq.addEventListener) mq.addEventListener('change', reflect);
  reflect();
})();

// Account menu: a disclosure popover. Closes on outside click, Escape, or when
// any item inside it is used.
(function initAccountMenu() {
  const btn = document.getElementById('avatarBtn');
  const menu = document.getElementById('acctMenu');
  if (!btn || !menu) return;
  const set = (open) => {
    menu.hidden = !open;
    btn.setAttribute('aria-expanded', open ? 'true' : 'false');
  };
  btn.addEventListener('click', (e) => { e.stopPropagation(); set(menu.hidden); });
  document.addEventListener('click', (e) => {
    if (menu.hidden) return;
    // The Facts switch is a setting: leave the menu open so the change is visible.
    if (e.target.closest('#factsToggle')) return;
    if (!menu.contains(e.target) || e.target.closest('.menu-item')) set(false);
  });
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && !menu.hidden) { set(false); btn.focus(); }
  });
  // Refresh lives in the filter row on desktop and in this menu on mobile.
  const rm = document.getElementById('refreshMenu');
  if (rm) rm.addEventListener('click', () => $('#refresh').click());
})();

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

// Reflect login state in the header: the account menu shows "Sign in" for
// guests, or the member's name, WhatsApp and Log out once signed in.
function updateAuthUI() {
  const isAdmin = !!(_currentUser && _currentUser.role === 'admin');
  // Operations (SRE dashboard + Admin) is admin-only, not for every visitor.
  document.querySelectorAll('[data-admin-only]').forEach((n) => { n.hidden = !isAdmin; });
  if (!isAdmin && (view === 'admin' || view === 'sre')) { view = 'feed'; render(); }
  const signedIn = !!_currentUser;
  $('#userMenu').hidden = !signedIn;
  $('#waConnect').hidden = !signedIn;
  $('#logout').hidden = !signedIn;
  $('#logoutSep').hidden = !signedIn;
  $('#signIn').hidden = signedIn;
  const av = $('#avatarInit');
  if (signedIn) {
    const label = _currentUser.display_name || _currentUser.email || '';
    $('#userName').textContent = label;
    $('#userEmail').textContent = _currentUser.display_name ? (_currentUser.email || '') : '';
    $('#userEmail').hidden = !_currentUser.display_name;
    const initials = label.split(/[\s@.]+/).filter(Boolean).slice(0, 2).map((w) => w[0]).join('').toUpperCase();
    av.textContent = initials || '?';
  } else {
    av.innerHTML = icon('circle-user');
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
    $('#authError').style.color = 'var(--gain)';
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
        ${d.already_linked ? `<p class="wa-linked">${icon('check', 'ic sm')} This account is already connected. Generating a new code re-links a different phone.</p>` : ''}
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
  const mkt = currentMarket() === 'in' ? 'IN' : 'US';
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
  $('#chatFab').setAttribute('aria-expanded', show ? 'true' : 'false');
  if (show) {
    panel.classList.remove('chat-min');   // always open expanded
    $('#chatScope').textContent = chatScopeLabel();
    renderChatSuggest();
    $('#chatText').focus();
  }
}

$('#chatFab').addEventListener('click', () => toggleChat());
$('#chatClose').addEventListener('click', () => toggleChat(false));

// Minimize → collapse to just the header bar; click the header (or —) to restore.
$('#chatMin').addEventListener('click', (e) => {
  e.stopPropagation();
  $('#chatPanel').classList.toggle('chat-min');
});
$('.chat-head').addEventListener('click', (e) => {
  // clicking the collapsed header restores it (ignore clicks on the buttons)
  if (e.target.closest('.chat-head-btns')) return;
  $('#chatPanel').classList.remove('chat-min');
});
// Maximize ⤢ ↔ restore ⤡ — toggle a roomier panel size.
$('#chatMax').addEventListener('click', (e) => {
  e.stopPropagation();
  const panel = $('#chatPanel');
  panel.classList.remove('chat-min');
  const max = panel.classList.toggle('chat-max');
  e.currentTarget.innerHTML = icon(max ? 'minimize-2' : 'maximize-2', 'ic sm');
  e.currentTarget.title = max ? 'Restore' : 'Maximize';
  e.currentTarget.setAttribute('aria-label', max ? 'Restore' : 'Maximize');
});
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
        : source === 'advice-declined' ? '🎓 educational — not investment advice'
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
  // Guarded like every other localStorage access here: it throws in private
  // windows and when site data is blocked, which used to abort this function
  // outright and, in dismiss(), throw after the popup had already been hidden.
  try { if (localStorage.getItem('seen_welcome')) return; } catch (e) {}
  const popup = $('#welcomePopup');
  if (!popup) return;
  popup.hidden = false;
  const dismiss = () => {
    popup.hidden = true;
    try { localStorage.setItem('seen_welcome', '1'); } catch (e) {}
  };
  const closeBtn = $('#welcomeClose'), gotItBtn = $('#welcomeGotIt');
  if (closeBtn) closeBtn.addEventListener('click', dismiss);
  if (gotItBtn) gotItBtn.addEventListener('click', dismiss);
})();

async function boot() {
  // Public-first: render the dashboard for everyone, then check session in the
  // background to flip the header into logged-in mode if a cookie is present.
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
          <button class="fund-fs" data-sym="${esc(f.symbol)}"
                  title="Plain-English summary of this fund's official filing">📄 Fact sheet</button>
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
      <div class="fund-amt-row">
        <label for="amt-${esc(f.symbol)}" class="muted">You hold</label>
        <input class="fund-amt" id="amt-${esc(f.symbol)}" data-sym="${esc(f.symbol)}"
               type="number" min="0" step="any" inputmode="decimal"
               placeholder="optional" value="${f.amount != null ? esc(String(f.amount)) : ''}" />
      </div>
      <div class="fund-fs-panel" id="ffs-${esc(f.symbol)}" style="display:none"></div>
      <button class="fund-detail-btn" data-sym="${esc(f.symbol)}">Details ▾</button>
      <div class="fund-detail-panel" id="fdp-${esc(f.symbol)}" style="display:none"></div>
    </div>`;
}

// ── Fact sheet ────────────────────────────────────────────────────────────────

// Backend stage -> what the user is actually waiting for. Building a fact sheet
// takes 30-90s, and a static spinner that long reads as broken.
const _FS_PROGRESS = {
  queued:      'Queued…',
  fetching:    "Finding the fund's official filing…",
  parsing:     'Reading the prospectus…',
  indexing:    'Indexing the filing…',
  summarizing: 'Writing the plain-English summary…',
};
// _loadDrivers polls forever; this doesn't. Ingest can legitimately fail, and
// an endless poll is a silent battery and quota drain on an abandoned tab.
const _FS_MAX_POLLS = 10;

function _fsCiteLink(cite, citations) {
  if (!cite) return '';
  const n = parseInt(String(cite).replace(/^S/, ''), 10);
  const c = (citations || []).find(x => x.n === n);
  if (!c) return '';
  const label = [c.form_type, c.heading].filter(Boolean).join(' · ');
  return ` <a class="fs-cite" href="${esc(c.url)}" target="_blank" rel="noopener"
             title="${esc(label)}">[${n}]</a>`;
}

function _fsRender(sym, d) {
  const s = d.summary || {};
  const sections = (s.sections || []).map((sec, i) => `
    <details class="fs-section" ${i < 2 ? 'open' : ''}>
      <summary>${esc(sec.title)}</summary>
      <ul>${(sec.bullets || []).map(b =>
        `<li>${esc(b.text)}${_fsCiteLink(b.cite, d.citations)}</li>`).join('')}</ul>
    </details>`).join('');

  const jargon = (s.jargon || []).length ? `
    <details class="fs-jargon">
      <summary>Jargon, in plain English (${s.jargon.length})</summary>
      <dl>${s.jargon.map(j =>
        `<dt>${esc(j.term)}</dt><dd>${esc(j.plain)}</dd>`).join('')}</dl>
    </details>` : '';

  // Provenance is never optional: a summary without the filing it came from
  // is just an assertion.
  const src = (d.documents || []).map(doc =>
    `<a href="${esc(doc.url)}" target="_blank" rel="noopener">${esc(doc.form_type)}</a>${
      doc.filed_date ? ' filed ' + esc(doc.filed_date) : ''}`).join(' · ');

  const notes = (d.notes || []).length
    ? `<p class="fs-notes muted">${esc(d.notes.join(' '))}</p>` : '';

  return `
    <p class="fs-headline">${esc(s.headline || '')}</p>
    ${sections}
    ${jargon}
    ${notes}
    <p class="fs-source muted">Source: ${src || 'the fund’s own filing'}
      — summarised automatically. Analysis, not investment advice.</p>
    <div class="fs-ask">
      <input id="fsq-${esc(sym)}" type="text" maxlength="500"
             placeholder="Ask about this fact sheet — e.g. what are the risks?" />
      <button class="fs-ask-btn" data-sym="${esc(sym)}">Ask</button>
    </div>
    <div class="fs-answers" id="fsa-${esc(sym)}"></div>`;
}

async function _loadFactsheet(sym, attempt) {
  const panel = document.getElementById('ffs-' + sym);
  if (!panel) return;
  attempt = attempt || 0;
  if (!attempt) panel.innerHTML = waitingHtml('Opening the fact sheet…', 'factsheet');

  try {
    const d = await getJSON(`/api/funds/${encodeURIComponent(sym)}/factsheet`);

    if (d.status === 'ready') {
      panel.innerHTML = _fsRender(sym, d);
      const btn = panel.querySelector('.fs-ask-btn');
      const input = panel.querySelector(`#fsq-${CSS.escape(sym)}`);
      if (btn) btn.addEventListener('click', () => _askFactsheet(sym));
      if (input) input.addEventListener('keydown', e => {
        if (e.key === 'Enter') _askFactsheet(sym);
      });
      return;
    }

    if (d.status === 'unavailable') {
      panel.innerHTML = `<div class="empty">${esc((d.notes || []).join(' ') ||
        'No source document is available for this fund.')}</div>`;
      return;
    }

    if (attempt >= _FS_MAX_POLLS) {
      panel.innerHTML = `<div class="empty">Still working on this one —
        close and reopen the fact sheet in a minute.</div>`;
      // Clear the fetch-once flag, or reopening does nothing and the advice
      // above is a dead end.
      delete panel.dataset.loaded;
      return;
    }
    panel.innerHTML = waitingHtml(_FS_PROGRESS[d.status] || 'Working…', 'factsheet');
    setTimeout(() => _loadFactsheet(sym, attempt + 1), 12000);
  } catch (e) {
    panel.innerHTML = `<div class="empty">Could not load the fact sheet: ${esc(e.message)}</div>`;
  }
}

async function _askFactsheet(sym) {
  const input = document.getElementById('fsq-' + sym);
  const out = document.getElementById('fsa-' + sym);
  if (!input || !out) return;
  const q = input.value.trim();
  if (!q) return;
  input.value = '';

  const row = document.createElement('div');
  row.className = 'fs-qa';
  row.innerHTML = `<p class="fs-q">${esc(q)}</p><p class="fs-a loading">Reading the filing…</p>`;
  out.appendChild(row);

  try {
    const r = await postJSON(`/api/funds/${encodeURIComponent(sym)}/factsheet/ask`,
                             { question: q });
    const cites = (r.citations || []).map(c =>
      `<a class="fs-cite" href="${esc(c.url)}" target="_blank" rel="noopener"
          title="${esc([c.form_type, c.heading].filter(Boolean).join(' · '))}">[${c.n}]</a>`).join(' ');
    row.querySelector('.fs-a').outerHTML =
      `<p class="fs-a">${esc(r.answer)}</p>` +
      (cites ? `<p class="fs-cites muted">Sources: ${cites}</p>` : '');
  } catch (e) {
    row.querySelector('.fs-a').outerHTML =
      `<p class="fs-a muted">Could not answer that: ${esc(e.message)}</p>`;
  }
}

function _toggleFactsheet(sym) {
  const panel = document.getElementById('ffs-' + sym);
  if (!panel) return;
  const open = panel.style.display !== 'none';
  panel.style.display = open ? 'none' : 'block';
  if (!open && !panel.dataset.loaded) {
    panel.dataset.loaded = '1';
    _loadFactsheet(sym, 0);
  }
}

async function _loadDrivers(sym, period, isRetry) {
  const body = document.querySelector(`#drv-${CSS.escape(sym)} .drv-body`);
  if (!body) return;
  if (!isRetry) body.innerHTML = waitingHtml('Analyzing holdings…', 'drivers');
  try {
    const d = await getJSON(`/api/funds/${encodeURIComponent(sym)}/drivers?period=${period}`);
    if (d.status === 'computing') {
      body.innerHTML = waitingHtml(
        "Fetching the fund's complete SEC portfolio and pricing every holding — "
        + 'this first run takes ~30s…', 'drivers');
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
  panel.innerHTML = waitingHtml('Loading…', 'holdings');
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
          <div class="drv-body">${waitingHtml('Analyzing holdings…', 'drivers')}</div>
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
  out.innerHTML = waitingHtml('Comparing…', 'compare');
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
  clearOverview();
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

  const xraySection = _currentUser
    ? '<div class="xray-section" id="xraySection"></div>' : '';

  $('#content').innerHTML = addBar + xraySection
    + '<h4 style="padding:0 0 8px">My tracked funds</h4>' + portfolioHtml + compareSection;

  if (_currentUser) _loadXray(0);

  document.getElementById('fundAddBtn').addEventListener('click', _addFund);
  document.getElementById('fundSymInput').addEventListener('keydown', e => { if (e.key === 'Enter') _addFund(); });
  document.getElementById('cmpBtn').addEventListener('click', _runCompare);
  $('#content').querySelectorAll('.fund-amt').forEach(inp => {
    inp.addEventListener('change', () => _saveFundAmount(inp.dataset.sym, inp.value));
    inp.addEventListener('keydown', e => { if (e.key === 'Enter') inp.blur(); });
  });

  const fundSignIn = document.getElementById('fundSignIn');
  if (fundSignIn) fundSignIn.addEventListener('click', () => showAuth());

  $('#content').querySelectorAll('.fund-detail-btn').forEach(btn =>
    btn.addEventListener('click', () => _toggleFundDetail(btn.dataset.sym)));
  $('#content').querySelectorAll('.fund-rm').forEach(btn =>
    btn.addEventListener('click', () => _removeFund(btn.dataset.sym)));
  $('#content').querySelectorAll('.fund-fs').forEach(btn =>
    btn.addEventListener('click', () => _toggleFactsheet(btn.dataset.sym)));
}

// ── Portfolio X-Ray ───────────────────────────────────────────────────────────
// What you actually own once the funds are collapsed into the securities
// underneath them: overlap, real fee cost, concentration.

const _XRAY_MAX_POLLS = 10;   // capped, like the fact sheet — ingest can fail

// Currency figures are v1-US-only, matching the SEC N-PORT holdings the X-Ray
// is built from. Always 2dp — "$28.5 a year in fees" reads like a broken
// number rather than a real one.
function _xrayMoney(n) {
  return '$' + n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function _xrayBar(pct) {
  const p = Math.max(0, Math.min(100, pct));
  return `<div class="xray-bar"><span class="xray-bar-fill" style="width:${p.toFixed(1)}%"></span></div>`;
}

function _xrayRender(d) {
  if (d.status === 'empty') {
    return `<div class="empty">${esc((d.notes || []).join(' '))}</div>`;
  }

  const overlap = d.overlap_pct || 0;
  // Only ever shown when the user entered amounts for every fund — the API
  // omits money entirely otherwise rather than guessing a portfolio size.
  const money = (d.annual_fee != null)
    ? `<div class="xray-stat">
         <div class="xray-stat-val">${esc(_xrayMoney(d.annual_fee))}</div>
         <div class="xray-stat-lbl">a year in fees${
           d.fee_on_overlap ? ` · ${esc(_xrayMoney(d.fee_on_overlap))} of it on duplicated holdings` : ''}</div>
       </div>` : '';

  const dupes = (d.duplicated || []).map(h => `
    <div class="xray-row">
      <span class="xray-name">${esc(h.name)}</span>
      <span class="xray-held">${esc((h.held_by || []).join(' · '))}</span>
      <span class="xray-wt num">${h.effective_weight_pct.toFixed(2)}%</span>
    </div>`).join('');

  const fee = d.blended_expense_ratio != null
    ? `<span class="num">${d.blended_expense_ratio}%</span> blended expense ratio` : '';
  const conc = d.concentration_top10_pct
    ? `Top 10 positions are <span class="num">${d.concentration_top10_pct.toFixed(1)}%</span> of everything you hold` : '';

  const notes = (d.notes || []).length
    ? `<p class="xray-notes muted">${esc(d.notes.join(' '))}</p>` : '';

  return `
    <div class="xray-head">
      <h4>Portfolio X-Ray</h4>
      <span class="muted">${d.fund_count} fund${d.fund_count === 1 ? '' : 's'}${
        d.amount_weighted ? ' · weighted by your amounts' : ' · equal-weighted'}</span>
    </div>
    <div class="xray-headline">
      <span class="xray-big num">${overlap.toFixed(0)}%</span>
      <span>of your money is in positions held by more than one of your funds</span>
    </div>
    ${_xrayBar(overlap)}
    <div class="xray-stats">
      ${fee ? `<div class="xray-stat"><div class="xray-stat-val">${fee}</div></div>` : ''}
      ${money}
      ${conc ? `<div class="xray-stat"><div class="xray-stat-val">${conc}</div></div>` : ''}
    </div>
    ${dupes ? `<div class="xray-dupes">
        <div class="xray-cols muted"><span>Held by more than one fund</span><span>In</span><span>Your exposure</span></div>
        ${dupes}
      </div>` : ''}
    ${notes}`;
}

async function _loadXray(attempt) {
  const box = document.getElementById('xraySection');
  if (!box) return;
  attempt = attempt || 0;
  if (!attempt) box.innerHTML = waitingHtml('Working out what you actually own…', 'xray');

  try {
    const d = await getJSON('/api/funds/portfolio/xray');
    if (d.status === 'computing') {
      if (attempt >= _XRAY_MAX_POLLS) {
        box.innerHTML = '<div class="empty">Still reading your funds’ holdings — reopen this tab shortly.</div>';
        return;
      }
      box.innerHTML = waitingHtml('Reading the full holdings of each fund…', 'xray');
      setTimeout(() => _loadXray(attempt + 1), 8000);
      return;
    }
    box.innerHTML = _xrayRender(d);
  } catch (e) {
    if (e.auth) { box.innerHTML = ''; return; }
    box.innerHTML = `<div class="empty">Could not build the X-Ray: ${esc(e.message)}</div>`;
  }
}

async function _saveFundAmount(sym, raw) {
  const trimmed = (raw || '').trim();
  const amount = trimmed === '' ? null : Number(trimmed);
  if (amount !== null && (!isFinite(amount) || amount < 0)) {
    $('#status').textContent = 'Enter a positive amount, or leave it blank.';
    return;
  }
  try {
    await postJSON(`/api/funds/${encodeURIComponent(sym)}/amount`, { amount }, 'PATCH');
    // The amount changes every weighting, so rebuild rather than show a stale one.
    _loadXray(0);
  } catch (e) {
    if (e.auth) return;
    $('#status').textContent = 'Could not save that amount: ' + e.message;
  }
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
  return `<span class="cell" style="background:color-mix(in srgb, var(--series-1) ${Math.round(alpha * 100)}%, transparent)"
    title="${v.toFixed(2)}% errors"></span>`;
}

function _fmtUptime(seconds) {
  if (seconds == null) return '—';
  const d = Math.floor(seconds / 86400), h = Math.floor((seconds % 86400) / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  return d > 0 ? `${d}d ${h}h` : h > 0 ? `${h}h ${m}m` : `${m}m`;
}

async function loadSRE() {
  clearOverview();
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
          ${srcRow('🛈 Out of scope (guardrail)', bySrc['out-of-scope'] || 0, '')}
          ${srcRow('🎓 Advice declined', bySrc['advice-declined'] || 0, '')}`
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
function _rememberSearch(sym, name, type) {
  if (!sym) return;
  const list = _getRecentSearches().filter((r) => r.symbol !== sym);
  // Keep the type: without it a fund picked from recents would route to the
  // stock view, which is exactly what the type field exists to prevent.
  list.unshift({ symbol: sym, name: name || '', type: type || 'stock' });
  try { localStorage.setItem(_RECENT_KEY, JSON.stringify(list.slice(0, 8))); } catch (e) {}
}

// A mutual fund has no meaningful stock detail page, so it always goes to the
// Funds tab. An ETF trades like a stock and has a perfectly good overview, so
// it only goes to Funds when the user actually tracks it — otherwise routing it
// there would dead-end on a status line instead of showing anything.
function openSearchResult(sym, type) {
  if (type === 'fund') { openFundSymbol(sym, true); return; }
  if (type === 'etf')  { openFundSymbol(sym, false); return; }
  openSymbol(sym);
}

async function openFundSymbol(sym, fallbackToAddBox) {
  view = 'funds';
  document.querySelectorAll('.tab').forEach(t =>
    t.classList.toggle('active', t.dataset.view === 'funds'));
  try { await loadFunds(); } catch (e) { /* handled by the missing-card path below */ }

  const card = document.getElementById('fc-' + sym);
  if (card) {
    card.scrollIntoView({ behavior: 'smooth', block: 'center' });
    _toggleFactsheet(sym);
    return;
  }
  if (!fallbackToAddBox) {
    openSymbol(sym);   // tradeable and untracked — the stock view still helps
    return;
  }
  // Not tracked: prefill the add box rather than silently doing nothing, since
  // the fact sheet lives on the fund card.
  const st = document.getElementById('status');
  if (st) st.textContent = `Add ${sym} to your funds to see its fact sheet.`;
  const input = document.getElementById('fundSymInput');
  if (input) { input.value = sym; input.focus(); }
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
    drop.innerHTML = heading + hits.map((r) => {
      const t = r.type || 'stock';
      const pill = (t === 'fund' || t === 'etf')
        ? `<span class="hit-type">${t === 'fund' ? 'Fund' : 'ETF'}</span>` : '';
      return `
      <button class="search-hit" data-sym="${esc(r.symbol)}" data-name="${esc(r.name || '')}"
              data-type="${esc(t)}">
        <span class="sym">${esc(r.symbol)}</span>
        <span class="nm">${esc(r.name || '')}</span>${pill}</button>`;
    }).join('');
    drop.classList.add('open');
    drop.querySelectorAll('.search-hit').forEach((b) =>
      b.addEventListener('mousedown', (e) => {
        e.preventDefault();
        _rememberSearch(b.dataset.sym, b.dataset.name, b.dataset.type);
        close(); inp.value = '';
        openSearchResult(b.dataset.sym, b.dataset.type);
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
        // Mirror the mousedown handler above: route by type, or a keyboard
        // Enter on a fund/ETF result dead-ends on the stock detail view while
        // the identical mouse click correctly opens the Funds tab.
        _rememberSearch(first.dataset.sym, first.dataset.name, first.dataset.type);
        close(); inp.value = '';
        openSearchResult(first.dataset.sym, first.dataset.type);
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

// ── Ask-AI suggestion chips (market-aware + randomized) ──────────────────────
// Example prompts follow the selected market (India users see Reliance/Infosys,
// not Apple/Tesla) and a fresh random 4 are drawn each time the chat opens, so
// the site feels alive rather than static.
const CHAT_SUGGEST = {
  us: [
    ['Strongest buys', 'Which stocks have the strongest buy consensus?'],
    ['Apple fundamentals', 'What are the fundamentals of Apple?'],
    ['Tesla news', "What's the latest news on Tesla?"],
    ['Best hit rates', 'Which analysts have the best target hit rates?'],
    ['Most covered', 'Which stocks have the most analyst coverage?'],
    ['Highest upside', 'Which stocks have the highest upside to their target?'],
    ['NVIDIA view', "How's NVDA rated and why?"],
    ['Microsoft news', "What's the latest news on Microsoft?"],
    ['Most bearish', 'Which stocks are the most sell-rated right now?'],
    ['Amazon fundamentals', 'What are the fundamentals of Amazon?'],
  ],
  in: [
    ['Strongest buys', 'Which stocks have the strongest buy consensus?'],
    ['Reliance fundamentals', 'What are the fundamentals of Reliance Industries?'],
    ['Infosys news', "What's the latest news on Infosys?"],
    ['Best hit rates', 'Which analysts have the best target hit rates?'],
    ['Most covered', 'Which stocks have the most analyst coverage?'],
    ['TCS view', "How's TCS rated and why?"],
    ['HDFC Bank news', "What's the latest news on HDFC Bank?"],
    ['Highest upside', 'Which stocks have the highest upside to their target?'],
    ['Most bearish', 'Which stocks are the most sell-rated right now?'],
    ['Tata Motors fundamentals', 'What are the fundamentals of Tata Motors?'],
  ],
};

function renderChatSuggest() {
  const box = document.getElementById('chatSuggest');
  if (!box) return;
  const pool = (CHAT_SUGGEST[currentMarket()] || CHAT_SUGGEST.us).slice();
  // Fisher–Yates shuffle, then take 4 — a fresh mix every time the chat opens.
  for (let i = pool.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [pool[i], pool[j]] = [pool[j], pool[i]];
  }
  box.innerHTML = '';
  pool.slice(0, 4).forEach(([label, q]) => {
    const b = document.createElement('button');
    b.type = 'button';
    b.textContent = label;
    b.dataset.q = q;
    b.addEventListener('click', () => {
      document.getElementById('chatText').value = q;
      document.getElementById('chatForm').requestSubmit();
    });
    box.appendChild(b);
  });
}
renderChatSuggest();
