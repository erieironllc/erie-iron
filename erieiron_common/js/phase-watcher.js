(function () {
    const POLL_INTERVAL_MS = 2000;
    let watchers = [];

    function normalizeSeq(value) {
        if (value === undefined || value === null || value === '') {
            return null;
        }
        const parsed = parseInt(value, 10);
        return Number.isNaN(parsed) ? null : parsed;
    }

    function clearAll() {
        watchers.forEach((watcher) => {
            if (watcher.timer) {
                window.clearInterval(watcher.timer);
            }
        });
        watchers = [];
    }

    function handleResponse(watcher, resp) {
        if (!resp) {
            return;
        }
        const incoming = normalizeSeq(resp['phase_change_seq']);
        if (incoming === null) {
            return;
        }

        if (watcher.seq === null || watcher.seq === undefined) {
            watcher.seq = incoming;
            watcher.el.data('phaseWatchSeq', incoming);
            return;
        }

        if (incoming !== watcher.seq) {
            clearAll();
            window.location.reload();
        }
    }

    function startWatcher(el) {
        const $el = $(el);
        const url = $el.data('phaseWatchUrl');
        const taskId = $el.data('phaseWatchTaskId');
        if (!url || !taskId) {
            return;
        }

        const watcher = {
            el: $el,
            url,
            taskId: String(taskId),
            seq: normalizeSeq($el.data('phaseWatchSeq')),
            timer: null,
            inFlight: false,
        };

        const poll = () => {
            if (watcher.inFlight) {
                return;
            }
            watcher.inFlight = true;
            $.ajax({
                url: watcher.url,
                type: 'GET',
                dataType: 'json',
                cache: false,
                success: (resp) => {
                    handleResponse(watcher, resp);
                },
                complete: () => {
                    watcher.inFlight = false;
                },
                error: () => {
                    watcher.inFlight = false;
                }
            });
        };

        watcher.timer = window.setInterval(poll, POLL_INTERVAL_MS);
        poll();
        watchers.push(watcher);
    }

    window.initPhaseWatchers = function () {
        clearAll();
        const seenTasks = new Set();
        $('[data-phase-watch-url]').each((_, el) => {
            const $el = $(el);
            const taskId = $el.data('phaseWatchTaskId');
            if (!taskId) {
                return;
            }
            const key = String(taskId);
            if (seenTasks.has(key)) {
                return;
            }
            seenTasks.add(key);
            startWatcher(el);
        });
    };
})();
