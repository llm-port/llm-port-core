/**
 * Admin → Stacks page.
 * Deploy, update, and roll back compose stacks. List view uses DataTable.
 */
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { stacks, type StackSummary, type StackRevision, type StackDiff } from "~/api/admin";
import { DataTable, type ColumnDef } from "~/components/DataTable";
import { useAsyncData } from "~/lib/useAsyncData";

import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Grid from "@mui/material/Grid";
import Paper from "@mui/material/Paper";
import Stack from "@mui/material/Stack";
import Table from "@mui/material/Table";
import TableBody from "@mui/material/TableBody";
import TableCell from "@mui/material/TableCell";
import TableContainer from "@mui/material/TableContainer";
import TableHead from "@mui/material/TableHead";
import TableRow from "@mui/material/TableRow";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";
import Alert from "@mui/material/Alert";
import CircularProgress from "@mui/material/CircularProgress";

import ArrowBackIcon from "@mui/icons-material/ArrowBack";
import RocketLaunchIcon from "@mui/icons-material/RocketLaunch";
import HistoryIcon from "@mui/icons-material/History";
import CompareArrowsIcon from "@mui/icons-material/CompareArrows";

type View = "list" | "deploy" | "revisions" | "diff";

export default function StacksPage() {
  const { t } = useTranslation();
  const {
    data,
    loading,
    error,
    refresh: load,
    setError,
  } = useAsyncData(
    () => stacks.list(),
    [],
    { initialValue: [] as StackSummary[] },
  );
  const [view, setView] = useState<View>("list");
  const [selectedStack, setSelectedStack] = useState<string | null>(null);

  const [stackId, setStackId] = useState("");
  const [composeYaml, setComposeYaml] = useState("");
  const [deploying, setDeploying] = useState(false);
  const [deployError, setDeployError] = useState<string | null>(null);

  const [revisions, setRevisions] = useState<StackRevision[]>([]);

  const [fromRev, setFromRev] = useState("");
  const [toRev, setToRev] = useState("");
  const [diff, setDiff] = useState<StackDiff | null>(null);
  const [diffError, setDiffError] = useState<string | null>(null);
  const columns: ColumnDef<StackSummary>[] = [
    {
      key: "stack_id",
      label: t("stacks.stack_id"),
      sortable: true,
      searchValue: (s) => s.stack_id,
      render: (s) => (
        <Typography variant="body2" fontFamily="monospace" fontWeight={600}>
          {s.stack_id}
        </Typography>
      ),
    },
    {
      key: "latest_rev",
      label: t("stacks.latest_rev"),
      sortable: true,
      sortValue: (s) => s.latest_rev,
      render: (s) => (
        <Typography variant="body2" color="text.secondary">
          v{s.latest_rev}
        </Typography>
      ),
    },
    {
      key: "created_at",
      label: t("stacks.updated"),
      sortable: true,
      sortValue: (s) => new Date(s.created_at).getTime(),
      render: (s) => (
        <Typography variant="body2" color="text.secondary" fontSize="0.8rem">
          {new Date(s.created_at).toLocaleString()}
        </Typography>
      ),
    },
  ];

  async function handleDeploy(e: React.FormEvent) {
    e.preventDefault();
    setDeployError(null);
    setDeploying(true);
    try {
      await stacks.deploy({ stack_id: stackId, compose_yaml: composeYaml });
      setView("list");
      setStackId("");
      setComposeYaml("");
      await load();
    } catch (e: unknown) {
      setDeployError(e instanceof Error ? e.message : t("stacks.deploy_failed"));
    } finally {
      setDeploying(false);
    }
  }

  async function handleLoadRevisions(sid: string) {
    setSelectedStack(sid);
    const revs = await stacks.revisions(sid);
    setRevisions(revs);
    setView("revisions");
  }

  async function handleRollback(rev: number) {
    if (!selectedStack) return;
    if (!confirm(t("stacks.confirm_rollback", { stack: selectedStack, rev }))) return;
    try {
      await stacks.rollback(selectedStack, rev);
      await load();
      await handleLoadRevisions(selectedStack);
    } catch (e: unknown) {
      alert(e instanceof Error ? e.message : t("stacks.rollback_failed"));
    }
  }

  async function handleDiff(e: React.FormEvent) {
    e.preventDefault();
    if (!selectedStack) return;
    setDiffError(null);
    try {
      const d = await stacks.diff(selectedStack, Number(fromRev), Number(toRev));
      setDiff(d);
    } catch (e: unknown) {
      setDiffError(e instanceof Error ? e.message : t("stacks.diff_failed"));
    }
  }

  return (
    <Box sx={{ display: "flex", flexDirection: "column", height: "100%", overflow: "hidden" }}>
      {/* Fixed header area */}
      <Box sx={{ flexShrink: 0 }}>
        <Stack direction="row" alignItems="center" justifyContent="space-between" sx={{ mb: 2 }}>
          <Stack direction="row" spacing={1}>
            {view !== "list" && (
              <Button
                variant="outlined"
                size="small"
                startIcon={<ArrowBackIcon />}
                onClick={() => setView("list")}
              >
                {t("common.back")}
              </Button>
            )}
          </Stack>
        </Stack>
      </Box>

      {/* Scrollable content area */}
      <Box sx={{ flexGrow: 1, overflow: "auto", display: "flex", flexDirection: "column" }}>
        {/* List view */}
        {view === "list" && (
          <DataTable
            title={t("stacks.title")}
            columns={columns}
            rows={data}
            rowKey={(s) => s.stack_id}
            loading={loading}
            error={error}
            emptyMessage={t("stacks.empty")}
            onRefresh={load}
            searchPlaceholder={t("stacks.search_placeholder")}
            onRowClick={(s) => handleLoadRevisions(s.stack_id)}
            toolbarActions={
              <Button
                variant="contained"
                size="small"
                startIcon={<RocketLaunchIcon />}
                onClick={() => setView("deploy")}
              >
                {t("stacks.deploy_stack")}
              </Button>
            }
          />
        )}

        {/* Deploy form */}
        {view === "deploy" && (
          <Paper variant="outlined" sx={{ p: 4, maxWidth: 640, flexShrink: 0 }}>
            <form onSubmit={handleDeploy}>
              <Typography variant="h6" gutterBottom>
                {t("stacks.deploy_update")}
              </Typography>
              <TextField
                label={t("stacks.stack_id")}
                fullWidth
                size="small"
                placeholder="my-app"
                value={stackId}
                onChange={(e) => setStackId(e.target.value)}
                required
                sx={{ mb: 2 }}
              />
              <TextField
                label="docker-compose.yaml"
                fullWidth
                multiline
                rows={10}
                placeholder={"services:\n  web:\n    image: nginx:latest"}
                value={composeYaml}
                onChange={(e) => setComposeYaml(e.target.value)}
                required
                inputProps={{ style: { fontFamily: "monospace", fontSize: "0.8rem" } }}
                sx={{ mb: 2 }}
              />
              {deployError && (
                <Alert severity="error" sx={{ mb: 2 }}>
                  {deployError}
                </Alert>
              )}
              <Button
                type="submit"
                variant="contained"
                disabled={deploying}
                startIcon={
                  deploying ? <CircularProgress size={16} /> : <RocketLaunchIcon />
                }
              >
                {deploying ? t("stacks.deploying") : t("common.deploy")}
              </Button>
            </form>
          </Paper>
        )}

        {/* Revisions view */}
        {view === "revisions" && selectedStack && (
          <Box sx={{ flexShrink: 0 }}>
            <Typography variant="h6" gutterBottom>
              {t("stacks.revisions")} —{" "}
              <Box component="span" sx={{ fontFamily: "monospace" }}>
                {selectedStack}
              </Box>
            </Typography>

            {/* Diff form */}
            <Paper variant="outlined" sx={{ p: 2.5, mb: 2 }}>
              <form onSubmit={handleDiff}>
                <Stack direction="row" spacing={1.5} alignItems="flex-end">
                  <TextField
                    label={t("stacks.from_rev")}
                    type="number"
                    size="small"
                    sx={{ width: 100 }}
                    value={fromRev}
                    onChange={(e) => setFromRev(e.target.value)}
                    required
                  />
                  <TextField
                    label={t("stacks.to_rev")}
                    type="number"
                    size="small"
                    sx={{ width: 100 }}
                    value={toRev}
                    onChange={(e) => setToRev(e.target.value)}
                    required
                  />
                  <Button
                    type="submit"
                    variant="outlined"
                    size="small"
                    startIcon={<CompareArrowsIcon />}
                  >
                    {t("stacks.view_diff")}
                  </Button>
                </Stack>
                {diffError && (
                  <Alert severity="error" sx={{ mt: 1.5 }}>
                    {diffError}
                  </Alert>
                )}
              </form>
            </Paper>

            {diff && (
              <Grid container spacing={2} sx={{ mb: 2 }}>
                <Grid size={{ xs: 12, md: 6 }}>
                  <Typography variant="caption" color="text.secondary" fontWeight={600}>
                    v{diff.from_rev}
                  </Typography>
                  <Paper
                    variant="outlined"
                    sx={(theme) => ({
                      bgcolor: theme.palette.mode === "dark" ? "grey.900" : "grey.100",
                      p: 2,
                      height: 200,
                      overflow: "auto",
                      mt: 0.5,
                    })}
                  >
                    <Box
                      component="pre"
                      sx={{ m: 0, fontFamily: "monospace", fontSize: "0.75rem", color: "success.main" }}
                    >
                      {diff.compose_yaml_from}
                    </Box>
                  </Paper>
                </Grid>
                <Grid size={{ xs: 12, md: 6 }}>
                  <Typography variant="caption" color="text.secondary" fontWeight={600}>
                    v{diff.to_rev}
                  </Typography>
                  <Paper
                    variant="outlined"
                    sx={(theme) => ({
                      bgcolor: theme.palette.mode === "dark" ? "grey.900" : "grey.100",
                      p: 2,
                      height: 200,
                      overflow: "auto",
                      mt: 0.5,
                    })}
                  >
                    <Box
                      component="pre"
                      sx={{ m: 0, fontFamily: "monospace", fontSize: "0.75rem", color: "success.main" }}
                    >
                      {diff.compose_yaml_to}
                    </Box>
                  </Paper>
                </Grid>
              </Grid>
            )}

            <TableContainer component={Paper} variant="outlined">
              <Table size="small" stickyHeader>
                <TableHead>
                  <TableRow>
                    <TableCell>{t("stacks.rev")}</TableCell>
                    <TableCell>{t("stacks.created_at")}</TableCell>
                    <TableCell align="right">{t("common.actions")}</TableCell>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {revisions.map((r) => (
                    <TableRow key={r.id}>
                      <TableCell>
                        <Typography variant="body2" fontFamily="monospace" fontWeight={600}>
                          v{r.rev}
                        </Typography>
                      </TableCell>
                      <TableCell>
                        <Typography variant="body2" color="text.secondary" fontSize="0.8rem">
                          {new Date(r.created_at).toLocaleString()}
                        </Typography>
                      </TableCell>
                      <TableCell align="right">
                        <Button
                          size="small"
                          variant="outlined"
                          color="warning"
                          onClick={() => handleRollback(r.rev)}
                        >
                          {t("stacks.rollback_to", { rev: r.rev })}
                        </Button>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </TableContainer>
          </Box>
        )}
      </Box>
    </Box>
  );
}
