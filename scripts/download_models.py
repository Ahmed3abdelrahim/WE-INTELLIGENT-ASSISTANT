#!/usr/bin/env python3
"""Download every model listed in SPEC.md section 2, and only those.

Source: ModelScope (modelscope.cn), not huggingface.co.
Why: on this machine/network, huggingface.co and cdn-lfs.huggingface.co are
unreachable at the TCP level (connect() times out on every CloudFront IP — verified
with `curl -v`, not a flaky retry), while modelscope.cn mirrors the *exact same*
repos (`Qwen/Qwen3-4B-GGUF`, `BAAI/bge-m3`, `BAAI/bge-reranker-v2-m3`,
`Systran/faster-whisper-large-v3`, `rhasspy/piper-voices`) under the same repo ids
and was confirmed reachable. See docs/decisions.md for the full diagnosis.
One exception: `large-v3-turbo` is not mirrored as `Systran/faster-whisper-large-v3-turbo`
on ModelScope; `mobiuslabsgmbh/faster-whisper-large-v3-turbo` is the same CTranslate2
conversion of openai/whisper-large-v3-turbo, confirmed present with the expected
faster-whisper file layout (config.json, model.bin, tokenizer.json, vocabulary.json).

No invented URLs: every repo id / path here was verified reachable via
`https://modelscope.cn/api/v1/models/<repo>/repo/files` before being hardcoded, and
each downloaded file's sha256 (when ModelScope reports one) is checked after download.

Real downloads only. Nothing is skipped silently; failures are printed and the
script continues to the next model, exiting non-zero at the end if any failed.
"""
import hashlib
import os
import sys
import time
from pathlib import Path

import httpx

REPO_ROOT = Path(__file__).resolve().parents[1]
MODELS_DIR = REPO_ROOT / "models"
MS_API = "https://modelscope.cn/api/v1/models"

RESULTS = []


def record(name, ok, detail):
    RESULTS.append((name, ok, detail))
    print(f"[{'OK' if ok else 'FAIL'}] {name}: {detail}", flush=True)


def ms_list(repo: str, root: str | None = None) -> list[dict]:
    params = {"Revision": "master"}
    if root:
        params["Root"] = root
    resp = httpx.get(f"{MS_API}/{repo}/repo/files", params=params, timeout=30.0)
    resp.raise_for_status()
    data = resp.json()
    if data.get("Code") != 200:
        raise RuntimeError(data.get("Message"))
    return data["Data"]["Files"]


def ms_list_recursive(repo: str, root: str | None = None, exclude_dirs: set[str] = frozenset()) -> list[dict]:
    out = []
    for entry in ms_list(repo, root):
        if entry["Type"] == "tree":
            name = entry["Path"].rsplit("/", 1)[-1]
            if name in exclude_dirs:
                continue
            out.extend(ms_list_recursive(repo, entry["Path"], exclude_dirs))
        else:
            out.append(entry)
    return out


def ms_download_file(repo: str, path: str, dest: Path, expected_sha256: str | None = None, expected_size: int | None = None):
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and expected_size and dest.stat().st_size == expected_size:
        print(f"  skip (already present, size matches): {dest}", flush=True)
        return
    tmp = dest.with_suffix(dest.suffix + ".part")
    url = f"{MS_API}/{repo}/repo"
    params = {"Revision": "master", "FilePath": path}
    t0 = time.time()
    with httpx.stream("GET", url, params=params, timeout=300.0, follow_redirects=True) as r:
        r.raise_for_status()
        hasher = hashlib.sha256()
        total = 0
        with open(tmp, "wb") as f:
            for chunk in r.iter_bytes(chunk_size=4 * 1024 * 1024):
                f.write(chunk)
                hasher.update(chunk)
                total += len(chunk)
    if expected_sha256 and hasher.hexdigest() != expected_sha256:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"sha256 mismatch for {path}: expected {expected_sha256}, got {hasher.hexdigest()}")
    tmp.rename(dest)
    print(f"  downloaded {path} -> {dest} ({total / 1e6:.1f} MB in {time.time() - t0:.1f}s)", flush=True)


def download_llm():
    name = "LLM: Qwen/Qwen3-4B-GGUF (Q4_K_M)"
    try:
        files = ms_list("Qwen/Qwen3-4B-GGUF")
        match = next((f for f in files if f["Name"] == "Qwen3-4B-Q4_K_M.gguf"), None)
        if not match:
            record(name, False, f"Qwen3-4B-Q4_K_M.gguf not found among {[f['Name'] for f in files]}")
            return
        dest = MODELS_DIR / "llm" / "qwen3-4b-q4_k_m.gguf"
        ms_download_file("Qwen/Qwen3-4B-GGUF", match["Path"], dest, match.get("Sha256"), match.get("Size"))
        record(name, True, f"saved to {dest} ({dest.stat().st_size / 1e9:.2f} GB)")
    except Exception as e:  # noqa: BLE001
        record(name, False, str(e))


def download_asr():
    for repo, dest_name in [
        ("Systran/faster-whisper-large-v3", "large-v3"),
        ("mobiuslabsgmbh/faster-whisper-large-v3-turbo", "large-v3-turbo"),
    ]:
        name = f"ASR: {repo} -> models/asr/{dest_name}"
        try:
            files = ms_list(repo)
            dest_dir = MODELS_DIR / "asr" / dest_name
            for f in files:
                if f["Type"] != "blob" or f["Name"] in (".gitattributes", "README.md"):
                    continue
                ms_download_file(repo, f["Path"], dest_dir / f["Name"], f.get("Sha256"), f.get("Size"))
            record(name, True, f"saved to {dest_dir}")
        except Exception as e:  # noqa: BLE001
            record(name, False, str(e))


def download_embeddings():
    for repo, dest_name, exclude in [
        ("BAAI/bge-m3", "bge-m3", {"imgs", "onnx"}),
        ("BAAI/bge-reranker-v2-m3", "bge-reranker-v2-m3", {"assets", "onnx"}),
    ]:
        name = f"Encoder: {repo} -> models/encoder/{dest_name}"
        try:
            files = ms_list_recursive(repo, exclude_dirs=exclude)
            dest_dir = MODELS_DIR / "encoder" / dest_name
            for f in files:
                if f["Name"] == ".gitattributes":
                    continue
                ms_download_file(repo, f["Path"], dest_dir / f["Path"], f.get("Sha256"), f.get("Size"))
            record(name, True, f"saved to {dest_dir} ({len(files)} files)")
        except Exception as e:  # noqa: BLE001
            record(name, False, str(e))


def download_tts():
    repo = "rhasspy/piper-voices"
    voices = [
        ("en/en_US/lessac/medium/en_US-lessac-medium.onnx", "en_US-lessac-medium.onnx"),
        ("en/en_US/lessac/medium/en_US-lessac-medium.onnx.json", "en_US-lessac-medium.onnx.json"),
        ("ar/ar_JO/kareem/medium/ar_JO-kareem-medium.onnx", "ar_voice.onnx"),
        ("ar/ar_JO/kareem/medium/ar_JO-kareem-medium.onnx.json", "ar_voice.onnx.json"),
    ]
    for src_path, dest_name in voices:
        name = f"TTS: {repo}/{src_path}"
        try:
            files = ms_list(repo, root=os.path.dirname(src_path))
            match = next((f for f in files if f["Path"] == src_path), None)
            dest = MODELS_DIR / "tts" / dest_name
            ms_download_file(repo, src_path, dest, match.get("Sha256") if match else None, match.get("Size") if match else None)
            record(name, True, f"saved to {dest}")
        except Exception as e:  # noqa: BLE001
            record(name, False, str(e))


def main():
    t0 = time.time()
    download_llm()
    download_asr()
    download_embeddings()
    download_tts()
    print(f"\n--- done in {time.time() - t0:.1f}s ---")
    failed = [r for r in RESULTS if not r[1]]
    if failed:
        print(f"{len(failed)} model(s) failed to download:")
        for n, _, d in failed:
            print(f"  - {n}: {d}")
        sys.exit(1)


if __name__ == "__main__":
    main()
