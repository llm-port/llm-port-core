import { useEffect, useMemo, useState } from "react";

import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import CircularProgress from "@mui/material/CircularProgress";
import Chip from "@mui/material/Chip";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";
import Tooltip from "@mui/material/Tooltip";

import type { LogEntry, LogStream } from "~/api/logs";
import { DataTable, type ColumnDef } from "~/components/DataTable";
import BugReportOutlinedIcon from "@mui/icons-material/BugReportOutlined";
import ErrorOutlineIcon from "@mui/icons-material/ErrorOutline";
import InfoOutlinedIcon from "@mui/icons-material/InfoOutlined";
import WarningAmberIcon from "@mui/icons-material/WarningAmber";

interface FlattenedLog extends LogEntry {
  id: string;
  __labels: Record<string, string>;
  __level: string;
  __service: string;
  __container: string;
  __host: string;
  __job: string;
  __tsMs: number;
}

interface LogsTableProps {
  streams: LogStream[];
  loading: boolean;
  error: string | null;
  live: boolean;
}

const PAGE_SIZE = 200;

function levelUi(level: string): {
  label: string;
  color: "default" | "error" | "warning" | "info" | "success";
  icon: JSX.Element;
} {
  const value = level.toLowerCase();
  if (value === "error" || value === "fatal") {
    return { label: value || "unknown", color: "error", icon: <ErrorOutlineIcon fontSize="small" /> };
  }
  if (value === "warn" || value === "warning") {
    return { label: value || "unknown", color: "warning", icon: <WarningAmberIcon fontSize="small" /> };
  }
  if (value === "debug" || value === "trace") {
    return { label: value || "unknown", color: "default", icon: <BugReportOutlinedIcon fontSize="small" /> };
  }
  return { label: value || "info", color: "info", icon: <InfoOutlinedIcon fontSize="small" /> };
}

export default function LogsTable({ streams, loading, error, live }: LogsTableProps) {
  const [visibleCount, setVisibleCount] = useState(PAGE_SIZE);
  const [levelFilter, setLevelFilter] = useState<string[]>([]);
  const [serviceFilter, setServiceFilter] = useState<string[]>([]);
  const [containerFilter, setContainerFilter] = useState<string[]>([]);

  const flatLogs = useMemo(() => {
    const lines: FlattenedLog[] = [];
    let idx = 0;
    for (const stream of streams) {
      const labels = stream.labels ?? {};
      for (const entry of stream.entries) {
        const level = String(labels.level ?? "");
        const service = String(labels.compose_service ?? labels.service_name ?? "");
        const container = String(labels.container ?? labels.container_id ?? "");
        const host = String(labels.host ?? "");
        const job = String(labels.job ?? "");
        const tsMs = new Date(entry.ts).getTime();

        lines.push({
          ...entry,
          id: `${entry.ts}-${idx}`,
          __labels: labels,
          __level: level,
          __service: service,
          __container: container,
          __host: host,
          __job: job,
          __tsMs: Number.isNaN(tsMs) ? 0 : tsMs,
        });
        idx += 1;
      }
    }
    return lines.sort((a, b) => b.__tsMs - a.__tsMs);
  }, [streams]);

  const levelOptions = useMemo(
    () =>
      Array.from(new Set(flatLogs.map((r) => r.__level).filter(Boolean)))
        .sort()
        .map((value) => ({ value, label: value })),
    [flatLogs],
  );

  const serviceOptions = useMemo(
    () =>
      Array.from(new Set(flatLogs.map((r) => r.__service).filter(Boolean)))
        .sort()
        .map((value) => ({ value, label: value })),
    [flatLogs],
  );

  const containerOptions = useMemo(
    () =>
      Array.from(new Set(flatLogs.map((r) => r.__container).filter(Boolean)))
        .sort()
        .map((value) => ({ value, label: value })),
    [flatLogs],
  );

  const filteredRows = useMemo(() => {
    return flatLogs.filter((row) => {
      const levelActive = levelFilter.length > 0 && levelFilter.length < levelOptions.length;
      const serviceActive = serviceFilter.length > 0 && serviceFilter.length < serviceOptions.length;
      const containerActive = containerFilter.length > 0 && containerFilter.length < containerOptions.length;

      if (levelActive && !levelFilter.includes(row.__level)) return false;
      if (serviceActive && !serviceFilter.includes(row.__service)) return false;
      if (containerActive && !containerFilter.includes(row.__container)) return false;
      return true;
    });
  }, [
    flatLogs,
    levelFilter,
    serviceFilter,
    containerFilter,
    levelOptions.length,
    serviceOptions.length,
    containerOptions.length,
  ]);

  const visibleRows = filteredRows.slice(0, visibleCount);

  function withDefaults(prev: string[], options: { value: string; label: string }[]): string[] {
    const allValues = options.map((opt) => opt.value);
    if (allValues.length === 0) return [];
    if (prev.length === 0) return allValues;
    const prevSet = new Set(prev);
    const kept = allValues.filter((value) => prevSet.has(value));
    const newlyDiscovered = allValues.filter((value) => !prevSet.has(value));
    return [...kept, ...newlyDiscovered];
  }

  useEffect(() => {
    setLevelFilter((prev) => withDefaults(prev, levelOptions));
  }, [levelOptions]);

  useEffect(() => {
    setServiceFilter((prev) => withDefaults(prev, serviceOptions));
  }, [serviceOptions]);

  useEffect(() => {
    setContainerFilter((prev) => withDefaults(prev, containerOptions));
  }, [containerOptions]);

  useEffect(() => {
    setVisibleCount(PAGE_SIZE);
  }, [levelFilter, serviceFilter, containerFilter]);

  const columns: ColumnDef<FlattenedLog>[] = [
    {
      key: "time",
      label: "Time",
      sortable: true,
      sortValue: (row) => row.__tsMs,
      minWidth: 120,
      render: (row) => (
        <Typography variant="body2" fontFamily="monospace" fontSize="0.78rem" noWrap>
          {new Date(row.ts).toLocaleTimeString()}
        </Typography>
      ),
    },
    {
      key: "level",
      label: "Level",
      sortable: true,
      sortValue: (row) => row.__level,
      searchValue: (row) => row.__level,
      minWidth: 90,
      render: (row) => {
        const ui = levelUi(row.__level);
        return (
          <Chip
            size="small"
            variant="outlined"
            color={ui.color}
            icon={ui.icon}
            label={ui.label.toUpperCase()}
            sx={{ fontFamily: "monospace" }}
          />
        );
      },
    },
    {
      key: "service",
      label: "Service",
      sortable: true,
      sortValue: (row) => row.__service,
      searchValue: (row) => `${row.__service} ${row.__job} ${row.__host}`,
      minWidth: 180,
      render: (row) => (
        <Tooltip title={`${row.__service || "—"} ${row.__job ? `(job=${row.__job})` : ""}`}>
          <Typography variant="body2" noWrap sx={{ maxWidth: 260 }}>
            {row.__service || "—"}
          </Typography>
        </Tooltip>
      ),
    },
    {
      key: "container",
      label: "Container",
      sortable: true,
      sortValue: (row) => row.__container,
      searchValue: (row) => row.__container,
      minWidth: 160,
      render: (row) => (
        <Tooltip title={row.__container || "—"}>
          <Typography variant="body2" fontFamily="monospace" noWrap sx={{ maxWidth: 240 }}>
            {row.__container || "—"}
          </Typography>
        </Tooltip>
      ),
    },
    {
      key: "message",
      label: "Message",
      searchValue: (row) => row.line,
      minWidth: 480,
      render: (row) => (
        <Tooltip title={row.line}>
          <Typography
            variant="body2"
            fontFamily="monospace"
            sx={{
              whiteSpace: "nowrap",
              overflow: "hidden",
              textOverflow: "ellipsis",
              maxWidth: "100%",
            }}
          >
            {row.line}
          </Typography>
        </Tooltip>
      ),
    },
    {
      key: "copy",
      label: "Actions",
      minWidth: 90,
      render: (row) => (
        <Button
          size="small"
          variant="text"
          onClick={async () => {
            await navigator.clipboard.writeText(row.line);
          }}
        >
          Copy
        </Button>
      ),
    },
  ];

  if (error) {
    return <Alert severity="error">{error}</Alert>;
  }

  if (loading) {
    return (
      <Box sx={{ display: "flex", justifyContent: "center", py: 6 }}>
        <CircularProgress />
      </Box>
    );
  }

  if (filteredRows.length === 0 && flatLogs.length === 0) {
    return <Alert severity="info">No logs found for the current filters.</Alert>;
  }

  return (
    <Box sx={{ minHeight: 0, display: "flex", flexDirection: "column", flexGrow: 1 }}>
      <Stack direction="row" justifyContent="space-between" alignItems="center" sx={{ mb: 0.5 }}>
        <Typography variant="caption" color="text.secondary">
          {filteredRows.length} lines
        </Typography>
        {live && (
          <Typography variant="caption" color="success.main">
            Live tail active
          </Typography>
        )}
      </Stack>
      <DataTable
        title="Log Entries"
        columns={columns}
        rows={visibleRows}
        rowKey={(row) => row.id}
        loading={false}
        error={null}
        emptyMessage={flatLogs.length === 0 ? "No logs found." : "No rows match current column filters."}
        searchPlaceholder="Search logs..."
        columnFilters={[
          {
            label: "Level",
            value: "",
            options: levelOptions,
            multi: true,
            multiValue: levelFilter,
            onMultiChange: (values) => setLevelFilter(values),
            minWidth: 120,
          },
          {
            label: "Service",
            value: "",
            options: serviceOptions,
            multi: true,
            multiValue: serviceFilter,
            onMultiChange: (values) => setServiceFilter(values),
            minWidth: 160,
          },
          {
            label: "Container",
            value: "",
            options: containerOptions,
            multi: true,
            multiValue: containerFilter,
            onMultiChange: (values) => setContainerFilter(values),
            minWidth: 180,
          },
        ]}
        toolbarActions={
          visibleCount < filteredRows.length ? (
            <Button variant="outlined" size="small" onClick={() => setVisibleCount((v) => v + PAGE_SIZE)}>
              Load more
            </Button>
          ) : undefined
        }
      />
    </Box>
  );
}
