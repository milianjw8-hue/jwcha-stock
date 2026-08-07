/* 주도주 스윙 콘솔 — 모바일 PWA
 * 외부 의존성 없음. 차트는 canvas 로 직접 그린다.
 * 룰셋 평가기(evalCond/scoreRuleset)는 rules/rules.py 와 동일 규칙이어야 한다. */
(function () {
"use strict";

/* ══════════ 유틸 ══════════ */
var $ = function (id) { return document.getElementById(id); };
function esc(s) { var d = document.createElement("div"); d.textContent = String(s == null ? "" : s); return d.innerHTML; }
function nf(n, d) { return (n == null || isNaN(n)) ? "—" : Number(n).toLocaleString("ko-KR", { maximumFractionDigits: d == null ? 0 : d }); }
function pf(n, d) { return (n == null || isNaN(n)) ? "—" : (n >= 0 ? "+" : "") + Number(n).toFixed(d == null ? 1 : d) + "%"; }
function el(tag, cls, html) { var e = document.createElement(tag); if (cls) e.className = cls; if (html != null) e.innerHTML = html; return e; }

var toastT;
function toast(msg) {
  var t = $("toast"); t.textContent = msg; t.classList.add("on");
  clearTimeout(toastT); toastT = setTimeout(function () { t.classList.remove("on"); }, 2200);
}

/* ══════════ 저장소 — IndexedDB, 실패 시 localStorage ══════════ */
var DB = null, DBN = "swing_console", ST = "kv", memFallback = {};
function openDB() {
  return new Promise(function (res) {
    if (DB) return res(DB);
    if (!window.indexedDB) return res(null);
    var rq;
    try { rq = indexedDB.open(DBN, 1); } catch (e) { return res(null); }
    rq.onupgradeneeded = function () { rq.result.createObjectStore(ST); };
    rq.onsuccess = function () { DB = rq.result; res(DB); };
    rq.onerror = function () { res(null); };
  });
}
function lsGet(k) { try { return JSON.parse(localStorage.getItem(k)); } catch (e) { return memFallback[k]; } }
function lsSet(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) { memFallback[k] = v; } }
var kv = {
  get: function (k, dflt) {
    return openDB().then(function (db) {
      if (!db) { var v = lsGet(k); return v == null ? dflt : v; }
      return new Promise(function (res) {
        var rq = db.transaction(ST, "readonly").objectStore(ST).get(k);
        rq.onsuccess = function () { res(rq.result === undefined ? dflt : rq.result); };
        rq.onerror = function () { res(dflt); };
      });
    });
  },
  set: function (k, v) {
    return openDB().then(function (db) {
      if (!db) { lsSet(k, v); return; }
      return new Promise(function (res) {
        var tx = db.transaction(ST, "readwrite");
        tx.objectStore(ST).put(v, k);
        tx.oncomplete = tx.onerror = function () { res(); };
      });
    });
  }
};

/* ══════════ 상태 ══════════ */
var S = {
  market: "us",
  page: "today",
  scan: { us: null, kr: null },
  rules: { us: null, kr: null, exit: null },
  bt: { us: null, kr: null },
  ohlc: {},              // slug -> payload
  positions: [],
  journal: [],
  watch: [],
  manual: {},            // "mkt:ticker" -> {itemId: bool}
  sel: { chart: "", check: "" }
};
function key(mkt, t) { return mkt + ":" + t; }
function slug(mkt, t) { return mkt + "_" + String(t).replace(/[^A-Za-z0-9._-]/g, "_"); }

/* ══════════ 룰셋 평가기 (rules/rules.py 와 동일 규칙) ══════════ */
function evalCond(c, m) {
  if (!c || typeof c !== "object") return null;
  var i, r, sawNull = false;
  if (c.all) {
    for (i = 0; i < c.all.length; i++) { r = evalCond(c.all[i], m); if (r === false) return false; if (r === null) sawNull = true; }
    return sawNull ? null : true;
  }
  if (c.any) {
    for (i = 0; i < c.any.length; i++) { r = evalCond(c.any[i], m); if (r === true) return true; if (r === null) sawNull = true; }
    return sawNull ? null : false;
  }
  if (c.not) { r = evalCond(c.not, m); return r === null ? null : !r; }
  if (c.m == null) return null;
  var left = m ? m[c.m] : undefined;
  if (left === undefined || left === null) return null;
  var right;
  if ("m2" in c) { right = m[c.m2]; if (right === undefined || right === null) return null; }
  else if ("v" in c) { right = c.v; }
  else return null;
  // bool 과 숫자를 섞어 비교하면 조용히 잘못된 결과가 나온다
  if ((typeof left === "boolean") !== (typeof right === "boolean")) return null;
  switch (c.op) {
    case ">": return left > right;
    case ">=": return left >= right;
    case "<": return left < right;
    case "<=": return left <= right;
    case "==": return left === right;
    case "!=": return left !== right;
  }
  return null;
}
function scoreRuleset(rs, metrics, manual) {
  manual = manual || {};
  var got = 0, tot = 0, coreMiss = [], autoN = 0, items = {};
  (rs.groups || []).forEach(function (g) {
    (g.items || []).forEach(function (it) {
      var w = Number(it.w) || 0; tot += w;
      var res = it.auto ? evalCond(it.auto, metrics) : null;
      var src = res === null ? "manual" : "auto";
      if (res === null) res = !!manual[it.id]; else autoN++;
      if (res) got += w; else if (it.core) coreMiss.push(it.id);
      items[it.id] = { ok: res, src: src };
    });
  });
  var pct = tot ? Math.round(got / tot * 100) : 0;
  var v = rs.verdict || { go: 85, wait: 70 };
  var verdict = coreMiss.length ? "핵심 미충족" : pct >= v.go ? "진입 가능" : pct >= v.wait ? "관망" : "제외";
  return { pct: pct, verdict: verdict, coreMiss: coreMiss, auto: autoN, items: items, total: Object.keys(items).length };
}

/* ══════════ 데이터 로드 ══════════ */
function getJSON(url) {
  return fetch(url, { cache: "no-cache" }).then(function (r) { return r.ok ? r.json() : null; }).catch(function () { return null; });
}
function loadAll() {
  return Promise.all([
    getJSON("results_us.json"), getJSON("results.json"),
    getJSON("rules/us_minervini.json"), getJSON("rules/kr_leader.json"), getJSON("rules/exit_rules.json"),
    getJSON("backtest_us.json"), getJSON("backtest_kr.json")
  ]).then(function (r) {
    S.scan.us = r[0]; S.scan.kr = r[1];
    S.rules.us = r[2]; S.rules.kr = r[3]; S.rules.exit = r[4];
    S.bt.us = r[5]; S.bt.kr = r[6];
    renderAll();
  });
}
function candidates(mkt) { var s = S.scan[mkt]; return (s && s.candidates) || []; }
// 보유 종목 — 스캔을 통과하지 못해도 스캐너가 지표를 실어준다 (예전 결과 파일엔 없다)
function holdings(mkt) { var s = S.scan[mkt]; return (s && s.holdings) || []; }
function pick(list, t) {
  for (var i = 0; i < list.length; i++) if (list[i].ticker === t) return list[i];
  return null;
}
function findCand(mkt, t) { return pick(candidates(mkt), t); }
// 오늘 후보이면서 동시에 보유일 수 있다. 그때는 수급 지표까지 채워진 후보 쪽이 낫다.
function findData(mkt, t) { return findCand(mkt, t) || pick(holdings(mkt), t); }
function nameOf(c) { return c.name || c.ticker; }
function loadOhlc(mkt, t) {
  var sl = slug(mkt, t);
  if (S.ohlc[sl]) return Promise.resolve(S.ohlc[sl]);
  return getJSON("ohlc/" + sl + ".json").then(function (d) { if (d) S.ohlc[sl] = d; return d; });
}

/* ══════════ 지표 배지 ══════════ */
function badges(m, compact) {
  if (!m) return [];
  var out = [];
  var volOk = m.vol_confirmed !== false;
  function add(txt, cls) { out.push({ t: txt, c: (cls || "") + (volOk ? "" : " mute") }); }
  if (m.vol_ratio_20 != null) add("거래량 x" + m.vol_ratio_20, m.vol_ratio_20 >= 2 ? "go" : m.vol_ratio_20 >= 1.5 ? "" : "mute");
  if (m.pct_of_52wk_high != null) out.push({ t: "고점 " + m.pct_of_52wk_high + "%", c: m.pct_of_52wk_high >= 95 ? "go" : "" });
  if (m.breakout_60) add("🔥60일 돌파", "go");
  if (m.bb_squeeze) add("🔵스퀴즈");
  if (m.band_state === "walk") add("🟢밴드워킹", "go");
  if (m.band_state === "warn") add("🟠상단 경계", "warn");
  if (m.cup_handle) add("☕컵앤핸들", "go");
  if (m.double_bottom) add("W쌍바닥", m.db_confirmed ? "go" : "");
  if (m.vcp_shrinking) add("VCP " + (m.vcp_depths || []).join("→") + "%", "go");
  if (m.retest_level) add("↩리테스트 " + nf(m.retest_level, 2));
  if (m.three_white_soldiers) add("적삼병");
  if (m.morning_star) add("모닝스타");
  if (m.ma_aligned_bull) add("정배열", "go");
  if (m.ma_aligned_bear) add("역배열", "bad");
  if (m.disparity20_pctile != null && m.disparity20_pctile >= 90) add("이격 과열 " + m.disparity20_pctile + "%ile", "warn");
  // 경보는 거래량 검증과 무관하게 항상 진하게
  if (m.distribution_alert) out.push({ t: "🔴분산 경보", c: "bad" });
  if (m.topping_alert) out.push({ t: "⚠천정 패턴", c: "bad" });
  if (m.evening_star) out.push({ t: "이브닝스타", c: "bad" });
  if (m.three_black_crows) out.push({ t: "흑삼병", c: "bad" });
  return compact ? out.slice(0, 5) : out;
}
function chipsHTML(list) {
  return list.map(function (b) { return '<span class="chip ' + b.c + '">' + esc(b.t) + "</span>"; }).join("");
}

/* ══════════ 오늘 탭 ══════════ */
function renderToday() {
  var s = S.scan[S.market];
  $("asof").textContent = s ? (S.market === "us" ? "미장" : "국장") + " · " + s.asof : "스캔 결과 없음 (Actions 첫 실행 전)";

  var lt = $("market-light");
  var ok = s && s.market_ok;
  lt.className = "light " + (s ? (ok ? "ok" : "no") : "");
  lt.querySelector(".txt").innerHTML = s
    ? (ok ? "시장 필터 통과" : "시장 필터 미통과 — 현금 우선") +
      '<span class="sub2">' + (ok ? "셋업을 봐도 되는 국면" : "지수가 막히면 셋업보다 현금이 먼저다") + "</span>"
    : '스캔 결과 없음<span class="sub2">GitHub Actions 가 아직 실행되지 않았다</span>';

  var list = candidates(S.market);
  $("cand-count").textContent = list.length ? list.length + "종목" : "";
  var box = $("cands"); box.innerHTML = "";
  if (!list.length) { box.appendChild(el("p", "empty", "후보 없음")); }
  list.forEach(function (c) {
    var m = c.metrics || {};
    var b = el("button", "cand");
    var r = c.rule || {};
    var vcls = r.verdict === "진입 가능" ? "go" : r.verdict === "관망" ? "warn" : "";
    b.innerHTML =
      '<div class="top"><span class="nm">' + esc(nameOf(c)) + "</span>" +
      (r.pct != null ? '<span class="chip ' + vcls + '">' + r.pct + "% " + esc(r.verdict) + "</span>" : "") +
      '<span class="px">' + nf(c.close, S.market === "us" ? 2 : 0) + "</span></div>" +
      '<div class="met">' + chipsHTML(badges(m, true)) + "</div>";
    b.addEventListener("click", function () { openChart(S.market, c.ticker); });
    box.appendChild(b);
  });

  renderTodo();
}

function renderTodo() {
  var box = $("todo"), items = [];
  var s = S.scan[S.market];

  S.positions.filter(function (p) { return p.mkt === S.market; }).forEach(function (p) {
    var v = evalPosition(p);
    (v.alerts || []).forEach(function (a) { items.push({ t: "⚠ " + p.tick + " — " + a, c: "bad" }); });
  });

  if (s && !s.market_ok) items.push({ t: "시장 필터 미통과 — 신규 진입 보류", c: "warn" });

  candidates(S.market).forEach(function (c) {
    var m = c.metrics || {}, r = c.rule || {};
    if (r.verdict === "진입 가능") items.push({ t: "✓ " + nameOf(c) + " 룰 " + r.pct + "% — 진입 검토", c: "go" });
    else if (m.bb_squeeze && m.vol_confirmed) items.push({ t: "🔵 " + nameOf(c) + " 스퀴즈 — 방향 분출 대기", c: "" });
    else if (m.retest_level) items.push({ t: "↩ " + nameOf(c) + " 리테스트 " + nf(m.retest_level, 2) + " — 지지 확인 시 진입", c: "" });
  });

  box.innerHTML = "";
  $("todo-count").textContent = items.length ? items.length + "건" : "";
  if (!items.length) { box.appendChild(el("p", "empty", "지금 할 일 없음")); return; }
  items.slice(0, 12).forEach(function (i) {
    box.appendChild(el("p", "rule", '<span class="chip ' + i.c + '">' + esc(i.t) + "</span>"));
  });
}

/* ══════════ 차트 (canvas 직접 렌더) ══════════ */
var CH = { data: null, metrics: null, n: 70, off: 0, cursor: -1, mkt: "us" };
var COL = { ma20: "#F2B33D", ma60: "#4EA3FF", ma120: "#8FAEB8", bb: "#7A6BD8", sup: "#2ED47A", res: "#FF5B6E" };

function sma(arr, p) {
  var out = new Array(arr.length).fill(null), sum = 0;
  for (var i = 0; i < arr.length; i++) {
    sum += arr[i];
    if (i >= p) sum -= arr[i - p];
    if (i >= p - 1) out[i] = sum / p;
  }
  return out;
}
function bollinger(arr, p, k) {
  var mid = sma(arr, p), up = [], dn = [];
  for (var i = 0; i < arr.length; i++) {
    if (mid[i] == null) { up.push(null); dn.push(null); continue; }
    var s = 0;
    for (var j = i - p + 1; j <= i; j++) s += Math.pow(arr[j] - mid[i], 2);
    var sd = Math.sqrt(s / p);
    up.push(mid[i] + k * sd); dn.push(mid[i] - k * sd);
  }
  return { mid: mid, up: up, dn: dn };
}

function drawChart() {
  var cv = $("chart"), d = CH.data;
  if (!cv) return;
  var dpr = window.devicePixelRatio || 1;
  var W = cv.clientWidth || 340, H = Math.round(Math.min(Math.max(W * 0.82, 260), 420));
  cv.width = W * dpr; cv.height = H * dpr; cv.style.height = H + "px";
  var g = cv.getContext("2d");
  g.setTransform(dpr, 0, 0, dpr, 0, 0);
  g.clearRect(0, 0, W, H);
  if (!d) {
    g.fillStyle = "#8FAEB8"; g.font = "13px system-ui"; g.textAlign = "center";
    g.fillText(CH.missing ? "일봉 데이터 없음 — 다음 스캔에서 생성된다" : "종목을 선택하세요", W / 2, H / 2);
    return;
  }

  var N = d.c.length;
  var n = Math.max(20, Math.min(CH.n, N));
  var end = N - CH.off, start = Math.max(0, end - n);
  end = Math.min(N, start + n);

  var PL = 6, PR = 52, PT = 42, VH = Math.round(H * 0.20), GAP = 8;
  var PH = H - PT - VH - GAP - 6;
  var cw = (W - PL - PR) / (end - start);

  var ma20 = sma(d.c, 20), ma60 = sma(d.c, 60), ma120 = sma(d.c, 120), bb = bollinger(d.c, 20, 2);

  var lo = Infinity, hi = -Infinity;
  for (var i = start; i < end; i++) {
    lo = Math.min(lo, d.l[i], bb.dn[i] == null ? Infinity : bb.dn[i]);
    hi = Math.max(hi, d.h[i], bb.up[i] == null ? -Infinity : bb.up[i]);
  }
  var pad = (hi - lo) * 0.06 || 1; lo -= pad; hi += pad;
  var Y = function (p) { return PT + PH - (p - lo) / (hi - lo) * PH; };
  var X = function (i) { return PL + (i - start + 0.5) * cw; };

  // 가격 눈금
  g.strokeStyle = "rgba(38,80,95,.45)"; g.fillStyle = "#8FAEB8";
  g.font = "10px ui-monospace,monospace"; g.textAlign = "left"; g.lineWidth = 1;
  for (var k = 0; k <= 4; k++) {
    var p = lo + (hi - lo) * k / 4, y = Y(p);
    g.beginPath(); g.moveTo(PL, y); g.lineTo(W - PR, y); g.stroke();
    g.fillText(nf(p, p < 100 ? 2 : 0), W - PR + 5, y + 3);
  }

  // 볼린저 밴드 음영
  g.fillStyle = "rgba(122,107,216,.13)";
  g.beginPath();
  var began = false;
  for (i = start; i < end; i++) { if (bb.up[i] == null) continue; if (!began) { g.moveTo(X(i), Y(bb.up[i])); began = true; } else g.lineTo(X(i), Y(bb.up[i])); }
  for (i = end - 1; i >= start; i--) { if (bb.dn[i] == null) continue; g.lineTo(X(i), Y(bb.dn[i])); }
  if (began) { g.closePath(); g.fill(); }

  // 지지·저항
  var lv = (CH.metrics && CH.metrics.sr_levels) || [];
  g.setLineDash([4, 4]); g.lineWidth = 1;
  lv.forEach(function (L) {
    if (L.price < lo || L.price > hi) return;
    g.strokeStyle = L.kind === "support" ? "rgba(46,212,122,.55)" : "rgba(255,91,110,.55)";
    g.beginPath(); g.moveTo(PL, Y(L.price)); g.lineTo(W - PR, Y(L.price)); g.stroke();
  });
  g.setLineDash([]);

  // 이동평균선
  function line(arr, color) {
    g.strokeStyle = color; g.lineWidth = 1.4; g.beginPath();
    var st = false;
    for (var i = start; i < end; i++) {
      if (arr[i] == null) continue;
      if (!st) { g.moveTo(X(i), Y(arr[i])); st = true; } else g.lineTo(X(i), Y(arr[i]));
    }
    g.stroke();
  }
  line(ma120, COL.ma120); line(ma60, COL.ma60); line(ma20, COL.ma20);

  // 캔들 + 거래량
  var vmax = 0;
  for (i = start; i < end; i++) vmax = Math.max(vmax, d.v[i] || 0);
  var VY = PT + PH + GAP;
  var bw = Math.max(1, Math.min(cw * 0.68, 14));
  for (i = start; i < end; i++) {
    var up = d.c[i] >= d.o[i];
    var col = up ? (CH.mkt === "kr" ? "#FF4D5E" : "#2ED47A") : (CH.mkt === "kr" ? "#4EA3FF" : "#FF5B6E");
    var x = X(i);
    g.strokeStyle = col; g.fillStyle = col; g.lineWidth = 1;
    g.beginPath(); g.moveTo(x, Y(d.h[i])); g.lineTo(x, Y(d.l[i])); g.stroke();
    var yo = Y(d.o[i]), yc = Y(d.c[i]);
    g.fillRect(x - bw / 2, Math.min(yo, yc), bw, Math.max(1, Math.abs(yc - yo)));
    if (vmax > 0) {
      var vh = (d.v[i] || 0) / vmax * VH;
      g.globalAlpha = 0.5;
      g.fillRect(x - bw / 2, VY + VH - vh, bw, vh);
      g.globalAlpha = 1;
    }
  }
  // 거래량 20일 평균선
  var vma = sma(d.v, 20);
  g.strokeStyle = "rgba(242,179,61,.85)"; g.lineWidth = 1; g.beginPath();
  var vst = false;
  for (i = start; i < end; i++) {
    if (vma[i] == null || vmax <= 0) continue;
    var vy = VY + VH - vma[i] / vmax * VH;
    if (!vst) { g.moveTo(X(i), vy); vst = true; } else g.lineTo(X(i), vy);
  }
  g.stroke();

  // 크로스헤어
  var ci = CH.cursor;
  if (ci >= start && ci < end) {
    g.strokeStyle = "rgba(230,240,242,.45)"; g.setLineDash([3, 3]);
    g.beginPath(); g.moveTo(X(ci), PT); g.lineTo(X(ci), VY + VH); g.stroke();
    g.setLineDash([]);
  }
  var si = (ci >= start && ci < end) ? ci : end - 1;
  var info = $("chart-info");
  var chg = si > 0 ? (d.c[si] / d.c[si - 1] - 1) * 100 : 0;
  info.innerHTML =
    "<span>" + esc(d.d[si]) + "</span>" +
    "<span>종가 " + nf(d.c[si], 2) + " (" + pf(chg) + ")</span>" +
    "<span>거래량 " + nf(d.v[si]) + "</span>" +
    (ma20[si] != null ? "<span>MA20 " + nf(ma20[si], 2) + "</span>" : "");
}

function chartTouch() {
  var cv = $("chart"); if (!cv) return;
  var pts = {}, pinch0 = 0, n0 = 0, panX = 0, off0 = 0;
  function dist() {
    var k = Object.keys(pts); if (k.length < 2) return 0;
    return Math.abs(pts[k[0]].x - pts[k[1]].x);
  }
  cv.addEventListener("pointerdown", function (e) {
    cv.setPointerCapture(e.pointerId);
    pts[e.pointerId] = { x: e.clientX, y: e.clientY };
    if (Object.keys(pts).length === 2) { pinch0 = dist(); n0 = CH.n; }
    else { panX = e.clientX; off0 = CH.off; setCursor(e); }
  });
  cv.addEventListener("pointermove", function (e) {
    if (!pts[e.pointerId]) return;
    pts[e.pointerId] = { x: e.clientX, y: e.clientY };
    var k = Object.keys(pts);
    if (k.length >= 2 && pinch0 > 0) {
      var f = dist() / pinch0;
      CH.n = Math.max(20, Math.min(200, Math.round(n0 / f)));
    } else if (CH.data) {
      var W = cv.clientWidth, cw = (W - 58) / CH.n;
      var shift = Math.round((e.clientX - panX) / Math.max(cw, 1));
      CH.off = Math.max(0, Math.min(CH.data.c.length - 20, off0 + shift));
      setCursor(e);
    }
    drawChart();
  });
  function up(e) { delete pts[e.pointerId]; if (Object.keys(pts).length < 2) pinch0 = 0; }
  cv.addEventListener("pointerup", up);
  cv.addEventListener("pointercancel", up);
  function setCursor(e) {
    if (!CH.data) return;
    var r = cv.getBoundingClientRect(), W = cv.clientWidth;
    var n = Math.max(20, Math.min(CH.n, CH.data.c.length));
    var end = CH.data.c.length - CH.off, start = Math.max(0, end - n);
    var cw = (W - 58) / (end - start);
    CH.cursor = start + Math.floor((e.clientX - r.left - 6) / cw);
  }
  window.addEventListener("resize", drawChart);
}

function openChart(mkt, ticker) {
  if (mkt !== S.market) setMarket(mkt);
  S.sel.chart = ticker;
  $("chart-pick").value = ticker;
  go("chart");
  loadOhlc(mkt, ticker).then(function (d) {
    var c = findData(mkt, ticker);
    CH.data = d; CH.missing = !d; CH.metrics = c ? c.metrics : null;
    CH.mkt = mkt; CH.off = 0; CH.cursor = -1;
    if (!d) $("chart-info").innerHTML = "";
    drawChart(); renderChartMetrics(c);
  });
}

function renderChartMetrics(c) {
  var box = $("chart-metrics");
  if (!c || !c.metrics) {
    box.innerHTML = '<p class="empty">지표 없음 — 다음 스캔이 실행되면 채워진다.</p>';
    return;
  }
  var m = c.metrics;
  var rows = [
    ["이평선 배열", m.ma_aligned_bull ? "정배열" : m.ma_aligned_bear ? "역배열" : "혼조"],
    ["20일선 대비", pf(m.close_vs_ma20_pct)],
    ["이격도(20)", m.disparity20 == null ? "—" : m.disparity20 + " (" + (m.disparity20_pctile == null ? "—" : m.disparity20_pctile + "%ile") + ")"],
    ["거래량 / 20일평균", m.vol_ratio_20 == null ? "—" : "x" + m.vol_ratio_20 + (m.vol_confirmed ? " ✓" : " (미검증)")],
    ["52주 고점 대비", m.pct_of_52wk_high == null ? "—" : m.pct_of_52wk_high + "%"],
    ["볼린저 %B", m.bb_pctb == null ? "—" : m.bb_pctb + (m.band_state === "walk" ? " · 밴드워킹(보유)" : m.band_state === "warn" ? " · 상단 경계" : "")],
    ["밴드폭", m.bb_width_pctile == null ? "—" : m.bb_width_pctile + "%ile" + (m.bb_squeeze ? " · 스퀴즈" : "")],
    ["ATR(14)", m.atr14 == null ? "—" : nf(m.atr14, 2) + " (" + (m.atr_pct == null ? "—" : m.atr_pct + "%") + ")"],
    ["위꼬리 / 아래꼬리", (m.upper_wick_ratio == null ? "—" : Math.round(m.upper_wick_ratio * 100) + "%") + " / " + (m.lower_wick_ratio == null ? "—" : Math.round(m.lower_wick_ratio * 100) + "%")],
    ["피봇", m.pivot == null ? "—" : nf(m.pivot, 2) + " (" + pf(m.dist_to_pivot_pct) + ")"],
    ["골든/데드 크로스", (m.golden_cross_days != null ? "골든 " + m.golden_cross_days + "일 전" : "") + (m.dead_cross_days != null ? " · 데드 " + m.dead_cross_days + "일 전" : "") || "—"]
  ];
  box.innerHTML = '<div class="out">' + rows.map(function (r) {
    return '<div class="row"><span>' + esc(r[0]) + "</span><b>" + esc(r[1]) + "</b></div>";
  }).join("") + "</div>" + '<div class="met" style="margin-top:10px">' + chipsHTML(badges(m)) + "</div>";
}

/* ══════════ 포지션 ══════════ */
function avgOf(p) {
  var q = 0, s = 0;
  p.tranches.forEach(function (t) { q += t.qty; s += t.qty * t.px; });
  return { qty: q, avg: q ? s / q : 0, cost: s };
}
function quoteOf(p) {
  // 손으로 넣은 현재가는 그날 안에서만 믿는다. 어제 값이 오늘 종가를 이기면 안 된다.
  if (p.cur && p.curAt === today()) return p.cur;
  var c = findData(p.mkt, p.tick);
  if (c && c.close) return c.close;
  if (p.cur) return p.cur;
  var d = S.ohlc[slug(p.mkt, p.tick)];
  return d ? d.c[d.c.length - 1] : null;
}

function evalPosition(p) {
  var a = avgOf(p), cur = quoteOf(p), src = findData(p.mkt, p.tick);
  var m = src ? src.metrics : null;
  var risk0 = p.entry0 - p.stop0;
  var r = (cur != null && risk0 > 0) ? (cur - a.avg) / risk0 : null;
  var pct = (cur != null && a.avg) ? (cur / a.avg - 1) * 100 : null;
  var high = Math.max(p.high || 0, cur || 0, p.entry0);

  // 트레일링 스탑 — 한 번 올린 손절선은 내리지 않는다
  var trail = null, mode = p.trail && p.trail.mode, tp = p.trail && Number(p.trail.p);
  if (mode === "pct" && tp > 0) trail = high * (1 - tp / 100);
  else if (mode === "atr" && tp > 0 && m && m.atr14) trail = high - m.atr14 * tp;
  else if (mode && mode.indexOf("ma") === 0 && m && m[mode] != null) trail = m[mode];
  var stop = Math.max(p.stop0, p.stopManual || 0, trail || 0);

  var alerts = [];
  if (cur != null && cur <= stop) alerts.push("손절선 " + nf(stop, 2) + " 도달 — 청산 규칙");
  if (m) {
    if (m.topping_alert) alerts.push("천정 패턴 발생 — 분할 청산 검토");
    if (m.distribution_alert) alerts.push("고점 대량거래 분산 경보");
    if (m.three_black_crows) alerts.push("흑삼병 — 하락 추세 전환 경계");
    if (mode === "ma20" && m.close != null && m.ma20 != null && m.close < m.ma20)
      alerts.push("20일선 종가 이탈 — 추적 규칙상 정리");
  }
  var r1 = riskRules(p.mkt);
  var t1 = r1 && r1.first_target ? p.entry0 + risk0 * (r1.first_target.r || 2) : null;
  if (r != null && t1 && cur >= t1 && !p.tookProfit) alerts.push("1차 익절 구간(" + (r1.first_target.r) + "R) 도달 — 절반 정리 후 손절 본전 이동");

  return { avg: a.avg, qty: a.qty, cost: a.cost, cur: cur, r: r, pct: pct, stop: stop,
           trail: trail, high: high, alerts: alerts, target1: t1, metrics: m,
           rule: src ? src.rule : null };
}
function riskRules(mkt) { return S.rules.exit && S.rules.exit.markets ? S.rules.exit.markets[mkt] : null; }

function renderPositions() {
  var box = $("pos-list"); box.innerHTML = "";
  var list = S.positions;
  $("pos-count").textContent = list.length ? list.length + "종목" : "";
  var alertN = 0;
  if (!list.length) { box.appendChild(el("p", "empty", "보유 포지션 없음")); }

  list.forEach(function (p, idx) {
    var v = evalPosition(p);
    alertN += v.alerts.length ? 1 : 0;
    var cls = v.alerts.length ? "alert" : (v.r > 0 ? "win" : v.r < 0 ? "lose" : "");
    var card = el("div", "pos " + cls);
    card.innerHTML =
      '<div class="ph"><span class="nm">' + esc(p.tick) + "</span>" +
      '<span class="chip">' + (p.mkt === "kr" ? "국장" : "미장") + "</span>" +
      '<span class="r ' + (v.r > 0 ? "up" : v.r < 0 ? "dn" : "") + '">' +
      (v.r == null ? "—" : (v.r >= 0 ? "+" : "") + v.r.toFixed(2) + "R") + "</span></div>" +
      '<div class="kv">' +
      "<span>평단 / 수량</span><span>" + nf(v.avg, 2) + " · " + nf(v.qty) + "주</span>" +
      "<span>현재가</span><span>" + (v.cur == null ? "—" : nf(v.cur, 2)) + " (" + pf(v.pct) + ")</span>" +
      "<span>손절선</span><span>" + nf(v.stop, 2) + (v.trail && v.trail > p.stop0 ? " ▲추적" : "") + "</span>" +
      "<span>최고가</span><span>" + nf(v.high, 2) + "</span>" +
      "<span>1차 익절</span><span>" + (v.target1 == null ? "—" : nf(v.target1, 2)) + "</span>" +
      "</div>";

    // 살 때의 근거가 아직 유효한지. 점수가 무너졌으면 경보가 없어도 팔 이유가 된다.
    if (v.rule) {
      var vd = v.rule.verdict;
      card.appendChild(el("div", "hold-rule " + (vd === "진입 가능" ? "ok" : vd === "관망" ? "warn" : "bad"),
        "<b>지금 다시 본다면</b> " + esc(vd) + " · 규칙 " + v.rule.pct + "%"));
    }
    if (v.metrics) {
      card.appendChild(el("div", "met", chipsHTML(badges(v.metrics, true))));
    } else {
      // 스캐너가 이 종목을 모른다 — POSITIONS_JSON 을 갱신하면 다음 스캔부터 채워진다
      card.appendChild(el("div", "hold-none",
        "지표 없음 — 아래 <b>감시용 JSON 복사</b> 후 POSITIONS_JSON 시크릿에 넣으면 다음 스캔부터 채워진다"));
    }

    v.alerts.forEach(function (a) { card.appendChild(el("div", "alertbox", "<b>⚠</b> " + esc(a))); });

    // 피라미딩 다음 차수
    var pl = pyramidPlan(p);
    if (pl.next) {
      var lastOk = pl.lastProfitable;
      var box2 = el("div", lastOk ? "okbox" : "alertbox",
        lastOk
          ? "<b>다음 차수</b> " + pl.next.no + "차 · 비중 " + Math.round(pl.next.frac * 100) + "% · 약 " + nf(pl.next.qty) + "주"
          : "<b>추가 매수 잠금</b> 직전 차수가 손실 중이다. 추가 매수는 물타기다.");
      card.appendChild(box2);
    }

    var row = el("div", "btnrow");
    var bCur = el("button", "act ghost", "현재가");
    bCur.addEventListener("click", function () {
      var x = prompt(p.tick + " 현재가", v.cur == null ? "" : String(v.cur));
      if (x == null) return;
      p.cur = parseFloat(x) || null; p.curAt = today();
      p.high = Math.max(p.high || 0, p.cur || 0);
      save("positions"); renderPositions(); renderTodo();
    });
    var bAdd = el("button", "act ghost", "추가 매수");
    bAdd.disabled = !pl.lastProfitable;
    bAdd.addEventListener("click", function () { addTranche(idx); });
    var bClose = el("button", "act danger", "청산");
    bClose.addEventListener("click", function () { closePosition(idx); });
    row.appendChild(bCur); row.appendChild(bAdd); row.appendChild(bClose);
    card.appendChild(row);
    box.appendChild(card);
  });

  var badge = $("pos-badge");
  if (alertN) { badge.hidden = false; badge.textContent = alertN; } else badge.hidden = true;
}

function pyramidPlan(p) {
  var py = S.rules.exit && S.rules.exit.pyramiding;
  var splits = (py && py.default_splits) || [0.5, 0.3, 0.2];
  var done = p.tranches.length;
  var v = quoteOf(p);
  var last = p.tranches[done - 1];
  var lastProfitable = !!(v != null && last && v > last.px);
  if (done >= splits.length) return { next: null, lastProfitable: lastProfitable, splits: splits };
  var totalQty = p.planQty || (p.tranches[0].qty / splits[0]);
  return {
    next: { no: done + 1, frac: splits[done], qty: Math.floor(totalQty * splits[done]) },
    lastProfitable: lastProfitable, splits: splits
  };
}

function addTranche(idx) {
  var p = S.positions[idx], pl = pyramidPlan(p);
  if (!pl.next) { toast("분할 계획을 모두 소진했다"); return; }
  if (!pl.lastProfitable) { toast("직전 차수가 손실 중 — 추가 매수 금지"); return; }
  var px = parseFloat(prompt(p.tick + " " + pl.next.no + "차 매수가", String(quoteOf(p) || "")));
  if (!px) return;
  var qty = parseInt(prompt("수량 (권장 " + pl.next.qty + "주)", String(pl.next.qty)), 10);
  if (!qty) return;
  p.tranches.push({ px: px, qty: qty, d: today() });
  // 추가 매수 때마다 손절선을 함께 올린다 (규칙: raise_stop_on_each_add)
  var newStop = parseFloat(prompt("올릴 손절가 (현재 " + evalPosition(p).stop + ")", String(evalPosition(p).stop)));
  if (newStop) p.stopManual = newStop;
  save("positions"); renderPositions(); toast(pl.next.no + "차 매수 기록됨");
}

function closePosition(idx) {
  var p = S.positions[idx], v = evalPosition(p);
  var px = parseFloat(prompt(p.tick + " 청산가", String(v.cur || "")));
  if (!px) return;
  var why = prompt("청산 사유 (일지에 기록된다)", "") || "";
  var risk0 = p.entry0 - p.stop0;
  var r = risk0 > 0 ? (px - v.avg) / risk0 : 0;
  S.journal.unshift({
    id: Date.now(), mkt: p.mkt, tick: p.tick, opened: p.opened, closed: today(),
    avg: v.avg, exit: px, qty: v.qty, r: Number(r.toFixed(2)),
    pct: Number(((px / v.avg - 1) * 100).toFixed(2)),
    whyIn: p.why || "", whyOut: why,
    ruleBroken: v.alerts.length > 0 && px > v.stop ? false : null
  });
  S.positions.splice(idx, 1);
  save("positions"); save("journal");
  renderPositions(); renderJournal(); renderTodo();
  toast((r >= 0 ? "+" : "") + r.toFixed(2) + "R 로 청산 기록됨");
}

function today() { return new Date().toISOString().slice(0, 10); }

function addPosition() {
  var tick = $("np-tick").value.trim().toUpperCase();
  var entry = parseFloat($("np-entry").value), qty = parseInt($("np-qty").value, 10);
  var stop = parseFloat($("np-stop").value);
  if (!tick) { $("np-tick").focus(); return toast("종목을 입력하세요"); }
  if (!entry || !qty) { return toast("진입가와 수량을 입력하세요"); }
  if (!stop) { $("np-stop").focus(); return toast("손절가 없이는 등록할 수 없다"); }
  if (stop >= entry) { $("np-stop").focus(); return toast("손절가는 진입가보다 낮아야 한다"); }
  var splits = (S.rules.exit && S.rules.exit.pyramiding.default_splits) || [0.5, 0.3, 0.2];
  S.positions.unshift({
    id: Date.now(), mkt: S.market, tick: tick,
    tranches: [{ px: entry, qty: qty, d: today() }],
    planQty: Math.round(qty / splits[0]),
    entry0: entry, stop0: stop, stopManual: 0, high: entry,
    trail: { mode: $("np-trail").value, p: parseFloat($("np-trailp").value) || 0 },
    why: $("np-why").value.trim(), opened: today()
  });
  save("positions");
  ["np-tick", "np-entry", "np-qty", "np-stop", "np-why"].forEach(function (i) { $(i).value = ""; });
  renderPositions(); renderTodo(); toast(tick + " 등록됨");
}

function exportPositions() {
  var out = S.positions.map(function (p) {
    var v = evalPosition(p);
    return { mkt: p.mkt, tick: p.tick, avg: Number(v.avg.toFixed(4)), qty: v.qty,
             stop: Number(v.stop.toFixed(4)), high: Number(v.high.toFixed(4)),
             trail: p.trail, entry0: p.entry0, stop0: p.stop0 };
  });
  var txt = JSON.stringify(out);
  if (navigator.clipboard) navigator.clipboard.writeText(txt).then(function () { toast("복사됨 — POSITIONS_JSON 시크릿에 붙여넣기"); },
    function () { prompt("복사해서 POSITIONS_JSON 시크릿에 넣으세요", txt); });
  else prompt("복사해서 POSITIONS_JSON 시크릿에 넣으세요", txt);
}

/* ══════════ 체크 탭 ══════════ */
function renderRules() {
  var rs = S.rules[S.market], box = $("rule-groups");
  box.innerHTML = "";
  if (!rs) { box.appendChild(el("p", "empty", "룰셋 로드 실패")); return; }
  var t = S.sel.check;
  var c = t ? findData(S.market, t) : null;
  var metrics = c ? c.metrics : null;
  var mk = key(S.market, t || "-");
  var man = S.manual[mk] || (S.manual[mk] = {});

  rs.groups.forEach(function (g) {
    var card = el("div", "card");
    card.appendChild(el("h2", null, esc(g.title)));
    if (g.note) card.appendChild(el("p", "note", esc(g.note)));

    var autoWrap = el("div", "fold"), autoHead = el("div", "foldhead",
      '<span class="arw">›</span><span class="lbl"></span>');
    var manualWrap = el("div");
    var autoCount = 0;

    g.items.forEach(function (it) {
      var res = it.auto ? evalCond(it.auto, metrics) : null;
      var isAuto = res !== null;
      var lab = el("label", "chk");
      var inp = document.createElement("input");
      inp.type = "checkbox";
      inp.checked = isAuto ? res : !!man[it.id];
      inp.disabled = isAuto;
      inp.addEventListener("change", function () { man[it.id] = inp.checked; save("manual"); paintScore(); });
      var sp = el("span", "t",
        '<span class="' + (it.core ? "core" : "") + '">' + esc(it.t) + "</span>" +
        (isAuto ? '<span class="auto' + (res ? "" : " autono") + '">' + (res ? "자동 충족" : "자동 미충족") + "</span>" : "") +
        (it.h ? '<span class="h">' + esc(it.h) + "</span>" : ""));
      lab.appendChild(inp); lab.appendChild(sp);
      if (isAuto) { autoWrap.appendChild(lab); autoCount++; } else manualWrap.appendChild(lab);
    });

    if (autoCount) {
      autoHead.querySelector(".lbl").textContent = "자동 판정 " + autoCount + "개 — 펼쳐보기";
      autoHead.addEventListener("click", function () {
        autoHead.classList.toggle("open"); autoWrap.classList.toggle("open");
      });
      card.appendChild(autoHead); card.appendChild(autoWrap);
    }
    card.appendChild(manualWrap);
    box.appendChild(card);
  });
  paintScore();
  renderExitRules();
}

function paintScore() {
  var rs = S.rules[S.market];
  if (!rs) return;
  var t = S.sel.check, c = t ? findData(S.market, t) : null;
  var man = S.manual[key(S.market, t || "-")] || {};
  var r = scoreRuleset(rs, c ? c.metrics : null, man);
  $("rule-score").textContent = r.pct;
  $("rule-bar").style.width = r.pct + "%";
  var v = $("rule-verdict");
  v.textContent = r.coreMiss.length ? "핵심 미충족 " + r.coreMiss.length : r.verdict;
  v.className = "verdict " + (r.verdict === "진입 가능" ? "v-go" : r.verdict === "관망" ? "v-wait" : "v-no");
  $("rule-auto").textContent = t
    ? "자동 판정 " + r.auto + " / 전체 " + r.total + "개 — 나머지는 직접 판단한다."
    : "종목을 선택하면 지표로 판정 가능한 항목이 자동으로 채워진다.";
}

function renderExitRules() {
  var er = riskRules(S.market), box = $("exit-rules");
  if (!er) { box.innerHTML = '<p class="empty">규칙 로드 실패</p>'; return; }
  var html =
    '<p class="rule"><b>손절</b> ' + esc(er.stop.text) + "</p>" +
    '<p class="rule"><b>1차 익절</b> ' + esc(er.first_target.text) + "</p>" +
    '<p class="rule"><b>추적</b> ' + esc(er.trail.text) + "</p>";
  (er.hard_exits || []).forEach(function (h) {
    html += '<p class="rule"><b>' + esc(h.t) + "</b> " + esc(h.text) + "</p>";
  });
  box.innerHTML = html;
}

function calcSizing() {
  var a = parseFloat($("acct").value) || 0, rp = parseFloat($("risk").value) || 0,
      e = parseFloat($("entry").value) || 0, s = parseFloat($("stop").value) || 0;
  var per = e - s, ids = ["o-qty", "o-cost", "o-w", "o-r", "o-t2", "o-t3"];
  var set = function (id, v) { $(id).textContent = v; };
  if (!(a > 0 && rp > 0 && e > 0 && s > 0 && per > 0)) {
    ids.forEach(function (i) { set(i, "—"); });
    if (e > 0 && s >= e) set("o-qty", "손절가 < 진입가");
    renderPyramid(0, 0);
    return;
  }
  var qty = Math.floor(a * rp / 100 / per), cost = qty * e;
  set("o-qty", nf(qty) + " 주"); set("o-cost", nf(cost));
  set("o-w", (cost / a * 100).toFixed(1) + " %");
  set("o-r", "-" + nf(qty * per));
  set("o-t2", nf(e + per * 2)); set("o-t3", nf(e + per * 3));
  renderPyramid(qty, e);
  kv.set("sizing", { a: a, rp: rp });
}

function renderPyramid(totalQty, entry) {
  var py = S.rules.exit && S.rules.exit.pyramiding, box = $("pyramid");
  if (!py) { box.innerHTML = ""; return; }
  var splits = py.default_splits;
  if (!totalQty) {
    box.innerHTML = '<p class="empty">사이징 값을 채우면 분할 계획이 계산된다.</p>';
    return;
  }
  var html = "";
  splits.forEach(function (f, i) {
    var q = Math.floor(totalQty * f);
    html += '<div class="tranche' + (i === 0 ? " done" : "") + '">' +
      '<span class="no">' + (i + 1) + "</span>" +
      "<span>" + Math.round(f * 100) + "% · " + (i === 0 ? "최초 진입" : "직전 차수 수익 중일 때만") + "</span>" +
      '<span class="amt">' + nf(q) + "주 / " + nf(q * entry) + "</span></div>";
  });
  html += '<p class="note" style="margin-top:10px">' + esc(py.note) + "</p>";
  box.innerHTML = html;
}

/* ══════════ 관심종목 ══════════ */
function renderWatch() {
  var ul = $("wl-list"); ul.innerHTML = "";
  $("wl-empty").style.display = S.watch.length ? "none" : "block";
  S.watch.forEach(function (it, i) {
    var li = el("li", null,
      '<span class="tag">' + (it.mkt === "kr" ? "국장" : "미장") + "</span>" +
      '<span class="tick">' + esc(it.tick) + "</span>" +
      '<span style="color:var(--dim);font-size:12px">' + esc(it.memo || "") + "</span>" +
      '<span class="sc">' + esc(it.score) + "</span>");
    var x = el("button", "x", "×");
    x.setAttribute("aria-label", "삭제");
    x.addEventListener("click", function () { S.watch.splice(i, 1); save("watch"); renderWatch(); });
    li.appendChild(x); ul.appendChild(li);
  });
}

/* ══════════ 룰셋 검증 (백테스트 결과) ══════════ */
function renderBacktest() {
  var b = S.bt[S.market], box = $("bt-box"), asof = $("bt-asof");
  if (!b) {
    asof.textContent = "";
    box.innerHTML = '<p class="empty">' + (S.market === "kr" ? "국장" : "미장") +
      ' 백테스트 미실행 — Actions \u2192 백테스트 \u2192 Run workflow</p>';
    return;
  }
  asof.textContent = b.asof;
  var live = (b.stats && b.stats.live_all) || {};
  var base = (b.stats && b.stats.research_all) || {};
  var rows = [
    ["기간", b.period.start + " ~ " + b.period.end],
    ["유니버스 / 신호", b.universe_size + "종목 / " + b.signals + "건"],
    ["현행 룰셋 매매", (live.n || 0) + "건"],
    ["현행 룰셋 기대값", live.expectancy_r == null ? "—" : live.expectancy_r.toFixed(3) + "R"],
    ["기준선 기대값", base.expectancy_r == null ? "—" : base.expectancy_r.toFixed(3) + "R"],
    ["검증기간 유지율", b.retention == null ? "—" : Math.round(b.retention * 100) + "%"]
  ];
  var html = '<div class="out">' + rows.map(function (r) {
    return '<div class="row"><span>' + esc(r[0]) + "</span><b>" + esc(r[1]) + "</b></div>";
  }).join("") + "</div>";

  // 규칙을 신뢰해도 되는지 한 줄로 결론을 낸다
  var warn = [];
  if (!live.n) warn.push("현행 룰셋으로 체결된 매매가 0건 — 규칙이 너무 빡빡하다.");
  else if (base.expectancy_r != null && live.expectancy_r != null && live.expectancy_r <= base.expectancy_r)
    warn.push("룰셋이 기준선(고점근접+거래량)을 개선하지 못했다.");
  if (b.overfit_warning) warn.push("검증기간 유지율 " + Math.round(b.retention * 100) + "% — 과최적화.");
  if (b.survivorship_bias) warn.push("미장 유니버스에 생존편향이 있어 실제보다 좋게 나온다.");
  if (live.expectancy_r != null && live.expectancy_r <= 0)
    warn.push("기대값이 0 이하 — 이 규칙으로는 매매하지 않는 편이 낫다.");

  html += warn.length
    ? warn.map(function (w) { return '<div class="alertbox"><b>⚠</b> ' + esc(w) + "</div>"; }).join("")
    : '<div class="okbox">기준선 대비 개선 확인 · 과최적화 경고 없음</div>';

  if ((b.core_blockers || []).length) {
    var top = b.core_blockers.slice(0, 3).filter(function (x) { return x.blocked_pct >= 50; });
    if (top.length) {
      html += '<p class="note" style="margin-top:10px">신호를 가장 많이 탈락시킨 항목: ' +
        top.map(function (x) { return "<code>" + esc(x.id) + "</code> " + x.blocked_pct + "%"; }).join(", ") +
        "</p>";
    }
  }
  html += '<p class="note" style="margin-top:8px">전체 리포트: <code>docs/backtest_' +
    S.market + ".md</code></p>";
  box.innerHTML = html;
}

/* ══════════ 일지 ══════════ */
function renderJournal() {
  var box = $("log-list"); box.innerHTML = "";
  $("log-count").textContent = S.journal.length ? S.journal.length + "건" : "";
  if (!S.journal.length) box.appendChild(el("p", "empty", "기록 없음 — 청산하면 자동으로 남는다"));
  S.journal.slice(0, 50).forEach(function (j) {
    var d = el("div", "pos " + (j.r > 0 ? "win" : "lose"));
    d.innerHTML =
      '<div class="ph"><span class="nm">' + esc(j.tick) + "</span>" +
      '<span class="chip">' + esc(j.opened) + " → " + esc(j.closed) + "</span>" +
      '<span class="r ' + (j.r > 0 ? "up" : "dn") + '">' + (j.r >= 0 ? "+" : "") + j.r + "R</span></div>" +
      '<div class="kv"><span>평단 → 청산</span><span>' + nf(j.avg, 2) + " → " + nf(j.exit, 2) + " (" + pf(j.pct) + ")</span></div>" +
      (j.whyIn ? '<p class="rule" style="margin-top:9px"><b>진입</b> ' + esc(j.whyIn) + "</p>" : "") +
      (j.whyOut ? '<p class="rule"><b>청산</b> ' + esc(j.whyOut) + "</p>" : "");
    box.appendChild(d);
  });

  var st = $("log-stats"), n = S.journal.length;
  if (!n) { st.innerHTML = '<div class="row"><span>기록</span><b>0건</b></div>'; return; }
  var wins = S.journal.filter(function (j) { return j.r > 0; });
  var loss = S.journal.filter(function (j) { return j.r <= 0; });
  var avg = function (a) { return a.length ? a.reduce(function (s, j) { return s + j.r; }, 0) / a.length : 0; };
  var wr = wins.length / n;
  var exp = wr * avg(wins) + (1 - wr) * avg(loss);
  var totR = S.journal.reduce(function (s, j) { return s + j.r; }, 0);
  st.innerHTML =
    '<div class="row"><span>매매 횟수</span><b>' + n + "건</b></div>" +
    '<div class="row"><span>승률</span><b>' + (wr * 100).toFixed(0) + "%</b></div>" +
    '<div class="row"><span>평균 수익 R</span><b>+' + avg(wins).toFixed(2) + "R</b></div>" +
    '<div class="row bad"><span>평균 손실 R</span><b>' + avg(loss).toFixed(2) + "R</b></div>" +
    '<div class="row hi"><span>기대값</span><b>' + (exp >= 0 ? "+" : "") + exp.toFixed(2) + "R</b></div>" +
    '<div class="row"><span>누적 R</span><b>' + (totR >= 0 ? "+" : "") + totR.toFixed(1) + "R</b></div>";
}

/* ══════════ 셀렉트 채우기 ══════════ */
function fillPickers() {
  [["chart-pick", "chart"], ["check-pick", "check"]].forEach(function (pair) {
    var sel = $(pair[0]), cur = S.sel[pair[1]];
    sel.innerHTML = '<option value="">' + (pair[1] === "chart" ? "후보를 선택하세요" : "종목 선택 — 자동 판정 항목이 채워진다") + "</option>";
    function group(label, list, prefix) {
      if (!list.length) return;
      var g = document.createElement("optgroup");
      g.label = label;
      list.forEach(function (c) {
        var o = document.createElement("option");
        o.value = c.ticker; o.textContent = prefix + nameOf(c);
        g.appendChild(o);
      });
      sel.appendChild(g);
    }
    group("오늘의 후보", candidates(S.market), "");
    // 보유 종목이 오늘 후보에도 들었다면 후보 쪽에만 둔다 — 같은 값이 두 번 뜨면 헷갈린다
    group("보유 종목", holdings(S.market).filter(function (c) {
      return !findCand(S.market, c.ticker);
    }), "◆ ");
    if (cur && findData(S.market, cur)) sel.value = cur; else S.sel[pair[1]] = "";
  });
}

/* ══════════ 렌더 총괄 / 탭 ══════════ */
function renderAll() {
  fillPickers(); renderToday(); renderRules(); renderPositions(); renderWatch();
  renderJournal(); renderBacktest();
  // 분할 계획은 exit_rules 가 로드된 뒤에야 그릴 수 있다 (부팅 시점의 calcSizing 은 이르다)
  calcSizing();
}
function setMarket(m) {
  S.market = m;
  document.documentElement.style.setProperty("--accent", m === "kr" ? "var(--up-kr)" : "var(--up)");
  Array.prototype.forEach.call($("mkt-seg").children, function (b) { b.classList.toggle("on", b.dataset.m === m); });
  kv.set("market", m);
  renderAll();
}
function go(p) {
  S.page = p;
  Array.prototype.forEach.call(document.querySelectorAll(".page"), function (s) { s.classList.remove("on"); });
  $("p-" + p).classList.add("on");
  Array.prototype.forEach.call($("tabbar").children, function (b) { b.classList.toggle("on", b.dataset.p === p); });
  window.scrollTo({ top: 0, behavior: "smooth" });
  if (p === "chart") drawChart();
}

/* ══════════ 저장 ══════════ */
function save(what) {
  if (what === "positions") kv.set("positions", S.positions);
  if (what === "journal") kv.set("journal", S.journal);
  if (what === "watch") kv.set("watch", S.watch);
  if (what === "manual") kv.set("manual", S.manual);
}

/* ══════════ 부팅 ══════════ */
function bindEvents() {
  Array.prototype.forEach.call($("mkt-seg").children, function (b) {
    b.addEventListener("click", function () { setMarket(b.dataset.m); });
  });
  Array.prototype.forEach.call($("tabbar").children, function (b) {
    b.addEventListener("click", function () { go(b.dataset.p); });
  });
  $("reload").addEventListener("click", function () { loadAll().then(function () { toast("갱신됨"); }); });

  $("chart-pick").addEventListener("change", function () {
    var t = this.value;
    if (!t) { CH.data = null; drawChart(); return; }
    openChart(S.market, t);
  });
  $("check-pick").addEventListener("change", function () {
    S.sel.check = this.value; renderRules();
  });

  $("np-add").addEventListener("click", addPosition);
  $("pos-export").addEventListener("click", exportPositions);
  ["acct", "risk", "entry", "stop"].forEach(function (i) {
    $(i).addEventListener("input", calcSizing);
  });
  $("wl-add").addEventListener("click", function () {
    var t = $("wl-tick").value.trim();
    if (!t) { $("wl-tick").focus(); return; }
    S.watch.unshift({ tick: t, memo: $("wl-memo").value.trim(), score: $("rule-score").textContent,
                      mkt: S.market, d: today() });
    save("watch"); renderWatch();
    $("wl-tick").value = ""; $("wl-memo").value = "";
  });
  $("log-export").addEventListener("click", function () {
    var txt = JSON.stringify({ positions: S.positions, journal: S.journal }, null, 1);
    if (navigator.clipboard) navigator.clipboard.writeText(txt).then(function () { toast("클립보드에 복사됨"); });
    else prompt("복사하세요", txt);
  });
  $("log-clear").addEventListener("click", function () {
    if (!confirm("일지를 모두 삭제한다. 되돌릴 수 없다.")) return;
    S.journal = []; save("journal"); renderJournal(); toast("삭제됨");
  });
  chartTouch();
}

function boot() {
  bindEvents();
  Promise.all([
    kv.get("positions", []), kv.get("journal", []), kv.get("watch", []),
    kv.get("manual", {}), kv.get("market", "us"), kv.get("sizing", null)
  ]).then(function (r) {
    S.positions = r[0] || []; S.journal = r[1] || []; S.watch = r[2] || [];
    S.manual = r[3] || {};
    if (r[5]) { $("acct").value = r[5].a; $("risk").value = r[5].rp; }
    setMarket(r[4] || "us");
    calcSizing();
    return loadAll();
  });
  if ("serviceWorker" in navigator) navigator.serviceWorker.register("sw.js").catch(function () {});
}
boot();
})();
