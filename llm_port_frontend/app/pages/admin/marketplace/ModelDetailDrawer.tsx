/**
 * One model, in full: what its card says, how it fits each cluster, its files.
 */
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { marketplaceApi, type MarketDetail } from "~/api/marketplace";
import { formatBytes, formatTokens } from "~/lib/engine";
import { fitDetail, fitSummary, formatCount, formatParams } from "~/lib/hosting";
import { useAsyncData } from "~/lib/useAsyncData";

import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Chip from "@mui/material/Chip";
import CircularProgress from "@mui/material/CircularProgress";
import Divider from "@mui/material/Divider";
import Drawer from "@mui/material/Drawer";
import IconButton from "@mui/material/IconButton";
import Link from "@mui/material/Link";
import Paper from "@mui/material/Paper";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";

import CloseIcon from "@mui/icons-material/Close";
import OpenInNewIcon from "@mui/icons-material/OpenInNew";
import VerifiedIcon from "@mui/icons-material/Verified";

import { CapabilityChips } from "./ModelCard";

const TONE = { success: "success", info: "info", warning: "warning", error: "error", default: "default" } as const;
const FILES_SHOWN = 12;

export interface ModelDetailDrawerProps {
  repoId: string | null;
  clusterId: string | null;
  onClose: () => void;
  onHost: (repoId: string) => void;
  canHost: boolean;
}

export function ModelDetailDrawer({ repoId, clusterId, onClose, onHost, canHost }: ModelDetailDrawerProps) {
  const { t } = useTranslation();
  const state = useAsyncData(
    () => (repoId ? marketplaceApi.detail(repoId, clusterId) : Promise.resolve(null)),
    [repoId, clusterId],
    { initialValue: null as MarketDetail | null },
  );

  return (
    <Drawer
      anchor="right"
      open={repoId !== null}
      onClose={onClose}
      slotProps={{ paper: { sx: { width: { xs: "100%", sm: 560 } } } }}
    >
      <Stack direction="row" alignItems="center" sx={{ px: 2, py: 1.5 }} spacing={1}>
        <Typography variant="h6" sx={{ flexGrow: 1, minWidth: 0 }} noWrap title={repoId ?? ""}>
          {repoId?.split("/").pop()}
        </Typography>
        {repoId && (
          <IconButton
            component="a"
            href={`https://huggingface.co/${repoId}`}
            target="_blank"
            rel="noopener noreferrer"
            aria-label={t("marketplace.detail.open_hub")}
          >
            <OpenInNewIcon />
          </IconButton>
        )}
        <IconButton onClick={onClose} aria-label={t("common.close")}>
          <CloseIcon />
        </IconButton>
      </Stack>
      <Divider />
      <Box sx={{ p: 2, overflowY: "auto", flexGrow: 1 }}>
        {state.loading && !state.data ? (
          <Box sx={{ display: "flex", justifyContent: "center", p: 4 }}>
            <CircularProgress />
          </Box>
        ) : state.error ? (
          <Alert severity="error">{state.error}</Alert>
        ) : state.data ? (
          <DetailBody detail={state.data} />
        ) : null}
      </Box>
      {repoId && canHost && (
        <>
          <Divider />
          <Stack direction="row" spacing={1} sx={{ p: 2 }} justifyContent="flex-end">
            <Button onClick={onClose}>{t("common.close")}</Button>
            <Button
              variant="contained"
              disabled={!state.data || !state.data.model.runnable}
              onClick={() => onHost(repoId)}
              data-testid="detail-host"
            >
              {t("marketplace.host_on_cluster")}
            </Button>
          </Stack>
        </>
      )}
    </Drawer>
  );
}

function Fact({ label, value }: { label: string; value: React.ReactNode }) {
  if (value === null || value === undefined || value === "") return null;
  return (
    <Stack direction="row" spacing={2} sx={{ py: 0.25 }}>
      <Typography variant="body2" color="text.secondary" sx={{ minWidth: 150 }}>
        {label}
      </Typography>
      <Typography variant="body2" sx={{ wordBreak: "break-word" }}>
        {value}
      </Typography>
    </Stack>
  );
}

function DetailBody({ detail }: { detail: MarketDetail }) {
  const { t, i18n } = useTranslation();
  const [allFiles, setAllFiles] = useState(false);
  const m = detail.model;
  const blurb = m.curated?.blurb ? t(m.curated.blurb, { defaultValue: "" }) : "";
  const files = m.files ?? [];
  const shownFiles = allFiles ? files : files.slice(0, FILES_SHOWN);

  return (
    <Stack spacing={2.5}>
      <Stack spacing={1}>
        <Typography variant="body2" color="text.secondary">
          {m.repo_id}
        </Typography>
        {blurb && <Typography variant="body1">{blurb}</Typography>}
        {m.curated?.verified && (
          <Chip
            size="small"
            color="primary"
            variant="outlined"
            icon={<VerifiedIcon />}
            sx={{ alignSelf: "flex-start" }}
            label={t("marketplace.verified_help", { on: m.curated.verified.on, date: m.curated.verified.date })}
          />
        )}
        <CapabilityChips capabilities={m.capabilities} />
      </Stack>

      {detail.hub === "offline" && <Alert severity="info">{t("marketplace.detail.offline")}</Alert>}
      {!m.runnable && (
        <Alert severity="warning">{t(`marketplace.not_runnable.${m.not_runnable_reason ?? "task"}`)}</Alert>
      )}
      {m.gated && <Alert severity="info">{t("marketplace.detail.gated")}</Alert>}
      {m.needs_remote_code && <Alert severity="warning">{t("marketplace.detail.remote_code")}</Alert>}

      <Box>
        <Typography variant="subtitle2" gutterBottom>
          {t("marketplace.detail.fit_title")}
        </Typography>
        {detail.clusters.length === 0 ? (
          <Typography variant="body2" color="text.secondary">
            {t("marketplace.no_clusters")}
          </Typography>
        ) : (
          <Stack spacing={1}>
            {detail.clusters.map((c) => {
              const fit = detail.fits[c.environment_id];
              const summary = fitSummary(fit);
              return (
                <Paper key={c.environment_id} variant="outlined" sx={{ p: 1.25 }} data-testid={`detail-fit-${c.environment_id}`}>
                  <Stack direction="row" spacing={1} alignItems="center">
                    <Typography variant="body2" fontWeight={600} sx={{ flexGrow: 1 }}>
                      {c.name}
                    </Typography>
                    <Chip size="small" label={summary.label} color={TONE[summary.tone]} />
                  </Stack>
                  <Typography variant="caption" color="text.secondary">
                    {fitDetail(fit, c)}
                  </Typography>
                </Paper>
              );
            })}
          </Stack>
        )}
      </Box>

      {m.summary && (
        <Box>
          <Typography variant="subtitle2" gutterBottom>
            {t("marketplace.detail.about")}
          </Typography>
          <Typography variant="body2" sx={{ whiteSpace: "pre-line" }}>
            {m.summary}
          </Typography>
          <Link href={`https://huggingface.co/${m.repo_id}`} target="_blank" rel="noopener noreferrer" variant="body2">
            {t("marketplace.detail.read_more")}
          </Link>
        </Box>
      )}

      <Box>
        <Typography variant="subtitle2" gutterBottom>
          {t("marketplace.detail.facts")}
        </Typography>
        <Fact label={t("marketplace.detail.parameters")} value={formatParams(m.params_b, m.active_params_b)} />
        <Fact label={t("marketplace.detail.weights")} value={m.weights_bytes ? formatBytes(m.weights_bytes) : null} />
        <Fact label={t("marketplace.detail.context")} value={m.max_context ? t("marketplace.detail.tokens", { value: formatTokens(m.max_context) }) : null} />
        <Fact label={t("marketplace.detail.precision")} value={[m.dtype, m.quantization?.toUpperCase()].filter(Boolean).join(" · ")} />
        <Fact label={t("marketplace.detail.format")} value={m.format} />
        <Fact label={t("marketplace.detail.architecture")} value={m.architecture} />
        <Fact label={t("marketplace.detail.base_model")} value={m.base_model} />
        <Fact label={t("marketplace.detail.license")} value={m.license} />
        <Fact label={t("marketplace.detail.downloads")} value={m.downloads ? formatCount(m.downloads) : null} />
        <Fact label={t("marketplace.detail.likes")} value={m.likes ? formatCount(m.likes) : null} />
        <Fact
          label={t("marketplace.detail.updated")}
          value={m.last_modified ? new Date(m.last_modified).toLocaleDateString(i18n.language) : null}
        />
        <Fact
          label={t("marketplace.detail.on_server")}
          value={
            detail.local
              ? detail.local.status === "downloading"
                ? t("marketplace.local_downloading")
                : detail.local.deployments > 0
                  ? t("marketplace.local_deployed", { count: detail.local.deployments })
                  : t("marketplace.local_kept")
              : t("marketplace.detail.not_on_server")
          }
        />
      </Box>

      {files.length > 0 && (
        <Box>
          <Typography variant="subtitle2" gutterBottom>
            {t("marketplace.detail.files", { count: files.length, size: formatBytes(m.total_bytes ?? null) })}
          </Typography>
          <Stack spacing={0.25}>
            {shownFiles.map((f) => (
              <Stack key={f.name} direction="row" spacing={1}>
                <Typography variant="caption" sx={{ fontFamily: "monospace", flexGrow: 1, wordBreak: "break-all" }}>
                  {f.name}
                </Typography>
                <Typography variant="caption" color="text.secondary">
                  {f.size ? formatBytes(f.size) : ""}
                </Typography>
              </Stack>
            ))}
          </Stack>
          {files.length > FILES_SHOWN && (
            <Button size="small" onClick={() => setAllFiles((v) => !v)} sx={{ mt: 0.5 }}>
              {allFiles ? t("marketplace.detail.fewer_files") : t("marketplace.detail.all_files", { count: files.length })}
            </Button>
          )}
        </Box>
      )}
    </Stack>
  );
}
