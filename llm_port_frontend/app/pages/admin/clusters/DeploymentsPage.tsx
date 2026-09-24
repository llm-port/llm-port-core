/**
 * Deployments — every model this installation is serving.
 *
 * Same data as the Phase 6 list, said in the operator's words: "Copying the
 * model" rather than `preparing`, "Copies" rather than `replicas`, and the
 * cluster named rather than an environment id.
 */
import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate, useSearchParams } from "react-router";

import { inferenceApi } from "~/api/inference";
import type {
  InferenceDeployment,
  InferenceEndpoint,
  InferenceEnvironment,
} from "~/api/inference";
import { models as modelsApi, type Model } from "~/api/llm";
import { ConfirmDialog } from "~/components/ConfirmDialog";
import { DataTable, type ColumnDef } from "~/components/DataTable";
import { HostModelDialog } from "~/components/hosting/HostModelDialog";
import { useAsyncData } from "~/lib/useAsyncData";

import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Chip from "@mui/material/Chip";
import Skeleton from "@mui/material/Skeleton";
import Stack from "@mui/material/Stack";
import Tooltip from "@mui/material/Tooltip";
import Typography from "@mui/material/Typography";

import AddIcon from "@mui/icons-material/Add";
import DeleteOutlineIcon from "@mui/icons-material/DeleteOutline";
import PlayArrowIcon from "@mui/icons-material/PlayArrow";
import StopIcon from "@mui/icons-material/Stop";

import { copiesWanted, deploymentPhaseColor } from "../inference/common";
import { phaseLabel, shortId } from "./presentation";

interface DeploymentsData {
  deployments: InferenceDeployment[];
  clusters: InferenceEnvironment[];
  models: Model[];
  endpoints: Record<string, InferenceEndpoint[]>;
}

const EMPTY: DeploymentsData = {
  deployments: [],
  clusters: [],
  models: [],
  endpoints: {},
};

/**
 * The addresses, once we know which deployments there are.
 *
 * This is a fan-out — one call per deployment — which is why it must not sit
 * in front of the table. It used to: the page did three calls, then N more,
 * and showed nothing until all of them had returned. On a cluster where the
 * endpoints are exactly what is broken, that is the longest possible wait for
 * the most useful screen.
 */
async function loadEndpoints(
  deployments: InferenceDeployment[],
): Promise<Record<string, InferenceEndpoint[]>> {
  const lists = await Promise.all(
    deployments.map((d) =>
      inferenceApi.listEndpoints(d.id).catch(() => [] as InferenceEndpoint[]),
    ),
  );
  const endpoints: Record<string, InferenceEndpoint[]> = {};
  deployments.forEach((d, i) => {
    endpoints[d.id] = lists[i];
  });
  return endpoints;
}

export default function DeploymentsPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  // The table's own rows. Everything else on this page decorates them.
  // Phases move on their own -- preparing, applying, running -- so this is
  // the table most likely to be wrong the moment after it is drawn.
  const deployments = useAsyncData(() => inferenceApi.listDeployments(), [], {
    initialValue: [] as InferenceDeployment[],
    refreshMs: 10_000,
  });
  // Names. Their absence degrades to a short id, so they never gate the table.
  const clusters = useAsyncData(() => inferenceApi.listEnvironments(), [], {
    initialValue: [] as InferenceEnvironment[],
  });
  const models = useAsyncData(() => modelsApi.list(), [], {
    initialValue: [] as Model[],
  });
  // Runs once the rows are known, and fills the Address column in behind them.
  const endpoints = useAsyncData(
    () =>
      deployments.data.length === 0
        ? Promise.resolve({} as Record<string, InferenceEndpoint[]>)
        : loadEndpoints(deployments.data),
    [deployments.data],
    { initialValue: {} as Record<string, InferenceEndpoint[]> },
  );

  const data: DeploymentsData = {
    deployments: deployments.data,
    clusters: clusters.data,
    models: models.data,
    endpoints: endpoints.data,
  };
  const loading = deployments.loading;
  const error = deployments.error;
  const [actionError, setActionError] = useState<string | null>(null);

  const setError = setActionError;
  const refresh = useCallback(async () => {
    await Promise.all([deployments.refresh(), clusters.refresh(), models.refresh()]);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  const [busyKey, setBusyKey] = useState<string | null>(null);
  const [wizardOpen, setWizardOpen] = useState(false);
  // `?deploy=1` opens the wizard: the providers screen sends people here when
  // a cluster is ready, and landing on a list would make them look for the button.
  const [searchParams, setSearchParams] = useSearchParams();
  useEffect(() => {
    if (searchParams.get("deploy") === "1") {
      setWizardOpen(true);
      setSearchParams({}, { replace: true });
    }
  }, [searchParams, setSearchParams]);
  const [deleteTarget, setDeleteTarget] = useState<InferenceDeployment | null>(null);
  const [deleting, setDeleting] = useState(false);

  const clusterName = (id: string) =>
    data.clusters.find((c) => c.id === id)?.name ?? shortId(id);
  const modelName = (id: string) =>
    data.models.find((m) => m.id === id)?.display_name ?? shortId(id);

  async function run(key: string, action: () => Promise<unknown>) {
    setBusyKey(key);
    try {
      await action();
      await refresh();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : t("common.error_unexpected"));
    } finally {
      setBusyKey(null);
    }
  }

  const columns: ColumnDef<InferenceDeployment>[] = [
    {
      key: "name",
      label: t("clusters.deployments.col_name"),
      sortable: true,
      sortValue: (row) => row.name,
      searchValue: (row) => `${row.name} ${row.description ?? ""}`,
      render: (row) => (
        <Typography variant="body2" fontWeight={600}>
          {row.name}
        </Typography>
      ),
    },
    {
      key: "model",
      label: t("clusters.deployments.col_model"),
      sortable: true,
      sortValue: (row) => modelName(row.model_id),
      searchValue: (row) => modelName(row.model_id),
      render: (row) => <Typography variant="body2">{modelName(row.model_id)}</Typography>,
    },
    {
      key: "cluster",
      label: t("clusters.deployments.col_cluster"),
      sortable: true,
      sortValue: (row) => clusterName(row.environment_id),
      searchValue: (row) => clusterName(row.environment_id),
      render: (row) => (
        <Button
          size="small"
          sx={{ textTransform: "none", p: 0, minWidth: 0 }}
          onClick={(event) => {
            event.stopPropagation();
            navigate(`/admin/clusters/${row.environment_id}`);
          }}
        >
          {clusterName(row.environment_id)}
        </Button>
      ),
    },
    {
      key: "state",
      label: t("clusters.deployments.col_state"),
      sortable: true,
      sortValue: (row) => row.phase,
      render: (row) => (
        <Stack direction="row" spacing={0.5} alignItems="center">
          <Chip
            size="small"
            label={phaseLabel(row.phase)}
            color={deploymentPhaseColor(row.phase)}
          />
          {row.desired_state === "stopped" && (
            <Chip size="small" variant="outlined" label={t("clusters.deployments.stopping")} />
          )}
        </Stack>
      ),
    },
    {
      key: "copies",
      label: t("clusters.deployments.col_copies"),
      align: "right",
      sortable: true,
      sortValue: (row) => row.ready_replicas,
      render: (row) => (
        <Typography
          variant="body2"
          color={row.ready_replicas < copiesWanted(row) ? "warning.main" : "text.primary"}
        >
          {row.ready_replicas} / {copiesWanted(row)}
        </Typography>
      ),
    },
    {
      key: "endpoint",
      label: t("clusters.deployments.col_address"),
      render: (row) => {
        const known = data.endpoints[row.id];
        if (known === undefined) {
          // Still fetching. "not published yet" here would be a gap reported
          // as a fact, which is the one thing this product does not do.
          return <Skeleton variant="text" width={140} animation="wave" />;
        }
        if (known.length === 0) {
          return (
            <Typography variant="body2" color="text.secondary">
              {t("clusters.deployments.not_published")}
            </Typography>
          );
        }
        const primary = known[0];
        return (
          <Tooltip title={primary.status}>
            <Typography variant="body2" sx={{ fontFamily: "monospace" }}>
              {primary.address}
              {primary.path}
            </Typography>
          </Tooltip>
        );
      },
    },
    {
      key: "actions",
      label: t("common.actions"),
      align: "right",
      hideable: false,
      render: (row) => (
        <Stack
          direction="row"
          spacing={0.5}
          justifyContent="flex-end"
          onClick={(event) => event.stopPropagation()}
        >
          {row.desired_state === "active" ? (
            <Tooltip title={t("common.stop")}>
              <span>
                <Button
                  size="small"
                  color="warning"
                  aria-label={t("common.stop")}
                  disabled={busyKey === `stop:${row.id}`}
                  onClick={() =>
                    run(`stop:${row.id}`, () =>
                      inferenceApi.updateDeployment(row.id, { desired_state: "stopped" }),
                    )
                  }
                >
                  <StopIcon fontSize="small" />
                </Button>
              </span>
            </Tooltip>
          ) : (
            <Tooltip title={t("common.start")}>
              <span>
                <Button
                  size="small"
                  color="success"
                  aria-label={t("common.start")}
                  disabled={busyKey === `start:${row.id}`}
                  onClick={() =>
                    run(`start:${row.id}`, () =>
                      inferenceApi.updateDeployment(row.id, { desired_state: "active" }),
                    )
                  }
                >
                  <PlayArrowIcon fontSize="small" />
                </Button>
              </span>
            </Tooltip>
          )}
          <Tooltip title={t("common.delete")}>
            <span>
              <Button
                size="small"
                color="error"
                aria-label={t("common.delete")}
                onClick={() => setDeleteTarget(row)}
              >
                <DeleteOutlineIcon fontSize="small" />
              </Button>
            </span>
          </Tooltip>
        </Stack>
      ),
    },
  ];

  // Only assert this once the cluster list has actually come back;
  // otherwise the page tells a new operator to go and create the cluster
  // they are already looking at.
  const noCluster = !clusters.loading && !clusters.error && data.clusters.length === 0;

  return (
    <Box sx={{ display: "flex", flexDirection: "column", gap: 2, height: "100%" }}>
      {noCluster && (
        <Alert
          severity="info"
          action={
            <Button size="small" color="inherit" onClick={() => navigate("/admin/clusters")}>
              {t("clusters.deployments.go_to_clusters")}
            </Button>
          }
        >
          {t("clusters.deployments.no_cluster")}
        </Alert>
      )}
      <DataTable
        columns={columns}
        rows={data.deployments}
        rowKey={(row) => row.id}
        loading={loading}
        error={error ?? actionError}
        title={t("clusters.deployments.title")}
        emptyMessage={t("clusters.deployments.empty")}
        searchPlaceholder={t("clusters.deployments.search")}
        onRefresh={() => void refresh()}
        onRowClick={(row) => navigate(`/admin/deployments/${row.id}`)}
        columnVisibilityKey="dt-deployments"
        toolbarActions={
          <Button
            variant="contained"
            size="small"
            startIcon={<AddIcon />}
            disabled={noCluster}
            onClick={() => setWizardOpen(true)}
          >
            {t("clusters.next.ready.action")}
          </Button>
        }
      />

      <HostModelDialog
        open={wizardOpen}
        models={data.models}
        onClose={() => setWizardOpen(false)}
        onHosted={(deploymentId) => {
          setWizardOpen(false);
          navigate(`/admin/deployments/${deploymentId}`);
        }}
      />

      <ConfirmDialog
        open={deleteTarget !== null}
        title={t("clusters.deployments.delete_title")}
        message={
          deleteTarget ? t("clusters.deployments.delete_message", { name: deleteTarget.name }) : ""
        }
        confirmLabel={t("common.delete")}
        loading={deleting}
        onConfirm={() => {
          const target = deleteTarget;
          setDeleteTarget(null);
          if (!target) return;
          setDeleting(true);
          void inferenceApi
            .deleteDeployment(target.id)
            .then(() => refresh())
            .catch((err: unknown) =>
              setError(err instanceof Error ? err.message : t("common.delete_failed")),
            )
            .finally(() => setDeleting(false));
        }}
        onClose={() => setDeleteTarget(null)}
      />
    </Box>
  );
}
