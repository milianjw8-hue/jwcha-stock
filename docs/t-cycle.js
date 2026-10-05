/* T-cycle timing overlay for the Nasdaq/NYSE relative-strength page. */
(() => {
  const result = document.getElementById('tResult');
  const chart = document.getElementById('tChart');
  const inputA = document.getElementById('tA');
  const inputX = document.getElementById('tX');
  const dataStatus = document.getElementById('dataStatus');
  let series = [];
  let sourceLabel = '자동';

  const ratio = d => Number(d.nasdaq) / Number(d.nyse);
  const stamp = value => Date.parse(`${value}T00:00:00Z`);

  function nearestIndex(date) {
    const target = stamp(date);
    let best = 0;
    let distance = Infinity;
    series.forEach((item, index) => {
      const delta = Math.abs(stamp(item.date) - target);
      if (delta < distance) {
        best = index;
        distance = delta;
      }
    });
    return best;
  }

  function addWeekdays(date, count) {
    const day = new Date(`${date}T12:00:00Z`);
    let remaining = count;
    while (remaining > 0) {
      day.setUTCDate(day.getUTCDate() + 1);
      if (day.getUTCDay() !== 0 && day.getUTCDay() !== 6) remaining--;
    }
    return day.toISOString().slice(0, 10);
  }

  function pivots(values, radius = 3) {
    const lows = [];
    const highs = [];
    for (let i = radius; i < values.length - radius; i++) {
      const window = values.slice(i - radius, i + radius + 1);
      const low = Math.min(...window);
      const high = Math.max(...window);
      if (values[i] === low && window.indexOf(low) === radius) lows.push(i);
      if (values[i] === high && window.indexOf(high) === radius) highs.push(i);
    }
    return { lows, highs };
  }

  function suggestDates() {
    if (series.length < 80) return;
    const values = series.map(ratio);
    const { lows, highs } = pivots(values);
    let choice = null;
    if (highs.length) {
      const latestHigh = highs[highs.length - 1];
      let topCenter = latestHigh;
      let firstHigh = latestHigh;
      // When two nearby peaks have similar height, use the center of the M shape.
      for (let i = highs.length - 2; i >= 0; i--) {
        const prior = highs[i];
        if (latestHigh - prior > 45) break;
        const gapPct = Math.abs(values[latestHigh] / values[prior] - 1);
        if (latestHigh - prior >= 5 && gapPct <= 0.015) {
          firstHigh = prior;
          topCenter = Math.round((prior + latestHigh) / 2);
          break;
        }
      }
      const candidates = lows.filter(i => i < firstHigh && topCenter - i >= 5 && topCenter - i <= 180);
      if (candidates.length) choice = { a: candidates[candidates.length - 1], x: topCenter };
    }
    if (!choice) {
      const x = Math.max(10, series.length - 15);
      const a = Math.max(0, x - 30);
      choice = { a, x };
    }
    inputA.value = series[choice.a].date;
    inputX.value = series[choice.x].date;
    calculate();
  }

  function draw(a, x, b) {
    const values = series.map(ratio);
    const last = values.length - 1;
    const end = Math.max(last, b);
    const px = i => 34 + (i / Math.max(1, end)) * 532;
    const min = Math.min(...values);
    const max = Math.max(...values);
    const pad = (max - min || Math.abs(max) * 0.02 || 0.01) * 0.12;
    const py = value => 153 - ((value - (min - pad)) / (max - min + 2 * pad)) * 128;
    const path = values.map((value, i) => `${i ? 'L' : 'M'}${px(i)},${py(value)}`).join(' ');
    let svg = '<line x1="34" y1="159" x2="566" y2="159" stroke="#dbe4df"/>';
    svg += `<path d="${path}" fill="none" stroke="#17694d" stroke-width="2.5" stroke-linejoin="round"/>`;
    [[a, '#d58a42', 'A'], [x, '#4968b0', 'X'], [b, '#b7443d', 'B']].forEach(([index, color, label]) => {
      const position = px(index);
      svg += `<line x1="${position}" y1="20" x2="${position}" y2="204" stroke="${color}" stroke-width="2" stroke-dasharray="5 4"/>`;
      svg += `<text x="${Math.min(560, Math.max(40, position))}" y="15" text-anchor="middle" fill="${color}" font-size="12" font-weight="700">${label}</text>`;
    });
    const ax = px(a), xx = px(x), bx = px(b);
    svg += `<line x1="${ax}" y1="190" x2="${xx}" y2="190" stroke="#26352f" stroke-width="2"/>`;
    svg += `<line x1="${xx}" y1="181" x2="${xx}" y2="199" stroke="#26352f" stroke-width="2"/>`;
    svg += `<line x1="${xx}" y1="190" x2="${bx}" y2="190" stroke="#26352f" stroke-width="2"/>`;
    svg += `<text x="${(ax + xx) / 2}" y="184" text-anchor="middle" fill="#526159" font-size="10">A–X</text>`;
    svg += `<text x="${(xx + bx) / 2}" y="184" text-anchor="middle" fill="#526159" font-size="10">X–B</text>`;
    svg += '<text x="34" y="216" fill="#7a8982" font-size="10">과거</text>';
    svg += `<text x="566" y="216" text-anchor="end" fill="#7a8982" font-size="10">${b > last ? '예상' : '현재까지'}</text>`;
    chart.innerHTML = svg;
  }

  function calculate() {
    if (series.length < 80) {
      result.innerHTML = '5년 일간 이력이 아직 연결되지 않았습니다.<small>데이터 갱신 후 최소 80거래일 이상이 있어야 합니다.</small>';
      chart.innerHTML = '';
      return;
    }
    const a = nearestIndex(inputA.value);
    const x = nearestIndex(inputX.value);
    if (!inputA.value || !inputX.value || x <= a) {
      result.textContent = 'A 날짜는 X 날짜보다 앞서야 합니다.';
      chart.innerHTML = '';
      return;
    }
    const span = x - a;
    if (span < 5 || span > 180) {
      result.innerHTML = 'A–X 간격을 5~180거래일로 선택해 주세요.<small>너무 짧거나 긴 구간은 자동 제안 대신 차트에서 중심점을 확인해 조정할 수 있습니다.</small>';
      chart.innerHTML = '';
      return;
    }
    const b = x + span;
    const last = series.length - 1;
    const future = b > last;
    const offset = future ? b - last : 0;
    const bDate = future ? addWeekdays(series[last].date, offset) : series[b].date;
    const startDate = future ? addWeekdays(series[last].date, Math.max(1, offset - 3)) : series[Math.max(0, b - 3)].date;
    const endDate = future ? addWeekdays(series[last].date, offset + 3) : series[Math.min(last, b + 3)].date;
    const current = ratio(series[last]);
    result.innerHTML = `<strong>B 예상 관찰 구간: ${startDate} ~ ${endDate}</strong><small>${sourceLabel} 데이터 · A–X ${span}거래일 = X–B ${span}거래일 · 중심 예상일 ${bDate}${future ? ` · 최신 자료부터 약 ${offset}거래일 후` : ' · 과거 구간 투영'}<br>A ${series[a].date} · X ${series[x].date} · 현재 비율 ${current.toFixed(4)}<br>이 날짜는 나스닥÷NYSE 비율의 전환 후보입니다. 두 지수의 가격 매수·매도 지시는 아닙니다.</small>`;
    draw(a, x, b);
  }

  inputA.addEventListener('change', calculate);
  inputX.addEventListener('change', calculate);
  document.getElementById('suggestT').addEventListener('click', suggestDates);
  document.getElementById('applyTHistory').addEventListener('click', () => {
    const raw = document.getElementById('tPaste').value.trim();
    const rows = raw ? raw.split(/\r?\n/).map(line => line.trim()).filter(Boolean) : [];
    const parsed = rows.map(line => {
      const cells = line.split(/[\t,;]+/).map(value => value.trim());
      if (cells.length < 3 || !/^\d{4}-\d{2}-\d{2}$/.test(cells[0])) return null;
      const nasdaq = Number(cells[1].replace(/[,$\s]/g, ''));
      const nyse = Number(cells[2].replace(/[,$\s]/g, ''));
      return nasdaq > 0 && nyse > 0 ? { date: cells[0], nasdaq, nyse } : null;
    }).filter(Boolean).sort((a, b) => a.date.localeCompare(b.date));
    const unique = [...new Map(parsed.map(item => [item.date, item])).values()];
    if (unique.length < 80) {
      result.innerHTML = `읽은 유효 데이터가 ${unique.length}개입니다. 최소 80거래일이 필요합니다.<small>각 줄에 날짜, 나스닥 종가, NYSE 종가를 넣고 쉼표 또는 탭으로 구분하세요.</small>`;
      return;
    }
    series = unique;
    sourceLabel = '직접 입력';
    suggestDates();
    if (dataStatus) dataStatus.textContent = `직접 입력 일간 데이터 ${series.length}개 · T 분석 적용`;
  });
  window.addEventListener('market-daily-data', event => {
    series = Array.isArray(event.detail)
      ? event.detail.filter(item => item.date && Number(item.nasdaq) > 0 && Number(item.nyse) > 0).sort((a, b) => a.date.localeCompare(b.date))
      : [];
    sourceLabel = '자동';
    if (series.length >= 80) suggestDates();
    else {
      result.innerHTML = '일간 이력이 아직 준비되지 않았습니다.<small>기존 자동 데이터는 최근 10주만 저장하므로, 앱 데이터 갱신 작업을 실행해야 T자 계산이 가능합니다.</small>';
      chart.innerHTML = '';
      if (dataStatus) dataStatus.textContent += ' · 일간 이력 미포함';
    }
  });
})();
