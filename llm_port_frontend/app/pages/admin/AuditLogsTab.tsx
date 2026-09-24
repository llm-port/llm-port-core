/**
 * Admin -> Audit log table content used inside LogsPage tabs.
 */
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { audit, type AuditEvent } from "~/api/admin";
import { DataTable, type ColumnDef } from "~/components/DataTable";
import { ResultChip, SeverityChip } from "~/components/Chips";

import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Stack from "@mui/material/Stack";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";

import FilterListIcon from "@mui/icons-material/FilterList";

export default function AuditLogsTab() {
  const { t, i18n } = useTranslation();
  const [data, setData] = useState<AuditEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [filterAction, setFilterAction] = useState("");
  const [filterTarget, setFilterTarget] = useState("");

  async function load() {
    setLoading(true);
    setError(null);
    try {
      const result = await audit.list({
        action: filterAction || undefined,
        target_id: filterTarget || undefined,
        limit: 200,
      });
      setData(result);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : t("common.load_failed"));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void load();
  }, []);

  const columns: ColumnDef<AuditEvent>[] = [
    {
      key: "time",
      label: t("logs.time"),
      sortable: true,
      sortValue: (ev) => new Date(ev.time).getTime(),
      render: (ev) => (
        <Typography variant="body2" color="text.secondary" fontSize="0.8rem" noWrap>
          {new Date(ev.time).toLocaleString(i18n.language || undefined)}
        </Typography>
      ),
    },
    {
      key: "action",
      label: t("audit.action"),
      sortable: true,
      searchValue: (ev) => ev.action,
      render: (ev) => (
        <Typography variant="body2" fontFamily="monospace" fontSize="0.8rem">
          {ev.action}
        </Typography>
      ),
    },
    {
      key: "target",
      label: t("audit.target"),
      searchValue: (ev) => ev.target_type + " " + ev.target_id,
      render: (ev) => (
        <Typography variant="body2" fontFamily="monospace" fontSize="0.8rem" color="text.secondary">
          <Box component="span" sx={{ color: "text.disabled", mr: 0.5 }}>
            {ev.target_type}/
          </Box>
          {ev.target_id.slice(0, 24)}
        </Typography>
      ),
    },
    {
      key: "result",
      label: t("audit.result"),
      render: (ev) => <ResultChip value={ev.result as "allow" | "deny"} />,
    },
    {
      key: "severity",
      label: t("audit.severity"),
      render: (ev) => <SeverityChip value={ev.severity} />,
    },
    {
      key: "actor",
      label: t("audit.actor"),
      searchValue: (ev) => ev.actor_id ?? "",
      render: (ev) => (
        <Typography variant="body2" fontFamily="monospace" fontSize="0.8rem" color="text.secondary">
          {ev.actor_id?.slice(0, 8) ?? "—"}
        </Typography>
      ),
    },
  ];

  return (
    <Box sx={{ display: "flex", flexDirection: "column", height: "100%", overflow: "hidden" }}>
      <DataTable
        title={t("audit.title")}
        columns={columns}
        rows={data}
        rowKey={(ev) => ev.id}
        loading={loading}
        error={error}
        emptyMessage={t("audit.empty")}
        onRefresh={load}
        searchPlaceholder={t("audit.search")}
        toolbarActions={
          <Stack direction="row" spacing={1} alignItems="center">
            <TextField
              label={t("audit.action")}
              size="small"
              value={filterAction}
              onChange={(e) => setFilterAction(e.target.value)}
              sx={{ width: 160 }}
            />
            <TextField
              label={t("audit.target_id")}
              size="small"
              value={filterTarget}
              onChange={(e) => setFilterTarget(e.target.value)}
              sx={{ width: 160 }}
            />
            <Button variant="outlined" size="small" startIcon={<FilterListIcon />} onClick={load}>
              {t("inference.detail.apply")}
            </Button>
          </Stack>
        }
      />
    </Box>
  );
}
