#!/usr/bin/env python3
"""Generates SYNTHETIC audio clips for ASR/TTS plumbing tests only (SPEC.md section 10) —
never a substitute for the real recorded clips in eval/audio_manifest.jsonl. Requires the
TTS service reachable at TTS_URL (default http://127.0.0.1:8002).

Produces eval/audio/synthetic_{en_clean,egy_clean,en_noisy,silence}.wav.

With --manifest it instead builds a synthetic ASR eval set mirroring the 10 real recording
slots: eval/audio/synthetic/*.wav + eval/audio_manifest.synthetic.jsonl (ground truth = the
TTS input text), for `eval/asr_eval.py --manifest eval/audio_manifest.synthetic.jsonl`.
"""
import json
import sys
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


SYNTHETIC_SET = [
    # (id, lang, dialect, condition, text)
    ("en_clean_01", "en", "", "clean", "What is the price of the home internet package?"),
    ("en_clean_02", "en", "", "clean", "How can I recharge my mobile balance?"),
    ("en_noisy_01", "en", "", "noisy", "Which number should I call before travelling abroad?"),
    ("ar_msa_clean_01", "ar", "msa", "clean", "ما هي أسعار باقات الإنترنت المنزلي؟"),
    ("ar_msa_noisy_01", "ar", "msa", "noisy", "كيف يمكنني شحن رصيد الهاتف المحمول؟"),
    ("ar_egy_clean_01", "ar", "egyptian", "clean", "عايز أعرف سعر باقة الإنترنت المنزلي"),
    ("ar_egy_clean_02", "ar", "egyptian", "clean", "إزاي أنقل ملكية الخط لاسم تاني؟"),
    ("ar_egy_noisy_01", "ar", "egyptian", "noisy", "هو رقم خدمة العملاء كام؟"),
    ("ar_codeswitch_01", "ar", "egyptian", "code-switched", "عايز أعرف الواي فاي بتاع باقة WE Air"),
]


def make_manifest():
    out_dir = os.path.join(OUT_DIR, "synthetic")
    os.makedirs(out_dir, exist_ok=True)
    rows = []
    for cid, lang, dialect, condition, text in SYNTHETIC_SET:
        path = os.path.join(out_dir, f"{cid}.wav")
        if condition == "noisy":
            clean = os.path.join(out_dir, f"_{cid}_clean.wav")
            synthesize(text, lang, clean)
            make_noisy(clean, path)
            os.remove(clean)
        else:
            synthesize(text, lang, path)
        rows.append({"id": cid, "filename": f"synthetic/{cid}.wav", "lang": lang, "dialect": dialect,
                     "condition": condition, "transcript": text, "notes": "synthetic Piper TTS"})
    manifest = os.path.join(os.path.dirname(OUT_DIR), "audio_manifest.synthetic.jsonl")
    with open(manifest, "w", encoding="utf-8") as f:
        f.writelines(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)
    print("wrote", manifest)


def main():
    if "--manifest" in sys.argv:
        make_manifest()
        return
    os.makedirs(OUT_DIR, exist_ok=True)
    en_clean = os.path.join(OUT_DIR, "synthetic_en_clean.wav")
    synthesize("What is the price of the home internet package?", "en", en_clean)
    synthesize("عايز أعرف سعر باقة الإنترنت المنزلي", "ar", os.path.join(OUT_DIR, "synthetic_egy_clean.wav"))
    make_noisy(en_clean, os.path.join(OUT_DIR, "synthetic_en_noisy.wav"))
    make_silence(os.path.join(OUT_DIR, "synthetic_silence.wav"))


if __name__ == "__main__":
    main()
