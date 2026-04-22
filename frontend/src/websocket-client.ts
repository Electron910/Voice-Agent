type MessageHandler = (data: any) => void;
type AudioHandler = (audio: ArrayBuffer) => void;

export class WebSocketClient {
  private ws: WebSocket | null = null;
  private url: string;
  private messageHandlers: Map<string, MessageHandler[]> = new Map();
  private audioHandler: AudioHandler | null = null;
  private reconnectAttempts = 0;
  private maxReconnectAttempts = 5;
  private sessionId: string;

  constructor(baseUrl: string, sessionId: string) {
    this.url = `${baseUrl}/ws/${sessionId}`;
    this.sessionId = sessionId;
  }

  connect(): Promise<void> {
    return new Promise((resolve, reject) => {
      this.ws = new WebSocket(this.url);
      this.ws.binaryType = "arraybuffer";

      this.ws.onopen = () => {
        this.reconnectAttempts = 0;
        resolve();
      };

      this.ws.onmessage = (event: MessageEvent) => {
        if (event.data instanceof ArrayBuffer) {
          if (this.audioHandler) {
            this.audioHandler(event.data);
          }
          return;
        }

        try {
          const data = JSON.parse(event.data);
          const type = data.type || "unknown";
          const handlers = this.messageHandlers.get(type) || [];
          handlers.forEach((h) => h(data));

          const allHandlers = this.messageHandlers.get("*") || [];
          allHandlers.forEach((h) => h(data));
        } catch (e) {
          console.error("Failed to parse message:", e);
        }
      };

      this.ws.onclose = () => {
        this.attemptReconnect();
      };

      this.ws.onerror = (error) => {
        reject(error);
      };
    });
  }

  on(type: string, handler: MessageHandler): void {
    if (!this.messageHandlers.has(type)) {
      this.messageHandlers.set(type, []);
    }
    this.messageHandlers.get(type)!.push(handler);
  }

  onAudio(handler: AudioHandler): void {
    this.audioHandler = handler;
  }

  sendJSON(data: object): void {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(data));
    }
  }

  sendAudio(audioData: ArrayBuffer): void {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(audioData);
    }
  }

  sendInterrupt(): void {
    this.sendJSON({ type: "interrupt" });
  }

  disconnect(): void {
    this.maxReconnectAttempts = 0;
    if (this.ws) {
      this.ws.close();
      this.ws = null;
    }
  }

  private attemptReconnect(): void {
    if (this.reconnectAttempts >= this.maxReconnectAttempts) return;
    this.reconnectAttempts++;
    const delay = Math.min(1000 * Math.pow(2, this.reconnectAttempts), 10000);
    setTimeout(() => this.connect().catch(() => {}), delay);
  }

  get isConnected(): boolean {
    return this.ws !== null && this.ws.readyState === WebSocket.OPEN;
  }
}