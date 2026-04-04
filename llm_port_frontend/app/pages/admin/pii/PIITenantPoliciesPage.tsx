import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Chip from "@mui/material/Chip";
import CircularProgress from "@mui/material/CircularProgress";
import Paper from "@mui/material/Paper";
import Stack from "@mui/material/Stack";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";

import PiiPolicyForm from "~/components/PiiPolicyForm";
import {
  clearPIITenantPolicy,
  fetchPIIPolicyOptions,
  getPIITenantPolicy,
  listPIIPolicyTenants,
  normalizePIIPolicy,
  type PIIPolicyConfig,
  type PIIPolicyOptionsResponse,
  type PIITenantPolicyPayload,
  upsertPIITenantPolicy,
} from "~/api/pii";

function jsonPreview(value: unknown): string {
  try {
    return JSON.stringify(value ?? null, null, 2);
  } catch {
    return String(value);
  }
}

export default function PIITenantPoliciesPage() {
  const { t } = useTranslation();
  const [options, setOptions] = useState<PIIPolicyOptionsResponse | null>(null);
  const [optionsLoading, setOptionsLoading] = useState(true);
  const [optionsError, setOptionsError] = useState<string | null>(null);

  const [tenantQuery, setTenantQuery] = useState("");
  const [tenantSuggestions, setTenantSuggestions] = useState<string[]>([]);
  const [loadingSuggestions, setLoadingSuggestions] = useState(false);

  const [tenantLoading, setTenantLoading] = useState(false);
  const [tenantError, setTenantError] = useState<string | null>(null);
  const [tenantData, setTenantData] = useState<PIITenantPolicyPayload | null>(null);

  const [draftPolicy, setDraftPolicy] = useState<PIIPolicyConfig>(
    normalizePIIPolicy(undefined),
  );
  const [saving, setSaving] = useState(false);
  const [statusMessage, setStatusMessage] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setOptionsLoading(true);
    fetchPIIPolicyOptions()
      .then((value) => {
        if (!cancelled) {
          setOptions(value);
          setOptionsError(null);
        }
      })
      .catch((error: unknown) => {
        if (!cancelled) {
          setOptionsError(error instanceof Error ? error.message : "Failed to load options.");
        }
      })
      .finally(() => {
        if (!cancelled) setOptionsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    const query = tenantQuery.trim();
    if (!query) {
      setTenantSuggestions([]);
      return;
    }
    let cancelled = false;
    setLoadingSuggestions(true);
    const timer = setTimeout(() => {
      listPIIPolicyTenants(query, 20)
        .then((resp) => {
          if (!cancelled) setTenantSuggestions(resp.items);
        })
        .catch(() => {
          if (!cancelled) setTenantSuggestions([]);
        })
        .finally(() => {
          if (!cancelled) setLoadingSuggestions(false);
        });
    }, 250);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [tenantQuery]);

  const effectivePolicyPreview = useMemo(
    () => jsonPreview(tenantData?.effective_policy),
    [tenantData],
  );

  async function loadTenant(tenantId: string) {
    const trimmed = tenantId.trim();
    if (!trimmed) return;
    setTenantLoading(true);
    setTenantError(null);
    setStatusMessage(null);
    try {
      const data = await getPIITenantPolicy(trimmed);
      setTenantData(data);
      const basePolicy = data.override_policy ?? data.default_policy ?? normalizePIIPolicy(undefined, options ?? undefined);
      setDraftPolicy(normalizePIIPolicy(basePolicy, options ?? undefined));
    } catch (error: unknown) {
      setTenantError(error instanceof Error ? error.message : "Failed to load tenant policy.");
      setTenantData(null);
    } finally {
      setTenantLoading(false);
    }
  }

  async function saveOverride() {
    const tenantId = tenantData?.tenant_id ?? tenantQuery.trim();
    if (!tenantId) return;
    setSaving(true);
    setTenantError(null);
    setStatusMessage(null);
    try {
      const saved = await upsertPIITenantPolicy(tenantId, draftPolicy);
      setTenantData(saved);
      setStatusMessage(
        t("pii_policy.save_override_success", {
          defaultValue: "Tenant override saved.",
        }),
      );
    } catch (error: unknown) {
      setTenantError(error instanceof Error ? error.message : "Failed to save tenant policy override.");
    } finally {
      setSaving(false);
    }
  }

  async function clearOverride() {
    const tenantId = tenantData?.tenant_id ?? tenantQuery.trim();
    if (!tenantId) return;
    setSaving(true);
    setTenantError(null);
    setStatusMessage(null);
    try {
      const saved = await clearPIITenantPolicy(tenantId);
      setTenantData(saved);
      const basePolicy = saved.default_policy ?? normalizePIIPolicy(undefined, options ?? undefined);
      setDraftPolicy(normalizePIIPolicy(basePolicy, options ?? undefined));
      setStatusMessage(
        t("pii_policy.clear_override_success", {
          defaultValue: "Tenant override cleared.",
        }),
      );
    } catch (error: unknown) {
      setTenantError(error instanceof Error ? error.message : "Failed to clear tenant policy override.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <Stack spacing={2}>
      <Paper variant="outlined" sx={{ p: 2 }}>
        <Typography variant="h6" sx={{ mb: 1 }}>
          {t("pii_policy.tenants_title", { defaultValue: "Tenant PII Policies" })}
        </Typography>
        <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
          {t("pii_policy.tenants_description", {
            defaultValue: "Override the default PII policy for a specific tenant.",
          })}
        </Typography>
        <Stack direction={{ xs: "column", md: "row" }} spacing={1.5}>
          <TextField
            size="small"
            fullWidth
            label={t("pii_policy.tenant_id", { defaultValue: "Tenant ID" })}
            value={tenantQuery}
            onChange={(event) => setTenantQuery(event.target.value)}
            placeholder={t("pii_policy.tenant_id_placeholder", { defaultValue: "tenant-123" })}
          />
          <Button
            variant="contained"
            onClick={() => void loadTenant(tenantQuery)}
            disabled={tenantLoading || !tenantQuery.trim()}
          >
            {tenantLoading ? t("common.loading") : t("pii_policy.load_tenant", { defaultValue: "Load Tenant" })}
          </Button>
        </Stack>
        {loadingSuggestions && (
          <Box sx={{ mt: 1 }}>
            <CircularProgress size={16} />
          </Box>
        )}
        {tenantSuggestions.length > 0 && (
          <Stack direction="row" spacing={1} sx={{ mt: 1.5 }} flexWrap="wrap" useFlexGap>
            {tenantSuggestions.map((tenantId) => (
              <Chip
                key={tenantId}
                label={tenantId}
                onClick={() => {
                  setTenantQuery(tenantId);
                  void loadTenant(tenantId);
                }}
              />
            ))}
          </Stack>
        )}
      </Paper>

      {optionsError && <Alert severity="warning">{optionsError}</Alert>}
      {tenantError && <Alert severity="error">{tenantError}</Alert>}
      {statusMessage && <Alert severity="success">{statusMessage}</Alert>}

      {tenantData && (
        <Stack spacing={2}>
          <Paper variant="outlined" sx={{ p: 2 }}>
            <Typography variant="subtitle1" sx={{ mb: 1, fontWeight: 600 }}>
              {t("pii_policy.override_editor", { defaultValue: "Tenant Override" })}
            </Typography>
            <PiiPolicyForm
              value={draftPolicy}
              options={options}
              disabled={optionsLoading || saving}
              onChange={setDraftPolicy}
            />
            <Stack direction="row" spacing={1} justifyContent="flex-end" sx={{ mt: 2 }}>
              <Button
                variant="outlined"
                color="warning"
                onClick={() => void clearOverride()}
                disabled={saving}
              >
                {t("pii_policy.clear_override", { defaultValue: "Clear Override" })}
              </Button>
              <Button
                variant="contained"
                onClick={() => void saveOverride()}
                disabled={saving || optionsLoading}
              >
                {saving ? t("common.loading") : t("common.save")}
              </Button>
            </Stack>
          </Paper>

          <Paper variant="outlined" sx={{ p: 2 }}>
            <Typography variant="subtitle1" sx={{ mb: 1, fontWeight: 600 }}>
              {t("pii_policy.effective_preview", { defaultValue: "Effective Policy Preview" })}
            </Typography>
            <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
              {t("pii_policy.precedence_hint", {
                defaultValue: "Precedence: tenant override > system default > none.",
              })}
            </Typography>
            <Box
              component="pre"
              sx={{
                m: 0,
                p: 1.5,
                bgcolor: "background.default",
                borderRadius: 1,
                overflowX: "auto",
                fontSize: 12,
              }}
            >
              {effectivePolicyPreview}
            </Box>
          </Paper>
        </Stack>
      )}
    </Stack>
  );
}
