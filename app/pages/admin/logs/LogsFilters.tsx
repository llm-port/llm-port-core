import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import FormControl from "@mui/material/FormControl";
import InputLabel from "@mui/material/InputLabel";
import MenuItem from "@mui/material/MenuItem";
import Select from "@mui/material/Select";
import Stack from "@mui/material/Stack";
import Switch from "@mui/material/Switch";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";

const PRESETS = ["15m", "1h", "6h", "24h", "custom"] as const;
export type TimePreset = (typeof PRESETS)[number];

interface LogsFiltersProps {
  preset: TimePreset;
  customStart: string;
  customEnd: string;
  search: string;
  live: boolean;
  availableLabelKeys: string[];
  selectedLabels: Record<string, string>;
  valuesByLabel: Record<string, string[]>;
  onPresetChange: (value: TimePreset) => void;
  onCustomStartChange: (value: string) => void;
  onCustomEndChange: (value: string) => void;
  onSearchChange: (value: string) => void;
  onLiveChange: (value: boolean) => void;
  onLabelValueChange: (label: string, value: string) => void;
  onApply: () => void;
}

export default function LogsFilters({
  preset,
  customStart,
  customEnd,
  search,
  live,
  availableLabelKeys,
  selectedLabels,
  valuesByLabel,
  onPresetChange,
  onCustomStartChange,
  onCustomEndChange,
  onSearchChange,
  onLiveChange,
  onLabelValueChange,
  onApply,
}: LogsFiltersProps) {
  return (
    <Stack spacing={1.5} sx={{ mb: 2 }}>
      <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap" useFlexGap>
        <FormControl size="small" sx={{ minWidth: 130 }}>
          <InputLabel>Time range</InputLabel>
          <Select
            value={preset}
            label="Time range"
            onChange={(e) => onPresetChange(e.target.value as TimePreset)}
          >
            <MenuItem value="15m">Last 15m</MenuItem>
            <MenuItem value="1h">Last 1h</MenuItem>
            <MenuItem value="6h">Last 6h</MenuItem>
            <MenuItem value="24h">Last 24h</MenuItem>
            <MenuItem value="custom">Custom</MenuItem>
          </Select>
        </FormControl>

        {preset === "custom" && (
          <>
            <TextField
              size="small"
              label="Start"
              type="datetime-local"
              value={customStart}
              onChange={(e) => onCustomStartChange(e.target.value)}
              InputLabelProps={{ shrink: true }}
            />
            <TextField
              size="small"
              label="End"
              type="datetime-local"
              value={customEnd}
              onChange={(e) => onCustomEndChange(e.target.value)}
              InputLabelProps={{ shrink: true }}
            />
          </>
        )}

        <TextField
          size="small"
          label="Search text"
          value={search}
          onChange={(e) => onSearchChange(e.target.value)}
          sx={{ minWidth: 200 }}
        />

        <Stack direction="row" spacing={0.5} alignItems="center">
          <Switch checked={live} onChange={(e) => onLiveChange(e.target.checked)} />
          <Typography variant="body2">Live</Typography>
        </Stack>

        <Button variant="outlined" size="small" onClick={onApply}>
          Apply
        </Button>
      </Stack>

      <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap>
        {availableLabelKeys.map((labelKey) => (
          <FormControl key={labelKey} size="small" sx={{ minWidth: 180 }}>
            <InputLabel>{labelKey}</InputLabel>
            <Select
              value={selectedLabels[labelKey] ?? ""}
              label={labelKey}
              onChange={(e) => onLabelValueChange(labelKey, e.target.value)}
            >
              <MenuItem value="">All</MenuItem>
              {(valuesByLabel[labelKey] ?? []).map((value) => (
                <MenuItem key={value} value={value}>
                  {value}
                </MenuItem>
              ))}
            </Select>
          </FormControl>
        ))}
      </Stack>

      <Box>
        <Typography variant="caption" color="text.secondary">
          Query is generated from selected labels and search text.
        </Typography>
      </Box>
    </Stack>
  );
}
