const None = null;
const True = true;
const False = false;

class Mutex {
    constructor() {
        this.queue = Promise.resolve();
    }

    synchronized(fn) {
        this.queue = this.queue.then(() => fn()).catch(console.error);
        return this.queue;
    }
}

// value must correspond to UUID_NULL_OBJECT value on
const UUID_NULL_OBJECT = '11111111-1111-1111-1111-111111111111'

function print() {
    console.warn("ERROR: calling print() instead of console.log()", arguments);
}

(function ($) {
    $.fn.rect = function () {
        if (!this.length) return null;
        const rect = this[0].getBoundingClientRect();
        return {
            width: rect.width,
            height: rect.height,
            top: rect.top,
            left: rect.left,
            right: rect.right,
            bottom: rect.bottom
        };
    };

    $.fn.scrollIntoView = function () {
        if (!this.length) return null;
        scroll_into_view(this);
        return this;
    };

    $.fn.on_form_change = function (callback) {
        if (!this.length || typeof callback !== 'function') return;

        return this
            .off("input change", "input, select, textarea")
            .on("input change", "input, select, textarea", callback)
    };

    $.fn.on_hidden = function (callback) {
        if (!this.length || typeof callback !== 'function') return;

        const observer = new MutationObserver((mutations) => {
            mutations.forEach(mutation => {
                const $target = $(mutation.target);
                if (!$target.is_visible()) {
                    callback(mutation.target);
                }
            });
        });

        this.each(function () {
            observer.observe(this, {attributes: true, attributeFilter: ['class', 'style']});
        });

        return this;
    };

    $.fn.is_visible = function () {
        if (!this.length) return false;
        return this.toArray().every(el => {
            const style = window.getComputedStyle(el);
            if (!el.offsetParent || style.display === "none" || style.visibility === "hidden" || style.opacity === "0") {
                return false;
            }

            const rect = el.getBoundingClientRect();
            return (
                rect.top >= 0 &&
                rect.left >= 0 &&
                rect.bottom <= (window.innerHeight || document.documentElement.clientHeight) &&
                rect.right <= (window.innerWidth || document.documentElement.clientWidth)
            );
        });
    };

    $.fn.class_if = function (class_name, bool_val) {
        if (!this.length) return null;

        if (bool_val) {
            this.addClass(class_name);
        } else {
            this.removeClass(class_name);
        }
    };

    $.fn.boolData = function (data_attr_name) {
        if (Array.isArray(data_attr_name)) {
            return data_attr_name.map(v => this.boolData(v));
        } else {
            return parse_bool(
                this.data(data_attr_name)
            );
        }
    };

    $.fn.show = function (show_it = true, on_hide = null) {
        if (!show_it) {
            this.hide();
            if (on_hide) {
                on_hide();
            }
        } else {
            if (this.hasClass("modal")) {
                this.modal("show");
            } else {
                this.removeClass("d-none");
                this.removeClass("visually-hidden");
                this.removeClass("invisible");
            }

            if (on_hide) {
                setTimeout(() => {
                    $(this).on_hidden(on_hide);
                }, 500);
            }
        }

        return $(this);
    };

    $.fn.hide = function (hide_it = true) {
        if (!this.length) return null;

        if (!hide_it) {
            return this.show();
        } else {
            if (this.hasClass("modal")) {
                return this.modal("hide");
            } else {
                return this.addClass("d-none");
            }
        }
    };

    $.fn.toggle = function () {
        if (!this.length) return null;

        if (this.hasClass("d-none")) {
            return this.show();
        } else {
            return this.hide();
        }
    };

    $.fn.scroll_top = function () {
        this.each(el => {
            el.scrollTop = 0
        });
        return $(this);
    };

    $.fn.scroll_to_end = function () {
        if (!this.length) return false;
        if (!$(this).children().length) return false;

        $(this).children().last()[0].scrollIntoView({behavior: "smooth"});
    };

    $.fn.mouse_is_over = function (event) {
        if (!this.length) return false;

        const elements_under_cursor = document.elementsFromPoint(event.clientX, event.clientY);

        return this.toArray().some(el => elements_under_cursor.includes(el));
    };

    $.fn.getScrollableParent = function () {
        let element = this;
        while (element.length) {
            // Check if the current element is scrollable
            const overflowY = element.css("overflow-y");
            const overflowX = element.css("overflow-x");
            if ((overflowY === "auto" || overflowY === "scroll" || overflowX === "auto" || overflowX === "scroll") &&
                (element[0].scrollHeight > element[0].clientHeight || element[0].scrollWidth > element[0].clientWidth)) {
                return element;
            }
            // Move to the parent element
            element = element.parent();
        }
        // Return the document element if no scrollable parent is found
        return $(document);
    };
})(jQuery);

function erie_common_init() {
    $('.dropdown-toggle').dropdown();
    getServerCommsManager();
    getBaseView();
}

function getBaseView() {
    if (!window.baseView) {
        window.baseView = 1;
        window.baseView = new BaseContainerView();
    }
    return window.baseView;
}

function getChatManager() {
    if (!window.chatManager) {
        window.chatManager = new ChatManager();
    }
    return window.chatManager;
}

function close_all_dropdowns() {
    try {
        document.querySelectorAll('.dropdown-toggle').forEach((toggle) => {
            (new bootstrap.Dropdown(toggle)).hide();
        });
    } catch (e) {
        console.warn(e);
    }
}

function close_all_modals() {
    $("#txt_global_search").hide();
    $(".tool-modal:not([data-bs-keyboard='false'])").hide();
    $(".modal:not([data-bs-keyboard='false'])").hide();
}


/**
 * @returns {ServerCommsManager}
 */
function getServerCommsManager() {
    if (!window.serverCommsManager) {
        window.serverCommsManager = new ServerCommsManager();
    }
    return window.serverCommsManager;
}

/**
 * @returns {ServerCommsManager}
 */
function erie_server() {
    return getServerCommsManager();
}


function pause_nav_clicks() {
    window.disable_nav_clicks = true;
    setTimeout(() => {
        window.disable_nav_clicks = false;
    }, 300);
}

function nav_clicks_paused() {
    return window.disable_nav_clicks === true;
}

function debounce(func, wait) {
    let timeout;
    return function (...args) {
        clearTimeout(timeout);
        timeout = setTimeout(() => {
            func.apply(this, args)
        }, wait);
    };
}

function isFalse(value) {
    return !isTrue(value);
}

function isTrue(value) {
    return ['true', '1', 'yes', 'on'].includes(
        String(value).trim().toLowerCase()
    );
}

// does a POST to the server in ajax fashion.  main util funtion for client server comms
function is_form(el) {
    try {
        return $(el).is("form")
    } catch (e) {
        return false;
    }
}


function form_data_to_dict(form_data) {
    const d = {}
    if (form_data) {
        for (const [key, value] of form_data.entries()) {
            d[key] = value;
        }
    }
    return d;
}


function get_unique_selector(element) {
    if (!(element instanceof Element)) return;

    const interaction_name = $(element).data("interaction_name");
    if (is_not_empty(interaction_name)) {
        return interaction_name;
    }

    if (element.id) {
        return `#${CSS.escape(element.id)}`;
    }

    const selectors = [];
    let depth = 0;
    const maxDepth = 5; // Set a maximum depth to prevent performance issues

    while (element && element.nodeType === Node.ELEMENT_NODE && depth < maxDepth) {
        let selector = element.nodeName.toLowerCase();

        if (element.className) {
            const uniqueClasses = Array.from(element.classList).filter(className => {
                return document.getElementsByClassName(className).length === 1;
            });

            if (uniqueClasses.length > 0) {
                selector += '.' + CSS.escape(uniqueClasses[0]);
                selectors.unshift(selector);
                break;
            } else {
                selector += '.' + CSS.escape(element.classList[0]);
            }
        } else if (element.hasAttribute('name')) {
            selector += `[name="${CSS.escape(element.getAttribute('name'))}"]`;
        }

        const siblings = element.parentNode ? element.parentNode.children : [];
        if (siblings.length > 1) {
            const index = Array.prototype.indexOf.call(siblings, element) + 1;
            selector += `:nth-child(${index})`;
        }

        selectors.unshift(selector);
        element = element.parentElement;
        depth++;
    }

    return selectors.join(' > ');
}


function calculate_trend_line(data_array) {
    const N = data_array.length;
    const X = Array.from({length: N}, (_, idx) => idx + 1); // create an array with sequential vals [1, 2, ..., N]
    const Y = data_array;

    const sumX = X.reduce((sum, val) => sum + val, 0);
    const sumY = Y.reduce((sum, val) => sum + val, 0);

    const sumXY = X.reduce(
        (sum, val, idx) => {
            return sum + (val * Y[idx]);
        }, 0);

    const sumX2 = X.reduce(
        (sum, val) => {
            return sum + (val * val);
        }, 0);

    const slope = (N * sumXY - sumX * sumY) /
        (N * sumX2 - sumX * sumX);

    const intercept = (sumY - slope * sumX) /
        N;

    return X.map((val) => {
        return slope * val + intercept;
    });
}

function strip_non_numeric(str) {
    str = default_string(str);
    return str.replace(/\D/g, '');
}

function default_float(s, default_val = 0.0) {
    if (typeof (s) === "number") {
        return parseFloat(s);
    }

    if (typeof (default_val) !== "number") {
        default_val = default_float(default_val);
    }

    if (is_empty(s)) {
        return default_val;
    }

    const v = parseFloat(s);
    if (isNaN(v)) {
        return default_val;
    } else {
        return v;
    }
}

function int(s, default_val = 0) {
    return default_int(s, default_val);
}

function default_int(s, default_val = 0) {
    if (typeof (s) === "number") {
        return parseInt(s);
    }

    if (typeof (default_val) !== "number") {
        default_val = default_int(default_val);
    }

    s = strip_non_numeric(s)
    if (is_empty(s)) {
        return default_val;
    }
    try {
        const v = parseInt(s);
        if (isNaN(v)) {
            return default_val;
        } else {
            return v;
        }
    } catch (e) {
        return default_val;
    }

}

function default_string(s, default_val = "") {
    if (is_empty(s)) {
        return default_val;
    }
    return s
}

function is_empty(s) {
    return s === "" || s === undefined || s === null;
}

function is_not_empty(s) {
    return !is_empty(s);
}

function capitalizeWords(str) {
    return str.toLowerCase().replace(/\b\w/g, c => c.toUpperCase());
}

function secondsToMinutesSeconds(seconds) {
    const mins = Math.floor(seconds / 60);
    const secs = seconds % 60;
    return `${mins}:${secs < 10 ? '0' + secs : secs}`;
}


function getCSRFToken() {
    if (document.cookie && document.cookie !== '') {
        let cookies = document.cookie.split(';');
        for (let i = 0; i < cookies.length; i++) {
            let cookie = jQuery.trim(cookies[i]);
            // Check if the cookie name starts with "csrftoken"
            if (cookie.substring(0, 10) === 'csrftoken=') {
                return decodeURIComponent(cookie.substring(10));
            }
        }
    }

    return null;
}


function testUrl(url) {
    return fetch(url)
        .then(response => {
            if (!response.ok) {
                throw new Error('Network response was not ok');
            }
            return response;
        })
        .then(response => {
            return response.status !== 404;

        })
        .catch(error => {
            return false;
        });
}

function set_nav_highlight(cls_name) {
    $("#top_nav .selected").removeClass("selected");
    if (cls_name) {
        $(`#top_nav #${cls_name}`).addClass("selected");
    }
}

/**
 * Sanitizes a string to make it safe for use as a filename.
 */
function sanitize_filename(filename, options = {}) {
    const {
        maxLength = 255,
        replaceSpacesWithUnderscore = true
    } = options;

    if (typeof filename !== 'string') {
        throw new TypeError('Filename must be a string');
    }

    const invalidChars = /[\/\\?%*:|"<>]/g;
    let sanitized = filename.replace(invalidChars, '');

    if (replaceSpacesWithUnderscore) {
        sanitized = sanitized.replace(/\s+/g, '_');
    } else {
        sanitized = sanitized.trim();
    }

    sanitized = sanitized.replace(/^[.\s]+|[.\s]+$/g, '');

    // Handle reserved Windows filenames (case-insensitive)
    const reservedNames = [
        'CON', 'PRN', 'AUX', 'NUL',
        'COM1', 'COM2', 'COM3', 'COM4', 'COM5', 'COM6', 'COM7', 'COM8', 'COM9',
        'LPT1', 'LPT2', 'LPT3', 'LPT4', 'LPT5', 'LPT6', 'LPT7', 'LPT8', 'LPT9'
    ];

    const nameWithoutExtension = sanitized.split('.').slice(0, -1).join('.') || sanitized;
    const extension = sanitized.includes('.') ? '.' + sanitized.split('.').pop() : '';

    if (reservedNames.includes(nameWithoutExtension.toUpperCase())) {
        sanitized = '_' + sanitized;
    }

    // Truncate to the maximum length
    if (sanitized.length > maxLength) {
        // Ensure the extension is preserved
        if (extension.length > 0) {
            const truncatedName = sanitized.slice(0, maxLength - extension.length);
            sanitized = truncatedName + extension;
        } else {
            sanitized = sanitized.slice(0, maxLength);
        }
    }

    if (sanitized.length === 0) {
        sanitized = 'unnamed';
    }

    return sanitized;
}

function is_dict(variable) {
    if (variable === null) {
        return false
    }

    if (is_jquery_object(variable)) {
        return false;
    }

    if (variable instanceof Date) {
        return false;
    }

    if (Array.isArray(variable)) {
        return false;
    }

    return typeof variable === 'object'
}

function escapeRegExp(string) {
    return string.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'); // $& means the whole matched string
}

function escapeHtml(unsafe) {
    return unsafe
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");
}

function highlight_keyword(text, keyword) {
    text = String(text)
    keyword = String(keyword)

    if (!keyword) return text;

    text = escapeHtml(text)

    return text.replace(
        new RegExp(`(${escapeRegExp(keyword)})`, 'gi'),
        (match) => `<b>${match}</b>`
    );
}


function get_next_el(group_selector, selected_class, increment) {
    if (!increment) {
        increment = 1;
    }

    const all_elements = $(group_selector);
    let selected_index = -1;
    all_elements.each((idx, el) => {
        if ($(el).hasClass(selected_class)) {
            selected_index = idx;
            return false;
        }
    });

    selected_index = selected_index + increment;

    if (selected_index < 0) {
        selected_index = all_elements.length - 1;
    } else if (selected_index >= all_elements.length) {
        selected_index = 0
    }

    return $(all_elements[selected_index]);
}

function backbutton_allowed(url) {
    // error_tokens is intentionally misnamed
    let allowed = false;

    error_tokens.some((allowed_back_url_prefix) => {
        if (url.startsWith("/" + allowed_back_url_prefix)) {
            allowed = true;
            return true;
        }
    });
    return allowed;
}

function isElementInViewport(el) {
    el = $(el)[0];
    const rect = el.getBoundingClientRect();
    return (
        rect.top >= 0 &&
        rect.left >= 0 &&
        rect.bottom <= (window.innerHeight || document.documentElement.clientHeight) &&
        rect.right <= (window.innerWidth || document.documentElement.clientWidth)
    );
}

function sleep(ms) {
    return new Promise(resolve => setTimeout(resolve, ms));
}


function on_invisible(el, callback) {
    const observer = new IntersectionObserver(
        (entries, observer) => {
            $.each(entries, (index, entry) => {
                if (!entry.isIntersecting) {
                    callback(el);
                }
            });
        },
        {
            threshold: 0  // Trigger when even 1px is not visible
        }
    );

    $(el).each((idx, el2) => {
        observer.observe(el2);
    })
}


function listen_for_move(element, callback) {
    element = $(element)[0];
    let last_position = element.getBoundingClientRect();

    function checkPosition() {
        const current_position = element.getBoundingClientRect();

        // Check if the element's position has changed
        if (current_position.top !== last_position.top || current_position.left !== last_position.left) {
            callback(current_position, last_position);
            last_position = current_position; // Update the last known position
        }
    }

    const scrolling_element = find_scrollable_parent(element);
    if (scrolling_element.length > 0) {
        scrolling_element[0].addEventListener('scroll', checkPosition);
    }
    window.addEventListener('scroll', checkPosition);
    window.addEventListener('resize', checkPosition);
}

function find_scrollable_parent(element) {
    if ($(element).length === 0) {
        return $(window);
    }

    element = $(element)[0];
    let parent = element.parentElement;

    while (parent) {
        const overflowY = window.getComputedStyle(parent).overflowY;
        const overflowX = window.getComputedStyle(parent).overflowX;
        const isScrollableY = (overflowY === 'auto' || overflowY === 'scroll') && parent.scrollHeight > parent.clientHeight;
        const isScrollableX = (overflowX === 'auto' || overflowX === 'scroll') && parent.scrollWidth > parent.clientWidth;

        if (isScrollableY || isScrollableX) {
            return $(parent);
        }

        parent = parent.parentElement;
    }

    return $(window);
}

function remove_blank_lines(text) {
    return text
        .split('\n')                     // Split text into lines
        .filter(line => line.trim() !== '') // Remove blank lines
        .join('\n');                     // Join lines back into a string
}


function show_generic_modal(title, body_msg) {
    $("#modal_common_error_message .modal-title").empty().append(title);
    $("#modal_common_error_message .modal-body").empty().append(body_msg);
    $("#modal_common_error_message").show();
}

function make_cookie_name_safe(name) {
    return encodeURIComponent(name).replace(/%/g, '');
}


function get_resizer_bar_cookie_name(resizer_el) {
    const resizer_bar = $(resizer_el);
    return make_cookie_name_safe(resizer_bar.attr("id"));
}

function delete_cookie(name) {
    $.cookie(name, '', {
        expires: -1,
        path: '/'
    });
}

function set_cookie(name, value) {
    $.cookie(name, value, {
        expires: 10000,
        path: '/'
    });
}

function get_cookie(name, default_value) {
    return $.cookie(name) || default_value;
}

function ensure_list(the_list) {
    if (!the_list) {
        return [];
    }

    if (!Array.isArray(the_list)) {
        return [the_list];
    }

    return the_list;
}

function first(the_list) {
    the_list = ensure_list(the_list);
    if (the_list.length === 0) {
        return null;
    } else {
        return the_list[0]
    }
}

function get_scrollable_ancestor(element) {
    if (!element) return null;

    const overflow_regex = /(auto|scroll|overlay)/;

    let parent = element.parentElement;

    while (parent) {
        const style = window.getComputedStyle(parent);
        const overflowY = style.overflowY;

        if (overflow_regex.test(overflowY)) {
            return parent;
        }

        parent = parent.parentElement;
    }

    return document.scrollingElement;
}

function is_child_visible_within_parent(parent, child) {
    child = $(child);
    parent = $(parent);


    const parent_top = parent.offset().top;
    const parent_bottom = parent_top + parent.outerHeight();

    const child_top = child.offset().top;
    const child_bottom = child_top + child.outerHeight();

    return child_bottom > parent_top && child_top < parent_bottom;
}

function scroll_into_view(element) {
    if (element === null || $(element).length === 0) {
        return;
    }
    element = $(element)[0];

    const scrollable_ancestor = get_scrollable_ancestor(element);
    if (scrollable_ancestor && is_child_visible_within_parent(scrollable_ancestor, element)) {
        // it's already visible
        return;
    }

    element.scrollIntoView({
        behavior: 'instant'
    });

}

function ensure_visible(el, func_is_visible, func_make_visible) {
    el = $(el);
    el.show();

    if (el.length === 0) {
        return;
    }

    if (el.data("parent_el")) {
        func_is_visible = func_is_visible || ((parent_el) => {
            parent_el.hasClass("d-none")
        });

        func_make_visible = func_make_visible || ((parent_el) => {
            parent_el.show()
        });

        const elements_to_make_visible = []

        let parent_el = $(el.data("parent_el"));
        while (parent_el.length > 0) {
            if (!func_is_visible(parent_el)) {
                elements_to_make_visible.push(parent_el);
            }
            parent_el = $(parent_el.data("parent_el"));
        }
        elements_to_make_visible.reverse();
        elements_to_make_visible.forEach(el => {
            func_make_visible(el);
        });
    }

    scroll_into_view(el);
}


/**
 * Replaces the last N elements of arr_1 with elements from arr_2.
 *
 * @param {Array} arr_1 - The original array to be modified.
 * @param {Array} arr_2 - The array whose elements will replace the last elements of arr_1.
 * @returns {void} The function modifies arr_1 in place.
 */
function replace_last_elements(arr_1, arr_2) {
    arr_1 = ensure_list(arr_1);
    arr_2 = ensure_list(arr_2);

    if (arr_2.length > arr_1.length) {
        throw new Error("The second array must be smaller than or equal to the first array.");
    }

    const start = arr_1.length - arr_2.length;
    arr_1.splice(start, arr_2.length, ...arr_2);
}


function get_target_el(trigger_el) {
    if (trigger_el) {
        if (trigger_el instanceof Event) {
            trigger_el = trigger_el.target;
        } else if (trigger_el instanceof jQuery.Event) {
            trigger_el = trigger_el.target;
        }

        return $(trigger_el);
    } else {
        return null;
    }
}

function parse_bool(v) {
    if (!v) {
        return false;
    }

    const s = String(v).toLowerCase();
    if (['true', '1', 't', 'y', 'yes'].includes(s)) {
        return true;
    } else if (['false', '0', 'f', 'n', 'no'].includes(s)) {
        return false;
    }

    throw new Error(`Cannot parse boolean value from: ${s}`);
}

function parse_date_Ymd_hms(dateString) {
    // from the python format Ymd h:m:s

    const [datePart, timePart] = dateString.split(' ');
    const year = datePart.slice(0, 4);
    const month = datePart.slice(4, 6) - 1;
    const day = datePart.slice(6, 8);

    const [hours, minutes, seconds] = timePart.split(':').map(Number);

    // Create and return a JavaScript Date object
    return new Date(year, month, day, hours, minutes, seconds);
}

function get_current_time_formated() {
    const now = new Date();
    const month = String(now.getMonth() + 1).padStart(2, '0');
    const day = String(now.getDate()).padStart(2, '0');
    const year = now.getFullYear();
    const hours = String(now.getHours()).padStart(2, '0');
    const minutes = String(now.getMinutes()).padStart(2, '0');

    return `${month}/${day}/${year} ${hours}:${minutes}`;
}

function millis_to_hh_mm_ss(millis) {
    let seconds = millis / 1000;
    let hours = Math.floor(seconds / 3600);
    let minutes = Math.floor((seconds % 3600) / 60);

    if (millis < 60 * 1000) {
        return `${parseFloat((millis / 1000).toFixed(2)).toFixed(1)}s`;
    }

    seconds = Math.floor(seconds) % 60;
    if (millis < 60 * 60 * 1000) {
        return `${minutes}:${seconds.toString().padStart(2, '0')}`;
    }

    return `${hours}:${minutes.toString().padStart(2, '0')}:${seconds.toString().padStart(2, '0')}`;
}

function millis_to_bars(duration_millis, bpm, beats_per_bar) {
    const millis_per_beat = 60000 / bpm;

    const total_beats = duration_millis / millis_per_beat;

    const bars = Math.floor(total_beats / beats_per_bar) + 1;
    const beats = Math.floor(total_beats % beats_per_bar) + 1;

    return `${bars}:${beats}`;
}

function is_valid_uuid(v) {
    if (!v) {
        return false;
    }

    return v !== UUID_NULL_OBJECT;
}

function is_invalid_uuid(v) {
    return !is_valid_uuid(v);
}

function reverse_map(input) {
    const reversedMap = {}
    for (const [key, value] of Object.entries(input)) {
        reversedMap[value] = key;
    }
    return reversedMap;
}

function is_jquery_object(obj) {
    return obj && typeof obj === "object" && !!obj.jquery;
}

function is_boolean(variable) {
    return typeof variable === 'boolean';
}

function str(v) {
    if (!v) {
        return "";
    } else if (is_boolean(v)) {
        return v ? "true" : "false";
    } else {
        return "" + v;
    }
}

function find_closest_number(list, target, default_val = 0) {
    if (!list || list.length === 0) return default_val;

    return list.reduce((closest, num) =>
        Math.abs(num - target) < Math.abs(closest - target) ? num : closest
    );
}

function copy_data_props(src_el, dest_el) {
    src_el = $(src_el);
    dest_el = $(dest_el);


    $.each(src_el.data(), (name, val) => {
        dest_el.data(name, val);
    });
}

function get_intersecting_elements_from_ev(ev, target_selector = "*") {
    const page_y = ev.pageY;
    const page_x = ev.pageX;

    const matched_elements = [];
    $(target_selector).each(function () {
        const $elem = $(this);
        const offset = $elem.offset();
        const width = $elem.outerWidth();
        const height = $elem.outerHeight();

        if (
            page_x >= offset.left &&
            page_x <= offset.left + width &&
            page_y >= offset.top &&
            page_y <= offset.top + height
        ) {
            matched_elements.push(this);
        }
    });

    return $(matched_elements);
}

function get_intersecting_elements(src_el, target_selector = "*") {
    src_el = $(src_el);

    const src_bounds = src_el.offset();
    src_bounds.right = src_bounds.left + src_el.outerWidth();
    src_bounds.bottom = src_bounds.top + src_el.outerHeight();

    const intersectingElements = [];
    $(target_selector).each((idx, el) => {
        if (el === src_el[0]) return;
        el = $(el);

        const el_bounds = el.offset();
        el_bounds.right = el_bounds.left + el.outerWidth();
        el_bounds.bottom = el_bounds.top + el.outerHeight();

        if (
            src_bounds.left < el_bounds.right &&
            src_bounds.right > el_bounds.left &&
            src_bounds.top < el_bounds.bottom &&
            src_bounds.bottom > el_bounds.top
        ) {
            intersectingElements.push(el[0]);
        }
    });

    return $(intersectingElements);
}

function join_with_and(items) {
    if (!items || items.length === 0) {
        return '';
    } else if (items.length === 1) {
        return items[0];
    } else if (items.length === 2) {
        return items.join(' and ');
    } else {
        return items.slice(0, -1).join(', ') + ' and ' + items[items.length - 1];
    }
}

function average(numbers) {
    if (numbers.length === 0) {
        return 0;
    }
    let total_sum = numbers.reduce((acc, num) => acc + num, 0);
    return total_sum / numbers.length;
}

function median(numbers) {

    if (numbers.length === 0) {
        return 0;
    }

    let sorted_numbers = numbers.slice().sort((a, b) => a - b);
    let mid_index = Math.floor(sorted_numbers.length / 2);

    if (sorted_numbers.length % 2 !== 0) {
        return sorted_numbers[mid_index];
    } else {
        return (sorted_numbers[mid_index - 1] + sorted_numbers[mid_index]) / 2;
    }
}


function get_position(event, offset_obj) {
    const x_offset = offset_obj ? offset_obj.offset().left : 0;
    const y_offset = offset_obj ? offset_obj.offset().top : 0;
    return {
        left: event.pageX - x_offset,
        top: event.pageY - y_offset,
        page_x: event.pageX,
        page_y: event.pageY,
        client_x: event.clientX,
        client_y: event.clientY,
        offset_x: event.offsetX,
        offset_y: event.offsetY
    };
}

function last_stop(ev) {
    if (ev) {
        ev.preventDefault();
        ev.stopPropagation();
    }
    return false;
}

function all_same() {
    return new Set(arguments).size === 1;
}

function get_transparent_img() {
    const img = new Image();
    img.src = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/wcAAwAB/edEPkYAAAAASUVORK5CYII="; // Transparent 1x1 pixel
    return img;
}

function is_numeric(value) {
    return !isNaN(parseFloat(value)) && isFinite(value);
}

function get_elements_under_mouse(event, class_name) {
    if (class_name) {
        return $(document.elementsFromPoint(event.clientX, event.clientY)
            .filter(el => el.classList.contains(class_name)));
    } else {
        return $(document.elementsFromPoint(event.clientX, event.clientY));
    }
}

function random_choice(array) {
    return array[Math.floor(Math.random() * array.length)];
}

function clear_form(frm, except = null) {
    let els = $(frm)
        .find('input, select, textarea')
        .not(':button, :submit, :reset, :hidden')
    ;

    if (except) {
        els = els.not(except)
    }

    els.val('')
        .prop('checked', false)
        .prop('selected', false);
}

function valid_cloudfront_url_or_null(url) {
    if (is_cloudfront_url_valid(url)) {
        return url;
    } else {
        return null;
    }
}

function is_cloudfront_url_valid(url) {
    if (!url) {
        return false;
    }

    const expires = new URL(url).searchParams.get('Expires')
    if (!expires) {
        return false;
    }

    return Math.floor(Date.now() / 1000) < parseInt(expires, 10)
}


function local_db_put(db_name, key, obj) {
    const request = indexedDB.open(db_name, 3);

    request.onupgradeneeded = (event) => {
        const db = event.target.result;
        if (!db.objectStoreNames.contains(key)) {
            db.createObjectStore(key, {keyPath: 'id'});
        }
    };

    request.onsuccess = (event) => {
        const db = event.target.result;
        const tx = db.transaction(key, 'readwrite');
        const store = tx.objectStore(key);
        Object.entries(obj).forEach(([id, srcs]) => {
            store.put({id, srcs});
        });
        tx.oncomplete = () => {
            db.close();
        };
    };

    request.onerror = (event) => {
        console.error('IndexedDB error storing waveform URLs:', event.target.error);
    };
}

async function local_db_get(db_name, key) {
    // open (and upgrade if needed) the database
    const db = await new Promise((resolve, reject) => {
        const request = indexedDB.open(db_name, 3);
        request.onupgradeneeded = event => {
            const db = event.target.result;
            if (!db.objectStoreNames.contains(key)) {
                db.createObjectStore(key, {keyPath: 'id'});
            }
        };
        request.onsuccess = event => resolve(event.target.result);
        request.onerror = event => reject(event.target.error);
    });

    try {
        // retrieve all entries from the object store
        const results = await new Promise((resolve, reject) => {
            const tx = db.transaction(key, 'readonly');
            const store = tx.objectStore(key);
            const getAllRequest = store.getAll();
            getAllRequest.onsuccess = () => resolve(getAllRequest.result);
            getAllRequest.onerror = event => reject(event.target.error);
        });

        const obj = {};
        results.forEach(({id, srcs}) => {
            obj[id] = srcs;
        });

        db.close();
        return obj;
    } catch (error) {
        db.close();
        throw error;
    }
}

function log(...data) {
    console.log(...data);
}

function get_querystring_params() {

    const urlParams = new URLSearchParams(window.location.search);
    return Object.fromEntries(urlParams);

}
