/**
 * Pricing tab — CRUD editor for price_catalog entries.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import {
  observability,
  type PricingEntry,
  type PricingCreate,
} from "~/api/observability";

import Alert from "@mui/material/Alert";
import Autocomplete from "@mui/material/Autocomplete";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Chip from "@mui/material/Chip";
import CircularProgress from "@mui/material/CircularProgress";
import Collapse from "@mui/material/Collapse";
import Dialog from "@mui/material/Dialog";
import DialogActions from "@mui/material/DialogActions";
import DialogContent from "@mui/material/DialogContent";
import DialogTitle from "@mui/material/DialogTitle";
import IconButton from "@mui/material/IconButton";
import Stack from "@mui/material/Stack";
import Table from "@mui/material/Table";
import TableBody from "@mui/material/TableBody";
import TableCell from "@mui/material/TableCell";
import TableContainer from "@mui/material/TableContainer";
import TableHead from "@mui/material/TableHead";
import TableRow from "@mui/material/TableRow";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";
import AddIcon from "@mui/icons-material/Add";
import DeleteIcon from "@mui/icons-material/Delete";
import EditIcon from "@mui/icons-material/Edit";
import HistoryIcon from "@mui/icons-material/History";
import ExpandMoreIcon from "@mui/icons-material/ExpandMore";
import ExpandLessIcon from "@mui/icons-material/ExpandLess";

function fmtDate(iso: string) {
  return new Date(iso).toLocaleString();
}

export default function PricingTab() {
  const { t } = useTranslation();
  const [entries, setEntries] = useState<PricingEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [createOpen, setCreateOpen] = useState(false);
  const [editEntry, setEditEntry] = useState<PricingEntry | null>(null);
  const [historyFor, setHistoryFor] = useState<{
    provider: string;
    model: string;
  } | null>(null);
  const [history, setHistory] = useState<PricingEntry[]>([]);
  const [historyLoading, setHistoryLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await observability.listPricing();
      setEntries(data);
    } catch (err: unknown) {
      setError(
        err instanceof Error
          ? err.message
          : t("observability.load_pricing_failed"),
      );
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const handleDelete = async (id: string) => {
    try {
      await observability.deactivatePricing(id);
      await load();
    } catch (err: unknown) {
      setError(
        err instanceof Error ? err.message : t("observability.delete_failed"),
      );
    }
  };

  const handleShowHistory = async (provider: string, model: string) => {
    if (historyFor?.provider === provider && historyFor?.model === model) {
      setHistoryFor(null);
      return;
    }
    setHistoryFor({ provider, model });
    setHistoryLoading(true);
    try {
      const h = await observability.pricingHistory(provider, model);
      setHistory(h);
    } catch {
      setHistory([]);
    } finally {
      setHistoryLoading(false);
    }
  };

  return (
    <Box>
      <Alert severity="info" sx={{ mb: 2 }}>
        {t("observability.pricing_info")}
      </Alert>

      <Stack direction="row" justifyContent="flex-end" sx={{ mb: 2 }}>
        <Button
          variant="contained"
          size="small"
          startIcon={<AddIcon />}
          onClick={() => setCreateOpen(true)}
        >
          {t("observability.add_price")}
        </Button>
      </Stack>

      {error && (
        <Alert severity="error" sx={{ mb: 2 }}>
          {error}
        </Alert>
      )}

      {loading ? (
        <Box sx={{ display: "flex", justifyContent: "center", py: 4 }}>
          <CircularProgress />
        </Box>
      ) : entries.length === 0 ? (
        <Typography color="text.secondary" sx={{ py: 4, textAlign: "center" }}>
          {t("observability.no_pricing_entries")}
        </Typography>
      ) : (
        <TableContainer>
          <Table size="small">
            <TableHead>
              <TableRow>
                <TableCell>{t("observability.col_provider")}</TableCell>
                <TableCell>{t("observability.col_model")}</TableCell>
                <TableCell align="right">
                  {t("observability.col_input_price")}
                </TableCell>
                <TableCell align="right">
                  {t("observability.col_output_price")}
                </TableCell>
                <TableCell>{t("observability.col_currency")}</TableCell>
                <TableCell>{t("observability.col_source")}</TableCell>
                <TableCell>{t("observability.col_effective")}</TableCell>
                <TableCell align="right">
                  {t("observability.col_actions")}
                </TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {entries.map((e) => (
                <TableRow key={e.id} hover>
                  <TableCell>{e.provider}</TableCell>
                  <TableCell>{e.model}</TableCell>
                  <TableCell align="right">
                    {Number(e.input_price_per_1k).toFixed(6)}
                  </TableCell>
                  <TableCell align="right">
                    {Number(e.output_price_per_1k).toFixed(6)}
                  </TableCell>
                  <TableCell>{e.currency}</TableCell>
                  <TableCell>
                    <Chip
                      label={e.source ?? "—"}
                      size="small"
                      variant="outlined"
                    />
                  </TableCell>
                  <TableCell sx={{ whiteSpace: "nowrap" }}>
                    {fmtDate(e.effective_from)}
                  </TableCell>
                  <TableCell align="right">
                    <Stack
                      direction="row"
                      spacing={0.5}
                      justifyContent="flex-end"
                    >
                      <IconButton size="small" onClick={() => setEditEntry(e)}>
                        <EditIcon fontSize="small" />
                      </IconButton>
                      <IconButton
                        size="small"
                        onClick={() => handleShowHistory(e.provider, e.model)}
                      >
                        <HistoryIcon fontSize="small" />
                      </IconButton>
                      <IconButton
                        size="small"
                        color="error"
                        onClick={() => handleDelete(e.id)}
                      >
                        <DeleteIcon fontSize="small" />
                      </IconButton>
                    </Stack>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </TableContainer>
      )}

      {/* History panel */}
      {historyFor && (
        <Box sx={{ mt: 2 }}>
          <Typography variant="subtitle2">
            {t("observability.history_title", {
              provider: historyFor.provider,
              model: historyFor.model,
            })}
          </Typography>
          {historyLoading ? (
            <CircularProgress size={20} />
          ) : history.length === 0 ? (
            <Typography variant="body2" color="text.secondary">
              {t("observability.no_history")}
            </Typography>
          ) : (
            <Table size="small" sx={{ mt: 1 }}>
              <TableHead>
                <TableRow>
                  <TableCell>{t("common.status")}</TableCell>
                  <TableCell align="right">
                    {t("observability.col_input_price")}
                  </TableCell>
                  <TableCell align="right">
                    {t("observability.col_output_price")}
                  </TableCell>
                  <TableCell>{t("observability.col_effective")}</TableCell>
                  <TableCell>{t("observability.col_source")}</TableCell>
                  <TableCell>{t("observability.col_notes")}</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {history.map((h) => (
                  <TableRow key={h.id}>
                    <TableCell>
                      <Chip
                        label={
                          h.active ? t("common.active") : t("common.inactive")
                        }
                        size="small"
                        color={h.active ? "success" : "default"}
                      />
                    </TableCell>
                    <TableCell align="right">
                      {Number(h.input_price_per_1k).toFixed(6)}
                    </TableCell>
                    <TableCell align="right">
                      {Number(h.output_price_per_1k).toFixed(6)}
                    </TableCell>
                    <TableCell sx={{ whiteSpace: "nowrap" }}>
                      {fmtDate(h.effective_from)}
                    </TableCell>
                    <TableCell>{h.source ?? "—"}</TableCell>
                    <TableCell>{h.notes ?? "—"}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </Box>
      )}

      {/* Create dialog */}
      <CreateDialog
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        onCreated={load}
      />

      {/* Edit dialog */}
      {editEntry && (
        <EditDialog
          entry={editEntry}
          onClose={() => setEditEntry(null)}
          onUpdated={load}
        />
      )}
    </Box>
  );
}

// ── Create dialog ────────────────────────────────────────────────────────────

function CreateDialog({
  open,
  onClose,
  onCreated,
}: {
  open: boolean;
  onClose: () => void;
  onCreated: () => void;
}) {
  const { t } = useTranslation();
  const [form, setForm] = useState<PricingCreate>({
    provider: "",
    model: "",
    input_price_per_1k: 0,
    output_price_per_1k: 0,
    currency: "USD",
  });
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleSave = async () => {
    setSaving(true);
    setError(null);
    try {
      await observability.createPricing(form);
      onCreated();
      onClose();
    } catch (err: unknown) {
      setError(
        err instanceof Error ? err.message : t("observability.create_failed"),
      );
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog open={open} onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle>{t("observability.add_pricing_title")}</DialogTitle>
      <DialogContent>
        {error && (
          <Alert severity="error" sx={{ mb: 2 }}>
            {error}
          </Alert>
        )}
        <Stack spacing={2} sx={{ mt: 1 }}>
          <NameAutocomplete
            value={form.provider}
            onChange={(v) => setForm({ ...form, provider: v })}
            label={t("observability.col_provider")}
            placeholder={t("observability.provider_names_placeholder")}
            fetcher={observability.providerNames}
          />
          <NameAutocomplete
            value={form.model}
            onChange={(v) => setForm({ ...form, model: v })}
            label={t("observability.col_model")}
            placeholder={t("observability.model_names_placeholder")}
            fetcher={observability.modelNames}
          />
          <TextField
            label={t("observability.input_price_per_1k")}
            type="number"
            value={form.input_price_per_1k}
            onChange={(e) =>
              setForm({ ...form, input_price_per_1k: Number(e.target.value) })
            }
            size="small"
            fullWidth
            inputProps={{ step: 0.000001, min: 0 }}
          />
          <TextField
            label={t("observability.output_price_per_1k")}
            type="number"
            value={form.output_price_per_1k}
            onChange={(e) =>
              setForm({ ...form, output_price_per_1k: Number(e.target.value) })
            }
            size="small"
            fullWidth
            inputProps={{ step: 0.000001, min: 0 }}
          />
          <TextField
            label={t("observability.notes")}
            value={form.notes ?? ""}
            onChange={(e) =>
              setForm({ ...form, notes: e.target.value || undefined })
            }
            size="small"
            fullWidth
            multiline
            rows={2}
          />
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>{t("common.cancel")}</Button>
        <Button
          variant="contained"
          onClick={handleSave}
          disabled={saving || !form.provider || !form.model}
        >
          {saving ? t("observability.saving") : t("common.create")}
        </Button>
      </DialogActions>
    </Dialog>
  );
}

// ── Edit dialog ──────────────────────────────────────────────────────────────

function EditDialog({
  entry,
  onClose,
  onUpdated,
}: {
  entry: PricingEntry;
  onClose: () => void;
  onUpdated: () => void;
}) {
  const { t } = useTranslation();
  const [inputPrice, setInputPrice] = useState(
    Number(entry.input_price_per_1k),
  );
  const [outputPrice, setOutputPrice] = useState(
    Number(entry.output_price_per_1k),
  );
  const [notes, setNotes] = useState(entry.notes ?? "");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleSave = async () => {
    setSaving(true);
    setError(null);
    try {
      await observability.updatePricing(entry.id, {
        input_price_per_1k: inputPrice,
        output_price_per_1k: outputPrice,
        notes: notes || undefined,
      });
      onUpdated();
      onClose();
    } catch (err: unknown) {
      setError(
        err instanceof Error ? err.message : t("observability.update_failed"),
      );
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog open onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle>
        {t("observability.edit_pricing_title", {
          provider: entry.provider,
          model: entry.model,
        })}
      </DialogTitle>
      <DialogContent>
        {error && (
          <Alert severity="error" sx={{ mb: 2 }}>
            {error}
          </Alert>
        )}
        <Stack spacing={2} sx={{ mt: 1 }}>
          <TextField
            label={t("observability.input_price_per_1k")}
            type="number"
            value={inputPrice}
            onChange={(e) => setInputPrice(Number(e.target.value))}
            size="small"
            fullWidth
            inputProps={{ step: 0.000001, min: 0 }}
          />
          <TextField
            label={t("observability.output_price_per_1k")}
            type="number"
            value={outputPrice}
            onChange={(e) => setOutputPrice(Number(e.target.value))}
            size="small"
            fullWidth
            inputProps={{ step: 0.000001, min: 0 }}
          />
          <TextField
            label={t("observability.notes")}
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            size="small"
            fullWidth
            multiline
            rows={2}
          />
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>{t("common.cancel")}</Button>
        <Button variant="contained" onClick={handleSave} disabled={saving}>
          {saving ? t("observability.saving") : t("common.save")}
        </Button>
      </DialogActions>
    </Dialog>
  );
}

// ── Generic name autocomplete ───────────────────────────────────────────────

function NameAutocomplete({
  value,
  onChange,
  label,
  placeholder,
  fetcher,
}: {
  value: string;
  onChange: (v: string) => void;
  label: string;
  placeholder?: string;
  fetcher: (q: string) => Promise<string[]>;
}) {
  const [options, setOptions] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const fetchOptions = useCallback(
    (q: string) => {
      if (timerRef.current) clearTimeout(timerRef.current);
      timerRef.current = setTimeout(async () => {
        setLoading(true);
        try {
          const names = await fetcher(q);
          setOptions(names);
        } catch {
          setOptions([]);
        } finally {
          setLoading(false);
        }
      }, 250);
    },
    [fetcher],
  );

  useEffect(() => {
    fetchOptions("");
    return () => {
      if (timerRef.current) clearTimeout(timerRef.current);
    };
  }, [fetchOptions]);

  return (
    <Autocomplete
      freeSolo
      options={options}
      value={value}
      onInputChange={(_e, v) => {
        onChange(v);
        fetchOptions(v);
      }}
      loading={loading}
      renderInput={(params) => (
        <TextField
          {...params}
          label={label}
          placeholder={placeholder}
          size="small"
          fullWidth
          required
          slotProps={{
            input: {
              ...params.InputProps,
              endAdornment: (
                <>
                  {loading ? (
                    <CircularProgress color="inherit" size={16} />
                  ) : null}
                  {params.InputProps.endAdornment}
                </>
              ),
            },
          }}
        />
      )}
    />
  );
}
