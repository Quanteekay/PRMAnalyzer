/* ============================================================
 * PRMAnalyzer — client-side interactions and chart rendering
 * ============================================================ */

const PALETTE_DARK = {
    text: '#c5c8d6',
    grid: 'rgba(255,255,255,0.06)',
};
const PALETTE_LIGHT = {
    text: '#3b3f52',
    grid: 'rgba(0,0,0,0.08)',
};

let CURRENT_PALETTE = PALETTE_DARK;

const COLORS = [
    '#7c5cff', '#00d4ff', '#ff5cf2', '#29e0a3', '#ffc857',
    '#ff5874', '#9b6dff', '#5cd5ff', '#ff89ee', '#5ee8b9',
    '#ffd980', '#ff7c92', '#b18bff', '#82dfff', '#ffa9f3',
    '#82f0c9',
];

/* ----------------------- Theme toggle ----------------------- */
(function initTheme() {
    const stored = localStorage.getItem('prm-theme') || 'dark';
    document.documentElement.dataset.theme = stored;
    CURRENT_PALETTE = stored === 'light' ? PALETTE_LIGHT : PALETTE_DARK;
    document.addEventListener('DOMContentLoaded', () => {
        const btn = document.getElementById('theme-toggle');
        if (!btn) return;
        btn.addEventListener('click', () => {
            const next = document.documentElement.dataset.theme === 'light' ? 'dark' : 'light';
            document.documentElement.dataset.theme = next;
            localStorage.setItem('prm-theme', next);
            CURRENT_PALETTE = next === 'light' ? PALETTE_LIGHT : PALETTE_DARK;
            // Reload charts after theme switch
            location.reload();
        });
    });
})();

/* ----------------------- Watchlist ----------------------- */
function getCSRFToken() {
    const m = document.cookie.match(/csrf_token=([^;]+)/);
    if (m) return m[1];
    const meta = document.querySelector('meta[name=csrf-token]');
    return meta ? meta.content : '';
}

document.addEventListener('click', async (e) => {
    const btn = e.target.closest('.watch-toggle');
    if (!btn) return;
    e.preventDefault();
    const city = btn.dataset.city;
    const fd = new FormData();
    fd.append('city', city);
    try {
        const r = await fetch('/watchlist/toggle', { method: 'POST', body: fd });
        const data = await r.json();
        if (!data.ok) return;
        if (data.action === 'added') {
            btn.dataset.watched = '1';
            btn.textContent = btn.classList.contains('star-btn') ? '★' : '☆ usuń';
            if (btn.classList.contains('star-btn')) btn.textContent = '★';
        } else {
            btn.dataset.watched = '0';
            btn.textContent = btn.classList.contains('star-btn') ? '☆' : '☆ dodaj';
            if (btn.classList.contains('star-btn')) btn.textContent = '☆';
        }
    } catch (err) {
        console.error('Watchlist toggle failed:', err);
    }
});

/* ----------------------- Table tools (filter, sort, pagination) ----------------------- */
window.initTableTools = function (tableSelector) {
    const table = document.querySelector(tableSelector);
    if (!table) return;

    const tbody = table.querySelector('tbody');
    const allRows = Array.from(tbody.querySelectorAll('tr'));
    let filtered = allRows.slice();
    let pageSize = 25;
    let currentPage = 1;
    let sortKey = null;
    let sortDir = 1;

    const filterInput = document.getElementById('filter-input');
    const voivSelect = document.getElementById('filter-voiv');
    const pageSelect = document.getElementById('filter-pagesize');
    const prevBtn = document.getElementById('page-prev');
    const nextBtn = document.getElementById('page-next');
    const pageInfo = document.getElementById('page-info');

    function applyFilter() {
        const q = (filterInput?.value || '').trim().toLowerCase();
        const voiv = voivSelect?.value || '';
        filtered = allRows.filter((row) => {
            const text = (row.dataset.city + ' ' + row.dataset.voivodeship).toLowerCase();
            if (q && !text.includes(q)) return false;
            if (voiv && row.dataset.voivodeship !== voiv) return false;
            return true;
        });
        if (sortKey) sortRows();
        currentPage = 1;
        render();
    }

    function sortRows() {
        filtered.sort((a, b) => {
            let av, bv;
            if (sortKey === 'city' || sortKey === 'voivodeship') {
                av = (a.dataset[sortKey] || '').toLowerCase();
                bv = (b.dataset[sortKey] || '').toLowerCase();
            } else {
                av = parseFloat(a.dataset[sortKey] || 0);
                bv = parseFloat(b.dataset[sortKey] || 0);
            }
            if (av < bv) return -1 * sortDir;
            if (av > bv) return 1 * sortDir;
            return 0;
        });
    }

    function render() {
        tbody.innerHTML = '';
        const start = (currentPage - 1) * pageSize;
        const end = Math.min(start + pageSize, filtered.length);
        for (let i = start; i < end; i++) {
            const row = filtered[i];
            const idxCell = row.querySelector('.row-idx');
            if (idxCell) idxCell.textContent = i + 1;
            tbody.appendChild(row);
        }
        const totalPages = Math.max(1, Math.ceil(filtered.length / pageSize));
        if (pageInfo) pageInfo.textContent = `strona ${currentPage} / ${totalPages} · ${filtered.length} wyników`;
        if (prevBtn) prevBtn.disabled = currentPage <= 1;
        if (nextBtn) nextBtn.disabled = currentPage >= totalPages;
    }

    filterInput?.addEventListener('input', applyFilter);
    voivSelect?.addEventListener('change', applyFilter);
    pageSelect?.addEventListener('change', () => {
        pageSize = parseInt(pageSelect.value, 10);
        currentPage = 1;
        render();
    });
    prevBtn?.addEventListener('click', () => { currentPage--; render(); });
    nextBtn?.addEventListener('click', () => { currentPage++; render(); });

    table.querySelectorAll('th[data-sort]').forEach((th) => {
        th.style.cursor = 'pointer';
        th.addEventListener('click', () => {
            const key = th.dataset.sort;
            if (key === 'index') return;
            if (sortKey === key) sortDir *= -1; else { sortKey = key; sortDir = 1; }
            sortRows();
            render();
            table.querySelectorAll('th').forEach((h) => h.classList.remove('sort-asc', 'sort-desc'));
            th.classList.add(sortDir === 1 ? 'sort-asc' : 'sort-desc');
        });
    });

    render();
};

/* ----------------------- Global city search (in navbar, if present) ----------------------- */
(function initSearch() {
    document.addEventListener('DOMContentLoaded', () => {
        const input = document.getElementById('global-search');
        const results = document.getElementById('global-search-results');
        if (!input || !results) return;
        let debounce;
        input.addEventListener('input', () => {
            clearTimeout(debounce);
            debounce = setTimeout(async () => {
                const q = input.value.trim();
                if (!q) { results.innerHTML = ''; results.classList.remove('open'); return; }
                try {
                    const r = await fetch(`/api/search?q=${encodeURIComponent(q)}`);
                    const data = await r.json();
                    if (!data.results.length) {
                        results.innerHTML = '<div class="search-empty">Brak wyników</div>';
                    } else {
                        results.innerHTML = data.results.map((x) =>
                            `<a href="/compare?city=${encodeURIComponent(x.city)}" class="search-item">
                                <strong>${x.city}</strong>
                                <span class="muted">${x.voivodeship}</span>
                             </a>`
                        ).join('');
                    }
                    results.classList.add('open');
                } catch (err) { console.error(err); }
            }, 200);
        });
        document.addEventListener('click', (e) => {
            if (!e.target.closest('.search-shell')) results.classList.remove('open');
        });
    });
})();

/* ----------------------- Chart utilities ----------------------- */
function baseOpts(extra = {}) {
    return {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
            legend: {
                labels: { color: CURRENT_PALETTE.text, font: { family: 'Inter', size: 11 }, boxWidth: 12 },
                position: 'bottom',
            },
            tooltip: {
                backgroundColor: 'rgba(7, 8, 13, 0.95)',
                borderColor: 'rgba(255,255,255,0.1)',
                borderWidth: 1,
                titleColor: '#f4f6fb',
                bodyColor: '#c5c8d6',
                padding: 12,
                cornerRadius: 8,
                callbacks: {
                    label: (ctx) => {
                        const v = ctx.parsed.y ?? ctx.parsed;
                        if (typeof v !== 'number') return ctx.label;
                        return `${ctx.dataset.label || ctx.label}: ${v.toLocaleString('pl-PL')} zł`;
                    },
                },
            },
        },
        scales: {
            x: { ticks: { color: CURRENT_PALETTE.text, font: { size: 10 } }, grid: { color: CURRENT_PALETTE.grid } },
            y: {
                ticks: {
                    color: CURRENT_PALETTE.text,
                    font: { family: 'JetBrains Mono', size: 10 },
                    callback: (v) => v.toLocaleString('pl-PL'),
                },
                grid: { color: CURRENT_PALETTE.grid },
            },
        },
        ...extra,
    };
}

function makeGradient(ctx, height, c1, c2) {
    const g = ctx.createLinearGradient(0, 0, 0, height);
    g.addColorStop(0, c1);
    g.addColorStop(1, c2);
    return g;
}

/* ----------------------- Dashboard charts ----------------------- */
window.renderDashboardCharts = function () {
    const data = window.PRM_DATA?.cities || [];
    if (!data.length) return;

    const top = data.slice(0, 12);
    const labels = top.map((c) => c.city);
    const ctx1 = document.getElementById('chart-cities');
    if (ctx1) {
        const c = ctx1.getContext('2d');
        new Chart(ctx1, {
            type: 'bar',
            data: {
                labels,
                datasets: [
                    {
                        label: 'Pierwotny (zł/m²)',
                        data: top.map((c) => c.primary || 0),
                        backgroundColor: makeGradient(c, 320, 'rgba(124,92,255,0.95)', 'rgba(124,92,255,0.3)'),
                        borderRadius: 6,
                    },
                    {
                        label: 'Wtórny (zł/m²)',
                        data: top.map((c) => c.secondary || 0),
                        backgroundColor: makeGradient(c, 320, 'rgba(0,212,255,0.9)', 'rgba(0,212,255,0.25)'),
                        borderRadius: 6,
                    },
                ],
            },
            options: baseOpts(),
        });
    }

    const ctx2 = document.getElementById('chart-tx');
    if (ctx2) {
        const top8 = [...data].sort(
            (a, b) => (b.tx_primary + b.tx_secondary) - (a.tx_primary + a.tx_secondary)
        ).slice(0, 8);
        new Chart(ctx2, {
            type: 'doughnut',
            data: {
                labels: top8.map((c) => c.city),
                datasets: [{
                    data: top8.map((c) => c.tx_primary + c.tx_secondary),
                    backgroundColor: COLORS,
                    borderColor: 'rgba(7,8,13,0.9)',
                    borderWidth: 2,
                }],
            },
            options: {
                responsive: true, maintainAspectRatio: false, cutout: '62%',
                plugins: {
                    legend: { labels: { color: CURRENT_PALETTE.text, boxWidth: 10, font: { size: 11 } }, position: 'right' },
                    tooltip: { backgroundColor: 'rgba(7,8,13,0.95)' },
                },
            },
        });
    }
};

/* ----------------------- Analytics charts ----------------------- */
window.renderAnalyticsCharts = function () {
    const cities = window.PRM_DATA?.cities || [];
    const trend = window.PRM_DATA?.trend || {};

    const ctxT = document.getElementById('chart-trend');
    if (ctxT && Object.keys(trend).length) {
        const yearsAll = new Set();
        Object.values(trend).forEach((s) => s.years.forEach((y) => yearsAll.add(y)));
        const years = [...yearsAll].sort();
        const voivodeships = Object.keys(trend).sort();
        const datasets = voivodeships.map((v, i) => {
            const series = trend[v];
            const byYear = Object.fromEntries(series.years.map((y, idx) => [y, series.values[idx]]));
            return {
                label: v,
                data: years.map((y) => byYear[y] ?? null),
                borderColor: COLORS[i % COLORS.length],
                backgroundColor: COLORS[i % COLORS.length] + '22',
                borderWidth: 2, tension: 0.3, pointRadius: 3,
                pointBackgroundColor: COLORS[i % COLORS.length], fill: false,
            };
        });
        new Chart(ctxT, {
            type: 'line',
            data: { labels: years, datasets },
            options: baseOpts({
                interaction: { mode: 'index', intersect: false },
                plugins: {
                    legend: { labels: { color: CURRENT_PALETTE.text, boxWidth: 8, font: { size: 10 } }, position: 'right' },
                    tooltip: { backgroundColor: 'rgba(7,8,13,0.95)' },
                },
            }),
        });
    }

    const ctxB = document.getElementById('chart-primary-bar');
    if (ctxB) {
        const top10 = [...cities].sort((a, b) => (b.primary || 0) - (a.primary || 0)).slice(0, 10);
        const c = ctxB.getContext('2d');
        new Chart(ctxB, {
            type: 'bar',
            data: {
                labels: top10.map((x) => x.city),
                datasets: [{
                    label: 'Cena m² (pierwotny)',
                    data: top10.map((x) => x.primary || 0),
                    backgroundColor: makeGradient(c, 320, '#ff5cf2', '#7c5cff'),
                    borderRadius: 6,
                }],
            },
            options: baseOpts({ indexAxis: 'y' }),
        });
    }

    const ctxS = document.getElementById('chart-spread');
    if (ctxS) {
        const ranked = [...cities]
            .filter((c) => c.primary && c.secondary)
            .map((c) => ({ ...c, spread: c.primary - c.secondary }))
            .sort((a, b) => b.spread - a.spread).slice(0, 10);
        const c = ctxS.getContext('2d');
        new Chart(ctxS, {
            type: 'bar',
            data: {
                labels: ranked.map((x) => x.city),
                datasets: [{
                    label: 'Spread pierwotny − wtórny',
                    data: ranked.map((x) => x.spread),
                    backgroundColor: makeGradient(c, 320, '#29e0a3', '#00d4ff'),
                    borderRadius: 6,
                }],
            },
            options: baseOpts(),
        });
    }
};

/* ----------------------- Compare chart ----------------------- */
window.renderCompareChart = function () {
    const items = window.RCN_COMPARE || [];
    const ctx = document.getElementById('chart-compare');
    if (!ctx || !items.length) return;
    const c = ctx.getContext('2d');
    new Chart(ctx, {
        type: 'bar',
        data: {
            labels: items.map((x) => x.city),
            datasets: [
                {
                    label: 'Pierwotny zł/m²',
                    data: items.map((x) => x.primary || 0),
                    backgroundColor: makeGradient(c, 280, 'rgba(124,92,255,0.9)', 'rgba(124,92,255,0.3)'),
                    borderRadius: 6,
                },
                {
                    label: 'Wtórny zł/m²',
                    data: items.map((x) => x.secondary || 0),
                    backgroundColor: makeGradient(c, 280, 'rgba(0,212,255,0.9)', 'rgba(0,212,255,0.3)'),
                    borderRadius: 6,
                },
            ],
        },
        options: baseOpts(),
    });
};

/* ----------------------- Forecast chart ----------------------- */
window.renderForecastChart = function () {
    const result = window.RCN_FORECAST;
    if (!result || !result.historical?.length) return;
    const ctx = document.getElementById('chart-forecast');
    if (!ctx) return;

    const hist = result.historical;
    const fc = result.forecast || [];
    const labels = [...hist.map((h) => h.year), ...fc.map((f) => f.year)];

    const histLine = hist.map((h) => h.price).concat(fc.map(() => null));
    // Make forecast start exactly from last historical point
    const fcLine = hist.map(() => null);
    if (hist.length) fcLine[hist.length - 1] = hist[hist.length - 1].price;
    fc.forEach((f) => fcLine.push(f.price));

    const lowBand = hist.map(() => null).concat(fc.map((f) => f.low));
    const highBand = hist.map(() => null).concat(fc.map((f) => f.high));
    if (hist.length) {
        lowBand[hist.length - 1] = hist[hist.length - 1].price;
        highBand[hist.length - 1] = hist[hist.length - 1].price;
    }

    new Chart(ctx, {
        type: 'line',
        data: {
            labels,
            datasets: [
                {
                    label: 'Historia',
                    data: histLine,
                    borderColor: '#7c5cff',
                    backgroundColor: 'rgba(124,92,255,0.1)',
                    borderWidth: 3, pointRadius: 4, tension: 0.2,
                },
                {
                    label: 'Prognoza',
                    data: fcLine,
                    borderColor: '#00d4ff',
                    backgroundColor: 'rgba(0,212,255,0.05)',
                    borderWidth: 3, pointRadius: 4, tension: 0.2,
                    borderDash: [6, 6],
                },
                {
                    label: 'Dolne 95%',
                    data: lowBand,
                    borderColor: 'rgba(0,212,255,0.4)',
                    borderWidth: 1, pointRadius: 0, fill: '+1',
                    backgroundColor: 'rgba(0,212,255,0.1)',
                },
                {
                    label: 'Górne 95%',
                    data: highBand,
                    borderColor: 'rgba(0,212,255,0.4)',
                    borderWidth: 1, pointRadius: 0, fill: false,
                },
            ],
        },
        options: baseOpts({
            interaction: { mode: 'index', intersect: false },
        }),
    });
};

/* ----------------------- Leaflet map ----------------------- */
window.renderMap = async function () {
    const mapEl = document.getElementById('map');
    if (!mapEl || typeof L === 'undefined') return;

    const map = L.map('map', { zoomControl: true }).setView([52.0, 19.3], 6);
    L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_nolabels/{z}/{x}/{y}{r}.png', {
        attribution: '© OpenStreetMap, © CartoDB',
        maxZoom: 10,
    }).addTo(map);
    // Labels layer (city names) on top
    L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_only_labels/{z}/{x}/{y}{r}.png', {
        maxZoom: 10,
    }).addTo(map);

    const prices = window.PRM_MAP?.prices || {};
    const wages = window.PRM_MAP?.wages || {};
    const all = Object.values(prices);
    const min = Math.min(...all);
    const max = Math.max(...all);

    function colorFor(price) {
        if (!price) return '#3a3f55';
        const t = (price - min) / Math.max(1, max - min);
        // 5-stop gradient
        const stops = ['#1d5b80', '#2d8fb3', '#7c5cff', '#ff5cf2', '#ff6080'];
        const idx = Math.min(Math.floor(t * stops.length), stops.length - 1);
        return stops[idx];
    }

    const resp = await fetch('/static/data/voivodeships.geojson');
    const geo = await resp.json();

    L.geoJSON(geo, {
        style: (feat) => ({
            fillColor: colorFor(prices[feat.properties.nazwa]),
            fillOpacity: 0.78,
            color: 'rgba(255,255,255,0.5)',
            weight: 1,
        }),
        onEachFeature: (feat, layer) => {
            const name = feat.properties.nazwa;
            const price = prices[name];
            const wage = wages[name];
            const months = price && wage ? (price / wage).toFixed(2) : '—';
            layer.bindPopup(`
                <h4>${name}</h4>
                <div class="pop-stat"><span>Średnia cena m²</span> <strong>${price ? price.toLocaleString('pl-PL') + ' zł' : '—'}</strong></div>
                <div class="pop-stat"><span>Wynagrodzenie</span> <strong>${wage ? wage.toLocaleString('pl-PL') + ' zł' : '—'}</strong></div>
                <div class="pop-stat"><span>Miesięcy na 1 m²</span> <strong>${months}</strong></div>
            `);
            layer.on('mouseover', () => layer.setStyle({ weight: 3, color: '#fff' }));
            layer.on('mouseout', () => layer.setStyle({ weight: 1, color: 'rgba(255,255,255,0.5)' }));
        },
    }).addTo(map);

    // Legend
    const legend = L.control({ position: 'bottomright' });
    legend.onAdd = () => {
        const div = L.DomUtil.create('div', 'map-legend');
        const stops = ['#1d5b80', '#2d8fb3', '#7c5cff', '#ff5cf2', '#ff6080'];
        div.innerHTML = '<strong>Cena m²</strong><br>' + stops.map((color, i) => {
            const t = i / (stops.length - 1);
            const v = Math.round(min + t * (max - min));
            return `<span class="swatch" style="background:${color}"></span> ${v.toLocaleString('pl-PL')} zł`;
        }).join('<br>');
        return div;
    };
    legend.addTo(map);
};
