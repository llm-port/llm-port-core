import { useState } from "react";
import { useNavigate } from "react-router";
import { useTranslation } from "react-i18next";
import { nodesApi, type ManagedNode } from "~/api/nodes";
import { DataTable, type ColumnDef } from "~/components/DataTable";
import { useAsyncData } from "~/lib/useAsyncData";
import NodeOnboardingDrawer from "./NodeOnboardingPage";

import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Chip from "@mui/material/Chip";
import IconButton from "@mui/material/IconButton";
import Stack from "@mui/material/Stack";
import Tooltip from "@mui/material/Tooltip";
import Typography from "@mui/material/Typography";

import BuildCircleIcon from "@mui/icons-material/BuildCircle";
import ExploreOffIcon from "@mui/icons-material/ExploreOff";
import Inventory2Icon from "@mui/icons-material/Inventory2";
import OpenInNewIcon from "@mui/icons-material/OpenInNew";

function statusColor(status: string): "success" | "warning" | "error" | "default" {
  if (status === "healthy") return "success";
  if (status === "maintenance" || status === "draining" || status === "degraded") return "warning";
  if (status === "offline" || status === "error") return "error";
  return "default";
}

function gpuCount(node: ManagedNode): number {
  const caps = node.capabilities ?? {};
  const fromFlat = Number(caps.gpu_count ?? 0);
  if (Number.isFinite(fromFlat) && fromFlat > 0) return fromFlat;
  const gpu = caps.gpu;
  if (gpu && typeof gpu === "object") {
    const fromNested = Number((gpu as Record<string, unknown>).count ?? 0);
    if (Number.isFinite(fromNested) && fromNested > 0) return fromNested;
  }
  return 0;
}

export default function NodeFleetPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { data, loading, error, refresh, setError } = useAsyncData(
    () => nodesApi.list(),
    [],
    { initialValue: [] as ManagedNode[] },
  );
  const [actionBusyKey, setActionBusyKey] = useState<string | null>(null);
  const [onboardingOpen, setOnboardingOpen] = useState(false);

  async function runAction(key: string, action: () => Promise<void>) {
    setActionBusyKey(key);
    try {
      await action();
      await refresh();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Action failed.");
    } finally {
      setActionBusyKey(null);
    }
  }

  const columns: ColumnDef<ManagedNode>[] = [
    {
      key: "host",
      label: "Host",
      sortable: true,
      sortValue: (row) => row.host,
      searchValue: (row) => `${row.host} ${row.agent_id}`,
      render: (row) => (
        <Box>
          <Typography variant="body2" fontWeight={600}>
            {row.host}
          </Typography>
          <Typography variant="caption" color="text.secondary" fontFamily="monospace">
            {row.agent_id}
          </Typography>
        </Box>
      ),
    },
    {
      key: "status",
      label: "Status",
      sortable: true,
      sortValue: (row) => row.status,
      searchValue: (row) => row.status,
      render: (row) => <Chip size="small" color={statusColor(row.status)} label={row.status} />,
    },
    {
      key: "gpu",
      label: "GPU",
      align: "center",
      sortable: true,
      sortValue: (row) => gpuCount(row),
      render: (row) => (
        <Typography variant="body2" fontFamily="monospace">
          {gpuCount(row)}
        </Typography>
      ),
    },
    {
      key: "flags",
      label: "State",
      searchValue: (row) =>
        `${row.maintenance_mode ? "maintenance" : ""} ${row.draining ? "draining" : ""}`.trim(),
      render: (row) => (
        <Stack direction="row" spacing={0.5} flexWrap="wrap">
          {row.maintenance_mode && <Chip size="small" label="maintenance" color="warning" variant="outlined" />}
          {row.draining && <Chip size="small" label="draining" color="warning" variant="outlined" />}
          {!row.maintenance_mode && !row.draining && (
            <Typography variant="body2" color="text.secondary">
              -
            </Typography>
          )}
        </Stack>
      ),
    },
    {
      key: "last_seen",
      label: "Last Seen",
      sortable: true,
      sortValue: (row) => (row.last_seen ? Date.parse(row.last_seen) : 0),
      render: (row) => (
        <Typography variant="body2" color="text.secondary">
          {row.last_seen ? new Date(row.last_seen).toLocaleString() : "-"}
        </Typography>
      ),
    },
    {
      key: "actions",
      label: t("common.actions"),
      align: "right",
      hideable: false,
      render: (row) => {
        const maintenanceKey = `${row.id}:maintenance`;
        const drainKey = `${row.id}:drain`;
        const invKey = `${row.id}:inventory`;
        return (
          <Stack direction="row" spacing={0.5} justifyContent="flex-end">
            <Tooltip title="Details">
              <IconButton
                size="small"
                onClick={(event) => {
                  event.stopPropagation();
                  navigate(`/admin/nodes/${row.id}`);
                }}
              >
                <OpenInNewIcon fontSize="small" />
              </IconButton>
            </Tooltip>
            <Tooltip title={row.maintenance_mode ? "Disable maintenance" : "Enable maintenance"}>
              <span>
                <IconButton
                  size="small"
                  onClick={(event) => {
                    event.stopPropagation();
                    runAction(maintenanceKey, async () => {
                      await nodesApi.setMaintenance(row.id, !row.maintenance_mode);
                    });
                  }}
                  disabled={actionBusyKey === maintenanceKey}
                >
                  <BuildCircleIcon fontSize="small" />
                </IconButton>
              </span>
            </Tooltip>
            <Tooltip title={row.draining ? "Disable drain" : "Enable drain"}>
              <span>
                <IconButton
                  size="small"
                  onClick={(event) => {
                    event.stopPropagation();
                    runAction(drainKey, async () => {
                      await nodesApi.setDrain(row.id, !row.draining);
                    });
                  }}
                  disabled={actionBusyKey === drainKey}
                >
                  <ExploreOffIcon fontSize="small" />
                </IconButton>
              </span>
            </Tooltip>
            <Tooltip title="Refresh inventory">
              <span>
                <IconButton
                  size="small"
                  onClick={(event) => {
                    event.stopPropagation();
                    runAction(invKey, async () => {
                      await nodesApi.issueCommand(row.id, { command_type: "refresh_inventory" });
                    });
                  }}
                  disabled={actionBusyKey === invKey}
                >
                  <Inventory2Icon fontSize="small" />
                </IconButton>
              </span>
            </Tooltip>
          </Stack>
        );
      },
    },
  ];

  return (
    <>
    <DataTable
      title="Node Fleet"
      rows={data}
      columns={columns}
      rowKey={(row) => row.id}
      loading={loading}
      error={error}
      onRefresh={refresh}
      emptyMessage="No nodes registered yet."
      searchPlaceholder="Search host or agent id"
      onRowClick={(row) => navigate(`/admin/nodes/${row.id}`)}
      toolbarActions={
        <Button variant="contained" onClick={() => setOnboardingOpen(true)}>
          Add Node
        </Button>
      }
      pagination={25}
      columnVisibilityKey="dt-node-fleet"
    />
    <NodeOnboardingDrawer open={onboardingOpen} onClose={() => setOnboardingOpen(false)} />
    </>
  );
}
