/**
 * The vLLM a machine already runs that LLM.Port did not start (Phase 8).
 *
 * Listed from what the machine's agent reports, with each running one asked
 * what it serves. Routing one puts it behind the gateway under a name and
 * touches nothing on the machine; stopping routing takes it out again.
 */
import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import Alert from "@mui/material/Alert";
import Button from "@mui/material/Button";
import Card from "@mui/material/Card";
import CardContent from "@mui/material/CardContent";
import Chip from "@mui/material/Chip";
import CircularProgress from "@mui/material/CircularProgress";
import Dialog from "@mui/material/Dialog";
import DialogActions from "@mui/material/DialogActions";
import DialogContent from "@mui/material/DialogContent";
import DialogTitle from "@mui/material/DialogTitle";
import Stack from "@mui/material/Stack";
import Table from "@mui/material/Table";
import TableBody from "@mui/material/TableBody";
import TableCell from "@mui/material/TableCell";
import TableHead from "@mui/material/TableHead";
import TableRow from "@mui/material/TableRow";
import TextField from "@mui/material/TextField";
import Tooltip from "@mui/material/Tooltip";
import Typography from "@mui/material/Typography";

import { inferenceApi, type FoundEntry } from "~/api/inference";

/** A name to suggest: what the container answers to, without an org prefix. */
export function suggestName(entry: FoundEntry): string {
  const served = entry.container.served_model_names[0] ?? entry.container.model ?? entry.container.name;
  return served.split("/").pop()!.toLowerCase();
}

function managedBy(value: string | null, t: (k: string, o?: Record<string, unknown>) => string): string | null {
  if (!value) return null;
  if (value.startsWith("compose:")) return t("found.managed_by_compose", { name: value.slice(8) });
  return t("found.managed_by", { name: value });
}

export default function FoundVllmCard({ nodeId, reported }: { nodeId: string; reported: boolean }) {
  const { t } = useTranslation();
  const [entries, setEntries] = useState<FoundEntry[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [routing, setRouting] = useState<FoundEntry | null>(null);
  const [alias, setAlias] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      setEntries(await inferenceApi.found(nodeId));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [nodeId]);

  useEffect(() => {
    void load();
    const timer = window.setInterval(() => void load(), 30_000);
    return () => window.clearInterval(timer);
  }, [load]);

  async function route() {
    if (!routing) return;
    setBusy(true);
    setError(null);
    try {
      await inferenceApi.routeFound(nodeId, routing.container.name, alias.trim());
      setRouting(null);
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function release(entry: FoundEntry) {
    if (!entry.adoption) return;
    if (!window.confirm(t("found.confirm_release", { alias: entry.adoption.alias }))) return;
    setBusy(true);
    try {
      await inferenceApi.releaseFound(entry.adoption.id);
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card variant="outlined" data-testid="found-vllm">
      <CardContent sx={{ py: 1.5, "&:last-child": { pb: 1.5 } }}>
        <Typography variant="subtitle2">{t("found.title")}</Typography>
        <Typography variant="caption" color="text.secondary" component="p" sx={{ mb: 1 }}>
          {t("found.intro")}
        </Typography>
        {error && !routing && <Alert severity="error" sx={{ mb: 1 }}>{error}</Alert>}
        {entries === null && !error && <CircularProgress size={18} />}
        {entries !== null && entries.length === 0 && (
          <Typography variant="body2" color="text.secondary">
            {reported ? t("found.none") : t("found.not_reported")}
          </Typography>
        )}
        {entries !== null && entries.length > 0 && (
          <Table size="small">
            <TableHead>
              <TableRow>
                <TableCell>{t("found.col_container")}</TableCell>
                <TableCell>{t("found.col_model")}</TableCell>
                <TableCell>{t("found.col_state")}</TableCell>
                <TableCell>{t("found.col_port")}</TableCell>
                <TableCell>{t("found.col_route")}</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {entries.map((entry) => {
                const c = entry.container;
                const who = managedBy(c.managed_by, t);
                return (
                  <TableRow key={c.name} data-testid={`found-${c.name}`}>
                    <TableCell>
                      <Typography variant="body2" sx={{ fontFamily: "monospace" }}>{c.name}</Typography>
                      <Typography variant="caption" color="text.secondary">
                        {c.image}{who ? ` · ${who}` : ""}
                      </Typography>
                    </TableCell>
                    <TableCell>
                      <Typography variant="body2">{c.model ?? "-"}</Typography>
                      {c.task && (
                        <Typography variant="caption" color="text.secondary">
                          {t(`found.task_${c.task}`)}
                          {c.task_from === "name" ? ` (${t("found.task_guessed")})` : ""}
                        </Typography>
                      )}
                      {entry.check?.ok && (
                        <Typography variant="caption" color="text.secondary" component="div">
                          {t("found.answers", { models: entry.check.models.join(", ") })}
                        </Typography>
                      )}
                    </TableCell>
                    <TableCell>
                      <Chip
                        size="small"
                        variant="outlined"
                        label={c.state}
                        color={c.state === "running" ? "success" : "default"}
                      />
                    </TableCell>
                    <TableCell sx={{ fontFamily: "monospace" }}>
                      {c.host_port ?? t("found.not_published")}
                    </TableCell>
                    <TableCell>
                      {entry.adoption ? (
                        <Stack spacing={0.5} alignItems="flex-start">
                          <Chip size="small" color="primary" label={t("found.routed_as", { alias: entry.adoption.alias })} />
                          {c.state !== "running" && (
                            <Typography variant="caption" color="warning.main">
                              {t("found.paused", { state: c.state })}
                            </Typography>
                          )}
                          <Button size="small" color="inherit" disabled={busy} onClick={() => void release(entry)}>
                            {t("found.release")}
                          </Button>
                        </Stack>
                      ) : (
                        <Tooltip title={entry.reason ?? ""}>
                          <span>
                            <Button
                              size="small"
                              variant="outlined"
                              disabled={!entry.can_route || busy}
                              onClick={() => {
                                setError(null);
                                setAlias(suggestName(entry));
                                setRouting(entry);
                              }}
                              data-testid={`found-route-${c.name}`}
                            >
                              {t("found.route")}
                            </Button>
                          </span>
                        </Tooltip>
                      )}
                      {!entry.adoption && entry.reason && (
                        <Typography variant="caption" color="text.secondary" component="div" sx={{ maxWidth: 260 }}>
                          {entry.reason}
                        </Typography>
                      )}
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        )}
      </CardContent>

      <Dialog open={routing !== null} onClose={() => setRouting(null)} maxWidth="sm" fullWidth>
        <DialogTitle>{t("found.route_title", { container: routing?.container.name ?? "" })}</DialogTitle>
        <DialogContent>
          <Typography variant="body2" sx={{ mb: 2 }}>{t("found.route_explain")}</Typography>
          {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}
          <TextField
            autoFocus
            fullWidth
            size="small"
            label={t("found.name")}
            value={alias}
            onChange={(e) => setAlias(e.target.value)}
            inputProps={{ "data-testid": "found-alias" }}
          />
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setRouting(null)}>{t("common.cancel")}</Button>
          <Button variant="contained" disabled={!alias.trim() || busy} onClick={() => void route()} data-testid="found-route-confirm">
            {t("found.route")}
          </Button>
        </DialogActions>
      </Dialog>
    </Card>
  );
}
