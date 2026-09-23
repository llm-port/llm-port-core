# Where onboarding still costs the operator something

Written after walking `onboarding-a-node.md` end to end against the DGX Spark
pair, doing every console step in a browser and only touching a shell where
the guide says to. Everything below is something that happened, not something
that might.

The benchmark is AINode: one command, no console visit, machine joins. We are
further along than that comparison usually suggests — the cluster wizard, the
network ranking and the runtime resolution are all things it has no answer
for — but the first ninety seconds are worse, and the first ninety seconds are
what people judge.

---

## Status

Every item below has since been fixed. The analysis is kept as written,
because the reasons are the useful part.

| # | Friction | What changed |
|---|---|---|
| 1 | Two commands, one of them `sudo` | One copyable line. Run without root, the installer goes to `~/.local/bin` and a `systemd --user` service with linger -- no password anywhere, and it survives logout and reboot |
| 2 | The console told the machine to install from itself | The backend reports its own reachable addresses by interface; loopback is never offered |
| 3 | Nothing tells you a machine is waiting | A badge on *Machines* (and a dot on its group when collapsed), and a banner with **Review** on the page |
| 4 | One thing, four names | *Machine* everywhere a person reads it, in all five languages |
| 5 | The model must be on the server first | An offline-only deployment starts the server's download itself and waits on it, showing its progress; a failed download is reported, not retried on a loop |
| 6 | 15 GB, no progress, no resume, every machine from the server | Resumable (served from a one-time export with `Range`); progress per machine as a percentage bar, rate and time left; the server serves one machine and that machine seeds the rest over the cluster fabric, falling back to the server |
| 7 | A permanent failure retried forever | Failures carry a code; permanent ones wait for their inputs to change (re-checked hourly), transient ones back off; the reason reaches the page, with **Try again** |
| 8 | Installed somewhere not on `PATH` | The installer says so |

### Found on the second walk-through, and fixed

The guide was walked again end to end after the fixes above: both DGX nodes
installed without sudo, approved from the badge, clustered over RoCE with the
image seeded node to node, a chat model deployed, and a reply in chat. These
came up on the way.

| Where | What happened | What changed |
|---|---|---|
| Step 1 | Re-running the install line to upgrade filed a fresh join request for a machine already in the fleet | The agent asks the server whether its credential is still a member; if so it skips the join and restarts the service |
| Step 1 | An upgrade installed the new binary and kept running the old one: `enable --now` does not restart a running service | `start` enables, then restarts |
| Step 1 | *Skip the approval step* opened a card 0px tall whenever the panel was taller than the window | The drawer's children no longer shrink; the paper scrolls |
| Step 1 | A rootless agent could not start a cluster: the token directory under `/tmp` was left root-owned by an older install | The agent picks the first token directory it can actually write |
| Step 1 | Icon buttons in the machine list had no accessible names | Each has a label |
| Step 4 | The websocket dropped while a machine loaded the 11 GB image: the server's 20s pong timeout was shorter than a busy agent's reply | Both sides allow 120s |
| Step 4 | A machine whose agent was stopped stayed *healthy* | Marked offline when its stream closes, or after five minutes of silence |
| Step 4 | A cluster whose machines were all gone could not be deleted: it could not be stopped first | **Delete** on the cluster page; when no member is reachable it deletes without stopping |
| Step 6 | The model picker listed the same model three times, one a failed download, indistinguishable; the failed one has no files, so deploying it could only fail | Failed downloads are not offered; duplicates show repository and date; downloading a repository already kept returns the existing record |
| Step 6 | The deployment page kept saying "syncing, on 0 of 2 machines" next to *Serving* | Its cards follow the deployment while it comes up and on every phase change |
| Step 6 | Scale, Stop or Save blanked the cluster, machines and model files on the deployment page | The page's refresh used the first render's loaders; it now uses the current ones |
| Step 7 | A deployment made from the wizard served and never appeared in chat: publication needs an alias, never invents one, and the wizard never set one | The wizard asks **Offer in chat as**, prefilled from the model; an existing deployment has **Offer in chat** on its page, which does not restart it |

### Found scaling the model, and fixed

Scaling the chat deployment from one copy to two from the console, then to
three on a cluster with two accelerators, then back to one.

| What happened | What changed |
|---|---|
| After **Apply** nothing happened for 93 s, while the page still read "1 / 1": the reconciler only looked every 30 s, and each pass waited up to 60 s for a scaling app to report RUNNING | A committed change wakes the reconciler; a serving app is reported at once and followed pass by pass. The change now starts within about 12 s |
| "wanted" in **Copies (ready / wanted)** was ready + pending, so a request for 3 read "2 / 2" | It is the number asked for, shown the moment Apply is pressed |
| For the whole scale-up the deployment read **Starting**, "app application DEPLOYING:", while its first copy served throughout | It stays **Serving**: "Serving on 1 of 2 copies; 1 more starting." |
| Three copies on a two-accelerator cluster read the same, forever, with no reason | "1 cannot start: each copy needs 1 accelerator, and this cluster has 2, so 2 fit. Scale to 2, or add a machine" -- and the dialog warns before Apply |
| The Scale dialog said "Replicas" and "replaces the spec's scale block … on the next reconcile pass" | "Copies", what they are for, and how many fit on this cluster |
| The number being typed in the dialog was reset by the page's 10 s refresh | It is set when the dialog opens |
| "Last checked: pending (gen 3, observed 2)"; a **Reconcile** button | "checking now"; **Check now**, as on the cluster page |
| A reached count still settling read "Serving on 1 of 1 copies; 0 more starting." | "Serving on 1 copy; finishing the change." |

Not shown: which machine each copy is on. Ray's status reports copy counts
per deployment, not placement, and adding it means changing the runtime
image.

### Found looking for a machine's logs, and fixed

| What happened | What changed |
|---|---|
| Since both DGX nodes were reinstalled with the one-line install, none of their logs reached Loki: the installer writes no Loki address, and an agent without one forwards nothing, silently | The agent asks the backend where to send logs (`GET /api/node-files/log-sink`); the backend answers with Loki's port on the address the machine reached it on, or `LLM_PORT_BACKEND_AGENT_LOKI_URL`. Agent 0.1.10 |
| The Logs page offered five hard-coded filters under their raw label names; a model's logs (labels `app` / `deployment` / `replica`, no `container`) could not be filtered, and the default query `{container=~".+"}` hid them entirely | Filters come from the labels in the chosen range, named for people (Machine, Source, Deployment, Component, Copy), each narrowed by the others; the default query matches every stream |
| "Container" was the machine's whole system journal (`node-spark-3201`), and the table's Container column linked it to the Containers page | Machine and Source columns; only a real server container links to the Containers page |
| No way to go from a machine to its logs | **Open in Logs** on the machine's page (`/admin/logs?host=…`) |
| The agent logged every push to Loki, and shipped those lines to Loki | httpx request logging off in the agent |

Still open: a join request shows the machine's hostname as its address, not
the IP it connected from; and the gateway still holds an alias
(`qwen2.5-0.5b`) and a disabled instance for a deployment that no longer
exists. How that deployment was removed has not been traced, so whether
deleting one leaks them is not yet known.

---

## What is already good, and should not be traded away

- **The installer is generated per deployment.** Address, build, version and
  digest are filled in before the script leaves the server. Nothing is looked
  up by hand, and the checksum is verified by the script rather than by eye.
- **Air-gapped is not a separate path.** The script tries the published
  release, falls back to this backend's own copy, and verifies the same digest
  either way. In the walkthrough the GitHub release 404'd and the fallback
  carried it with no operator involvement at all.
- **The runtime image is pushed from the server, not pulled from a registry.**
  A node needs no internet and no registry credentials.
- **Creating a cluster starts it.** There is no separate "start" step to
  forget. (The guide described one; the product is simpler than its docs.)
- **The network step explains itself.** It ranks each link with its reasons —
  link speed, dedicated fabric, carries the default gateway — rather than
  choosing silently. On the pair it correctly preferred 200 Gb/s RoCE over the
  1 Gb/s management link.

---

## The friction, in the order an operator meets it

### 1. It is two commands, and one of them is `sudo`

```bash
curl -fsSLO http://10.88.10.220:8000/api/install/llmport-agent.sh
sh llmport-agent.sh --join
```

Two lines means two pastes, and the second wants root. On a machine whose
sudo needs a password — both DGX nodes — a non-interactive run dies after
installing the binary, having already half-succeeded.

**Do:** offer the whole thing as one copyable line, `curl … && sh …`. It is
one paste and still lands on disk before running, so the `curl | sh` argument
in `install/__init__.py` is untouched.

**Do:** stop asking for privilege that is not needed. *(Done during the
walkthrough — `--install-path ~/.local/bin/llmport-agent` no longer invokes
sudo, because the directory is already the user's.)* What still needs root is
the systemd unit, and that is worth saying out loud rather than discovering
when the agent dies at logout.

**Consider:** a `--user` mode that installs a `systemd --user` unit and needs
no root at all. That would make the common case genuinely sudo-free.

### 2. The console told the machine to install from itself

The Add Node panel built its command from the browser's URL, so it read
`curl -fsSLO http://localhost:5173/...`. Run on the machine being added, that
fetches from *that* machine and finds nothing. The guide warns about this in
prose — "as this machine sees it, not localhost" — which is a warning that the
UI was generating the wrong thing.

*(Fixed during the walkthrough: the backend now reports its own reachable
addresses with the interface each belongs to, the console offers them, and it
refuses to hand out a loopback address.)*

The general lesson is worth keeping: **the console must not infer the node's
view of the network from the operator's.**

### 3. Nothing tells you a machine is waiting

The guide says to approve in the fleet. The fleet page has no badge, no
banner and no row — the pending machine is only visible after clicking **Add
Node**, which reads like "start something new", not "finish what you
started". An operator who ran the install and came back to approve has
nothing to go to.

**Do:** a count on the Nodes nav item and a banner on the fleet page whenever
a join request is pending. The data is already there; only the surfacing is
missing.

### 4. One thing has four names

| Where | What it says |
|---|---|
| Navigation | Nodes |
| Page title | Node Fleet |
| Button | Add Node |
| Panel opened by that button | Add a machine |
| This guide, until now | Machines |

Pick one. "Machine" is the friendlier word and is already what the panel says
at the moment of highest confusion; the i18n keys exist in five languages, so
this is a copy change rather than a refactor.

### 5. The model must be on the server first, and nothing says so until it is
too late

Step 5 exists because a cluster's runtime cannot reach the internet. The guide
calls skipping it "the most common way a deployment stalls" — which is an
admission that the product lets you start something that cannot finish.

**Do:** when a deployment names a model the server does not hold, offer to
fetch it as part of the deployment instead of stalling on "Copying the model".
The deployment already knows the model; it should not need the operator to
have known the ordering.

### 6. The first cluster start is a 15 GB transfer with no progress and no
resume

This is the longest part of onboarding by far, and during the walkthrough it
failed: the transfer saturated the link, the websocket keepalive timed out at
20 s, and the control channel carrying the command was dropped **by the
transfer it had started**. `docker load` got a truncated stream, the command
stayed `running`, and the cluster sat at "preparing" explaining nothing.

*(Fixed during the walkthrough: the keepalive now tolerates a saturated link,
and a command in flight when its stream closes is failed immediately with a
reason, rather than waiting out a five-minute silence budget.)*

Still open, and worth doing:

- **Resume.** A 15 GB transfer that restarts from zero on any blip is a bad
  bet on a site link. Range requests would make it restartable.
- **Progress the operator can see.** The agent emits progress events; the
  cluster page shows "Preparing 1 machine". A percentage and a rate turn a
  worrying ten minutes into an understood one.
- **Node-to-node seeding.** Adding the second machine to a cluster ships the
  same 15 GB from the server again. Nodes that already hold the image could
  serve it.

### 7. A failure that can never succeed is retried forever

The cluster start above failed on a digest the catalogue pins and the artefact
cannot satisfy. That is a deterministic failure: nothing about retrying it
changes the answer. It was nevertheless re-issued every ~90 seconds for three
hours -- **303 identical failures**, measured -- with no backoff and no point
at which the system concluded anything.

It is cheap — the agent sees the image is already present, checks the digest
and gives up in under a second, so nothing is re-transferred — but the cluster
page shows "Failed" with no hint that it is looping, and the command history
fills with identical rows that bury the first, informative one.

**Do:** back off on repeat, and distinguish "not yet" from "not ever". A
digest that does not match will not match on the ninetieth attempt either,
and saying so once is more useful than saying it eighty times.

### 8. `llmport-agent` is installed somewhere not on `PATH`

A rootless install lands in `~/.local/bin`, which is not on `PATH` on a DGX
OS image, so the next documented command is `command not found` for a file
that installed perfectly. *(The installer now says so.)*

---

## If we want AINode's first ninety seconds

In order of value for the work:

1. **One line, no sudo, no console visit.** Generate a token-bearing command
   in the console — it already exists behind *Provisioning this
   automatically?* — and make that the headline, with approve-by-code as the
   fallback for "I cannot paste into that machine". Today the defaults are the
   other way round.
2. **Surface pending joins** (§3). One badge.
3. **Make the image transfer resumable and visible** (§6). This is the only
   step measured in tens of minutes.
4. **Fetch the model as part of deploying it** (§5).
5. **One name** (§4).

Items 1, 2 and 5 are a day's work between them and remove most of the
felt difference. Items 3 and 4 are where the remaining time actually goes.
