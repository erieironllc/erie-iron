// this is special dropdown handling for when a dropdown is in a modal or in a scrollable div
function init_dropdowns(container_el) {
    let visible_dropdown = null;

    function hide_dropdown() {
        if (!visible_dropdown) {
            return;
        }

        const dropdown_toggle = visible_dropdown['dropdown_toggle'];
        const dropdown_menu = visible_dropdown['dropdown_menu'];
        visible_dropdown = null;

        dropdown_menu.removeAttr('style').hide();
        dropdown_toggle
            .after(dropdown_menu.detach())
            .attr('aria-expanded', 'false');
    }

    let scroll_timeout = null;
    find_scrollable_parent($(".dropdown-item", container_el)).off("scroll").on("scroll", (event) => {
        clearTimeout(scroll_timeout);
        scroll_timeout = setTimeout(hide_dropdown, 500); // not sure why this is necessary, but without it dropdowns that you need to scroll to don't work
    });


    $(document).off('click.dropdown-hide').on('click.dropdown-hide', (e) => {
        if (!visible_dropdown) {
            return;
        }

        const dropdown_toggle = visible_dropdown['dropdown_toggle'];
        const dropdown_menu = visible_dropdown['dropdown_menu'];

        if (e.target.id === "delegate_events_tester") {
            return;
        }

        if (dropdown_toggle.is(e.target)) {
            // this was fired from clicking on the toggle.  hide handled elsewhere
            return;
        }

        if (dropdown_menu.is(e.target)) {
            // this was fired from clicking on the dropdown menu.  hide handled elsewhere
            return;
        }

        if (dropdown_toggle.has(e.target).length > 0) {
            // we've already moved the menu back by the toggle
            return;
        }

        hide_dropdown();
    });

    const init_dropdown = (el) => {
        const dropdown_toggle = $(el);
        const dropdown_menu = dropdown_toggle.next('.dropdown-menu');
        const dropdown = new bootstrap.Dropdown(dropdown_toggle[0], {
            popperConfig: function (default_popper_config) {
                default_popper_config.modifiers = [{
                    name: 'applyStyles',
                    enabled: false
                }];
                return default_popper_config;
            }
        });

        dropdown_toggle.off('click').on('click', () => {
            hide_dropdown();
            visible_dropdown = {
                "dropdown_toggle": dropdown_toggle,
                "dropdown_menu": dropdown_menu
            }

            const offset = dropdown_toggle.offset();
            const dropdown_height = dropdown_menu.outerHeight();
            const viewport_height = $(window).height();
            const scroll_top = $(window).scrollTop();

            // Calculate the space available below and above the toggle
            const space_below = viewport_height - (offset.top - scroll_top + dropdown_toggle.outerHeight());
            const space_above = offset.top - scroll_top;

            // If there is more space below or equal space, place the dropdown below the toggle
            // Otherwise, place it above the toggle
            let dropdown_top_position;
            if (space_below >= dropdown_height || space_below >= space_above) {
                dropdown_top_position = offset.top + dropdown_toggle.outerHeight();
            } else {
                dropdown_top_position = offset.top - dropdown_height;
            }

            $('body').append(dropdown_menu.detach());
            dropdown_toggle.focus();
            dropdown_menu
                .show()
                .css({
                    'display': 'block',
                    'z-index': 10000,
                    'position': 'absolute',
                    'top': dropdown_top_position,
                    'left': offset.left
                });
        });

        $(".dropdown-item", dropdown_menu).off("mouseover").on("mouseover", (ev) => {
            const f_stop_prop = event => {
                event.stopImmediatePropagation();
            }
            document.removeEventListener('focusin', f_stop_prop, true);
            document.addEventListener('focusin', f_stop_prop, true);
            setTimeout(() => {
                document.removeEventListener('focusin', f_stop_prop, true);
            }, 1000);

        });

        $(".dropdown-item", dropdown_menu).off("click").on("click", (ev) => {
            ev.stopImmediatePropagation();

            dropdown_toggle.focus();

            const dropdown_item = $(ev.target).closest(".dropdown-item");
            let val = dropdown_item.data("value");
            if (!val) {
                dropdown_item.text().trim();
            }
            dropdown_toggle.data("value", val);

            const clicked_text = dropdown_item.text();
            dropdown_toggle.text(clicked_text);
            hide_dropdown();
            dropdown_item.trigger("selected");
            dropdown_toggle.trigger("change", dropdown_item);

            return last_stop(ev);
        });
    }

    $('.erie-dropdown-toggle', container_el).each((idx, el) => {
        init_dropdown(el);
    });
}
