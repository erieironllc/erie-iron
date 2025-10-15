CodeFileView = ErieView.extend({
    el: 'body',

    events: {
        'change .version-checkbox': 'handleCheckboxChange'
    },

    init_view: function (options) {
        const $container = $('.codefile-view');
        this.codefileId = $container.data('codefile-id');
        this.codeVersions = this.parseCodeVersionsData($container.data('code-versions'));
        this.initializeView();
        
        (get_querystring_params()["v"] || "").split(",").forEach((v)=>{
            const value = String(v).trim();
            if (!value) return;

            $(`.version-checkbox[data-version-number="${CSS.escape ? CSS.escape(value) : value}"]`).click();
        });
    },

    parseCodeVersionsData: function (dataString) {
        const versions = {};
        if (!dataString) return versions;

        const versionEntries = dataString.split(';');
        versionEntries.forEach(entry => {
            const parts = entry.split(':');
            if (parts.length >= 4) {
                const id = parts[0];
                const versionNumber = parseInt(parts[1]);
                const iterationId = parts[2] === 'null' ? null : parts[2];
                const createdAt = parts.slice(3).join(':'); // In case time has colons

                versions[id] = {
                    versionNumber: versionNumber,
                    iterationId: iterationId,
                    createdAt: createdAt
                };
            }
        });

        return versions;
    },

    initializeView: function () {
        // Show latest version by default
        this.updateRightPane();
    },

    handleCheckboxChange: function (ev) {
        this.updateRightPane();
        return last_stop(ev);
    },

    updateRightPane: function () {
        const checkedBoxes = $('.version-checkbox:checked');
        const selectedVersionIds = checkedBoxes.map((i, cb) => $(cb).val()).get();

        const rightPaneTitle = $('#right-pane-title');
        const codeContent = $('#code-content');
        const codeDisplay = $('#code-display');

        // Show loading state
        rightPaneTitle.text('Loading...');
        codeContent.text('');

        erie_server().exec_server_post(
            $("#codefile-view"),
            {
                "versions": selectedVersionIds
            },
            (response) => {
                if (response.error) {
                    rightPaneTitle.text('Error');
                    codeContent.text(response.error);
                } else {
                    rightPaneTitle.text(response.title);

                    if (response.content_type === 'diff') {
                        // For diffs, render as HTML
                        codeContent.html(response.content);
                        codeDisplay.addClass('diff-display');
                    } else {
                        // For code, render as text
                        codeContent.text(response.content);
                        codeDisplay.removeClass('diff-display');
                    }
                }
            },
            () => {
                rightPaneTitle.text('Error');
                codeContent.text('Failed to load content from server.');
            }
        )
    },

    copyToClipboard: function () {
        const codeContent = $('#code-content').text();
        if (navigator.clipboard) {
            navigator.clipboard.writeText(codeContent).then(() => {
                this.showCopyFeedback();
            });
        }
    },

    showCopyFeedback: function () {
        const btn = $('.copy-btn');
        const originalText = btn.text();
        btn.text('Copied!');
        btn.removeClass('btn-outline-secondary').addClass('btn-success');
        setTimeout(() => {
            btn.text(originalText);
            btn.removeClass('btn-success').addClass('btn-outline-secondary');
        }, 1000);
    }
});