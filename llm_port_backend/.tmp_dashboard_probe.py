import asyncio
from llm_port_backend.services.docker.client import DockerService


async def main():
    d = DockerService()
    containers = await d.list_containers(all_=True)
    running = [c for c in containers if str(c.get("State", "")).lower() == "running"]
    print("containers", len(containers), "running", len(running))
    if running:
        sample = running[0]
        print("sample_id", sample.get("Id"))
        try:
            stats = await d.container_stats(sample["Id"])
            print("stats_ok", isinstance(stats, dict), "keys", list(stats.keys())[:12])
            print("cpu_stats_present", "cpu_stats" in stats, "memory_stats_present", "memory_stats" in stats)
        except Exception as exc:
            print("stats_error", type(exc).__name__, str(exc))
    await d.close()


asyncio.run(main())
