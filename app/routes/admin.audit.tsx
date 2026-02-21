/**
 * Admin → Audit Log page.
 * Read-only, filterable view of all audit events with MUI.
 */
import { useState, useEffect } from "react";
import { audit, type AuditEvent } from "~/api/admin";

import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Chip from "@mui/material/Chip";
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
import CircularProgress from "@mui/material/CircularProgress";

import RefreshIcon from "@mui/icons-material/Refresh";
import FilterListIcon from "@mui/icons-material/FilterList";

export default function AuditLogPage() {
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
      setError(e instanceof Error ? e.message : "Failed to load audit log.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { load(); }, []);

  return (
    <Box sx={{ display: "flex", flexDirection: "column", height: "100%", overflow: "hidden" }}>
      {/* Fixed header area */}
      <Box sx={{ flexShrink: 0 }}>
        <Stack direction="row" alignItems="center" justifyContent="space-between" sx={{ mb: 2 }}>
          <Typography variant="h5">Audit Log</Typography>
          <Tooltip title="Refresh">
            <IconButton onClick={load} size="small">
              <RefreshIcon />
            </IconButton>
          </Tooltip>
        </Stack>

        {/* Filters */}
        <Paper variant="outlined" sx={{ p: 2, mb: 2 }}>
          <Stack direction="row" spacing={1.5} alignItems="flex-end">
            <TextField
              label="Filter by action"
              size="small"
              value={filterAction}
              onChange={(e) => setFilterAction(e.target.value)}
              sx={{ width: 200 }}
            />
            <TextField
              label="Filter by target ID"
              size="small"
              value={filterTarget}
              onChange={(e) => setFilterTarget(e.target.value)}
              sx={{ width: 200 }}
            />
            <Button
              variant="outlined"
              size="small"
              startIcon={<FilterListIcon />}
              onClick={load}
            >
              Apply
            </Button>
          </Stack>
        </Paper>
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
                <TableCell>Time</TableCell>
                <TableCell>Action</TableCell>
                <TableCell>Target</TableCell>
                <TableCell>Result</TableCell>
                <TableCell>Severity</TableCell>
                <TableCell>Actor</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {data.length === 0 && (
                <TableRow>
                  <TableCell colSpan={6} align="center" sx={{ py: 4, color: "text.secondary" }}>
                    No events.
                  </TableCell>
                </TableRow>
              )}
              {data.map((ev) => (
                <TableRow key={ev.id}>
                  <TableCell>
                    <Typography variant="body2" color="text.secondary" fontSize="0.8rem" noWrap>
                      {new Date(ev.time).toLocaleString()}
                    </Typography>
                  </TableCell>
                  <TableCell>
                    <Typography variant="body2" fontFamily="monospace" fontSize="0.8rem">
                      {ev.action}
                    </Typography>
                  </TableCell>
                  <TableCell>
                    <Typography variant="body2" fontFamily="monospace" fontSize="0.8rem" color="text.secondary">
                      <Box component="span" sx={{ color: "text.disabled", mr: 0.5 }}>{ev.target_type}/</Box>
                      {ev.target_id.slice(0, 24)}
                    </Typography>
                  </TableCell>
                  <TableCell>
                    <Chip
                      label={ev.result}
                      size="small"
                      color={ev.result === "allow" ? "success" : "error"}
                      variant="outlined"
                    />
                  </TableCell>
                  <TableCell>
                    <Chip
                      label={ev.severity}
                      size="small"
                      color={ev.severity === "high" ? "error" : "default"}
                      variant="filled"
                    />
                  </TableCell>
                  <TableCell>
                    <Typography variant="body2" fontFamily="monospace" fontSize="0.8rem" color="text.secondary">
                      {ev.actor_id?.slice(0, 8) ?? "—"}
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
