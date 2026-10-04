# Audio Recording Checklist (for Ahmed)

SPEC.md section 10 calls for ~10 real recorded clips. `eval/audio_manifest.jsonl.template`
lists the 10 slots this was planned around (lang/dialect/condition mix) — rename it to
`eval/audio_manifest.jsonl` once filled in. Feel free to change wording/count; the mix
(EN, MSA, Egyptian, noisy, code-switched) matters more than hitting exactly 10.

## How to record
1. Ask a real WE Telecom Egypt-style question per clip (see `eval/questions.jsonl` for
   ideas once Phase 5 generates it — or improvise naturally).
2. Save as 16kHz+ mono WAV (or anything `ffmpeg`/faster-whisper can decode: m4a, webm, mp3 are fine too).
3. Put the file in `eval/audio/` using the `filename` from the manifest.
4. Fill in the manifest's `"transcript"` field with the **verbatim** ground-truth transcript
   (exactly what you said, including false starts/fillers if any — this is what WER/CER is measured against).
5. For the "noisy" clips: record with real background noise (TV, traffic, fan) rather than
   adding noise synthetically — that's the condition we actually want to measure.
6. For the "code-switched" clip: naturally mix English brand/product words into an Arabic
   sentence (e.g. "عايز أعرف الـ Wi-Fi بتاع باقة الـ Home Internet").
7. For "accented": speak English with a natural Egyptian accent.

## What's already done
- `eval/audio_manifest.jsonl.template` — the 10-slot plan (lang/dialect/condition/empty transcript).
- Synthetic Piper-TTS clips will be generated separately for **plumbing tests only**
  (ASR smoke test, pipeline wiring) and are clearly labeled `synthetic` wherever used —
  never substituted for the real eval numbers in `eval/results.md`.

## When done
Rename the template to `eval/audio_manifest.jsonl` (drop `.template`) and run:

```bash
python eval/asr_eval.py          # writes eval/results_asr.md
```

It compares large-v3-turbo vs large-v3, beam 1 vs 5, hotwords on/off (WER, CER, speed).
On a 10 GB GPU stop the backend and ASR service first (it loads the models itself).
A synthetic-TTS run of the same tool is in `eval/results_asr_synthetic.md` for reference.
