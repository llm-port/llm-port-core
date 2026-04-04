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
import { useTranslation } from "react-i18next";

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
  const { t } = useTranslation();

  return (
    <Stack spacing={1} sx={{ mb: 1 }}>
      <Stack
        direction="row"
        spacing={1}
        alignItems="center"
        flexWrap="wrap"
        useFlexGap
      >
        <FormControl size="small" sx={{ minWidth: 130 }}>
          <InputLabel>{t("logs.time_range")}</InputLabel>
          <Select
            value={preset}
            label={t("logs.time_range")}
            onChange={(e) => onPresetChange(e.target.value as TimePreset)}
          >
            <MenuItem value="15m">{t("logs.preset_15m")}</MenuItem>
            <MenuItem value="1h">{t("logs.preset_1h")}</MenuItem>
            <MenuItem value="6h">{t("logs.preset_6h")}</MenuItem>
            <MenuItem value="24h">{t("logs.preset_24h")}</MenuItem>
            <MenuItem value="custom">{t("logs.preset_custom")}</MenuItem>
          </Select>
        </FormControl>

        {preset === "custom" && (
          <>
            <TextField
              size="small"
              label={t("logs.start")}
              type="datetime-local"
              value={customStart}
              onChange={(e) => onCustomStartChange(e.target.value)}
              InputLabelProps={{ shrink: true }}
            />
            <TextField
              size="small"
              label={t("logs.end")}
              type="datetime-local"
              value={customEnd}
              onChange={(e) => onCustomEndChange(e.target.value)}
              InputLabelProps={{ shrink: true }}
            />
          </>
        )}

        <TextField
          size="small"
          label={t("logs.search")}
          value={search}
          onChange={(e) => onSearchChange(e.target.value)}
          sx={{ minWidth: 200 }}
        />

        {availableLabelKeys.map((labelKey) => (
          <FormControl key={labelKey} size="small" sx={{ minWidth: 160 }}>
            <InputLabel>{labelKey}</InputLabel>
            <Select
              value={selectedLabels[labelKey] ?? ""}
              label={labelKey}
              onChange={(e) => onLabelValueChange(labelKey, e.target.value)}
            >
              <MenuItem value="">
                {t("table.all", { defaultValue: "All" })}
              </MenuItem>
              {(valuesByLabel[labelKey] ?? []).map((value) => (
                <MenuItem key={value} value={value}>
                  {value}
                </MenuItem>
              ))}
            </Select>
          </FormControl>
        ))}

        <Stack direction="row" spacing={0.5} alignItems="center">
          <Switch
            checked={live}
            onChange={(e) => onLiveChange(e.target.checked)}
          />
          <Typography variant="body2">{t("logs.live")}</Typography>
        </Stack>

        <Button variant="outlined" size="small" onClick={onApply}>
          {t("logs.apply")}
        </Button>
      </Stack>
    </Stack>
  );
}
