#!/usr/bin/env python3
"""Generates SYNTHETIC audio clips for ASR/TTS plumbing tests only (SPEC.md section 10) —
never a substitute for the real recorded clips in eval/audio_manifest.jsonl. Requires the
TTS service reachable at TTS_URL (default http://127.0.0.1:8002).

Produces eval/audio/synthetic_{en_clean,egy_clean,en_noisy,silence}.wav.
"""
import os
import random
import struct
import wave

import httpx

TTS_URL = os.environ.get("TTS_URL", "http://127.0.0.1:8002")
OUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "eval", "audio")


def synthesize(text: str, lang: str, out_path: str):
    resp = httpx.post(f"{TTS_URL}/synthesize", json={"text": text, "lang": lang}, timeout=60.0)
    resp.raise_for_status()
    with open(out_path, "wb") as f:
        f.write(resp.content)
    print("wrote", out_path)


def make_noisy(src_path: str, out_path: str, amplitude: int = 1800, seed: int = 42):
    with wave.open(src_path, "rb") as w:
        params = w.getparams()
        frames = w.readframes(w.getnframes())
    samples = struct.unpack("<%dh" % (len(frames) // 2), frames)
    rng = random.Random(seed)
    noisy = [max(-32768, min(32767, s + rng.randint(-amplitude, amplitude))) for s in samples]
    with wave.open(out_path, "wb") as w:
        w.setparams(params)
        w.writeframes(struct.pack("<%dh" % len(noisy), *noisy))
    print("wrote", out_path)


def make_silence(out_path: str, seconds: float = 2.0, sr: int = 16000):
    n = int(sr * seconds)
    with wave.open(out_path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(struct.pack("<%dh" % n, *([0] * n)))
    print("wrote", out_path)


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    en_clean = os.path.join(OUT_DIR, "synthetic_en_clean.wav")
    synthesize("What is the price of the home internet package?", "en", en_clean)
    synthesize("عايز أعرف سعر باقة الإنترنت المنزلي", "ar", os.path.join(OUT_DIR, "synthetic_egy_clean.wav"))
    make_noisy(en_clean, os.path.join(OUT_DIR, "synthetic_en_noisy.wav"))
    make_silence(os.path.join(OUT_DIR, "synthetic_silence.wav"))


if __name__ == "__main__":
    main()
