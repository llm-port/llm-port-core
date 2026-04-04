/**
 * PII Activity Log — paginated list of PII scan events.
 *
 * Shows timestamp, operation, mode, entities found, entity-type counts,
 * source, and request ID.  **No raw PII text is displayed.**
 */
import { useEffect, useState, useCallback } from "react";
import { useTranslation } from "react-i18next";
import { fetchPIIEvents, type PIIEvent, type PIIEventsPage } from "~/api/pii";

import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Chip from "@mui/material/Chip";
import CircularProgress from "@mui/material/CircularProgress";
import FormControl from "@mui/material/FormControl";
import InputLabel from "@mui/material/InputLabel";
import MenuItem from "@mui/material/MenuItem";
import Paper from "@mui/material/Paper";
import Select from "@mui/material/Select";
import Stack from "@mui/material/Stack";
import Switch from "@mui/material/Switch";
import FormControlLabel from "@mui/material/FormControlLabel";
import Table from "@mui/material/Table";
import TableBody from "@mui/material/TableBody";
import TableCell from "@mui/material/TableCell";
import TableContainer from "@mui/material/TableContainer";
import TableHead from "@mui/material/TableHead";
import TablePagination from "@mui/material/TablePagination";
import TableRow from "@mui/material/TableRow";
import Typography from "@mui/material/Typography";
import { useTheme } from "@mui/material/styles";

const PAGE_SIZE = 25;

function formatDate(iso: string): string {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
}

function entityChips(counts: Record<string, number> | null) {
  if (!counts) return "—";
  return Object.entries(counts)
    .sort((a, b) => b[1] - a[1])
    .map(([type, cnt]) => (
      <Chip
        key={type}
        label={`${type} ×${cnt}`}
        size="small"
        variant="outlined"
        sx={{ mr: 0.5, mb: 0.5 }}
      />
    ));
}

export default function PIIActivityLogPage() {
  const { t } = useTranslation();
  const theme = useTheme();

  const [data, setData] = useState<PIIEventsPage | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Filters
  const [operation, setOperation] = useState<string>("");
  const [source, setSource] = useState<string>("");
  const [piiOnly, setPiiOnly] = useState(false);
  const [page, setPage] = useState(0);

  const reload = useCallback(() => {
    setLoading(true);
    setError(null);
    fetchPIIEvents({
      operation: operation || undefined,
      source: source || undefined,
      pii_only: piiOnly || undefined,
      limit: PAGE_SIZE,
      offset: page * PAGE_SIZE,
    })
      .then(setData)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, [operation, source, piiOnly, page]);

  useEffect(() => { reload(); }, [reload]);

  return (
    <Box sx={{ p: 0 }}>
      {/* ── Filters ────────────────────────────────────────────────── */}
      <Stack direction="row" spacing={2} sx={{ mb: 2 }} alignItems="center" flexWrap="wrap">
        <FormControl size="small" sx={{ minWidth: 140 }}>
          <InputLabel>{t("pii_log.operation_filter", "Operation")}</InputLabel>
          <Select
            value={operation}
            label={t("pii_log.operation_filter", "Operation")}
            onChange={(e) => { setOperation(e.target.value); setPage(0); }}
          >
            <MenuItem value="">{t("common.all", "All")}</MenuItem>
            <MenuItem value="scan">scan</MenuItem>
            <MenuItem value="redact">redact</MenuItem>
            <MenuItem value="sanitize">sanitize</MenuItem>
          </Select>
        </FormControl>

        <FormControl size="small" sx={{ minWidth: 120 }}>
          <InputLabel>{t("pii_log.source_filter", "Source")}</InputLabel>
          <Select
            value={source}
            label={t("pii_log.source_filter", "Source")}
            onChange={(e) => { setSource(e.target.value); setPage(0); }}
          >
            <MenuItem value="">{t("common.all", "All")}</MenuItem>
            <MenuItem value="api">api</MenuItem>
            <MenuItem value="gateway">gateway</MenuItem>
          </Select>
        </FormControl>

        <FormControlLabel
          control={
            <Switch
              checked={piiOnly}
              onChange={(e) => { setPiiOnly(e.target.checked); setPage(0); }}
              size="small"
            />
          }
          label={t("pii_log.pii_only", "PII detected only")}
        />
      </Stack>

      {/* ── Error state ────────────────────────────────────────────── */}
      {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}

      {/* ── Loading ────────────────────────────────────────────────── */}
      {loading && (
        <Box sx={{ display: "flex", justifyContent: "center", mt: 4 }}>
          <CircularProgress />
        </Box>
      )}

      {/* ── Table ──────────────────────────────────────────────────── */}
      {!loading && data && (
        <>
          <TableContainer component={Paper} variant="outlined">
            <Table size="small">
              <TableHead>
                <TableRow>
                  <TableCell>{t("pii_log.col_time", "Time")}</TableCell>
                  <TableCell>{t("pii_log.col_operation", "Operation")}</TableCell>
                  <TableCell>{t("pii_log.col_mode", "Mode")}</TableCell>
                  <TableCell align="center">{t("pii_log.col_pii", "PII?")}</TableCell>
                  <TableCell align="right">{t("pii_log.col_entities", "Entities")}</TableCell>
                  <TableCell>{t("pii_log.col_types", "Entity Types")}</TableCell>
                  <TableCell>{t("pii_log.col_source", "Source")}</TableCell>
                  <TableCell>{t("pii_log.col_lang", "Lang")}</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {data.items.length === 0 && (
                  <TableRow>
                    <TableCell colSpan={8} align="center">
                      <Typography variant="body2" color="text.secondary" sx={{ py: 4 }}>
                        {t("pii_log.no_events", "No PII events found.")}
                      </Typography>
                    </TableCell>
                  </TableRow>
                )}
                {data.items.map((ev) => (
                  <TableRow key={ev.id} hover>
                    <TableCell sx={{ whiteSpace: "nowrap" }}>
                      {formatDate(ev.created_at)}
                    </TableCell>
                    <TableCell>
                      <Chip label={ev.operation} size="small" color="default" />
                    </TableCell>
                    <TableCell>{ev.mode ?? "—"}</TableCell>
                    <TableCell align="center">
                      {ev.pii_detected ? (
                        <Chip label="Yes" size="small" color="warning" />
                      ) : (
                        <Chip label="No" size="small" color="success" variant="outlined" />
                      )}
                    </TableCell>
                    <TableCell align="right">
                      <Typography variant="body2" fontWeight={ev.entities_found > 0 ? 700 : 400}>
                        {ev.entities_found}
                      </Typography>
                    </TableCell>
                    <TableCell>{entityChips(ev.entity_type_counts)}</TableCell>
                    <TableCell>
                      <Chip
                        label={ev.source}
                        size="small"
                        color={ev.source === "gateway" ? "primary" : "default"}
                        variant="outlined"
                      />
                    </TableCell>
                    <TableCell>{ev.language}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </TableContainer>
          <TablePagination
            component="div"
            count={data.total}
            page={page}
            onPageChange={(_, p) => setPage(p)}
            rowsPerPage={PAGE_SIZE}
            rowsPerPageOptions={[PAGE_SIZE]}
          />
        </>
      )}
    </Box>
  );
}
