RootChatView = ConversationViewBase.extend({
    el: '#root-chat-root',

    init_view: function () {
        this.asyncClientEventType = 'root_conversation_response';
        this.messageStatusUrlTemplate = this.$el.data('messageStatusUrlTemplate');
        this.websocketEnabled = !!this.$el.data('websocketEnabled');
        this.clearAsyncResponsePolling();
        this.pendingAsyncResponse = null;
        this.websocketConnected = false;

        if (!this._boundAsyncResponseHandler) {
            this._boundAsyncResponseHandler = this.handleAsyncLlmResponse.bind(this);
        }
        $('body')
            .off('llm_response_ready.rootChat')
            .on('llm_response_ready.rootChat', this._boundAsyncResponseHandler);

        if (!this._registeredWebsocketHandlers) {
            this._boundWebsocketConnectedHandler = this.handleWebsocketConnected.bind(this);
            this._boundWebsocketDisconnectedHandler = this.handleWebsocketDisconnected.bind(this);
            erie_server()
                .on('websocket_connected', this._boundWebsocketConnectedHandler)
                .on('websocket_closed', this._boundWebsocketDisconnectedHandler)
                .on('websocket_error', this._boundWebsocketDisconnectedHandler);
            this._registeredWebsocketHandlers = true;
        }

        return ConversationViewBase.prototype.init_view.call(this);
    },

    handleWebsocketConnected: function () {
        this.websocketConnected = true;
    },

    handleWebsocketDisconnected: function () {
        this.websocketConnected = false;
        this.startAsyncResponsePolling();
    },

    clearAsyncResponsePolling: function () {
        if (this.asyncResponseFallbackTimer) {
            clearTimeout(this.asyncResponseFallbackTimer);
            this.asyncResponseFallbackTimer = null;
        }

        if (this.asyncResponsePollTimer) {
            clearTimeout(this.asyncResponsePollTimer);
            this.asyncResponsePollTimer = null;
        }
    },

    clearPendingAsyncResponse: function (conversationId) {
        if (
            conversationId &&
            this.pendingAsyncResponse &&
            String(this.pendingAsyncResponse.conversationId) !== String(conversationId)
        ) {
            return;
        }

        this.clearAsyncResponsePolling();
        this.pendingAsyncResponse = null;
    },

    buildAsyncStatusUrl: function (pendingAsyncResponse) {
        return this.resolveConversationUrl(
            this.messageStatusUrlTemplate,
            pendingAsyncResponse.conversationId
        )
            .replace('__QUEUED_MESSAGE_ID__', encodeURIComponent(pendingAsyncResponse.queuedMessageId))
            .replace('__USER_MESSAGE_ID__', encodeURIComponent(pendingAsyncResponse.userMessageId));
    },

    trackPendingAsyncResponse: function (response, conversationId) {
        this.clearPendingAsyncResponse();
        this.pendingAsyncResponse = {
            conversationId: String(conversationId),
            queuedMessageId: String(response.queued_message_id),
            userMessageId: String(response.user_message.id)
        };

        if (!this.websocketEnabled || !this.websocketConnected) {
            this.startAsyncResponsePolling();
            return;
        }

        this.asyncResponseFallbackTimer = setTimeout(() => {
            this.startAsyncResponsePolling();
        }, 3000);
    },

    startAsyncResponsePolling: function () {
        if (!this.pendingAsyncResponse || !this.messageStatusUrlTemplate) {
            return;
        }

        this.clearAsyncResponsePolling();
        this.pollAsyncResponse();
    },

    pollAsyncResponse: function () {
        if (!this.pendingAsyncResponse) {
            return;
        }

        const self = this;
        const statusUrl = this.buildAsyncStatusUrl(this.pendingAsyncResponse);
        erie_server().exec_server_get(
            statusUrl,
            (response) => {
                if (!self.pendingAsyncResponse) {
                    return;
                }

                if (response.status === 'processing') {
                    self.asyncResponsePollTimer = setTimeout(() => {
                        self.pollAsyncResponse();
                    }, 1000);
                    return;
                }

                if (response.payload) {
                    self.handleAsyncLlmResponse(null, response.payload);
                    return;
                }

                self.asyncResponsePollTimer = setTimeout(() => {
                    self.pollAsyncResponse();
                }, 1000);
            },
            () => {
                if (!self.pendingAsyncResponse) {
                    return;
                }

                self.asyncResponsePollTimer = setTimeout(() => {
                    self.pollAsyncResponse();
                }, 2000);
            }
        );
    },

    hasRenderedMessage: function (messageId) {
        if (!messageId) {
            return false;
        }

        return this.$messagesContainer.find(`[data-message-id="${messageId}"]`).length > 0;
    },

    sendMessage: function (message) {
        const self = this;
        const conversationId = String(this.conversationId);

        this.setComposerEnabled(false);
        this.addMessageToUI({
            role: 'user',
            content: message,
            created_at: new Date().toISOString()
        });
        this.$messageInput.val('');
        this.showLoadingIndicator();

        erie_server().exec_server_post(
            this.resolveConversationUrl(this.messageUrlTemplate, conversationId),
            {message: message},
            (response) => {
                if (response.conversation_title) {
                    self.updateConversationEntryTitle(conversationId, response.conversation_title);
                    if (conversationId === String(self.conversationId)) {
                        self.$('[data-role="current-conversation-title"]').text(response.conversation_title);
                    }
                }

                self.trackPendingAsyncResponse(response, conversationId);
                self.showStatus('Processing message...', 'info');
            },
            () => {
                self.clearPendingAsyncResponse();
                self.hideLoadingIndicator();
                self.showStatus('Failed to send message. Please try again.', 'danger');
                self.setComposerEnabled(true);
            }
        );
    },

    handleAsyncLlmResponse: function (_event, payload) {
        if (payload.client_event_type !== this.asyncClientEventType) {
            return;
        }

        const responseConversationId = String(payload.conversation_id);
        this.clearPendingAsyncResponse(responseConversationId);

        if (payload.conversation_title) {
            this.updateConversationEntryTitle(responseConversationId, payload.conversation_title);
        }

        if (responseConversationId !== String(this.conversationId)) {
            return;
        }

        this.hideLoadingIndicator();

        if (payload.error) {
            this.showStatus('Failed to generate response. Please try again.', 'danger');
            this.setComposerEnabled(true);
            return;
        }

        if (payload.assistant_message) {
            if (!this.hasRenderedMessage(payload.assistant_message.id)) {
                this.addMessageToUI(payload.assistant_message);

                if (payload.assistant_message.changes && payload.assistant_message.changes.length > 0) {
                    payload.assistant_message.changes.forEach((change) => {
                        this.addChangeProposalToUI(change);
                    });
                }
            }
        }

        if (payload.conversation_title) {
            this.$('[data-role="current-conversation-title"]').text(payload.conversation_title);
        }

        this.setComposerEnabled(true);
        this.$messageInput.focus();
        this.loadChangeHistory();
    }
});
