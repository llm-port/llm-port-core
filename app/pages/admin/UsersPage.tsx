import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { adminUsers, type AdminUser, type RbacRole } from "~/api/admin";
import { DataTable, type ColumnDef } from "~/components/DataTable";

import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Checkbox from "@mui/material/Checkbox";
import Chip from "@mui/material/Chip";
import Dialog from "@mui/material/Dialog";
import DialogActions from "@mui/material/DialogActions";
import DialogContent from "@mui/material/DialogContent";
import DialogTitle from "@mui/material/DialogTitle";
import FormControlLabel from "@mui/material/FormControlLabel";
import FormGroup from "@mui/material/FormGroup";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";

export default function UsersPage() {
  const { t } = useTranslation();
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [roles, setRoles] = useState<RbacRole[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [editing, setEditing] = useState<AdminUser | null>(null);
  const [selectedRoleIds, setSelectedRoleIds] = useState<string[]>([]);
  const [saving, setSaving] = useState(false);

  async function load() {
    setLoading(true);
    setError(null);
    try {
      const [allUsers, allRoles] = await Promise.all([adminUsers.list(), adminUsers.listRoles()]);
      setUsers(allUsers);
      setRoles(allRoles);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : t("users.failed_load"));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void load();
  }, []);

  function openEdit(user: AdminUser) {
    setEditing(user);
    setSelectedRoleIds(user.roles.map((role) => role.id));
  }

  function toggleRole(roleId: string) {
    setSelectedRoleIds((prev) =>
      prev.includes(roleId) ? prev.filter((id) => id !== roleId) : [...prev, roleId],
    );
  }

  async function saveRoles() {
    if (!editing) return;
    setSaving(true);
    try {
      const updated = await adminUsers.setUserRoles(editing.id, selectedRoleIds);
      setUsers((prev) => prev.map((u) => (u.id === updated.id ? updated : u)));
      setEditing(null);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : t("users.failed_update_roles"));
    } finally {
      setSaving(false);
    }
  }

  const rolePermissionCount = useMemo(() => {
    const map: Record<string, number> = {};
    for (const role of roles) map[role.id] = role.permissions.length;
    return map;
  }, [roles]);

  const columns: ColumnDef<AdminUser>[] = [
    {
      key: "email",
      label: t("users.user"),
      sortable: true,
      sortValue: (u) => u.email,
      searchValue: (u) => u.email,
      render: (u) => (
        <>
          <Typography fontWeight={600}>{u.email}</Typography>
          <Typography variant="caption" color="text.secondary" fontFamily="monospace">
            {u.id}
          </Typography>
        </>
      ),
      minWidth: 320,
    },
    {
      key: "flags",
      label: t("users.flags"),
      searchValue: (u) => `${u.is_active} ${u.is_superuser} ${u.is_verified}`,
      render: (u) => (
        <Stack direction="row" spacing={0.8}>
          {u.is_superuser && <Chip size="small" color="warning" label={t("users.superuser")} />}
          {u.is_active ? (
            <Chip size="small" color="success" label={t("common.active")} />
          ) : (
            <Chip size="small" color="default" label={t("common.inactive")} />
          )}
          {u.is_verified ? (
            <Chip size="small" color="info" label={t("users.verified")} />
          ) : (
            <Chip size="small" color="default" label={t("users.unverified")} />
          )}
        </Stack>
      ),
      minWidth: 260,
    },
    {
      key: "roles",
      label: t("users.roles"),
      searchValue: (u) => u.roles.map((r) => r.name).join(" "),
      render: (u) => (
        <Stack direction="row" spacing={0.8} flexWrap="wrap" useFlexGap>
          {u.roles.length ? (
            u.roles.map((r) => <Chip key={r.id} size="small" label={r.name} />)
          ) : (
            <Typography variant="body2" color="text.secondary">{t("users.no_roles")}</Typography>
          )}
        </Stack>
      ),
      minWidth: 260,
    },
    {
      key: "permissions",
      label: t("users.permissions"),
      sortable: true,
      sortValue: (u) => u.permissions.length,
      searchValue: (u) => u.permissions.map((p) => `${p.resource}:${p.action}`).join(" "),
      render: (u) => <Typography>{u.permissions.length}</Typography>,
      minWidth: 120,
    },
    {
      key: "actions",
      label: t("common.actions"),
      align: "right",
      render: (u) => (
        <Button size="small" variant="outlined" onClick={() => openEdit(u)}>
          {t("users.edit_roles")}
        </Button>
      ),
      minWidth: 140,
    },
  ];

  return (
    <>
      {error && (
        <Alert severity="error" sx={{ mb: 2 }}>
          {error}
        </Alert>
      )}

      <DataTable
        title={t("users.title")}
        rows={users}
        columns={columns}
        rowKey={(u) => u.id}
        loading={loading}
        onRefresh={load}
        emptyMessage={t("users.empty")}
        searchPlaceholder={t("users.search_placeholder")}
      />

      <Dialog open={!!editing} onClose={() => (saving ? undefined : setEditing(null))} fullWidth maxWidth="sm">
        <DialogTitle>{t("users.assign_roles")}</DialogTitle>
        <DialogContent>
          <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
            {editing?.email}
          </Typography>
          <FormGroup>
            {roles.map((role) => (
              <FormControlLabel
                key={role.id}
                control={
                  <Checkbox
                    checked={selectedRoleIds.includes(role.id)}
                    onChange={() => toggleRole(role.id)}
                    disabled={saving}
                  />
                }
                label={t("users.role_permissions", {
                  name: role.name,
                  count: rolePermissionCount[role.id] ?? 0,
                })}
              />
            ))}
          </FormGroup>
          {!roles.length && (
            <Box sx={{ mt: 1 }}>
              <Typography variant="body2" color="text.secondary">{t("users.no_roles_available")}</Typography>
            </Box>
          )}
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setEditing(null)} disabled={saving}>{t("common.cancel")}</Button>
          <Button variant="contained" onClick={saveRoles} disabled={saving}>{t("common.save")}</Button>
        </DialogActions>
      </Dialog>
    </>
  );
}
