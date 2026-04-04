import { useState } from "react";
import { useParams, useNavigate } from "react-router";
import { useTranslation } from "react-i18next";
import { DataTable, type ColumnDef } from "~/components/DataTable";
import { useAsyncData } from "~/lib/useAsyncData";
import {
  getServer,
  listServerTools,
  refreshServer,
  testServer,
  updateServer,
  updateTool,
  type MCPServerDetail,
  type MCPToolDetail,
  type MCPTestResult,
} from "~/api/mcp";

import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Card from "@mui/material/Card";
import CardContent from "@mui/material/CardContent";
import Chip from "@mui/material/Chip";
import Divider from "@mui/material/Divider";
import Grid from "@mui/material/Grid";
import Stack from "@mui/material/Stack";
import Switch from "@mui/material/Switch";
import Typography from "@mui/material/Typography";

import ArrowBackIcon from "@mui/icons-material/ArrowBack";
import RefreshIcon from "@mui/icons-material/Refresh";
import ScienceIcon from "@mui/icons-material/Science";

import { MCPSettingsPanel } from "./MCPSettingsPanel";

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

export default function MCPServerDetailPage() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const { t } = useTranslation();

  const {
    data: server,
    loading: serverLoading,
    error: serverError,
    refresh: reloadServer,
    setError,
  } = useAsyncData(() => getServer(id!), [id], {
    initialValue: null as MCPServerDetail | null,
  });

  const {
    data: tools,
    loading: toolsLoading,
    refresh: reloadTools,
  } = useAsyncData(() => listServerTools(id!), [id], {
    initialValue: [] as MCPToolDetail[],
  });

  const [testResult, setTestResult] = useState<MCPTestResult | null>(null);
  const [testing, setTesting] = useState(false);

  async function handleRefresh() {
    try {
      await refreshServer(id!);
      await Promise.all([reloadServer(), reloadTools()]);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Refresh failed");
    }
  }

  async function handleTest() {
    setTesting(true);
    setTestResult(null);
    try {
      const result = await testServer(id!);
      setTestResult(result);
    } catch (err: unknown) {
      setTestResult({
        success: false,
        tools_discovered: 0,
        message: err instanceof Error ? err.message : "Test failed",
      });
    } finally {
      setTesting(false);
    }
  }

  async function handleToggleEnabled() {
    if (!server) return;
    try {
      await updateServer(id!, { enabled: !server.enabled });
      await reloadServer();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Update failed");
    }
  }

  async function handleToggleTool(tool: MCPToolDetail) {
    try {
      await updateTool(tool.id, { enabled: !tool.enabled });
      await reloadTools();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Update failed");
    }
  }

  const toolColumns: ColumnDef<MCPToolDetail>[] = [
    {
      key: "name",
      label: t("mcp.tool_name", "Name"),
      sortable: true,
      sortValue: (tool) => tool.qualified_name,
      searchValue: (tool) => `${tool.qualified_name} ${tool.description}`,
      render: (tool) => (
        <>
          <Typography
            fontWeight={600}
            fontFamily="monospace"
            fontSize="0.85rem"
          >
            {tool.qualified_name}
          </Typography>
          <Typography variant="caption" color="text.secondary">
            {tool.description}
          </Typography>
        </>
      ),
      minWidth: 300,
    },
    {
      key: "version",
      label: t("mcp.version", "Version"),
      render: (tool) => (
        <Typography variant="body2" fontFamily="monospace">
          {tool.version}
        </Typography>
      ),
      minWidth: 80,
    },
    {
      key: "enabled",
      label: t("common.enabled", "Enabled"),
      sortable: true,
      sortValue: (tool) => (tool.enabled ? 1 : 0),
      render: (tool) => (
        <Switch
          size="small"
          checked={tool.enabled}
          onChange={() => handleToggleTool(tool)}
        />
      ),
      minWidth: 80,
    },
    {
      key: "last_seen",
      label: t("mcp.last_seen", "Last Seen"),
      sortable: true,
      sortValue: (tool) => tool.last_seen_at,
      render: (tool) => (
        <Typography variant="body2" color="text.secondary">
          {new Date(tool.last_seen_at).toLocaleString()}
        </Typography>
      ),
      minWidth: 160,
    },
  ];

  if (serverLoading && !server) {
    return <Typography>{t("common.loading", "Loading…")}</Typography>;
  }

  return (
    <Box>
      {serverError && (
        <Alert severity="error" sx={{ mb: 2 }} onClose={() => setError(null)}>
          {serverError}
        </Alert>
      )}

      {testResult && (
        <Alert
          severity={testResult.success ? "success" : "error"}
          sx={{ mb: 2 }}
          onClose={() => setTestResult(null)}
        >
          {testResult.message}
          {testResult.success &&
            ` (${testResult.tools_discovered} tools discovered)`}
        </Alert>
      )}

      {/* ── Header ── */}
      <Stack direction="row" alignItems="center" spacing={2} sx={{ mb: 3 }}>
        <Button
          startIcon={<ArrowBackIcon />}
          onClick={() => navigate("/admin/mcp/servers")}
          size="small"
        >
          {t("common.back", "Back")}
        </Button>
        <Typography variant="h5" fontWeight={700} sx={{ flex: 1 }}>
          {server?.name ?? "…"}
        </Typography>
        <Button
          startIcon={<RefreshIcon />}
          onClick={handleRefresh}
          size="small"
          variant="outlined"
        >
          {t("mcp.refresh", "Refresh")}
        </Button>
        <Button
          startIcon={<ScienceIcon />}
          onClick={handleTest}
          disabled={testing}
          size="small"
          variant="outlined"
        >
          {t("mcp.test", "Test")}
        </Button>
      </Stack>

      {/* ── Server info card ── */}
      {server && (
        <Card sx={{ mb: 3 }}>
          <CardContent>
            <Grid container spacing={3}>
              <Grid size={{ xs: 12, sm: 6, md: 3 }}>
                <Typography variant="caption" color="text.secondary">
                  {t("mcp.status", "Status")}
                </Typography>
                <Box sx={{ mt: 0.5 }}>
                  <Chip
                    label={server.status}
                    color={STATUS_COLOR[server.status] ?? "default"}
                    size="small"
                  />
                </Box>
              </Grid>
              <Grid size={{ xs: 12, sm: 6, md: 3 }}>
                <Typography variant="caption" color="text.secondary">
                  {t("mcp.transport", "Transport")}
                </Typography>
                <Typography>{server.transport.toUpperCase()}</Typography>
              </Grid>
              <Grid size={{ xs: 12, sm: 6, md: 3 }}>
                <Typography variant="caption" color="text.secondary">
                  {t("mcp.tool_prefix", "Tool Prefix")}
                </Typography>
                <Typography fontFamily="monospace">
                  {server.tool_prefix}
                </Typography>
              </Grid>
              <Grid size={{ xs: 12, sm: 6, md: 3 }}>
                <Typography variant="caption" color="text.secondary">
                  {t("mcp.pii_mode", "PII Mode")}
                </Typography>
                <Typography>{server.pii_mode}</Typography>
              </Grid>
              <Grid size={{ xs: 12, sm: 6, md: 3 }}>
                <Typography variant="caption" color="text.secondary">
                  {t("common.enabled", "Enabled")}
                </Typography>
                <Box>
                  <Switch
                    checked={server.enabled}
                    onChange={handleToggleEnabled}
                    size="small"
                  />
                </Box>
              </Grid>
              <Grid size={{ xs: 12, sm: 6, md: 3 }}>
                <Typography variant="caption" color="text.secondary">
                  {t("mcp.timeout", "Timeout")}
                </Typography>
                <Typography>{server.timeout_sec}s</Typography>
              </Grid>
              {server.url && (
                <Grid size={{ xs: 12, sm: 6 }}>
                  <Typography variant="caption" color="text.secondary">
                    {t("mcp.url", "URL")}
                  </Typography>
                  <Typography fontFamily="monospace" fontSize="0.85rem">
                    {server.url}
                  </Typography>
                </Grid>
              )}
              {server.command_json && (
                <Grid size={{ xs: 12, sm: 6 }}>
                  <Typography variant="caption" color="text.secondary">
                    {t("mcp.command", "Command")}
                  </Typography>
                  <Typography fontFamily="monospace" fontSize="0.85rem">
                    {server.command_json.join(" ")}
                  </Typography>
                </Grid>
              )}
            </Grid>
          </CardContent>
        </Card>
      )}

      <Divider sx={{ my: 2 }} />

      {/* ── Provider settings (dynamic) ── */}
      {server && server.url && <MCPSettingsPanel serverId={id!} />}

      {/* ── Tools table ── */}
      <DataTable
        title={t("mcp.tools_title", "Discovered Tools")}
        rows={tools}
        columns={toolColumns}
        rowKey={(tool) => tool.id}
        loading={toolsLoading}
        onRefresh={reloadTools}
        emptyMessage={t("mcp.no_tools", "No tools discovered yet.")}
        searchPlaceholder={t("mcp.search_tools", "Search tools…")}
      />
    </Box>
  );
}
