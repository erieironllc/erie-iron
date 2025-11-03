InfrastructureStacksView = ErieView.extend({
    el: 'body',

    init_view: function () {
        this._containerLookup = {};
        this.initializeContainers();
        this.initializeTableSorting();
        this.initializeDiagramTabs();
    },

    initializeContainers: function () {
        const self = this;
        $('.infrastructure-stacks-tabs[data-base-dom-id]').each(function () {
            const $container = $(this);
            const baseDomId = $container.data('baseDomId');
            if (!baseDomId || self._containerLookup[baseDomId]) {
                return;
            }

            const info = {
                baseDomId: baseDomId,
                container: $container,
                tableTabId: baseDomId + '-table-tab',
                diagramTabId: baseDomId + '-diagram-tab',
                tablePaneId: baseDomId + '-table',
                diagramPaneId: baseDomId + '-diagram-pane',
                diagramHash: '#' + baseDomId + '-diagram'
            };

            self._containerLookup[baseDomId] = info;
        });
    },

    initializeTableSorting: function () {
        if (this._tablesInitialized) {
            return;
        }
        this._tablesInitialized = true;

        const initializeTables = () => {
            const tables = document.querySelectorAll('table[data-table-sortable]');
            tables.forEach((table) => {
                if (table.getAttribute('data-sort-initialized') === 'true') {
                    return;
                }
                table.setAttribute('data-sort-initialized', 'true');

                const defaultHeader = table.querySelector('th[data-sort-default]');
                if (defaultHeader) {
                    const direction = defaultHeader.getAttribute('data-sort-default') || 'asc';
                    this.applySort(table, defaultHeader, direction, true);
                }

                table.querySelectorAll('tr[data-stack-detail-url]').forEach((row) => {
                    row.style.cursor = 'pointer';
                });
            });
        };

        if (document.readyState === 'loading') {
            document.addEventListener('DOMContentLoaded', initializeTables, { once: true });
        } else {
            initializeTables();
        }

        this._tableClickHandler = (event) => {
            const header = event.target.closest && event.target.closest('th[data-sort-key]');
            if (header) {
                const table = header.closest('table[data-table-sortable]');
                if (!table) {
                    return;
                }
                const currentDir = header.getAttribute('data-sort-dir') || 'asc';
                const nextDir = currentDir === 'asc' ? 'desc' : 'asc';
                this.applySort(table, header, nextDir, false);
                event.preventDefault();
                event.stopPropagation();
                return;
            }

            const interactiveElement = event.target.closest && event.target.closest('a, button, input, textarea, select, label');
            if (interactiveElement) {
                return;
            }

            const row = event.target.closest && event.target.closest('tr[data-stack-detail-url]');
            if (!row) {
                return;
            }

            const destination = row.getAttribute('data-stack-detail-url');
            if (destination) {
                window.location.href = destination;
            }
        };

        document.addEventListener('click', this._tableClickHandler);
    },

    parseSortValue: function (rawValue, type) {
        if (type === 'number') {
            const number = parseFloat(rawValue);
            return isNaN(number) ? 0 : number;
        }
        if (type === 'date') {
            const timestamp = Date.parse(rawValue);
            return isNaN(timestamp) ? 0 : timestamp;
        }
        const value = rawValue || '';
        return String(value).toLowerCase();
    },

    applySort: function (table, header, direction, isInitial) {
        if (!table || !header) {
            return;
        }

        const tbody = table.tBodies[0];
        if (!tbody) {
            return;
        }

        const rows = Array.prototype.filter.call(tbody.rows, (row) => {
            return row.getAttribute('data-empty-row') !== 'true';
        });

        if (rows.length === 0) {
            return;
        }

        const columnIndex = Array.prototype.indexOf.call(header.parentNode.children, header);
        const sortType = header.getAttribute('data-sort-type') || 'string';

        rows.sort((rowA, rowB) => {
            const cellA = rowA.cells[columnIndex];
            const cellB = rowB.cells[columnIndex];
            const valueA = cellA ? cellA.getAttribute('data-sort-value') : '';
            const valueB = cellB ? cellB.getAttribute('data-sort-value') : '';
            const parsedA = this.parseSortValue(valueA, sortType);
            const parsedB = this.parseSortValue(valueB, sortType);

            if (parsedA < parsedB) {
                return -1;
            }
            if (parsedA > parsedB) {
                return 1;
            }
            return 0;
        });

        if (direction === 'desc') {
            rows.reverse();
        }

        rows.forEach((row) => {
            tbody.appendChild(row);
        });

        table.querySelectorAll('th[data-sort-key]').forEach((th) => {
            if (th === header) {
                th.setAttribute('aria-sort', direction);
                th.setAttribute('data-sort-dir', direction);
            } else {
                th.setAttribute('aria-sort', 'none');
                th.removeAttribute('data-sort-dir');
            }
        });

        if (!isInitial) {
            const scopeAttr = header.getAttribute('data-sort-key');
            if (scopeAttr) {
                table.setAttribute('data-active-sort', scopeAttr + ':' + direction);
            }
        }
    },

    initializeDiagramTabs: function () {
        if (this._diagramTabsInitialized) {
            return;
        }
        this._diagramTabsInitialized = true;

        this._shownTabHandler = (event) => {
            if (!event.target || !event.target.id) {
                return;
            }
            const targetId = event.target.id;
            const info = this.findContainerByDiagramTabId(targetId);
            if (!info) {
                return;
            }
            window.dispatchEvent(new Event('resize'));
        };
        document.addEventListener('shown.bs.tab', this._shownTabHandler);

        this._boundHashHandler = this.handleHashChange.bind(this);
        window.addEventListener('hashchange', this._boundHashHandler);

        const runOnReady = () => {
            this.maybeActivateFromHash(window.location.hash);
        };

        if (document.readyState === 'loading') {
            document.addEventListener('DOMContentLoaded', runOnReady, { once: true });
        } else {
            runOnReady();
        }
    },

    findContainerByDiagramTabId: function (diagramTabId) {
        const lookup = this._containerLookup || {};
        for (const key in lookup) {
            if (!Object.prototype.hasOwnProperty.call(lookup, key)) {
                continue;
            }
            const info = lookup[key];
            if (info.diagramTabId === diagramTabId) {
                return info;
            }
        }
        return null;
    },

    maybeActivateFromHash: function (hash) {
        if (!hash) {
            return;
        }
        
        const scrollLeft = window.scrollX;
        const scrollTop = window.scrollY;

        const lookup = this._containerLookup || {};
        Object.keys(lookup).forEach((key) => {
            const info = lookup[key];
            if (!info || hash !== info.diagramHash) {
                return;
            }
            this.activateDiagramTab(info);
        });
        
        
        window.requestAnimationFrame(() => {
            window.scrollTo(scrollLeft, scrollTop);
        });
    },

    activateDiagramTab: function (info) {
        if (!info) {
            return;
        }

        const tabButton = document.getElementById(info.diagramTabId);
        if (tabButton) {
            const hasBootstrap = typeof window.bootstrap !== 'undefined' && window.bootstrap.Tab;
            if (hasBootstrap) {
                window.bootstrap.Tab.getOrCreateInstance(tabButton).show();
            } else {
                tabButton.classList.add('active');
                const tableTab = document.getElementById(info.tableTabId);
                if (tableTab) {
                    tableTab.classList.remove('active');
                }
                const diagramPane = document.getElementById(info.diagramPaneId);
                if (diagramPane) {
                    diagramPane.classList.add('show', 'active');
                }
                const tablePane = document.getElementById(info.tablePaneId);
                if (tablePane) {
                    tablePane.classList.remove('show', 'active');
                }
            }
        }

    },

    handleHashChange: function () {
        this.maybeActivateFromHash(window.location.hash);
    },

    remove: function () {
        if (this._tableClickHandler) {
            document.removeEventListener('click', this._tableClickHandler, true);
            this._tableClickHandler = null;
        }
        if (this._boundHashHandler) {
            window.removeEventListener('hashchange', this._boundHashHandler);
            this._boundHashHandler = null;
        }
        if (this._shownTabHandler) {
            document.removeEventListener('shown.bs.tab', this._shownTabHandler);
            this._shownTabHandler = null;
        }
        ErieView.prototype.remove.call(this);
    }
});
