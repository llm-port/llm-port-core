/**
 * VllmEngineArgsPanel — searchable, categorised panel for configuring
 * vLLM engine arguments with recipe (template) support.
 *
 * Designed as a controlled component: the parent owns the values and
 * receives changes via `onChange`.  Only non-default values are stored.
 */
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import Accordion from "@mui/material/Accordion";
import AccordionDetails from "@mui/material/AccordionDetails";
import AccordionSummary from "@mui/material/AccordionSummary";
import Badge from "@mui/material/Badge";
import Box from "@mui/material/Box";
import Checkbox from "@mui/material/Checkbox";
import Chip from "@mui/material/Chip";
import FormControl from "@mui/material/FormControl";
import FormControlLabel from "@mui/material/FormControlLabel";
import InputAdornment from "@mui/material/InputAdornment";
import InputLabel from "@mui/material/InputLabel";
import MenuItem from "@mui/material/MenuItem";
import Select from "@mui/material/Select";
import Stack from "@mui/material/Stack";
import TextField from "@mui/material/TextField";
import Tooltip from "@mui/material/Tooltip";
import Typography from "@mui/material/Typography";

import ExpandMoreIcon from "@mui/icons-material/ExpandMore";
import SearchIcon from "@mui/icons-material/Search";
import RestartAltIcon from "@mui/icons-material/RestartAlt";
import IconButton from "@mui/material/IconButton";

import type { VllmEngineArgDef } from "~/lib/vllm/types";
import {
  VLLM_CATEGORIES,
  VLLM_ENGINE_ARGS,
  filterArgsByVersion,
} from "~/lib/vllm/registry";
import { VLLM_RECIPES, suggestRecipe } from "~/lib/vllm/recipes";

// ── Props ────────────────────────────────────────────────────────────

export interface VllmEngineArgsPanelProps {
  /** Current non-default values, keyed by CLI flag name. */
  values: Record<string, string | number | boolean>;
  /** Called whenever any value changes. */
  onChange: (values: Record<string, string | number | boolean>) => void;
  /** vLLM version string (e.g. "0.7.3") — filters visible args. */
  version?: string;
  /** Model display name for recipe auto-suggestion. */
  modelName?: string;
}

// ── Component ────────────────────────────────────────────────────────

export function VllmEngineArgsPanel({
  values,
  onChange,
  version = "0.7.3",
  modelName,
}: VllmEngineArgsPanelProps) {
  const { t } = useTranslation();
  const [search, setSearch] = useState("");
  const [selectedRecipe, setSelectedRecipe] = useState("");

  // Filter args for the current vLLM version
  const availableArgs = useMemo(
    () => filterArgsByVersion(VLLM_ENGINE_ARGS, version),
    [version],
  );

  // Filter by search query
  const filteredArgs = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return availableArgs;
    return availableArgs.filter(
      (a) =>
        a.flag.includes(q) ||
        a.description.toLowerCase().includes(q) ||
        a.category.includes(q),
    );
  }, [availableArgs, search]);

  // Visible categories (only those with matching args)
  const visibleCategories = useMemo(() => {
    const catIds = new Set(filteredArgs.map((a) => a.category));
    return VLLM_CATEGORIES.filter((c) => catIds.has(c.id));
  }, [filteredArgs]);

  // Suggested recipe based on model name
  const suggestedRecipeId = useMemo(
    () => (modelName ? suggestRecipe(modelName) : undefined),
    [modelName],
  );

  const changedCount = Object.keys(values).length;

  // ── Handlers ─────────────────────────────────────────────────────

  function handleArgChange(
    flag: string,
    value: string | number | boolean | undefined,
  ) {
    const next = { ...values };
    const def = availableArgs.find((a) => a.flag === flag);
    // Remove from map if cleared or equal to default
    if (value === undefined || value === "" || (def && value === def.default)) {
      delete next[flag];
    } else {
      next[flag] = value;
    }
    // If user manually changed a value, clear recipe selection
    setSelectedRecipe("");
    onChange(next);
  }

  function handleRecipeChange(recipeId: string) {
    setSelectedRecipe(recipeId);
    if (!recipeId) {
      onChange({});
      return;
    }
    const recipe = VLLM_RECIPES.find((r) => r.id === recipeId);
    if (recipe) {
      onChange({ ...recipe.args });
    }
  }

  function handleReset() {
    setSelectedRecipe("");
    onChange({});
  }

  // ── Render ───────────────────────────────────────────────────────

  return (
    <Box>
      {/* ── Controls row: recipe + search ───────────────────────── */}
      <Stack direction="row" spacing={1} sx={{ mb: 1.5 }}>
        <FormControl size="small" sx={{ minWidth: 200 }}>
          <InputLabel>{t("vllm.recipe", "Recipe")}</InputLabel>
          <Select
            value={selectedRecipe}
            label={t("vllm.recipe", "Recipe")}
            onChange={(e) => handleRecipeChange(e.target.value)}
          >
            <MenuItem value="">
              <em>{t("vllm.recipe_custom", "Custom")}</em>
            </MenuItem>
            {VLLM_RECIPES.map((r) => (
              <MenuItem key={r.id} value={r.id}>
                <Stack>
                  <Typography variant="body2">
                    {r.name}
                    {r.id === suggestedRecipeId && (
                      <Chip
                        label={t("vllm.suggested", "suggested")}
                        size="small"
                        color="success"
                        variant="outlined"
                        sx={{ ml: 1, height: 18, fontSize: "0.65rem" }}
                      />
                    )}
                  </Typography>
                  <Typography
                    variant="caption"
                    color="text.secondary"
                    sx={{ whiteSpace: "normal", maxWidth: 320 }}
                  >
                    {r.description}
                  </Typography>
                </Stack>
              </MenuItem>
            ))}
          </Select>
        </FormControl>

        <TextField
          size="small"
          placeholder={t("vllm.search_args", "Search parameters...")}
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          fullWidth
          slotProps={{
            input: {
              startAdornment: (
                <InputAdornment position="start">
                  <SearchIcon fontSize="small" />
                </InputAdornment>
              ),
            },
          }}
        />
      </Stack>

      {/* ── Summary chips ────────────────────────────────────────── */}
      <Stack direction="row" spacing={1} sx={{ mb: 1 }} alignItems="center">
        {changedCount > 0 && (
          <Chip
            label={t("vllm.args_modified", {
              count: changedCount,
              defaultValue: "{{count}} modified",
            })}
            size="small"
            color="primary"
            variant="outlined"
          />
        )}
        {changedCount > 0 && (
          <Tooltip title={t("vllm.reset_all", "Reset all to defaults")}>
            <IconButton size="small" onClick={handleReset}>
              <RestartAltIcon fontSize="small" />
            </IconButton>
          </Tooltip>
        )}
      </Stack>

      {/* ── Category accordions ──────────────────────────────────── */}
      <Box
        sx={{
          maxHeight: 380,
          overflow: "auto",
          border: 1,
          borderColor: "divider",
          borderRadius: 1,
        }}
      >
        {visibleCategories.map((cat) => {
          const catArgs = filteredArgs.filter((a) => a.category === cat.id);
          const catChanged = catArgs.filter((a) => a.flag in values).length;

          return (
            <Accordion
              key={cat.id}
              disableGutters
              elevation={0}
              defaultExpanded={catChanged > 0 || search.trim().length > 0}
              sx={{ "&::before": { display: "none" } }}
            >
              <AccordionSummary expandIcon={<ExpandMoreIcon />}>
                <Typography variant="subtitle2" sx={{ flex: 1 }}>
                  {cat.label}
                </Typography>
                {catChanged > 0 && (
                  <Badge
                    badgeContent={catChanged}
                    color="primary"
                    sx={{ mr: 2 }}
                  />
                )}
              </AccordionSummary>
              <AccordionDetails
                sx={{
                  pt: 0,
                  display: "flex",
                  flexDirection: "column",
                  gap: 1.5,
                }}
              >
                {catArgs.map((argDef) => (
                  <ArgField
                    key={argDef.flag}
                    def={argDef}
                    value={values[argDef.flag]}
                    onChange={(v) => handleArgChange(argDef.flag, v)}
                  />
                ))}
              </AccordionDetails>
            </Accordion>
          );
        })}
      </Box>
    </Box>
  );
}

// ── Individual Arg Field ─────────────────────────────────────────────

interface ArgFieldProps {
  def: VllmEngineArgDef;
  value?: string | number | boolean;
  onChange: (v: string | number | boolean | undefined) => void;
}

function ArgField({ def, value, onChange }: ArgFieldProps) {
  const isModified = value !== undefined;

  // Highlight border when value differs from default
  const highlightSx = isModified
    ? ({
        "& .MuiOutlinedInput-notchedOutline": { borderColor: "primary.main" },
      } as const)
    : undefined;

  if (def.type === "boolean") {
    const checked =
      value !== undefined ? Boolean(value) : Boolean(def.default ?? false);
    return (
      <Box>
        <FormControlLabel
          control={
            <Checkbox
              size="small"
              checked={checked}
              onChange={(_, c) =>
                onChange(c === (def.default ?? false) ? undefined : c)
              }
            />
          }
          label={
            <Typography
              variant="body2"
              fontFamily="monospace"
              fontSize="0.8rem"
              color={isModified ? "primary.main" : "text.primary"}
            >
              --{def.flag}
            </Typography>
          }
        />
        <Typography
          variant="caption"
          color="text.secondary"
          sx={{ pl: 4, display: "block", lineHeight: 1.3, mt: -0.5 }}
        >
          {def.description}
        </Typography>
      </Box>
    );
  }

  if (def.type === "enum") {
    const displayVal =
      value !== undefined
        ? String(value)
        : def.default !== undefined
          ? String(def.default)
          : "";
    return (
      <TextField
        select
        size="small"
        fullWidth
        label={`--${def.flag}`}
        value={displayVal}
        onChange={(e) => {
          const v = e.target.value;
          onChange(v === "" || v === String(def.default ?? "") ? undefined : v);
        }}
        helperText={def.description}
        slotProps={{
          inputLabel: { sx: { fontFamily: "monospace" } },
          formHelperText: {
            sx: { fontSize: "0.7rem", mx: 0.5, lineHeight: 1.2 },
          },
        }}
        sx={highlightSx}
      >
        {def.default !== undefined && (
          <MenuItem value={String(def.default)}>
            {String(def.default)}{" "}
            <Typography
              component="span"
              variant="caption"
              color="text.secondary"
              sx={{ ml: 0.5 }}
            >
              (default)
            </Typography>
          </MenuItem>
        )}
        {(def.choices ?? [])
          .filter((c) => String(c) !== String(def.default ?? "__none__"))
          .map((c) => (
            <MenuItem key={c} value={c}>
              {c}
            </MenuItem>
          ))}
      </TextField>
    );
  }

  if (def.type === "number") {
    const displayVal =
      value !== undefined
        ? value
        : def.default !== undefined
          ? def.default
          : "";
    return (
      <TextField
        size="small"
        fullWidth
        type="number"
        label={`--${def.flag}`}
        value={displayVal}
        onChange={(e) => {
          const v = e.target.value;
          if (v === "") {
            onChange(undefined);
          } else {
            const n = Number(v);
            onChange(
              def.default !== undefined && n === def.default ? undefined : n,
            );
          }
        }}
        helperText={
          def.description +
          (def.default !== undefined ? ` (default: ${def.default})` : "")
        }
        slotProps={{
          htmlInput: { step: def.step, min: def.min, max: def.max },
          inputLabel: { sx: { fontFamily: "monospace" } },
          formHelperText: {
            sx: { fontSize: "0.7rem", mx: 0.5, lineHeight: 1.2 },
          },
        }}
        sx={highlightSx}
      />
    );
  }

  // string type
  const displayVal =
    value !== undefined
      ? String(value)
      : def.default !== undefined
        ? String(def.default)
        : "";
  return (
    <TextField
      size="small"
      fullWidth
      label={`--${def.flag}`}
      value={displayVal}
      onChange={(e) => {
        const v = e.target.value;
        onChange(v === "" || v === String(def.default ?? "") ? undefined : v);
      }}
      helperText={def.description}
      slotProps={{
        inputLabel: { sx: { fontFamily: "monospace" } },
        formHelperText: {
          sx: { fontSize: "0.7rem", mx: 0.5, lineHeight: 1.2 },
        },
      }}
      sx={highlightSx}
    />
  );
}
