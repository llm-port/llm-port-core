import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router";
import { useTranslation } from "react-i18next";

import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Stack from "@mui/material/Stack";
import Tab from "@mui/material/Tab";
import Tabs from "@mui/material/Tabs";
import Typography from "@mui/material/Typography";

import { logsApi, type LogStream } from "~/api/logs";
import AuditLogsTab from "~/pages/admin/AuditLogsTab";
import LogsFilters, { type TimePreset } from "~/pages/admin/logs/LogsFilters";
import LogsTable from "~/pages/admin/logs/LogsTable";

type LogsTab = "logs" | "audit";

const LABEL_PRIORITY = ["compose_service", "container", "job", "host", "level"];

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

function buildLogql(
  selectedLabels: Record<string, string>,
  search: string,
  fallbackLabel?: string,
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
    const safeLabel = fallbackLabel ?? "job";
    parts.push(`${safeLabel}=~".+"`);
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
    {},
  );
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
    () => buildLogql(selectedLabels, search, availableLabelKeys[0]),
    [selectedLabels, search, availableLabelKeys],
  );

  function closeSocket() {
    manualCloseRef.current = true;
    wsRef.current?.close();
    wsRef.current = null;
  }

  async function fetchQueryRange() {
    setLoading(true);
    setError(null);
    try {
      let start: string | undefined;
      let end: string | undefined;
      if (preset === "custom") {
        start = datetimeLocalToIso(customStart);
        end = datetimeLocalToIso(customEnd);
      } else {
        const range = presetToRange(preset);
        start = range.start;
        end = range.end;
      }

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

  async function loadLabels() {
    try {
      const result = await logsApi.getLabels();
      const preferred = LABEL_PRIORITY.filter((key) =>
        result.labels.includes(key),
      );
      setAvailableLabelKeys(preferred);

      const valuesPairs = await Promise.all(
        preferred.map(async (label) => {
          const values = await logsApi.getLabelValues(label);
          return [label, values.values] as const;
        }),
      );
      setValuesByLabel(Object.fromEntries(valuesPairs));
    } catch (e: unknown) {
      setTopError(
        e instanceof Error ? e.message : t("logs.failed_load_labels"),
      );
    }
  }

  useEffect(() => {
    void loadLabels();
    void fetchQueryRange();
    return () => {
      closeSocket();
    };
  }, []);

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
