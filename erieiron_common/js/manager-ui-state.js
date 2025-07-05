const StateStorageLocation = Object.freeze({
    URL: 'url',
    COOKIE: 'cookie',
    PAGE: 'page'
});

class ManagerUiState {
    ID_LIST_DELIMETER = ",";
    EVENT_BEFORE_UPDATE = "EVENT_BEFORE_UPDATE";
    EVENT_AFTER_UPDATE = "EVENT_AFTER_UPDATE";

    constructor(state_container_el, state_listeners) {
        this.changed_properties = new Set();
        this.state_container_el = $(state_container_el);
        this.state_listeners = state_listeners;
        this.aliases_short_to_long = {
            // this must match the values in the StateProperty python enum
            "p": "project_id",
            "ph": "phrase_id",
            "s": "section_id",
            "a": "arrangement_id",
            "zd": "zoom_duration",
            "zp": "zoom_pan",
            "ppt": "phrase_panel_tab",
            "cpm": "chat_panel_mode",
            "v": "view_name"
        }
        this.aliases_long_to_short = reverse_map(this.aliases_short_to_long);

        // like if you change the project, then arrangement is no longer valid
        this.variable_dependencies = {
            "p": ["ph", "s", "a", "zd", "zp"],
            "a": ["s", "zd", "zp"]
        }

        this.url_stored_properties = Object.keys(this.aliases_long_to_short).map(key => this._get_internal_property_name(key));
        this.cookie_stored_properties = [];
    }

    get_storage_location(prop_name) {
        prop_name = this._get_internal_property_name(prop_name);

        if (this.url_stored_properties.includes(prop_name)) {
            return StateStorageLocation.URL;
        } else if (this.cookie_stored_properties.includes(prop_name)) {
            return StateStorageLocation.COOKIE;
        } else {
            return StateStorageLocation.PAGE;
        }
    }

    get_state() {
        const state = $(this.state_container_el).length ? Object.fromEntries(
            Object.keys($(this.state_container_el).data())
                .map(key => {
                    return [
                        this._get_internal_property_name(key),
                        $(this.state_container_el).data(key)
                    ];
                })
        ) : {};

        const url_search_parms = new URLSearchParams(window.location.search);
        url_search_parms.entries().forEach(([property_name, value]) => {
            property_name = this._get_internal_property_name(property_name);
            if (this.url_stored_properties.includes(property_name)) {
                state[property_name] = value;
            }
        });

        return state;
    }

    get_float(property_name, default_value = 0) {
        const v = parseFloat(this.get(property_name, default_value));
        if (isNaN(v)) {
            return default_value;
        } else {
            return v;
        }
    }

    get_list(property_name) {
        const v = this.get(property_name);
        if (v === undefined || v === null || v === UUID_NULL_OBJECT) {
            return [];
        }
        return v.split(this.ID_LIST_DELIMETER);
    }

    get(property_name, default_value = null) {
        const v = this.get_state()[this._get_internal_property_name(property_name)];
        if (v === undefined || v === null || v === UUID_NULL_OBJECT) {
            return default_value;
        }
        return v;
    }

    unset(property_name) {
        const d = ensure_list(property_name)
            .reduce((acc, property_name) => {
                acc[property_name] = UUID_NULL_OBJECT;
                return acc;
            }, {});

        this.set(d);
    }

    async set_no_update(property_name, value) {
        if (is_dict(property_name)) {
            await this._set_state_internal(property_name, true);
        } else if (is_jquery_object(property_name)) {
            const d = Object.entries(this.aliases_long_to_short)
                .reduce((acc, [long_name, alias_name]) => {
                    const value = property_name.data(long_name);
                    if (value !== undefined) {
                        acc[alias_name] = value;
                    }
                    return acc;
                }, {});

            await this._set_state_internal(d, true);
        } else {
            await this._set_state_internal({
                [property_name]: value
            }, true);
        }
    }

    async add(property_name, value) {
        const current_values = ensure_list(this.get_list(property_name));
        current_values.push(value);

        await this.set(property_name, current_values.join(this.ID_LIST_DELIMETER));

        return current_values;
    }

    async set(property_name, value) {
        if (Array.isArray(value)) {
            value = value.join(this.ID_LIST_DELIMETER);
        }

        if (is_dict(property_name)) {
            await this._set_state_internal(property_name);
        } else if (is_jquery_object(property_name)) {
            const d = Object.entries(this.aliases_long_to_short)
                .reduce((acc, [long_name, alias_name]) => {
                    const value = property_name.data(long_name);
                    if (value !== undefined) {
                        acc[alias_name] = value;
                    }
                    return acc;
                }, {});

            await this._set_state_internal(d);
        } else {
            await this._set_state_internal({
                [property_name]: value
            });
        }
    }

    async _set_state_internal(dict_prop_name_val, suppress_update) {
        if (!dict_prop_name_val) {
            return;
        }
        const initial_state = this.get_state();
        this.state_container_el.trigger(this.EVENT_BEFORE_UPDATE, [initial_state]);

        // figure out what's changing
        for (let [property_name, value] of Object.entries(dict_prop_name_val)) {
            const internal_property_name = this._get_internal_property_name(property_name);
            if (!is_equal(initial_state[internal_property_name], value)) {
                this.changed_properties.add(internal_property_name)
            }
        }

        // clear out the dependent vars
        (new Set(this.changed_properties)).forEach((property_name) => {
            ensure_list(this.variable_dependencies[property_name]).forEach((dependent_var) => {
                dict_prop_name_val[dependent_var] = UUID_NULL_OBJECT;
                this.changed_properties.add(dependent_var);
            })
        })
        const url = this.get_state_url();

        // update the url for the new state
        for (let [property_name, value] of Object.entries(dict_prop_name_val)) {
            const external_property_name = this._get_external_property_name(property_name);
            const internal_property_name = this._get_internal_property_name(property_name);

            $(this.state_container_el).data(external_property_name, value);

            if (this.url_stored_properties.includes(internal_property_name)) {
                if (url.searchParams.get(internal_property_name) !== value) {
                    if (value === undefined || value === UUID_NULL_OBJECT) {
                        url.searchParams.delete(internal_property_name);
                    } else {
                        url.searchParams.set(internal_property_name, value);
                    }
                }
            }
        }

        if (url.href !== window.location.href) {
            window.history.pushState({}, '', url);
        }

        if (suppress_update) {
            this.changed_properties = new Set();
        } else {
            if (!this.updating) {
                // if we get more updates while we are updating, just queue them up
                while (true) {
                    try {
                        this.updating = true;
                        const clone_changed_properties = Array.from(this.changed_properties);
                        this.changed_properties = new Set();
                        await this._update_listeners(clone_changed_properties);
                    } finally {
                        this.updating = false;
                    }

                    if (!this.changed_properties || this.changed_properties.size === 0) {
                        break;
                    }
                }
            }
        }
    }

    get_state_url(cache_bust = false) {
        const initial_state = this.get_state();

        // start constructing url with existing state, then overlay on the query params (which will overwrite the changed vals)
        const url = new URL(window.location.href);
        Object.entries(initial_state)
            .forEach(([property_name, value]) => {
                property_name = this._get_internal_property_name(property_name);

                if (!this.url_stored_properties.includes(property_name)) {
                    return;
                }

                if (!value) {
                    return;
                }
                if (value === UUID_NULL_OBJECT) {
                    return;
                }

                url.searchParams.set(property_name, value)
            });

        if (cache_bust) {
            url.searchParams.set("cb", (new Date()).getTime());
        }

        return url;
    }

    async _update_listeners(changed_properties) {
        // identify the functions listening to the changed properties
        const unique_listeners = new Set();
        changed_properties
            .forEach((property_name) => {
                if (property_name in this.aliases_short_to_long) {
                    const alias_name = this.aliases_short_to_long[property_name];
                    ensure_list(this.state_listeners[alias_name]).forEach(listener_func => {
                        unique_listeners.add(listener_func);
                    });
                }

                ensure_list(this.state_listeners[property_name]).forEach(listener_func => {
                    unique_listeners.add(listener_func);
                });
            });

        // normalize the listeners to either jq's or functions
        const jq_listeners = [];
        const non_jq_listeners = [];
        Array.from(unique_listeners).forEach((listener_func) => {
            if (is_jquery_object(listener_func)) {
                jq_listeners.push(listener_func);
            } else if (typeof (listener_func) === 'string') {
                if ($(listener_func).length > 0) {
                    jq_listeners.push($(listener_func));
                } else {
                    console.warn("listener not found", listener_func);
                }
            } else if (typeof (listener_func) === 'function') {
                non_jq_listeners.push(listener_func);
            } else {
                console.warn("unknown listener type", listener_func);
            }

        });
        const normalized_listeners = [...non_jq_listeners, ...jq_listeners];

        // create a version of the state object with the alias populated to pass to the function listeners
        const new_state = this.get_state();
        const state_with_aliases = {...new_state};
        for (const [property_name, prop_name_alias] of Object.entries(this.aliases_short_to_long)) {
            state_with_aliases[prop_name_alias] = state_with_aliases[property_name]
        }

        await Promise.all(
            Array.from(normalized_listeners)
                .map(listener => {
                        if (is_jquery_object(listener)) {
                            const needs_refresh = Object.entries(state_with_aliases)
                                .filter(([property_name, val]) => {
                                    return listener.data(property_name) !== undefined;
                                })
                                .some(
                                    ([property_name, val]) =>
                                        listener.data(property_name) !== val
                                );

                            if (needs_refresh) {
                                return erie_server().exec_rerender(
                                    listener,
                                    new_state
                                );
                            } else {
                                return null;
                            }
                        } else if (typeof (listener) === 'function') {
                            return Promise.resolve(listener(state_with_aliases));
                        } else {
                            console.warn("unknown listener type", listener);
                            return null;
                        }
                    }
                ).filter((promise) => promise)
        );

        this.state_container_el.trigger(this.EVENT_AFTER_UPDATE, [this.get_state()]);
    }

    _get_external_property_name(property_name) {
        if (property_name in this.aliases_short_to_long) {
            return this.aliases_short_to_long[property_name];
        } else {
            return property_name;
        }
    }

    _get_internal_property_name(property_name) {
        if (property_name in this.aliases_long_to_short) {
            return this.aliases_long_to_short[property_name];
        } else {
            return property_name;
        }
    }

    async rerender(el, post_data, custom_response_handler = null) {
        el = $(el)
        post_data = post_data || {};


        return await erie_server().exec_rerender(
            el,
            {...this.get_state(), ...post_data},
            null,
            custom_response_handler
        );
    }
}

function is_equal(val1, val2) {
    if (val1 === "" && val2 === UUID_NULL_OBJECT) {
        return true;
    } else if (val2 === "" && val1 === UUID_NULL_OBJECT) {
        return true;
    } else {
        return val1 === val2;
    }
}