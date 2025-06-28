class ReconnectingWebSocket {
    constructor(url, options = {}) {
        this.url = url;
        this.protocols = [];
        this.ws = null;

        this.reconnectInterval = options.reconnectInterval || 1000; // ms
        this.maxReconnectInterval = options.maxReconnectInterval || 30000; // ms
        this.reconnectDecay = options.reconnectDecay || 1.5;
        this.maxReconnectAttempts = options.maxReconnectAttempts || Infinity;

        this.reconnectAttempts = 0;
        this.forcedClose = false;
        this.timedOut = false;

        this.onopen = null;
        this.onmessage = null;
        this.onclose = null;
        this.onerror = null;

        this.connect(false);
    }

    connect(reconnectAttempt) {
        this.ws = new WebSocket(this.url, this.protocols);

        if (reconnectAttempt) {
            this.reconnectAttempts++;
        }

        this.ws.onopen = (event) => {
            this.reconnectAttempts = 0;
            if (this.onopen) this.onopen(event);
        };

        this.ws.onmessage = (event) => {
            if (this.onmessage) this.onmessage(event);
        };

        this.ws.onclose = (event) => {
            this.ws = null;
            if (this.forcedClose) {
                if (this.onclose) this.onclose(event);
            } else {
                if (this.onclose) this.onclose(event);
                this.reconnect();
            }
        };

        this.ws.onerror = (event) => {
            if (this.onerror) this.onerror(event);
            // close the socket to trigger reconnection
            this.ws.close();
        };
    }

    reconnect() {
        if (this.reconnectAttempts < this.maxReconnectAttempts) {
            let timeout = this.reconnectInterval * Math.pow(this.reconnectDecay, this.reconnectAttempts);
            timeout = Math.min(timeout, this.maxReconnectInterval);

            console.debug(`WebSocket disconnected. Attempting to reconnect in ${timeout} ms`);

            setTimeout(() => {
                this.connect(true);
            }, timeout);
        } else {
            console.warn('Maximum reconnect attempts reached.');
        }
    }

    send(data) {
        if (this.ws && this.ws.readyState === WebSocket.OPEN) {
            this.ws.send(data);
        } else {
            console.error('WebSocket is not open. Ready state:', this.ws.readyState);
        }
    }

    close() {
        this.forcedClose = true;
        if (this.ws) {
            this.ws.close();
        }
    }
}