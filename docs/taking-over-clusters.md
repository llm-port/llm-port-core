# Taking over clusters your machines still run

If an LLM.Port server is rebuilt without its database, or restored from a
backup older than its clusters, its machines keep running the clusters and
models it started. The new server does not know about them. Recreating them
restarts every model. While the old apps run they also keep their GPUs, so the
recreated models often don't fit.

Taking over records a running cluster in the new server as it is: its
machines, and each model it serves under its original deployment id. No model
restarts, either during the takeover or afterwards.

---

## What you need

- **The machines approved in this server.** Their agents still hold
  credentials for the old server. Re-run the install line from this server's
  **Machines → Add a machine** page on each machine and approve the requests.
  Re-installing the agent does not touch the running cluster, its models, or
  the cluster token.
- **Agent 0.1.12 or later** on the machines. Older agents can't describe their
  cluster. The install line brings them up to date.

## Taking over

1. Open **Clusters**. When a machine runs a cluster this server doesn't
   manage, a card called **Running on your machines, not managed here** lists
   it: its machines, and each model with its copies and GPUs.
2. Choose **Take over…**.
3. Give the cluster a name. For each model, give the name clients ask for at
   the gateway. The old server kept these names only in its own database, not
   on the machines, so they have to be entered again. The suggestion is the
   model's own name in lower case.
4. Choose **Take over**. It takes a few seconds. The cluster then opens with
   its models running.

## What happens

The new server:

1. **Reads the cluster from Ray on the machine.** It gets the members and,
   for each model, the configuration it was deployed with.
2. **Rebuilds each deployment from that configuration.** The deployment keeps
   its original id, which is part of the app's name in Ray (`llmport-<id>`).
3. **Checks the rebuilt configuration.** It asks the cluster to compare the
   configuration it would deploy for each model with what runs, using Ray's
   own configuration model. If anything differs, the takeover stops, names the
   difference, and records nothing. Recording it anyway would make the
   reconciler redeploy the model, which is a restart.
4. **Takes over the cluster's token.** The machine hands the token over, and
   the server stores it encrypted with its settings key. It never appears in
   plain text in the command log.
5. **Records the cluster as already up, and each model as applied.** The
   reconciler's next pass only checks health and publishes each model to the
   gateway under its name.

The cluster is found on the network Ray runs on. For the DGX pair that is the
200 Gb/s RoCE link, not the management network. That network is recorded as
the cluster's interconnect, so the health check finds every member there.

## What is not carried over

- **Names shown in the console and gateway aliases.** You enter them again at
  step 3.
- **History.** Logs, metrics, and past requests stay with the old server's
  database.
- **Apps LLM.Port did not deploy.** An app whose name is not `llmport-<id>`
  keeps running and is listed, but is not managed.
- **A second model in one app.** LLM.Port deploys one model per app. For an
  app with several models, only the first is taken over, and the card says so.

## When a cluster cannot be taken over

The card says why, and **Take over…** stays disabled:

| Message | What to do |
|---|---|
| *spark-9 is in the cluster but not in this fleet* | Re-run the install line on that machine and approve it. |
| *… already belong to a cluster this server manages* | That machine is in a cluster here. Remove it from that cluster first. |
| *This server already has the deployment of …* | The model is already managed here. Nothing to take over. |
| *… could not say what it serves (its agent may be older than 0.1.12)* | Re-run the install line on that machine. |

If the check at step 3 finds a difference, the dialog shows the setting and
both values, for example
`llm_configs.0.engine_kwargs.max_model_len: running 8192, would deploy 32768`.
Nothing was recorded. This means the model was deployed by a version of
LLM.Port that compiled its settings differently. You can either redeploy it
from this server (it restarts), or leave it running unmanaged.
