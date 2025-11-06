CloudAccountsView = ErieView.extend({
    el: '#cloud-accounts-root',
    
    events: {
        'click [data-action="add-account"]': 'openCreateModal',
        'click [data-action="edit-account"]': 'handleEditClick',
        'click [data-action="delete-account"]': 'handleDeleteClick',
        'submit #cloud-account-form': 'submitForm',
        'click #cloud-account-delete-confirm': 'performDelete',
        'change #cloud-account-rotate': 'handleRotateToggle'
    },

    init_view: function () {
        this.$form = this.$('#cloud-account-form');
        this.$providerSelect = this.$('#cloud-account-provider');
        this.$credentialFields = this.$('[data-role="credential-fields"] input');
        this.$rotateToggleWrapper = this.$('#cloud-account-rotate-toggle');
        this.$rotateCheckbox = this.$('#cloud-account-rotate');
        this.$submitBtn = this.$('[data-role="submit-btn"]');
        this.apiRoot = this.$el.data('apiRoot');

        const modalEl = document.getElementById('cloud-account-modal');
        this.formModal = modalEl ? new bootstrap.Modal(modalEl) : null;
        const deleteModalEl = document.getElementById('cloud-account-delete-modal');
        this.deleteModal = deleteModalEl ? new bootstrap.Modal(deleteModalEl) : null;

        this.accounts = this._safeParse(this.$el.attr('data-accounts'));
        this.providerChoices = this._safeParse(this.$el.attr('data-provider-choices'));
        this.currentAccountId = null;
        this.deleteAccountId = null;

    },

    refresh: function () {
        this.renderTable();
    },

    _safeParse: function (raw) {
        if (!raw) {
            return [];
        }
        try {
            return JSON.parse(raw);
        } catch (err) {
            console.warn('Unable to parse JSON payload', err);
            return [];
        }
    },

    openCreateModal: function () {
        this.currentAccountId = null;
        this.isEdit = false;
        this.$form[0].reset();
        this._populateProviderChoices();
        this.$providerSelect.prop('disabled', this.providerChoices.length <= 1);
        this.$('#cloud-account-session-duration').val(3600);
        this.$('#cloud-account-default-dev').prop('checked', true);
        this.$('#cloud-account-default-prod').prop('checked', false);
        this._toggleRotateControls(true, { hideToggle: true });
        this._setModalTitle('Add Cloud Account');
        this.$submitBtn.text('Add Cloud Account');
        if (this.formModal) {
            this.formModal.show();
        }
    },

    handleEditClick: function (event) {
        event.preventDefault();
        const id = String($(event.currentTarget).data('accountId'));
        this.openEditModal(id);
    },

    openEditModal: function (accountId) {
        const account = this._findAccount(accountId);
        if (!account) {
            console.error('Unable to locate the selected cloud account.');
            return;
        }

        this.currentAccountId = accountId;
        this.isEdit = true;
        this.$form[0].reset();
        this._populateProviderChoices();
        this.$providerSelect.val(account.provider);
        this.$providerSelect.prop('disabled', true);
        this.$('#cloud-account-name').val(account.name);
        this.$('#cloud-account-identifier').val(account.account_identifier || '');
        this.$('#cloud-account-region').val(account.metadata && account.metadata.region ? account.metadata.region : '');
        this.$('#cloud-account-default-dev').prop('checked', Boolean(account.is_default_dev));
        this.$('#cloud-account-default-prod').prop('checked', Boolean(account.is_default_production));
        this.$('#cloud-account-role-arn').val('');
        this.$('#cloud-account-external-id').val('');
        this.$('#cloud-account-session-duration').val(3600);
        this._toggleRotateControls(false, { showToggle: true });
        this._setModalTitle('Edit Cloud Account');
        this.$submitBtn.text('Save Changes');
        if (this.formModal) {
            this.formModal.show();
        }
    },

    handleDeleteClick: function (event) {
        event.preventDefault();
        this.deleteAccountId = String($(event.currentTarget).data('accountId'));
        if (this.deleteModal) {
            this.deleteModal.show();
        }
    },

    handleRotateToggle: function () {
        const enabled = this.$rotateCheckbox.is(':checked');
        this._toggleRotateControls(enabled, { showToggle: true });
    },

    _toggleRotateControls: function (credentialsEnabled, options) {
        const hideToggle = options && options.hideToggle;
        if (hideToggle) {
            this.$rotateToggleWrapper.addClass('d-none');
            this.$rotateCheckbox.prop('checked', true);
        } else {
            this.$rotateToggleWrapper.removeClass('d-none');
            this.$rotateCheckbox.prop('checked', credentialsEnabled);
        }

        const disableFields = this.isEdit && !credentialsEnabled;
        this.$credentialFields.each(function () {
            const input = $(this);
            if (disableFields) {
                input.data('was-required', input.prop('required'));
                input.prop('required', false);
            } else {
                const wasRequired = input.data('was-required');
                if (typeof wasRequired !== 'undefined') {
                    input.prop('required', Boolean(wasRequired));
                }
            }
            input.prop('disabled', disableFields);
        });

        if (!this.isEdit || credentialsEnabled || hideToggle) {
            this.$('#cloud-account-role-arn').prop('required', true);
        }
    },

    _populateProviderChoices: function () {
        const select = this.$providerSelect;
        if (!select.length) {
            return;
        }
        select.empty();
        const choices = this.providerChoices && this.providerChoices.length > 0 ? this.providerChoices : [{ value: 'aws', label: 'AWS' }];
        choices.forEach((choice) => {
            const option = $('<option></option>').val(choice.value).text(choice.label);
            select.append(option);
        });
        if (!select.val()) {
            select.val('aws');
        }
    },

    submitForm: function (event) {
        event.preventDefault();
        const payload = this._buildPayload();
        if (!payload) {
            return;
        }

        const isEdit = Boolean(this.currentAccountId);
        const url = isEdit ? `${this.apiRoot}/${this.currentAccountId}` : `${this.apiRoot}/create`;

        this._setFormLoading(true);
        $.ajax({
            url: url,
            method: 'POST',
            data: JSON.stringify(payload),
            contentType: 'application/json',
            headers: {
                'X-CSRFToken': getCSRFToken()
            }
        }).always(() => {
            window.location.reload();
        });
    },

    _buildPayload: function () {
        const name = this.$('#cloud-account-name').val().trim();
        if (!name) {
            console.error('Name is required.');
            return null;
        }
        const provider = this.$providerSelect.val() || 'aws';
        const payload = {
            name: name,
            provider: provider,
            account_identifier: this.$('#cloud-account-identifier').val().trim() || null,
            region: this.$('#cloud-account-region').val().trim() || null,
            is_default_dev: this.$('#cloud-account-default-dev').is(':checked'),
            is_default_production: this.$('#cloud-account-default-prod').is(':checked')
        };

        const shouldIncludeCredentials = !this.isEdit || this.$rotateCheckbox.is(':checked');
        if (shouldIncludeCredentials) {
            const roleArn = this.$('#cloud-account-role-arn').val().trim();
            if (!roleArn) {
                console.error('Role ARN is required.');
                return null;
            }
            const sessionDurationRaw = this.$('#cloud-account-session-duration').val();
            let sessionDuration = parseInt(sessionDurationRaw, 10);
            if (Number.isNaN(sessionDuration)) {
                sessionDuration = 3600;
            }
            if (sessionDuration < 900 || sessionDuration > 43200) {
                console.error('Session duration must be between 900 and 43,200 seconds.');
                return null;
            }
            payload.credentials = {
                role_arn: roleArn,
                external_id: this.$('#cloud-account-external-id').val().trim() || null,
                session_duration: sessionDuration
            };
        }
        return payload;
    },

    performDelete: function () {
        if (!this.deleteAccountId) {
            return;
        }
        const url = `${this.apiRoot}/${this.deleteAccountId}/delete`;
        $.ajax({
            url: url,
            method: 'POST',
            headers: {
                'X-CSRFToken': getCSRFToken()
            }
        }).done(() => {
            if (this.deleteModal) {
                this.deleteModal.hide();
            }
            window.location.reload();
        }).fail((xhr) => {
            const error = this._extractError(xhr) || 'Unable to delete the cloud account.';
            console.error(error);
        }).always(() => {
            this.deleteAccountId = null;
        });
    },

    _setFormLoading: function (isLoading) {
        if (!this.$submitBtn.length) {
            return;
        }
        if (isLoading) {
            this.$submitBtn.data('original-text', this.$submitBtn.text());
            this.$submitBtn.prop('disabled', true).text('Saving...');
        } else {
            const original = this.$submitBtn.data('original-text');
            if (original) {
                this.$submitBtn.text(original);
            }
            this.$submitBtn.prop('disabled', false);
        }
    },

    _setModalTitle: function (title) {
        this.$('#cloudAccountModalLabel').text(title);
    },

    _findAccount: function (accountId) {
        accountId = String(accountId);
        return _.find(this.accounts, (item) => String(item.id) === accountId) || null;
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
