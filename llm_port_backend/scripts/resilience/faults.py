"""Inject one failure into a live cluster and time how LLM.Port comes back.

    python faults.py <scenario> [--deployment qwen-chat] [--timeout 900]

Scenarios (see ``SCENARIOS``) break one thing on the head or a worker over
SSH (key only), or restart a local dev process by touching its source. Every
five seconds the run records what LLM.Port says -- cluster status, the
deployment's phase and copies -- and whether a real chat through the gateway
answers, and prints each change as it happens. It stops once everything is
healthy again (or after ``--timeout``) and writes a JSON report beside itself.

This breaks the cluster on purpose. Run it against a test cluster, in a
maintenance window, and one scenario at a time.
"""

from __future__ import annotations

import argparse
import http.cookiejar
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent
CORE = HERE.parents[2]
RUNTIME = "llm-port-ray-runtime"


@dataclass
class Scenario:
    where: str  # "head", "worker", "local" (touch a dev source file) or "manual"
    command: str
    expect: str
    #: Whether LLM.Port is expected to notice anything at all.
    breaks: bool = True


SCENARIOS: dict[str, Scenario] = {
    "replica-crash": Scenario(
        "worker",
        # [R]: the pattern must not match pkill's own command line.
        f"docker exec {RUNTIME} pkill -9 -f 'Serve[R]eplica:.*LLMServer'",
        "Ray Serve restarts the copy; the deployment reads fewer copies meanwhile.",
    ),
    "worker-ray-loss": Scenario(
        "worker",
        f"docker exec {RUNTIME} ray stop --force",
        "Cluster degrades naming the worker, rejoins it; the head's copy keeps serving.",
    ),
    "head-process-loss": Scenario(
        "head",
        f"docker exec {RUNTIME} pkill -9 gcs_server",
        "Cluster checks twice, re-forms (stop, start head, join), the model is re-applied.",
    ),
    "head-runtime-restart": Scenario(
        "head",
        f"docker restart {RUNTIME}",
        "What a reboot does to the runtime: it comes back idle. Re-formed as above.",
    ),
    "agent-restart": Scenario(
        "worker",
        "systemctl --user restart llmport-agent",
        "Nothing restarts: the agent reconnects to a healthy cluster.",
        breaks=False,
    ),
    "backend-restart": Scenario(
        "local",
        "llm_port_backend/llm_port_backend/web/lifespan.py",
        "Nothing restarts and nothing is re-applied; clusters are looked at on startup.",
        breaks=False,
    ),
    "gateway-restart": Scenario(
        "manual",
        "Restart the gateway process now (it runs without auto-reload).",
        "Chat fails for the seconds the gateway is down, then answers; routes survive.",
    ),
    "watch": Scenario(
        "none",
        "",
        "Breaks nothing: records every change until --timeout, for faults injected by hand.",
        breaks=False,
    ),
}


@dataclass
class Sample:
    t: float
    env: str
    env_message: str
    phase: str
    copies: str
    dep_message: str
    chat: str

    def key(self) -> tuple:
        return (self.env, self.env_message, self.phase, self.copies, self.dep_message, self.chat.split()[0])


@dataclass
class Run:
    scenario: str
    injected_at: float = 0.0
    samples: list[Sample] = field(default_factory=list)


class Api:
    def __init__(self, base: str, gateway: str) -> None:
        self.base, self.gateway = base.rstrip("/"), gateway.rstrip("/")
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))
        self.login()

    def login(self) -> None:
        self.opener.open(urllib.request.Request(f"{self.base}/api/auth/dev-login", method="POST"), timeout=10)

    def get(self, path: str) -> dict:
        try:
            with self.opener.open(f"{self.base}{path}", timeout=15) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code != 401:
                raise
            self.login()  # a long watch outlives the session
            with self.opener.open(f"{self.base}{path}", timeout=15) as r:
                return json.load(r)

    def token(self) -> str:
        return next(c.value for c in self.jar if c.name == "fapiauth")

    def chat(self, model: str) -> str:
        body = json.dumps({"model": model, "messages": [{"role": "user", "content": "Say OK"}], "max_tokens": 3})
        req = urllib.request.Request(
            f"{self.gateway}/v1/chat/completions", data=body.encode(), method="POST",
            headers={"Authorization": f"Bearer {self.token()}", "content-type": "application/json"},
        )
        start = time.time()
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                r.read()
                return f"{r.status} {time.time() - start:.1f}s"
        except urllib.error.HTTPError as e:
            return f"{e.code} {time.time() - start:.1f}s"
        except Exception as e:  # noqa: BLE001 - any failure to answer is the measurement
            return f"ERR {time.time() - start:.1f}s {type(e).__name__}"


def ssh(host: str, key: str, command: str) -> str:
    out = subprocess.run(
        ["ssh", "-i", key, "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", host, command],
        capture_output=True, text=True, timeout=120,
    )
    return (out.stdout + out.stderr).strip()


def sample(api: Api, env_id: str, dep_id: str, model: str, t0: float) -> Sample:
    env = api.get(f"/api/inference/environments/{env_id}")
    dep = api.get(f"/api/inference/deployments/{dep_id}")
    wanted = ((dep.get("spec") or {}).get("scale") or {}).get("replicas") or dep.get("total_replicas")
    return Sample(
        t=round(time.time() - t0, 1),
        env=env["status"],
        env_message=env.get("status_message") or "",
        phase=dep["phase"],
        copies=f"{dep.get('ready_replicas')}/{wanted}",
        dep_message=dep.get("phase_message") or dep.get("status_message") or "",
        chat=api.chat(model),
    )


def healthy(s: Sample) -> bool:
    ready, wanted = s.copies.split("/")
    return s.env == "ready" and s.phase == "running" and ready == wanted and s.chat.startswith("200")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("scenario", choices=sorted(SCENARIOS))
    parser.add_argument("--deployment", default="qwen-chat")
    parser.add_argument("--model", default="qwen2.5-0.5b-instruct", help="name to chat with through the gateway")
    parser.add_argument("--api", default="http://127.0.0.1:8000")
    parser.add_argument("--gateway", default="http://127.0.0.1:8001")
    parser.add_argument("--head", default="sachi@10.88.10.71")
    parser.add_argument("--worker", default="sachi@10.88.10.49")
    parser.add_argument("--key", default=str(Path.home() / ".ssh" / "id_dgx_spark"))
    parser.add_argument("--timeout", type=float, default=900)
    parser.add_argument("--settle", type=float, default=120, help="healthy this long ends a no-break run")
    parser.add_argument(
        "--manual", action="store_true",
        help="print the command to run on the machine instead of running it over SSH",
    )
    args = parser.parse_args()

    scenario = SCENARIOS[args.scenario]
    api = Api(args.api, args.gateway)
    deps = api.get("/api/inference/deployments")
    deps = deps if isinstance(deps, list) else deps.get("items", [])
    dep = next(d for d in deps if d["name"] == args.deployment)
    env_id, dep_id = dep["environment_id"], dep["id"]

    watching = scenario.where == "none"
    print(f"{args.scenario}: {scenario.expect}")
    before = sample(api, env_id, dep_id, args.model, time.time())
    if not healthy(before) and not watching:
        print(f"not starting: not healthy to begin with ({before})")
        return 2

    run = Run(args.scenario, injected_at=time.time())
    if scenario.where == "local":
        # The dev backend reloads on a source change: that is its restart.
        (CORE / scenario.command).touch()
        print(f"touched {scenario.command}")
    elif scenario.where == "manual":
        print(scenario.command)
    elif scenario.where in ("head", "worker"):
        host = args.head if scenario.where == "head" else args.worker
        if args.manual:
            print(f"run on {host} now: {scenario.command}")
        else:
            print(f"on {host}: {scenario.command}\n  -> {ssh(host, args.key, scenario.command) or 'ok'}")

    last: tuple | None = None
    broke_at: float | None = None
    healthy_since: float | None = None
    while time.time() - run.injected_at < args.timeout:
        try:
            s = sample(api, env_id, dep_id, args.model, run.injected_at)
        except Exception as exc:  # noqa: BLE001 - the backend itself may be the thing restarting
            s = Sample(round(time.time() - run.injected_at, 1), "unreachable", str(exc)[:80], "?", "?/?", "", "ERR")
        run.samples.append(s)
        if s.key() != last:
            last = s.key()
            print(f"{time.strftime('%H:%M:%S')} +{s.t:6.1f}s  cluster={s.env} {s.env_message[:160]}")
            print(f"          model={s.phase} {s.copies} {s.dep_message[:160]}  chat={s.chat}", flush=True)
        if watching:
            time.sleep(5)
            continue
        if healthy(s):
            healthy_since = healthy_since if healthy_since is not None else s.t
            if broke_at is not None or (not scenario.breaks and s.t - healthy_since >= args.settle):
                break
        else:
            healthy_since = None
            broke_at = broke_at if broke_at is not None else s.t
        time.sleep(5)

    outage = [s.t for s in run.samples if not s.chat.startswith("200")]
    report = {
        "scenario": args.scenario,
        "expect": scenario.expect,
        "first_unhealthy_s": broke_at,
        "recovered_s": healthy_since if broke_at is not None else None,
        "chat_failed_between_s": [outage[0], outage[-1]] if outage else None,
        "ended_healthy": bool(run.samples) and healthy(run.samples[-1]),
        "samples": [s.__dict__ for s in run.samples],
    }
    out = HERE / f"report-{args.scenario}-{time.strftime('%Y%m%dT%H%M%S')}.json"
    out.write_text(json.dumps(report, indent=1), encoding="utf-8")
    def secs(value: float | None) -> str:
        return "-" if value is None else f"{value}s"

    failed = report["chat_failed_between_s"]
    print(
        f"\nfirst unhealthy: {secs(broke_at)}  recovered: {secs(report['recovered_s'])}  "
        f"chat failed: {f'{failed[0]}s to {failed[1]}s' if failed else 'never'}  "
        f"healthy at end: {report['ended_healthy']}\n{out}"
    )
    return 0 if report["ended_healthy"] else 1


if __name__ == "__main__":
    sys.exit(main())
