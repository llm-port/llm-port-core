/**
 * Admin → Images page.
 * Lists local images, pull form, and prune controls with MUI.
 */
import { useState, useEffect } from "react";
import { useOutletContext } from "react-router";
import { images, type ImageSummary, type PruneReport } from "~/api/admin";

import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import IconButton from "@mui/material/IconButton";
import Paper from "@mui/material/Paper";
import Stack from "@mui/material/Stack";
import Table from "@mui/material/Table";
import TableBody from "@mui/material/TableBody";
import TableCell from "@mui/material/TableCell";
import TableContainer from "@mui/material/TableContainer";
import TableHead from "@mui/material/TableHead";
import TableRow from "@mui/material/TableRow";
import TextField from "@mui/material/TextField";
import Tooltip from "@mui/material/Tooltip";
import Typography from "@mui/material/Typography";
import Alert from "@mui/material/Alert";
import Chip from "@mui/material/Chip";
import CircularProgress from "@mui/material/CircularProgress";

import RefreshIcon from "@mui/icons-material/Refresh";
import CloudDownloadIcon from "@mui/icons-material/CloudDownload";
import DeleteSweepIcon from "@mui/icons-material/DeleteSweep";

interface AdminContext {
  rootModeActive: boolean;
}

function bytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 ** 2) return `${(n / 1024).toFixed(1)} KB`;
  if (n < 1024 ** 3) return `${(n / 1024 ** 2).toFixed(1)} MB`;
  return `${(n / 1024 ** 3).toFixed(2)} GB`;
}

export default function ImagesPage() {
  const { rootModeActive } = useOutletContext<AdminContext>();
  const [data, setData] = useState<ImageSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [pullImage, setPullImage] = useState("");
  const [pullTag, setPullTag] = useState("latest");
  const [pulling, setPulling] = useState(false);
  const [pullError, setPullError] = useState<string | null>(null);
  const [pruneReport, setPruneReport] = useState<PruneReport | null>(null);
  const [pruning, setPruning] = useState(false);

  async function load() {
    setLoading(true);
    setError(null);
    try {
      setData(await images.list());
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to load images.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { load(); }, []);

  async function handlePull(e: React.FormEvent) {
    e.preventDefault();
    setPullError(null);
    setPulling(true);
    try {
      await images.pull(pullImage, pullTag);
      setPullImage("");
      setPullTag("latest");
      await load();
    } catch (e: unknown) {
      setPullError(e instanceof Error ? e.message : "Pull failed.");
    } finally {
      setPulling(false);
    }
  }

  async function handlePrune(dryRun: boolean) {
    setPruning(true);
    try {
      const report = await images.prune(dryRun);
      setPruneReport(report);
      if (!dryRun) await load();
    } catch (e: unknown) {
      alert(e instanceof Error ? e.message : "Prune failed.");
    } finally {
      setPruning(false);
    }
  }

  return (
    <Box sx={{ display: "flex", flexDirection: "column", height: "100%", overflow: "hidden" }}>
      {/* Fixed header area */}
      <Box sx={{ flexShrink: 0 }}>
        <Stack direction="row" alignItems="center" justifyContent="space-between" sx={{ mb: 2 }}>
          <Typography variant="h5">Images</Typography>
          <Tooltip title="Refresh">
            <IconButton onClick={load} size="small">
              <RefreshIcon />
            </IconButton>
          </Tooltip>
        </Stack>

      {/* Pull form */}
      <Paper variant="outlined" sx={{ p: 2.5, mb: 2 }}>
        <form onSubmit={handlePull}>
          <Stack direction="row" spacing={1.5} alignItems="flex-end" flexWrap="wrap">
            <TextField
              label="Image"
              size="small"
              placeholder="nginx"
              value={pullImage}
              onChange={(e) => setPullImage(e.target.value)}
              required
              sx={{ width: 220 }}
            />
            <TextField
              label="Tag"
              size="small"
              placeholder="latest"
              value={pullTag}
              onChange={(e) => setPullTag(e.target.value)}
              sx={{ width: 120 }}
            />
            <Button
              type="submit"
              variant="contained"
              disabled={pulling}
              startIcon={pulling ? <CircularProgress size={16} /> : <CloudDownloadIcon />}
            >
              {pulling ? "Pulling…" : "Pull Image"}
            </Button>
          </Stack>
          {pullError && <Alert severity="error" sx={{ mt: 1.5 }}>{pullError}</Alert>}
        </form>
      </Paper>

      {/* Prune */}
      <Paper variant="outlined" sx={{ p: 2.5, mb: 2 }}>
        <Stack direction="row" alignItems="center" spacing={2}>
          <Typography variant="body2" color="text.secondary" sx={{ flexGrow: 1 }}>
            Prune dangling images{" "}
            <Typography component="span" variant="caption" color="text.secondary">
              (requires Root Mode)
            </Typography>
          </Typography>
          <Button
            size="small"
            variant="outlined"
            disabled={!rootModeActive || pruning}
            onClick={() => handlePrune(true)}
          >
            Dry Run
          </Button>
          <Button
            size="small"
            variant="contained"
            color="error"
            disabled={!rootModeActive || pruning}
            startIcon={<DeleteSweepIcon />}
            onClick={() => handlePrune(false)}
          >
            {pruning ? "Pruning…" : "Prune"}
          </Button>
        </Stack>
      </Paper>

      {pruneReport && (
        <Alert severity={pruneReport.dry_run ? "info" : "success"} sx={{ mb: 2 }}>
          <strong>{pruneReport.dry_run ? "Dry Run — would prune:" : "Pruned:"}</strong>{" "}
          {pruneReport.deleted.length} image(s) / saved {bytes(pruneReport.space_reclaimed)}
        </Alert>
      )}
      </Box>

      {loading && (
        <Box sx={{ display: "flex", justifyContent: "center", py: 6 }}>
          <CircularProgress size={32} />
        </Box>
      )}
      {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}

      {!loading && !error && (
        <TableContainer component={Paper} variant="outlined" sx={{ flexGrow: 1, overflow: "auto" }}>
          <Table size="small" stickyHeader>
            <TableHead>
              <TableRow>
                <TableCell>Tags</TableCell>
                <TableCell>ID</TableCell>
                <TableCell>Size</TableCell>
                <TableCell>Created</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {data.length === 0 && (
                <TableRow>
                  <TableCell colSpan={4} align="center" sx={{ py: 4, color: "text.secondary" }}>
                    No images.
                  </TableCell>
                </TableRow>
              )}
              {data.map((img) => (
                <TableRow key={img.id}>
                  <TableCell>
                    {img.repo_tags.length > 0 ? (
                      <Stack direction="row" spacing={0.5} flexWrap="wrap" useFlexGap>
                        {img.repo_tags.map((t) => (
                          <Chip key={t} label={t} size="small" variant="outlined" sx={{ fontFamily: "monospace", fontSize: "0.75rem" }} />
                        ))}
                      </Stack>
                    ) : (
                      <Typography variant="caption" color="text.secondary">&lt;none&gt;</Typography>
                    )}
                  </TableCell>
                  <TableCell>
                    <Typography variant="body2" fontFamily="monospace" fontSize="0.8rem" color="text.secondary">
                      {img.id.slice(7, 19)}
                    </Typography>
                  </TableCell>
                  <TableCell>
                    <Typography variant="body2" fontSize="0.8rem">
                      {bytes(img.size)}
                    </Typography>
                  </TableCell>
                  <TableCell>
                    <Typography variant="body2" fontSize="0.8rem" color="text.secondary">
                      {img.created ? new Date(img.created).toLocaleDateString() : "—"}
                    </Typography>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </TableContainer>
      )}
    </Box>
  );
}
