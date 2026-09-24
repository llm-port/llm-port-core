/**
 * The model marketplace: find a model on Hugging Face, see whether it fits, host it.
 *
 * Three ways to look: models we recommend (tested, grouped by what they are
 * for), a search of the Hub, and the models this server already keeps. Every
 * card says how the model fits the chosen cluster -- one accelerator, a share
 * of one, several, or not at all -- so the operator picks from what will run
 * rather than finding out after a download.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate, useSearchParams } from "react-router";

import { models as modelsApi, type Model } from "~/api/llm";
import {
  marketplaceApi,
  type KeptModel,
  type MarketCluster,
  type MarketList,
  type MarketModel,
  type MarketSort,
  type ModelTask,
} from "~/api/marketplace";
import { HuggingFaceAccessButton } from "~/components/HuggingFaceAccess";
import { HostModelDialog } from "~/components/hosting/HostModelDialog";
import { formatBytes } from "~/lib/engine";
import { useAsyncData } from "~/lib/useAsyncData";
import { useCan } from "~/lib/useCan";

import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Chip from "@mui/material/Chip";
import CircularProgress from "@mui/material/CircularProgress";
import FormControlLabel from "@mui/material/FormControlLabel";
import Grid from "@mui/material/Grid";
import InputAdornment from "@mui/material/InputAdornment";
import MenuItem from "@mui/material/MenuItem";
import Stack from "@mui/material/Stack";
import Switch from "@mui/material/Switch";
import Tab from "@mui/material/Tab";
import Tabs from "@mui/material/Tabs";
import TextField from "@mui/material/TextField";
import ToggleButton from "@mui/material/ToggleButton";
import ToggleButtonGroup from "@mui/material/ToggleButtonGroup";
import Typography from "@mui/material/Typography";

import DownloadingIcon from "@mui/icons-material/Downloading";
import SearchIcon from "@mui/icons-material/Search";

import { KeptModels, isDownloading } from "./KeptModels";
import { ModelCard } from "./ModelCard";
import { ModelDetailDrawer } from "./ModelDetailDrawer";

type TabId = "recommended" | "search" | "local";
const TABS: TabId[] = ["recommended", "search", "local"];
const SORTS: MarketSort[] = ["trending", "downloads", "likes", "recent"];
const TASKS: ModelTask[] = ["chat", "embedding", "vision"];

/** What the host dialog needs of a kept model. */
function asModel(k: KeptModel): Model {
  return {
    id: k.model_id,
    display_name: k.display_name,
    source: k.source as Model["source"],
    hf_repo_id: k.hf_repo_id,
    hf_revision: k.hf_revision,
    license_ack_required: false,
    tags: null,
    status: k.status as Model["status"],
    instances: [],
    created_at: k.created_at ?? "",
    updated_at: k.created_at ?? "",
  };
}

function fits(model: MarketModel): boolean {
  return model.runnable && (model.fit?.status === "fits" || model.fit?.status === "unknown" || !model.fit);
}

function ModelGrid({
  items,
  onOpen,
  onHost,
  canHost,
}: {
  items: MarketModel[];
  onOpen: (repo: string) => void;
  onHost: (repo: string) => void;
  canHost: boolean;
}) {
  return (
    <Grid container spacing={2}>
      {items.map((m) => (
        <Grid key={m.repo_id} size={{ xs: 12, sm: 6, lg: 4, xl: 3 }}>
          <ModelCard model={m} onOpen={onOpen} onHost={onHost} canHost={canHost} />
        </Grid>
      ))}
    </Grid>
  );
}

function Loading() {
  return (
    <Box sx={{ display: "flex", justifyContent: "center", p: 4 }}>
      <CircularProgress />
    </Box>
  );
}

export default function MarketplacePage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const can = useCan();
  const canHost = can("llm.models:download") && can("inference.deployments:create");
  const [params, setParams] = useSearchParams();

  const tab = (TABS.includes(params.get("tab") as TabId) ? params.get("tab") : "recommended") as TabId;
  const [clusterId, setClusterId] = useState<string | null>(params.get("cluster"));
  const [query, setQuery] = useState(params.get("q") ?? "");
  const [debounced, setDebounced] = useState(query);
  const [sort, setSort] = useState<MarketSort>("trending");
  const [task, setTask] = useState<ModelTask>("chat");
  const [onlyFits, setOnlyFits] = useState(false);
  const [showUnrunnable, setShowUnrunnable] = useState(false);
  const [openRepo, setOpenRepo] = useState<string | null>(null);
  const [hostRepo, setHostRepo] = useState<string | null>(null);
  const [hostKept, setHostKept] = useState<KeptModel | null>(null);

  useEffect(() => {
    const handle = window.setTimeout(() => setDebounced(query.trim()), 400);
    return () => window.clearTimeout(handle);
  }, [query]);

  const clusters = useAsyncData(() => marketplaceApi.clusters(), [], { initialValue: [] as MarketCluster[] });

  // Pick the first cluster with accelerators once they arrive, unless one was asked for.
  useEffect(() => {
    if (clusterId || clusters.data.length === 0) return;
    const usable = clusters.data.find((c) => c.gpu_count > 0) ?? clusters.data[0];
    setClusterId(usable.environment_id);
  }, [clusters.data, clusterId]);

  const list = useAsyncData<MarketList | null>(
    () => {
      if (tab === "local") return Promise.resolve(null);
      if (tab === "recommended") return marketplaceApi.recommended(clusterId);
      return marketplaceApi.search({ q: debounced, sort, task, clusterId, limit: 48 });
    },
    [tab, clusterId, debounced, sort, task],
    { initialValue: null },
  );

  // The models this server keeps, and their downloads: read on every tab for
  // the downloads chip, every few seconds while anything moves.
  const kept = useAsyncData(() => marketplaceApi.kept().then((r) => r.items), [], {
    initialValue: [] as KeptModel[],
  });
  const activeDownloads = kept.data.filter(isDownloading).length;
  useEffect(() => {
    const handle = window.setInterval(() => void kept.refresh(true), activeDownloads > 0 ? 3000 : 30000);
    return () => window.clearInterval(handle);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeDownloads]);

  // Models copied into the server's cache by hand are picked up once per
  // visit, as the old models page did on every load.
  const scanned = useRef(false);
  useEffect(() => {
    if (tab !== "local" || scanned.current || !can("llm.models:create")) return;
    scanned.current = true;
    modelsApi
      .scanLocal()
      .then((r) => (r.imported_count > 0 ? kept.refresh(true) : undefined))
      .catch(() => undefined);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tab]);

  function setTab(next: TabId) {
    const p = new URLSearchParams(params);
    p.set("tab", next);
    setParams(p, { replace: true });
  }

  function chooseCluster(id: string) {
    setClusterId(id);
    const p = new URLSearchParams(params);
    p.set("cluster", id);
    setParams(p, { replace: true });
  }

  const items = list.data?.items ?? [];
  const runnable = items.filter((m) => m.runnable);
  const hidden = items.length - runnable.length;
  const shown = useMemo(() => {
    let out = tab === "search" && !showUnrunnable ? runnable : items;
    if (onlyFits) out = out.filter(fits);
    return out;
  }, [items, runnable, tab, showUnrunnable, onlyFits]);

  const cluster = clusters.data.find((c) => c.environment_id === clusterId) ?? null;
  const open = (repo: string) => setOpenRepo(repo);
  const host = (repo: string) => {
    setOpenRepo(null);
    setHostRepo(repo);
  };

  return (
    <Stack spacing={2} sx={{ pb: 4 }}>
      <Stack direction={{ xs: "column", md: "row" }} spacing={2} alignItems={{ md: "center" }}>
        <Box sx={{ flexGrow: 1 }}>
          <Typography variant="h5" fontWeight={600}>
            {t("marketplace.title")}
          </Typography>
          <Typography variant="body2" color="text.secondary">
            {t("marketplace.subtitle")}
          </Typography>
        </Box>
        <Stack direction="row" spacing={1.5} alignItems="center" flexWrap="wrap" useFlexGap>
          {clusters.data.length > 0 && (
            <TextField
              select
              size="small"
              label={t("marketplace.fit_for")}
              value={clusterId ?? ""}
              onChange={(e) => chooseCluster(e.target.value)}
              sx={{ minWidth: 260 }}
              slotProps={{ htmlInput: { "data-testid": "market-cluster" } }}
            >
              {clusters.data.map((c) => (
                <MenuItem key={c.environment_id} value={c.environment_id}>
                  <Box>
                    <Typography variant="body2">{c.name}</Typography>
                    <Typography variant="caption" color="text.secondary">
                      {c.gpu_count > 0
                        ? t("hosting.cluster_hardware", {
                            count: c.gpu_count,
                            accelerator: c.accelerator ?? t("hosting.an_accelerator"),
                            memory: formatBytes(c.gpu_bytes),
                          })
                        : t("hosting.cluster_no_gpus")}
                    </Typography>
                  </Box>
                </MenuItem>
              ))}
            </TextField>
          )}
          {activeDownloads > 0 && (
            <Chip
              icon={<DownloadingIcon />}
              color="info"
              label={t("marketplace.downloads.chip", { count: activeDownloads })}
              onClick={() => setTab("local")}
              data-testid="market-downloads-chip"
            />
          )}
          <HuggingFaceAccessButton onChanged={() => void list.refresh(true)} />
        </Stack>
      </Stack>

      {!clusters.loading && clusters.data.length === 0 && (
        <Alert
          severity="info"
          action={
            <Button size="small" onClick={() => navigate("/admin/clusters")}>
              {t("hosting.go_to_clusters")}
            </Button>
          }
        >
          {t("marketplace.no_clusters")}
        </Alert>
      )}
      {list.data?.hub === "offline" && (
        <Alert severity="info" data-testid="market-offline">
          {t("marketplace.offline")}
        </Alert>
      )}
      {list.error && <Alert severity="error">{list.error}</Alert>}

      <Tabs
        value={tab}
        onChange={(_, v: TabId) => setTab(v)}
        variant="scrollable"
        allowScrollButtonsMobile
        sx={{ borderBottom: 1, borderColor: "divider" }}
      >
        {TABS.map((id) => (
          <Tab key={id} value={id} label={t(`marketplace.tab.${id}`)} data-testid={`market-tab-${id}`} />
        ))}
      </Tabs>

      {tab === "search" && (
        <Stack direction={{ xs: "column", md: "row" }} spacing={1.5} alignItems={{ md: "center" }}>
          <TextField
            size="small"
            fullWidth
            placeholder={t("marketplace.search_placeholder")}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            slotProps={{
              input: {
                startAdornment: (
                  <InputAdornment position="start">
                    <SearchIcon fontSize="small" />
                  </InputAdornment>
                ),
              },
              htmlInput: { "data-testid": "market-search" },
            }}
          />
          <ToggleButtonGroup exclusive size="small" value={task} onChange={(_, v: ModelTask | null) => v && setTask(v)}>
            {TASKS.map((id) => (
              <ToggleButton key={id} value={id} sx={{ textTransform: "none", whiteSpace: "nowrap" }} data-testid={`market-task-${id}`}>
                {t(`marketplace.task.${id}`)}
              </ToggleButton>
            ))}
          </ToggleButtonGroup>
          <TextField
            select
            size="small"
            label={t("marketplace.sort_label")}
            value={sort}
            onChange={(e) => setSort(e.target.value as MarketSort)}
            sx={{ minWidth: 170 }}
          >
            {SORTS.map((id) => (
              <MenuItem key={id} value={id}>
                {t(`marketplace.sort.${id}`)}
              </MenuItem>
            ))}
          </TextField>
        </Stack>
      )}

      {tab !== "local" && (
        <FormControlLabel
          control={<Switch size="small" checked={onlyFits} onChange={(e) => setOnlyFits(e.target.checked)} />}
          label={t("marketplace.only_fits")}
        />
      )}

      {tab === "local" ? (
        <KeptModels
          items={kept.data}
          loading={kept.loading}
          error={kept.error}
          onChanged={() => kept.refresh(true)}
          onHost={(k) => setHostKept(k)}
          onOpen={open}
          can={(permission) => can(permission)}
        />
      ) : list.loading && !list.data ? (
        <Loading />
      ) : tab === "recommended" ? (
        <Stack spacing={3}>
          {(list.data?.groups ?? []).map((group) => {
            const inGroup = shown.filter((m) => m.curated?.group === group);
            if (inGroup.length === 0) return null;
            return (
              <Box key={group} data-testid={`market-group-${group}`}>
                <Typography variant="h6" sx={{ mb: 0.25 }}>
                  {t(`marketplace.group.${group}`)}
                </Typography>
                <Typography variant="body2" color="text.secondary" sx={{ mb: 1.5 }}>
                  {t(`marketplace.group_help.${group}`)}
                </Typography>
                <ModelGrid items={inGroup} onOpen={open} onHost={host} canHost={canHost} />
              </Box>
            );
          })}
        </Stack>
      ) : shown.length === 0 && !list.loading ? (
        <Typography variant="body2" color="text.secondary" sx={{ py: 4, textAlign: "center" }}>
          {t("marketplace.search_empty")}
        </Typography>
      ) : (
        <Box sx={{ position: "relative" }}>
          {list.loading && (
            <Box sx={{ position: "absolute", top: -12, left: 0, right: 0 }}>
              <CircularProgress size={16} />
            </Box>
          )}
          <ModelGrid items={shown} onOpen={open} onHost={host} canHost={canHost} />
        </Box>
      )}

      {tab === "search" && hidden > 0 && (
        <Box>
          <Button size="small" onClick={() => setShowUnrunnable((v) => !v)} data-testid="market-show-unrunnable">
            {showUnrunnable ? t("marketplace.hide_unrunnable") : t("marketplace.show_unrunnable", { count: hidden })}
          </Button>
        </Box>
      )}

      {cluster && (
        <Typography variant="caption" color="text.secondary">
          {t("marketplace.fit_footnote")}
        </Typography>
      )}

      <ModelDetailDrawer
        repoId={openRepo}
        clusterId={clusterId}
        onClose={() => setOpenRepo(null)}
        onHost={host}
        canHost={canHost}
        onDownloaded={() => void kept.refresh(true)}
      />
      <HostModelDialog
        open={hostRepo !== null}
        repoId={hostRepo}
        clusterId={clusterId}
        onClose={() => setHostRepo(null)}
        onHosted={(id) => {
          setHostRepo(null);
          navigate(`/admin/deployments/${id}`);
        }}
      />
      <HostModelDialog
        open={hostKept !== null}
        models={hostKept ? [asModel(hostKept)] : []}
        keptModelId={hostKept?.model_id ?? null}
        clusterId={clusterId}
        onClose={() => setHostKept(null)}
        onHosted={(id) => {
          setHostKept(null);
          navigate(`/admin/deployments/${id}`);
        }}
      />
    </Stack>
  );
}
