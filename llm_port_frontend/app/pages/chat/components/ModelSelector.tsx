/**
 * ModelSelector — dropdown to pick active model alias.
 */
import { useEffect } from "react";
import Chip from "@mui/material/Chip";
import FormControl from "@mui/material/FormControl";
import InputLabel from "@mui/material/InputLabel";
import MenuItem from "@mui/material/MenuItem";
import Select from "@mui/material/Select";
import Tooltip from "@mui/material/Tooltip";
import type { SelectChangeEvent } from "@mui/material/Select";
import WarningAmberIcon from "@mui/icons-material/WarningAmber";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router";

import type { ModelAlias } from "~/api/chatTypes";
import { chatApi } from "~/api/chatClient";
import { useAsyncData } from "~/lib/useAsyncData";

interface Props {
  value: string;
  onChange: (alias: string) => void;
  size?: "small" | "medium";
}

export default function ModelSelector({
  value,
  onChange,
  size = "small",
}: Props) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const {
    data: models,
    loading,
    refresh: refreshModels,
  } = useAsyncData<ModelAlias[]>(() => chatApi.listModels(), [], {
    initialValue: [],
  });

  // Re-fetch models when window regains focus (e.g. after editing in admin)
  useEffect(() => {
    const onFocus = () => {
      void refreshModels();
    };
    window.addEventListener("focus", onFocus);
    return () => window.removeEventListener("focus", onFocus);
  }, [refreshModels]);

  // Auto-select first model when list loads and no selection
  useEffect(() => {
    if (!value && models.length > 0) {
      onChange(models[0].alias);
    }
  }, [models, value, onChange]);

  const handleChange = (e: SelectChangeEvent) => {
    onChange(e.target.value);
  };

  // No models available
  if (!loading && models.length === 0) {
    return (
      <Tooltip
        title={t("chat.no_models_hint", {
          defaultValue:
            "No models are available. Configure a provider and model alias in the admin panel.",
        })}
        arrow
      >
        <Chip
          icon={<WarningAmberIcon sx={{ fontSize: 14 }} />}
          label={t("chat.no_models", { defaultValue: "No models" })}
          size="small"
          color="warning"
          variant="outlined"
          onClick={() => navigate("/login")}
          sx={{ fontSize: "0.75rem", height: 28, cursor: "pointer" }}
        />
      </Tooltip>
    );
  }

  return (
    <FormControl size={size} sx={{ minWidth: 140 }}>
      <InputLabel sx={{ fontSize: "0.8rem" }}>
        {t("chat.model", { defaultValue: "Model" })}
      </InputLabel>
      <Select
        value={models.some((m) => m.alias === value) ? value : ""}
        onChange={handleChange}
        label={t("chat.model", { defaultValue: "Model" })}
        sx={{ fontSize: "0.8rem", height: 32 }}
        MenuProps={{
          anchorOrigin: { vertical: "top", horizontal: "left" },
          transformOrigin: { vertical: "bottom", horizontal: "left" },
        }}
      >
        {models.map((m) => (
          <MenuItem key={m.alias} value={m.alias} sx={{ fontSize: "0.8rem" }}>
            {m.alias}
          </MenuItem>
        ))}
      </Select>
    </FormControl>
  );
}
