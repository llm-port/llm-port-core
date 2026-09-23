# What the gateway does with a chat request

A chat request passes through up to eight steps before it reaches the
model: limits, routing, RAG, the session's history, skills, PII, tools and
the model slot. Each step had tests of its own. Run together, which is how
the chat page uses them, they had bugs no single test could see, and PII
made every chat close to a second slower.

Checked on the workstation on 2026-09-23 with every module running (PII,
RAG Lite, skills, MCP), a chat session, and the `qwen-chat` deployment on
the DGX pair.

---

## The order

| # | Step | What it does |
|---|---|---|
| 1 | Limits and routing | Checks rate limits, finds the routes for the model name, and refuses a request of the wrong kind (a chat request to an embedding model) |
| 2 | RAG | Searches RAG Lite for context, as the user who asked |
| 3 | Session | Loads the chat's history, memory and attachments, and saves the new user message |
| 4 | Retrieved context | Added to this request only, after the session step, so it never becomes history |
| 5 | Skills | Adds the skills that apply, as system messages |
| 6 | PII | Scans everything that is about to leave: the message, the history, the retrieved context |
| 7 | Tools | Fetches the MCP tool list |
| 8 | Model slot | Taken only now, when nothing else is left to wait for |

The answer then comes back through PII (tokens back to names, streamed or
whole), is saved to the session, and is written to the audit log.

The model slot is taken last. It used to be taken first and held while RAG,
the session and the PII scans ran, so the model sat idle, reserved for a
request that was still gathering its context.

## PII

With PII in tokenize mode, a name leaves as a token and comes back as the
name:

| | What it contains |
|---|---|
| Sent to the model | `I am [PERSON_1]` |
| Shown in chat, and saved to the session | `I am Alice Meyer` |
| Kept in the trace | `I am <PERSON>` |

![A streamed answer in chat, with the name restored](images/pipeline/chat-pii-name-streamed.png)

The chat page always streams. Tokens are put back as the answer streams,
including a token that arrives split across two pieces (`[PER`, then
`SON_1]`): text that may be the start of one waits until the rest arrives.

Retrieved context is scanned too. "Munich" in a RAG document leaves as
`[LOCATION_1]`, and the answer still says Munich.

**What PII costs.** Text is scanned once per request, and the trace's copy
is made from that scan. The PII service remembers the result for each piece of
text it has seen, keyed by a hash of the text and the scan settings. It keeps
entity types and positions, never the text. So a chat's history is not
scanned again on every turn: only the new message is.

| On a 10-turn chat | Before | After |
|---|---|---|
| PII scans per request | 2 | 1 |
| PII time per turn | 162 ms, growing to 444 ms by turn 10 | 30–50 ms, flat |
| Time the gateway adds per turn | 345–591 ms | 140–210 ms |

The model's own time is not in these figures.

## Found and fixed

| What | Effect | Fix |
|---|---|---|
| A streamed answer kept the tokens | The chat page showed "Hello [PERSON_1]". The streaming path dropped the token mapping | Streamed and whole answers share one path; tokens are restored as the answer streams |
| The streamed answer was saved with the tokens | The history said "[PERSON_1]", and the next turn sent it to the model like that | The answer is saved as the user saw it |
| Retrieved context was saved as chat history | Each turn's RAG results became a system message in the history, sent again on every later turn and scanned by PII each time. This is what made PII look like 874 ms | Context is added after the session step, to this request only |
| History over its token budget kept the oldest turns | The newest turns, the ones the question follows from, were dropped | The newest turns that fit are kept |
| RAG Lite search was called without a token | Every search was refused (401), and the answer went out without its context. Only the gateway's log said so | The search runs as the user who asked |
| The gateway ignored the RAG Lite switch | Switching RAG Lite on in Modules did nothing for chat | The gateway reads the switch at startup |
| Skills were never used | The gateway's list of modules did not include skills, so no skill reached a chat | Skills is on the list |
| Publishing a skill failed | Publish returned 500 and nothing was published | Fixed in the skills service |
| Assigning a skill failed | The skills service expected `/assign`; the backend sends `/assignments` (405) | The skills service takes `/assignments` |
| The model was told the wrong placeholders | In redact mode it was told to expect `[REDACTED_PERSON]`; PII writes `<PERSON>` | The instructions name what PII writes |
| The first chat after PII started was slow | spaCy finished loading on the first request (~0.9 s) | PII warms up when it starts |

## Every module in the dev environment

```
llmport dev up --modules pii,mcp,skills
```

runs PII, MCP and skills on the host next to the backend and gateway, on
127.0.0.1, and points the gateway and backend at them:

| Module | Address |
|---|---|
| PII | http://127.0.0.1:8003 |
| MCP | http://127.0.0.1:8007 |
| Skills | http://127.0.0.1:8008 |

Without `--modules`, the modules switched on with `llmport module enable`
run. A module that is not started is switched off in the gateway and
backend, so no request is sent to a port nothing listens on.

- The first start downloads PII's spaCy model (about 400 MB).
- PII runs on Python 3.13. spaCy does not load on 3.14.
- In dev, do not press **Enable** for PII, MCP or skills on the Modules page.
  It starts their containers, which the host-run gateway cannot reach.
  RAG Lite has no container: enable it there.

## Next

Once the slot is no longer held, the steps before the model can run side by
side: RAG, the session and skills do not depend on each other. That would
save about 100 ms per request. The other remaining item is the gateway's
own routing lookups, which it makes against the database on every request.
