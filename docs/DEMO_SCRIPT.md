# Three-minute demo script

Target length: **2 minutes 45 seconds**, leaving margin. Every claim in this
script is something the product actually does and can be re-run live.

> **Compliance gate — read before recording.** The Ring track rules say: *"Any
> Ring API, SDK, simulator, or device listed in Ring's developer documentation is
> acceptable. The demo video must show the project working through a simulator or
> an actual Ring device you have for testing."* Ring's official simulator **is**
> the Ring Developer Playground (`developer.amazon.com/ring/console/playground`).
>
> The hackathon organizer's own answer on the Devpost forum (Emerson Sklar,
> Amazon Ring developer advocate) settles how to combine sources, and it is worth
> quoting in full:
>
> > "1) Yes. The simulator is the Ring Developer Playground. Its Package, Vehicle
> > and Motion streams are simulated live-view sessions you consume through the
> > API. … So you won't be penalized for building only on the Playground.
> > 2) Not through the Playground itself. Those three streams are the only
> > simulated sources. What you can do is run your own footage through your app's
> > own processing to show that part of the experience. 3) Yes, combining them is
> > fine. Use the Playground streams to show the integration working end to end,
> > and your own videos to show scenarios the samples don't cover. Just make it
> > clear in your video and README which footage comes from Ring and which is
> > yours, so judges can see the Ring API is actually being used."
>
> **This is the shape this demo must take.** The Playground's three sample
> streams cannot produce a withdrawal, an encryption transition, a duplicate
> delivery or a late arrival — those are the conditions worth demonstrating, and
> the organizer explicitly permits showing them from our own source *provided we
> label which is which*.
>
> So: **Segment A (live)** shows the `ring` source making real, authenticated,
> read-only calls to the Playground — the integration working end to end.
> **Segment B (local)** shows `ring-simulated`, labelled on screen as WITNESS's
> own scenario, for the conditions the Playground cannot produce.
>
> The scenario below is **Segment B**. It runs on WITNESS's own simulated source
> (`ring-simulated`), which is generated from a scenario table and does **not**
> contact Ring. It must never be presented as the Ring simulator.
>
> To record **Segment A**, the Playground is behind an Amazon Developer sign-in;
> obtain a free account, generate a one-click token, and run with
> `RING_ACCESS_TOKEN` set (see README, and `scripts/live_smoke.py` to confirm the
> token works before recording). **Playground tokens are valid for 30 minutes**,
> so record Segment A in one sitting or regenerate the token. If no Playground
> access can be obtained, record Segment B only and state in the video and the
> submission text that the Ring simulator path was not exercised. See
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

## Segment A (live, ~25s) — record this first, with a Playground token

This is the segment the rules require: the integration working end to end against
Ring's simulator. Record it before Segment B, because the token expires.

```bash
export RING_ACCESS_TOKEN="<one-click token from the Playground>"
python scripts/live_smoke.py          # gate: must print a real result, not exit 2
rm -f witness.db
python -m uvicorn witness.api:app --port 8000
```

Then, on camera:

1. Select **Ring Partner API** in the source selector. With a token set it reads
   without the `· not configured` suffix, and the blue simulated banner is
   absent. (The Integration tab shows *Credentials configured*.)
2. Press **Observe now**. The response is a real read-only call to the
   Playground's `/v1/devices`.
3. Show the timeline entry: it names the source, the observation time, and the
   device(s) the Playground returned.
4. **Say:** "That is a real authenticated call to the Ring Partner API — read
   only, no write scope. Whatever the Playground returned is what you see; we
   have not invented a device."

**Do not** claim the Playground produced a withdrawal, an encryption transition,
a duplicate or a late arrival. It cannot. If it returns nothing, show that the
source reports a successful empty response and say so — that is the product
working, not a failure.

**If the token cannot be obtained:** skip Segment A entirely, record Segment B
only, and state on camera and in the submission text that the Ring Playground
path was not exercised. Do not imply otherwise.

---

## Segment B (local, ~2m20s) — WITNESS's own scenario

Everything from here runs on the labelled simulated source.

## B · 0:00 — 0:15 · The problem, in one question

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

## B · 0:15 — 0:35 · What WITNESS is

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

## B · 0:35 — 1:05 · The withdrawal — and the false one it refuses to make

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

## B · 1:05 — 1:30 · A conclusion you can check

**On screen:** click **Evidence detail** on the withdrawal card.

**Say:** "Every assessment carries the rule that produced it, the evidence it
rests on, and the conclusions it does not license. This is the rule id. These are
the observation references. This is not a model's opinion — it is a pure
function over stored observations, so you can replay it and get the same result."

**On screen:** the ledger records are visible in the drawer footer / Integrity
tab.

---

## B · 1:30 — 2:00 · Integrity, stated honestly

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

## B · 2:00 — 2:25 · The integration, stated honestly

**On screen:** Integration tab, Ring Partner API section.

Use the wording that matches what you actually recorded:

- **If you recorded Segment A live:** "You saw this make a real authenticated
  call at the start. It is read-only, bearer token, real HTTP — no write scope.
  Ring's capabilities are reported as 'Documented' rather than 'Verified',
  because 'Verified' is reserved for behaviour we have exercised ourselves."
- **If you did not (no Playground access):** "This is a real client for the Ring
  Partner API — read-only, bearer token, real HTTP. We have no Amazon Developer
  credentials in this environment, so it has not made an authenticated call, and
  WITNESS says so rather than pretending otherwise."
- **What we have actually verified:** with a Playground token supplied through
  the environment, the real adapter returned **HTTP 200** from
  `GET /v1/devices` with one synthetic device, and **HTTP 200** from the history
  endpoint with zero events. That is the whole of what is verified live. It does
  not include a withdrawal, an encryption transition, a duplicate or a late
  arrival — the Playground cannot produce those.

**Say:** "Set a token and the same code path runs live. Without one, it refuses
to contact Ring and reports the source as unconfigured — rather than showing you
an empty device list, which is exactly the bug this product exists to fix."

**Say:** "The simulated source is labelled simulated in every payload, every
timeline entry, and on screen. It exists because the Playground's three sample
streams cannot produce withdrawal, encryption transitions, duplicate delivery or
late arrival — and those are the conditions worth demonstrating. The Ring
organizers confirmed this split is acceptable provided we say which footage is
which, and we do."

---

## B · 2:25 — 2:45 · Close

**On screen:** Method tab — the full status taxonomy.

**Say:** "Thirteen statuses. Each has a documented trigger and an explicit list of
what it does not establish. The principle underneath all of them: missing
evidence is not proof that nothing happened."

**Say:** "WITNESS doesn't tell you your home is safe. It tells you exactly how
much your home's systems can actually see — and where the blind spots are. That
is the difference between a notification and something you can rely on."

---

## What not to do

- Do not claim a live Ring call was made **unless Segment A actually shows one**.
  If it was not recorded, say so.
- Do not present the local segment as the Ring Playground. Say which segment is
  which.
- Do not claim the ledger is immutable or tamper-proof. It is tamper-evident.
- Do not claim WITNESS detects emergencies, or protects anyone, or makes anyone
  safer. It reports evidence quality.
- Do not skip the limitation note. It is the strongest thirty seconds in the
  demo.
- Do not show the Method tab for more than a few seconds. It is depth, not the
  story.

## Running order and the three-minute budget

The rules require the video be **under three minutes**. Two segments do not both
fit at full length, so:

| Plan | Segment A | Segment B | Total |
|---|---|---|---|
| With Playground access | ~25s live | trimmed to ~2m25s | ~2m50s |
| Without Playground access | none | full ~2m45s | ~2m45s |

If you have Segment A, cut the Method-tab beat and the integrity walk-through to
their shortest forms. The live segment is worth more to the Ring track than the
extra depth, because it is the evidence the track requires. Put Segment A first,
so a judge who stops early still sees the real integration.

## Recording checklist

Before you press record:

- [ ] `python -m pytest -q` passes (332 tests).
- [ ] `node --check web/app.js` passes.
- [ ] `python scripts/demo_tamper.py` reports five classes detected, one
      limitation stated.
- [ ] If recording Segment A: `python scripts/live_smoke.py` prints a real
      result and exits 0 — **not** exit 2 (not configured) or exit 3 (refused).
      A token must have been generated within the last 30 minutes.
- [ ] `witness.db` deleted so the run starts from a clean ledger.
- [ ] The simulated-source banner is visible whenever `ring-simulated` is
      selected.
- [ ] You know which segment is which, and you say so on camera.

During the recording:

- [ ] Segment A (if recorded) shows a real authenticated read-only call, and you
      do not claim the Playground produced a condition it cannot.
- [ ] Segment B is described as WITNESS's own scenario, never as the simulator.
- [ ] The empty-response-versus-failed-request distinction is shown, not just
      described.
- [ ] The ledger check shows tampering detected, and the one limitation it
      cannot detect is stated out loud.
- [ ] No emergency, medical, safety or reliability claim is made.

After:

- [ ] Total length is under three minutes.
- [ ] Uploaded publicly to YouTube or Vimeo, in English.
- [ ] The submission text repeats which footage came from Ring and which is
      ours.

## Fallback if the live server will not start

The test suite exercises every behaviour in this script headlessly:

```bash
python -m pytest tests/ -q
```

If the server fails during recording, switch to showing the test output for
`test_source_failure_frame_does_not_become_a_withdrawal` and
`test_full_rewrite_is_not_detectable_by_a_local_verifier`, and narrate the same
story. Do not record a demo that shows something not working and claim it works.
