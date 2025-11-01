(function (global, $) {
    const COLOR_PALETTE = [
        '#4e79a7', '#f28e2b', '#e15759', '#76b7b2', '#59a14f',
        '#edc948', '#b07aa1', '#ff9da7', '#9c755f', '#bab0ac'
    ];

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

    function formatTooltipContent(dateMs, segment, bucketType) {
        const date = new Date(dateMs);
        const dateLabel = formatBucketLabel(date, bucketType, true);
        const value = formatCurrency(segment.value);
        const total = formatCurrency(segment.totalForDay || segment.value);

        const label = segment.seriesLabel || 'Total';
        let totalLabel = 'Daily Total';
        if (bucketType === 'hour') {
            totalLabel = 'Hourly Total';
        } else if (bucketType === 'week') {
            totalLabel = 'Weekly Total';
        }

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
            const ratio = layout.pixelRatio || global.devicePixelRatio || 1;
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

            const content = formatTooltipContent(hitSegment.dateMs, hitSegment, layout.bucketType || 'day');
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

    function formatBucketLabel(dateObj, bucketType, forTooltip = false) {
        if (!(dateObj instanceof Date) || Number.isNaN(dateObj.getTime())) {
            return '';
        }

        const type = bucketType || 'day';
        if (type === 'hour') {
            const hourLabel = dateObj.toLocaleTimeString(undefined, {hour: 'numeric'});
            return forTooltip ? dateObj.toLocaleString(undefined, {month: 'short', day: 'numeric', hour: 'numeric'}) : hourLabel;
        }
        if (type === 'week') {
            const base = dateObj.toLocaleDateString(undefined, {month: 'short', day: 'numeric'});
            return forTooltip ? `Week of ${base}` : base;
        }

        return dateObj.toLocaleDateString(undefined, {month: 'short', day: 'numeric'});
    }

    function formatDateLabel(dateObj, bucketType) {
        return formatBucketLabel(dateObj, bucketType);
    }

    function getCanvasContext(canvas) {
        if (!canvas || typeof canvas.getContext !== 'function') {
            return null;
        }

        const ctx = canvas.getContext('2d');
        if (!ctx) {
            return null;
        }

        const ratio = global.devicePixelRatio || 1;
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

        const bucketType = options.bucketType || 'day';
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
                bucketType,
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

            ctx.fillText(formatDateLabel(new Date(ms), bucketType), centerX, height - padding.bottom + 20);
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
            bucketType,
        });
    }

    function makeDebounce(fn, wait) {
        if (typeof global !== 'undefined' && typeof global.debounce === 'function') {
            return global.debounce(fn, wait);
        }

        let timeoutId;
        return (...args) => {
            clearTimeout(timeoutId);
            timeoutId = setTimeout(() => fn(...args), wait);
        };
    }

    const BusinessesLlmSpendView = ErieView.extend({
        el: 'body',

        events: {
            'change #llm-spend-range-select': 'handleRangeChange'
        },

        init_view: function () {
            this.bucketType = 'day';
            this.series = {
                total: [],
                vendor: [],
                business: [],
                initiative: [],
                task: [],
                iteration: [],
                title: [],
            };

            this.windowNamespace = '.llmSpendView';
            this.boundHandlePageNav = this.handlePageNav.bind(this);
            this.navListenerAttached = false;
            this.debouncedRender = makeDebounce(() => {
                this.renderCharts();
            }, 200);

            this.ensureNavListener();
            this.boot();
        },

        boot: function () {
            this.teardownListeners();
            if (!this.chartsPresent()) {
                return;
            }

            this.initializeData();
            this.renderCharts();
            this.bindListeners();
        },

        bindListeners: function () {
            this.ensureNavListener();
            $(window).on('resize' + this.windowNamespace, this.debouncedRender);
        },

        teardownListeners: function () {
            $(window).off('resize' + this.windowNamespace, this.debouncedRender);
        },

        handlePageNav: function () {
            this.boot();
            this.debouncedRender();
        },

        ensureNavListener: function () {
            if (!this.navListenerAttached) {
                document.addEventListener('on_page_nav', this.boundHandlePageNav);
                this.navListenerAttached = true;
            }
        },

        handleRangeChange: function (event) {
            const select = event.currentTarget;
            if (select && select.form) {
                select.form.submit();
            }
            if (typeof last_stop === 'function') {
                return last_stop(event);
            }
            return false;
        },

        chartsPresent: function () {
            return Boolean(
                document.getElementById('llm-spend-total-chart') ||
                document.getElementById('llm-spend-vendor-chart') ||
                document.getElementById('llm-spend-business-chart') ||
                document.getElementById('llm-spend-initiative-chart') ||
                document.getElementById('llm-spend-task-chart') ||
                document.getElementById('llm-spend-iteration-chart') ||
                document.getElementById('llm-spend-title-chart')
            );
        },

        initializeData: function () {
            const metaEl = document.getElementById('llm-spend-meta');
            this.bucketType = metaEl ? (metaEl.dataset.bucket || 'day') : 'day';

            this.series.total = this.buildTotalSeries(parseJsonScript('llm-spend-total-data'));
            this.series.vendor = this.buildEntriesSeries(parseJsonScript('llm-spend-vendor-data'), {
                fallbackLabel: 'Vendor',
                colorOffset: 0,
                labelAccessor: (entry) => {
                    const raw = (entry.vendor || '').toString();
                    if (!raw) {
                        return 'Vendor';
                    }
                    return raw.charAt(0).toUpperCase() + raw.slice(1);
                },
            });
            this.series.business = this.buildEntriesSeries(parseJsonScript('llm-spend-business-data'), {
                fallbackLabel: 'Unassigned',
                colorOffset: 2,
                labelAccessor: (entry) => (entry.business || '').toString() || 'Unassigned',
            });
            this.series.initiative = this.buildEntriesSeries(parseJsonScript('llm-spend-initiative-data'), {
                fallbackLabel: 'Initiative',
                colorOffset: 3,
                labelAccessor: (entry) => (entry.initiative || '').toString() || 'Initiative',
            });
            this.series.task = this.buildEntriesSeries(parseJsonScript('llm-spend-task-data'), {
                fallbackLabel: 'Task',
                colorOffset: 4,
                labelAccessor: (entry) => (entry.task || '').toString() || 'Task',
            });
            this.series.iteration = this.buildEntriesSeries(parseJsonScript('llm-spend-iteration-data'), {
                fallbackLabel: 'Iteration',
                colorOffset: 5,
                labelAccessor: (entry) => (entry.iteration || '').toString() || 'Iteration',
            });
            this.series.title = this.buildEntriesSeries(parseJsonScript('llm-spend-title-data'), {
                fallbackLabel: 'Untitled',
                colorOffset: 6,
                labelAccessor: (entry) => (entry.title || '').toString() || 'Untitled',
            });
        },

        buildTotalSeries: function (data) {
            if (!Array.isArray(data) || data.length === 0) {
                return [];
            }

            const points = data.map((point) => ({
                date: point.date,
                total: toNumber(point.total),
            }));

            const total = points.reduce((sum, point) => sum + point.total, 0);

            return [{
                label: 'Total',
                points,
                total,
                color: COLOR_PALETTE[0],
            }];
        },

        buildEntriesSeries: function (data, options) {
            if (!Array.isArray(data)) {
                return [];
            }

            const opts = options || {};
            const colorOffset = opts.colorOffset || 0;
            const fallbackLabel = opts.fallbackLabel || '';
            const accessor = typeof opts.labelAccessor === 'function' ? opts.labelAccessor : null;

            return data.map((entry, idx) => {
                const points = Array.isArray(entry.points) ? entry.points.map((point) => ({
                    date: point.date,
                    total: toNumber(point.total),
                })) : [];

                const computedLabel = accessor ? accessor(entry, idx) : ((entry.label || '')).toString();
                const label = computedLabel || fallbackLabel;
                const color = COLOR_PALETTE[(idx + colorOffset) % COLOR_PALETTE.length];
                const total = entry.total !== undefined ? toNumber(entry.total) : points.reduce((sum, point) => sum + point.total, 0);

                return {
                    label,
                    points,
                    total,
                    color,
                };
            });
        },

        renderCharts: function () {
            const charts = [
                {canvasId: 'llm-spend-total-chart', legendId: 'llm-spend-total-legend', series: this.series.total, colorOffset: 0},
                {canvasId: 'llm-spend-initiative-chart', legendId: 'llm-spend-initiative-legend', series: this.series.initiative, colorOffset: 2},
                {canvasId: 'llm-spend-task-chart', legendId: 'llm-spend-task-legend', series: this.series.task, colorOffset: 3},
                {canvasId: 'llm-spend-iteration-chart', legendId: 'llm-spend-iteration-legend', series: this.series.iteration, colorOffset: 4},
                {canvasId: 'llm-spend-business-chart', legendId: 'llm-spend-business-legend', series: this.series.business, colorOffset: 5},
                {canvasId: 'llm-spend-vendor-chart', legendId: 'llm-spend-vendor-legend', series: this.series.vendor, colorOffset: 6},
                {canvasId: 'llm-spend-title-chart', legendId: 'llm-spend-title-legend', series: this.series.title, colorOffset: 7},
            ];

            charts.forEach(({canvasId, legendId, series, colorOffset}) => {
                const canvas = document.getElementById(canvasId);
                if (!canvas) {
                    return;
                }

                const legendEl = legendId ? document.getElementById(legendId) : null;
                renderStackedBarChart(canvas, series, {
                    legendEl,
                    colorOffset,
                    bucketType: this.bucketType,
                });
            });
        },

        remove: function () {
            this.teardownListeners();
            if (this.navListenerAttached) {
                document.removeEventListener('on_page_nav', this.boundHandlePageNav);
                this.navListenerAttached = false;
            }
            return Backbone.View.prototype.remove.apply(this, arguments);
        },
    });

    global.BusinessesLlmSpendView = BusinessesLlmSpendView;

    $(document).ready(() => {
        if (!global.businessesLlmSpendView) {
            global.businessesLlmSpendView = new BusinessesLlmSpendView();
        } else if (typeof global.businessesLlmSpendView.boot === 'function') {
            global.businessesLlmSpendView.boot();
        }
    });
})(window, jQuery);
