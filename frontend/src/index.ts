import { WebSocketClient } from "./websocket-client";
import { AudioCapture } from "./audio-capture";
import { AudioPlayer } from "./audio-player";
import { UIManager } from "./ui";

const WS_URL = "ws://localhost:8000";
const SESSION_ID = crypto.randomUUID();

let wsClient: WebSocketClient;
let audioCapture: AudioCapture;
let audioPlayer: AudioPlayer;
let ui: UIManager;
let isRecording = false;

async function initialize(): Promise<void> {
  ui = new UIManager();
  audioCapture = new AudioCapture();
  audioPlayer = new AudioPlayer();
  wsClient = new WebSocketClient(WS_URL, SESSION_ID);

  setupEventHandlers();
  setupButtonHandlers();

  try {
    await wsClient.connect();
    ui.setStatus("Connected");
    ui.addLog("WebSocket connected");
  } catch (e) {
    ui.setStatus("Connection Failed", "#f44336");
    ui.addLog(`Connection failed: ${e}`);
  }
}

function setupEventHandlers(): void {
  wsClient.on("connected", (data) => {
    ui.addLog(`Session: ${data.session_id}`);
  });

  wsClient.on("initialized", (data) => {
    ui.addLog(`Initialized with language: ${data.language}`);
  });

  wsClient.on("transcript", (data) => {
    ui.showTranscript(data.text, data.language);
    ui.addLog(`Transcribed: "${data.text}" [${data.language}]`);
  });

  wsClient.on("response", (data) => {
    ui.showResponse(data);
    ui.showReasoning(data);
    ui.addLog(`Agent: ${data.text.substring(0, 80)}...`);
  });

  wsClient.on("latency", (data) => {
    ui.showLatency(data);
  });

  wsClient.on("error", (data) => {
    ui.addLog(`Error: ${data.message}`);
    ui.setStatus("Error", "#f44336");
  });

  wsClient.onAudio((audioData) => {
    audioPlayer.queueAudio(audioData);
  });
}

function setupButtonHandlers(): void {
  const recordBtn = document.getElementById("recordBtn") as HTMLButtonElement;
  const stopBtn = document.getElementById("stopBtn") as HTMLButtonElement;
  const interruptBtn = document.getElementById("interruptBtn") as HTMLButtonElement;
  const textInput = document.getElementById("textInput") as HTMLInputElement;
  const sendBtn = document.getElementById("sendBtn") as HTMLButtonElement;
  const initBtn = document.getElementById("initBtn") as HTMLButtonElement;

  initBtn.addEventListener("click", () => {
    const patientId = (document.getElementById("patientId") as HTMLInputElement).value;
    const language = (document.getElementById("language") as HTMLSelectElement).value;
    wsClient.sendJSON({
      type: "init",
      patient_id: patientId || SESSION_ID,
      language: language,
    });
    ui.addLog(`Initialized: patient=${patientId}, lang=${language}`);
  });

  recordBtn.addEventListener("click", async () => {
    if (isRecording) return;
    isRecording = true;
    recordBtn.disabled = true;
    stopBtn.disabled = false;
    ui.setStatus("Recording...", "#f44336");

    await audioCapture.start((audioData) => {
      wsClient.sendAudio(audioData);
    });
  });

  stopBtn.addEventListener("click", () => {
    if (!isRecording) return;
    isRecording = false;
    audioCapture.stop();
    recordBtn.disabled = false;
    stopBtn.disabled = true;
    ui.setStatus("Processing...", "#FF9800");
    wsClient.sendJSON({ type: "speech_end" });
  });

  interruptBtn.addEventListener("click", () => {
    wsClient.sendInterrupt();
    audioPlayer.stop();
    ui.addLog("Interrupted agent response");
  });

  sendBtn.addEventListener("click", () => {
    const text = textInput.value.trim();
    if (text) {
      wsClient.sendJSON({ type: "text", content: text });
      ui.showTranscript(text, "typed");
      textInput.value = "";
    }
  });

  textInput.addEventListener("keypress", (e) => {
    if (e.key === "Enter") {
      sendBtn.click();
    }
  });
}

document.addEventListener("DOMContentLoaded", initialize);