(function ($) {
    const COLOR_PALETTE = [
        '#4e79a7', '#f28e2b', '#e15759', '#76b7b2', '#59a14f',
        '#edc948', '#b07aa1', '#ff9da7', '#9c755f', '#bab0ac'
    ];

    let totalSeries = [];
    let vendorSeries = [];
    let businessSeries = [];
    let initiativeSeries = [];
    let taskSeries = [];
    let iterationSeries = [];
    let titleSeries = [];
    let bucketType = 'day';

    const TOOLTIP_ID = 'llm-spend-tooltip';
    const TOOLTIP_STYLE_ID = 'llm-spend-tooltip-style';

    function ensureTooltipStyle() {
        if (document.getElementById(TOOLTIP_STYLE_ID)) {
            return;
        }

        const style = document.createElement('style');
        style.id = TOOLTIP_STYLE_ID;
        style.textContent = `
            #${TOOLTIP_ID} {
                position: fixed;
                z-index: 9999;
                background: rgba(20, 20, 20, 0.92);
                border: 1px solid rgba(255, 255, 255, 0.1);
                border-radius: 6px;
                padding: 8px 10px;
                color: #ffffff;
                font-size: 12px;
                pointer-events: none;
                box-shadow: 0 4px 12px rgba(0, 0, 0, 0.25);
                opacity: 0;
                transition: opacity 120ms ease;
            }

            #${TOOLTIP_ID}.visible {
                opacity: 1;
            }

            #${TOOLTIP_ID} .tooltip-title {
                font-weight: 600;
                margin-bottom: 4px;
            }

            #${TOOLTIP_ID} .tooltip-row {
                display: flex;
                justify-content: space-between;
                gap: 16px;
            }

            #${TOOLTIP_ID} .tooltip-label {
                font-weight: 500;
            }
        `;

        document.head.appendChild(style);
    }

    function ensureTooltipNode() {
        let tooltip = document.getElementById(TOOLTIP_ID);
        if (!tooltip) {
            tooltip = document.createElement('div');
            tooltip.id = TOOLTIP_ID;
            document.body.appendChild(tooltip);
        }

        ensureTooltipStyle();
        return tooltip;
    }

    function hideTooltip() {
        const tooltip = document.getElementById(TOOLTIP_ID);
        if (tooltip) {
            tooltip.classList.remove('visible');
        }
    }

    function showTooltip(htmlContent, clientX, clientY) {
        const tooltip = ensureTooltipNode();
        tooltip.innerHTML = htmlContent;
        const OFFSET = 14;
        tooltip.style.left = `${clientX + OFFSET}px`;
        tooltip.style.top = `${clientY + OFFSET}px`;
        tooltip.classList.add('visible');
    }

    function formatTooltipContent(dateMs, segment) {
        const date = new Date(dateMs);
        const dateLabel = formatBucketLabel(date, true);
        const value = formatCurrency(segment.value);
        const total = formatCurrency(segment.totalForDay || segment.value);

        const label = segment.seriesLabel || 'Total';
        const totalLabel = bucketType === 'hour' ? 'Hourly Total' : bucketType === 'week' ? 'Weekly Total' : 'Daily Total';

        return `
            <div class="tooltip-title">${dateLabel}</div>
            <div class="tooltip-row">
                <span class="tooltip-label">${label}</span>
                <span>${value}</span>
            </div>
            <div class="tooltip-row">
                <span class="tooltip-label">${totalLabel}</span>
                <span>${total}</span>
            </div>
        `;
    }

    function registerChartLayout(canvas, layout) {
        canvas.__llmSpendLayout = layout;

        if (!canvas.__llmSpendHoverInstalled) {
            installHoverHandlers(canvas);
            canvas.__llmSpendHoverInstalled = true;
        }
    }

    function installHoverHandlers(canvas) {
        canvas.addEventListener('mousemove', (event) => {
            const layout = canvas.__llmSpendLayout;
            if (!layout || !layout.segments.length) {
                hideTooltip();
                return;
            }

            const rect = canvas.getBoundingClientRect();
            const ratio = layout.pixelRatio || window.devicePixelRatio || 1;
            const x = (event.clientX - rect.left) * ratio;
            const y = (event.clientY - rect.top) * ratio;

            const hitSegment = layout.segments.find((segment) => (
                x >= segment.x &&
                x <= segment.x + segment.width &&
                y >= segment.y &&
                y <= segment.y + segment.height
            ));

            if (!hitSegment) {
                hideTooltip();
                return;
            }

            const content = formatTooltipContent(hitSegment.dateMs, hitSegment);
            showTooltip(content, event.clientX, event.clientY);
        });

        canvas.addEventListener('mouseleave', () => {
            hideTooltip();
        });
    }


    function parseJsonScript(id) {
        const el = document.getElementById(id);
        if (!el) {
            return null;
        }

        try {
            return JSON.parse(el.textContent || 'null');
        } catch (error) {
            console.error('Failed to parse JSON script', id, error);
            return null;
        }
    }

    function toNumber(value) {
        const num = Number(value);
        return Number.isFinite(num) ? num : 0;
    }

    function dateToMs(dateInput) {
        if (!dateInput) {
            return null;
        }

        const value = new Date(dateInput);
        const ms = value.getTime();
        return Number.isNaN(ms) ? null : ms;
    }

    function formatCurrency(value) {
        const absVal = Math.abs(value);
        const decimals = absVal >= 100 ? 0 : 2;
        const prefix = value < 0 ? '-$' : '$';
        return prefix + absVal.toFixed(decimals);
    }

    function formatBucketLabel(dateObj, forTooltip = false) {
        if (!(dateObj instanceof Date) || Number.isNaN(dateObj.getTime())) {
            return '';
        }
        if (bucketType === 'hour') {
            const hourLabel = dateObj.toLocaleTimeString(undefined, {hour: 'numeric'});
            return forTooltip ? dateObj.toLocaleString(undefined, {month: 'short', day: 'numeric', hour: 'numeric'}) : hourLabel;
        }
        if (bucketType === 'week') {
            const base = dateObj.toLocaleDateString(undefined, {month: 'short', day: 'numeric'});
            return forTooltip ? `Week of ${base}` : base;
        }

        return dateObj.toLocaleDateString(undefined, {month: 'short', day: 'numeric'});
    }

    function formatDateLabel(dateObj) {
        return formatBucketLabel(dateObj);
    }

    function getCanvasContext(canvas) {
        if (!canvas || typeof canvas.getContext !== 'function') {
            return null;
        }

        const ctx = canvas.getContext('2d');
        if (!ctx) {
            return null;
        }

        const ratio = window.devicePixelRatio || 1;
        const displayWidth = canvas.clientWidth || (canvas.parentElement ? canvas.parentElement.clientWidth : 600) || 600;
        const displayHeight = canvas.clientHeight || 300;

        canvas.width = displayWidth * ratio;
        canvas.height = displayHeight * ratio;
        ctx.setTransform(ratio, 0, 0, ratio, 0, 0);

        return {
            ctx,
            width: displayWidth,
            height: displayHeight,
            pixelRatio: ratio,
        };
    }

    function updateLegend(legendEl, series) {
        if (!legendEl) {
            return;
        }

        legendEl.innerHTML = '';
        if (!Array.isArray(series) || !series.length) {
            return;
        }

        series.forEach((item) => {
            const legendItem = document.createElement('div');
            legendItem.className = 'llm-spend-legend-item';

            const swatch = document.createElement('span');
            swatch.className = 'llm-spend-legend-swatch';
            swatch.style.backgroundColor = item.color || COLOR_PALETTE[0];

            const label = document.createElement('span');
            const displayLabel = (item.label || '').toString();
            label.textContent = `${displayLabel} (${formatCurrency(item.total || 0)})`;

            legendItem.appendChild(swatch);
            legendItem.appendChild(label);
            legendEl.appendChild(legendItem);
        });
    }

    function normalizeSeriesForChart(series, colorOffset = 0) {
        if (!Array.isArray(series)) {
            return [];
        }

        return series.map((item, idx) => {
            const color = item.color || COLOR_PALETTE[(colorOffset + idx) % COLOR_PALETTE.length];
            const values = new Map();

            if (Array.isArray(item.points)) {
                item.points.forEach((point) => {
                    const ms = dateToMs(point.date);
                    if (ms !== null) {
                        values.set(ms, toNumber(point.total));
                    }
                });
            }

            const providedTotal = item.total !== undefined ? toNumber(item.total) : null;
            const fallbackTotal = Array.from(values.values()).reduce((sum, value) => sum + value, 0);

            return {
                label: (item.label || '').toString(),
                color,
                total: providedTotal !== null ? providedTotal : fallbackTotal,
                values,
            };
        });
    }

    function collectSortedDates(series) {
        const dateSet = new Set();
        series.forEach((seriesItem) => {
            seriesItem.values.forEach((_, key) => {
                dateSet.add(key);
            });
        });
        return Array.from(dateSet).sort((a, b) => a - b);
    }

    function renderStackedBarChart(canvas, rawSeries, options = {}) {
        const context = getCanvasContext(canvas);
        if (!context) {
            return;
        }

        const padding = options.padding || {top: 24, right: 24, bottom: 48, left: 80};
        const {ctx, width, height, pixelRatio} = context;
        const plotWidth = Math.max(width - padding.left - padding.right, 0);
        const plotHeight = Math.max(height - padding.top - padding.bottom, 0);

        ctx.clearRect(0, 0, width, height);

        const series = normalizeSeriesForChart(rawSeries, options.colorOffset);
        const activeSeries = series.filter((seriesItem) => seriesItem.total > 0);

        const dates = collectSortedDates(activeSeries);
        if (!dates.length || !plotWidth || !plotHeight) {
            ctx.fillStyle = '#aaaaaa';
            ctx.font = '14px sans-serif';
            ctx.fillText('No data available', padding.left, padding.top + 20);
            updateLegend(options.legendEl, []);
            registerChartLayout(canvas, {
                segments: [],
                totalsByDate: new Map(),
                pixelRatio,
            });
            return;
        }

        const totalsByDate = dates.map((ms) => activeSeries.reduce((sum, seriesItem) => sum + (seriesItem.values.get(ms) || 0), 0));
        let maxY = Math.max(...totalsByDate, 0);
        if (maxY <= 0) {
            maxY = 1;
        }

        const yScale = (value) => height - padding.bottom - (value / maxY) * plotHeight;

        ctx.strokeStyle = '#2f2f2f';
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.moveTo(padding.left, padding.top);
        ctx.lineTo(padding.left, height - padding.bottom);
        ctx.lineTo(width - padding.right, height - padding.bottom);
        ctx.stroke();

        const horizontalGridCount = options.yTicks || 4;
        ctx.font = '12px sans-serif';
        ctx.fillStyle = '#bbbbbb';
        ctx.textAlign = 'right';
        for (let i = 0; i <= horizontalGridCount; i += 1) {
            const value = (i / horizontalGridCount) * maxY;
            const y = yScale(value);

            ctx.strokeStyle = '#1e1e1e';
            ctx.beginPath();
            ctx.moveTo(padding.left, y);
            ctx.lineTo(width - padding.right, y);
            ctx.stroke();

            ctx.fillText(formatCurrency(value), padding.left - 8, y + 4);
        }

        const groupWidth = plotWidth / dates.length;
        const barWidth = Math.min(48, Math.max(12, groupWidth * 0.6));
        ctx.textAlign = 'center';
        ctx.fillStyle = '#bbbbbb';
        dates.forEach((ms, index) => {
            const centerX = padding.left + index * groupWidth + groupWidth / 2;

            ctx.strokeStyle = '#1e1e1e';
            ctx.beginPath();
            ctx.moveTo(centerX, height - padding.bottom);
            ctx.lineTo(centerX, height - padding.bottom + 6);
            ctx.stroke();

            ctx.fillText(formatDateLabel(new Date(ms)), centerX, height - padding.bottom + 20);
        });

        const layoutSegments = [];
        const totalsByDateMap = new Map();

        dates.forEach((ms, index) => {
            const barX = padding.left + index * groupWidth + (groupWidth - barWidth) / 2;
            let currentY = height - padding.bottom;

            activeSeries.forEach((seriesItem) => {
                const value = seriesItem.values.get(ms) || 0;
                if (value <= 0) {
                    return;
                }

                const barHeight = (value / maxY) * plotHeight;
                const adjustedHeight = Math.max(barHeight, 1);
                const topY = currentY - adjustedHeight;
                ctx.fillStyle = seriesItem.color;
                ctx.fillRect(barX, topY, barWidth, adjustedHeight);

                layoutSegments.push({
                    x: barX * pixelRatio,
                    y: topY * pixelRatio,
                    width: barWidth * pixelRatio,
                    height: adjustedHeight * pixelRatio,
                    value,
                    seriesLabel: seriesItem.label,
                    dateMs: ms,
                    color: seriesItem.color,
                    totalForDay: totalsByDate[index],
                });

                currentY -= adjustedHeight;
            });

            totalsByDateMap.set(ms, totalsByDate[index]);
        });

        updateLegend(options.legendEl, activeSeries);
        registerChartLayout(canvas, {
            segments: layoutSegments,
            totalsByDate: totalsByDateMap,
            pixelRatio,
        });
    }

    function initializeData() {
        const metaEl = document.getElementById('llm-spend-meta');
        if (metaEl) {
            bucketType = metaEl.dataset.bucket || 'day';
        } else {
            bucketType = 'day';
        }

        const totalData = parseJsonScript('llm-spend-total-data');
        totalSeries = Array.isArray(totalData) && totalData.length ? [{
            label: 'Total',
            points: totalData.map((point) => ({
                date: point.date,
                total: toNumber(point.total),
            })),
            total: totalData.reduce((sum, point) => sum + toNumber(point.total), 0),
            color: COLOR_PALETTE[0],
        }] : [];

        const vendorData = parseJsonScript('llm-spend-vendor-data');
        vendorSeries = Array.isArray(vendorData) ? vendorData.map((entry, idx) => {
            const points = Array.isArray(entry.points) ? entry.points.map((point) => ({
                date: point.date,
                total: toNumber(point.total),
            })) : [];
            const label = ((entry.vendor || '')).toString();
            const color = COLOR_PALETTE[idx % COLOR_PALETTE.length];
            const total = entry.total !== undefined ? toNumber(entry.total) : points.reduce((sum, point) => sum + point.total, 0);

            return {
                label: label.charAt(0).toUpperCase() + label.slice(1),
                points,
                total,
                color,
            };
        }) : [];

        const businessData = parseJsonScript('llm-spend-business-data');
        businessSeries = Array.isArray(businessData) ? businessData.map((entry, idx) => {
            const points = Array.isArray(entry.points) ? entry.points.map((point) => ({
                date: point.date,
                total: toNumber(point.total),
            })) : [];
            const label = ((entry.business || '')).toString();
            const color = COLOR_PALETTE[(idx + 2) % COLOR_PALETTE.length];
            const total = entry.total !== undefined ? toNumber(entry.total) : points.reduce((sum, point) => sum + point.total, 0);

            return {
                label: label || 'Unassigned',
                points,
                total,
                color,
            };
        }) : [];

        const initiativeData = parseJsonScript('llm-spend-initiative-data');
        initiativeSeries = Array.isArray(initiativeData) ? initiativeData.map((entry, idx) => {
            const points = Array.isArray(entry.points) ? entry.points.map((point) => ({
                date: point.date,
                total: toNumber(point.total),
            })) : [];
            const label = ((entry.initiative || '')).toString();
            const color = COLOR_PALETTE[(idx + 3) % COLOR_PALETTE.length];
            const total = entry.total !== undefined ? toNumber(entry.total) : points.reduce((sum, point) => sum + point.total, 0);

            return {
                label: label || 'Initiative',
                points,
                total,
                color,
            };
        }) : [];

        const taskData = parseJsonScript('llm-spend-task-data');
        taskSeries = Array.isArray(taskData) ? taskData.map((entry, idx) => {
            const points = Array.isArray(entry.points) ? entry.points.map((point) => ({
                date: point.date,
                total: toNumber(point.total),
            })) : [];
            const label = ((entry.task || '')).toString();
            const color = COLOR_PALETTE[(idx + 4) % COLOR_PALETTE.length];
            const total = entry.total !== undefined ? toNumber(entry.total) : points.reduce((sum, point) => sum + point.total, 0);

            return {
                label: label || 'Task',
                points,
                total,
                color,
            };
        }) : [];

        const iterationData = parseJsonScript('llm-spend-iteration-data');
        iterationSeries = Array.isArray(iterationData) ? iterationData.map((entry, idx) => {
            const points = Array.isArray(entry.points) ? entry.points.map((point) => ({
                date: point.date,
                total: toNumber(point.total),
            })) : [];
            const label = ((entry.iteration || '')).toString();
            const color = COLOR_PALETTE[(idx + 5) % COLOR_PALETTE.length];
            const total = entry.total !== undefined ? toNumber(entry.total) : points.reduce((sum, point) => sum + point.total, 0);

            return {
                label: label || 'Iteration',
                points,
                total,
                color,
            };
        }) : [];

        const titleData = parseJsonScript('llm-spend-title-data');
        titleSeries = Array.isArray(titleData) ? titleData.map((entry, idx) => {
            const points = Array.isArray(entry.points) ? entry.points.map((point) => ({
                date: point.date,
                total: toNumber(point.total),
            })) : [];
            const label = ((entry.title || '')).toString();
            const color = COLOR_PALETTE[(idx + 6) % COLOR_PALETTE.length];
            const total = entry.total !== undefined ? toNumber(entry.total) : points.reduce((sum, point) => sum + point.total, 0);

            return {
                label: label || 'Untitled',
                points,
                total,
                color,
            };
        }) : [];
    }

    function renderCharts() {
        const totalCanvas = document.getElementById('llm-spend-total-chart');
        const totalLegend = document.getElementById('llm-spend-total-legend');
        if (totalCanvas) {
            renderStackedBarChart(totalCanvas, totalSeries, {legendEl: totalLegend});
        }

        const initiativeCanvas = document.getElementById('llm-spend-initiative-chart');
        const initiativeLegend = document.getElementById('llm-spend-initiative-legend');
        if (initiativeCanvas) {
            renderStackedBarChart(initiativeCanvas, initiativeSeries, {legendEl: initiativeLegend, colorOffset: 2});
        }

        const taskCanvas = document.getElementById('llm-spend-task-chart');
        const taskLegend = document.getElementById('llm-spend-task-legend');
        if (taskCanvas) {
            renderStackedBarChart(taskCanvas, taskSeries, {legendEl: taskLegend, colorOffset: 3});
        }

        const iterationCanvas = document.getElementById('llm-spend-iteration-chart');
        const iterationLegend = document.getElementById('llm-spend-iteration-legend');
        if (iterationCanvas) {
            renderStackedBarChart(iterationCanvas, iterationSeries, {legendEl: iterationLegend, colorOffset: 4});
        }

        const businessCanvas = document.getElementById('llm-spend-business-chart');
        const businessLegend = document.getElementById('llm-spend-business-legend');
        if (businessCanvas) {
            renderStackedBarChart(businessCanvas, businessSeries, {legendEl: businessLegend, colorOffset: 5});
        }

        const vendorCanvas = document.getElementById('llm-spend-vendor-chart');
        const vendorLegend = document.getElementById('llm-spend-vendor-legend');
        if (vendorCanvas) {
            renderStackedBarChart(vendorCanvas, vendorSeries, {legendEl: vendorLegend, colorOffset: 6});
        }

        const titleCanvas = document.getElementById('llm-spend-title-chart');
        const titleLegend = document.getElementById('llm-spend-title-legend');
        if (titleCanvas) {
            renderStackedBarChart(titleCanvas, titleSeries, {legendEl: titleLegend, colorOffset: 7});
        }
    }

    function chartsPresent() {
        return Boolean(
            document.getElementById('llm-spend-total-chart') ||
            document.getElementById('llm-spend-vendor-chart') ||
            document.getElementById('llm-spend-business-chart') ||
            document.getElementById('llm-spend-initiative-chart') ||
            document.getElementById('llm-spend-task-chart') ||
            document.getElementById('llm-spend-iteration-chart') ||
            document.getElementById('llm-spend-title-chart')
        );
    }

    function boot() {
        if (!chartsPresent()) {
            return;
        }

        initializeData();
        renderCharts();
    }

    const makeDebounce = (typeof window !== 'undefined' && typeof window.debounce === 'function')
        ? (fn, wait) => window.debounce(fn, wait)
        : (fn, wait) => {
            let timeoutId;
            return function (...args) {
                clearTimeout(timeoutId);
                timeoutId = setTimeout(() => fn.apply(this, args), wait);
            };
        };

    const debouncedRender = makeDebounce(renderCharts, 200);

    $(document).ready(() => {
        boot();
        $(window).on('resize.llmSpend', debouncedRender);
    });

    document.addEventListener('on_page_nav', () => {
        boot();
        debouncedRender();
    });
})(jQuery);
