export class UIManager {
  private statusEl: HTMLElement;
  private transcriptEl: HTMLElement;
  private responseEl: HTMLElement;
  private reasoningEl: HTMLElement;
  private latencyEl: HTMLElement;
  private logEl: HTMLElement;

  constructor() {
    this.statusEl = document.getElementById("status")!;
    this.transcriptEl = document.getElementById("transcript")!;
    this.responseEl = document.getElementById("response")!;
    this.reasoningEl = document.getElementById("reasoning")!;
    this.latencyEl = document.getElementById("latency")!;
    this.logEl = document.getElementById("log")!;
  }

  setStatus(status: string, color: string = "#4CAF50"): void {
    this.statusEl.textContent = status;
    this.statusEl.style.color = color;
  }

  showTranscript(text: string, language: string): void {
    const entry = document.createElement("div");
    entry.className = "transcript-entry user";
    entry.innerHTML = `<span class="label">You (${language}):</span> ${text}`;
    this.transcriptEl.appendChild(entry);
    this.transcriptEl.scrollTop = this.transcriptEl.scrollHeight;
  }

  showResponse(data: any): void {
    const entry = document.createElement("div");
    entry.className = "transcript-entry agent";
    entry.innerHTML = `<span class="label">Agent (${data.language}):</span> ${data.text}`;
    this.transcriptEl.appendChild(entry);
    this.transcriptEl.scrollTop = this.transcriptEl.scrollHeight;

    this.responseEl.textContent = data.text;
  }

  showReasoning(data: any): void {
    const content = [
      `Intent: ${data.intent}`,
      `Reasoning: ${data.reasoning}`,
      `State: ${data.conversation_state}`,
    ];

    if (data.tool_calls && data.tool_calls.length > 0) {
      content.push(`Tools: ${JSON.stringify(data.tool_calls, null, 2)}`);
    }
    if (data.tool_results && data.tool_results.length > 0) {
      content.push(`Results: ${JSON.stringify(data.tool_results, null, 2)}`);
    }

    this.reasoningEl.textContent = content.join("\n\n");
  }

  showLatency(data: any): void {
    const d = data.data || data;
    const lines = [
      `Total: ${d.total_ms}ms ${d.under_target ? "✅" : "❌"}`,
      `STT: ${d.stt_ms}ms`,
      `Agent: ${d.agent_ms}ms`,
      `Tools: ${d.tool_ms}ms`,
      `TTS First Byte: ${d.tts_first_byte_ms}ms`,
    ];
    this.latencyEl.textContent = lines.join("\n");

    if (!d.under_target && d.total_ms > 0) {
      this.latencyEl.style.color = "#f44336";
    } else {
      this.latencyEl.style.color = "#4CAF50";
    }
  }

  addLog(message: string): void {
    const entry = document.createElement("div");
    entry.className = "log-entry";
    const now = new Date().toLocaleTimeString();
    entry.textContent = `[${now}] ${message}`;
    this.logEl.appendChild(entry);
    this.logEl.scrollTop = this.logEl.scrollHeight;
  }
}