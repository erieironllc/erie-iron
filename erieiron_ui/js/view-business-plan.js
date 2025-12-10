BusinessPlanView = ErieView.extend({
    el: 'body',

    events: {
        'click .export-pitch-deck-btn': 'handleExportClick'
    },

    init_view: function(options) {
        // View initialized
    },

    handleExportClick: function(ev) {
        const $btn = $(ev.currentTarget);
        const businessId = $btn.data('business-id');

        // Disable button and show loading state
        $btn.prop('disabled', true);
        const originalHtml = $btn.html();
        $btn.html('<span class="spinner-border spinner-border-sm me-2"></span>Generating...');

        // Construct download URL
        const downloadUrl = `/_business/export_pitch_deck/${businessId}`;

        // Create temporary anchor element and trigger click (most reliable download method)
        const link = document.createElement('a');
        link.href = downloadUrl;
        link.download = '';
        document.body.appendChild(link);
        link.click();
        document.body.removeChild(link);

        // Reset button after delay (download will start)
        setTimeout(() => {
            $btn.prop('disabled', false);
            $btn.html(originalHtml);
        }, 3000);

        return last_stop(ev);
    }
});
