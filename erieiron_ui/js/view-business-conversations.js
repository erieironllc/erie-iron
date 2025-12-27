BusinessConversationsView = ErieView.extend({
    el: '#business-conversations-root',

    events: {
        'click [data-action="new-conversation"]': 'createNewConversation',
        'click [data-action="send-message"]': 'handleSendMessage',
        'keypress [data-role="message-input"]': 'handleKeyPress',
        'click [data-action="approve-change"]': 'handleApproveChange',
        'click [data-action="decline-change"]': 'handleDeclineChange',
        'change [data-role="conversation-select"]': 'handleConversationSelect'
    },

    init_view: function () {
        console.log("Initializing BusinessConversationsView");
        this.businessId = this.$el.data('businessId');
        this.conversationId = this.$el.data('conversationId') || null;
        this.messages = [];
        this.$messageInput = this.$('[data-role="message-input"]');
        this.$messagesContainer = this.$('[data-role="messages-container"]');
        this.$sendBtn = this.$('[data-action="send-message"]');
        this.$newConversationBtn = this.$('[data-action="new-conversation"]');

        // Check if messages are already server-rendered
        const $existingMessages = this.$messagesContainer.find('.conversation-message');

        if ($existingMessages.length > 0 && this.conversationId) {
            // Messages are already rendered server-side
            console.log('Messages already rendered server-side, count:', $existingMessages.length);

            // Scroll to the last message's timestamp after layout is complete
            const self = this;
            const $lastMessage = $existingMessages.last();
            if ($lastMessage.length > 0) {
                const lastMessageId = $lastMessage.data('messageId');
                console.log('Scrolling to last message:', lastMessageId);

                // Scroll to last message using scrollIntoView
                setTimeout(function() {
                    // Get the last message element
                    const $lastMsg = $lastMessage;

                    if ($lastMsg && $lastMsg.length > 0) {
                        console.log('Scrolling to last message using scrollIntoView');

                        // Scroll the last message into view at the bottom of the container
                        $lastMsg[0].scrollIntoView({
                            behavior: 'auto',
                            block: 'end',
                            inline: 'nearest'
                        });

                        console.log('ScrollIntoView called on message:', lastMessageId);

                        // Also try scrolling to bottom manually as backup
                        setTimeout(function() {
                            // const container = self.$messagesContainer[0];
                            // Scroll up a bit from the very bottom to clear the input box
                            // container.scrollTop = container.scrollTop - 150;
                            // console.log('Adjusted scroll position to clear input box, scrollTop:', container.scrollTop);
                        }, 100);
                    }
                }, 500);
            }
        } else {
            // Check URL hash for conversation ID (e.g., #conversation=uuid)
            const urlParams = new URLSearchParams(window.location.hash.substring(1));
            const conversationIdFromUrl = urlParams.get('conversation');

            if (conversationIdFromUrl) {
                console.log('Loading conversation from URL:', conversationIdFromUrl);
                this.loadConversation(conversationIdFromUrl);
            } else if (!this.conversationId) {
                // Load most recent conversation only if not already loaded
                this.loadMostRecentConversation();
            }
        }
    },

    refresh: function () {
        this.renderMessages();
    },

    loadMostRecentConversation: function () {
        const self = this;
        const $conversationSelect = this.$('[data-role="conversation-select"]');

        // Check if dropdown has any conversation options (excluding the placeholder)
        const $options = $conversationSelect.find('option[value!=""]');

        console.log('loadMostRecentConversation: found', $options.length, 'conversation(s)');

        if ($options.length > 0) {
            // Load the first conversation option (most recent)
            const conversationId = $options.first().val();
            console.log('Loading most recent conversation:', conversationId);
            $conversationSelect.val(conversationId);
            self.loadConversation(conversationId);
        } else {
            // No conversations exist - do NOT create one automatically
            // Just leave the empty state message
            console.log('No conversations available');
            self.$messageInput.prop('disabled', true);
            self.$sendBtn.prop('disabled', true);
        }
    },

    loadConversation: function (conversationId) {
        const self = this;

        console.log('loadConversation called for ID:', conversationId);

        erie_server().exec_server_get(
            `/api/conversation/${conversationId}/`,
            (response) => {
                console.log('Conversation loaded successfully:', response);
                console.log('Number of messages:', response.messages ? response.messages.length : 0);

                self.conversationId = conversationId;
                self.messages = response.messages || [];

                // Set lastAddedMessageId to the last message so we scroll to it
                if (self.messages.length > 0) {
                    const lastMessage = self.messages[self.messages.length - 1];
                    self.lastAddedMessageId = lastMessage.id;
                    console.log('Setting scroll target to last message:', lastMessage.id);
                }

                self.renderMessages();
                self.updateActiveConversation();
                self.$messageInput.prop('disabled', false);
                self.$sendBtn.prop('disabled', false);
            },
            (xhr) => {
                console.error('Failed to load conversation:', xhr);
                console.error('Error status:', xhr.status);
                console.error('Error response:', xhr.responseText);
                self.showStatus('Failed to load conversation. Please try another conversation or create a new one.', 'danger');

                // Do NOT automatically create a new conversation - let the user decide
                self.$messageInput.prop('disabled', true);
                self.$sendBtn.prop('disabled', true);
            }
        );
    },

    updateActiveConversation: function () {
        // Update dropdown to show current conversation
        if (this.conversationId) {
            this.$('[data-role="conversation-select"]').val(this.conversationId);
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

                // Update conversation title in dropdown if it changed
                if (response.conversation_title && response.conversation_title !== self.getConversationTitle()) {
                    self.updateConversationTitle(response.conversation_title);
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
        this.lastAddedMessageId = message.id;
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

        // Scroll to the last added message (specifically to its timestamp)
        if (this.lastAddedMessageId) {
            this.scrollToMessage(this.lastAddedMessageId);
            this.lastAddedMessageId = null; // Clear after scrolling
        } else {
            // Fallback to scrolling to bottom
            this.$messagesContainer.scrollTop(this.$messagesContainer[0].scrollHeight);
        }
    },

    scrollToMessage: function (messageId) {
        const $message = this.$('.conversation-message[data-message-id="' + messageId + '"]');
        if ($message.length > 0) {
            const $timestamp = $message.find('.message-timestamp');
            const container = this.$messagesContainer[0];

            // Scroll to the timestamp element (last element in the message)
            if ($timestamp.length > 0) {
                const timestampOffset = $timestamp.offset().top;
                const containerOffset = this.$messagesContainer.offset().top;
                const scrollPosition = container.scrollTop + timestampOffset - containerOffset - 20; // 20px padding from top

                // Smooth scroll to the timestamp
                this.$messagesContainer.animate({
                    scrollTop: scrollPosition
                }, 300);
            }
        }
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
        const $text = $('<div>').addClass('loading-text').text('thinking...');

        $loading.append($spinner, $text);
        this.$messagesContainer.append($loading);

        // Scroll to bottom to show the loading indicator
        this.$messagesContainer.scrollTop(this.$messagesContainer[0].scrollHeight);
    },

    hideLoadingIndicator: function () {
        this.$('[data-role="loading-indicator"]').remove();
    },

    handleConversationSelect: function (e) {
        const conversationId = $(e.currentTarget).val();
        if (conversationId && conversationId !== this.conversationId) {
            this.loadConversation(conversationId);
        }
    },

    getConversationTitle: function () {
        const $select = this.$('[data-role="conversation-select"]');
        const $option = $select.find('option[value="' + this.conversationId + '"]');
        return $option.text();
    },

    updateConversationTitle: function (newTitle) {
        const $select = this.$('[data-role="conversation-select"]');
        const $option = $select.find('option[value="' + this.conversationId + '"]');
        if ($option.length > 0) {
            $option.text(newTitle);
            console.log('Updated conversation title to:', newTitle);
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
