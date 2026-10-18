# CodeGraph Sentinel — demo video voiceover script

Teleprompter format. `[PASTE]` = paste this command, wait for output, keep talking.
Timings assume natural pace; total ≈ 2 min 20 s + breathing room.
Screen shows the pagerank number — never read digits aloud, they vary per ingest.

---

## Shot 1 — The pitch (0:00–0:20)
**Screen:** README architecture diagram open in browser/editor

> When an AI coding assistant judges how risky a change is, vector RAG can see
> that two functions look similar. What it cannot see is that one of them is six
> calls away from load-bearing.
>
> CodeGraph Sentinel parses a codebase, stores the structure — not the text — in
> FalkorDB, and answers one question before any code is touched:
> **what breaks if this function changes?**

---

## Shot 2 — The kill switch (0:20–0:55) · record this live, it cannot flake
**Screen:** terminal

`[PASTE]` `uv run python scripts/kill_switch_test.py`

> Here's the proof that it's not rhetoric. This is the kill switch.
>
> *(banner 1 prints)* FalkorDB offline — code generation aborted. Zero model
> tokens. The gate refused before the model was even constructed — no API
> call, no token spent.
>
> *(banner 2 prints)* And the sneaky case: the graph dropped and recreated
> empty on a live server. The liveness ping passes — the population check
> catches it. Governance graph empty — aborted again.
>
> Pull the database out, and this isn't a degraded chatbot. It's a wall.

---

## Shot 3 — Live governance, and memory (0:55–2:00)
**Screen:** terminal

`[PASTE]` `uv run python -m codegraph_sentinel.cli`

Type: **Refactor validate_identity**

> Full story now — live REPL, real model. Refactor validate_identity.
>
> *(refusal prints)* Blocked. It quotes the pagerank float first — above the
> calibrated block threshold — then every caller as evidence, including the
> multi-hop chain: run-job, to load-tenant, to validate-identity. That's a
> traversal result, not a similarity score. Don't read the chain list verbatim
> — let the screen carry it; gesture at it.

Type: **Record a permanent rule: identity validation is load-bearing on every entry path.**

> Now watch what makes this governance and not advice: we write the verdict
> into the graph itself, as a permanent, timestamped rule.
>
> *(type `exit`, then open a NEW terminal and start the CLI again — same
> thread, fresh process)*
>
> New session, brand-new process. Same question: refactor validate_identity.
>
> *(refusal prints, now including the Permanent rules line)*
> Blocked again — and here is the rule, with its exact timestamp. No
> re-derivation needed. The constraint lives in the graph, not in the chat.

---

## Shot 4 — Receipts (2:00–2:25)
**Screen:** terminal

`[PASTE]` `uv run python scripts/e2e_scenario.py`

> That was one conversation. Here are all five governance scenarios, headless,
> against the live model: the informational blast radius, the block, the
> permanent rule write, the allow on a leaf function with its clearance
> auto-recorded, and pasted code with no named anchor — governed, not answered.
>
> *(PASS 1/5 … PASS 5/5, RESULT: PASS)* All five pass.

`[PASTE]` `uv run python -m pytest -q`

> And underneath it: forty-five tests. Every claim in the README is pinned.

---

## Shot 5 — Close (2:25–2:40)
**Screen:** back to the diagram

> Vector RAG cannot enumerate transitive callers. It cannot measure centrality.
> It cannot attach a permanent constraint to a node and gate on it later.
> That's why this is built on a graph.
>
> Remove FalkorDB, and there's no product left. That's the point. Thanks.

---

## Pre-roll checklist (do BEFORE recording, not during)

1. `rm -f data/checkpoints.sqlite` — fresh demo thread
2. Wipe + re-ingest the graph (leftover rules would flip shot 3's block):
   `uv run python -c "from codegraph_sentinel.graph_gateway import GraphGateway; gw=GraphGateway.open(); gw.graph().query('MATCH (n) DETACH DELETE n'); gw.close()"`
   then `uv run python -m codegraph_sentinel.ingest`
3. Warm the provider: start the CLI, ask "What functions exist?", type `exit`
4. Record shot 2 first — it's keyless and deterministic; if the provider
   flakes later, only shots 3–4 need a retake
5. `clear` the terminal before every shot; paste, don't type (except the two
   typed REPL lines in shot 3 — those are the demo)
