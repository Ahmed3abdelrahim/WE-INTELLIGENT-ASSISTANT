// MediaRecorder wrapper for mic input (SPEC.md section 8: webm/opus, 60s cap).
class Recorder {
  constructor(maxSeconds = 60) {
    this.maxSeconds = maxSeconds;
    this.mediaRecorder = null;
    this.stream = null;
    this.chunks = [];
    this.onTick = null; // callback(elapsedSeconds)
    this._timer = null;
    this._elapsed = 0;
  }

  async start() {
    this.stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    this.chunks = [];
    this._elapsed = 0;
    const mimeType = MediaRecorder.isTypeSupported("audio/webm;codecs=opus")
      ? "audio/webm;codecs=opus"
      : "audio/webm";
    this.mediaRecorder = new MediaRecorder(this.stream, { mimeType });
    this.mediaRecorder.ondataavailable = (e) => {
      if (e.data && e.data.size > 0) this.chunks.push(e.data);
    };
    this.mediaRecorder.start();
    this._timer = setInterval(() => {
      this._elapsed += 1;
      if (this.onTick) this.onTick(this._elapsed);
      if (this._elapsed >= this.maxSeconds) this.stop();
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
        const blob = new Blob(this.chunks, { type: "audio/webm" });
        this.stream.getTracks().forEach((t) => t.stop());
        resolve(blob);
      };
      this.mediaRecorder.stop();
    });
  }

  get isRecording() {
    return !!this.mediaRecorder && this.mediaRecorder.state === "recording";
  }
}
