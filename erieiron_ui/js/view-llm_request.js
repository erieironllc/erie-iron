LlmRequestView = ErieView.extend({
    el: 'body',

    events: {
        'click #btn_optimize': 'btn_optimize_click',
        'click #btn_debug': 'btn_debug_click',
        'click #btn_ask': 'btn_ask_click'
    },

    init_view: function (options) {
        // $("#modal_llmrequest_debug").show();


    },

    btn_ask_click: function (ev, optimize) {
        $("#txt_question_response").text("thinking...  this might take a bit ");

        const t = setInterval(() => {
            $("#txt_question_response").text($("#txt_question_response").text() + ".");
        }, 1000);

        erie_server().exec_server_post(
            $("#frm_llm_question"),
            {
                "optimize": optimize
            },
            (resp) => {
                clearInterval(t);
                $("#txt_question_response").empty().append(resp);
            })
        return last_stop(ev)
    },

    btn_optimize_click: function (ev) {
        $("#modal_llmrequest_debug").show();
        $("#txt_debug_prompt").text("Please review context and response and look to optimize this interaction");
        this.btn_ask_click(ev, true);

        return last_stop(ev)
    },

    btn_debug_click: function (ev) {
        $("#modal_llmrequest_debug").show();

        return last_stop(ev)
    },
});