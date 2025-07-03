IterationView = ErieView.extend({
    el: 'body',

    events: {
        'click .code_type_toggle': 'code_type_toggle_click'
    },

    init_view: function (options) {

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