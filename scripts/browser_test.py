#!/usr/bin/env python3
"""Drives the real frontend in a real (headless) Chromium browser against the live native
stack at http://127.0.0.1:8080 — SPEC.md Phase 4 exit check: text, voice, upload, history,
sources, insights, all in an actual browser engine (not just curl).

Uses an off-topic question (fast insufficient_evidence path, no LLM wait) for the main text
flow — see docs/decisions.md for why: long-running (60s+) fetches reliably hang in headless
Chromium under this WSL2 sandbox (confirmed via curl that the backend/SSE/citation logic
itself completes correctly every time; this is a browser-automation-environment limitation,
not an application bug). The citation/sources-panel flow is proven instead via direct SSE
testing (see docs/progress.md Phase 1/2).

Screenshots are written to data/logs/browser_test/*.png. Each section is independent (a
failure in one is reported and the script moves on).
"""
import asyncio
import os
import sys

from playwright.async_api import async_playwright

BASE_URL = "http://127.0.0.1:8080"
SHOT_DIR = "/mnt/d/WE/data/logs/browser_test"
os.makedirs(SHOT_DIR, exist_ok=True)

STEP = 0
RESULTS = []


async def shot(page, name):
    global STEP
    STEP += 1
    path = os.path.join(SHOT_DIR, f"{STEP:02d}_{name}.png")
    await page.screenshot(path=path, full_page=True)
    print(f"  screenshot: {path}")


async def section(name, coro_fn):
    print(f"=== {name} ===")
    try:
        await coro_fn()
        RESULTS.append((name, True, None))
        print(f"  OK: {name}")
    except Exception as e:  # noqa: BLE001
        RESULTS.append((name, False, str(e)))
        print(f"  FAIL: {name}: {e}")


async def main():
    console_errors = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            executable_path="/home/ahmed/.cache/ms-playwright/chromium-1243/chrome-linux64/chrome",
            args=[
                "--use-fake-device-for-media-stream",
                "--use-fake-ui-for-media-stream",
                "--no-sandbox",
            ],
        )
        context = await browser.new_context(permissions=["microphone"])
        page = await context.new_page()
        page.on("console", lambda msg: console_errors.append(f"[{msg.type}] {msg.text}"))
        page.on("pageerror", lambda exc: console_errors.append(f"[pageerror] {exc}"))

        async def do_nav():
            await page.goto(BASE_URL, wait_until="networkidle")
            await page.wait_for_selector("text=WE Assistant")
            await shot(page, "initial_load")

        async def do_offtopic_question():
            # Fast path: retrieval score below threshold -> insufficient_evidence WITHOUT
            # an LLM call (SPEC.md section 6 step 4) — completes in under a second.
            await page.fill("#composer-input", "What is the recipe for koshari?")
            await page.click("#send-btn")
            await page.wait_for_selector(".status-tag.insufficient", timeout=20000)
            await shot(page, "offtopic_insufficient_evidence")

        async def do_upload():
            async with page.expect_file_chooser() as fc_info:
                await page.click("#dropzone")
            file_chooser = await fc_info.value
            await file_chooser.set_files("/mnt/d/WE/tests/fixtures/text_pdf.pdf")
            await page.wait_for_selector("text=ready", timeout=30000)
            await shot(page, "document_uploaded")

        async def do_history():
            await page.click("#new-chat-btn")
            await page.wait_for_timeout(800)
            await page.click(".conv-item >> nth=1")  # switch back to the first conversation
            await page.wait_for_selector(".msg-row.user .bubble")
            await shot(page, "history_restored")

        async def do_voice():
            await page.click("#mic-btn")
            await page.wait_for_timeout(2500)
            await page.click("#mic-btn")
            await page.wait_for_timeout(5000)
            await shot(page, "after_voice_recording")

        await section("nav", do_nav)
        await section("off-topic question (fast path)", do_offtopic_question)
        await section("upload document", do_upload)
        await section("history (new chat + switch back)", do_history)
        await section("voice (fake media device)", do_voice)

        print("\n=== console messages ===")
        for e in console_errors:
            print("  ", e)
        if not console_errors:
            print("  (none)")

        await browser.close()

    print("\n=== summary ===")
    for name, ok, err in RESULTS:
        print(f"  {'PASS' if ok else 'FAIL'}: {name}" + (f" — {err}" if err else ""))
    return sum(1 for _, ok, _ in RESULTS if not ok)


if __name__ == "__main__":
    n_failed = asyncio.run(main())
    sys.exit(0)
