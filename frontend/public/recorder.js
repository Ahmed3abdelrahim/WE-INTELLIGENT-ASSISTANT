// MediaRecorder wrapper for mic input (SPEC.md section 8: webm/opus, 60s cap).
//
// Opening the microphone (getUserMedia) takes 1-2s on many Windows machines, so the stream is
// kept open for KEEP_WARM_MS after a recording: back-to-back recordings start instantly, and
// the mic is released (browser recording indicator off) once idle.
const KEEP_WARM_MS = 60000;

class Recorder {
  constructor(maxSeconds = 60) {
    this.maxSeconds = maxSeconds;
    this.mediaRecorder = null;
    this.stream = null;
    this.chunks = [];
    this.onTick = null; // callback(elapsedSeconds)
    this.onAutoStop = null; // callback() when the max length is reached
    this._timer = null;
    this._elapsed = 0;
    this._releaseTimer = null;
  }

  _streamIsLive() {
    return !!this.stream && this.stream.getAudioTracks().some((t) => t.readyState === "live");
  }

  // Resolves once audio is actually being captured (MediaRecorder "start" event).
  async start() {
    clearTimeout(this._releaseTimer);
    if (!this._streamIsLive()) {
      this.stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    }
    this.chunks = [];
    this._elapsed = 0;
    const mimeType = MediaRecorder.isTypeSupported("audio/webm;codecs=opus")
      ? "audio/webm;codecs=opus"
      : "audio/webm";
    this.mediaRecorder = new MediaRecorder(this.stream, { mimeType });
    this.mediaRecorder.ondataavailable = (e) => {
      if (e.data && e.data.size > 0) this.chunks.push(e.data);
    };
    await new Promise((resolve) => {
      this.mediaRecorder.onstart = resolve;
      this.mediaRecorder.start();
    });
    this._timer = setInterval(() => {
      this._elapsed += 1;
      if (this.onTick) this.onTick(this._elapsed);
      if (this._elapsed >= this.maxSeconds && this.onAutoStop) this.onAutoStop();
    }, 1000);
  }

  stop() {
    if (this._timer) {
      clearInterval(this._timer);
      this._timer = null;
    }
    if (!this.mediaRecorder || this.mediaRecorder.state === "inactive") {
      return Promise.resolve(new Blob(this.chunks, { type: "audio/webm" }));
    }
    return new Promise((resolve) => {
      this.mediaRecorder.onstop = () => {
        resolve(new Blob(this.chunks, { type: "audio/webm" }));
        this._releaseTimer = setTimeout(() => this.release(), KEEP_WARM_MS);
      };
      this.mediaRecorder.stop();
    });
  }

  release() {
    clearTimeout(this._releaseTimer);
    if (this.stream) this.stream.getTracks().forEach((t) => t.stop());
    this.stream = null;
  }

  get isRecording() {
    return !!this.mediaRecorder && this.mediaRecorder.state === "recording";
  }
}
