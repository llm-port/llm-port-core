/**
 * HfModelSearch — Autocomplete input for searching HuggingFace models.
 *
 * Provides a debounced search against the backend's `/search/hf-search`
 * proxy, with option rendering that shows downloads, likes, and pipeline tag.
 */
import { useState, useEffect } from "react";
import { useTranslation } from "react-i18next";
import Autocomplete from "@mui/material/Autocomplete";
import CircularProgress from "@mui/material/CircularProgress";
import Stack from "@mui/material/Stack";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";
import { search, type HFModelHit } from "~/api/llm";

interface HfModelSearchProps {
  /** Current HF repo ID value. */
  value: string;
  /** Fired when the user picks or types a repo ID. */
  onChange: (repoId: string) => void;
  /** TextField label override. */
  label?: string;
  /** TextField placeholder override. */
  placeholder?: string;
  /** Whether the field is required. */
  required?: boolean;
  /** Whether the field is disabled. */
  disabled?: boolean;
  /** Auto-focus the input on mount. */
  autoFocus?: boolean;
}

export function HfModelSearch({
  value,
  onChange,
  label,
  placeholder = "meta-llama/Llama-3.1-8B",
  required = false,
  disabled = false,
  autoFocus = false,
}: HfModelSearchProps) {
  const { t } = useTranslation();
  const [options, setOptions] = useState<HFModelHit[]>([]);
  const [searching, setSearching] = useState(false);
  const [inputValue, setInputValue] = useState(value);

  // Keep inputValue in sync when `value` changes externally
  useEffect(() => {
    setInputValue(value);
  }, [value]);

  // Debounced HF search
  useEffect(() => {
    if (inputValue.length < 2) {
      setOptions([]);
      return;
    }
    const timer = setTimeout(async () => {
      setSearching(true);
      try {
        const hits = await search.hfModels(inputValue, 12);
        setOptions(hits);
      } catch {
        setOptions([]);
      } finally {
        setSearching(false);
      }
    }, 350);
    return () => clearTimeout(timer);
  }, [inputValue]);

  return (
    <Autocomplete
      freeSolo
      options={options}
      getOptionLabel={(opt) => (typeof opt === "string" ? opt : opt.id)}
      inputValue={inputValue}
      onInputChange={(_, v) => {
        setInputValue(v);
        onChange(v);
      }}
      onChange={(_, v) => {
        if (v && typeof v !== "string") {
          onChange(v.id);
          setInputValue(v.id);
        }
      }}
      loading={searching}
      disabled={disabled}
      renderOption={(props, opt) => {
        const { key, ...rest } = props;
        return (
          <li key={key} {...rest}>
            <Stack sx={{ width: "100%" }}>
              <Typography
                variant="body2"
                fontWeight={600}
                fontFamily="monospace"
                fontSize="0.85rem"
              >
                {(opt as HFModelHit).id}
              </Typography>
              <Typography variant="caption" color="text.secondary">
                {(opt as HFModelHit).pipeline_tag ?? t("llm_models.model")}
                {" · "}
                {t("llm_models.downloads", {
                  count: (opt as HFModelHit).downloads ?? 0,
                })}
                {" · "}
                {t("llm_models.likes", {
                  count: (opt as HFModelHit).likes ?? 0,
                })}
              </Typography>
            </Stack>
          </li>
        );
      }}
      renderInput={(params) => (
        <TextField
          {...params}
          label={label ?? t("llm_models.hf_repo_id")}
          placeholder={placeholder}
          required={required}
          autoFocus={autoFocus}
          slotProps={{
            input: {
              ...params.InputProps,
              endAdornment: (
                <>
                  {searching ? <CircularProgress size={18} /> : null}
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
