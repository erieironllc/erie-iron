NicheIdeasView = ErieView.extend({
    el: 'body',

    events: {
        'change #niche-select': 'niche_select_change',
        'click #find-ideas-btn': 'find_ideas_btn_click'
    },

    init_view: function (options) {
        this.setupNicheDescriptions();
    },

    setupNicheDescriptions: function() {
        // Store niche descriptions from template data
        this.nicheDescriptions = {};
        $('#niche-select option').each((index, option) => {
            const key = $(option).val();
            const description = $(option).data('description') || '';
            if (key) {
                this.nicheDescriptions[key] = description;
            }
        });
    },

    niche_select_change: function(ev) {
        const selectedNiche = $(ev.target).val();
        
        // Update description text
        const description = this.nicheDescriptions[selectedNiche] || '';
        $('#niche-description').text(description);
        
        // Reload page with new niche parameter
        const url = new URL(window.location);
        url.searchParams.set('niche', selectedNiche);
        window.location.href = url.toString();
    },

    find_ideas_btn_click: function(ev) {
        ev.preventDefault();
        
        const niche = $('#niche-select').val();
        const userInput = $('#user-guidance').val().trim();
        
        // Validate inputs
        if (!niche) {
            alert('Please select a niche category.');
            return;
        }
        
        // Show loading modal
        this.showLoadingModal();
        
        // Publish PubSub message via AJAX
        this.publishNicheBusinessIdeasMessage(niche, userInput);
    },

    showLoadingModal: function() {
        $('#generating-ideas-modal').modal('show');
    },

    hideLoadingModal: function() {
        $('#generating-ideas-modal').modal('hide');
    },

    publishNicheBusinessIdeasMessage: function(niche, userInput) {
        $.ajax({
            url: '/api/pubsub/publish/',
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': $('input[name="csrfmiddlewaretoken"]').val()
            },
            data: JSON.stringify({
                message_type: 'FIND_NICHE_BUSINESS_IDEAS',
                payload: {
                    niche: niche,
                    user_input: userInput,
                    requested_count: 5
                }
            }),
            success: (response) => {
                this.handlePublishSuccess(response);
            },
            error: (xhr, status, error) => {
                this.handlePublishError(xhr, status, error);
            }
        });
    },

    handlePublishSuccess: function(response) {
        this.hideLoadingModal();
        
        // Show success message
        const alert = $('<div class="alert alert-success alert-dismissible fade show" role="alert">')
            .html('<i class="fas fa-check-circle"></i> Business idea generation started! Ideas will appear below as they are generated. <button type="button" class="btn-close" data-bs-dismiss="alert"></button>');
        $('.niche-ideas-container').prepend(alert);
        
        // Auto-refresh page after delay to show new ideas
        setTimeout(() => {
            window.location.reload();
        }, 5000);
    },

    handlePublishError: function(xhr, status, error) {
        this.hideLoadingModal();
        
        // Show error message
        const alert = $('<div class="alert alert-danger alert-dismissible fade show" role="alert">')
            .html('<i class="fas fa-exclamation-triangle"></i> Failed to start idea generation. Please try again. <button type="button" class="btn-close" data-bs-dismiss="alert"></button>');
        $('.niche-ideas-container').prepend(alert);
        
        console.error('Error publishing message:', error);
    }
});