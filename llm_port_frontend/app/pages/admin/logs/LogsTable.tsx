import { useEffect, useMemo, useState } from "react";
import type { ReactElement } from "react";
import { useNavigate } from "react-router";
import { useTranslation } from "react-i18next";

import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import CircularProgress from "@mui/material/CircularProgress";
import Chip from "@mui/material/Chip";
import Link from "@mui/material/Link";
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
  icon: ReactElement;
} {
  const value = level.toLowerCase();
  if (value === "error" || value === "fatal") {
    return {
      label: value || "unknown",
      color: "error",
      icon: <ErrorOutlineIcon fontSize="small" />,
    };
  }
  if (value === "warn" || value === "warning") {
    return {
      label: value || "unknown",
      color: "warning",
      icon: <WarningAmberIcon fontSize="small" />,
    };
  }
  if (value === "debug" || value === "trace") {
    return {
      label: value || "unknown",
      color: "default",
      icon: <BugReportOutlinedIcon fontSize="small" />,
    };
  }
  return {
    label: value || "info",
    color: "info",
    icon: <InfoOutlinedIcon fontSize="small" />,
  };
}

export default function LogsTable({
  streams,
  loading,
  error,
  live,
}: LogsTableProps) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [visibleCount, setVisibleCount] = useState(PAGE_SIZE);
  const [levelFilter, setLevelFilter] = useState<string[]>([]);
  const [containerFilter, setContainerFilter] = useState<string[]>([]);

  const flatLogs = useMemo(() => {
    const lines: FlattenedLog[] = [];
    let idx = 0;
    for (const stream of streams) {
      const labels = stream.labels ?? {};
      for (const entry of stream.entries) {
        const level = String(labels.level ?? "");
        const service = String(
          labels.compose_service ?? labels.service_name ?? "",
        );
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

  const containerOptions = useMemo(
    () =>
      Array.from(new Set(flatLogs.map((r) => r.__container).filter(Boolean)))
        .sort()
        .map((value) => ({ value, label: value })),
    [flatLogs],
  );

  const filteredRows = useMemo(() => {
    return flatLogs.filter((row) => {
      const levelActive =
        levelFilter.length > 0 && levelFilter.length < levelOptions.length;
      const containerActive =
        containerFilter.length > 0 &&
        containerFilter.length < containerOptions.length;

      if (levelActive && !levelFilter.includes(row.__level)) return false;
      if (containerActive && !containerFilter.includes(row.__container))
        return false;
      return true;
    });
  }, [
    flatLogs,
    levelFilter,
    containerFilter,
    levelOptions.length,
    containerOptions.length,
  ]);

  const visibleRows = filteredRows.slice(0, visibleCount);

  function withDefaults(
    prev: string[],
    options: { value: string; label: string }[],
  ): string[] {
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
    setContainerFilter((prev) => withDefaults(prev, containerOptions));
  }, [containerOptions]);

  useEffect(() => {
    setVisibleCount(PAGE_SIZE);
  }, [levelFilter, containerFilter]);

  const columns: ColumnDef<FlattenedLog>[] = [
    {
      key: "time",
      label: t("logs.time"),
      sortable: true,
      sortValue: (row) => row.__tsMs,
      minWidth: 105,
      render: (row) => (
        <Typography
          variant="body2"
          fontFamily="monospace"
          fontSize="0.78rem"
          noWrap
        >
          {new Date(row.ts).toLocaleTimeString()}
        </Typography>
      ),
    },
    {
      key: "level",
      label: t("logs.level"),
      sortable: true,
      sortValue: (row) => row.__level,
      searchValue: (row) => row.__level,
      minWidth: 100,
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
      key: "container",
      label: t("logs.container"),
      sortable: true,
      sortValue: (row) => row.__container,
      searchValue: (row) => row.__container,
      minWidth: 160,
      render: (row) => {
        const name = row.__container || "—";
        if (!row.__container) {
          return (
            <Typography
              variant="body2"
              fontFamily="monospace"
              noWrap
              sx={{ maxWidth: 240 }}
            >
              {name}
            </Typography>
          );
        }
        return (
          <Tooltip title={name}>
            <Link
              component="button"
              variant="body2"
              underline="hover"
              onClick={() =>
                navigate(
                  `/admin/containers?highlight=${encodeURIComponent(name)}`,
                )
              }
              sx={{
                fontFamily: "monospace",
                maxWidth: 240,
                overflow: "hidden",
                textOverflow: "ellipsis",
                whiteSpace: "nowrap",
                textAlign: "left",
              }}
            >
              {name}
            </Link>
          </Tooltip>
        );
      },
    },
    {
      key: "message",
      label: t("logs.message"),
      searchValue: (row) => row.line,
      minWidth: 300,
      render: (row) => (
        <Tooltip title={row.line}>
          <Typography
            variant="body2"
            fontFamily="monospace"
            sx={{
              whiteSpace: "pre-wrap",
              overflowWrap: "anywhere",
              wordBreak: "break-word",
            }}
          >
            {row.line}
          </Typography>
        </Tooltip>
      ),
    },
    {
      key: "copy",
      label: t("common.actions"),
      minWidth: 70,
      render: (row) => (
        <Button
          size="small"
          variant="text"
          onClick={async () => {
            await navigator.clipboard.writeText(row.line);
          }}
        >
          {t("logs.copy")}
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
    return <Alert severity="info">{t("logs.no_logs_current_filters")}</Alert>;
  }

  return (
    <Box
      sx={{
        minHeight: 0,
        display: "flex",
        flexDirection: "column",
        flexGrow: 1,
      }}
    >
      <DataTable
        title={t("logs.entries_title")}
        columns={columns}
        rows={visibleRows}
        rowKey={(row) => row.id}
        loading={false}
        error={null}
        tableLayout="auto"
        emptyMessage={
          flatLogs.length === 0
            ? t("logs.no_logs_found")
            : t("logs.no_rows_match_filters")
        }
        searchPlaceholder={t("logs.search_logs")}
        columnFilters={[
          {
            label: t("logs.level"),
            value: "",
            options: levelOptions,
            multi: true,
            multiValue: levelFilter,
            onMultiChange: (values) => setLevelFilter(values),
            minWidth: 120,
          },
          {
            label: t("logs.container"),
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
            <Button
              variant="outlined"
              size="small"
              onClick={() => setVisibleCount((v) => v + PAGE_SIZE)}
            >
              {t("logs.load_more")}
            </Button>
          ) : undefined
        }
      />
    </Box>
  );
}
