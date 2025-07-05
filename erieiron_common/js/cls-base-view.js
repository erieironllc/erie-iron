ErieView = Backbone.View.extend({
    _bound_events: [],
    _logged_el_set: [],

    initialize: function (options) {
        if (this['init_view']) {
            this['init_view'](options);
        }
        this.delegateEvents();
    },

    delegateEvents: function (events) {
        this.undelegateEvents();
        events = events || _.result(this, 'events');
        if (!events) return this;

        _.each(events, (method, key) => {
            if (!_.isFunction(method)) {
                method = this[method];
            }
            if (!method) {
                return;
            }

            const km = `${key}_${method}`;
            const key_parts = key.split(" ");
            const event_name = key_parts[0];
            const selector = key_parts.slice(1).join(" ")

            if ($(selector, this.el).length === 0) {
                return;
            }

            if (this._bound_events.includes(km)) {
                return
            }
            this._bound_events.push(km);

            this.$el.off(event_name, selector).on(event_name, selector, function (event) {
                try {
                    if (selector === "#delegate_events_tester") {
                        return;
                    }

                    if (!["click", "dblclick"].includes(event_name)) {
                        return;
                    }

                    this._log_interaction(event, selector);
                } finally {
                    method.apply(this, arguments);
                }
            }.bind(this));
        });

        return this;
    },

    _log_interaction: function (event, selector) {
        if (selector === "#delegate_events_tester") {
            return
        }

        const selector_el = $(event.target).closest(selector);
        if (selector_el.data("interaction_name")) {
            selector = selector_el.data("interaction_name");
        }

        if (selector === ".nav_action") {
            selector = get_unique_selector(event.target);
        }

        if (selector) {
            // debounce logging
            clearTimeout(this.logged_el_set_clear_timeout);
            if (this._logged_el_set.includes(selector)) {
                return;
            }

            this._logged_el_set.push(selector);
            this.logged_el_set_clear_timeout = setTimeout(() => {
                this._logged_el_set = []
            }, 500);

            if (window['gtag']) {
                gtag('event', 'user_interaction', {
                    'element': selector
                });
            } else {
                // console.debug("log_interaction", selector);
            }
        }
    }
});