// Live monitor — polls the FastAPI backend every 3 seconds
// API_BASE auto-detects: same origin when served by FastAPI, localhost for local file open
const API_BASE = (window.location.hostname === "localhost" || window.location.hostname === "127.0.0.1" || window.location.protocol === "file:")
  ? "http://localhost:8000"
  : window.location.origin;

const elActiveCalls   = document.getElementById("active-calls");
const elBookings      = document.getElementById("recent-bookings");
const elServerStatus  = document.getElementById("server-status");
const elLiveTranscript = document.getElementById("live-transcript");

// Recent bookings stored in memory for this session
const recentBookings = [];

async function checkHealth() {
  try {
    const res = await fetch(`${API_BASE}/health`, { signal: AbortSignal.timeout(2000) });
    if (res.ok) {
      elServerStatus.textContent = "Online";
      elServerStatus.className = "status-dot status-online";
      return true;
    }
  } catch (_) {}
  elServerStatus.textContent = "Offline";
  elServerStatus.className = "status-dot status-offline";
  return false;
}

async function fetchStats() {
  try {
    const res = await fetch(`${API_BASE}/stats`, { signal: AbortSignal.timeout(2000) });
    if (!res.ok) return;
    const data = await res.json();

    // Active calls
    elActiveCalls.textContent = data.active_calls ?? "0";

    // New bookings
    if (data.latest_booking) {
      const b = data.latest_booking;
      const key = `${b.timestamp}-${b.name}`;
      if (!recentBookings.find(x => x.key === key)) {
        recentBookings.unshift({ key, ...b });
        if (recentBookings.length > 5) recentBookings.pop();
        renderBookings();
      }
    }
  } catch (_) {}
}

function renderBookings() {
  if (recentBookings.length === 0) {
    elBookings.innerHTML = '<p class="monitor-hint">No bookings yet this session.</p>';
    return;
  }
  elBookings.innerHTML = recentBookings.map(b => `
    <div class="booking-entry">
      <strong>${b.name || "—"}</strong> &middot; ${b.date || "—"} at ${b.time || "—"}<br>
      <span style="color:#6b7280;font-size:12px">${b.phone || ""} &middot; ${b.timestamp || ""}</span>
    </div>
  `).join("");
}

async function poll() {
  const online = await checkHealth();
  if (online) await fetchStats();
}

// Poll immediately, then every 3 seconds
poll();
setInterval(poll, 3000);

// ==========================================
// Web-to-Bot Call Logic (Hands-Free Full Duplex)
// ==========================================

const VAD_THRESHOLD = 0.07; // Fine-tuned for better pickup
const SILENCE_DURATION = 1500; // Increased to 1.5s to give user more thinking time
const WS_BASE = API_BASE.replace(/^http/, "ws");

let webSocket = null;
let audioContext = null;
let micStream = null;
let scriptProcessor = null;
let currentBotAudio = null; // Currently playing HTMLAudioElement

const elBtnStart = document.getElementById("btn-start-call");
const elBtnEnd = document.getElementById("btn-end-call");
const elStatus = document.getElementById("web-call-status");

function updateStatus(text, className) {
  if (elStatus) {
    elStatus.textContent = text;
    elStatus.className = `status-indicator ${className}`;
  }
}

async function startWebCall() {
  updateStatus("Connecting...", "");
  if (elLiveTranscript) elLiveTranscript.textContent = "";
  const sessionId = crypto.randomUUID();
  const wsUrl = `${WS_BASE}/web-call/${sessionId}`;
  
  try {
    // Request mic early with noise suppression
    micStream = await navigator.mediaDevices.getUserMedia({ 
      audio: {
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true
      } 
    });
  } catch (err) {
    updateStatus("Microphone access denied.", "status-error");
    return;
  }

  // Mic granted — now swap buttons
  elBtnStart.style.display = "none";
  elBtnEnd.style.display = "inline-block";

  webSocket = new WebSocket(wsUrl);
  
  webSocket.onopen = () => {
    updateStatus("Connected! Say Hello.", "status-listening");
    startAudioCapture();
  };

  webSocket.onmessage = (event) => {
    if (typeof event.data === "string") {
      try {
        const msg = JSON.parse(event.data);
        if (msg.type === "audio" && msg.url) {
          playBotAudio(msg.url);
        } else if (msg.type === "transcript") {
          if (elLiveTranscript) {
            if (msg.isFinal) {
              elLiveTranscript.textContent = `You: "${msg.text}"`;
            } else {
              elLiveTranscript.textContent = `Hearing: "${msg.text}..."`;
            }
          }
        }
      } catch (e) {
        console.error("Invalid WS message", e);
      }
    }
  };

  webSocket.onclose = () => {
    endWebCall();
  };
}

function playBotAudio(url) {
  if (currentBotAudio) {
    currentBotAudio.pause();
    currentBotAudio = null;
  }
  
  updateStatus("Bot Speaking...", "status-speaking");
  if (elLiveTranscript) elLiveTranscript.textContent = ""; // Clear transcript when bot replies
  window._botStartTime = Date.now();
  const audioUrl = url.startsWith("http") ? url : `${API_BASE}${url}`;
  currentBotAudio = new Audio(audioUrl);
  
  currentBotAudio.onended = () => {
    currentBotAudio = null;
    updateStatus("Listening...", "status-listening");
    // Send greeting_ended message to backend so it knows to start transcribing user
    if (webSocket && webSocket.readyState === WebSocket.OPEN) {
      webSocket.send(JSON.stringify({ type: "greeting_ended" }));
    }
  };
  
  currentBotAudio.play().catch(e => console.error("Audio play failed:", e));
}

function startAudioCapture() {
  audioContext = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: 48000 });
  const source = audioContext.createMediaStreamSource(micStream);
  
  // Create a ScriptProcessorNode with bufferSize 4096 and 1 output channel
  scriptProcessor = audioContext.createScriptProcessor(4096, 1, 1);
  
  source.connect(scriptProcessor);
  scriptProcessor.connect(audioContext.destination); // Required for script processor to fire

  const elMeterContainer = document.getElementById("volume-meter-container");
  const elMeterBar = document.getElementById("volume-meter-bar");
  if (elMeterContainer) elMeterContainer.style.display = "block";
  
  let isUserTalking = false;
  let silenceTimer = 0;
  
  scriptProcessor.onaudioprocess = (audioProcessingEvent) => {
    if (!webSocket || webSocket.readyState !== WebSocket.OPEN) return;

    const inputBuffer = audioProcessingEvent.inputBuffer;
    const inputData = inputBuffer.getChannelData(0);
    
    // Calculate RMS to detect volume
    let sum = 0;
    for (let i = 0; i < inputData.length; i++) {
      sum += inputData[i] * inputData[i];
    }
    const rms = Math.sqrt(sum / inputData.length);
    
    // Update volume meter
    if (elMeterBar) {
      const volPercent = Math.min(100, Math.sqrt(rms) * 200); // Scaled for visibility
      elMeterBar.style.width = `${volPercent}%`;
      elMeterBar.style.background = rms > VAD_THRESHOLD ? "#10b981" : "#94a3b8";
    }
    
    // VAD Interrupt Logic
    // Dynamic Threshold: if bot is speaking, require MUCH louder voice to interrupt (3x)
    // Protection window: ignore all interrupts for the first 1000ms of bot speech
    if (!window._botStartTime) window._botStartTime = 0;
    const timeSinceBotStarted = Date.now() - window._botStartTime;
    const isBotSpeaking = !!currentBotAudio;
    const isProtected = isBotSpeaking && timeSinceBotStarted < 1000;
    
    const currentThreshold = isBotSpeaking ? (VAD_THRESHOLD * 3.0) : VAD_THRESHOLD;
    
    if (rms > currentThreshold && !isProtected) {
      silenceTimer = 0;
      if (!isUserTalking) {
        // Debounce interrupt: user must speak for ~500ms (6 chunks) before we kill bot audio
        if (!window._interruptCounter) window._interruptCounter = 0;
        window._interruptCounter++;
        
        if (window._interruptCounter >= 6) {
          isUserTalking = true;
          // If bot is speaking, interrupt it!
          if (currentBotAudio) {
            console.log("[VAD] Sustained speech detected, interrupting bot.");
            currentBotAudio.pause();
            currentBotAudio = null;
            updateStatus("Listening...", "status-listening");
            webSocket.send(JSON.stringify({ type: "interrupt" }));
          }
        }
      }
    } else {
      window._interruptCounter = 0;
      silenceTimer += (inputData.length / audioContext.sampleRate) * 1000;
      if (isUserTalking && silenceTimer > SILENCE_DURATION) {
        isUserTalking = false;
        updateStatus("Thinking...", "status-thinking");
      }
    } 
    
    // Convert Float32 to Int16 without downsampling
    const result = new Int16Array(inputData.length);
    for (let i = 0; i < inputData.length; i++) {
      let sample = inputData[i];
      sample = Math.max(-1, Math.min(1, sample));
      result[i] = sample < 0 ? sample * 0x8000 : sample * 0x7FFF;
    }
    
    // Always send audio to the server for now to debug transcription issues
    webSocket.send(result.buffer);
  };
}

function endWebCall() {
  window._interruptCounter = 0;
  window._botStartTime = 0;

  if (webSocket) {
    webSocket.close();
    webSocket = null;
  }
  if (scriptProcessor) {
    scriptProcessor.disconnect();
    scriptProcessor = null;
  }
  if (audioContext) {
    audioContext.close();
    audioContext = null;
  }
  if (micStream) {
    micStream.getTracks().forEach(track => track.stop());
    micStream = null;
  }
  if (currentBotAudio) {
    currentBotAudio.pause();
    currentBotAudio = null;
  }
  
  updateStatus("Call ended.", "");
  if (elLiveTranscript) elLiveTranscript.textContent = "";
  if (elBtnStart && elBtnEnd) {
    elBtnStart.style.display = "inline-block";
    elBtnEnd.style.display = "none";
  }
}

if (elBtnStart && elBtnEnd) {
  elBtnStart.addEventListener("click", startWebCall);
  elBtnEnd.addEventListener("click", endWebCall);
}
