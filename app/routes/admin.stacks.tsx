/**
 * Admin → Stacks page.
 * Deploy, update, and roll back compose stacks with MUI.
 */
import { useState, useEffect } from "react";
import { stacks, type StackSummary, type StackRevision, type StackDiff } from "~/api/admin";

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
  const [data, setData] = useState<StackSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
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

  async function load() {
    setLoading(true);
    setError(null);
    try {
      setData(await stacks.list());
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to load stacks.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { load(); }, []);

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
      setDeployError(e instanceof Error ? e.message : "Deploy failed.");
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
    if (!confirm(`Roll back ${selectedStack} to revision ${rev}?`)) return;
    try {
      await stacks.rollback(selectedStack, rev);
      await load();
      await handleLoadRevisions(selectedStack);
    } catch (e: unknown) {
      alert(e instanceof Error ? e.message : "Rollback failed.");
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
      setDiffError(e instanceof Error ? e.message : "Diff failed.");
    }
  }

  return (
    <Box sx={{ display: "flex", flexDirection: "column", height: "100%", overflow: "hidden" }}>
      {/* Fixed header area */}
      <Box sx={{ flexShrink: 0 }}>
        <Stack direction="row" alignItems="center" justifyContent="space-between" sx={{ mb: 2 }}>
          <Typography variant="h5">Stacks</Typography>
          <Stack direction="row" spacing={1}>
            {view !== "list" && (
              <Button
                variant="outlined"
                size="small"
                startIcon={<ArrowBackIcon />}
                onClick={() => setView("list")}
              >
                Back
              </Button>
            )}
            {view === "list" && (
              <Button
                variant="contained"
                size="small"
                startIcon={<RocketLaunchIcon />}
                onClick={() => setView("deploy")}
              >
                Deploy Stack
              </Button>
            )}
          </Stack>
        </Stack>
      </Box>

      {/* Scrollable content area */}
      <Box sx={{ flexGrow: 1, overflow: "auto" }}>

      {/* List view */}
      {view === "list" && (
        <>
          {loading && (
            <Box sx={{ display: "flex", justifyContent: "center", py: 6 }}>
              <CircularProgress size={32} />
            </Box>
          )}
          {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}
          {!loading && !error && (
            <TableContainer component={Paper} variant="outlined">
              <Table size="small" stickyHeader>
                <TableHead>
                  <TableRow>
                    <TableCell>Stack ID</TableCell>
                    <TableCell>Latest Rev</TableCell>
                    <TableCell>Updated</TableCell>
                    <TableCell align="right">Actions</TableCell>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {data.length === 0 && (
                    <TableRow>
                      <TableCell colSpan={4} align="center" sx={{ py: 4, color: "text.secondary" }}>
                        No stacks deployed.
                      </TableCell>
                    </TableRow>
                  )}
                  {data.map((s) => (
                    <TableRow key={s.stack_id}>
                      <TableCell>
                        <Typography variant="body2" fontFamily="monospace" fontWeight={600}>
                          {s.stack_id}
                        </Typography>
                      </TableCell>
                      <TableCell>
                        <Typography variant="body2" color="text.secondary">
                          v{s.latest_rev}
                        </Typography>
                      </TableCell>
                      <TableCell>
                        <Typography variant="body2" color="text.secondary" fontSize="0.8rem">
                          {new Date(s.created_at).toLocaleString()}
                        </Typography>
                      </TableCell>
                      <TableCell align="right">
                        <Button
                          size="small"
                          variant="outlined"
                          startIcon={<HistoryIcon />}
                          onClick={() => handleLoadRevisions(s.stack_id)}
                        >
                          Revisions
                        </Button>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </TableContainer>
          )}
        </>
      )}

      {/* Deploy form */}
      {view === "deploy" && (
        <Paper variant="outlined" sx={{ p: 4, maxWidth: 640 }}>
          <form onSubmit={handleDeploy}>
            <Typography variant="h6" gutterBottom>
              Deploy / Update Stack
            </Typography>
            <TextField
              label="Stack ID"
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
            {deployError && <Alert severity="error" sx={{ mb: 2 }}>{deployError}</Alert>}
            <Button
              type="submit"
              variant="contained"
              disabled={deploying}
              startIcon={deploying ? <CircularProgress size={16} /> : <RocketLaunchIcon />}
            >
              {deploying ? "Deploying…" : "Deploy"}
            </Button>
          </form>
        </Paper>
      )}

      {/* Revisions view */}
      {view === "revisions" && selectedStack && (
        <Box>
          <Typography variant="h6" gutterBottom>
            Revisions — <Box component="span" sx={{ fontFamily: "monospace" }}>{selectedStack}</Box>
          </Typography>

          {/* Diff form */}
          <Paper variant="outlined" sx={{ p: 2.5, mb: 2 }}>
            <form onSubmit={handleDiff}>
              <Stack direction="row" spacing={1.5} alignItems="flex-end">
                <TextField
                  label="From Rev"
                  type="number"
                  size="small"
                  sx={{ width: 100 }}
                  value={fromRev}
                  onChange={(e) => setFromRev(e.target.value)}
                  required
                />
                <TextField
                  label="To Rev"
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
                  View Diff
                </Button>
              </Stack>
              {diffError && <Alert severity="error" sx={{ mt: 1.5 }}>{diffError}</Alert>}
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
                  sx={{
                    bgcolor: "#0d1117",
                    p: 2,
                    height: 200,
                    overflow: "auto",
                    mt: 0.5,
                  }}
                >
                  <Box
                    component="pre"
                    sx={{ m: 0, fontFamily: "monospace", fontSize: "0.75rem", color: "#69f0ae" }}
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
                  sx={{
                    bgcolor: "#0d1117",
                    p: 2,
                    height: 200,
                    overflow: "auto",
                    mt: 0.5,
                  }}
                >
                  <Box
                    component="pre"
                    sx={{ m: 0, fontFamily: "monospace", fontSize: "0.75rem", color: "#69f0ae" }}
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
                  <TableCell>Rev</TableCell>
                  <TableCell>Created At</TableCell>
                  <TableCell align="right">Actions</TableCell>
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
                        Rollback to v{r.rev}
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
