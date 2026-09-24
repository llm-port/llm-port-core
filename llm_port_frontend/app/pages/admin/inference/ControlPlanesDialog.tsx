/**
 * Control-plane management (Phase 6, gap G-1).
 *
 * Control planes were creatable only through the API, so first-time setup
 * needed an API client before any environment could exist. They are a
 * small, rarely-touched entity -- usually exactly one -- so they get a
 * dialog on the Environments page rather than a page and a nav entry of
 * their own.
 */
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { inferenceApi, type ControlPlane } from "~/api/inference";

import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Chip from "@mui/material/Chip";
import Dialog from "@mui/material/Dialog";
import DialogActions from "@mui/material/DialogActions";
import DialogContent from "@mui/material/DialogContent";
import DialogTitle from "@mui/material/DialogTitle";
import Divider from "@mui/material/Divider";
import MenuItem from "@mui/material/MenuItem";
import Stack from "@mui/material/Stack";
import Switch from "@mui/material/Switch";
import Table from "@mui/material/Table";
import TableBody from "@mui/material/TableBody";
import TableCell from "@mui/material/TableCell";
import TableHead from "@mui/material/TableHead";
import TableRow from "@mui/material/TableRow";
import TextField from "@mui/material/TextField";
import Tooltip from "@mui/material/Tooltip";
import Typography from "@mui/material/Typography";

import DeleteOutlineIcon from "@mui/icons-material/DeleteOutline";

export interface ControlPlanesDialogProps {
  open: boolean;
  onClose: () => void;
  /** Called after any change, so the caller can reload its own list. */
  onChanged: () => void;
}

export function ControlPlanesDialog({
  open,
  onClose,
  onChanged,
}: ControlPlanesDialogProps) {
  const { t } = useTranslation();
  const planeStatus = (status: string) =>
    ["pending", "connected", "disconnected", "degraded", "failed", "disabled"].includes(status)
      ? t(`inference.planes.status_${status}`)
      : status;
  const [planes, setPlanes] = useState<ControlPlane[]>([]);
  const [drivers, setDrivers] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [form, setForm] = useState({ name: "", driver: "", description: "" });

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    void (async () => {
      try {
        const [rows, keys] = await Promise.all([
          inferenceApi.listControlPlanes(),
          // A server with no driver registered is a real state; fall back to
          // an empty list rather than pretending "ray" is available.
          inferenceApi.listDrivers().catch(() => [] as string[]),
        ]);
        if (cancelled) return;
        setPlanes(rows);
        setDrivers(keys);
        setForm((prev) => ({ ...prev, driver: prev.driver || (keys[0] ?? "") }));
      } catch (err: unknown) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : String(err));
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [open]);

  async function run(action: () => Promise<unknown>) {
    setBusy(true);
    setError(null);
    try {
      await action();
      setPlanes(await inferenceApi.listControlPlanes());
      onChanged();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  const canCreate = Boolean(form.name.trim() && form.driver);

  return (
    <Dialog open={open} onClose={busy ? undefined : onClose} maxWidth="md" fullWidth>
      <DialogTitle>{t("inference.planes.title")}</DialogTitle>
      <DialogContent>
        <Stack spacing={2} sx={{ mt: 1 }}>
          {error && <Alert severity="error">{error}</Alert>}

          {planes.length === 0 ? (
            <Typography variant="body2" color="text.secondary">
              {t("inference.planes.empty")}
            </Typography>
          ) : (
            <Table size="small">
              <TableHead>
                <TableRow>
                  <TableCell>{t("common.name")}</TableCell>
                  <TableCell>{t("inference.planes.driver")}</TableCell>
                  <TableCell>{t("common.status")}</TableCell>
                  <TableCell align="center">{t("common.enabled")}</TableCell>
                  <TableCell align="right">{t("common.actions")}</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {planes.map((plane) => (
                  <TableRow key={plane.id}>
                    <TableCell>{plane.name}</TableCell>
                    <TableCell>
                      <Chip
                        size="small"
                        label={plane.driver}
                        color={
                          drivers.includes(plane.driver) ? "default" : "warning"
                        }
                      />
                    </TableCell>
                    <TableCell>{planeStatus(plane.status)}</TableCell>
                    <TableCell align="center">
                      <Switch
                        size="small"
                        checked={plane.enabled}
                        disabled={busy}
                        slotProps={{
                          input: { "aria-label": t("inference.planes.enable", { name: plane.name }) },
                        }}
                        onChange={(_, checked) =>
                          void run(() =>
                            inferenceApi.updateControlPlane(plane.id, {
                              enabled: checked,
                            }),
                          )
                        }
                      />
                    </TableCell>
                    <TableCell align="right">
                      <Tooltip title={t("common.delete")}>
                        <span>
                          <Button
                            size="small"
                            color="error"
                            aria-label={t("inference.planes.delete", { name: plane.name })}
                            disabled={busy}
                            onClick={() =>
                              void run(() =>
                                inferenceApi.deleteControlPlane(plane.id),
                              )
                            }
                          >
                            <DeleteOutlineIcon fontSize="small" />
                          </Button>
                        </span>
                      </Tooltip>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}

          <Divider />

          <Typography variant="subtitle2">{t("inference.planes.new")}</Typography>
          {drivers.length === 0 && (
            <Alert severity="warning">
              {t("inference.planes.no_driver")}
            </Alert>
          )}
          <Stack direction={{ xs: "column", sm: "row" }} spacing={2}>
            <TextField
              label={t("common.name")}
              size="small"
              value={form.name}
              fullWidth
              onChange={(e) => setForm({ ...form, name: e.target.value })}
            />
            <TextField
              select
              label={t("inference.planes.driver")}
              size="small"
              value={form.driver}
              sx={{ minWidth: 160 }}
              disabled={drivers.length === 0}
              onChange={(e) => setForm({ ...form, driver: e.target.value })}
            >
              {drivers.map((driver) => (
                <MenuItem key={driver} value={driver}>
                  {driver}
                </MenuItem>
              ))}
            </TextField>
          </Stack>
          <TextField
            label={t("inference.planes.description")}
            size="small"
            value={form.description}
            fullWidth
            onChange={(e) => setForm({ ...form, description: e.target.value })}
          />
          <Box>
            <Button
              variant="contained"
              size="small"
              disabled={!canCreate || busy}
              onClick={() =>
                void run(async () => {
                  await inferenceApi.createControlPlane({
                    name: form.name.trim(),
                    driver: form.driver,
                    description: form.description.trim() || null,
                  });
                  setForm({ name: "", driver: form.driver, description: "" });
                })
              }
            >
              {t("common.create")}
            </Button>
          </Box>
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} disabled={busy}>
          {t("common.close")}
        </Button>
      </DialogActions>
    </Dialog>
  );
}
