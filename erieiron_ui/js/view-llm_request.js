LlmRequestView = ErieView.extend({
    el: 'body',

    events: {
        'click #btn_ask': 'btn_ask_click',
        'click #btn_compare_response_diff': 'btn_compare_response_diff_click',
        'click #btn_compare': 'btn_compare_click',
        'click #btn_optimize': 'btn_optimize_click'
    },

    init_view: function (options) {

    },

    btn_optimize_click: function (ev) {
        $("#txt_optimize_response").text("thinking...  this might take a bit ");

        const t = setInterval(() => {
            $("#txt_optimize_response").text($("#txt_optimize_response").text() + ".");
        }, 1000);

        erie_server().exec_server_post(
            $("#frm_llm_optimize"), null,
            (resp) => {
                clearInterval(t);
                $("#txt_optimize_response").empty().append(resp);
                this.delegateEvents();
            })
        return last_stop(ev)
    },

    btn_compare_response_diff_click: function (ev) {
        $("#modal_diff").show();
        return last_stop(ev)
    },

    btn_compare_click: function (ev) {
        $("#td_compare_price").text("thinking...  this might take a bit ");
        $("#txt_compare_response").empty();
        $("#btn_compare_response_copy, #btn_compare_response_diff").hide();

        const t = setInterval(() => {
            $("#td_compare_price").text($("#td_compare_price").text() + ".");
        }, 1000);

        erie_server().exec_server_post(
            $("#frm_llm_compare"), null,
            (resp) => {
                clearInterval(t);
                $("#btn_compare_response_copy, #btn_compare_response_diff").show();
                $("#txt_compare_response").empty().append(resp.llm_response_text);
                $("#td_compare_price").empty().append(resp.price + " | " + resp.chat_millis);
                $("#compare_diff").removeClass("d-none").empty().append(resp.diff)
            }, 
            (err_resp)=>{
                clearInterval(t);
                $("#btn_compare_response_copy, #btn_compare_response_diff").hide();
                $("#txt_compare_response").empty();
                $("#td_compare_price").empty().append("Error:&nbsp;" + err_resp.responseJSON.error);
                $("#compare_diff").addClass("d-none");
            }
            );
        
        return last_stop(ev)
    },

    btn_ask_click: function (ev) {
        $("#txt_question_response").text("thinking...  this might take a bit ");

        const t = setInterval(() => {
            $("#txt_question_response").text($("#txt_question_response").text() + ".");
        }, 1000);

        erie_server().exec_server_post(
            $("#frm_llm_question"), null,
            (resp) => {
                clearInterval(t);
                $("#txt_question_response").empty().append(resp);
                this.delegateEvents();
            })
        return last_stop(ev)
    }
});