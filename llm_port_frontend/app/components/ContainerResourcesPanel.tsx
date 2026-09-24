/**
 * What a legacy engine container is given: which GPUs, how much shared
 * memory, limits, and the port inside it.
 *
 * Every field can stay empty: the server then picks what vLLM needs (all
 * GPUs, host IPC). Used by RuntimeDetailPage and ProviderWizardDialog.
 */
import { useTranslation } from "react-i18next";

import Grid from "@mui/material/Grid";
import MenuItem from "@mui/material/MenuItem";
import TextField from "@mui/material/TextField";

export interface ContainerResourceValues {
  gpuRequest: string;
  ipcMode: string;
  shmSize: string;
  memoryLimit: string;
  cpuLimit: string;
  containerPort: string;
}

export interface ContainerResourcesPanelProps {
  values: ContainerResourceValues;
  onChange: (field: keyof ContainerResourceValues, value: string) => void;
}

const GPU_CHOICES = ["", "all", "0", "0,1", "0,1,2,3"];
const IPC_CHOICES = ["", "host", "private", "shareable"];

export function ContainerResourcesPanel({ values, onChange }: ContainerResourcesPanelProps) {
  const { t } = useTranslation();
  const auto = t("container_resources.auto");
  return (
    <Grid container spacing={2}>
      <Grid size={{ xs: 12, sm: 6, md: 4 }}>
        <TextField
          select
          fullWidth
          size="small"
          label={t("container_resources.gpus")}
          value={values.gpuRequest}
          onChange={(e) => onChange("gpuRequest", e.target.value)}
          helperText={t("container_resources.gpus_help")}
        >
          {GPU_CHOICES.map((v) => (
            <MenuItem key={v || "auto"} value={v}>
              {v === "" ? <em>{auto}</em> : v === "all" ? t("container_resources.gpus_all") : t("container_resources.gpus_ids", { ids: v })}
            </MenuItem>
          ))}
        </TextField>
      </Grid>
      <Grid size={{ xs: 12, sm: 6, md: 4 }}>
        <TextField
          select
          fullWidth
          size="small"
          label={t("container_resources.ipc")}
          value={values.ipcMode}
          onChange={(e) => onChange("ipcMode", e.target.value)}
          helperText={t("container_resources.ipc_help")}
        >
          {IPC_CHOICES.map((v) => (
            <MenuItem key={v || "auto"} value={v}>
              {v === "" ? <em>{auto}</em> : v}
            </MenuItem>
          ))}
        </TextField>
      </Grid>
      <Grid size={{ xs: 12, sm: 6, md: 4 }}>
        <TextField
          fullWidth
          size="small"
          label={t("container_resources.shm")}
          placeholder="16g"
          value={values.shmSize}
          onChange={(e) => onChange("shmSize", e.target.value)}
          helperText={t("container_resources.shm_help")}
        />
      </Grid>
      <Grid size={{ xs: 12, sm: 6, md: 4 }}>
        <TextField
          fullWidth
          size="small"
          label={t("container_resources.memory")}
          placeholder="32g"
          value={values.memoryLimit}
          onChange={(e) => onChange("memoryLimit", e.target.value)}
          helperText={t("container_resources.memory_help")}
        />
      </Grid>
      <Grid size={{ xs: 12, sm: 6, md: 4 }}>
        <TextField
          fullWidth
          size="small"
          label={t("container_resources.cpus")}
          placeholder="8"
          value={values.cpuLimit}
          onChange={(e) => onChange("cpuLimit", e.target.value)}
          helperText={t("container_resources.cpus_help")}
        />
      </Grid>
      <Grid size={{ xs: 12, sm: 6, md: 4 }}>
        <TextField
          fullWidth
          size="small"
          label={t("container_resources.port")}
          placeholder="8000"
          value={values.containerPort}
          onChange={(e) => onChange("containerPort", e.target.value)}
          helperText={t("container_resources.port_help")}
        />
      </Grid>
    </Grid>
  );
}
