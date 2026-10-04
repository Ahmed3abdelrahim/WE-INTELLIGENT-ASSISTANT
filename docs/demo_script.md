# Demo Script

Run `make up` (or `bash scripts/native_up.sh` + `nginx -c scripts/native_nginx.conf` if no
Docker — see README) and open **http://127.0.0.1:8080**. Expect each grounded answer to take
roughly 60-140 seconds on CPU-only hardware (see `eval/results.md`) — this is real, measured
latency, not a bug; narrate through it rather than waiting in silence.

## 1. English question

Type: *"What internet packages does WE offer for home?"*

Point out: streamed answer, `[S#]` citation chips under the answer, click one to open the
sources panel (title, link, page/section, excerpt).

## 2. Egyptian voice question

Click the mic button, ask (in Egyptian Arabic) something like *"عايز أعرف باقات الموبايل
المتاحة عندكوا"*, let the 60s-capped recording stop itself or click mic again. The transcript
auto-fills and sends (unless "review before sending" is checked). Point out: the stage line
(Transcribing → Searching → Generating → Speaking) and the answer's audio player.

## 3. Follow-up question

Ask something that depends on the previous answer (e.g. *"وهو ده سعره كام؟"* / "and what's
the price of that?"). Point out: the backend rewrites it into a standalone query internally
using the last 4 turns before retrieving — the answer should correctly resolve the pronoun.

## 4. Upload an Arabic PDF and ask about it

Drag a real Arabic PDF (or use `tests/fixtures/arabic_pdf.pdf` for a demo) into the
documents dropzone, wait for its status to reach "ready", check its box, then ask a question
about its content. Point out: the citation now shows the uploaded filename and page number
instead of a te.eg URL, and that a different browser session (or an incognito window with a
cleared session) cannot see or query this document — session isolation is structural
(enforced in the retrieval filter itself), not just UI-level.

## 5. Unanswerable question → the assistant abstains

Ask something unrelated, e.g. *"What is the recipe for koshari?"* Point out: the response
status is `insufficient_evidence` (shown as a tag under the message), with no citations and
no fabricated facts — this is the citation validator and/or the pre-LLM retrieval-score gate
doing its job, both covered in `eval/results.md`.

## 6. Insights

Click "Insights" in the sidebar. Point out: a single on-demand LLM call returns validated
JSON (intent, products, language, dialect, sentiment, resolved, needs_escalation, summary),
rendered as a readable card, not raw JSON.

## 7. Wi-Fi off → everything still works

Disconnect the machine's network, then repeat step 1 (or any text question). Point out:
`HF_HUB_OFFLINE=1`/`TRANSFORMERS_OFFLINE=1` are set everywhere, every model loads from a
local path, and every service call goes to `127.0.0.1` — nothing in the default (`local`
provider) path ever reaches the internet. (See README's "Offline operation" section for how
this was verified on the build machine, where physically toggling Wi-Fi wasn't practical in
the sandboxed dev environment — architecturally verified there instead.)
