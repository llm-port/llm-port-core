FROM nvcr.io/nvidia/vllm:26.08-py3

# Air-gapped runtime defaults and telemetry suppression
ENV HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1 \
    HF_DATASETS_OFFLINE=1 \
    VLLM_NO_USAGE_STATS=1 \
    VLLM_DO_NOT_TRACK=1 \
    RAY_DISABLE_USAGE_STATS=1 \
    DO_NOT_TRACK=1

# Install Ray 2.58.0, pyarrow, and dashboard/metrics agent dependencies
# (preserving NVIDIA-optimized vLLM, PyTorch, and CUDA stack)
RUN pip install --no-cache-dir --no-deps ray==2.58.0 pyarrow && \
    pip install --no-cache-dir aiohttp-cors opencensus opentelemetry-exporter-prometheus

# Fail the build if the per-node metrics path cannot load.
#
# Worker nodes export Prometheus metrics through Ray's dashboard agent, whose
# `reporter_agent` imports `opencensus` at module scope (reporter_agent.py:15)
# and pulls in `ray._private.prometheus_exporter`, which needs it too.  When
# that import fails the agent never binds --metrics-export-port, so the head
# looks healthy while every worker stays silent -- which is exactly how the
# image shipped to the DGX pair: `opencensus`, `aiohttp-cors` and
# `opentelemetry-exporter-prometheus` were all absent from the deployed image
# even though the line above installs them, and nothing caught it until the
# two-node certification's worker scrape timed out.  Importing the real
# modules here is the only check that would have.
RUN python3 -c "import aiohttp_cors, opencensus; \
import ray._private.prometheus_exporter as pe; \
import ray.dashboard.modules.reporter.reporter_agent as ra; \
print('metrics stack OK:', opencensus.__name__, aiohttp_cors.__name__, pe.__name__, ra.__name__)"

# Install LLM.Port Ray Runtime helper package and validation assets
WORKDIR /opt/llm-port/runtime
COPY setup.py /opt/llm-port/runtime/
COPY llm_port_ray_runtime /opt/llm-port/runtime/llm_port_ray_runtime/
COPY fixtures.json test_local_validation.py /opt/llm-port/runtime/

RUN pip install --no-cache-dir /opt/llm-port/runtime && \
    llm-port-ray-runtime versions

# The helper is the host/runtime control contract (4B): the Node Agent invokes
# these verbs over `exec` and never imports Ray itself.  A build whose helper
# lacks a verb would leave the agent unable to deploy or tear down a Serve
# application, so the surface is asserted at build time rather than discovered
# on a node at deploy time.
RUN for verb in versions probe cluster-status serve-status run-serve-app delete-serve-app; do \
        llm-port-ray-runtime "$verb" --help > /dev/null 2>&1 || \
            { echo "helper is missing verb: $verb"; exit 1; }; \
    done && echo 'helper control contract OK'

WORKDIR /workspace

CMD ["llm-port-ray-runtime", "versions"]

