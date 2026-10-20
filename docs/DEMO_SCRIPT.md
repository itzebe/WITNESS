# Three-minute demo script

Target length: **2 minutes 45 seconds**, leaving margin. Every claim in this
script is something the product actually does and can be re-run live.

> **Compliance gate — read before recording.** The Ring track rules say: *"The
> demo video must show the project working through a simulator or an actual Ring
> device you have for testing."* Ring's official simulator **is** the Ring
> Developer Playground (`developer.amazon.com/ring/console/playground`) — the
> hackathon's own organizers confirmed on the Devpost forum that "the simulator
> is the Ring Developer Playground."
>
> The scenario below runs on WITNESS's **own** simulated source
> (`ring-simulated`), which is generated from a scenario table and does **not**
> contact Ring. That is not the Ring simulator, and it must not be presented as
> one.
>
> To satisfy the rule, record the demo with the **live `ring` source pointed at
> the Ring Developer Playground**. The Playground is behind an Amazon Developer
> sign-in; obtain a free account, generate a one-click token, and run with
> `RING_ACCESS_TOKEN` set (see README, and `scripts/live_smoke.py` to confirm the
> token works before recording). If the Playground does not expose a condition
> you want to show (for example the encryption transition), show the live source
> for what it does establish, and state plainly which frames come from WITNESS's
> own scenario. Do not present the simulated source as the Ring simulator. See
> `docs/SUBMISSION.md`.

Setup before recording:

```bash
rm -f witness.db
python -m uvicorn witness.api:app --port 8000
```

Open `http://127.0.0.1:8000`. Confirm the source selector reads
**Simulated Ring source (scripted) · simulated** and the blue simulated-source
banner is visible.

---

## 0:00 — 0:15 · The problem, in one question

**On screen:** the empty Overview tab.

**Say:** "Your camera stops showing up in your app. What does that mean? It could
be offline. It could have been unplugged. The owner could have unlinked the
integration. They could have turned on end-to-end encryption — which removes the
device from the API entirely. Or the request could simply have failed. Those are
five different facts, and almost every connected-home product shows them all as
'device offline.'"

**Why this works:** it is a concrete question with a real answer, not a preamble
about AI.

---

## 0:15 — 0:35 · What WITNESS is

**On screen:** press **Observe now** once. The verdict panel fills in: two
devices observed, one limitation.

**Say:** "WITNESS is a read-only integration for the Ring Partner API. It reports
what the source actually said, what can be established from it, and what it
cannot. It runs on a deterministic rules engine — no model call, no guess. The
same evidence always produces the same answer."

Point at the **What remains unknown** section.

**Say:** "This is the part most products leave out. Every question WITNESS could
not answer is listed here, with the specific reason."

---

## 0:35 — 1:05 · The withdrawal — and the false one it refuses to make

**On screen:** Integration tab. Press **Advance scenario** once. Back to
Overview. Press **Observe now**.

**Say:** "The Driveway camera is gone from the device list. One absence."

**On screen:** the Driveway card reads **Not observed** — not "offline", not
"removed".

**Say:** "WITNESS will not call that a withdrawal. One absence could be anything.
It counts consecutive successful absences and waits."

**On screen:** Integration tab → **Advance scenario** → Overview → **Observe
now**.

**Say:** "Now the source request fails."

**On screen:** both devices read **Source unavailable**; the verdict panel turns
red.

**Say:** "This is the critical case. A failed request is not evidence that a
device is offline. It is evidence that WITNESS could not look. So WITNESS draws
**no** device conclusion at all."

**On screen:** **Advance scenario** → **Observe now**.

**Say:** "The source recovers. The camera is still absent — that is now two
consecutive successful absences, which meets the threshold."

**On screen:** Driveway flips to **No longer exposed**.

**Say:** "Now it asserts a withdrawal. And it tells you exactly what that does
*not* mean: it does not mean the device was removed, or tampered with, or broken.
WITNESS cannot distinguish those, and it says so instead of picking one."

---

## 1:05 — 1:30 · A conclusion you can check

**On screen:** click **Evidence detail** on the withdrawal card.

**Say:** "Every assessment carries the rule that produced it, the evidence it
rests on, and the conclusions it does not license. This is the rule id. These are
the observation references. This is not a model's opinion — it is a pure
function over stored observations, so you can replay it and get the same result."

**On screen:** the ledger records are visible in the drawer footer / Integrity
tab.

---

## 1:30 — 2:00 · Integrity, stated honestly

**On screen:** Integrity tab. The green "Chain verified" banner.

**Say:** "Every observation and every assessment is appended to a hash-linked
ledger. Each record commits to the digest of the record before it. If anyone
modifies, inserts, deletes or reorders a record, verification fails and names the
first bad index."

**On screen:** scroll to the limitation note.

**Say:** "And here is what it does *not* prove. It does not prove the ledger was
never rewritten as a whole. Someone with database access could regenerate a fresh
chain. Detecting that needs an externally anchored checkpoint. WITNESS calls this
tamper-evident — not immutable, not certified. We would rather state the limit
than let you assume a guarantee we cannot make."

**This is the moment that differentiates the submission.** A judge who has
watched fifty demos claiming "blockchain-grade immutability" will remember the
one that explained its own boundary.

---

## 2:00 — 2:25 · The live integration

**On screen:** Integration tab, Ring Partner API section.

**Say:** "This is a real client for the Ring Partner API — read-only, bearer
token, real HTTP. In this environment we have no Amazon Developer credentials, so
it has not made an authenticated call. WITNESS reports its capabilities as
'Documented', never 'Verified', because that is what we can honestly claim."

**Say:** "Set a token and the same code path runs live. Without one, it refuses
to contact Ring and reports the source as unconfigured — rather than showing you
an empty device list, which is exactly the bug this product exists to fix."

**Say:** "The simulated source is labelled simulated in every payload, every
timeline entry, and on screen. It exists because the Playground cannot produce
withdrawal, encryption transitions, duplicate delivery or late arrival — and
those are the conditions worth demonstrating."

---

## 2:25 — 2:45 · Close

**On screen:** Method tab — the full status taxonomy.

**Say:** "Thirteen statuses. Each has a documented trigger and an explicit list of
what it does not establish. The principle underneath all of them: missing
evidence is not proof that nothing happened."

**Say:** "WITNESS doesn't tell you your home is safe. It tells you exactly how
much your home's systems can actually see — and where the blind spots are. That
is the difference between a notification and something you can rely on."

---

## What not to do

- Do not claim a live Ring call was made. It was not.
- Do not claim the ledger is immutable or tamper-proof. It is tamper-evident.
- Do not claim WITNESS detects emergencies, or protects anyone, or makes anyone
  safer. It reports evidence quality.
- Do not skip the limitation note. It is the strongest thirty seconds in the
  demo.
- Do not show the Method tab for more than a few seconds. It is depth, not the
  story.

## Fallback if the live server will not start

The test suite exercises every behaviour in this script headlessly:

```bash
python -m pytest tests/ -q
```

If the server fails during recording, switch to showing the test output for
`test_source_failure_frame_does_not_become_a_withdrawal` and
`test_full_rewrite_is_not_detectable_by_a_local_verifier`, and narrate the same
story. Do not record a demo that shows something not working and claim it works.
