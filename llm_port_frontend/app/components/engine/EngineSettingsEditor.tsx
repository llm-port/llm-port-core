/**
 * Engine settings, in the words of what they do.
 *
 * Replaces a panel that listed vLLM's command-line flags by name, with
 * defaults of its own invention (tool calling "on", parser "openai") and a
 * version filter pinned to vLLM 0.7.3. Here the choices an operator actually
 * makes come first, each in plain language with what it costs: how long a
 * conversation can be, how much of the card to use, how many requests at
 * once, whether the model calls tools or thinks aloud. Every other vLLM flag
 * is still reachable, searchable, with vLLM's own description -- and the
 * exact command that will run is always one click away.
 *
 * Controlled: the parent owns `value` (vLLM argument names, snake_case) and
 * `extra` (flags typed as text). Only settings that were chosen are in
 * `value`; anything absent is the engine's default. The same editor serves a
 * cluster deployment and a legacy container runtime: the adapters in
 * `~/lib/engine` store it either way.
 */
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import Accordion from "@mui/material/Accordion";
import AccordionDetails from "@mui/material/AccordionDetails";
import AccordionSummary from "@mui/material/AccordionSummary";
import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Card from "@mui/material/Card";
import CardContent from "@mui/material/CardContent";
import Chip from "@mui/material/Chip";
import FormControlLabel from "@mui/material/FormControlLabel";
import Grid from "@mui/material/Grid";
import IconButton from "@mui/material/IconButton";
import InputAdornment from "@mui/material/InputAdornment";
import MenuItem from "@mui/material/MenuItem";
import Slider from "@mui/material/Slider";
import Stack from "@mui/material/Stack";
import Switch from "@mui/material/Switch";
import TextField from "@mui/material/TextField";
import ToggleButton from "@mui/material/ToggleButton";
import ToggleButtonGroup from "@mui/material/ToggleButtonGroup";
import Tooltip from "@mui/material/Tooltip";
import Typography from "@mui/material/Typography";

import ContentCopyIcon from "@mui/icons-material/ContentCopy";
import ExpandMoreIcon from "@mui/icons-material/ExpandMore";
import RestartAltIcon from "@mui/icons-material/RestartAlt";
import SearchIcon from "@mui/icons-material/Search";

import {
  CATALOG,
  PRESETS,
  applyPreset,
  checkSettings,
  commandLine,
  formatBytes,
  formatTokens,
  type EngineConfig,
  type EngineTarget,
  type EngineValue,
  type EngineWarning,
  type FlagDef,
  type HardwareFacts,
  type ModelFacts,
  type PresetId,
} from "~/lib/engine";

export interface EngineSuggestion {
  config: EngineConfig;
  /** Why each suggested value was chosen (a code the interface words). */
  reasons: Record<string, string>;
}

export interface EngineSettingsEditorProps {
  value: EngineConfig;
  extra?: string;
  onChange: (value: EngineConfig, extra: string) => void;
  target: EngineTarget;
  model?: ModelFacts;
  hardware?: HardwareFacts;
  suggested?: EngineSuggestion;
  disabled?: boolean;
}

/** The memory share a cluster uses when none is chosen (the compiler's default). */
const CLUSTER_DEFAULT_SHARE = 0.8;
/** vLLM's own default. */
const ENGINE_DEFAULT_SHARE = 0.92;

const CONTEXT_STEPS = [2048, 4096, 8192, 16384, 32768, 65536, 131072, 262144, 524288, 1048576];
const SEQ_STEPS = [8, 16, 32, 64, 128, 256, 512];

type Tri = "auto" | "on" | "off";

function tri(value: EngineValue | undefined): Tri {
  if (value === true) return "on";
  if (value === false) return "off";
  return "auto";
}

export function EngineSettingsEditor({
  value,
  extra = "",
  onChange,
  target,
  model,
  hardware,
  suggested,
  disabled = false,
}: EngineSettingsEditorProps) {
  const { t } = useTranslation();
  const [preset, setPreset] = useState<PresetId | null>(null);
  const [search, setSearch] = useState("");
  const [onlyChanged, setOnlyChanged] = useState(true);
  const [copied, setCopied] = useState(false);

  const warnings = useMemo(() => checkSettings(value, extra, model, hardware), [value, extra, model, hardware]);
  const reasons = suggested?.reasons ?? {};
  const isEmbedding = (model?.capabilities ?? []).includes("embedding");
  const tp = Math.max(1, hardware?.tensorParallel ?? 1);

  function set(key: string, next: EngineValue | undefined) {
    const copy = { ...value };
    if (next === undefined || next === "") delete copy[key];
    else copy[key] = next;
    setPreset(null);
    onChange(copy, extra);
  }

  function setMany(changes: Record<string, EngineValue | undefined>) {
    const copy = { ...value };
    for (const [key, next] of Object.entries(changes)) {
      if (next === undefined) delete copy[key];
      else copy[key] = next;
    }
    setPreset(null);
    onChange(copy, extra);
  }

  function choosePreset(next: PresetId | null) {
    if (!next) return;
    setPreset(next);
    onChange(applyPreset(value, next, suggested?.config ?? {}, model, hardware), extra);
  }

  // ── context ──
  const trained = model?.maxContext ?? null;
  const fitsMax = hardware?.maxContextThatFits ?? null;
  const contextChoices = useMemo(() => {
    const cap = trained ?? 1048576;
    const steps = CONTEXT_STEPS.filter((s) => s <= cap);
    if (trained && !steps.includes(trained)) steps.push(trained);
    const current = Number(value.max_model_len);
    if (Number.isFinite(current) && current > 0 && !steps.includes(current)) steps.push(current);
    return steps.sort((a, b) => a - b);
  }, [trained, value.max_model_len]);
  const context = Number(value.max_model_len) || null;
  const kvPerConversation =
    model?.kvBytesPerToken && (context ?? trained)
      ? (model.kvBytesPerToken * ((context ?? trained) as number) * (value.kv_cache_dtype === "fp8" ? 0.5 : 1)) / tp
      : null;

  // ── memory share ──
  const defaultShare = target === "cluster" ? CLUSTER_DEFAULT_SHARE : ENGINE_DEFAULT_SHARE;
  const share = Number(value.gpu_memory_utilization) || defaultShare;
  const shareBytes = hardware?.gpuBytes ? hardware.gpuBytes * share : null;

  // ── tools / reasoning ──
  const toolsOn = value.enable_auto_tool_choice === true && Boolean(value.tool_call_parser);
  const suggestedTool = typeof suggested?.config.tool_call_parser === "string" ? suggested.config.tool_call_parser : null;
  const suggestedReasoning =
    typeof suggested?.config.reasoning_parser === "string" ? suggested.config.reasoning_parser : null;

  function suggestionChip(key: string) {
    const code = reasons[key];
    if (!code || suggested?.config[key] === undefined) return null;
    const same = JSON.stringify(suggested.config[key]) === JSON.stringify(value[key]);
    return (
      <Tooltip title={t(`engine.reason.${code}`, { defaultValue: "" })}>
        <Chip
          size="small"
          variant="outlined"
          color={same ? "success" : "default"}
          label={same ? t("engine.suggested") : t("engine.suggested_value", { value: String(suggested.config[key]) })}
          onClick={same || disabled ? undefined : () => set(key, suggested.config[key])}
          sx={{ height: 20, fontSize: "0.7rem" }}
        />
      </Tooltip>
    );
  }

  const changedCount = Object.keys(value).length + (extra.trim() ? 1 : 0);
  const modelName = model?.repoId ?? "";
  const preview = commandLine(modelName, value) + (extra.trim() ? ` ${extra.trim()}` : "");

  return (
    <Stack spacing={2} data-testid="engine-settings">
      {/* ── Presets ─────────────────────────────────────────────── */}
      <Stack direction={{ xs: "column", md: "row" }} spacing={1} alignItems={{ md: "center" }}>
        <Typography variant="body2" color="text.secondary" sx={{ minWidth: 90 }}>
          {t("engine.preset_label")}
        </Typography>
        <ToggleButtonGroup
          exclusive
          size="small"
          value={preset}
          disabled={disabled}
          onChange={(_, next: PresetId | null) => choosePreset(next)}
          sx={{ flexWrap: "wrap" }}
        >
          {PRESETS.map((id) => (
            <Tooltip key={id} title={t(`engine.preset.${id}_help`)}>
              <ToggleButton value={id} data-testid={`engine-preset-${id}`} sx={{ textTransform: "none" }}>
                {t(`engine.preset.${id}`)}
              </ToggleButton>
            </Tooltip>
          ))}
        </ToggleButtonGroup>
        <Box sx={{ flexGrow: 1 }} />
        <Chip
          size="small"
          variant="outlined"
          label={t("engine.changed_count", { count: changedCount })}
        />
        {suggested && (
          <Tooltip title={t("engine.reset_suggested_help")}>
            <span>
              <Button
                size="small"
                startIcon={<RestartAltIcon />}
                disabled={disabled}
                onClick={() => {
                  setPreset(null);
                  onChange({ ...suggested.config }, "");
                }}
              >
                {t("engine.reset_suggested")}
              </Button>
            </span>
          </Tooltip>
        )}
      </Stack>

      {warnings.length > 0 && (
        <Stack spacing={1}>
          {warnings.map((w) => (
            <Alert
              key={w.code}
              severity={w.code === "remote_code" || w.code === "extra_invalid" ? "warning" : "info"}
              data-testid={`engine-warning-${w.code}`}
            >
              {warningText(t, w)}
            </Alert>
          ))}
        </Stack>
      )}

      <Grid container spacing={2}>
        {/* ── Context and memory ─────────────────────────────────── */}
        <Grid size={{ xs: 12, md: 6 }}>
          <Section title={t("engine.section.memory")}>
            <Row
              label={t("engine.context.label")}
              chip={suggestionChip("max_model_len")}
              help={
                context
                  ? t("engine.context.help_set", {
                      tokens: formatTokens(context),
                      words: Math.round((context * 0.75) / 100) * 100,
                    })
                  : trained
                    ? t("engine.context.help_auto", { tokens: formatTokens(trained) })
                    : t("engine.context.help_unknown")
              }
            >
              <TextField
                select
                size="small"
                fullWidth
                disabled={disabled}
                value={context ?? ""}
                onChange={(e) => set("max_model_len", e.target.value === "" ? undefined : Number(e.target.value))}
                slotProps={{ select: { displayEmpty: true }, htmlInput: { "data-testid": "engine-context" } }}
              >
                <MenuItem value="">
                  {trained ? t("engine.context.auto_known", { tokens: formatTokens(trained) }) : t("engine.auto")}
                </MenuItem>
                {contextChoices.map((c) => (
                  <MenuItem key={c} value={c}>
                    {formatTokens(c)} {t("engine.context.tokens")}
                    {c === trained ? ` · ${t("engine.context.model_max")}` : ""}
                    {fitsMax && c > fitsMax ? ` · ${t("engine.context.over_memory")}` : ""}
                  </MenuItem>
                ))}
              </TextField>
              {kvPerConversation !== null && (
                <Typography variant="caption" color="text.secondary">
                  {t("engine.context.cache_cost", { size: formatBytes(kvPerConversation) })}
                </Typography>
              )}
            </Row>

            <Row
              label={t("engine.memory.label")}
              chip={suggestionChip("gpu_memory_utilization")}
              help={
                shareBytes
                  ? t("engine.memory.help_bytes", {
                      size: formatBytes(shareBytes),
                      total: formatBytes(hardware?.gpuBytes ?? null),
                    })
                  : t("engine.memory.help")
              }
            >
              <Stack direction="row" spacing={2} alignItems="center">
                <Slider
                  size="small"
                  min={0.05}
                  max={0.95}
                  step={0.05}
                  disabled={disabled}
                  value={share}
                  valueLabelDisplay="auto"
                  valueLabelFormat={(v) => `${Math.round(v * 100)}%`}
                  onChange={(_, v) => set("gpu_memory_utilization", Math.round((v as number) * 100) / 100)}
                  aria-label={t("engine.memory.label")}
                  data-testid="engine-memory"
                />
                <Typography variant="body2" sx={{ minWidth: 44, textAlign: "right" }}>
                  {Math.round(share * 100)}%
                </Typography>
              </Stack>
              {value.gpu_memory_utilization === undefined && (
                <Typography variant="caption" color="text.secondary">
                  {t("engine.memory.default", { pct: Math.round(defaultShare * 100) })}
                </Typography>
              )}
            </Row>

            <Row
              label={t("engine.kv8.label")}
              chip={suggestionChip("kv_cache_dtype")}
              help={t("engine.kv8.help")}
            >
              <Switch
                disabled={disabled}
                checked={value.kv_cache_dtype === "fp8"}
                onChange={(_, on) => set("kv_cache_dtype", on ? "fp8" : undefined)}
                slotProps={{ input: { "aria-label": t("engine.kv8.label") } }}
              />
            </Row>
          </Section>
        </Grid>

        {/* ── Throughput ─────────────────────────────────────────── */}
        <Grid size={{ xs: 12, md: 6 }}>
          <Section title={t("engine.section.throughput")}>
            <Row
              label={t("engine.seqs.label")}
              chip={suggestionChip("max_num_seqs")}
              help={t("engine.seqs.help")}
            >
              <TextField
                select
                size="small"
                fullWidth
                disabled={disabled}
                value={value.max_num_seqs ?? ""}
                onChange={(e) => set("max_num_seqs", e.target.value === "" ? undefined : Number(e.target.value))}
                slotProps={{ select: { displayEmpty: true } }}
              >
                <MenuItem value="">{t("engine.auto")}</MenuItem>
                {[...new Set([...SEQ_STEPS, ...(typeof value.max_num_seqs === "number" ? [value.max_num_seqs] : [])])]
                  .sort((a, b) => a - b)
                  .map((n) => (
                    <MenuItem key={n} value={n}>
                      {n}
                    </MenuItem>
                  ))}
              </TextField>
            </Row>
            <TriRow
              label={t("engine.prefix.label")}
              help={t("engine.prefix.help")}
              value={tri(value.enable_prefix_caching)}
              disabled={disabled}
              onChange={(v) => set("enable_prefix_caching", v === "auto" ? undefined : v === "on")}
              chip={suggestionChip("enable_prefix_caching")}
            />
            <TriRow
              label={t("engine.chunked.label")}
              help={t("engine.chunked.help")}
              value={tri(value.enable_chunked_prefill)}
              disabled={disabled}
              onChange={(v) => set("enable_chunked_prefill", v === "auto" ? undefined : v === "on")}
              chip={suggestionChip("enable_chunked_prefill")}
            />
          </Section>
        </Grid>

        {/* ── What it can do ─────────────────────────────────────── */}
        <Grid size={{ xs: 12, md: 6 }}>
          <Section title={t("engine.section.abilities")}>
            {isEmbedding ? (
              <Row
                label={t("engine.embed.label")}
                chip={suggestionChip("runner")}
                help={t("engine.embed.help")}
              >
                <Switch
                  disabled={disabled}
                  checked={value.runner === "pooling"}
                  onChange={(_, on) =>
                    setMany(
                      on
                        ? { runner: "pooling", ...(suggested?.config.convert ? { convert: suggested.config.convert } : {}) }
                        : { runner: undefined, convert: undefined },
                    )
                  }
                  slotProps={{ input: { "aria-label": t("engine.embed.label") } }}
                />
              </Row>
            ) : (
              <>
                <Row
                  label={t("engine.tools.label")}
                  chip={suggestionChip("tool_call_parser")}
                  help={toolsOn ? t("engine.tools.help_on") : t("engine.tools.help_off")}
                >
                  <Stack direction="row" spacing={1} alignItems="center">
                    <Switch
                      disabled={disabled}
                      checked={toolsOn}
                      onChange={(_, on) =>
                        setMany(
                          on
                            ? {
                                enable_auto_tool_choice: true,
                                tool_call_parser:
                                  (typeof value.tool_call_parser === "string" && value.tool_call_parser) ||
                                  suggestedTool ||
                                  "hermes",
                              }
                            : { enable_auto_tool_choice: undefined, tool_call_parser: undefined },
                        )
                      }
                      slotProps={{ input: { "aria-label": t("engine.tools.label") } }}
                    />
                    {toolsOn && (
                      <TextField
                        select
                        size="small"
                        fullWidth
                        disabled={disabled}
                        label={t("engine.tools.parser")}
                        value={String(value.tool_call_parser)}
                        onChange={(e) => set("tool_call_parser", e.target.value)}
                        slotProps={{ htmlInput: { "data-testid": "engine-tool-parser" } }}
                      >
                        {CATALOG.toolParsers.map((p) => (
                          <MenuItem key={p} value={p}>
                            {p}
                            {p === suggestedTool ? ` · ${t("engine.suggested")}` : ""}
                          </MenuItem>
                        ))}
                      </TextField>
                    )}
                  </Stack>
                </Row>
                <Row
                  label={t("engine.reasoning.label")}
                  chip={suggestionChip("reasoning_parser")}
                  help={t("engine.reasoning.help")}
                >
                  <TextField
                    select
                    size="small"
                    fullWidth
                    disabled={disabled}
                    value={typeof value.reasoning_parser === "string" ? value.reasoning_parser : ""}
                    onChange={(e) => set("reasoning_parser", e.target.value || undefined)}
                    slotProps={{ select: { displayEmpty: true }, htmlInput: { "data-testid": "engine-reasoning" } }}
                  >
                    <MenuItem value="">{t("engine.reasoning.off")}</MenuItem>
                    {CATALOG.reasoningParsers.map((p) => (
                      <MenuItem key={p} value={p}>
                        {p}
                        {p === suggestedReasoning ? ` · ${t("engine.suggested")}` : ""}
                      </MenuItem>
                    ))}
                  </TextField>
                </Row>
              </>
            )}
          </Section>
        </Grid>

        {/* ── Compatibility ──────────────────────────────────────── */}
        <Grid size={{ xs: 12, md: 6 }}>
          <Section title={t("engine.section.compatibility")}>
            <Row label={t("engine.eager.label")} chip={suggestionChip("enforce_eager")} help={t("engine.eager.help")}>
              <Switch
                disabled={disabled}
                checked={value.enforce_eager === true}
                onChange={(_, on) => set("enforce_eager", on ? true : undefined)}
                slotProps={{ input: { "aria-label": t("engine.eager.label") } }}
              />
            </Row>
            <Row
              label={t("engine.remote.label")}
              help={model?.needsRemoteCode ? t("engine.remote.help_needed") : t("engine.remote.help")}
            >
              <Switch
                disabled={disabled}
                color="warning"
                checked={value.trust_remote_code === true}
                onChange={(_, on) => set("trust_remote_code", on ? true : undefined)}
                slotProps={{ input: { "aria-label": t("engine.remote.label") } }}
              />
            </Row>
          </Section>
        </Grid>
      </Grid>

      {/* ── Every vLLM flag ────────────────────────────────────────── */}
      <Accordion variant="outlined" disableGutters>
        <AccordionSummary expandIcon={<ExpandMoreIcon />}>
          <Typography variant="subtitle2">
            {t("engine.all.title", { count: CATALOG.flags.length, version: CATALOG.vllmVersion })}
          </Typography>
        </AccordionSummary>
        <AccordionDetails>
          <AllFlags
            value={value}
            target={target}
            search={search}
            onSearch={setSearch}
            onlyChanged={onlyChanged}
            onOnlyChanged={setOnlyChanged}
            onSet={set}
            disabled={disabled}
          />
          <Box sx={{ mt: 2 }}>
            <Typography variant="subtitle2" gutterBottom>
              {t("engine.extra.title")}
            </Typography>
            <TextField
              fullWidth
              multiline
              minRows={2}
              size="small"
              disabled={disabled}
              value={extra}
              placeholder="--max-num-batched-tokens 8192 --async-scheduling"
              onChange={(e) => onChange(value, e.target.value)}
              helperText={t("engine.extra.help")}
              slotProps={{ input: { sx: { fontFamily: "monospace", fontSize: "0.8rem" } } }}
            />
          </Box>
        </AccordionDetails>
      </Accordion>

      {/* ── What will run ──────────────────────────────────────────── */}
      <Box>
        <Stack direction="row" alignItems="center" spacing={1}>
          <Typography variant="caption" color="text.secondary">
            {target === "cluster" ? t("engine.preview.cluster") : t("engine.preview.container")}
          </Typography>
          <Tooltip title={copied ? t("nodes.onboard.copied") : t("common.copy")}>
            <IconButton
              size="small"
              onClick={() => {
                void navigator.clipboard?.writeText(preview).then(() => {
                  setCopied(true);
                  window.setTimeout(() => setCopied(false), 1500);
                });
              }}
            >
              <ContentCopyIcon sx={{ fontSize: 14 }} />
            </IconButton>
          </Tooltip>
        </Stack>
        <Box
          component="pre"
          data-testid="engine-preview"
          sx={{
            m: 0,
            p: 1,
            bgcolor: "action.hover",
            borderRadius: 1,
            fontFamily: "monospace",
            fontSize: "0.75rem",
            whiteSpace: "pre-wrap",
            wordBreak: "break-all",
          }}
        >
          {preview}
        </Box>
      </Box>
    </Stack>
  );
}

function warningText(t: (k: string, o?: Record<string, unknown>) => string, w: EngineWarning): string {
  switch (w.code) {
    case "context_over_model":
    case "context_over_memory":
      return t(`engine.warning.${w.code}`, { max: formatTokens(w.max) });
    case "unknown_tool_parser":
    case "unknown_reasoning_parser":
      return t(`engine.warning.${w.code}`, { value: w.value });
    case "extra_invalid":
      return t("engine.warning.extra_invalid", { tokens: w.tokens.join(" ") });
    default:
      return t(`engine.warning.${w.code}`);
  }
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <Card variant="outlined" sx={{ height: "100%" }}>
      <CardContent>
        <Typography variant="subtitle2" sx={{ mb: 1.5 }}>
          {title}
        </Typography>
        <Stack spacing={2}>{children}</Stack>
      </CardContent>
    </Card>
  );
}

function Row({
  label,
  help,
  chip,
  children,
}: {
  label: string;
  help?: string;
  chip?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <Box>
      <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 0.5 }}>
        <Typography variant="body2" fontWeight={600}>
          {label}
        </Typography>
        {chip}
      </Stack>
      {children}
      {help && (
        <Typography variant="caption" color="text.secondary" display="block" sx={{ mt: 0.5, lineHeight: 1.35 }}>
          {help}
        </Typography>
      )}
    </Box>
  );
}

function TriRow({
  label,
  help,
  value,
  onChange,
  disabled,
  chip,
}: {
  label: string;
  help: string;
  value: Tri;
  onChange: (v: Tri) => void;
  disabled?: boolean;
  chip?: React.ReactNode;
}) {
  const { t } = useTranslation();
  return (
    <Row label={label} help={help} chip={chip}>
      <ToggleButtonGroup
        exclusive
        size="small"
        value={value}
        disabled={disabled}
        onChange={(_, v: Tri | null) => v && onChange(v)}
      >
        <ToggleButton value="auto" sx={{ textTransform: "none" }}>
          {t("engine.auto")}
        </ToggleButton>
        <ToggleButton value="on" sx={{ textTransform: "none" }}>
          {t("engine.on")}
        </ToggleButton>
        <ToggleButton value="off" sx={{ textTransform: "none" }}>
          {t("engine.off")}
        </ToggleButton>
      </ToggleButtonGroup>
    </Row>
  );
}

function AllFlags({
  value,
  target,
  search,
  onSearch,
  onlyChanged,
  onOnlyChanged,
  onSet,
  disabled,
}: {
  value: EngineConfig;
  target: EngineTarget;
  search: string;
  onSearch: (s: string) => void;
  onlyChanged: boolean;
  onOnlyChanged: (v: boolean) => void;
  onSet: (key: string, v: EngineValue | undefined) => void;
  disabled?: boolean;
}) {
  const { t } = useTranslation();
  const q = search.trim().toLowerCase();
  const shown = CATALOG.flags.filter((f) => {
    if (f.containerOnly && target === "cluster") return false;
    if (q) return f.flag.includes(q.replace(/^--/, "")) || (f.help ?? "").toLowerCase().includes(q);
    return !onlyChanged || f.key in value;
  });
  return (
    <Stack spacing={1.5}>
      <Stack direction={{ xs: "column", sm: "row" }} spacing={1} alignItems={{ sm: "center" }}>
        <TextField
          size="small"
          fullWidth
          value={search}
          onChange={(e) => onSearch(e.target.value)}
          placeholder={t("engine.all.search")}
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
        <FormControlLabel
          sx={{ whiteSpace: "nowrap" }}
          control={<Switch size="small" checked={onlyChanged} onChange={(_, v) => onOnlyChanged(v)} />}
          label={t("engine.all.only_set")}
        />
      </Stack>
      {shown.length === 0 && (
        <Typography variant="body2" color="text.secondary">
          {q ? t("engine.all.no_match") : t("engine.all.none_set")}
        </Typography>
      )}
      {shown.slice(0, 80).map((def) => (
        <FlagRow key={def.key} def={def} value={value[def.key]} onSet={onSet} disabled={disabled} />
      ))}
      {shown.length > 80 && (
        <Typography variant="caption" color="text.secondary">
          {t("engine.all.more", { count: shown.length - 80 })}
        </Typography>
      )}
    </Stack>
  );
}

function FlagRow({
  def,
  value,
  onSet,
  disabled,
}: {
  def: FlagDef;
  value: EngineValue | undefined;
  onSet: (key: string, v: EngineValue | undefined) => void;
  disabled?: boolean;
}) {
  const { t } = useTranslation();
  const defaultText = def.default !== undefined ? String(def.default) : t("engine.auto");
  let control: React.ReactNode;
  if (def.type === "boolean") {
    control = (
      <TextField
        select
        size="small"
        sx={{ minWidth: 140 }}
        disabled={disabled}
        value={value === undefined ? "" : value ? "true" : "false"}
        onChange={(e) => onSet(def.key, e.target.value === "" ? undefined : e.target.value === "true")}
        slotProps={{ select: { displayEmpty: true } }}
      >
        <MenuItem value="">{t("engine.default_is", { value: defaultText })}</MenuItem>
        <MenuItem value="true">{t("engine.on")}</MenuItem>
        <MenuItem value="false">{t("engine.off")}</MenuItem>
      </TextField>
    );
  } else if (def.type === "enum" && def.choices) {
    control = (
      <TextField
        select
        size="small"
        sx={{ minWidth: 180 }}
        disabled={disabled}
        value={value === undefined ? "" : String(value)}
        onChange={(e) => onSet(def.key, e.target.value || undefined)}
        slotProps={{ select: { displayEmpty: true } }}
      >
        <MenuItem value="">{t("engine.default_is", { value: defaultText })}</MenuItem>
        {def.choices.map((c) => (
          <MenuItem key={c} value={c}>
            {c}
          </MenuItem>
        ))}
      </TextField>
    );
  } else {
    const numeric = def.type === "integer" || def.type === "number";
    control = (
      <TextField
        size="small"
        sx={{ minWidth: 180 }}
        disabled={disabled}
        type={numeric ? "number" : "text"}
        placeholder={defaultText}
        value={value === undefined ? "" : String(value)}
        onChange={(e) => {
          const raw = e.target.value;
          if (raw === "") onSet(def.key, undefined);
          else onSet(def.key, numeric && !Number.isNaN(Number(raw)) ? Number(raw) : raw);
        }}
      />
    );
  }
  return (
    <Stack
      direction={{ xs: "column", md: "row" }}
      spacing={1.5}
      sx={{ borderTop: 1, borderColor: "divider", pt: 1 }}
      data-testid={`engine-flag-${def.flag}`}
    >
      <Box sx={{ flex: 1, minWidth: 0 }}>
        <Typography variant="body2" sx={{ fontFamily: "monospace", color: value !== undefined ? "primary.main" : undefined }}>
          --{def.flag}
        </Typography>
        {def.help && (
          <Typography variant="caption" color="text.secondary" sx={{ display: "block", lineHeight: 1.35 }}>
            {def.help}
          </Typography>
        )}
      </Box>
      <Stack direction="row" spacing={0.5} alignItems="flex-start">
        {control}
        {value !== undefined && (
          <Tooltip title={t("engine.reset_one")}>
            <IconButton size="small" disabled={disabled} onClick={() => onSet(def.key, undefined)}>
              <RestartAltIcon fontSize="small" />
            </IconButton>
          </Tooltip>
        )}
      </Stack>
    </Stack>
  );
}
