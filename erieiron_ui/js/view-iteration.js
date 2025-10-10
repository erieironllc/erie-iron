IterationView = ErieView.extend({
    el: 'body',

    events: {
        'click .code_type_toggle': 'code_type_toggle_click'
    },

    init_view: function (options) {
        this.setupLogPolling();
    },

    setupLogPolling: function () {
        this.clearLogPolling();

        const logsEl = $('#div_logs');
        if (logsEl.length === 0) {
            return;
        }

        const node = logsEl[0];
        if (node) {
            node.scrollTop = node.scrollHeight;
        }

        logsEl.data('currentLogText', logsEl.text());

        this._logPollingInFlight = false;

        const fetchLogs = () => {
            const target = $('#div_logs');
            if (target.length === 0) {
                this.clearLogPolling();
                return;
            }

            const fetchUrl = target.data('logsUrl');
            if (!fetchUrl) {
                return;
            }

            if (this._logPollingInFlight) {
                return;
            }
            this._logPollingInFlight = true;

            $.ajax({
                url: fetchUrl,
                type: 'GET',
                dataType: 'json',
                cache: false,
                success: (resp) => {
                    if (!resp) {
                        return;
                    }

                    const logText = resp['log_text'] || '';
                    const destination = $('#div_logs');
                    if (destination.length === 0) {
                        this.clearLogPolling();
                        return;
                    }

                    const previous = destination.data('currentLogText');
                    if (previous === logText) {
                        return;
                    }

                    destination.text(logText);
                    destination.data('currentLogText', logText);

                    const destNode = destination[0];
                    if (destNode) {
                        destNode.scrollTop = destNode.scrollHeight;
                    }
                },
                complete: () => {
                    this._logPollingInFlight = false;
                },
                error: () => {
                }
            });
        };

        fetchLogs();
        this._logPollingTimer = window.setInterval(fetchLogs, 1000);
        this._logPollingFn = fetchLogs;
    },

    clearLogPolling: function () {
        if (this._logPollingTimer) {
            window.clearInterval(this._logPollingTimer);
            this._logPollingTimer = null;
        }
        this._logPollingInFlight = false;
        this._logPollingFn = null;
    },

    code_type_toggle_click: function (ev) {
        log("here 2");
        const btn = $(ev.target).closest(".code_type_toggle");
        const card = btn.closest(".card");

        $(".full_code", card).show(
            btn.hasClass("code_diff")
        );

        $(".code_diff", card).show(
            btn.hasClass("full_code")
        );

        return last_stop(ev)
    },
});
