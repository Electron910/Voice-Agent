import structlog
from contextlib import asynccontextmanager
from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from backend.database import init_db
from backend.memory.memory_manager import memory_manager
from backend.api.websocket_handler import handle_websocket
from backend.api.rest_routes import router as rest_router
from backend.api.campaign_routes import router as campaign_router
from backend.agent.reasoning import reasoning_engine

structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.add_log_level,
        structlog.dev.ConsoleRenderer(),
    ],
)

logger = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("starting_application")
    await init_db()
    await memory_manager.initialize()
    await reasoning_engine.warmup()
    logger.info("application_ready")
    yield
    logger.info("shutting_down")
    await memory_manager.close()


app = FastAPI(
    title="VoiceAI Clinical Agent",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(rest_router)
app.include_router(campaign_router)

FRONTEND_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>VoiceAI Clinical Agent</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
            background: #0f1117;
            color: #e1e4e8;
            min-height: 100vh;
        }
        .container { max-width: 1400px; margin: 0 auto; padding: 20px; }
        h1 { color: #58a6ff; margin-bottom: 20px; font-size: 24px; }
        .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }
        @media (max-width: 768px) { .grid { grid-template-columns: 1fr; } }
        .panel {
            background: #161b22;
            border: 1px solid #30363d;
            border-radius: 8px;
            padding: 16px;
        }
        .panel h2 { color: #58a6ff; font-size: 14px; margin-bottom: 12px; text-transform: uppercase; letter-spacing: 1px; }
        .controls { display: flex; gap: 10px; margin-bottom: 20px; flex-wrap: wrap; align-items: center; }
        button {
            padding: 8px 16px;
            border: 1px solid #30363d;
            border-radius: 6px;
            background: #21262d;
            color: #e1e4e8;
            cursor: pointer;
            font-size: 14px;
            transition: all 0.2s;
        }
        button:hover { background: #30363d; }
        button:disabled { opacity: 0.4; cursor: not-allowed; }
        button.primary { background: #238636; border-color: #2ea043; }
        button.danger { background: #da3633; border-color: #f85149; }
        button.warning { background: #9e6a03; border-color: #d29922; }
        input, select {
            padding: 8px 12px;
            border: 1px solid #30363d;
            border-radius: 6px;
            background: #0d1117;
            color: #e1e4e8;
            font-size: 14px;
        }
        input:focus, select:focus { border-color: #58a6ff; outline: none; }
        #status { font-weight: bold; font-size: 14px; }
        .transcript-area {
            height: 300px;
            overflow-y: auto;
            border: 1px solid #30363d;
            border-radius: 6px;
            padding: 12px;
            background: #0d1117;
        }
        .transcript-entry { margin-bottom: 8px; padding: 8px; border-radius: 4px; font-size: 14px; line-height: 1.5; }
        .transcript-entry.user { background: #1c2333; border-left: 3px solid #58a6ff; }
        .transcript-entry.agent { background: #1c3321; border-left: 3px solid #3fb950; }
        .label { font-weight: bold; color: #8b949e; }
        pre {
            background: #0d1117;
            border: 1px solid #30363d;
            border-radius: 6px;
            padding: 12px;
            font-size: 12px;
            overflow: auto;
            max-height: 200px;
            white-space: pre-wrap;
            line-height: 1.5;
        }
        .log-area {
            height: 150px;
            overflow-y: auto;
            border: 1px solid #30363d;
            border-radius: 6px;
            padding: 8px;
            background: #0d1117;
            font-family: monospace;
            font-size: 12px;
        }
        .log-entry { color: #8b949e; margin-bottom: 2px; }
        .text-input-group { display: flex; gap: 8px; margin-top: 12px; }
        .text-input-group input { flex: 1; }
    </style>
</head>
<body>
    <div class="container">
        <h1>&#x1F3E5; VoiceAI Clinical Appointment Agent</h1>
        <div class="controls">
            <input id="patientId" placeholder="Patient ID (run /api/seed first)" style="width: 320px">
            <select id="language">
                <option value="en">English</option>
                <option value="hi">Hindi</option>
                <option value="ta">Tamil</option>
            </select>
            <button id="initBtn" class="primary">Initialize Session</button>
            <span id="status">Disconnected</span>
        </div>
        <div class="controls">
            <button id="recordBtn" class="primary">&#x1F3A4; Start Recording</button>
            <button id="stopBtn" class="danger" disabled>&#x23F9; Stop</button>
            <button id="interruptBtn" class="warning">&#x270B; Interrupt</button>
        </div>
        <div class="grid">
            <div class="panel">
                <h2>Conversation</h2>
                <div id="transcript" class="transcript-area"></div>
                <div class="text-input-group">
                    <input id="textInput" placeholder="Type a message...">
                    <button id="sendBtn">Send</button>
                </div>
            </div>
            <div class="panel">
                <h2>Agent Reasoning</h2>
                <pre id="reasoning">Waiting for interaction...</pre>
                <h2 style="margin-top:16px">Current Response</h2>
                <pre id="response">-</pre>
            </div>
            <div class="panel">
                <h2>Latency Breakdown</h2>
                <pre id="latency">No data yet</pre>
            </div>
            <div class="panel">
                <h2>System Log</h2>
                <div id="log" class="log-area"></div>
            </div>
        </div>
    </div>
    <script>
        var WS_PROTOCOL = location.protocol === 'https:' ? 'wss:' : 'ws:';
        var WS_URL = WS_PROTOCOL + '//' + location.host;
        var SESSION_ID = crypto.randomUUID();
        var ws = null;
        var audioCapture = null;
        var audioContext = null;
        var isRecording = false;

        var playbackContext = null;
        var audioQueue = [];
        var isPlaying = false;
        var currentSource = null;

        function getPlaybackContext() {
            if (!playbackContext || playbackContext.state === 'closed') {
                playbackContext = new AudioContext({ sampleRate: 16000 });
            }
            if (playbackContext.state === 'suspended') {
                playbackContext.resume();
            }
            return playbackContext;
        }

        function enqueueAudio(arrayBuffer) {
            audioQueue.push(arrayBuffer);
            if (!isPlaying) { playNextChunk(); }
        }

        function playNextChunk() {
            if (audioQueue.length === 0) {
                isPlaying = false;
                currentSource = null;
                return;
            }
            isPlaying = true;
            var ctx = getPlaybackContext();
            var chunk = audioQueue.shift();
            ctx.decodeAudioData(chunk.slice(0),
                function(audioBuffer) {
                    var source = ctx.createBufferSource();
                    source.buffer = audioBuffer;
                    source.connect(ctx.destination);
                    source.onended = playNextChunk;
                    currentSource = source;
                    source.start();
                    addLog('Playing audio (' + audioBuffer.duration.toFixed(2) + 's)');
                },
                function(err) {
                    addLog('Decode failed, trying raw PCM');
                    playRawPCM(ctx, chunk);
                }
            );
        }

        function playRawPCM(ctx, arrayBuffer) {
            var int16 = new Int16Array(arrayBuffer);
            var float32 = new Float32Array(int16.length);
            for (var i = 0; i < int16.length; i++) {
                float32[i] = int16[i] / 32768.0;
            }
            var audioBuffer = ctx.createBuffer(1, float32.length, 16000);
            audioBuffer.getChannelData(0).set(float32);
            var source = ctx.createBufferSource();
            source.buffer = audioBuffer;
            source.connect(ctx.destination);
            source.onended = playNextChunk;
            currentSource = source;
            source.start();
            addLog('Playing raw PCM (' + audioBuffer.duration.toFixed(2) + 's)');
        }

        function stopPlayback() {
            audioQueue = [];
            isPlaying = false;
            if (currentSource) {
                try { currentSource.stop(); } catch(e) {}
                currentSource = null;
            }
        }

        function addLog(msg) {
            var el = document.getElementById('log');
            var d = document.createElement('div');
            d.className = 'log-entry';
            d.textContent = '[' + new Date().toLocaleTimeString() + '] ' + msg;
            el.appendChild(d);
            el.scrollTop = el.scrollHeight;
        }

        function addTranscript(text, lang, type) {
            var el = document.getElementById('transcript');
            var d = document.createElement('div');
            d.className = 'transcript-entry ' + type;
            var label = type === 'user' ? 'You (' + lang + ')' : 'Agent (' + lang + ')';
            d.innerHTML = '<span class="label">' + label + ':</span> ' + text;
            el.appendChild(d);
            el.scrollTop = el.scrollHeight;
        }

        function connectWS() {
            ws = new WebSocket(WS_URL + '/ws/' + SESSION_ID);
            ws.binaryType = 'arraybuffer';

            ws.onopen = function() {
                document.getElementById('status').textContent = 'Connected';
                document.getElementById('status').style.color = '#4CAF50';
                addLog('WebSocket connected');
                getPlaybackContext();
            };

            ws.onmessage = function(event) {
                if (event.data instanceof ArrayBuffer) {
                    addLog('Received audio: ' + event.data.byteLength + ' bytes');
                    enqueueAudio(event.data);
                    return;
                }
                try {
                    var data = JSON.parse(event.data);

                    if (data.type === 'connected') addLog('Session: ' + data.session_id);
                    if (data.type === 'initialized') addLog('Language: ' + data.language);

                    if (data.type === 'transcript') {
                        addTranscript(data.text, data.language, 'user');
                        addLog('Heard: "' + data.text + '"');
                    }

                    if (data.type === 'response') {
                        addTranscript(data.text, data.language, 'agent');
                        document.getElementById('response').textContent = data.text;
                        var r = [];
                        r.push('Intent: ' + data.intent);
                        r.push('Reasoning: ' + (data.reasoning || ''));
                        r.push('State: ' + (data.conversation_state || ''));
                        if (data.tool_calls && data.tool_calls.length > 0)
                            r.push('Tools: ' + JSON.stringify(data.tool_calls, null, 2));
                        if (data.tool_results && data.tool_results.length > 0)
                            r.push('Results: ' + JSON.stringify(data.tool_results, null, 2));
                        document.getElementById('reasoning').textContent = r.join('\n\n');
                    }

                    if (data.type === 'audio' && data.data) {
                        var binary = atob(data.data);
                        var bytes = new Uint8Array(binary.length);
                        for (var i = 0; i < binary.length; i++) {
                            bytes[i] = binary.charCodeAt(i);
                        }
                        addLog('Received b64 audio: ' + bytes.byteLength + ' bytes');
                        enqueueAudio(bytes.buffer);
                    }

                    if (data.type === 'latency') {
                        var ld = data.data || data;
                        var lines = [
                            'Total: ' + ld.total_ms + 'ms ' + (ld.under_target ? ' OK' : ' SLOW'),
                            'STT: ' + ld.stt_ms + 'ms',
                            'Agent: ' + ld.agent_ms + 'ms',
                            'Tools: ' + ld.tool_ms + 'ms',
                            'TTS: ' + ld.tts_first_byte_ms + 'ms'
                        ];
                        document.getElementById('latency').textContent = lines.join('\n');
                        document.getElementById('latency').style.color = ld.under_target ? '#4CAF50' : '#f44336';
                    }

                    if (data.type === 'error') addLog('Error: ' + data.message);
                } catch(e) { console.error(e); }
            };

            ws.onclose = function() {
                document.getElementById('status').textContent = 'Disconnected';
                document.getElementById('status').style.color = '#f44336';
                addLog('Disconnected. Reconnecting...');
                setTimeout(connectWS, 3000);
            };
        }

        document.getElementById('initBtn').addEventListener('click', function() {
            getPlaybackContext();
            var pid = document.getElementById('patientId').value;
            var lang = document.getElementById('language').value;
            if (ws && ws.readyState === WebSocket.OPEN) {
                ws.send(JSON.stringify({type:'init', patient_id: pid || SESSION_ID, language: lang}));
                addLog('Initialized: patient=' + (pid || SESSION_ID) + ', lang=' + lang);
            }
        });

        document.getElementById('sendBtn').addEventListener('click', function() {
            var input = document.getElementById('textInput');
            var text = input.value.trim();
            if (text && ws && ws.readyState === WebSocket.OPEN) {
                ws.send(JSON.stringify({type:'text', content: text}));
                addTranscript(text, 'typed', 'user');
                input.value = '';
            }
        });

        document.getElementById('textInput').addEventListener('keypress', function(e) {
            if (e.key === 'Enter') document.getElementById('sendBtn').click();
        });

        document.getElementById('interruptBtn').addEventListener('click', function() {
            stopPlayback();
            if (ws && ws.readyState === WebSocket.OPEN) {
                ws.send(JSON.stringify({type:'interrupt'}));
                addLog('Interrupted');
            }
        });

        document.getElementById('recordBtn').addEventListener('click', function() {
            if (isRecording) return;
            stopPlayback();
            isRecording = true;
            document.getElementById('recordBtn').disabled = true;
            document.getElementById('stopBtn').disabled = false;
            document.getElementById('status').textContent = 'Recording...';
            document.getElementById('status').style.color = '#f44336';
            navigator.mediaDevices.getUserMedia({
                audio: { sampleRate: 16000, channelCount: 1, echoCancellation: true, noiseSuppression: true }
            }).then(function(stream) {
                audioContext = new AudioContext({ sampleRate: 16000 });
                var source = audioContext.createMediaStreamSource(stream);
                var processor = audioContext.createScriptProcessor(4096, 1, 1);
                processor.onaudioprocess = function(e) {
                    if (!isRecording) return;
                    var inp = e.inputBuffer.getChannelData(0);
                    var pcm = new Int16Array(inp.length);
                    for (var i = 0; i < inp.length; i++) {
                        var s = Math.max(-1, Math.min(1, inp[i]));
                        pcm[i] = s < 0 ? s * 0x8000 : s * 0x7FFF;
                    }
                    if (ws && ws.readyState === WebSocket.OPEN) ws.send(pcm.buffer);
                };
                source.connect(processor);
                var silentGain = audioContext.createGain();
                silentGain.gain.value = 0;
                processor.connect(silentGain);
                silentGain.connect(audioContext.destination);
                audioCapture = { stream: stream, processor: processor, source: source };
            }).catch(function(e) { addLog('Mic error: ' + e.message); isRecording = false; });
        });

        document.getElementById('stopBtn').addEventListener('click', function() {
            isRecording = false;
            document.getElementById('recordBtn').disabled = false;
            document.getElementById('stopBtn').disabled = true;
            document.getElementById('status').textContent = 'Processing...';
            document.getElementById('status').style.color = '#FF9800';
            if (audioCapture) {
                audioCapture.processor.disconnect();
                audioCapture.source.disconnect();
                audioCapture.stream.getTracks().forEach(function(t) { t.stop(); });
                audioCapture = null;
            }
            if (audioContext) { audioContext.close(); audioContext = null; }
            if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({type:'speech_end'}));
        });

        connectWS();
    </script>
</body>
</html>"""


@app.get("/", response_class=HTMLResponse)
async def root():
    return FRONTEND_HTML


@app.websocket("/ws/{session_id}")
async def websocket_endpoint(websocket: WebSocket, session_id: str):
    await handle_websocket(websocket, session_id)


@app.websocket("/ws")
async def websocket_endpoint_auto(websocket: WebSocket):
    await handle_websocket(websocket)