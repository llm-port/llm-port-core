/**
 * Shared panel for editing container resource settings (GPU, IPC, SHM, etc.).
 * Used by both RuntimeDetailPage (edit config) and ProviderWizardDialog.
 */
import FormControl from "@mui/material/FormControl";
import InputLabel from "@mui/material/InputLabel";
import MenuItem from "@mui/material/MenuItem";
import Select from "@mui/material/Select";
import Stack from "@mui/material/Stack";
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

export function ContainerResourcesPanel({
  values,
  onChange,
}: ContainerResourcesPanelProps) {
  return (
    <Stack spacing={2}>
      <Stack direction="row" spacing={2}>
        <FormControl size="small" sx={{ minWidth: 140 }}>
          <InputLabel>GPU</InputLabel>
          <Select
            label="GPU"
            value={values.gpuRequest}
            onChange={(e) => onChange("gpuRequest", e.target.value)}
          >
            <MenuItem value="">
              <em>Default</em>
            </MenuItem>
            <MenuItem value="all">all</MenuItem>
            <MenuItem value="0">0</MenuItem>
            <MenuItem value="0,1">0,1</MenuItem>
            <MenuItem value="0,1,2,3">0,1,2,3</MenuItem>
          </Select>
        </FormControl>
        <FormControl size="small" sx={{ minWidth: 140 }}>
          <InputLabel>IPC Mode</InputLabel>
          <Select
            label="IPC Mode"
            value={values.ipcMode}
            onChange={(e) => onChange("ipcMode", e.target.value)}
          >
            <MenuItem value="">
              <em>Default</em>
            </MenuItem>
            <MenuItem value="host">host</MenuItem>
            <MenuItem value="private">private</MenuItem>
            <MenuItem value="shareable">shareable</MenuItem>
          </Select>
        </FormControl>
        <TextField
          label="SHM Size"
          size="small"
          placeholder="e.g. 1g"
          value={values.shmSize}
          onChange={(e) => onChange("shmSize", e.target.value)}
          sx={{ width: 120 }}
        />
      </Stack>
      <Stack direction="row" spacing={2}>
        <TextField
          label="Memory Limit"
          size="small"
          placeholder="e.g. 32g"
          value={values.memoryLimit}
          onChange={(e) => onChange("memoryLimit", e.target.value)}
          sx={{ width: 140 }}
        />
        <TextField
          label="CPU Limit"
          size="small"
          placeholder="e.g. 8"
          value={values.cpuLimit}
          onChange={(e) => onChange("cpuLimit", e.target.value)}
          sx={{ width: 120 }}
        />
        <TextField
          label="Container Port"
          size="small"
          placeholder="8000"
          value={values.containerPort}
          onChange={(e) => onChange("containerPort", e.target.value)}
          sx={{ width: 140 }}
        />
      </Stack>
    </Stack>
  );
}
