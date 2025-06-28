const ServerCommsManager = ErieView.extend({
    el: 'body',
    messaging_subscribers: {},
    ontime_messaging_subscribers: {},

    events: {
        'click .nav_action': 'nav_el_click',
    },

    init_view: function () {
    },

    init_websocket_connection: function (websocket_url) {
        const ws = new ReconnectingWebSocket(websocket_url, [], {
            reconnectInterval: 1000,
            maxReconnectInterval: 30000,
            reconnectDecay: 1.5,
            maxReconnectAttempts: 10
        });

        ws.onopen = (event) => {
            // console.debug('websocket comms connected');
            this.fire("websocket_connected", event);
        };

        ws.onmessage = (event) => {
            const event_data = JSON.parse(event.data);
            const message_type = event_data['message_type']
            const payload = event_data['payload']
            this.fire(message_type, payload);
        };

        ws.onclose = (event) => {
            // console.debug('websocket comms closed');
            this.fire("websocket_closed", event);
        };

        ws.onerror = (event) => {
            // console.debug('websocket error:', event);
            this.fire("websocket_error", event);
        };
    },

    fire(message_type, payload) {
        if (this.messaging_subscribers[message_type]) {
            this.messaging_subscribers[message_type].forEach(f => {
                setTimeout(() => {
                    try {
                        f(payload);
                    } catch (e) {
                        console.error("error handling", message_type, payload, e);
                    }
                });
            });
        }
        if (this.ontime_messaging_subscribers[message_type]) {
            this.ontime_messaging_subscribers[message_type].forEach(f => {
                setTimeout(() => {
                    try {
                        f(payload);
                    } catch (e) {
                        console.error("error handling", message_type, payload, e);
                    }
                });
            });

            this.ontime_messaging_subscribers[message_type] = null;
        }
    },

    on: function (message_type, handler_function) {
        if (!this.messaging_subscribers[message_type]) {
            this.messaging_subscribers[message_type] = new Set();
        }
        this.messaging_subscribers[message_type].add(handler_function);

        return this;
    },

    once: function (message_type, handler_function) {
        if (!this.ontime_messaging_subscribers[message_type]) {
            this.ontime_messaging_subscribers[message_type] = new Set();
        }
        this.ontime_messaging_subscribers[message_type].add(handler_function);

        return this;
    },

    nav_el_click: function (ev) {
        $('.dropdown-toggle').dropdown('hide');
        $('#search_results_modal').hide();

        const nav_action = $(ev.target).closest(".nav_action");
        const url = nav_action.attr("action");

        $("#top_nav .selected").removeClass("selected");
        if (nav_action.attr("id")) {
            $(`#top_nav #${nav_action.attr("id")}`).addClass("selected");
        }

        if (is_empty(url)) {
            //do nothing
            return;
        } else if (ev.metaKey) {
            window.open(url, "_blank");
        } else {
            this.exec_navigation(url, {
                "trigger_el": nav_action
            });
        }

        return last_stop(ev);
    },

    set_url(url) {
        getBaseView().hide_all_tooltips();
        url = this.normalize_url(url);

        if (backbutton_allowed(url)) {
            if (window.location.pathname + window.location.search !== url) {
                history.pushState({}, '', url);
            }
        }
    },

    normalize_url(url) {
        if (url.pathname) {
            // it's a url object
            url = url.pathname + url.search;
        }

        let url_parts;
        if (Array.isArray(url)) {
            url_parts = url;
        } else {
            url_parts = url.split("/");
        }

        // trim off any trailing null uuids
        for (let i = 0; i < 100; i++) {
            const last_part = url_parts[url_parts.length - 1];
            if (last_part === UUID_NULL_OBJECT) {
                url_parts.pop()
            } else {
                break;
            }
        }

        // trim off any trailing empties
        for (let i = 0; i < 100; i++) {
            const last_part = url_parts[url_parts.length - 1];
            if (is_empty(last_part)) {
                url_parts.pop()
            } else {
                break;
            }
        }
        let normalized_url = url_parts.join('/');
        if (!normalized_url.startsWith("/")) {
            normalized_url = "/" + normalized_url;
        }

        return normalized_url;
    },

    /**
     * exec_navigation is helper to exec_server_post.  it calls exec_server_post, but also logs the interaction and
     * manages the hiding of any elements the might be changing as a result of the nav
     */
    exec_navigation(url, options = {}) {
        getBaseView().hide_all_tooltips();
        close_all_modals();

        url = this.normalize_url(url);

        const callback = options['callback'];
        const trigger_el = get_target_el(options['trigger_el']);

        let hide_el = options['hide_el'];
        if (!hide_el && trigger_el && trigger_el.data("hide_el")) {
            hide_el = trigger_el.data("hide_el");
        }
        if (!hide_el) {
            hide_el = "#main-content-container";
        }
        if (!Array.isArray(hide_el)) {
            hide_el = [hide_el];
        } else if (hide_el.length === 0) {
            hide_el = ["#main-content-container"];
        }

        hide_el.forEach(e => {
            $(e).css('visibility', 'hidden');
        });

        const main_container_el = $(".main-container")[0];
        const main_content_container = $("#main-content-container");

        let post_render_scroll_top;
        if (url.split("/").filter((s) => {
            return s.length > 0
        }).length < 2) {
            // this is a top level page request.  always scroll to top
            post_render_scroll_top = 0;
        } else {
            // this is an url to an element withing the page.  preserve the scrollTop
            post_render_scroll_top = main_container_el.scrollTop;
        }

        this.set_url(url);

        this.exec_server_post(url,
            {"no_header": true},
            (resp) => {
                if (typeof (resp) === 'object') {
                    const redirect_url = resp['redirect_url'];
                    // history.pushState({}, '', redirect_url);
                    if (redirect_url) {
                        window.location.href = redirect_url;
                    }
                } else {
                    if ($("#main-content-container", resp).length === 0) {
                        main_content_container.empty().append(resp);
                    } else {
                        main_content_container.empty().append($("#main-content-container", resp).children());
                    }

                    main_content_container.css('visibility', 'visible');
                    main_container_el.scrollTop = post_render_scroll_top;
                    setTimeout(() => {
                        document.dispatchEvent(new Event('on_page_nav'));
                        getBaseView().init_page();
                        if (callback) {
                            callback();
                        }
                    })
                }
            },
            (error_resp) => {
                main_content_container.css('visibility', 'visible');
                main_container_el.scrollTop = 0;
                console.debug(url, error_resp);
                if (error_resp['responseJSON']) {
                    main_content_container.empty().html("<pre>" + JSON.stringify(error_resp['responseJSON'], null, 2) + "</pre>");
                } else {
                    main_content_container.empty().html("<p class='mt-5'>Erie Iron, LLC experienced an error.  Perhaps try <a href='javascript: window.location.reload()'>reloading the page</a>.<br><br>If the error persists, please contact <a href='mailto:support@collaya.com'>Erie Iron, LLC support</a></p>");
                }
            },
            true // we logged the interaction already above
        );
    },

    /**
     * main interaction point with the server - most anytime the app talks to the server
     * it should go through exec_server_post
     *
     * params:
     *      el:  can either be an url string or a html element.  if it's
     *           an HTML element, this function will look for an attribute
     *           named 'action' to determine the url
     */
    exec_server_post_async: async function (
        el,
        post_data,
        suppress_interaction_logging
    ) {
        return new Promise(async (resolve, reject) => {
            await this.exec_server_post(
                el,
                post_data,
                (resp) => {
                    resolve(resp);
                },
                () => {
                    reject();
                },
                suppress_interaction_logging
            );
        });
    },

    /**
     * main interaction point with the server - most anytime the app talks to the server
     * it should go through exec_server_post
     *
     * params:
     *      el:  can either be an url string or a html element.  if it's
     *           an HTML element, this function will look for an attribute
     *           named 'action' to determine the url
     */
    exec_server_post: async function (
        el,
        post_data,
        f_success,
        f_error,
        suppress_interaction_logging
    ) {
        /* ************
        normalize the params
         ************ */
        if (!el) {
            throw "el is required"
        }
        if (!post_data) {
            post_data = {};
        }
        if (typeof (post_data) === "string") {
            post_data = [post_data];
        }
        if (!f_success) {
            f_success = (d) => {
            }
        }
        if (!f_error) {
            f_error = (d) => {
            }
        }

        let form_data;
        let post_url;
        let container_el;
        if (is_form(el)) {
            form_data = new FormData($(el)[0]);
            post_url = $(el).attr("action");
            container_el = $(el);
        } else {
            form_data = new FormData($("#action_form")[0]);
            if (typeof (el) === "string") {
                post_url = el;
                container_el = $("body");
            } else {
                post_url = $(el).attr("action");
                container_el = $(el);
            }
        }

        if (is_empty(post_url)) {
            console.debug("no action", el);
            throw "No action url"
        }

        if (Array.isArray(post_data)) {
            // they gave us an array of names.  see if the container el
            // has an input with the name or an attr with the name
            post_data.forEach((inp_name) => {
                let val = null;

                let inp = $(`input[name=${inp_name}]`, container_el);
                if (inp.length === 0) {
                    inp = $(`select[name=${inp_name}]`, container_el);
                }
                if (inp.length === 0) {
                    inp = $(`textarea[name=${inp_name}]`, container_el);
                }

                if (inp.length > 0) {
                    val = inp.val();
                } else {
                    val = container_el.attr(inp_name);
                }

                if (val == null) {
                    console.debug("no input found for", inp_name);
                } else if (typeof val !== 'undefined') {
                    form_data.append(inp_name, val);
                }
            });
        } else {
            // they gave us a map of key vaues.  create a hidden input with each of
            // the values and add them to the system form
            Object.entries(post_data).forEach(([key, value]) => {
                if (typeof value !== 'undefined') {
                    form_data.append(key, value);
                } else {
                    // console.warn(key, "value is undefined");
                }
            });
        }

        // console.log(form_data_to_dict(form_data));

        try {
            const resp = await this.server_request(post_url, form_data)
            f_success(resp);
        } catch (resp) {
            let err_data = "";
            if (resp['responseJSON']) {
                err_data = resp['responseJSON'];
            }

            console.debug(
                'ERROR',
                post_url,
                form_data_to_dict(form_data),
                err_data
            );

            f_error(resp);
        }
    },

    exec_rerender: async function (el, post_data, url_override, custom_response_handler) {
        return new Promise((resolve, reject) => {
            post_data = post_data || {};
            post_data["no_header"] = true;

            getBaseView().hide_all_tooltips();

            const target_el = $(el);
            if (target_el.length === 0) {
                return;
            }

            const el_id = target_el[0].id;

            const on_before_render_resp = target_el.triggerHandler("on_before_rerender");
            if (on_before_render_resp === false) {
                console.debug("skipping rerender.  on_before_rerender returned false", target_el);
                resolve();
                return;
            }

            const url = url_override ? url_override : target_el.attr("action");

            target_el.data("rerendering", true);
            this.exec_server_post(url, post_data,
                (resp) => {
                    resp = $(resp);
                    target_el.data("rerendering", null);

                    if (custom_response_handler) {
                        custom_response_handler(target_el, resp);
                    } else if (is_not_empty(el_id) && el_id === resp[0].id) {
                        target_el.empty().append(resp.children());

                        // copy the data attributes over
                        const resp_data_attrs = resp[0].dataset;
                        for (let key in resp_data_attrs) {
                            if (resp_data_attrs.hasOwnProperty(key)) {
                                target_el.data(key, resp_data_attrs[key]);
                            }
                        }
                        scroll_into_view($(".scroll-into-view", resp));
                    } else {
                        target_el.empty().append(resp);
                        scroll_into_view($(".scroll-into-view", resp));
                    }

                    setTimeout(() => {
                        getBaseView().init_page(target_el);
                        $("body").trigger("on_el_rerendered", [el, resp])
                        target_el.trigger("on_rerendered", [el, resp])
                        resolve();
                    }, 1);
                },
                (resp) => {
                    reject();
                }
            )
        });
    },

    server_request: async function (url, form_data) {
        if (!form_data) {
            form_data = new FormData();
        }

        return new Promise((resolve, reject) => {
            try {
                if (is_empty(url)) {
                    reject("no url supplied");
                }

                const csrf_token = $("#action_form input[name=csrfmiddlewaretoken]").val() || getCSRFToken();
                if (csrf_token) {
                    form_data.set("csrfmiddlewaretoken", csrf_token);
                }

                $.ajax({
                    url: url,
                    type: "POST",
                    data: form_data,
                    processData: false,
                    contentType: false,
                    beforeSend: (xhr, settings) => {
                        xhr.setRequestHeader('X-Requested-With', 'XMLHttpRequest');
                        xhr.setRequestHeader('X-CSRFToken', csrf_token);
                    },
                    success: (resp, status, xhr) => {
                        resolve(resp);
                    },
                    error: (resp) => {
                        reject(resp);
                    }
                });
            } catch (e) {
                console.error(e);
                reject(e);
            }
        });
    }
});