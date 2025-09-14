BaseContainerView = ErieView.extend({
    el: 'body',

    events: {
        'mousedown .resizer_bar-horiz': 'horiz_resizer_mousedown',
        'mousedown .resizer_bar-vert': 'vert_resizer_mousedown',

        'click .list-group-item': 'nav_click',
        'change #txt_llm_search': 'txt_llm_search_change',
        'change #statusFilter': 'statusFilter_change',
        'click .modal .btn-cancel': 'modal_cancel_click',
        'click #modal_cookie_consent .btn-primary': 'btn_accept_cookie_consent_click',

        'click .erie-toggle-btn': 'toggle_btn_click',
        'click .erie-companion-click': 'btn_companion_click',

        'mouseenter .has_submenu': 'show_submenu',
        'mouseleave .has_submenu': 'hide_submenu',

        'click': 'body_onclick',
        'click .btn-viewmode': 'viewmode_onclick',
        'click .copy-btn': 'copy_to_clipboard_click',

        'click .nav_search': 'nav_search_click',
        'blur .nav_search_input input': 'nav_search_blur',

        'click #top-feedback': 'top_feedback_click',
        'focus #txt_global_search': 'global_search_focus',
        'keypress #txt_global_search': 'global_search_keypress',

        'click #delegate_events_tester': 'delegate_events_tester_click'
    },

    init_view: function (options) {
        this.tooltip_instances = [];


        $(document).ready(function () {
            // Handle chevron rotation for collapsible LLM requests
            $('.card-header[data-bs-toggle="collapse"]:not(.copy-btn)').on('click', function () {
                const chevron = $(this).find('.collapse-icon');
                const target = $($(this).data('bs-target'));

                if (target.hasClass('show')) {
                    chevron.removeClass('fa-chevron-down').addClass('fa-chevron-right');
                } else {
                    chevron.removeClass('fa-chevron-right').addClass('fa-chevron-down');
                }
            });
        });

        setTimeout(() => {
            $("input[type=text]").first().focus();
        }, 100);


        const selected_tab_id = get_cookie("selected_tab_id");
        let the_tab = null;
        if (selected_tab_id) {
            the_tab = $(`#${selected_tab_id}`);
        }
        if (the_tab == null || the_tab.length === 0) {
            the_tab = $(".list-group-item").first()
        } else {
            $(".list-group-item.active").removeClass("active");
        }
        the_tab.addClass("active");
        $(".tab-pane.active").removeClass("active");
        $(`#${the_tab.attr("aria-controls")}`).addClass("active")

        this.init_page();


        const hash = window.location.hash;
        if (hash) {
            const tabTrigger = document.querySelector(`button[data-bs-target="${hash}"]`);
            if (tabTrigger) {
                const tab = new bootstrap.Tab(tabTrigger);
                tab.show();
            }
        }

        // Optional: update the URL hash when a tab is clicked
        document.querySelectorAll('button[data-bs-toggle="tab"]').forEach(btn => {
            btn.addEventListener('shown.bs.tab', function (event) {
                history.replaceState(null, null, event.target.dataset.bsTarget);
            });
        });

        document.addEventListener("wheel", function (event) {
            if (event.ctrlKey) {
                event.preventDefault();
            }
        }, {passive: false});

        document.addEventListener('contextmenu', this.show_context_menu.bind(this));

        document.addEventListener('on_page_nav', () => {
            this.delegateEvents();
        });

        document.addEventListener('keydown', (event) => {
            if ((event.metaKey || event.ctrlKey) && event.shiftKey && event.key === 'z') {
                $("body").trigger("on_redo_requested");
            } else if ((event.metaKey || event.ctrlKey) && event.key === 'z') {
                $("body").trigger("on_undo_requested");
            } else if (event.key === "Escape") {
                $("#erie_context_menu").hide();
                close_all_modals();
                close_all_dropdowns();
            } else if (event.key === "Enter" && !event.shiftKey) {
                $(".modal:visible .btn-primary").click();
            }
        });

        document.addEventListener('click', (ev) => {
            $("#erie_context_menu").hide();

            if (!$(ev.target).closest(".tool-modal").length && !$(ev.target).closest(".tool-modal-launcher").length) {
                $(".tool-modal").hide();
            }

            this.hide_notification_bubbles();
        });

        window.addEventListener('popstate', (e) => {
            if (backbutton_allowed(window.location.pathname)) {
                setTimeout(() => {
                    erie_server().exec_navigation(window.location.pathname + window.location.search, {
                        "trigger_el": $(".nav_container")
                    });
                }, 200);
            }
        });

        $(".erie_infinite_scroll").each((idx, el) => {
            this.init_infinite_scroll($(el));
        });

        $(".erie-require-input-values").each((idx, el) => {
            const btn = $(el);
            default_string(btn.data("required_inputs")).split(",").forEach(required_input_id => {
                btn.class_if("disabled", is_empty($(required_input_id).val()));
                $(required_input_id).on("keyup", () => {
                    btn.class_if("disabled", is_empty($(required_input_id).val()));
                });
            })
        });


        this.setupKeyCapture();

        setInterval(() => {
            if (!window._last_delegate_events_tester_click) {
                window._last_delegate_events_tester_click = (new Date()).getTime();
            }

            const ts = window._last_delegate_events_tester_click;
            $("#delegate_events_tester").click();
            setTimeout(() => {
                if (window._last_delegate_events_tester_click === ts) {
                    // we didn't update _last_delegate_events_tester_click after clicking #delegate_events_tester
                    // this means events are happening.  re-delegate events
                    // console.debug("re-delegating events", window._last_delegate_events_tester_click);
                    this.delegateEvents();
                }
            }, 100);
        }, 2000);
        
        this.delegateEvents()


    },

    delegate_events_tester_click(target_el) {
        window._last_delegate_events_tester_click = (new Date()).getTime();
    },

    init_page(target_el) {
        getServerCommsManager()
            .on("signed_waveform_urls_generated", this.set_waveform_urls.bind(this))
        ;

        const content_title = $(".main_content").attr("title");
        if (content_title) {
            document.title = "Erie Iron, LLC - " + content_title;
        } else {
            document.title = "Erie Iron, LLC";
        }

        this.swizzle_waveform_urls();

        $(".erie_toggle-active").data("is_on", true);

        $("#top_nav--page_menus").html(
            $("#div_page_specific_menus #top_menu_page_content").html()
        );

        $("#top_nav--buttons").html(
            $("#div_page_specific_nav_buttons").html()
        );

        if (target_el) {
            init_dropdowns(target_el);
        } else {
            init_dropdowns($('body'));
        }

        this.tooltip_instances = []

        $(".btn, .btn-tooltip, .titled").each((idx, btn_el) => {
            const btn = $(btn_el);
            if (is_empty(btn.data("bs-toggle")) && is_empty(btn.text().trim()) && is_not_empty(btn.attr("title"))) {
                btn.data("bs-toggle", "tooltip");
                const tt = new bootstrap.Tooltip(btn_el);
                btn_el.addEventListener('shown.bs.tooltip', function () {
                    setTimeout(() => {
                        tt.hide();
                    }, 3000)
                });
                this.tooltip_instances.push(tt);
            }
        });

        $(".erie-toggle-btn-save-state").each((idx, el) => {
            el = $(el);

            if (el.data("default_on")) {
                if (parse_bool(get_cookie(el.attr("id")))) {
                    el.removeClass("erie_toggle-active");
                } else {
                    el.addClass("erie_toggle-active");
                }
            } else {
                if (parse_bool(get_cookie(el.attr("id")))) {
                    el.addClass("erie_toggle-active");
                }
            }
            el.trigger("toggled", [el.hasClass("erie_toggle-active"), true]);
        });

        if ($("#modal_cookie_consent").length) {
            $("#modal_cookie_consent").show();
        }
    },

    async set_waveform_urls(resp, map_id_to_el, map_id_to_src) {
        if (!map_id_to_el) {
            map_id_to_el = {};
            $(".waveform-placeholder").each((idx, el) => {
                const waveform_id = $(el).data("waveform_id");
                if (!map_id_to_el[waveform_id]) {
                    map_id_to_el[waveform_id] = [];
                }
                map_id_to_el[waveform_id].push(el);
            });
        }

        if (!map_id_to_src) {
            map_id_to_src = await local_db_get("waveform_url_cache", "map_id_to_src");
        }

        Object.keys(resp).forEach((waveform_id) => {
            const waveform_url = resp[waveform_id];
            if (is_cloudfront_url_valid(waveform_url)) {
                const els = map_id_to_el[waveform_id];
                if (els) {
                    els.forEach(el => {
                        el.src = waveform_url;
                        $(el).data("waveform_url", waveform_url).removeClass("visually-hidden");
                    });
                }
            }
        });


        $(".waveform-placeholder").each((idx, el) => {
            const waveform_id = $(el).data("waveform_id");

            if (el.src && !is_cloudfront_url_valid(map_id_to_src[waveform_id])) {
                map_id_to_src[waveform_id] = el.src;
            }
        });
        local_db_put("waveform_url_cache", "map_id_to_src", map_id_to_src);
    },

    async swizzle_waveform_urls() {

        const formData = new FormData();
        const map_id_to_el = {};

        const map_id_to_src = await local_db_get("waveform_url_cache", "map_id_to_src");
        const cached_image_load_promises = [];


        function add_to_download_list(el) {
            const waveform_id = $(el).data("waveform_id");
            if (!map_id_to_el[waveform_id]) {
                map_id_to_el[waveform_id] = [];
            }

            map_id_to_el[waveform_id].push(el);
            formData.append("id", waveform_id);
        }

        // Timeout-wrapped image loader
        function load_with_timeout(el, url, timeout_ms = 3000) {
            const waveform_id = $(el).data("waveform_id");
            return new Promise((resolve, reject) => {
                let timed_out = false;

                const timeout = setTimeout(() => {
                    timed_out = true;
                    add_to_download_list(el);
                    reject(new Error("timeout"));
                }, timeout_ms);

                el.onload = () => {
                    clearTimeout(timeout);
                    if (!timed_out) {
                        resolve();
                    }
                };

                el.onerror = () => {
                    clearTimeout(timeout);
                    if (!timed_out) {
                        add_to_download_list(el);
                        reject(new Error("load error"));
                    }
                };

                $(el).data("waveform_url", url).removeClass("visually-hidden");
                el.src = url;
            });
        }

        $(".waveform-placeholder").each((idx, el) => {
            const waveform_id = $(el).data("waveform_id");
            const cached_url = map_id_to_src[waveform_id];


            if (is_cloudfront_url_valid(cached_url)) {
                cached_image_load_promises.push(load_with_timeout(el, cached_url));
            } else {
                add_to_download_list(el);
            }
        });

        await Promise.allSettled(cached_image_load_promises);

        if (formData.getAll("id").length) {
            const resp = await getServerCommsManager().server_request($("body").data("waveform_url"), formData);
            this.set_waveform_urls(resp, map_id_to_el);
        }

    },

    hide_notification_bubbles() {
        $(".notification-bubble").hide();
    },

    show_context_menu: function (event) {
        if (['INPUT', 'TEXTAREA'].includes(event.target.tagName)) {
            return;
        }

        if (event.shiftKey) {
            return;
        }
        const top_menu_dropdowns = $("#top_menu_page_content .dropdown");
        if (top_menu_dropdowns.length === 0) {
            return;
        }

        const erie_context_menu = $("#erie_context_menu");

        let menu_content = null;
        const ev_el = $(event.target).closest(".has_context_menu");
        if (ev_el.length && $(ev_el.data("context_menu")).length) {
            erie_context_menu.empty().append($(ev_el.data("context_menu")).clone().removeClass("d-none"));
        } else {
            menu_content = top_menu_dropdowns.toArray().map((el, idx) => {
                const sub_menus = $(".dropdown-menu", el);
                if (idx === 0) {
                    return sub_menus.html();
                } else {
                    const li_class = sub_menus.length ? "has_submenu" : "";
                    const sub_menus_html = `<ul class="submenu d-none">${sub_menus.html()}</ul>`;
                    return `<div class="divider"></div><li class="${li_class}">${$(".dropdown-toggle", el).first().text().trim()} ${sub_menus_html}</li>`;
                }
            }).join("");
            erie_context_menu.empty().append(`<ul>${menu_content}</ul>`)
            $(".no-context-menu", erie_context_menu).remove();
        }

        erie_context_menu
            .data("original_event", event)
            .css({
                top: event.clientY + 'px',
                left: event.clientX + 'px'
            })
            .show();

        // Make sure the menu is fully visible
        const window_width = $(window).width();
        const window_height = $(window).height();
        const menu_width = erie_context_menu.outerWidth();
        const menu_height = erie_context_menu.outerHeight();

        let new_left = event.clientX;
        let new_top = event.clientY;

        if (new_left + menu_width > window_width) {
            new_left = window_width - menu_width - 10;
        }
        if (new_top + menu_height > window_height) {
            new_top = window_height - menu_height - 10;
        }

        erie_context_menu.css({
            top: new_top + 'px',
            left: new_left + 'px'
        });


        try {
            $(".checked", $(event.currentTarget).find('.submenu'))[0].scrollIntoView({'block': 'center'});
        } catch (e) {
        }

        return last_stop(event);
    },

    show_submenu: function (event) {
        const sub_menu = $(event.currentTarget).find('.submenu');

        sub_menu.show();

        const window_width = $(window).width();
        const window_height = $(window).height();
        const sub_menu_offset = sub_menu.offset();
        const sub_menu_width = sub_menu.outerWidth();
        const sub_menu_height = sub_menu.outerHeight();

        let new_left = sub_menu_offset.left;
        let new_top = sub_menu_offset.top;

        if (new_left + sub_menu_width > window_width) {
            new_left = window_width - sub_menu_width - 10;
        }
        if (new_top + sub_menu_height > window_height) {
            new_top = window_height - sub_menu_height - 10;
        }

        sub_menu.offset({top: new_top, left: new_left});

        try {
            $(".checked", $(event.currentTarget).find('.submenu'))[0].scrollIntoView({'block': 'center'});
        } catch (e) {
        }
    },

    hide_submenu: function (event) {
        $(event.currentTarget).find('.submenu').hide();
    },

    btn_companion_click: function (ev) {
        $(
            $(ev.target).closest(".erie-companion-click").data("companion")
        ).trigger("click");
        return last_stop(ev);
    },

    toggle_btn_click: function (ev) {
        const btn = $(ev.target).closest(".erie-toggle-btn");
        let is_on;
        if (btn.hasClass("erie_toggle-active")) {
            btn.removeClass("erie_toggle-active").data("is_on", false);
            is_on = false;
        } else {
            btn.addClass("erie_toggle-active").data("is_on", true);
            is_on = true;
        }

        this.hide_all_tooltips();
        btn.trigger("toggled", [is_on, false]);
        if (btn.hasClass("erie-toggle-btn-save-state")) {
            if (btn.data("default_on")) {
                set_cookie(btn.attr("id"), is_on ? 0 : 1)
            } else {
                set_cookie(btn.attr("id"), is_on ? 1 : 0)
            }

        }
    },

    nav_click: function (ev) {
        const id = $(ev.target).attr("id");
        if (id) {
            set_cookie("selected_tab_id", id);
            console.log("Saved tab id:", id);
        }
    },

    txt_llm_search_change: function (ev) {
        const search_val = $("#txt_llm_search").val().toLowerCase();
        if (!search_val) {
            $(".llm_request_card").show();
        } else {
            $(".llm_request_card").each((idx, el) => {
                const card_text = $("pre", el)[0].innerText.toLowerCase();
                if (card_text.indexOf(search_val) >= 0) {
                    $(el).show();
                } else {
                    $(el).hide();
                }
            })
        }
    },

    statusFilter_change: function (ev) {
        const selected_status = $(ev.target).val();
        if (!selected_status) {
            $(".task_card").show();
        } else {
            $(".task_card").each((idx, el) => {
                el = $(el);
                el.show(el.data("status") === selected_status);
            });
        }
    },

    modal_cancel_click: function (ev) {
        const modal = $(ev.target).closest(".modal");
        modal.hide();
    },

    show_notification_bubble: function (attached_to, text, on_click) {
        const bubble = $("#generic-notification-bubble")
            .css({
                "cursor": on_click ? "pointer" : "default",
                "top": attached_to.offset().top + 30,
                "left": attached_to.offset().left - 14
            })
            .text(text)
            .show()
            .off("click").on("click", (ev) => {
                if (on_click) {
                    on_click(ev);
                }
            });

        const window_height = $(window).height();
        const bubble_height = bubble.outerHeight();
        const attached_to_bottom = attached_to.offset().top + attached_to.outerHeight() + 30;

        if (attached_to_bottom + bubble_height > window_height) {
            bubble.addClass("painted_above")
                .css("top", attached_to.offset().top - bubble_height - 10); // Position above the element
        } else {
            bubble.removeClass("painted_above")
        }
    },

    hide_all_tooltips: function () {
        this.tooltip_instances.forEach(tt => {
            tt.hide();
        });

        setTimeout(() => {
            this.tooltip_instances.forEach(tt => {
                tt.hide();
            });
        }, 500);
    },

    setupKeyCapture: function () {
        $(document).keydown((ev) => {
            // if ((ev.metaKey || ev.ctrlKey) && ev.key === 'f') {
            //     this.cmd_F_handler(ev);
            // }

            if (ev.which === 13) {
                this.enter_click(ev);
            } else if (ev.which === 38) {
                this.arrow_up_click(ev);
            } else if (ev.which === 40) {
                this.arrow_down_click(ev);
            }
        });
    },

    enter_click: function (ev) {
        const search_results_modal = $('#search_results_modal');
        if (!search_results_modal.is(":visible")) {
            return;
        }

        const current_selected = $(".search_result_section_item.search_result_section_item_current", search_results_modal);
        if (current_selected.is(":visible")) {
            search_results_modal.hide();
            current_selected.click();
        }

        last_stop(ev);
    },

    arrow_up_click: function (ev) {
        const search_results_modal = $('#search_results_modal');
        if (!search_results_modal.is(":visible")) {
            return;
        }

        const current_selected = $(".search_result_section_item.search_result_section_item_current", search_results_modal);
        if (current_selected.is(":visible")) {
            const next_el = get_next_el(".search_result_section_item", "search_result_section_item_current", -1);
            next_el.addClass("search_result_section_item_current");
            current_selected.removeClass("search_result_section_item_current")
        } else {
            $(".search_result_section_item", search_results_modal).last().addClass("search_result_section_item_current");
        }

        return last_stop(ev);
    },

    arrow_down_click: function (ev) {
        const search_results_modal = $('#search_results_modal');
        if (!search_results_modal.is(":visible")) {
            return;
        }

        const current_selected = $(".search_result_section_item.search_result_section_item_current", search_results_modal);
        if (current_selected.is(":visible")) {
            const next_el = get_next_el(".search_result_section_item", "search_result_section_item_current");
            next_el.addClass("search_result_section_item_current");
            current_selected.removeClass("search_result_section_item_current");
        } else {
            $(".search_result_section_item", search_results_modal).first().addClass("search_result_section_item_current");
        }

        return last_stop(ev);
    },

    cmd_F_handler: function (ev) {
        last_stop(ev);

        if ($('#search_results_modal').is(":visible")) {
            $('#search_results_modal').hide();
        } else {
            const input = $('#txt_global_search')[0];
            input.focus();
            input.select();
            return $("#txt_global_search").data("func_search")();
        }
    },

    nav_search_blur: function (ev) {
        clearTimeout(window.nav_search_blur_timeout);
        window.nav_search_blur_timeout = setTimeout(() => {
            $('#search_results_modal').hide();
            $("#top_nav .nav_search").show();
            $("#top_nav .nav_search_input").hide();
        }, 300);
    },

    nav_search_click: function (ev) {
        $("#top_nav .nav_search").hide();
        $("#top_nav .nav_search_input").show();
        $("#top_nav .nav_search_input input").show().focus();
    },

    global_search_focus: function (ev) {
        $("#txt_global_search").data("func_search")();
        return last_stop(ev);
    },


    top_feedback_click: function (ev) {
        const btn = $(ev.target).closest("#top-feedback");

        close_all_modals();

        const modal_top_feedback = $('#modal-top-feedback');

        $(".modal-body .btn-primary", modal_top_feedback).on('click', () => {
            erie_server().exec_server_post(btn, {
                'content': $("#txt-content", modal_top_feedback).val(),
                'allow_contact': $("#chk-allow-contact", modal_top_feedback).is(":checked")
            });

            $(".modal-body .btn-primary", modal_top_feedback).off('click');
            modal_top_feedback.hide();

            $("#modal-interaction-feedback-thankyou").show();
            $("#txt-content", modal_top_feedback).val("")

        });

        modal_top_feedback.show();

        return last_stop(ev);
    },


    body_onclick: function (ev) {
        $('#search_results_modal').hide();
        $(".tool-modal").hide();
    },

    global_search_keypress: function (ev) {
        if (ev && ev.which === 27) {
            $(ev.target).val("");
            $('#search_results_modal').hide();
        }
    },

    btn_accept_cookie_consent_click: function (ev) {
        erie_server().exec_server_post(
            $("#modal_cookie_consent form").attr("action"),
            {
                "accept_essential": $("#modal_cookie_consent input[name=accept_essential]").is(":checked"),
                "accept_analytics": $("#modal_cookie_consent input[name=accept_analytics]").is(":checked")
            },
            () => {
                window.location.reload();
            }
        );

        return last_stop(ev);
    },

    viewmode_onclick: function (ev) {
        const btn = $(ev.target).closest(".btn-viewmode");
        const btn_container = btn.closest(".btn-viewmode-container");
        $(".btn-viewmode.selected", btn_container).removeClass("selected");
        btn.addClass('selected');
    },

    copy_to_clipboard_click: function (ev) {
        const btn = $(ev.target);
        this.hide_all_tooltips();

        const targetSelector = btn.data('target');
        const content = $(targetSelector).text();

        navigator.clipboard.writeText(content).then(function () {
            if (btn.hasClass("bi")) {
                btn.removeClass('bi-copy').addClass('bi-check');
                setTimeout(function () {
                    btn.removeClass('bi-check').addClass('bi-copy');
                }, 2000);
            } else {
                const originalIcon = btn.find('i');
                originalIcon.removeClass('bi-copy').addClass('bi-check');
                setTimeout(function () {
                    originalIcon.removeClass('bi-check').addClass('bi-copy');
                }, 2000);
            }
        }.bind(this)).catch(function (err) {
            console.error('Failed to copy text: ', err);
            alert('Failed to copy to clipboard');
        });


        return last_stop(ev);
    },

    show_renameproject_modal(action_button, current_project_name, callback_onrename) {
        const modal_rename_project = $('#modal-rename-project');
        $("#txt-new_project_name", modal_rename_project)
            .val(current_project_name)
            .attr("placeholder", current_project_name);

        modal_rename_project.show();
        $("#btn-do-the-rename", modal_rename_project).off('click').on("click", () => {
            modal_rename_project.hide();
            erie_server().exec_server_post(
                action_button,
                {
                    "prop_name": "name",
                    "value": $("#txt-new_project_name", modal_rename_project).val()
                },
                (d) => {
                    callback_onrename();
                }
            );
        });

        $(".btn-cancel", modal_rename_project).off('click').click(() => {
            modal_rename_project.hide();
        });
    },

    show_createproject_modal(action_button, action_name, source_item_name, suggested_project_name_suffix) {
        const suggested_project_name = `${source_item_name} ${suggested_project_name_suffix}`

        const modal_create_project = $('#modal-create-project');
        $("#create_project_title", modal_create_project).text(`${action_name} Project`)
        $("#create_project_subtitle", modal_create_project).text(`${action_name} Project from "${source_item_name}"`)
        $("#txt-new_project_name", modal_create_project).val(suggested_project_name).attr("placeholder", suggested_project_name);
        modal_create_project.show();

        $("#btn-do-the-copy", modal_create_project).off('click').click(() => {
            modal_create_project.hide();
            erie_server().exec_server_post(
                action_button,
                {"new_project_name": $("#txt-new_project_name", modal_create_project).val()},
                (d) => {
                    erie_server().exec_navigation(d['redirect_url'], {
                        "trigger_el": action_button
                    });
                }
            );
        });

        $(".btn-cancel", modal_create_project).off('click').click(() => {
            modal_create_project.hide();
        });
    },

    horiz_resizer_mousedown: function (ev) {
        const resizer_bar = $(ev.target).closest(".resizer_bar-horiz");
        const resize_container = $(ev.target).closest(".resize_container");
        const left_col = $(resizer_bar.data("left_col"));
        const right_col = $(resizer_bar.data("right_col"));
        const cookie_name = get_resizer_bar_cookie_name(resizer_bar);

        const temp_mousemove = (e) => {
            const container_offset = resize_container.offset().left;
            const new_width = e.pageX - container_offset;

            set_cookie(cookie_name, new_width);

            left_col.css("width", new_width + "px");
            right_col.css("width", `calc(100% - ${new_width + 5}px)`);

            resizer_bar.trigger("on_resizing", [new_width]);
        }

        const temp_mouseup = () => {
            $(document).off("mousemove", temp_mousemove);
            $(document).off("mouseup", temp_mouseup);
            resizer_bar.trigger("on_resized", []);
        }

        $(document).on("mousemove", temp_mousemove);
        $(document).on("mouseup", temp_mouseup);
    },

    vert_resizer_mousedown: function (ev) {
        const resizer_bar = $(ev.target).closest(".resizer_bar-vert");
        const resize_container = $(ev.target).closest(".resize_container");
        const top_col = $(resizer_bar.data("top_col"));
        const bottom_col = $(resizer_bar.data("bottom_col"));
        const cookie_name = get_resizer_bar_cookie_name(resizer_bar);

        const temp_mousemove = (e) => {
            const container_offset = resize_container.offset().top;
            const new_height = 12 + e.pageY - container_offset;

            set_cookie(cookie_name, new_height, {expires: 10000, path: '/'});

            top_col.css("height", new_height + "px");
            bottom_col.css("height", `calc(100% - ${new_height + 5}px)`);
            resizer_bar.trigger("on_resizing", [new_height]);
        }

        const temp_mouseup = () => {
            $(document).off("mousemove", temp_mousemove);
            $(document).off("mouseup", temp_mouseup);
            resizer_bar.trigger("on_resized", []);
        }

        $(document).on("mousemove", temp_mousemove);
        $(document).on("mouseup", temp_mouseup);
    },

    init_infinite_scroll: function (el) {
        const PAGE_SIZE = 20
        if (!el.length) {
            console.error(el);
            throw "el doesn't exist"
        }
        if (el.hasClass("initialized")) {
            return;
        }
        el.addClass("initialized");

        const sort_by = el.data("sort_by");
        const fetch_url = el.data("fetch_url");

        let loading = false;
        let has_next = parse_bool(el.data("has_more"));

        const f_load_more = () => {
            if (!has_next || loading) return;
            loading = true;

            const next_page = el.data("page_number") + 1;

            erie_server().exec_server_post(
                fetch_url,
                {
                    "page_size": PAGE_SIZE,
                    "page_number": next_page,
                    "sort_by": sort_by
                },
                (resp) => {
                    const page_content = $(resp).filter((idx, e) => e.innerHTML);
                    const count_returned = page_content.length;

                    el.data("page_number", next_page);
                    el.append(page_content);
                    has_next = count_returned >= PAGE_SIZE;
                    loading = false;

                    el.data("scroll_observer").disconnect();
                    if (has_next && el.children().length) {
                        el.data("scroll_observer").observe(el.children().last()[0]);
                    }
                });
        }

        el.data("scroll_observer", new IntersectionObserver(entries => {
            entries.forEach(entry => {
                if (entry.isIntersecting) {
                    f_load_more()
                }
            });
        }));

        if (el.children().length) {
            el.data("scroll_observer").observe(el.children().last()[0]);
        }
    }
});