/**
 * vLLM's own recipe for a model: that its advice is in the suggestions, what
 * of it could not be used here, and the features it offers to opt into.
 */
import { useTranslation } from "react-i18next";

import type { VllmRecipe } from "~/api/marketplace";
import type { EngineConfig } from "~/lib/engine";

import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Checkbox from "@mui/material/Checkbox";
import FormControlLabel from "@mui/material/FormControlLabel";
import Link from "@mui/material/Link";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";

export interface RecipeNoticeProps {
  recipe: VllmRecipe;
  /** With these, usable opt-in features become checkboxes that change *value*. */
  value?: EngineConfig;
  onChange?: (value: EngineConfig) => void;
}

function hasAll(value: EngineConfig, part: EngineConfig): boolean {
  return Object.entries(part).every(([k, v]) => value[k] === v);
}

export function RecipeNotice({ recipe, value, onChange }: RecipeNoticeProps) {
  const { t } = useTranslation();
  const editable = value !== undefined && onChange !== undefined;
  const usable = recipe.opt_in.filter((o) => o.usable);
  const manual = recipe.opt_in.filter((o) => !o.usable);

  function toggle(part: EngineConfig, on: boolean) {
    if (!value || !onChange) return;
    const next = { ...value };
    for (const [k, v] of Object.entries(part)) {
      if (on) next[k] = v;
      else if (next[k] === v) delete next[k];
    }
    onChange(next);
  }

  return (
    <Alert severity={recipe.runtime_too_old ? "warning" : "info"} data-testid="recipe-notice">
      <Stack spacing={0.75}>
        <Typography variant="body2">
          {t("marketplace.recipe.applied")}{" "}
          <Link href={recipe.url} target="_blank" rel="noopener noreferrer">
            {t("marketplace.recipe.open")}
          </Link>
        </Typography>
        {recipe.runtime_too_old && recipe.min_vllm_version && (
          <Typography variant="body2" data-testid="recipe-too-old">
            {t("marketplace.recipe.too_old", { version: recipe.min_vllm_version })}
          </Typography>
        )}
        {recipe.dropped.length > 0 && (
          <Typography variant="caption" color="text.secondary">
            {t("marketplace.recipe.dropped")}{" "}
            <Box component="span" sx={{ fontFamily: "monospace" }}>
              {recipe.dropped.join(" ")}
            </Box>
          </Typography>
        )}
        {usable.length > 0 && (
          <Box>
            <Typography variant="caption" color="text.secondary" display="block">
              {t("marketplace.recipe.opt_in_title")}
            </Typography>
            {usable.map((o) =>
              editable ? (
                <FormControlLabel
                  key={o.name}
                  control={
                    <Checkbox
                      size="small"
                      checked={hasAll(value, o.config)}
                      onChange={(e) => toggle(o.config, e.target.checked)}
                      inputProps={{ "data-testid": `recipe-opt-in-${o.name}` } as React.InputHTMLAttributes<HTMLInputElement>}
                    />
                  }
                  label={<Typography variant="body2">{o.description || o.name}</Typography>}
                />
              ) : (
                <Typography key={o.name} variant="body2">
                  · {o.description || o.name}
                </Typography>
              ),
            )}
          </Box>
        )}
        {manual.length > 0 && (
          <Typography variant="caption" color="text.secondary">
            {t("marketplace.recipe.opt_in_manual", { features: manual.map((o) => o.description || o.name).join("; ") })}
          </Typography>
        )}
      </Stack>
    </Alert>
  );
}
