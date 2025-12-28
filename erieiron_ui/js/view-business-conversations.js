BusinessConversationsView = ErieView.extend({
    el: '#business-conversations-root',

    events: {
        'click [data-action="new-conversation"]': 'createNewConversation',
        'click [data-action="delete-conversation"]': 'handleDeleteConversation',
        'click [data-action="send-message"]': 'handleSendMessage',
        'keypress [data-role="message-input"]': 'handleKeyPress',
        'click [data-action="approve-change"]': 'handleApproveChange',
        'click [data-action="decline-change"]': 'handleDeclineChange',
        'change [data-role="conversation-select"]': 'handleConversationSelect',
        'click [data-action="toggle-change-history"]': 'handleToggleChangeHistory'
    },

    init_view: function () {
        console.log("Initializing BusinessConversationsView");
        this.businessId = this.$el.data('businessId');
        this.conversationId = this.$el.data('conversationId') || null;
        this.messages = [];
        this.changes = [];
        this.$messageInput = this.$('[data-role="message-input"]');
        this.$messagesContainer = this.$('[data-role="messages-container"]');
        this.$sendBtn = this.$('[data-action="send-message"]');
        this.$newConversationBtn = this.$('[data-action="new-conversation"]');
        this.$changeHistoryList = this.$('[data-role="change-history-list"]');
        this.$changeCount = this.$('[data-role="change-count"]');

        // Check if messages are already server-rendered
        const $existingMessages = this.$messagesContainer.find('.conversation-message');

        if ($existingMessages.length > 0 && this.conversationId) {
            this.loadChangeHistory();
            this.scrollToEnd();
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

                self.renderMessages();
                self.updateActiveConversation();
                self.$messageInput.prop('disabled', false);
                self.$sendBtn.prop('disabled', false);

                // Load change history for this conversation
                self.loadChangeHistory();
                this.scrollToEnd();
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

    handleDeleteConversation: function (e) {
        if (e) e.preventDefault();

        if (!this.conversationId) {
            return;
        }

        const conversationTitle = this.getConversationTitle();

        if (!confirm(`Are you sure you want to delete the conversation "${conversationTitle}"? This action cannot be undone.`)) {
            return;
        }

        const self = this;
        const $deleteBtn = this.$('[data-action="delete-conversation"]');

        $deleteBtn.prop('disabled', true);

        erie_server().exec_server_post(
            `/api/conversation/${this.conversationId}/delete/`,
            {},
            (response) => {
                self.showStatus('Conversation deleted - reloading page...', 'success');

                setTimeout(function () {
                    window.location.reload();
                }, 1000);
            },
            (xhr) => {
                console.error('Failed to delete conversation:', xhr);
                self.showStatus('Failed to delete conversation', 'danger');
                $deleteBtn.prop('disabled', false);
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
        console.log('[BusinessConversations] addMessageToUI called with:', message);

        this.messages.push(message);
        this.lastAddedMessageId = message.id;

        // Remove "no messages" placeholder if it exists
        this.$messagesContainer.find('.text-muted.text-center').remove();

        // Append the new message directly instead of re-rendering all messages
        const $msgDiv = $('<div>')
            .addClass('conversation-message')
            .addClass('message-' + message.role)
            .attr('data-message-id', message.id);

        const $header = $('<div>')
            .addClass('message-header');

        const $role = $('<div>')
            .addClass('message-role')
            .text(message.role === 'user' ? 'You' : 'AI Assistant');

        const $copyBtn = $('<button>')
            .addClass('btn btn-sm copy-btn bi bi-copy')
            .attr('data-target', '.message-content')
            .attr('title', 'Copy message to clipboard');

        $header.append($role, $copyBtn);

        const $content = $('<div>')
            .addClass('message-content')
            .html(renderMarkdown(message.content));

        const $timestamp = $('<div>')
            .addClass('message-timestamp')
            .text(formatTimestamp(message.created_at));

        $msgDiv.append($header, $content, $timestamp);

        console.log('[BusinessConversations] Message element created');
        console.log('[BusinessConversations] Classes:', $msgDiv.attr('class'));
        console.log('[BusinessConversations] Full HTML:', $msgDiv[0].outerHTML);
        console.log('[BusinessConversations] Parent container classes:', this.$messagesContainer.attr('class'));
        console.log('[BusinessConversations] Parent container parent classes:', this.$messagesContainer.parent().attr('class'));

        this.$messagesContainer.append($msgDiv);

        console.log('[BusinessConversations] Message appended to container');
        console.log('[BusinessConversations] Computed styles:', window.getComputedStyle($msgDiv[0]));

        this.scrollToEnd();
    },

    renderMessages: function () {
        const self = this;
        this.$messagesContainer.empty();

        if (this.messages.length === 0) {
            return;
        }

        this.messages.forEach(function (msg) {
            const $msgDiv = $('<div>')
                .addClass('conversation-message')
                .addClass('message-' + msg.role)
                .attr('data-message-id', msg.id);

            const $header = $('<div>')
                .addClass('message-header');

            const $role = $('<div>')
                .addClass('message-role')
                .text(msg.role === 'user' ? 'You' : 'AI Assistant');

            const $copyBtn = $('<button>')
                .addClass('btn btn-sm copy-btn bi bi-copy')
                .attr('data-target', '.message-content')
                .attr('title', 'Copy message to clipboard');

            $header.append($role, $copyBtn);

            const $content = $('<div>')
                .addClass('message-content')
                .html(renderMarkdown(msg.content));

            const $timestamp = $('<div>')
                .addClass('message-timestamp')
                .text(formatTimestamp(msg.created_at));

            $msgDiv.append($header, $content, $timestamp);
            self.$messagesContainer.append($msgDiv);
        });

        this.scrollToEnd();
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

                // Reload change history to show the newly applied change
                self.loadChangeHistory();
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
    },

    loadChangeHistory: function () {
        const self = this;

        if (!this.conversationId) {
            return;
        }

        console.log('Loading change history for conversation:', this.conversationId);

        erie_server().exec_server_get(
            `/api/conversation/${this.conversationId}/changes/`,
            (response) => {
                console.log('Change history loaded:', response);
                self.changes = response.changes || [];
                self.renderChangeHistory();
            },
            (xhr) => {
                console.error('Failed to load change history:', xhr);
            }
        );
    },

    renderChangeHistory: function () {
        const self = this;

        this.$changeCount.text(this.changes.length);

        if (this.changes.length === 0) {
            this.$changeHistoryList.html(
                '<div class="text-center py-3 text-muted" data-role="no-changes-message">' +
                '<small>No changes yet</small>' +
                '</div>'
            );
            return;
        }

        this.$changeHistoryList.empty();

        this.changes.forEach(function (change) {
            const $changeCard = self.buildChangeHistoryCard(change);
            self.$changeHistoryList.append($changeCard);
        });
    },

    buildChangeHistoryCard: function (change) {
        const self = this;
        const $card = $('<div>').addClass('change-history-card');

        const statusClass = change.applied ? 'applied' : (change.approved ? 'approved' : 'pending');
        $card.addClass('status-' + statusClass);

        const $header = $('<div>').addClass('change-card-header');

        const $typeAndStatus = $('<div>').addClass('d-flex justify-content-between align-items-start mb-2');

        const $type = $('<div>').addClass('change-type');
        const typeIcon = this.getChangeTypeIcon(change.change_type);
        $type.html(`<i class="bi ${typeIcon}"></i> ${this.formatChangeType(change.change_type)}`);

        const $status = $('<div>').addClass('change-status');
        if (change.applied) {
            $status.html('<span class="badge bg-success"><i class="bi bi-check-circle-fill"></i> Applied</span>');
        } else if (change.approved) {
            $status.html('<span class="badge bg-info"><i class="bi bi-check-circle"></i> Approved</span>');
        } else {
            $status.html('<span class="badge bg-warning"><i class="bi bi-clock"></i> Pending</span>');
        }

        $typeAndStatus.append($type, $status);
        $header.append($typeAndStatus);

        const $description = $('<div>').addClass('change-description').text(change.change_description);
        $header.append($description);

        const $meta = $('<div>').addClass('change-meta mt-2');
        const createdDate = new Date(change.created_at);
        $meta.append(`<small class="text-muted"><i class="bi bi-calendar"></i> ${formatDate(createdDate)}</small>`);

        if (change.applied_at) {
            const appliedDate = new Date(change.applied_at);
            $meta.append(` <small class="text-muted ms-2"><i class="bi bi-check-circle"></i> Applied ${formatDate(appliedDate)}</small>`);
        }

        $header.append($meta);
        $card.append($header);

        if (change.resulting_tasks && change.resulting_tasks.length > 0) {
            const $tasks = $('<div>').addClass('change-tasks mt-2');
            const $tasksHeader = $('<small>').addClass('text-muted d-block mb-1').html('<strong>Resulting Tasks:</strong>');
            $tasks.append($tasksHeader);

            const $tasksList = $('<ul>').addClass('mb-0');
            change.resulting_tasks.forEach(function (task) {
                const $taskItem = $('<li>').addClass('small');
                $taskItem.html(`<a href="#" class="text-decoration-none">${escapeHtml(task.name)}</a> <span class="text-muted">(${task.status})</span>`);
                $tasksList.append($taskItem);
            });
            $tasks.append($tasksList);
            $card.append($tasks);
        }

        if (change.change_details && Object.keys(change.change_details).length > 0) {
            const $details = $('<details>').addClass('change-details mt-2');
            const $summary = $('<summary>').addClass('small').text('View technical details');
            const $detailsContent = $('<pre>').addClass('small mt-1').text(JSON.stringify(change.change_details, null, 2));
            $details.append($summary, $detailsContent);
            $card.append($details);
        }

        return $card;
    },

    getChangeTypeIcon: function (changeType) {
        const icons = {
            'business_plan': 'bi-file-text',
            'architecture': 'bi-diagram-3',
            'infrastructure': 'bi-server',
            'initiative': 'bi-flag',
            'task': 'bi-check2-square'
        };
        return icons[changeType] || 'bi-file-text';
    },

    formatChangeType: function (changeType) {
        const types = {
            'business_plan': 'Business Plan',
            'architecture': 'Architecture',
            'infrastructure': 'Infrastructure',
            'initiative': 'New Initiative',
            'task': 'New Task'
        };
        return types[changeType] || changeType;
    },

    handleToggleChangeHistory: function (e) {
        const $chevron = this.$('[data-role="chevron"]');
        const isExpanded = $('#change-history-content').hasClass('show');

        if (isExpanded) {
            $chevron.removeClass('bi-chevron-up').addClass('bi-chevron-down');
        } else {
            $chevron.removeClass('bi-chevron-down').addClass('bi-chevron-up');
        }
    },
    
    scrollToEnd() {
        // const el_to_scroll_to = $(".conversation-message,.change-proposal").last();
        setTimeout(() => {
            this.$messagesContainer.scrollTop(this.$messagesContainer[0].scrollHeight + 200);
        }, 500);
    },
});
