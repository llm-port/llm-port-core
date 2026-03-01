import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { adminUsers, type RbacRole, type RbacPermission } from "~/api/admin";
import { DataTable, type ColumnDef } from "~/components/DataTable";
import { ConfirmDialog } from "~/components/ConfirmDialog";
import { FormDialog } from "~/components/FormDialog";
import { useAsyncData } from "~/lib/useAsyncData";

import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Checkbox from "@mui/material/Checkbox";
import Chip from "@mui/material/Chip";
import FormControlLabel from "@mui/material/FormControlLabel";
import IconButton from "@mui/material/IconButton";
import Stack from "@mui/material/Stack";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";
import Accordion from "@mui/material/Accordion";
import AccordionSummary from "@mui/material/AccordionSummary";
import AccordionDetails from "@mui/material/AccordionDetails";

import AddIcon from "@mui/icons-material/Add";
import EditIcon from "@mui/icons-material/Edit";
import DeleteIcon from "@mui/icons-material/Delete";
import ExpandMoreIcon from "@mui/icons-material/ExpandMore";
import LockIcon from "@mui/icons-material/Lock";

/** Group permissions by resource for the role editor. */
function groupByResource(perms: RbacPermission[]): Record<string, RbacPermission[]> {
  const map: Record<string, RbacPermission[]> = {};
  for (const p of perms) {
    if (!map[p.resource]) map[p.resource] = [];
    map[p.resource].push(p);
  }
  return map;
}

export default function RolesPage() {
  const { t } = useTranslation();

  // ── Data loading via useAsyncData ──
  const {
    data: { roles, allPermissions },
    loading,
    error,
    refresh: load,
    setError,
  } = useAsyncData(
    async () => {
      const [r, p] = await Promise.all([adminUsers.listRoles(), adminUsers.listPermissions()]);
      return { roles: r, allPermissions: p };
    },
    [],
    { initialValue: { roles: [] as RbacRole[], allPermissions: [] as RbacPermission[] } },
  );

  // Editor dialog state
  const [editorOpen, setEditorOpen] = useState(false);
  const [editingRole, setEditingRole] = useState<RbacRole | null>(null); // null = create
  const [roleName, setRoleName] = useState("");
  const [roleDesc, setRoleDesc] = useState("");
  const [selectedPermIds, setSelectedPermIds] = useState<Set<string>>(new Set());
  const [saving, setSaving] = useState(false);

  // Delete dialog
  const [deleteTarget, setDeleteTarget] = useState<RbacRole | null>(null);
  const [deleting, setDeleting] = useState(false);

  const grouped = useMemo(() => groupByResource(allPermissions), [allPermissions]);

  function openCreate() {
    setEditingRole(null);
    setRoleName("");
    setRoleDesc("");
    setSelectedPermIds(new Set());
    setEditorOpen(true);
  }

  function openEdit(role: RbacRole) {
    setEditingRole(role);
    setRoleName(role.name);
    setRoleDesc(role.description ?? "");
    setSelectedPermIds(new Set(role.permissions.map((p) => p.id)));
    setEditorOpen(true);
  }

  function togglePerm(id: string) {
    setSelectedPermIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function toggleResource(resource: string) {
    const perms = grouped[resource] || [];
    const allSelected = perms.every((p) => selectedPermIds.has(p.id));
    setSelectedPermIds((prev) => {
      const next = new Set(prev);
      for (const p of perms) {
        if (allSelected) next.delete(p.id);
        else next.add(p.id);
      }
      return next;
    });
  }

  async function save() {
    setSaving(true);
    setError(null);
    try {
      if (editingRole) {
        await adminUsers.updateRole(editingRole.id, {
          name: roleName,
          description: roleDesc || undefined,
          permission_ids: [...selectedPermIds],
        });
      } else {
        await adminUsers.createRole({
          name: roleName,
          description: roleDesc || undefined,
          permission_ids: [...selectedPermIds],
        });
      }
      setEditorOpen(false);
      await load();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : t("roles.failed_save"));
    } finally {
      setSaving(false);
    }
  }

  async function confirmDelete() {
    if (!deleteTarget) return;
    setDeleting(true);
    setError(null);
    try {
      await adminUsers.deleteRole(deleteTarget.id);
      setDeleteTarget(null);
      await load();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : t("roles.failed_delete"));
    } finally {
      setDeleting(false);
    }
  }

  const columns: ColumnDef<RbacRole>[] = [
    {
      key: "name",
      label: t("roles.name"),
      sortable: true,
      sortValue: (r) => r.name,
      searchValue: (r) => r.name,
      render: (r) => (
        <Stack direction="row" spacing={1} alignItems="center">
          <Typography fontWeight={600}>{r.name}</Typography>
          {r.is_builtin && (
            <Chip
              icon={<LockIcon />}
              size="small"
              label={t("roles.builtin")}
              color="default"
              variant="outlined"
            />
          )}
        </Stack>
      ),
      minWidth: 200,
    },
    {
      key: "description",
      label: t("roles.description"),
      searchValue: (r) => r.description ?? "",
      render: (r) => (
        <Typography variant="body2" color="text.secondary">
          {r.description || "—"}
        </Typography>
      ),
      minWidth: 200,
    },
    {
      key: "permissions",
      label: t("roles.permissions_count"),
      sortable: true,
      sortValue: (r) => r.permissions.length,
      render: (r) => <Typography>{r.permissions.length}</Typography>,
      minWidth: 120,
    },
    {
      key: "users",
      label: t("roles.users_count"),
      sortable: true,
      sortValue: (r) => r.user_count,
      render: (r) => <Typography>{r.user_count}</Typography>,
      minWidth: 100,
    },
    {
      key: "actions",
      label: t("common.actions"),
      align: "right",
      render: (r) => (
        <Stack direction="row" spacing={0.5} justifyContent="flex-end">
          {!r.is_builtin && (
            <>
              <IconButton size="small" onClick={() => openEdit(r)} title={t("common.edit")}>
                <EditIcon fontSize="small" />
              </IconButton>
              <IconButton size="small" color="error" onClick={() => setDeleteTarget(r)} title={t("common.delete")}>
                <DeleteIcon fontSize="small" />
              </IconButton>
            </>
          )}
          {r.is_builtin && (
            <Typography variant="caption" color="text.secondary">
              {t("roles.readonly")}
            </Typography>
          )}
        </Stack>
      ),
      minWidth: 140,
    },
  ];

  return (
    <>
      {error && (
        <Alert severity="error" sx={{ mb: 2 }} onClose={() => setError(null)}>
          {error}
        </Alert>
      )}

      <DataTable
        title={t("roles.title")}
        rows={roles}
        columns={columns}
        rowKey={(r) => r.id}
        loading={loading}
        onRefresh={load}
        emptyMessage={t("roles.empty")}
        searchPlaceholder={t("roles.search_placeholder")}
        toolbarActions={
          <Button variant="contained" startIcon={<AddIcon />} onClick={openCreate} size="small">
            {t("roles.create")}
          </Button>
        }
      />

      {/* ── Role editor dialog ── */}
      <FormDialog
        open={editorOpen}
        title={editingRole ? t("roles.edit_title") : t("roles.create_title")}
        loading={saving}
        submitLabel={editingRole ? t("common.save") : t("roles.create")}
        cancelLabel={t("common.cancel")}
        submitDisabled={!roleName.trim()}
        onSubmit={save}
        onClose={() => setEditorOpen(false)}
        maxWidth="md"
      >
          <Stack spacing={2} sx={{ mt: 1 }}>
            <TextField
              label={t("roles.name")}
              value={roleName}
              onChange={(e) => setRoleName(e.target.value)}
              fullWidth
              required
              disabled={saving}
              inputProps={{ maxLength: 64 }}
            />
            <TextField
              label={t("roles.description")}
              value={roleDesc}
              onChange={(e) => setRoleDesc(e.target.value)}
              fullWidth
              multiline
              minRows={2}
              disabled={saving}
            />

            <Typography variant="subtitle1" fontWeight={600}>
              {t("roles.select_permissions")}
            </Typography>
            <Typography variant="body2" color="text.secondary">
              {t("roles.selected_count", { count: selectedPermIds.size, total: allPermissions.length })}
            </Typography>

            <Box sx={{ maxHeight: 400, overflow: "auto" }}>
              {Object.entries(grouped)
                .sort(([a], [b]) => a.localeCompare(b))
                .map(([resource, perms]) => {
                  const allChecked = perms.every((p) => selectedPermIds.has(p.id));
                  const someChecked = perms.some((p) => selectedPermIds.has(p.id)) && !allChecked;
                  return (
                    <Accordion key={resource} disableGutters variant="outlined" sx={{ "&:before": { display: "none" } }}>
                      <AccordionSummary expandIcon={<ExpandMoreIcon />}>
                        <FormControlLabel
                          onClick={(e) => e.stopPropagation()}
                          control={
                            <Checkbox
                              checked={allChecked}
                              indeterminate={someChecked}
                              onChange={() => toggleResource(resource)}
                              disabled={saving}
                            />
                          }
                          label={
                            <Typography fontWeight={500} fontFamily="monospace">
                              {resource}
                            </Typography>
                          }
                        />
                      </AccordionSummary>
                      <AccordionDetails sx={{ pl: 6 }}>
                        <Stack direction="row" flexWrap="wrap" gap={1}>
                          {perms.map((p) => (
                            <FormControlLabel
                              key={p.id}
                              control={
                                <Checkbox
                                  size="small"
                                  checked={selectedPermIds.has(p.id)}
                                  onChange={() => togglePerm(p.id)}
                                  disabled={saving}
                                />
                              }
                              label={p.action}
                            />
                          ))}
                        </Stack>
                      </AccordionDetails>
                    </Accordion>
                  );
                })}
            </Box>
          </Stack>
      </FormDialog>

      {/* ── Delete confirmation ── */}
      <ConfirmDialog
        open={!!deleteTarget}
        title={t("roles.delete_title")}
        message={
          <>
            <Typography>
              {t("roles.delete_confirm", { name: deleteTarget?.name ?? "" })}
            </Typography>
            {(deleteTarget?.user_count ?? 0) > 0 && (
              <Alert severity="warning" sx={{ mt: 2 }}>
                {t("roles.delete_warning_users", { count: deleteTarget?.user_count ?? 0 })}
              </Alert>
            )}
          </>
        }
        confirmLabel={t("common.delete")}
        cancelLabel={t("common.cancel")}
        loading={deleting}
        onConfirm={confirmDelete}
        onClose={() => setDeleteTarget(null)}
      />
    </>
  );
}
