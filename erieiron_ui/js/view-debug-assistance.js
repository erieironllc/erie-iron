DebugAssistanceView = ErieView.extend({
    el: 'body',

    events: {
        'submit #debug-assistance-form': 'handleFormSubmit'
    },

    init_view: function (options) {
        const $container = $('.debug-assistance-view');
        this.taskId = $container.data('task-id');
        this.initializeView();
    },

    initializeView: function () {
    },

    handleFormSubmit: function (ev) {
        ev.preventDefault();
        
        this.submitDebugRequest($('#debug-question').val());
        return last_stop(ev);
    },

    submitDebugRequest: function (question) {
        this.hideDebugResponse();
        const submitBtn = $('#debug-assistance-form button[type="submit"]');
        const originalText = submitBtn.text();
        
        submitBtn.text('Processing...').prop('disabled', true);
        
        erie_server().exec_server_post(
            $("#debug-assistance-form"),
            {
                "question": question
            },
            (response) => {
                submitBtn.text(originalText).prop('disabled', false);
                if (response.success) {
                    $('#debug-response-content').html(response.debug_steps);
                    $('#debug-response').show();
                } else {
                    alert('Error: ' + (response.error || 'Failed to get debug assistance'));
                }
            },
            () => {
                submitBtn.text(originalText).prop('disabled', false);
                alert('An error occurred while getting debug assistance');
            }
        );
    },

    hideDebugResponse: function () {
        $('#debug-response').hide();
    },

    clearForm: function () {
        $('#debug-question').val('');
        this.hideDebugResponse();
    }
});