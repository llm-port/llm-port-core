import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  adminGroups,
  adminUsers,
  type Group,
  type GroupMember,
  type RbacRole,
  type AdminUser,
} from "~/api/admin";
import { DataTable, type ColumnDef } from "~/components/DataTable";
import { ConfirmDialog } from "~/components/ConfirmDialog";
import { FormDialog } from "~/components/FormDialog";
import { useAsyncData } from "~/lib/useAsyncData";

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
import IconButton from "@mui/material/IconButton";
import List from "@mui/material/List";
import ListItem from "@mui/material/ListItem";
import ListItemText from "@mui/material/ListItemText";
import Stack from "@mui/material/Stack";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";

import AddIcon from "@mui/icons-material/Add";
import EditIcon from "@mui/icons-material/Edit";
import DeleteIcon from "@mui/icons-material/Delete";
import PeopleIcon from "@mui/icons-material/People";
import PersonRemoveIcon from "@mui/icons-material/PersonRemove";

export default function GroupsPage() {
  const { t } = useTranslation();

  // ── Data loading via useAsyncData ──
  const {
    data: { groups, roles, allUsers },
    loading,
    error,
    refresh: load,
    setError,
  } = useAsyncData(
    async () => {
      const [g, r, u] = await Promise.all([
        adminGroups.list(),
        adminUsers.listRoles(),
        adminUsers.list(),
      ]);
      return { groups: g, roles: r, allUsers: u };
    },
    [],
    { initialValue: { groups: [] as Group[], roles: [] as RbacRole[], allUsers: [] as AdminUser[] } },
  );

  // Group editor dialog
  const [editorOpen, setEditorOpen] = useState(false);
  const [editingGroup, setEditingGroup] = useState<Group | null>(null);
  const [groupName, setGroupName] = useState("");
  const [groupDesc, setGroupDesc] = useState("");
  const [selectedRoleIds, setSelectedRoleIds] = useState<Set<string>>(new Set());
  const [saving, setSaving] = useState(false);

  // Delete dialog
  const [deleteTarget, setDeleteTarget] = useState<Group | null>(null);
  const [deleting, setDeleting] = useState(false);

  // Members dialog
  const [membersGroup, setMembersGroup] = useState<Group | null>(null);
  const [members, setMembers] = useState<GroupMember[]>([]);
  const [membersLoading, setMembersLoading] = useState(false);
  const [addMemberOpen, setAddMemberOpen] = useState(false);
  const [selectedUserIds, setSelectedUserIds] = useState<Set<string>>(new Set());

  // ── Group editor ───────────────────────────────────────────────────

  function openCreate() {
    setEditingGroup(null);
    setGroupName("");
    setGroupDesc("");
    setSelectedRoleIds(new Set());
    setEditorOpen(true);
  }

  function openEdit(group: Group) {
    setEditingGroup(group);
    setGroupName(group.name);
    setGroupDesc(group.description ?? "");
    setSelectedRoleIds(new Set(group.roles.map((r) => r.id)));
    setEditorOpen(true);
  }

  async function handleSave() {
    setSaving(true);
    try {
      const roleIds = Array.from(selectedRoleIds);
      if (editingGroup) {
        await adminGroups.update(editingGroup.id, {
          name: groupName,
          description: groupDesc || undefined,
          role_ids: roleIds,
        });
      } else {
        await adminGroups.create({
          name: groupName,
          description: groupDesc || undefined,
          role_ids: roleIds,
        });
      }
      setEditorOpen(false);
      await load();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : t("groups.failed_save"));
    } finally {
      setSaving(false);
    }
  }

  // ── Delete handler ─────────────────────────────────────────────────

  async function handleDelete() {
    if (!deleteTarget) return;
    setDeleting(true);
    try {
      await adminGroups.delete(deleteTarget.id);
      setDeleteTarget(null);
      await load();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : t("groups.failed_delete"));
    } finally {
      setDeleting(false);
    }
  }

  // ── Members management ────────────────────────────────────────────

  async function openMembers(group: Group) {
    setMembersGroup(group);
    setMembersLoading(true);
    try {
      const m = await adminGroups.listMembers(group.id);
      setMembers(m);
    } catch {
      setMembers([]);
    } finally {
      setMembersLoading(false);
    }
  }

  async function handleRemoveMember(userId: string) {
    if (!membersGroup) return;
    try {
      await adminGroups.removeMember(membersGroup.id, userId);
      setMembers((prev) => prev.filter((m) => m.id !== userId));
      // Refresh group list for updated member_count
      await load();
    } catch {
      /* swallow */
    }
  }

  function openAddMembers() {
    if (!membersGroup) return;
    const currentIds = new Set(members.map((m) => m.id));
    setSelectedUserIds(currentIds);
    setAddMemberOpen(true);
  }

  async function handleSetMembers() {
    if (!membersGroup) return;
    try {
      const updated = await adminGroups.setMembers(
        membersGroup.id,
        Array.from(selectedUserIds),
      );
      setMembers(updated);
      setAddMemberOpen(false);
      await load();
    } catch {
      /* swallow */
    }
  }

  // Available users not in the group
  const availableUsers = useMemo(() => {
    const memberIds = new Set(members.map((m) => m.id));
    return allUsers.filter((u) => !memberIds.has(u.id));
  }, [allUsers, members]);

  // ── Table columns ──────────────────────────────────────────────────

  const columns: ColumnDef<Group>[] = [
    {
      key: "name",
      label: t("groups.name"),
      sortable: true,
      render: (row) => (
        <Typography fontWeight={600}>{row.name}</Typography>
      ),
    },
    {
      key: "description",
      label: t("groups.description"),
      render: (row) => row.description ?? "—",
    },
    {
      key: "roles",
      label: t("groups.roles_count"),
      sortable: true,
      sortValue: (row: Group) => row.roles.length,
      render: (row) => (
        <Stack direction="row" spacing={0.5} flexWrap="wrap">
          {row.roles.map((r) => (
            <Chip key={r.id} label={r.name} size="small" />
          ))}
          {row.roles.length === 0 && "—"}
        </Stack>
      ),
    },
    {
      key: "members",
      label: t("groups.members_count"),
      sortable: true,
      sortValue: (row: Group) => row.member_count,
      render: (row) => row.member_count,
    },
    {
      key: "actions",
      label: "",
      render: (row) => (
        <Stack direction="row" spacing={0.5}>
          <IconButton size="small" title={t("groups.members")} onClick={() => openMembers(row)}>
            <PeopleIcon fontSize="small" />
          </IconButton>
          <IconButton size="small" title={t("groups.edit_title")} onClick={() => openEdit(row)}>
            <EditIcon fontSize="small" />
          </IconButton>
          <IconButton
            size="small"
            color="error"
            title={t("groups.delete_title")}
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
        rows={groups}
        rowKey={(r) => r.id}
        loading={loading}
        error={error}
        emptyMessage={t("groups.empty")}
        title={t("groups.title")}
        searchPlaceholder={t("groups.search_placeholder")}
        onRefresh={load}
        toolbarActions={
          <Button startIcon={<AddIcon />} variant="contained" size="small" onClick={openCreate}>
            {t("groups.create")}
          </Button>
        }
      />

      {/* ── Create / Edit Group Dialog ─────────────────────────────── */}
      <FormDialog
        open={editorOpen}
        title={editingGroup ? t("groups.edit_title") : t("groups.create_title")}
        loading={saving}
        submitLabel={editingGroup ? t("common.save") : t("groups.create")}
        cancelLabel={t("common.cancel")}
        submitDisabled={!groupName.trim()}
        onSubmit={handleSave}
        onClose={() => setEditorOpen(false)}
      >
          <Stack spacing={2} sx={{ mt: 1 }}>
            <TextField
              label={t("groups.name")}
              value={groupName}
              onChange={(e) => setGroupName(e.target.value)}
              required
              fullWidth
              size="small"
            />
            <TextField
              label={t("groups.description")}
              value={groupDesc}
              onChange={(e) => setGroupDesc(e.target.value)}
              fullWidth
              size="small"
              multiline
              rows={2}
            />
            <Typography variant="subtitle2">{t("groups.assign_roles")}</Typography>
            <Box sx={{ maxHeight: 280, overflow: "auto", border: 1, borderColor: "divider", borderRadius: 1, p: 1 }}>
              {roles.map((role) => (
                <FormControlLabel
                  key={role.id}
                  control={
                    <Checkbox
                      size="small"
                      checked={selectedRoleIds.has(role.id)}
                      onChange={() => {
                        setSelectedRoleIds((prev) => {
                          const next = new Set(prev);
                          if (next.has(role.id)) next.delete(role.id);
                          else next.add(role.id);
                          return next;
                        });
                      }}
                    />
                  }
                  label={
                    <Stack direction="row" spacing={1} alignItems="center">
                      <span>{role.name}</span>
                      {role.is_builtin && <Chip label="built-in" size="small" variant="outlined" />}
                    </Stack>
                  }
                />
              ))}
            </Box>
          </Stack>
      </FormDialog>

      {/* ── Delete Confirmation Dialog ────────────────────────────── */}
      <ConfirmDialog
        open={!!deleteTarget}
        title={t("groups.delete_title")}
        message={
          <>
            <Typography>
              {t("groups.delete_confirm", { name: deleteTarget?.name })}
            </Typography>
            {(deleteTarget?.member_count ?? 0) > 0 && (
              <Alert severity="warning" sx={{ mt: 1 }}>
                {t("groups.delete_warning_members", { count: deleteTarget?.member_count })}
              </Alert>
            )}
          </>
        }
        confirmLabel={t("common.delete")}
        cancelLabel={t("common.cancel")}
        loading={deleting}
        onConfirm={handleDelete}
        onClose={() => setDeleteTarget(null)}
      />

      {/* ── Members Management Dialog ─────────────────────────────── */}
      <Dialog
        open={!!membersGroup}
        onClose={() => setMembersGroup(null)}
        maxWidth="sm"
        fullWidth
      >
        <DialogTitle>
          {t("groups.members")} — {membersGroup?.name}
        </DialogTitle>
        <DialogContent>
          {membersLoading ? (
            <Typography>{t("common.loading")}</Typography>
          ) : members.length === 0 ? (
            <Typography color="text.secondary">{t("groups.no_members")}</Typography>
          ) : (
            <List dense>
              {members.map((m) => (
                <ListItem
                  key={m.id}
                  secondaryAction={
                    <IconButton
                      edge="end"
                      size="small"
                      color="error"
                      title={t("groups.remove_member")}
                      onClick={() => handleRemoveMember(m.id)}
                    >
                      <PersonRemoveIcon fontSize="small" />
                    </IconButton>
                  }
                >
                  <ListItemText primary={m.email} />
                </ListItem>
              ))}
            </List>
          )}
        </DialogContent>
        <DialogActions>
          <Button startIcon={<AddIcon />} onClick={openAddMembers}>
            {t("groups.add_members")}
          </Button>
          <Button onClick={() => setMembersGroup(null)}>{t("common.close")}</Button>
        </DialogActions>
      </Dialog>

      {/* ── Add Members Dialog ────────────────────────────────────── */}
      <Dialog open={addMemberOpen} onClose={() => setAddMemberOpen(false)} maxWidth="sm" fullWidth>
        <DialogTitle>{t("groups.manage_members")}</DialogTitle>
        <DialogContent>
          <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
            {t("groups.add_members")}
          </Typography>
          <Box sx={{ maxHeight: 320, overflow: "auto", border: 1, borderColor: "divider", borderRadius: 1, p: 1 }}>
            {allUsers.map((u) => (
              <FormControlLabel
                key={u.id}
                control={
                  <Checkbox
                    size="small"
                    checked={selectedUserIds.has(u.id)}
                    onChange={() => {
                      setSelectedUserIds((prev) => {
                        const next = new Set(prev);
                        if (next.has(u.id)) next.delete(u.id);
                        else next.add(u.id);
                        return next;
                      });
                    }}
                  />
                }
                label={u.email}
              />
            ))}
          </Box>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setAddMemberOpen(false)}>{t("common.cancel")}</Button>
          <Button variant="contained" onClick={handleSetMembers}>
            {t("common.save")}
          </Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
}
