ConversationViewBase = ErieView.extend({
    events: {
        'click [data-action="new-conversation"]': 'createNewConversation',
        'click [data-action="rename-conversation"]': 'handleRenameConversation',
        'click [data-action="delete-conversation"]': 'handleDeleteConversation',
        'click [data-action="send-message"]': 'handleSendMessage',
        'keydown [data-role="message-input"]': 'handleKeyPress',
        'click [data-action="approve-change"]': 'handleApproveChange',
        'click [data-action="decline-change"]': 'handleDeclineChange',
        'change [data-role="conversation-select"]': 'handleConversationSelect',
        'click [data-role="conversation-link"]': 'handleConversationSelect',
        'click [data-action="toggle-change-history"]': 'handleToggleChangeHistory'
    },

    init_view: function () {
        this.conversationId = this.$el.data('conversationId') || null;
        this.createUrl = this.$el.data('createUrl');
        this.detailUrlTemplate = this.$el.data('detailUrlTemplate');
        this.messageUrlTemplate = this.$el.data('messageUrlTemplate');
        this.renameUrlTemplate = this.$el.data('renameUrlTemplate');
        this.deleteUrlTemplate = this.$el.data('deleteUrlTemplate');
        this.defaultConversationTitle = this.$el.data('defaultConversationTitle') || 'New Conversation';
        this.autoCreateOnSend = !!this.$el.data('autoCreateOnSend');
        this.messages = [];
        this.messageChanges = {};
        this.changes = [];
        this.$messageInput = this.$('[data-role="message-input"]');
        this.$messagesContainer = this.$('[data-role="messages-container"]');
        this.$sendBtn = this.$('[data-action="send-message"]');
        this.$conversationSelect = this.$('[data-role="conversation-select"]');
        this.$conversationList = this.$('[data-role="conversation-list"]');
        this.$changeHistoryList = this.$('[data-role="change-history-list"]');
        this.$changeCount = this.$('[data-role="change-count"]');

        const $existingMessages = this.$messagesContainer.find('.conversation-message, .change-proposal');

        if ($existingMessages.length > 0 && this.conversationId) {
            this.messages = this.collectRenderedMessages();
            this.updateActiveConversation();
            this.setComposerEnabled(true);
            this.loadChangeHistory();
            this.scrollToEnd();
            return;
        }

        if (this.conversationId) {
            this.loadConversation(this.conversationId);
            return;
        }

        this.loadMostRecentConversation();
    },

    collectRenderedMessages: function () {
        const messages = [];
        this.$messagesContainer.find('.conversation-message').each(function () {
            const $message = $(this);
            const roleClass = ($message.attr('class') || '').split(' ').find(function (className) {
                return className.indexOf('message-') === 0;
            }) || '';
            const role = roleClass.replace('message-', '') || 'assistant';
            messages.push({
                id: $message.data('messageId'),
                role: role,
                content: $message.find('.message-content').text(),
                created_at: $message.find('.message-timestamp').text()
            });
        });
        return messages;
    },

    resolveConversationUrl: function (template, conversationId) {
        return String(template)
            .replace('__CONVERSATION_ID__', conversationId)
            .replace('__ID__', conversationId);
    },

    getConversationEntries: function () {
        const entries = [];

        this.$('[data-role="conversation-link"]').each(function () {
            entries.push({
                id: String($(this).data('conversationId')),
                title: $(this).find('[data-role="conversation-link-label"]').text().trim() || $(this).text().trim(),
                source: 'link',
                element: $(this)
            });
        });

        this.$conversationSelect.find('option[value!=""]').each(function () {
            entries.push({
                id: String($(this).val()),
                title: $(this).text().trim(),
                source: 'select',
                element: $(this)
            });
        });

        return entries;
    },

    getNextConversationId: function () {
        const entries = this.getConversationEntries();
        return entries.length > 0 ? entries[0].id : null;
    },

    loadMostRecentConversation: function () {
        const nextConversationId = this.getNextConversationId();

        if (nextConversationId) {
            this.loadConversation(nextConversationId);
            return;
        }

        this.updateActiveConversation();
        this.renderMessages();
        this.setComposerEnabled(this.autoCreateOnSend);
    },

    setComposerEnabled: function (enabled) {
        this.$messageInput.prop('disabled', !enabled);
        this.$sendBtn.prop('disabled', !enabled);
    },

    loadConversation: function (conversationId) {
        const self = this;

        erie_server().exec_server_get(
            this.resolveConversationUrl(this.detailUrlTemplate, conversationId),
            (response) => {
                self.conversationId = String(conversationId);
                self.messages = response.messages || [];
                self.messageChanges = response.message_changes || {};
                self.renderMessages();
                self.updateActiveConversation();
                self.setComposerEnabled(true);
                self.loadChangeHistory();
                self.scrollToEnd();
            },
            (xhr) => {
                self.showStatus('Failed to load conversation. Please try again.', 'danger');
                self.setComposerEnabled(self.autoCreateOnSend);
            }
        );
    },

    buildConversationEntryElement: function (conversationId, title) {
        return $('<div>')
            .addClass('root-chat__conversation-item')
            .attr('data-role', 'conversation-item')
            .attr('data-conversation-id', conversationId)
            .append(
                $('<button>')
                    .attr('type', 'button')
                    .addClass('root-chat__conversation-link')
                    .attr('data-role', 'conversation-link')
                    .attr('data-conversation-id', conversationId)
                    .append(
                        $('<span>')
                            .attr('data-role', 'conversation-link-label')
                            .text(title)
                    )
            )
            .append(
                $('<div>')
                    .addClass('dropdown root-chat__conversation-menu')
                    .append(
                        $('<button>')
                            .attr('type', 'button')
                            .addClass('btn btn-sm root-chat__conversation-menu-toggle')
                            .attr('data-bs-toggle', 'dropdown')
                            .attr('aria-expanded', 'false')
                            .attr('aria-label', 'Open chat actions')
                            .append($('<i>').addClass('bi bi-three-dots-vertical'))
                    )
                    .append(
                        $('<ul>')
                            .addClass('dropdown-menu dropdown-menu-end root-chat__conversation-menu-list')
                            .append(
                                $('<li>').append(
                                    $('<button>')
                                        .attr('type', 'button')
                                        .addClass('dropdown-item')
                                        .attr('data-action', 'rename-conversation')
                                        .attr('data-conversation-id', conversationId)
                                        .text('Rename chat')
                                )
                            )
                            .append(
                                $('<li>').append(
                                    $('<button>')
                                        .attr('type', 'button')
                                        .addClass('dropdown-item text-danger')
                                        .attr('data-action', 'delete-conversation')
                                        .attr('data-conversation-id', conversationId)
                                        .text('Delete chat')
                                )
                            )
                    )
            );
    },

    prependConversationEntry: function (conversationId, title) {
        if (this.$conversationList.length > 0) {
            this.$('[data-role="empty-conversation-list"]').addClass('d-none');
            this.$conversationList.prepend(this.buildConversationEntryElement(conversationId, title));
        }

        if (this.$conversationSelect.length > 0) {
            const $option = $('<option>')
                .val(conversationId)
                .text(title);
            this.$conversationSelect.prepend($option);
            this.$conversationSelect.val(conversationId);
        }
    },

    updateActiveConversation: function () {
        const conversationId = this.conversationId ? String(this.conversationId) : null;

        this.$('[data-role="conversation-item"]').removeClass('is-active');
        if (conversationId) {
            this.$('[data-role="conversation-item"]').filter(function () {
                return String($(this).data('conversationId')) === conversationId;
            }).addClass('is-active');
        }

        if (this.$conversationSelect.length > 0) {
            this.$conversationSelect.val(conversationId || '');
        }

        this.$('[data-role="current-conversation-title"]').text(
            conversationId ? this.getConversationTitle() : 'New Chat'
        );
    },

    createNewConversation: function (e, callback) {
        if (e) {
            e.preventDefault();
        }

        const self = this;
        erie_server().exec_server_post(
            this.createUrl,
            {
                title: this.defaultConversationTitle
            },
            (response) => {
                self.conversationId = String(response.conversation_id);
                self.messages = [];
                self.messageChanges = {};
                self.prependConversationEntry(self.conversationId, response.title || self.defaultConversationTitle);
                self.renderMessages();
                self.updateActiveConversation();
                self.setComposerEnabled(true);
                self.$messageInput.focus();

                if (callback) {
                    callback();
                    return;
                }

                self.showStatus('New conversation started.', 'success');
            },
            () => {
                self.showStatus('Failed to create conversation.', 'danger');
            }
        );
    },

    handleRenameConversation: function (e) {
        if (e) {
            e.preventDefault();
            e.stopPropagation();
        }

        const conversationId = String($(e.currentTarget).data('conversationId') || '');
        if (!conversationId) {
            return;
        }

        const currentTitle = this.getConversationTitleById(conversationId);
        const requestedTitle = prompt('Rename chat', currentTitle);
        if (requestedTitle === null) {
            return;
        }

        const trimmedTitle = requestedTitle.trim();
        if (!trimmedTitle) {
            this.showStatus('Chat name cannot be empty.', 'danger');
            return;
        }

        if (trimmedTitle === currentTitle) {
            return;
        }

        const self = this;
        $(e.currentTarget).prop('disabled', true);
        erie_server().exec_server_post(
            this.resolveConversationUrl(this.renameUrlTemplate, conversationId),
            {title: trimmedTitle},
            (response) => {
                if (String(self.conversationId) === conversationId) {
                    self.updateConversationTitle(response.title);
                } else {
                    self.updateConversationEntryTitle(conversationId, response.title);
                }
                $(e.currentTarget).prop('disabled', false);
                self.showStatus('Chat renamed.', 'success');
            },
            () => {
                self.showStatus('Failed to rename chat.', 'danger');
                $(e.currentTarget).prop('disabled', false);
            }
        );
    },

    handleDeleteConversation: function (e) {
        if (e) {
            e.preventDefault();
            e.stopPropagation();
        }

        const targetConversationId = String(
            $(e.currentTarget).data('conversationId') || this.conversationId || ''
        );
        if (!targetConversationId) {
            return;
        }

        const conversationTitle = this.getConversationTitleById(targetConversationId);
        if (!confirm(`Delete "${conversationTitle}"? This cannot be undone.`)) {
            return;
        }

        const self = this;
        const isCurrentConversation = String(this.conversationId) === targetConversationId;
        $(e.currentTarget).prop('disabled', true);
        erie_server().exec_server_post(
            this.resolveConversationUrl(this.deleteUrlTemplate, targetConversationId),
            {},
            () => {
                self.removeConversationEntry(targetConversationId);

                if (isCurrentConversation) {
                    const nextConversationId = self.getNextConversationId();

                    self.conversationId = null;
                    self.messages = [];
                    self.messageChanges = {};
                    self.renderMessages();
                    self.updateActiveConversation();

                    if (nextConversationId) {
                        self.loadConversation(nextConversationId);
                    } else {
                        self.setComposerEnabled(self.autoCreateOnSend);
                    }
                }

                self.showStatus('Conversation deleted.', 'success');
            },
            () => {
                self.showStatus('Failed to delete conversation.', 'danger');
                $(e.currentTarget).prop('disabled', false);
            }
        );
    },

    removeConversationEntry: function (conversationId) {
        this.$('[data-role="conversation-item"]').filter(function () {
            return String($(this).data('conversationId')) === String(conversationId);
        }).remove();
        this.$conversationSelect.find(`option[value="${conversationId}"]`).remove();

        if (this.$('[data-role="conversation-item"]').length === 0) {
            this.$('[data-role="empty-conversation-list"]').removeClass('d-none');
        }
    },

    handleSendMessage: function (e) {
        if (e) {
            e.preventDefault();
        }

        const message = this.$messageInput.val().trim();
        if (!message) {
            return;
        }

        if (!this.conversationId && this.autoCreateOnSend) {
            this.createNewConversation(null, this.sendMessage.bind(this, message));
            return;
        }

        if (!this.conversationId) {
            return;
        }

        this.sendMessage(message);
    },

    handleKeyPress: function (e) {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            this.handleSendMessage(e);
        }
    },

    sendMessage: function (message) {
        const self = this;

        this.setComposerEnabled(false);
        this.addMessageToUI({
            role: 'user',
            content: message,
            created_at: new Date().toISOString()
        });
        this.$messageInput.val('');
        this.showLoadingIndicator();

        erie_server().exec_server_post(
            this.resolveConversationUrl(this.messageUrlTemplate, this.conversationId),
            {message: message},
            (response) => {
                self.hideLoadingIndicator();
                self.addMessageToUI(response.assistant_message);

                if (response.assistant_message.changes && response.assistant_message.changes.length > 0) {
                    response.assistant_message.changes.forEach(function (change) {
                        self.addChangeProposalToUI(change);
                    });
                }

                if (response.conversation_title && response.conversation_title !== self.getConversationTitle()) {
                    self.updateConversationTitle(response.conversation_title);
                }

                self.setComposerEnabled(true);
                self.$messageInput.focus();
            },
            (xhr) => {
                self.hideLoadingIndicator();
                self.showStatus('Failed to send message. Please try again.', 'danger');
                self.setComposerEnabled(true);
            }
        );
    },

    buildMessageElement: function (message) {
        const $message = $('<div>')
            .addClass('conversation-message')
            .addClass('message-' + message.role)
            .attr('data-message-id', message.id || '');

        const $header = $('<div>').addClass('message-header');
        const $role = $('<div>')
            .addClass('message-role')
            .text(this.formatRoleLabel(message.role));
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

        $message.append($header, $content, $timestamp);
        return $message;
    },

    formatRoleLabel: function (role) {
        if (role === 'user') {
            return 'You';
        }
        if (role === 'system') {
            return 'System';
        }
        return 'AI Assistant';
    },

    addMessageToUI: function (message) {
        this.messages.push(message);
        this.$messagesContainer.find('.conversation-empty-state').remove();
        this.$messagesContainer.append(this.buildMessageElement(message));
        this.scrollToEnd();
    },

    renderMessages: function () {
        const self = this;
        this.$messagesContainer.empty();

        if (this.messages.length === 0) {
            this.renderEmptyState();
            return;
        }

        this.messages.forEach(function (message) {
            self.$messagesContainer.append(self.buildMessageElement(message));

            const changesForMessage = self.messageChanges[String(message.id)] || [];
            changesForMessage.forEach(function (change) {
                self.$messagesContainer.append(self.buildChangeProposalElement(change));
            });
        });

        this.scrollToEnd();
    },

    renderEmptyState: function () {
        const $template = this.$('[data-role="empty-state-template"]').first();
        if ($template.length > 0) {
            this.$messagesContainer.append($template.clone().removeClass('d-none').removeAttr('data-role'));
        }
    },

    buildChangeProposalElement: function (change) {
        const $proposal = $('<div>')
            .addClass('change-proposal alert alert-warning')
            .attr('data-change-id', change.id);

        const $header = $('<h5>')
            .addClass('alert-heading')
            .html('<i class="bi bi-lightbulb"></i> Change Proposal: ' + this.formatChangeType(change.change_type));
        const $description = $('<p>')
            .addClass('mb-2')
            .text(change.change_description);
        const $details = $('<details>').addClass('mb-3');
        const $summary = $('<summary>').text('View technical details');
        const $detailsContent = $('<pre>')
            .addClass('bg-light p-2 rounded')
            .css('font-size', '0.85rem')
            .text(JSON.stringify(change.change_details, null, 2));
        $details.append($summary, $detailsContent);

        const $actions = $('<div>').addClass('d-flex gap-2');
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
        return $proposal;
    },

    addChangeProposalToUI: function (change) {
        this.$messagesContainer.append(this.buildChangeProposalElement(change));
        this.scrollToEnd();
    },

    handleApproveChange: function (e) {
        e.preventDefault();
        const changeId = $(e.currentTarget).data('changeId');
        if (!confirm('Approve this change? It will be applied immediately.')) {
            return;
        }

        const self = this;
        const $proposal = this.$('[data-change-id="' + changeId + '"]');
        $proposal.find('button').prop('disabled', true);

        erie_server().exec_server_post(
            `/api/conversation/change/${changeId}/approve/`,
            {},
            () => {
                self.showStatus('Change approved and applied successfully.', 'success');
                self.loadConversation(self.conversationId);
            },
            (xhr) => {
                self.showStatus(
                    'Failed to approve change: ' + (xhr.responseJSON?.error || 'Unknown error'),
                    'danger'
                );
                $proposal.find('button').prop('disabled', false);
            }
        );
    },

    handleDeclineChange: function (e) {
        e.preventDefault();
        const changeId = $(e.currentTarget).data('changeId');
        const self = this;

        erie_server().exec_server_post(
            `/api/conversation/change/${changeId}/decline/`,
            {},
            () => {
                self.$('[data-change-id="' + changeId + '"]').fadeOut(200, function () {
                    $(this).remove();
                });
                self.showStatus('Change proposal declined.', 'info');
            },
            () => {
                self.showStatus('Failed to decline change.', 'danger');
            }
        );
    },

    showLoadingIndicator: function () {
        const $loading = $('<div>')
            .addClass('loading-indicator')
            .attr('data-role', 'loading-indicator')
            .append($('<div>').addClass('spinner'))
            .append($('<div>').addClass('loading-text').text('thinking...'));

        this.$messagesContainer.append($loading);
        this.scrollToEnd();
    },

    hideLoadingIndicator: function () {
        this.$('[data-role="loading-indicator"]').remove();
    },

    handleConversationSelect: function (e) {
        let conversationId = null;
        if ($(e.currentTarget).is('[data-role="conversation-link"]')) {
            e.preventDefault();
            conversationId = String($(e.currentTarget).data('conversationId'));
        } else {
            conversationId = $(e.currentTarget).val();
        }

        if (conversationId && String(conversationId) !== String(this.conversationId)) {
            this.loadConversation(conversationId);
        }
    },

    getConversationTitle: function () {
        if (this.conversationId) {
            return this.getConversationTitleById(this.conversationId);
        }

        return this.defaultConversationTitle;
    },

    getConversationTitleById: function (conversationId) {
        const resolvedConversationId = String(conversationId);
        const conversationItem = this.$('[data-role="conversation-item"]').filter(function () {
            return String($(this).data('conversationId')) === resolvedConversationId;
        }).first();
        if (conversationItem.length > 0) {
            return conversationItem.find('[data-role="conversation-link-label"]').text().trim();
        }

        if (this.$conversationSelect.length > 0) {
            return this.$conversationSelect.find(`option[value="${resolvedConversationId}"]`).text().trim();
        }

        return this.defaultConversationTitle;
    },

    updateConversationEntryTitle: function (conversationId, newTitle) {
        const resolvedConversationId = String(conversationId);
        this.$('[data-role="conversation-link"]').filter(function () {
            return String($(this).data('conversationId')) === resolvedConversationId;
        }).find('[data-role="conversation-link-label"]').text(newTitle);

        if (this.$conversationSelect.length > 0) {
            this.$conversationSelect.find(`option[value="${resolvedConversationId}"]`).text(newTitle);
        }
    },

    updateConversationTitle: function (newTitle) {
        const conversationId = String(this.conversationId);
        this.updateConversationEntryTitle(conversationId, newTitle);

        this.$('[data-role="current-conversation-title"]').text(newTitle);
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
        }, 4000);
    },

    loadChangeHistory: function () {
        if (!this.conversationId || this.$changeHistoryList.length === 0) {
            return;
        }

        const self = this;
        erie_server().exec_server_get(
            `/api/conversation/${this.conversationId}/changes/`,
            (response) => {
                self.changes = response.changes || [];
                self.renderChangeHistory();
            }
        );
    },

    renderChangeHistory: function () {
        this.$changeCount.text(this.changes.length);

        if (this.changes.length === 0) {
            this.$changeHistoryList.html(
                '<div class="text-center py-3 text-muted"><small>No changes yet</small></div>'
            );
            return;
        }

        const self = this;
        this.$changeHistoryList.empty();
        this.changes.forEach(function (change) {
            self.$changeHistoryList.append(self.buildChangeHistoryCard(change));
        });
    },

    buildChangeHistoryCard: function (change) {
        const $card = $('<div>').addClass('change-history-card');
        const statusClass = change.applied ? 'applied' : (change.approved ? 'approved' : 'pending');
        $card.addClass('status-' + statusClass);

        const $header = $('<div>').addClass('change-card-header');
        const $typeAndStatus = $('<div>').addClass('d-flex justify-content-between align-items-start mb-2');
        const $type = $('<div>')
            .addClass('change-type')
            .html(`<i class="bi ${this.getChangeTypeIcon(change.change_type)}"></i> ${this.formatChangeType(change.change_type)}`);
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
        $header.append($('<div>').addClass('change-description').text(change.change_description));
        $card.append($header);

        if (change.change_details && Object.keys(change.change_details).length > 0) {
            $card.append(
                $('<details>')
                    .addClass('change-details mt-2')
                    .append($('<summary>').addClass('small').text('View technical details'))
                    .append($('<pre>').addClass('small mt-1').text(JSON.stringify(change.change_details, null, 2)))
            );
        }

        return $card;
    },

    getChangeTypeIcon: function (changeType) {
        const icons = {
            business_plan: 'bi-file-text',
            architecture: 'bi-diagram-3',
            infrastructure: 'bi-server',
            initiative: 'bi-flag',
            task: 'bi-check2-square',
            workflow: 'bi-diagram-2'
        };
        return icons[changeType] || 'bi-file-text';
    },

    formatChangeType: function (changeType) {
        const labels = {
            business_plan: 'Business Plan',
            architecture: 'Architecture',
            infrastructure: 'Infrastructure',
            initiative: 'New Initiative',
            task: 'Task',
            workflow: 'Workflow'
        };
        return labels[changeType] || changeType;
    },

    handleToggleChangeHistory: function () {
        const $chevron = this.$('[data-role="chevron"]');
        const isExpanded = $('#change-history-content').hasClass('show');
        if (isExpanded) {
            $chevron.removeClass('bi-chevron-up').addClass('bi-chevron-down');
        } else {
            $chevron.removeClass('bi-chevron-down').addClass('bi-chevron-up');
        }
    },

    scrollToEnd: function () {
        const self = this;
        setTimeout(function () {
            if (self.$messagesContainer.length > 0) {
                self.$messagesContainer.scrollTop(self.$messagesContainer[0].scrollHeight + 200);
            }
        }, 50);
    }
});
