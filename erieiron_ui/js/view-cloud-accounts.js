CloudAccountsView = ErieView.extend({
    events: {
        'click [data-action="add-account"]': 'openCreateModal',
        'click [data-action="edit-account"]': 'handleEditClick',
        'click [data-action="delete-account"]': 'handleDeleteClick',
        'submit #cloud-account-form': 'submitForm',
        'click #cloud-account-delete-confirm': 'performDelete',
        'change #cloud-account-rotate': 'handleRotateToggle'
    },

    init_view: function () {
        this.$status = this.$('[data-role="status"]');
        this.$tableBody = this.$('[data-role="accounts-body"]');
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

        this._sortAccounts();
        this.renderTable();
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

    renderTable: function () {
        if (!this.accounts || this.accounts.length === 0) {
            this.$tableBody.html('<tr data-empty-row="true"><td colspan="7" class="text-center text-muted">No cloud accounts yet. Add one to define deployment credentials.</td></tr>');
            return;
        }

        const rows = this.accounts.map((account) => {
            const defaults = [];
            if (account.is_default_dev) {
                defaults.push('<span class="badge bg-success me-1">Dev</span>');
            }
            if (account.is_default_production) {
                defaults.push('<span class="badge bg-success">Prod</span>');
            }
            const defaultsHtml = defaults.length > 0 ? defaults.join(' ') : '<span class="text-muted">None</span>';
            const providerLabel = account.provider_label || account.provider;
            const accountId = account.account_identifier ? `<code>${_.escape(account.account_identifier)}</code>` : '<span class="text-muted">Not set</span>';
            const region = account.metadata && account.metadata.region ? `<code>${_.escape(account.metadata.region)}</code>` : '<span class="text-muted">Inherited</span>';
            const updatedDisplay = this._formatTimestamp(account.updated_at);
            const updatedTitle = account.updated_at ? _.escape(account.updated_at) : '';

            return `
                <tr data-account-id="${_.escape(account.id)}">
                    <td><strong>${_.escape(account.name)}</strong></td>
                    <td>${_.escape(providerLabel)}</td>
                    <td>${accountId}</td>
                    <td>${region}</td>
                    <td>${defaultsHtml}</td>
                    <td class="text-end"><span title="${updatedTitle}">${updatedDisplay}</span></td>
                    <td class="text-end">
                        <div class="btn-group btn-group-sm" role="group">
                            <button type="button" class="btn btn-outline-primary" data-action="edit-account" data-account-id="${_.escape(account.id)}">Edit</button>
                            <button type="button" class="btn btn-outline-danger" data-action="delete-account" data-account-id="${_.escape(account.id)}">Delete</button>
                        </div>
                    </td>
                </tr>
            `;
        }).join('');

        this.$tableBody.html(rows);
    },

    _formatTimestamp: function (value) {
        if (!value) {
            return '—';
        }
        try {
            const date = new Date(value);
            if (Number.isNaN(date.getTime())) {
                return _.escape(value);
            }
            return _.escape(date.toLocaleString());
        } catch (err) {
            return _.escape(String(value));
        }
    },

    openCreateModal: function () {
        this.currentAccountId = null;
        this.isEdit = false;
        this._clearStatus();
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
            this.showStatus('Unable to locate the selected cloud account.', 'danger');
            return;
        }

        this.currentAccountId = accountId;
        this.isEdit = true;
        this._clearStatus();
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
        this._clearStatus();
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
        }).done((data) => {
            if (this.formModal) {
                this.formModal.hide();
            }
            const message = isEdit ? 'Cloud account updated.' : 'Cloud account created.';
            this.reloadFromServer().always(() => {
                this.showStatus(message, 'success');
            });
        }).fail((xhr) => {
            const error = this._extractError(xhr) || 'Unable to save the cloud account.';
            this.showStatus(error, 'danger');
        }).always(() => {
            this._setFormLoading(false);
        });
    },

    _buildPayload: function () {
        const name = this.$('#cloud-account-name').val().trim();
        if (!name) {
            this.showStatus('Name is required.', 'danger');
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
                this.showStatus('Role ARN is required.', 'danger');
                return null;
            }
            const sessionDurationRaw = this.$('#cloud-account-session-duration').val();
            let sessionDuration = parseInt(sessionDurationRaw, 10);
            if (Number.isNaN(sessionDuration)) {
                sessionDuration = 3600;
            }
            if (sessionDuration < 900 || sessionDuration > 43200) {
                this.showStatus('Session duration must be between 900 and 43,200 seconds.', 'danger');
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
            this.reloadFromServer().always(() => {
                this.showStatus('Cloud account deleted.', 'success');
            });
        }).fail((xhr) => {
            const error = this._extractError(xhr) || 'Unable to delete the cloud account.';
            this.showStatus(error, 'danger');
        }).always(() => {
            this.deleteAccountId = null;
        });
    },

    reloadFromServer: function () {
        return $.getJSON(this.apiRoot)
            .done((data) => {
                this.accounts = data.accounts || [];
                if (data.provider_choices) {
                    this.providerChoices = data.provider_choices;
                }
                this._sortAccounts();
                this.renderTable();
            })
            .fail((xhr) => {
                const error = this._extractError(xhr) || 'Unable to refresh cloud accounts.';
                this.showStatus(error, 'danger');
            });
    },

    showStatus: function (message, variant) {
        const status = this.$status;
        if (!status.length) {
            return;
        }
        if (this._statusTimeout) {
            clearTimeout(this._statusTimeout);
            this._statusTimeout = null;
        }
        status.removeClass('d-none alert-info alert-success alert-danger');
        const className = variant === 'danger' ? 'alert-danger' : (variant === 'success' ? 'alert-success' : 'alert-info');
        status.addClass(className).text(message);
        this._statusTimeout = setTimeout(() => {
            this._clearStatus();
        }, 6000);
    },

    _clearStatus: function () {
        if (this._statusTimeout) {
            clearTimeout(this._statusTimeout);
            this._statusTimeout = null;
        }
        if (this.$status.length) {
            this.$status.addClass('d-none').removeClass('alert-info alert-success alert-danger').text('');
        }
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
    },

    _sortAccounts: function () {
        if (!Array.isArray(this.accounts)) {
            this.accounts = [];
            return;
        }
        this.accounts = _.sortBy(this.accounts, function (item) {
            return (item.name || '').toLowerCase();
        });
    }
});
