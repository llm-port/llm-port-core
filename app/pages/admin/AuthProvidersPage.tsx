import { useState } from "react";
import { useTranslation } from "react-i18next";
import {
  adminAuthProviders,
  type AuthProviderDetail,
} from "~/api/admin";
import { DataTable, type ColumnDef } from "~/components/DataTable";
import { ConfirmDialog } from "~/components/ConfirmDialog";
import { FormDialog } from "~/components/FormDialog";
import { useAsyncData } from "~/lib/useAsyncData";

import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Chip from "@mui/material/Chip";
import FormControlLabel from "@mui/material/FormControlLabel";
import IconButton from "@mui/material/IconButton";
import MenuItem from "@mui/material/MenuItem";
import Stack from "@mui/material/Stack";
import Switch from "@mui/material/Switch";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";

import AddIcon from "@mui/icons-material/Add";
import EditIcon from "@mui/icons-material/Edit";
import DeleteIcon from "@mui/icons-material/Delete";

export default function AuthProvidersPage() {
  const { t } = useTranslation();

  // ── Data loading via useAsyncData ──
  const {
    data: providers,
    loading,
    error,
    refresh: load,
    setError,
  } = useAsyncData(
    () => adminAuthProviders.list(),
    [],
    { initialValue: [] as AuthProviderDetail[] },
  );

  // Editor dialog
  const [editorOpen, setEditorOpen] = useState(false);
  const [editing, setEditing] = useState<AuthProviderDetail | null>(null);
  const [formName, setFormName] = useState("");
  const [formType, setFormType] = useState("oidc");
  const [formClientId, setFormClientId] = useState("");
  const [formClientSecret, setFormClientSecret] = useState("");
  const [formDiscoveryUrl, setFormDiscoveryUrl] = useState("");
  const [formAuthorizeUrl, setFormAuthorizeUrl] = useState("");
  const [formTokenUrl, setFormTokenUrl] = useState("");
  const [formUserinfoUrl, setFormUserinfoUrl] = useState("");
  const [formScopes, setFormScopes] = useState("openid email profile");
  const [formEnabled, setFormEnabled] = useState(true);
  const [formAutoRegister, setFormAutoRegister] = useState(true);
  const [saving, setSaving] = useState(false);

  // Delete dialog
  const [deleteTarget, setDeleteTarget] = useState<AuthProviderDetail | null>(null);
  const [deleting, setDeleting] = useState(false);

  // ── Editor ─────────────────────────────────────────────────────────

  function openCreate() {
    setEditing(null);
    setFormName("");
    setFormType("oidc");
    setFormClientId("");
    setFormClientSecret("");
    setFormDiscoveryUrl("");
    setFormAuthorizeUrl("");
    setFormTokenUrl("");
    setFormUserinfoUrl("");
    setFormScopes("openid email profile");
    setFormEnabled(true);
    setFormAutoRegister(true);
    setEditorOpen(true);
  }

  function openEdit(provider: AuthProviderDetail) {
    setEditing(provider);
    setFormName(provider.name);
    setFormType(provider.provider_type);
    setFormClientId(provider.client_id);
    setFormClientSecret(""); // never show secret
    setFormDiscoveryUrl(provider.discovery_url ?? "");
    setFormAuthorizeUrl(provider.authorize_url ?? "");
    setFormTokenUrl(provider.token_url ?? "");
    setFormUserinfoUrl(provider.userinfo_url ?? "");
    setFormScopes(provider.scopes);
    setFormEnabled(provider.enabled);
    setFormAutoRegister(provider.auto_register);
    setEditorOpen(true);
  }

  async function handleSave() {
    setSaving(true);
    try {
      if (editing) {
        const payload: Record<string, unknown> = {
          name: formName,
          provider_type: formType,
          client_id: formClientId,
          discovery_url: formDiscoveryUrl || undefined,
          authorize_url: formAuthorizeUrl || undefined,
          token_url: formTokenUrl || undefined,
          userinfo_url: formUserinfoUrl || undefined,
          scopes: formScopes,
          enabled: formEnabled,
          auto_register: formAutoRegister,
        };
        if (formClientSecret) {
          payload.client_secret = formClientSecret;
        }
        await adminAuthProviders.update(editing.id, payload);
      } else {
        await adminAuthProviders.create({
          name: formName,
          provider_type: formType,
          client_id: formClientId,
          client_secret: formClientSecret,
          discovery_url: formDiscoveryUrl || undefined,
          authorize_url: formAuthorizeUrl || undefined,
          token_url: formTokenUrl || undefined,
          userinfo_url: formUserinfoUrl || undefined,
          scopes: formScopes,
          enabled: formEnabled,
          auto_register: formAutoRegister,
        });
      }
      setEditorOpen(false);
      await load();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : t("auth_providers.failed_save"));
    } finally {
      setSaving(false);
    }
  }

  // ── Delete ─────────────────────────────────────────────────────────

  async function handleDelete() {
    if (!deleteTarget) return;
    setDeleting(true);
    try {
      await adminAuthProviders.delete(deleteTarget.id);
      setDeleteTarget(null);
      await load();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : t("auth_providers.failed_delete"));
    } finally {
      setDeleting(false);
    }
  }

  // ── Columns ────────────────────────────────────────────────────────

  const columns: ColumnDef<AuthProviderDetail>[] = [
    {
      key: "name",
      label: t("auth_providers.name"),
      sortable: true,
      render: (row) => <Typography fontWeight={600}>{row.name}</Typography>,
    },
    {
      key: "type",
      label: t("auth_providers.type"),
      render: (row) => (
        <Chip label={row.provider_type.toUpperCase()} size="small" variant="outlined" />
      ),
    },
    {
      key: "status",
      label: t("auth_providers.status"),
      render: (row) => (
        <Chip
          label={row.enabled ? t("auth_providers.enabled") : t("auth_providers.disabled")}
          size="small"
          color={row.enabled ? "success" : "default"}
        />
      ),
    },
    {
      key: "auto_register",
      label: t("auth_providers.auto_register"),
      render: (row) => (row.auto_register ? "Yes" : "No"),
    },
    {
      key: "actions",
      label: "",
      render: (row) => (
        <Stack direction="row" spacing={0.5}>
          <IconButton size="small" title={t("auth_providers.edit_title")} onClick={() => openEdit(row)}>
            <EditIcon fontSize="small" />
          </IconButton>
          <IconButton
            size="small"
            color="error"
            title={t("auth_providers.delete_title")}
            onClick={() => setDeleteTarget(row)}
          >
            <DeleteIcon fontSize="small" />
          </IconButton>
        </Stack>
      ),
    },
  ];

  return (
    <Box>
      <DataTable
        columns={columns}
        rows={providers}
        rowKey={(r) => r.id}
        loading={loading}
        error={error}
        emptyMessage={t("auth_providers.empty")}
        title={t("auth_providers.title")}
        searchPlaceholder={t("auth_providers.search_placeholder")}
        onRefresh={load}
        toolbarActions={
          <Button startIcon={<AddIcon />} variant="contained" size="small" onClick={openCreate}>
            {t("auth_providers.create")}
          </Button>
        }
      />

      {/* ── Create / Edit Provider Dialog ──────────────────────────── */}
      <FormDialog
        open={editorOpen}
        title={editing ? t("auth_providers.edit_title") : t("auth_providers.create_title")}
        loading={saving}
        submitLabel={editing ? t("common.save") : t("auth_providers.create")}
        cancelLabel={t("common.cancel")}
        submitDisabled={!formName.trim() || !formClientId.trim() || (!editing && !formClientSecret.trim())}
        onSubmit={handleSave}
        onClose={() => setEditorOpen(false)}
      >
          <Stack spacing={2} sx={{ mt: 1 }}>
            <TextField
              label={t("auth_providers.name")}
              value={formName}
              onChange={(e) => setFormName(e.target.value)}
              required
              fullWidth
              size="small"
            />
            <TextField
              label={t("auth_providers.type")}
              value={formType}
              onChange={(e) => setFormType(e.target.value)}
              select
              fullWidth
              size="small"
            >
              <MenuItem value="oidc">OIDC (OpenID Connect)</MenuItem>
              <MenuItem value="oauth2">OAuth2</MenuItem>
            </TextField>
            <TextField
              label={t("auth_providers.client_id")}
              value={formClientId}
              onChange={(e) => setFormClientId(e.target.value)}
              required
              fullWidth
              size="small"
            />
            <TextField
              label={t("auth_providers.client_secret")}
              value={formClientSecret}
              onChange={(e) => setFormClientSecret(e.target.value)}
              required={!editing}
              fullWidth
              size="small"
              type="password"
              helperText={editing ? "Leave blank to keep existing secret" : undefined}
            />

            {formType === "oidc" && (
              <TextField
                label={t("auth_providers.discovery_url")}
                value={formDiscoveryUrl}
                onChange={(e) => setFormDiscoveryUrl(e.target.value)}
                fullWidth
                size="small"
                placeholder="https://accounts.example.com/.well-known/openid-configuration"
              />
            )}

            {formType === "oauth2" && (
              <>
                <TextField
                  label={t("auth_providers.authorize_url")}
                  value={formAuthorizeUrl}
                  onChange={(e) => setFormAuthorizeUrl(e.target.value)}
                  fullWidth
                  size="small"
                />
                <TextField
                  label={t("auth_providers.token_url")}
                  value={formTokenUrl}
                  onChange={(e) => setFormTokenUrl(e.target.value)}
                  fullWidth
                  size="small"
                />
                <TextField
                  label={t("auth_providers.userinfo_url")}
                  value={formUserinfoUrl}
                  onChange={(e) => setFormUserinfoUrl(e.target.value)}
                  fullWidth
                  size="small"
                />
              </>
            )}

            <TextField
              label={t("auth_providers.scopes")}
              value={formScopes}
              onChange={(e) => setFormScopes(e.target.value)}
              fullWidth
              size="small"
            />

            <Stack direction="row" spacing={2}>
              <FormControlLabel
                control={
                  <Switch
                    checked={formEnabled}
                    onChange={(e) => setFormEnabled(e.target.checked)}
                  />
                }
                label={t("auth_providers.enabled")}
              />
              <FormControlLabel
                control={
                  <Switch
                    checked={formAutoRegister}
                    onChange={(e) => setFormAutoRegister(e.target.checked)}
                  />
                }
                label={t("auth_providers.auto_register")}
              />
            </Stack>
          </Stack>
      </FormDialog>

      {/* ── Delete Confirmation Dialog ────────────────────────────── */}
      <ConfirmDialog
        open={!!deleteTarget}
        title={t("auth_providers.delete_title")}
        message={t("auth_providers.delete_confirm", { name: deleteTarget?.name })}
        confirmLabel={t("common.delete")}
        cancelLabel={t("common.cancel")}
        loading={deleting}
        onConfirm={handleDelete}
        onClose={() => setDeleteTarget(null)}
      />
    </Box>
  );
}
