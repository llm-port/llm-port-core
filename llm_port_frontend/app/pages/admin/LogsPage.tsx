import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router";
import { useTranslation } from "react-i18next";

import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Stack from "@mui/material/Stack";
import Tab from "@mui/material/Tab";
import Tabs from "@mui/material/Tabs";
import Typography from "@mui/material/Typography";

import { inferenceApi } from "~/api/inference";
import { logsApi, type LogStream } from "~/api/logs";
import { nodesApi } from "~/api/nodes";
import AuditLogsTab from "~/pages/admin/AuditLogsTab";
import LogsFilters, { type TimePreset } from "~/pages/admin/logs/LogsFilters";
import LogsTable from "~/pages/admin/logs/LogsTable";
import { filterLabels, NO_NAMES, type NameLookups } from "~/pages/admin/logs/labelMeta";

type LogsTab = "logs" | "audit";

/** Labels to start with, from the URL: ``/admin/logs?host=10.88.10.71`` is one machine's logs. */
function labelsFromUrl(params: URLSearchParams): Record<string, string> {
  const out: Record<string, string> = {};
  for (const [key, value] of params.entries()) {
    if (key !== "tab" && /^[A-Za-z_][A-Za-z0-9_]*$/.test(key) && value) out[key] = value;
  }
  return out;
}

function getTab(param: string | null): LogsTab {
  return param === "audit" ? "audit" : "logs";
}

function presetToRange(preset: TimePreset): { start: string; end: string } {
  const end = new Date();
  const start = new Date(end);
  if (preset === "15m") start.setMinutes(start.getMinutes() - 15);
  if (preset === "1h") start.setHours(start.getHours() - 1);
  if (preset === "6h") start.setHours(start.getHours() - 6);
  if (preset === "24h") start.setHours(start.getHours() - 24);
  return { start: start.toISOString(), end: end.toISOString() };
}

function datetimeLocalToIso(value: string): string | undefined {
  if (!value) return undefined;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return undefined;
  return date.toISOString();
}

function nsToIso(ns: string): string {
  const normalized = ns.length > 13 ? ns.slice(0, 13) : ns;
  const ms = Number(normalized);
  return new Date(ms).toISOString();
}

export function buildLogql(
  selectedLabels: Record<string, string>,
  search: string,
): string {
  const parts: string[] = [];
  for (const [label, value] of Object.entries(selectedLabels)) {
    if (!value) continue;
    if (value.includes("*")) {
      let regex = value.replaceAll("*", ".*");
      if (regex === ".*") {
        regex = ".+";
      }
      parts.push(`${label}=~"${regex}"`);
    } else {
      parts.push(`${label}="${value.replaceAll('"', '\\"')}"`);
    }
  }
  if (parts.length === 0) {
    // Every stream carries ``job``. The first filter's label was used here
    // before -- "container" -- which silently hid every stream without one,
    // among them all of a model's own logs.
    parts.push(`job=~".+"`);
  }
  const selector = `{${parts.join(",")}}`;
  if (!search.trim()) return selector;
  return `${selector} |= "${search.replaceAll('"', '\\"')}"`;
}

function mergeStreams(
  current: LogStream[],
  incoming: LogStream[],
): LogStream[] {
  const byKey = new Map<string, LogStream>();
  for (const stream of current) {
    const key = JSON.stringify(stream.labels);
    byKey.set(key, { ...stream, entries: [...stream.entries] });
  }
  for (const stream of incoming) {
    const key = JSON.stringify(stream.labels);
    const existing = byKey.get(key);
    if (existing) {
      existing.entries.push(...stream.entries);
    } else {
      byKey.set(key, { ...stream, entries: [...stream.entries] });
    }
  }
  return Array.from(byKey.values());
}

function parseTailPayload(raw: string): LogStream[] {
  const parsed = JSON.parse(raw) as {
    streams?: { stream: Record<string, string>; values: [string, string][] }[];
  };
  const streams = parsed.streams ?? [];
  return streams.map((stream) => ({
    labels: stream.stream,
    entries: stream.values.map(([ts, line]) => {
      let structured: Record<string, unknown> | undefined;
      try {
        const obj = JSON.parse(line) as unknown;
        if (obj && typeof obj === "object" && !Array.isArray(obj)) {
          structured = obj as Record<string, unknown>;
        }
      } catch {
        // not JSON
      }
      return {
        ts: nsToIso(ts),
        line,
        structured,
      };
    }),
  }));
}

export default function LogsPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const tab = getTab(searchParams.get("tab"));

  const [preset, setPreset] = useState<TimePreset>("15m");
  const [customStart, setCustomStart] = useState("");
  const [customEnd, setCustomEnd] = useState("");
  const [search, setSearch] = useState("");
  const [live, setLive] = useState(false);

  const [availableLabelKeys, setAvailableLabelKeys] = useState<string[]>([]);
  const [selectedLabels, setSelectedLabels] = useState<Record<string, string>>(
    () => labelsFromUrl(searchParams),
  );
  const [names, setNames] = useState<NameLookups>(NO_NAMES);
  const [valuesByLabel, setValuesByLabel] = useState<Record<string, string[]>>(
    {},
  );

  const [streams, setStreams] = useState<LogStream[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [topError, setTopError] = useState<string | null>(null);

  const wsRef = useRef<WebSocket | null>(null);
  const manualCloseRef = useRef(false);

  const query = useMemo(
    () => buildLogql(selectedLabels, search),
    [selectedLabels, search],
  );

  function closeSocket() {
    manualCloseRef.current = true;
    wsRef.current?.close();
    wsRef.current = null;
  }

  function currentRange(): { start?: string; end?: string } {
    if (preset === "custom") {
      return { start: datetimeLocalToIso(customStart), end: datetimeLocalToIso(customEnd) };
    }
    return presetToRange(preset);
  }

  async function fetchQueryRange() {
    setLoading(true);
    setError(null);
    try {
      const { start, end } = currentRange();

      const response = await logsApi.queryRange({
        query,
        start,
        end,
        limit: 500,
        direction: "BACKWARD",
      });
      setStreams(response.streams);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : t("logs.failed_load"));
    } finally {
      setLoading(false);
    }
  }

  function startLiveTail() {
    setError(null);
    closeSocket();
    manualCloseRef.current = false;
    const ws = new WebSocket(logsApi.tailSocketUrl(query));
    wsRef.current = ws;
    ws.onmessage = (event) => {
      try {
        const incoming = parseTailPayload(String(event.data));
        setStreams((prev) => mergeStreams(prev, incoming));
      } catch {
        // Ignore malformed frames.
      }
    };
    ws.onerror = () => {
      setError(t("logs.live_failed"));
      setLive(false);
    };
    ws.onclose = () => {
      if (!manualCloseRef.current) {
        setError(t("logs.live_disconnected"));
        setLive(false);
      }
    };
  }

  // The filters are whatever labels the logs in this range carry, each with
  // the values the *other* selections leave -- not a fixed list of names.
  async function loadFilters() {
    try {
      const { start, end } = currentRange();
      const result = await logsApi.getFilters({ start, end, selected: selectedLabels });
      // Keep a selected label even if the range no longer has it, so it can
      // still be cleared.
      const keys = new Set([...Object.keys(result.labels), ...Object.keys(selectedLabels)]);
      setAvailableLabelKeys(filterLabels([...keys]));
      setValuesByLabel(result.labels);
      setTopError(null);
    } catch (e: unknown) {
      setTopError(
        e instanceof Error ? e.message : t("logs.failed_load_labels"),
      );
    }
  }

  // Machine and deployment names for label values that are only ids.
  // Best-effort: without them the ids themselves are shown.
  async function loadNames() {
    const [nodes, deployments] = await Promise.all([
      nodesApi.list().catch(() => []),
      inferenceApi.listDeployments().catch(() => []),
    ]);
    setNames({
      machines: new Map(nodes.map((n) => [n.host, n.agent_id] as [string, string])),
      deployments: new Map(deployments.map((d) => [`llmport-${d.id}`, d.name] as [string, string])),
    });
  }

  useEffect(() => {
    void loadNames();
    return () => {
      closeSocket();
    };
  }, []);

  useEffect(() => {
    void loadFilters();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [preset, customStart, customEnd, selectedLabels]);

  // Choosing a machine, source or level shows its logs at once; the search
  // text and a custom range still wait for Apply.
  useEffect(() => {
    if (!live) void fetchQueryRange();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedLabels, preset]);

  useEffect(() => {
    if (tab !== "logs") {
      closeSocket();
      setLive(false);
      return;
    }
    if (live) {
      startLiveTail();
    } else {
      closeSocket();
    }
  }, [live, query, tab]);

  function onTabChange(_event: React.SyntheticEvent, value: LogsTab) {
    navigate(`/admin/logs?tab=${value}`, { replace: true });
  }

  function onLabelValueChange(label: string, value: string) {
    setSelectedLabels((prev) => ({ ...prev, [label]: value }));
  }

  function onApplyFilters() {
    if (!live) {
      void fetchQueryRange();
    }
  }

  return (
    <Box
      sx={{
        display: "flex",
        flexDirection: "column",
        minHeight: 0,
        height: "100%",
      }}
    >
      <Typography variant="h5" sx={{ mb: 1 }}>
        {t("logs.title")}
      </Typography>
      <Tabs value={tab} onChange={onTabChange} sx={{ mb: 2 }}>
        <Tab value="logs" label={t("logs.tab_logs")} />
        <Tab value="audit" label={t("logs.tab_audit")} />
      </Tabs>

      {tab === "logs" && (
        <Box
          sx={{
            minHeight: 0,
            display: "flex",
            flexDirection: "column",
            flexGrow: 1,
          }}
        >
          {topError && (
            <Alert severity="error" sx={{ mb: 1 }}>
              {topError}
            </Alert>
          )}
          <LogsFilters
            preset={preset}
            customStart={customStart}
            customEnd={customEnd}
            search={search}
            live={live}
            availableLabelKeys={availableLabelKeys}
            selectedLabels={selectedLabels}
            valuesByLabel={valuesByLabel}
            names={names}
            onPresetChange={setPreset}
            onCustomStartChange={setCustomStart}
            onCustomEndChange={setCustomEnd}
            onSearchChange={setSearch}
            onLiveChange={setLive}
            onLabelValueChange={onLabelValueChange}
            onApply={onApplyFilters}
          />
          <Stack
            direction="row"
            spacing={1}
            alignItems="center"
            sx={{ mb: 0.5 }}
            flexWrap="wrap"
            useFlexGap
          >
            <Typography variant="caption" color="text.secondary">
              {t("logs.query_hint")}
            </Typography>
            <Typography
              variant="caption"
              fontFamily="monospace"
              color="text.secondary"
            >
              {query}
            </Typography>
            <Typography variant="caption" color="text.secondary">
              {streams.reduce((sum, s) => sum + s.entries.length, 0)}{" "}
              {t("logs.lines_label", { defaultValue: "lines" })}
            </Typography>
            {live && (
              <Typography variant="caption" color="success.main">
                {t("logs.live_tail_active")}
              </Typography>
            )}
          </Stack>
          <LogsTable
            streams={streams}
            loading={loading}
            error={error}
            live={live}
            names={names}
          />
        </Box>
      )}

      {tab === "audit" && (
        <Box
          sx={{
            minHeight: 0,
            display: "flex",
            flexDirection: "column",
            flexGrow: 1,
          }}
        >
          <AuditLogsTab />
        </Box>
      )}
    </Box>
  );
}
