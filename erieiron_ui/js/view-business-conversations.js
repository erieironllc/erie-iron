BusinessConversationsView = ErieView.extend({
    el: '#business-conversations-root',

    events: {
        'click [data-action="new-conversation"]': 'createNewConversation',
        'click [data-action="send-message"]': 'handleSendMessage',
        'keypress [data-role="message-input"]': 'handleKeyPress',
        'click [data-action="approve-change"]': 'handleApproveChange',
        'click [data-action="decline-change"]': 'handleDeclineChange',
        'click .conversation-item': 'handleConversationClick'
    },

    init_view: function () {
        console.log("DUDE");
        this.businessId = this.$el.data('businessId');
        this.conversationId = null;
        this.messages = [];
        this.$messageInput = this.$('[data-role="message-input"]');
        this.$messagesContainer = this.$('[data-role="messages-container"]');
        this.$sendBtn = this.$('[data-action="send-message"]');
        this.$newConversationBtn = this.$('[data-action="new-conversation"]');

        // Load most recent conversation or create new one
        this.loadMostRecentConversation();
    },

    refresh: function () {
        this.renderMessages();
    },

    loadMostRecentConversation: function () {
        const self = this;
        const $firstConversation = this.$('.conversation-item').first();

        if ($firstConversation.length > 0) {
            // Load the first conversation in the list (most recent)
            const conversationId = $firstConversation.data('conversationId');
            self.loadConversation(conversationId);
        } else {
            // No conversations exist, create a new one
            self.createNewConversation();
        }
    },

    loadConversation: function (conversationId) {
        const self = this;

        erie_server().exec_server_get(
            `/api/conversation/${conversationId}/detail/`,
            {},
            (response) => {
                self.conversationId = conversationId;
                self.messages = response.messages || [];
                self.renderMessages();
                self.updateActiveConversation();
                self.$messageInput.prop('disabled', false);
                self.$sendBtn.prop('disabled', false);
            },
            (xhr) => {
                console.error('Failed to load conversation:', xhr);
                self.showStatus('Failed to load conversation', 'danger');
                // Fall back to creating a new conversation
                self.createNewConversation();
            }
        );
    },

    updateActiveConversation: function () {
        // Remove active class from all conversation items
        this.$('.conversation-item').removeClass('active');
        // Add active class to the current conversation
        if (this.conversationId) {
            this.$('.conversation-item[data-conversation-id="' + this.conversationId + '"]').addClass('active');
        }
    },

    createNewConversation: function (e) {
        if (e) e.preventDefault();

        const self = this;

        erie_server().exec_server_post(
            `/api/business/${this.businessId}/conversations/create/`,
            {
                title: 'Business Discussion'
            },
            (response) => {
                self.conversationId = response.conversation_id;
                self.messages = [];
                self.renderMessages();
                self.$messageInput.prop('disabled', false);
                self.$sendBtn.prop('disabled', false);
                self.showStatus('New conversation started - reloading page...', 'success');

                // Reload the page to show the new conversation in the sidebar
                setTimeout(function () {
                    window.location.reload();
                }, 1000);
            },
            (xhr) => {
                console.error('Failed to create conversation:', xhr);
                self.showStatus('Failed to create conversation', 'danger');
            }
        );
    },

    handleSendMessage: function (e) {
        e.preventDefault();

        const message = this.$messageInput.val().trim();
        if (!message || !this.conversationId) {
            return;
        }

        this.sendMessage(message);
    },

    handleKeyPress: function (e) {
        if (e.which === 13 && !e.shiftKey) {
            e.preventDefault();
            this.handleSendMessage(e);
        }
    },

    sendMessage: function (message) {
        const self = this;

        // Disable input while sending
        this.$messageInput.prop('disabled', true);
        this.$sendBtn.prop('disabled', true);

        // Add user message to UI immediately
        this.addMessageToUI({
            role: 'user',
            content: message,
            created_at: new Date().toISOString()
        });

        // Clear input
        this.$messageInput.val('');

        // Show loading indicator
        this.showLoadingIndicator();

        erie_server().exec_server_post(
            `/api/conversation/${this.conversationId}/message/`,
            {message: message},
            (response) => {
                // Remove loading indicator
                self.hideLoadingIndicator();

                // User message already added, just add assistant response
                self.addMessageToUI(response.assistant_message);

                // Handle any change proposals
                if (response.assistant_message.changes && response.assistant_message.changes.length > 0) {
                    response.assistant_message.changes.forEach(function (change) {
                        self.addChangeProposalToUI(change);
                    });
                }

                // Re-enable input
                self.$messageInput.prop('disabled', false);
                self.$sendBtn.prop('disabled', false);
                self.$messageInput.focus();
            },
            (xhr) => {
                // Remove loading indicator
                self.hideLoadingIndicator();

                console.error('Failed to send message:', xhr);
                self.showStatus('Failed to send message. Please try again.', 'danger');
                self.$messageInput.prop('disabled', false);
                self.$sendBtn.prop('disabled', false);
            }
        );
    },

    addMessageToUI: function (message) {
        this.messages.push(message);
        this.renderMessages();
    },

    renderMessages: function () {
        const self = this;
        this.$messagesContainer.empty();

        if (this.messages.length === 0) {
            this.$messagesContainer.html(
                '<div class="text-muted text-center py-4">' +
                '<p>No messages yet. Start a conversation about your business!</p>' +
                '</div>'
            );
            return;
        }

        this.messages.forEach(function (msg) {
            const $msgDiv = $('<div>')
                .addClass('conversation-message')
                .addClass('message-' + msg.role)
                .attr('data-message-id', msg.id);

            const $role = $('<div>')
                .addClass('message-role')
                .text(msg.role === 'user' ? 'You' : 'AI Assistant');

            const $content = $('<div>')
                .addClass('message-content')
                .html(self.renderMarkdown(msg.content));

            const $timestamp = $('<div>')
                .addClass('message-timestamp')
                .text(self.formatTimestamp(msg.created_at));

            $msgDiv.append($role, $content, $timestamp);
            self.$messagesContainer.append($msgDiv);
        });

        // Scroll to bottom
        this.$messagesContainer.scrollTop(this.$messagesContainer[0].scrollHeight);
    },

    addChangeProposalToUI: function (change) {
        const self = this;
        const $proposal = $('<div>')
            .addClass('change-proposal alert alert-warning')
            .attr('data-change-id', change.id);

        const $header = $('<h5>')
            .addClass('alert-heading')
            .html('<i class="bi bi-lightbulb"></i> Change Proposal: ' + change.change_type);

        const $description = $('<p>')
            .addClass('mb-2')
            .text(change.change_description);

        const $details = $('<details>')
            .addClass('mb-3');

        const $summary = $('<summary>')
            .text('View technical details');

        const $detailsContent = $('<pre>')
            .addClass('bg-light p-2 rounded')
            .css('font-size', '0.85rem')
            .text(JSON.stringify(change.change_details, null, 2));

        $details.append($summary, $detailsContent);

        const $actions = $('<div>')
            .addClass('d-flex gap-2');

        const $approveBtn = $('<button>')
            .addClass('btn btn-success btn-sm')
            .attr('data-action', 'approve-change')
            .attr('data-change-id', change.id)
            .html('<i class="bi bi-check-circle"></i> Approve & Apply');

        const $declineBtn = $('<button>')
            .addClass('btn btn-secondary btn-sm')
            .attr('data-action', 'decline-change')
            .attr('data-change-id', change.id)
            .html('<i class="bi bi-x-circle"></i> Decline');

        $actions.append($approveBtn, $declineBtn);

        $proposal.append($header, $description, $details, $actions);
        this.$messagesContainer.append($proposal);

        // Scroll to bottom to show the proposal
        this.$messagesContainer.scrollTop(this.$messagesContainer[0].scrollHeight);
    },

    handleApproveChange: function (e) {
        e.preventDefault();
        const changeId = $(e.currentTarget).data('changeId');

        if (!confirm('Approve this change? It will be applied to your business plan and architecture.')) {
            return;
        }

        const self = this;
        const $proposal = this.$('[data-change-id="' + changeId + '"]');

        $proposal.find('button').prop('disabled', true);

        erie_server().exec_server_post(
            `/api/conversation/change/${changeId}/approve/`,
            {},
            (response) => {
                $proposal
                    .removeClass('alert-warning')
                    .addClass('alert-success');

                $proposal.find('.alert-heading').html(
                    '<i class="bi bi-check-circle-fill"></i> Change Approved & Applied'
                );

                $proposal.find('[data-action]').remove();

                $proposal.append(
                    '<p class="mb-0 small"><strong>Status:</strong> This change has been applied to your business. ' +
                    'New initiatives or tasks may have been created.</p>'
                );

                self.showStatus('Change approved and applied successfully!', 'success');
            },
            (xhr) => {
                console.error('Failed to approve change:', xhr);
                self.showStatus('Failed to approve change: ' + (xhr.responseJSON?.error || 'Unknown error'), 'danger');
                $proposal.find('button').prop('disabled', false);
            }
        );
    },

    handleDeclineChange: function (e) {
        e.preventDefault();
        const changeId = $(e.currentTarget).data('changeId');

        const self = this;
        const $proposal = this.$('[data-change-id="' + changeId + '"]');

        erie_server().exec_server_post(
            `/api/conversation/change/${changeId}/decline/`,
            {},
            (response) => {
                $proposal.fadeOut(300, function () {
                    $(this).remove();
                });
                self.showStatus('Change proposal declined', 'info');
            },
            (xhr) => {
                console.error('Failed to decline change:', xhr);
                self.showStatus('Failed to decline change', 'danger');
            }
        );
    },

    renderMarkdown: function (text) {
        // Simple markdown rendering - just basics
        return text
            .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
            .replace(/\*(.+?)\*/g, '<em>$1</em>')
            .replace(/`(.+?)`/g, '<code>$1</code>')
            .replace(/\n/g, '<br>');
    },

    formatTimestamp: function (isoString) {
        const date = new Date(isoString);
        return date.toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'});
    },

    showLoadingIndicator: function () {
        const $loading = $('<div>')
            .addClass('loading-indicator')
            .attr('data-role', 'loading-indicator');

        const $spinner = $('<div>').addClass('spinner');
        const $text = $('<div>').addClass('loading-text').text('AI is thinking...');

        $loading.append($spinner, $text);
        this.$messagesContainer.append($loading);

        // Scroll to bottom to show the loading indicator
        this.$messagesContainer.scrollTop(this.$messagesContainer[0].scrollHeight);
    },

    hideLoadingIndicator: function () {
        this.$('[data-role="loading-indicator"]').remove();
    },

    handleConversationClick: function (e) {
        e.preventDefault();
        const conversationId = $(e.currentTarget).data('conversationId');
        if (conversationId && conversationId !== this.conversationId) {
            this.loadConversation(conversationId);
        }
    },

    showStatus: function (message, type) {
        const $status = this.$('[data-role="status"]');
        $status
            .removeClass('d-none alert-success alert-danger alert-info alert-warning')
            .addClass('alert-' + type)
            .text(message)
            .fadeIn();

        setTimeout(function () {
            $status.fadeOut(function () {
                $(this).addClass('d-none');
            });
        }, 5000);
    }
});
