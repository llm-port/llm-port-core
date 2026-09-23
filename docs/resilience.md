# When something fails

What LLM.Port does when part of a cluster fails, what you see while it does
it, and how long it takes. The walk-through that sets up the cluster used here
is [Adding a machine and serving a model on it](onboarding-a-node.md).

---

## How LLM.Port notices

Every minute it looks at each cluster that is up and each model that is
serving. It also looks when a machine reconnects and when a backend starts.
A look is one status question to the cluster's head and one to Ray Serve. It
restarts nothing unless something is wrong.

It also looks twice before restarting anything. A cluster whose head does not
answer shows **Degraded** and says so:

> Ray on spark-3201, the cluster's head, is not running. Checking again before
> restarting anything.

Twenty seconds later it looks again. A busy head that was slow to answer is
not a dead one, and restarting a cluster that is serving would cause the
outage the check is there to catch.

Until now nothing looked again once a cluster read "ready". A Ray head that
died left the cluster **Ready** and its models **Running**, and every request
failed until someone changed something.

## What happens for each failure

| What fails | What you see | What LLM.Port does |
|---|---|---|
| A model copy crashes | The model reads fewer copies, then all of them | Nothing: Ray Serve restarts the copy |
| Ray stops on a worker | **Degraded**: "spark-ts3202 has dropped out of the cluster" | Takes the worker out and joins it again. Copies on the head keep serving |
| Ray's head process dies | **Degraded**, then "Restarting the cluster (attempt 1 of 3)" | Stops Ray everywhere, starts a new head, joins the workers, applies the models again |
| The head machine reboots | The machine reads **Offline** (after 30 s), then as above once it is back | As above: the runtime comes back without Ray in it |
| A worker's fabric link goes down | As for Ray stopping on a worker | As for Ray stopping on a worker, once the link is back |
| The agent on a machine restarts | Nothing | Looks at the cluster, finds it healthy, restarts nothing |
| The LLM.Port backend restarts | Nothing | Waits up to two minutes for the machines to reconnect, then looks. Nothing is restarted or re-applied |
| The gateway restarts | Chat fails while it is down | Nothing needed: routes are stored, not held in memory |

### Measured on the DGX pair, 2026-09-23

Every failure above was caused by hand on the two DGX Sparks while
`llm_port_backend/scripts/resilience/faults.py watch` recorded what LLM.Port
showed and whether a real chat through the gateway got an answer. The model
was Qwen2.5-0.5B-Instruct on two copies, one per machine.

Times are from the first sign of trouble in LLM.Port.

| Failure | First sign | Cluster ready again | Model on both copies again | Chat failed for |
|---|---|---|---|---|
| Agent restart (worker) | none: nothing changed | - | - | never |
| Model copy killed (worker) | model reads 1 of 2 | - | 41 s | never |
| Ray stopped on the worker | cluster Degraded | 51 s | 3 min 18 s | never |
| Ray's head process killed | cluster Degraded, 22 s before chat failed | 72 s | 3 min 15 s | 2 min 43 s |
| Head's runtime restarted | cluster Degraded, 88 s after chat failed | 63 s | 3 min | 4 min 23 s |
| Head machine rebooted | cluster Degraded, 75 s after the machine reconnected | 50 s | 3 min 10 s | 5 min 36 s, about 1.5 min of it the reboot |
| Worker's fabric link down for 3 min | worker's copy unhealthy; cluster Degraded 67 s later | 2 min 8 s | 4 min 51 s | never |
| Backend restarted (twice) | none: nothing changed | - | - | never |
| Gateway restarted | chat fails | - | - | while it was down |

Most of each outage is the model loading again, about two minutes for this
one. Re-forming the cluster itself took 15 to 20 s each time, and each worked
on its first attempt.

Found by these runs and fixed the same day:

- With the head down, the deployment page asked the head directly on every
  refresh. Each question took 30 s to fail, they piled up on the head's agent,
  and the cluster's own check waited behind them: the first head failure did
  not recover until this was fixed. The page now shows what was last recorded,
  and identical questions to one machine share a single request.
- A head that rebooted read **Ready** for two and a half minutes, covered by
  the two-minute allowance meant for backend restarts. That allowance now
  applies only in the first minutes after the backend starts; otherwise a
  machine is reported offline after 30 s.
- While a model was being applied again after a restart, its page still said
  "Checking again before restarting anything". It now says it is being
  applied again from the moment it is.

The last two fixes are covered by tests and were not re-run on the hardware.
The path where three restarts fail and the cluster reads **Failed** is also
covered only by tests: the fabric link came back before a restart was tried
against it.

### While a cluster is restarted

- A model on it reads **Not serving** with the cluster's reason, and the
  gateway stops routing to it.
- Nothing is applied to a cluster that is not ready.
- Once the cluster is back, each model is applied again. It can tell that it
  has to be: the new head is a different Ray node from the one the model was
  applied on.
- A model copy on a machine that is still up keeps serving while a worker is
  rejoined. The model reads **Degraded** with "Serving on 1 of 2 copies".

### When it cannot bring a cluster back

After three attempts, the second 30 s after the first and the third 60 s
after that, the cluster reads **Failed**:

> Ray on spark-3201, the cluster's head, is not running. LLM.Port restarted it
> 3 times and it did not come back (last: …). It tries again every 15
> minutes; use Try again once the cause is fixed.

It keeps trying every 15 minutes because the cause may be fixed without
anyone telling LLM.Port, for example a network link that comes back.
**Try again** on the cluster page starts over at once.

### What it does not restart on

- **A look nobody answered.** When the head's agent does not reply, nothing is
  known about Ray, so nothing is restarted. The model keeps its state and
  says "Could not check the model just now".
- **A machine that is reconnecting.** Nothing is applied while any machine in
  the cluster is offline. See the next section.

## A restart of the backend no longer restarts your models

The first time these checks ran, the backend happened to be restarting, and
the look came while the machines were reconnecting. The model read as absent
from them, so its config pointed at the Hugging Face name instead of the
local copy. That changed the config, and the model was applied again: both
copies restarted, twice.

Two changes stop this:

1. A model that is already applied is only looked at. Its config is not
   rebuilt unless you change the deployment.
2. Nothing is applied while a machine in the cluster is offline. The model
   says "Waiting for spark-3201 to reconnect before applying" and goes ahead
   once it has.

## Settings

| Setting | Default | What it does |
|---|---|---|
| `LLM_PORT_BACKEND_INFERENCE_HEALTH_CHECK_SEC` | `60` | How often clusters and models that are up are looked at. `0` turns the checks off |
