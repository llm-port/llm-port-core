# Hosting a model from the marketplace

**Inference → Model marketplace** is where you pick a model to run. It shows
models from Hugging Face, says whether each one fits your cluster, and puts it
on the cluster in one dialog. vLLM's settings are part of that dialog, with
the ones that matter for the model already filled in.

---

## Three ways to look

- **Recommended.** Models we have picked, grouped by what they are for: trying
  things out, general chat, coding, reasoning, vision and embeddings. A model
  marked **Tested here** has been served on the hardware named in its tooltip.
- **Search Hugging Face.** Search the Hub by name. You can filter by task
  (chat, embeddings, vision) and sort by trending, downloads, likes or
  newest. Models vLLM cannot serve are hidden, with a button to show them and
  the reason. Examples: GGUF files (they are for llama.cpp and Ollama), MLX
  weights, and repositories with no weights.
- **On this server.** Models this server has already downloaded. Hosting one
  of these downloads nothing.

## What "fits" means

Pick a cluster under **Fit for cluster**. Each card then says how the model
fits it:

| Badge | Meaning |
|---|---|
| **Shares a GPU** | Small enough that several copies, or other models, share one accelerator. Each copy takes only its share of the memory. |
| **Fits on N GPUs** | One copy needs N accelerators, split with tensor parallelism. |
| **Fits, but not right now** | It fits the cluster, but other models are using the memory it needs. |
| **Too large** | Even every accelerator in the cluster together cannot hold it. |
| **Size unknown** | Hugging Face does not say how large it is. |

The estimate adds three things for each accelerator: the model's weights,
split across the accelerators; the context cache for the chosen context
length, from the model's own configuration; and about 1.5 GiB of working
memory. The total must stay within 90% of the card. It is an estimate. The
engine settings step shows the numbers before anything starts.

## Hosting

**Host** opens the same dialog from every card, from a cluster's page and from
**Deployments**:

1. **Where.** The cluster, the size of each copy (a share of one accelerator,
   one, or several), and how many copies. The fit check picks the size. You
   can choose another.
2. **Engine settings.** How vLLM runs the model, in plain words. See below.
3. **Review.** The deployment's name and the name it has in chat. The dialog
   also says whether the server must download the model first.

The server downloads the model, copies it to the machines and starts the
copies. The deployment page shows each step. Nothing waits in the dialog.

## Engine settings

The engine settings step, the deployment page (**Engine settings → Change**)
and the legacy runtime screens all use the same editor.

- **Suggested for this model.** When the vLLM project publishes a recipe for
  the model on [vLLM Recipes](https://recipes.vllm.ai) (Apache-2.0), its
  recommended arguments come first: parsers, required flags, and overrides
  for the accelerator generation when the cluster's cards clearly belong to
  it. Otherwise tool calling and reasoning parsers are chosen for the model
  family. The context length is set to what fits. On a shared accelerator the
  memory share is set to the copy's share. Each suggestion says why it was
  made.

  Some recipe arguments are not used, and the dialog lists them: paths into
  vLLM's source tree (they are not in the runtime image), JSON options, and
  the tensor-parallel size (the fit check decides that). Optional recipe
  features appear as checkboxes. The server fetches recipes itself and keeps
  them for a day. A server without internet access has none and falls back to
  the family rules.
- **Presets.** *Balanced*, *Long context*, *Many users* and *Low memory*
  change only the settings they are about.
- **Sections.** Memory, throughput, abilities (tools, reasoning, embeddings)
  and compatibility. Each shows what vLLM does when it is left alone.
- **All vLLM settings.** Every flag of the vLLM version the cluster runs,
  searchable, for anything the sections do not cover. You can also type extra
  flags as text. Anything that could escape the command is refused.
- **Warnings** appear before a setting that will not start: a context longer
  than the model supports, a memory share above 0.95, a tool parser without
  automatic tool choice, or remote code.
- **Preview.** The `vllm serve …` command these settings make.

Changing engine settings on a running deployment restarts its copies. The
dialog says so before you save.

## Hugging Face access

Public models need no token. Gated models need a Hugging Face token from an
account that has accepted their license. This includes Llama, Gemma and some
Mistral models. A token also raises download limits.

Set the token under the **Hugging Face** chip at the top of the marketplace,
or in **Settings → LLM → Hugging Face**.

- The server checks the token with Hugging Face before saving it, and refuses
  one that Hugging Face rejects. If Hugging Face cannot be reached, the token
  is saved and marked as unchecked.
- It is stored encrypted with the settings master key, and no screen or API
  returns it. You see only the account it belongs to. If the token can also
  write, a note says so: a read token is all the server needs.
- It is used only by the server itself: to search, to read model details and
  to download. Cluster machines get their models from the server and never
  receive the token. A legacy container gets it only when it is allowed
  onto the network to fetch model code (`trust-remote-code`).
- The server will not save a token while it still uses the default settings
  master key. `llmport deploy` generates a random one for each installation.
- Setting and removing the token are recorded in the audit log, without the
  token.
- `LLM_PORT_BACKEND_HF_TOKEN` in the environment still works. A token saved
  in the interface takes its place.

## Without internet access

A server that cannot reach Hugging Face still shows the recommended list and
the models it keeps, with sizes estimated from their names. Search is not
available. You can host models that are already on the server, and models
added from a local path under **LLM → Models**.
