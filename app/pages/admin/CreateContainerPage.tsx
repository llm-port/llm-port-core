/**
 * Admin → Create Container.
 * Guided form to create a new Docker container with classification,
 * port bindings, environment variables, and volume binds.
 */
import { useState, useEffect } from "react";
import { useNavigate } from "react-router";
import { useTranslation } from "react-i18next";
import {
  containers,
  networks,
  images,
  type ContainerClass,
  type ContainerPolicy,
  type PortBinding,
} from "~/api/admin";
import { ClassChip, PolicyChip } from "~/components/Chips";

import Alert from "@mui/material/Alert";
import Autocomplete from "@mui/material/Autocomplete";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Chip from "@mui/material/Chip";
import CircularProgress from "@mui/material/CircularProgress";
import Divider from "@mui/material/Divider";
import FormControlLabel from "@mui/material/FormControlLabel";
import Grid from "@mui/material/Grid";
import IconButton from "@mui/material/IconButton";
import MenuItem from "@mui/material/MenuItem";
import Paper from "@mui/material/Paper";
import Stack from "@mui/material/Stack";
import Switch from "@mui/material/Switch";
import TextField from "@mui/material/TextField";
import ToggleButton from "@mui/material/ToggleButton";
import ToggleButtonGroup from "@mui/material/ToggleButtonGroup";
import Tooltip from "@mui/material/Tooltip";
import Typography from "@mui/material/Typography";

import AddIcon from "@mui/icons-material/Add";
import ArrowBackIcon from "@mui/icons-material/ArrowBack";
import DeleteOutlineIcon from "@mui/icons-material/DeleteOutline";
import RocketLaunchIcon from "@mui/icons-material/RocketLaunch";

interface EnvRow {
  key: string;
  value: string;
}

interface VolumeRow {
  host: string;
  container: string;
}

const CLASS_OPTIONS: ContainerClass[] = [
  "SYSTEM_CORE",
  "SYSTEM_AUX",
  "TENANT_APP",
  "UNTRUSTED",
];
const POLICY_OPTIONS: ContainerPolicy[] = ["free", "restricted", "locked"];

function SectionHeader({ children }: { children: React.ReactNode }) {
  return (
    <Typography
      variant="overline"
      color="text.secondary"
      letterSpacing={1.2}
      sx={{ mb: 1.5, display: "block" }}
    >
      {children}
    </Typography>
  );
}

export default function CreateContainerPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();

  // ── Form state ────────────────────────────────────────────────────────────
  const [image, setImage] = useState("");
  const [name, setName] = useState("");
  const [containerClass, setContainerClass] = useState<ContainerClass>("UNTRUSTED");
  const [policy, setPolicy] = useState<ContainerPolicy>("free");
  const [ownerScope, setOwnerScope] = useState("platform");
  const [autoStart, setAutoStart] = useState(false);
  const [network, setNetwork] = useState("");
  const [cmdRaw, setCmdRaw] = useState("");
  const [ports, setPorts] = useState<PortBinding[]>([]);
  const [envRows, setEnvRows] = useState<EnvRow[]>([]);
  const [volumeRows, setVolumeRows] = useState<VolumeRow[]>([]);

  // ── Submit state ──────────────────────────────────────────────────────────
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);

  // ── Network options ───────────────────────────────────────────────────────
  const [networkOptions, setNetworkOptions] = useState<{ id: string; name: string }[]>([]);

  // ── Image options (local Docker images) ─────────────────────────────────
  const [imageOptions, setImageOptions] = useState<string[]>([]);

  useEffect(() => {
    networks
      .list()
      .then((nets) => setNetworkOptions(nets.map((n) => ({ id: n.id, name: n.name }))))
      .catch(() => {});
    images
      .list()
      .then((imgs) => {
        const tags = imgs.flatMap((i) => i.repo_tags).filter((t) => t && t !== "<none>:<none>");
        setImageOptions([...new Set(tags)].sort());
      })
      .catch(() => {});
  }, []);

  // ── Helpers ───────────────────────────────────────────────────────────────
  function addPort() {
    setPorts((p) => [...p, { host_port: "", container_port: "" }]);
  }
  function updatePort(index: number, field: keyof PortBinding, value: string) {
    setPorts((p) => p.map((r, i) => (i === index ? { ...r, [field]: value } : r)));
  }
  function removePort(index: number) {
    setPorts((p) => p.filter((_, i) => i !== index));
  }

  function addEnv() {
    setEnvRows((r) => [...r, { key: "", value: "" }]);
  }
  function updateEnv(index: number, field: keyof EnvRow, value: string) {
    setEnvRows((r) => r.map((row, i) => (i === index ? { ...row, [field]: value } : row)));
  }
  function removeEnv(index: number) {
    setEnvRows((r) => r.filter((_, i) => i !== index));
  }

  function addVolume() {
    setVolumeRows((r) => [...r, { host: "", container: "" }]);
  }
  function updateVolume(index: number, field: keyof VolumeRow, value: string) {
    setVolumeRows((r) => r.map((row, i) => (i === index ? { ...row, [field]: value } : row)));
  }
  function removeVolume(index: number) {
    setVolumeRows((r) => r.filter((_, i) => i !== index));
  }

  // ── Submit ────────────────────────────────────────────────────────────────
  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setSubmitError(null);
    setSubmitting(true);
    try {
      const cmd = cmdRaw.trim()
        ? cmdRaw
            .trim()
            .match(/(?:[^\s"']+|"[^"]*"|'[^']*')+/g)
            ?.map((s) => s.replace(/^["']|["']$/g, "")) ?? []
        : undefined;

      const validPorts = ports.filter((p) => p.host_port && p.container_port);
      const validEnv = envRows
        .filter((r) => r.key.trim())
        .map((r) => `${r.key.trim()}=${r.value}`);
      const validVolumes = volumeRows
        .filter((r) => r.host.trim() && r.container.trim())
        .map((r) => `${r.host.trim()}:${r.container.trim()}`);

      const result = await containers.create({
        image: image.trim(),
        name: name.trim() || undefined,
        container_class: containerClass,
        owner_scope: ownerScope.trim() || "platform",
        policy,
        auto_start: autoStart,
        ports: validPorts.length ? validPorts : undefined,
        env: validEnv.length ? validEnv : undefined,
        cmd,
        network: network || undefined,
        volumes: validVolumes.length ? validVolumes : undefined,
      });

      // Navigate to the new container's detail page
      navigate(`/admin/containers/${result.id}`);
    } catch (err: unknown) {
      setSubmitError(err instanceof Error ? err.message : t("common.create_failed"));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Box sx={{ display: "flex", flexDirection: "column", height: "100%", overflow: "hidden" }}>
      {/* Header */}
      <Box sx={{ flexShrink: 0, mb: 2 }}>
        <Stack direction="row" alignItems="center" spacing={1.5}>
          <Tooltip title={t("create_container.back_to_containers")}>
            <IconButton size="small" onClick={() => navigate("/admin/containers")}>
              <ArrowBackIcon />
            </IconButton>
          </Tooltip>
          <Typography variant="h5">{t("create_container.title")}</Typography>
        </Stack>
      </Box>

      {/* Scrollable form */}
      <Box sx={{ flexGrow: 1, overflow: "auto" }}>
        <Box component="form" onSubmit={handleSubmit} sx={{ pb: 4 }}>
          <Grid container spacing={2.5} alignItems="flex-start">

            {/* ── LEFT COLUMN — main config ───────────────────────── */}
            <Grid size={{ xs: 12, lg: 7 }}>
              <Stack spacing={2.5}>

                {/* Image & Identity */}
                <Paper variant="outlined" sx={{ p: 3 }}>
                  <SectionHeader>{t("create_container.sections.image_identity")}</SectionHeader>
                  <Stack spacing={2}>
                    <Autocomplete
                      freeSolo
                      options={imageOptions}
                      inputValue={image}
                      onInputChange={(_, v) => setImage(v)}
                      renderInput={(params) => (
                        <TextField
                          {...params}
                          label={t("create_container.image")}
                          required
                          placeholder={t("create_container.image_placeholder")}
                          helperText={t("create_container.image_help")}
                          autoFocus
                        />
                      )}
                    />
                    <TextField
                      label={t("create_container.container_name")}
                      fullWidth
                      placeholder={t("create_container.container_name_placeholder")}
                      value={name}
                      onChange={(e) => setName(e.target.value)}
                    />
                    <TextField
                      label={t("create_container.command_override")}
                      fullWidth
                      placeholder='/bin/sh -c "echo hello"'
                      value={cmdRaw}
                      onChange={(e) => setCmdRaw(e.target.value)}
                      helperText={t("create_container.command_override_help")}
                      inputProps={{ style: { fontFamily: "monospace", fontSize: "0.85rem" } }}
                    />
                  </Stack>
                </Paper>

                {/* Port Bindings */}
                <Paper variant="outlined" sx={{ p: 3 }}>
                  <Stack direction="row" alignItems="center" justifyContent="space-between" mb={1.5}>
                    <SectionHeader>{t("create_container.sections.port_bindings")}</SectionHeader>
                    <Button size="small" startIcon={<AddIcon />} onClick={addPort}>
                      {t("common.add")}
                    </Button>
                  </Stack>
                  {ports.length === 0 ? (
                    <Typography variant="body2" color="text.secondary">
                      {t("create_container.no_port_bindings")}
                    </Typography>
                  ) : (
                    <Stack spacing={1}>
                      {ports.map((p, i) => (
                        <Stack key={i} direction="row" spacing={1} alignItems="center">
                          <TextField
                            label={t("create_container.host_port")}
                            size="small"
                            placeholder="8080"
                            value={p.host_port}
                            onChange={(e) => updatePort(i, "host_port", e.target.value)}
                            sx={{ width: 110 }}
                          />
                          <Typography color="text.secondary">→</Typography>
                          <TextField
                            label={t("create_container.container_port")}
                            size="small"
                            placeholder="80/tcp"
                            value={p.container_port}
                            onChange={(e) => updatePort(i, "container_port", e.target.value)}
                            sx={{ width: 130 }}
                          />
                          <Chip
                            label={
                              p.host_port && p.container_port
                                ? `${p.host_port}:${p.container_port}`
                                : t("create_container.incomplete")
                            }
                            size="small"
                            variant="outlined"
                            color={p.host_port && p.container_port ? "primary" : "default"}
                            sx={{ fontFamily: "monospace", flexShrink: 0 }}
                          />
                          <Tooltip title={t("common.remove")}>
                            <IconButton size="small" color="error" onClick={() => removePort(i)}>
                              <DeleteOutlineIcon fontSize="small" />
                            </IconButton>
                          </Tooltip>
                        </Stack>
                      ))}
                    </Stack>
                  )}
                </Paper>

                {/* Environment Variables */}
                <Paper variant="outlined" sx={{ p: 3 }}>
                  <Stack direction="row" alignItems="center" justifyContent="space-between" mb={1.5}>
                    <SectionHeader>{t("create_container.sections.environment_variables")}</SectionHeader>
                    <Button size="small" startIcon={<AddIcon />} onClick={addEnv}>
                      {t("common.add")}
                    </Button>
                  </Stack>
                  {envRows.length === 0 ? (
                    <Typography variant="body2" color="text.secondary">
                      {t("create_container.no_environment_variables")}
                    </Typography>
                  ) : (
                    <Stack spacing={1}>
                      {envRows.map((row, i) => (
                        <Stack key={i} direction="row" spacing={1} alignItems="center">
                          <TextField
                            label={t("common.key")}
                            size="small"
                            placeholder="POSTGRES_HOST"
                            value={row.key}
                            onChange={(e) => updateEnv(i, "key", e.target.value)}
                            sx={{ flex: 1 }}
                            inputProps={{ style: { fontFamily: "monospace" } }}
                          />
                          <Typography color="text.secondary">=</Typography>
                          <TextField
                            label={t("common.value")}
                            size="small"
                            placeholder="localhost"
                            value={row.value}
                            onChange={(e) => updateEnv(i, "value", e.target.value)}
                            sx={{ flex: 2 }}
                            inputProps={{ style: { fontFamily: "monospace" } }}
                          />
                          <Tooltip title={t("common.remove")}>
                            <IconButton size="small" color="error" onClick={() => removeEnv(i)}>
                              <DeleteOutlineIcon fontSize="small" />
                            </IconButton>
                          </Tooltip>
                        </Stack>
                      ))}
                    </Stack>
                  )}
                </Paper>

                {/* Volume Mounts */}
                <Paper variant="outlined" sx={{ p: 3 }}>
                  <Stack direction="row" alignItems="center" justifyContent="space-between" mb={1.5}>
                    <SectionHeader>{t("create_container.sections.volume_mounts")}</SectionHeader>
                    <Button size="small" startIcon={<AddIcon />} onClick={addVolume}>
                      {t("common.add")}
                    </Button>
                  </Stack>
                  {volumeRows.length === 0 ? (
                    <Typography variant="body2" color="text.secondary">
                      {t("create_container.no_volume_mounts")}
                    </Typography>
                  ) : (
                    <Stack spacing={1}>
                      {volumeRows.map((row, i) => (
                        <Stack key={i} direction="row" spacing={1} alignItems="center">
                          <TextField
                            label={t("create_container.host_path")}
                            size="small"
                            placeholder="/data/app"
                            value={row.host}
                            onChange={(e) => updateVolume(i, "host", e.target.value)}
                            sx={{ flex: 1 }}
                            inputProps={{ style: { fontFamily: "monospace" } }}
                          />
                          <Typography color="text.secondary">:</Typography>
                          <TextField
                            label={t("create_container.container_path")}
                            size="small"
                            placeholder="/var/app"
                            value={row.container}
                            onChange={(e) => updateVolume(i, "container", e.target.value)}
                            sx={{ flex: 1 }}
                            inputProps={{ style: { fontFamily: "monospace" } }}
                          />
                          <Tooltip title={t("common.remove")}>
                            <IconButton size="small" color="error" onClick={() => removeVolume(i)}>
                              <DeleteOutlineIcon fontSize="small" />
                            </IconButton>
                          </Tooltip>
                        </Stack>
                      ))}
                    </Stack>
                  )}
                </Paper>

              </Stack>
            </Grid>

            {/* ── RIGHT COLUMN — classification + runtime + actions ── */}
            <Grid size={{ xs: 12, lg: 5 }}>
              <Stack spacing={2.5} sx={{ position: { lg: "sticky" }, top: { lg: 0 } }}>

                {/* Classification & Policy */}
                <Paper variant="outlined" sx={{ p: 3 }}>
                  <SectionHeader>{t("create_container.sections.classification_policy")}</SectionHeader>
                  <Stack spacing={2.5}>
                    <Box>
                      <Typography variant="body2" color="text.secondary" gutterBottom>
                        {t("containers.class")}
                      </Typography>
                      <ToggleButtonGroup
                        value={containerClass}
                        exclusive
                        onChange={(_, v) => { if (v) setContainerClass(v as ContainerClass); }}
                        size="small"
                        sx={{ flexWrap: "wrap", gap: 0.5 }}
                      >
                        {CLASS_OPTIONS.map((cls) => (
                          <ToggleButton
                            key={cls}
                            value={cls}
                            sx={{ gap: 0.75, px: 1.5, textTransform: "none" }}
                          >
                            <ClassChip value={cls} />
                          </ToggleButton>
                        ))}
                      </ToggleButtonGroup>
                    </Box>

                    <Box>
                      <Typography variant="body2" color="text.secondary" gutterBottom>
                        {t("create_container.policy")}
                      </Typography>
                      <ToggleButtonGroup
                        value={policy}
                        exclusive
                        onChange={(_, v) => { if (v) setPolicy(v as ContainerPolicy); }}
                        size="small"
                      >
                        {POLICY_OPTIONS.map((p) => (
                          <ToggleButton
                            key={p}
                            value={p}
                            sx={{ gap: 0.75, px: 1.5, textTransform: "none" }}
                          >
                            <PolicyChip value={p} />
                          </ToggleButton>
                        ))}
                      </ToggleButtonGroup>
                    </Box>

                    <TextField
                      label={t("containers.scope")}
                      fullWidth
                      value={ownerScope}
                      onChange={(e) => setOwnerScope(e.target.value)}
                      helperText={t("create_container.owner_scope_help")}
                    />
                  </Stack>
                </Paper>

                {/* Runtime Options */}
                <Paper variant="outlined" sx={{ p: 3 }}>
                  <SectionHeader>{t("create_container.sections.runtime_options")}</SectionHeader>
                  <Stack spacing={2}>
                    <FormControlLabel
                      control={
                        <Switch
                          checked={autoStart}
                          onChange={(e) => setAutoStart(e.target.checked)}
                        />
                      }
                      label={t("create_container.start_immediately")}
                    />
                    <TextField
                      select
                      label={t("networks.title")}
                      fullWidth
                      value={network}
                      onChange={(e) => setNetwork(e.target.value)}
                      helperText={t("create_container.network_help")}
                    >
                      <MenuItem value="">
                        <em>{t("create_container.default_bridge")}</em>
                      </MenuItem>
                      {networkOptions.map((n) => (
                        <MenuItem key={n.id} value={n.name}>
                          {n.name}
                        </MenuItem>
                      ))}
                    </TextField>
                  </Stack>
                </Paper>

                {/* Submit */}
                <Paper variant="outlined" sx={{ p: 3 }}>
                  {submitError && (
                    <Alert severity="error" sx={{ mb: 2 }}>
                      {submitError}
                    </Alert>
                  )}
                  <Stack spacing={1}>
                    <Button
                      type="submit"
                      variant="contained"
                      fullWidth
                      size="large"
                      disabled={submitting || !image.trim()}
                      startIcon={submitting ? <CircularProgress size={18} /> : <RocketLaunchIcon />}
                    >
                      {submitting
                        ? t("common.creating")
                        : autoStart
                          ? t("create_container.create_start")
                          : t("create_container.title")}
                    </Button>
                    <Divider sx={{ my: 0.5 }} />
                    <Button
                      variant="text"
                      fullWidth
                      onClick={() => navigate("/admin/containers")}
                      disabled={submitting}
                    >
                      {t("common.cancel")}
                    </Button>
                  </Stack>
                </Paper>

              </Stack>
            </Grid>

          </Grid>
        </Box>
      </Box>
    </Box>
  );}
