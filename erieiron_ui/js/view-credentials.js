CredentialsView = ErieView.extend({
    el: '#credentials-root',

    events: {
        'click [data-action="edit-credential"]': 'handleEditClick',
        'click [data-action="delete-credential"]': 'handleDeleteClick',
        'click #save-credential-btn': 'submitForm'
    },

    init_view: function () {
        this.entityId = this.$el.data('entityId');
        this.entityType = this.$el.data('entityType'); // 'business' or 'stack'

        const modalEl = document.getElementById('edit-credential-modal');
        this.editModal = modalEl ? new bootstrap.Modal(modalEl) : null;
    },

    _getBaseUrl: function () {
        return `/${this.entityType}/${this.entityId}`;
    },

    handleEditClick: function (event) {
        event.preventDefault();
        const $btn = $(event.currentTarget);
        const service = $btn.data('service');
        const serviceName = $btn.data('serviceName');
        this.openEditModal(service, serviceName);
    },

    openEditModal: function (service, serviceName) {
        this.$('#edit-credential-service').val(service);
        this.$('#edit-service-name').text(serviceName);

        // Fetch secret details
        this._setFormLoading(true);
        erie_server().exec_server_get(
            `${this._getBaseUrl()}/credentials/${service}/secret/`,
            (response) => {
                this._populateModalWithSecret(response);
                this._setFormLoading(false);
            },
            (xhr) => {
                const error = this._extractError(xhr) || 'Failed to fetch secret details';
                alert(`Error: ${error}`);
                this._setFormLoading(false);
            }
        );

        if (this.editModal) {
            this.editModal.show();
        }
    },

    _populateModalWithSecret: function (data) {
        // Set ARN
        const arn = data.effective_arn || data.suggested_arn || '';
        this.$('#edit-arn-input').val(arn);

        // Show suggestion if ARN not set at current level
        if (data.suggested_arn && !data.effective_arn) {
            this.$('#edit-arn-suggestion').text(`Suggested: ${data.suggested_arn}`).addClass('text-info');
        } else {
            this.$('#edit-arn-suggestion').text('Full ARN of the secret in AWS Secrets Manager').removeClass('text-info');
        }

        // Render secret fields
        const container = this.$('#secret-fields-container');
        container.empty();

        const schema = data.schema || [];
        const secretValues = data.secret_values || {};

        schema.forEach((field) => {
            const fieldGroup = $('<div class="mb-3"></div>');

            const label = $(`<label class="form-label"></label>`);
            label.text(`${field.key}${field.required ? ' *' : ''}`);
            fieldGroup.append(label);

            const input = $(`<input type="text" class="form-control" />`);
            input.attr('id', `secret-field-${field.key}`);
            input.attr('data-secret-key', field.key);
            input.val(secretValues[field.key] || '');
            input.attr('placeholder', field.description);

            fieldGroup.append(input);

            // const helpText = $('<div class="form-text"></div>');
            // helpText.text(field.description);
            // fieldGroup.append(helpText);

            container.append(fieldGroup);
        });
    },

    handleDeleteClick: function (event) {
        event.preventDefault();
        const service = $(event.currentTarget).data('service');

        const entityLabel = this.entityType === 'business' ? 'business' : 'stack';
        if (!confirm(`Remove this credential ARN from ${entityLabel}? It will fall back to inherited defaults.`)) {
            return;
        }

        this.deleteCredential(service);
    },

    deleteCredential: function (service) {
        erie_server().exec_server_post(
            `${this._getBaseUrl()}/credentials/delete/`,
            {
                credential_service: service
            },
            () => {
                // Reload page to refresh the table
                window.location.reload();
            },
            (xhr) => {
                const error = this._extractError(xhr) || 'Failed to delete credential';
                alert(`Error: ${error}`);
            }
        )
    },

    submitForm: function (event) {
        event.preventDefault();

        const service = this.$('#edit-credential-service').val();
        const arn = this.$('#edit-arn-input').val().trim();

        if (!arn) {
            alert('ARN is required');
            return;
        }

        // Collect secret values
        const data = {
            arn: arn
        };
        
        this.$('[data-secret-key]').each((idx, el) => {
            const $el = $(el);
            const key = $(el).attr('data-secret-key');
            data[`secret--${key}`] = $(el).val()
        });

        this._setFormLoading(true);

        erie_server().exec_server_post(
            `${this._getBaseUrl()}/credentials/${service}/secret/update/`,
            data,
            () => {
                if (this.editModal) {
                    this.editModal.hide();
                }
                // Reload page to refresh the table
                window.location.reload();
            },
            (xhr) => {
                const error = this._extractError(xhr) || 'Failed to update credential';
                alert(`Error: ${error}`);
                this._setFormLoading(false);
            }
        )
    },

    _setFormLoading: function (isLoading) {
        const btn = this.$('#save-credential-btn');
        if (!btn.length) {
            return;
        }

        if (isLoading) {
            btn.data('original-text', btn.text());
            btn.prop('disabled', true).text('Saving...');
        } else {
            const original = btn.data('original-text');
            if (original) {
                btn.text(original);
            }
            btn.prop('disabled', false);
        }
    },

    _extractError: function (xhr) {
        if (!xhr) {
            return null;
        }
        if (xhr.responseJSON && xhr.responseJSON.error) {
            return xhr.responseJSON.error;
        }
        if (xhr.responseText) {
            try {
                const parsed = JSON.parse(xhr.responseText);
                if (parsed.error) {
                    return parsed.error;
                }
            } catch (err) {
                return xhr.responseText;
            }
        }
        return null;
    }
});
