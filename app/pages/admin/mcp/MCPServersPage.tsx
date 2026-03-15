import { useState } from "react";
import { useNavigate } from "react-router";
import { useTranslation } from "react-i18next";
import { DataTable, type ColumnDef } from "~/components/DataTable";
import { ConfirmDialog } from "~/components/ConfirmDialog";
import { FormDialog } from "~/components/FormDialog";
import { useAsyncData } from "~/lib/useAsyncData";
import {
  listServers,
  registerServer,
  deleteServer,
  refreshServer,
  type MCPServerSummary,
  type MCPTransportType,
  type PIIMode,
} from "~/api/mcp";

import Alert from "@mui/material/Alert";
import Button from "@mui/material/Button";
import Chip from "@mui/material/Chip";
import IconButton from "@mui/material/IconButton";
import MenuItem from "@mui/material/MenuItem";
import Stack from "@mui/material/Stack";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";

import AddIcon from "@mui/icons-material/Add";
import DeleteIcon from "@mui/icons-material/Delete";
import RefreshIcon from "@mui/icons-material/Refresh";
import OpenInNewIcon from "@mui/icons-material/OpenInNew";
import SettingsIcon from "@mui/icons-material/Settings";

const STATUS_COLOR: Record<
  string,
  "success" | "warning" | "error" | "default" | "info"
> = {
  active: "success",
  degraded: "warning",
  error: "error",
  disconnected: "default",
  disabled: "default",
  registering: "info",
};

export default function MCPServersPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();

  const {
    data: servers,
    loading,
    error,
    refresh: load,
    setError,
  } = useAsyncData(() => listServers(), [], {
    initialValue: [] as MCPServerSummary[],
  });

  // Register dialog
  const [createOpen, setCreateOpen] = useState(false);
  const [newName, setNewName] = useState("");
  const [newTransport, setNewTransport] =
    useState<MCPTransportType>("streamable_http");
  const [newPrefix, setNewPrefix] = useState("");
  const [newUrl, setNewUrl] = useState("");
  const [newCommand, setNewCommand] = useState("");
  const [newPiiMode, setNewPiiMode] = useState<PIIMode>("redact");
  const [creating, setCreating] = useState(false);

  // Delete dialog
  const [deleteTarget, setDeleteTarget] = useState<MCPServerSummary | null>(
    null,
  );
  const [deleting, setDeleting] = useState(false);

  function openCreate() {
    setNewName("");
    setNewTransport("streamable_http");
    setNewPrefix("");
    setNewUrl("");
    setNewCommand("");
    setNewPiiMode("redact");
    setCreateOpen(true);
  }

  async function handleCreate() {
    setCreating(true);
    setError(null);
    try {
      await registerServer({
        name: newName,
        transport: newTransport,
        tool_prefix:
          newPrefix || newName.toLowerCase().replace(/[^a-z0-9]/g, "_"),
        ...(newTransport === "sse" || newTransport === "streamable_http"
          ? { url: newUrl }
          : {}),
        ...(newTransport === "stdio"
          ? { command_json: newCommand.split(/\s+/).filter(Boolean) }
          : {}),
        pii_mode: newPiiMode,
        auto_discover: true,
      });
      setCreateOpen(false);
      await load();
    } catch (err: unknown) {
      setError(
        err instanceof Error ? err.message : "Failed to register server",
      );
    } finally {
      setCreating(false);
    }
  }

  async function handleRefresh(id: string) {
    try {
      await refreshServer(id);
      await load();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Refresh failed");
    }
  }

  async function confirmDelete() {
    if (!deleteTarget) return;
    setDeleting(true);
    setError(null);
    try {
      await deleteServer(deleteTarget.id);
      setDeleteTarget(null);
      await load();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Delete failed");
    } finally {
      setDeleting(false);
    }
  }

  const columns: ColumnDef<MCPServerSummary>[] = [
    {
      key: "name",
      label: t("mcp.name", "Name"),
      sortable: true,
      sortValue: (s) => s.name,
      searchValue: (s) => `${s.name} ${s.tool_prefix}`,
      render: (s) => (
        <>
          <Typography fontWeight={600}>{s.name}</Typography>
          <Typography
            variant="caption"
            color="text.secondary"
            fontFamily="monospace"
          >
            {s.tool_prefix}
          </Typography>
        </>
      ),
      minWidth: 200,
    },
    {
      key: "transport",
      label: t("mcp.transport", "Transport"),
      sortable: true,
      sortValue: (s) => s.transport,
      render: (s) => (
        <Chip
          size="small"
          label={s.transport.toUpperCase()}
          variant="outlined"
        />
      ),
      minWidth: 100,
    },
    {
      key: "status",
      label: t("mcp.status", "Status"),
      sortable: true,
      sortValue: (s) => s.status,
      render: (s) => (
        <Chip
          size="small"
          label={s.status}
          color={STATUS_COLOR[s.status] ?? "default"}
        />
      ),
      minWidth: 120,
    },
    {
      key: "tools",
      label: t("mcp.tool_count", "Tools"),
      sortable: true,
      sortValue: (s) => s.tool_count,
      render: (s) => <Typography>{s.tool_count}</Typography>,
      minWidth: 80,
    },
    {
      key: "pii_mode",
      label: t("mcp.pii_mode", "PII Mode"),
      render: (s) => (
        <Chip size="small" label={s.pii_mode} variant="outlined" />
      ),
      minWidth: 100,
    },
    {
      key: "actions",
      label: t("common.actions", "Actions"),
      align: "right",
      render: (s) => (
        <Stack direction="row" spacing={0.5} justifyContent="flex-end">
          {s.has_settings && (
            <IconButton
              size="small"
              title={t("mcp.provider_settings", "Provider Settings")}
              onClick={() => navigate(`/admin/mcp/servers/${s.id}`)}
              color="primary"
            >
              <SettingsIcon fontSize="small" />
            </IconButton>
          )}
          <IconButton
            size="small"
            title={t("mcp.view_details", "View details")}
            onClick={() => navigate(`/admin/mcp/servers/${s.id}`)}
          >
            <OpenInNewIcon fontSize="small" />
          </IconButton>
          <IconButton
            size="small"
            title={t("mcp.refresh", "Refresh")}
            onClick={() => handleRefresh(s.id)}
          >
            <RefreshIcon fontSize="small" />
          </IconButton>
          <IconButton
            size="small"
            color="error"
            title={t("common.delete", "Delete")}
            onClick={() => setDeleteTarget(s)}
          >
            <DeleteIcon fontSize="small" />
          </IconButton>
        </Stack>
      ),
      minWidth: 160,
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
        title={t("mcp.servers_title", "MCP Servers")}
        rows={servers}
        columns={columns}
        rowKey={(s) => s.id}
        loading={loading}
        onRefresh={load}
        emptyMessage={t("mcp.no_servers", "No MCP servers registered.")}
        searchPlaceholder={t("mcp.search_servers", "Search servers…")}
        toolbarActions={
          <Button
            variant="contained"
            startIcon={<AddIcon />}
            onClick={openCreate}
            size="small"
          >
            {t("mcp.register_server", "Register Server")}
          </Button>
        }
      />

      {/* ── Register server dialog ── */}
      <FormDialog
        open={createOpen}
        title={t("mcp.register_server", "Register Server")}
        loading={creating}
        submitLabel={t("mcp.register", "Register")}
        cancelLabel={t("common.cancel", "Cancel")}
        submitDisabled={
          !newName.trim() ||
          ((newTransport === "sse" || newTransport === "streamable_http") &&
            !newUrl.trim())
        }
        onSubmit={handleCreate}
        onClose={() => setCreateOpen(false)}
      >
        <Stack spacing={2} sx={{ mt: 1 }}>
          <TextField
            label={t("mcp.server_name", "Server Name")}
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
            fullWidth
            required
            disabled={creating}
          />
          <TextField
            label={t("mcp.transport", "Transport")}
            select
            value={newTransport}
            onChange={(e) =>
              setNewTransport(e.target.value as MCPTransportType)
            }
            fullWidth
            disabled={creating}
          >
            <MenuItem value="streamable_http">Streamable HTTP</MenuItem>
            <MenuItem value="sse">SSE (Legacy HTTP)</MenuItem>
            <MenuItem value="stdio">Stdio (Command)</MenuItem>
          </TextField>
          {(newTransport === "sse" || newTransport === "streamable_http") && (
            <TextField
              label={t("mcp.server_url", "Server URL")}
              value={newUrl}
              onChange={(e) => setNewUrl(e.target.value)}
              placeholder={
                newTransport === "streamable_http"
                  ? "http://localhost:8100/mcp/"
                  : "http://localhost:3000/sse"
              }
              fullWidth
              required
              disabled={creating}
            />
          )}
          {newTransport === "stdio" && (
            <TextField
              label={t("mcp.command", "Command")}
              value={newCommand}
              onChange={(e) => setNewCommand(e.target.value)}
              placeholder="npx -y @modelcontextprotocol/server-brave-search"
              fullWidth
              required
              disabled={creating}
              helperText={t(
                "mcp.command_help",
                "Space-separated command and arguments",
              )}
            />
          )}
          <TextField
            label={t("mcp.tool_prefix", "Tool Prefix")}
            value={newPrefix}
            onChange={(e) => setNewPrefix(e.target.value)}
            placeholder={
              newName.toLowerCase().replace(/[^a-z0-9]/g, "_") || "my_server"
            }
            fullWidth
            disabled={creating}
            helperText={t(
              "mcp.prefix_help",
              "Unique prefix for tool names (e.g. 'brave'). Auto-generated if empty.",
            )}
          />
          <TextField
            label={t("mcp.pii_mode", "PII Mode")}
            select
            value={newPiiMode}
            onChange={(e) => setNewPiiMode(e.target.value as PIIMode)}
            fullWidth
            disabled={creating}
          >
            <MenuItem value="allow">Allow (no filtering)</MenuItem>
            <MenuItem value="redact">Redact (remove PII)</MenuItem>
            <MenuItem value="block">Block (reject if PII found)</MenuItem>
          </TextField>
        </Stack>
      </FormDialog>

      {/* ── Delete confirm ── */}
      <ConfirmDialog
        open={!!deleteTarget}
        title={t("mcp.delete_server", "Delete Server")}
        message={t(
          "mcp.delete_confirm",
          `Are you sure you want to delete "${deleteTarget?.name}"? This will remove all associated tools.`,
        )}
        confirmLabel={t("common.delete", "Delete")}
        loading={deleting}
        onConfirm={confirmDelete}
        onClose={() => setDeleteTarget(null)}
      />
    </>
  );
}
