/**
 * ToolPanel — tree-view of available tools grouped by source/server.
 *
 * Tools are grouped into collapsible categories derived from the
 * dotted qualified name (e.g. "mcp.brave.search" → group "brave").
 * Each group and individual tool has a checkbox for bulk/fine control.
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import Box from "@mui/material/Box";
import Checkbox from "@mui/material/Checkbox";
import Collapse from "@mui/material/Collapse";
import IconButton from "@mui/material/IconButton";
import Skeleton from "@mui/material/Skeleton";
import Tooltip from "@mui/material/Tooltip";
import Typography from "@mui/material/Typography";
import CloudIcon from "@mui/icons-material/Cloud";
import ComputerIcon from "@mui/icons-material/Computer";
import ExpandMoreIcon from "@mui/icons-material/ExpandMore";
import ChevronRightIcon from "@mui/icons-material/ChevronRight";
import ExtensionIcon from "@mui/icons-material/Extension";
import HubIcon from "@mui/icons-material/Hub";
import RefreshIcon from "@mui/icons-material/Refresh";
import { useTranslation } from "react-i18next";

import type {
  ExecutionMode,
  ToolAvailabilityEntry,
  ToolAvailabilityResponse,
  ToolRealm,
  ToolSource,
} from "~/api/tools";
import {
  getAvailableTools,
  getToolCatalog,
  patchSessionToolPolicy,
} from "~/api/tools";

// ── Props ──────────────────────────────────────────────────────

interface Props {
  sessionId: string | null;
  executionMode: ExecutionMode;
  localOverrides?: Map<string, boolean>;
  onLocalOverride?: (toolId: string, enabled: boolean) => void;
}

// ── Source / realm visuals ──────────────────────────────────────

const SOURCE_ICON: Record<ToolSource, React.ReactElement> = {
  mcp: <HubIcon fontSize="small" />,
  core: <CloudIcon fontSize="small" />,
  skills: <ExtensionIcon fontSize="small" />,
  local_agent: <ComputerIcon fontSize="small" />,
  plugin: <ExtensionIcon fontSize="small" />,
};

// ── Grouping helpers ───────────────────────────────────────────

interface ToolGroup {
  key: string;
  label: string;
  source: ToolSource;
  realm: ToolRealm;
  tools: ToolAvailabilityEntry[];
}

function buildGroups(tools: ToolAvailabilityEntry[]): ToolGroup[] {
  const map = new Map<string, ToolGroup>();

  for (const tool of tools) {
    // tool_id is "mcp.servername.toolname" or "core.toolname"
    const parts = tool.tool_id.split(".");
    const groupKey =
      parts.length >= 2 ? `${parts[0]}.${parts[1]}` : tool.source;
    const groupLabel = parts.length >= 2 ? parts[1] : tool.source;

    let group = map.get(groupKey);
    if (!group) {
      group = {
        key: groupKey,
        label: groupLabel,
        source: tool.source,
        realm: tool.realm as ToolRealm,
        tools: [],
      };
      map.set(groupKey, group);
    }
    group.tools.push(tool);
  }

  const groups = Array.from(map.values()).sort((a, b) =>
    a.label.localeCompare(b.label),
  );
  for (const g of groups) {
    g.tools.sort((a, b) => {
      const nameA = a.display_name ?? a.tool_id;
      const nameB = b.display_name ?? b.tool_id;
      return nameA.localeCompare(nameB);
    });
  }
  return groups;
}

/** Short display name: last segment of the dotted tool_id. */
function toolShortName(tool: ToolAvailabilityEntry): string {
  if (tool.display_name) return tool.display_name;
  const parts = tool.tool_id.split(".");
  return parts[parts.length - 1];
}

// ── Component ──────────────────────────────────────────────────

export default function ToolPanel({
  sessionId,
  executionMode,
  localOverrides,
  onLocalOverride,
}: Props) {
  const { t } = useTranslation();
  const [catalog, setCatalog] = useState<ToolAvailabilityResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());

  // ── Fetch ──

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const result = sessionId
        ? await getAvailableTools(sessionId)
        : await getToolCatalog(executionMode);
      setCatalog(result);
    } catch {
      /* user can refresh manually */
    } finally {
      setLoading(false);
    }
  }, [sessionId, executionMode]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  // ── Derived state ──

  const tools = catalog?.tools ?? [];
  const groups = useMemo(() => buildGroups(tools), [tools]);
  const enabledCount = tools.filter((t) => t.effective_enabled).length;

  // Auto-expand all groups on first load
  useEffect(() => {
    if (groups.length > 0 && expanded.size === 0) {
      setExpanded(new Set(groups.map((g) => g.key)));
    }
  }, [groups]); // eslint-disable-line react-hooks/exhaustive-deps

  // ── Toggle handlers ──

  const toggleTool = useCallback(
    async (toolId: string, enabled: boolean) => {
      if (!sessionId) {
        onLocalOverride?.(toolId, enabled);
        setCatalog((prev) => {
          if (!prev) return prev;
          return {
            ...prev,
            tools: prev.tools.map((t) =>
              t.tool_id === toolId
                ? {
                    ...t,
                    user_enabled: enabled,
                    effective_enabled: enabled && t.available,
                  }
                : t,
            ),
          };
        });
        return;
      }
      setCatalog((prev) => {
        if (!prev) return prev;
        return {
          ...prev,
          tools: prev.tools.map((t) =>
            t.tool_id === toolId
              ? {
                  ...t,
                  user_enabled: enabled,
                  effective_enabled: enabled && t.policy_allowed && t.available,
                }
              : t,
          ),
        };
      });
      try {
        await patchSessionToolPolicy(sessionId, {
          tool_overrides: [{ tool_id: toolId, enabled }],
        });
      } catch {
        refresh();
      }
    },
    [sessionId, refresh, onLocalOverride],
  );

  const toggleGroup = useCallback(
    async (group: ToolGroup, enabled: boolean) => {
      const ids = group.tools.map((t) => t.tool_id);

      if (!sessionId) {
        for (const id of ids) onLocalOverride?.(id, enabled);
        setCatalog((prev) => {
          if (!prev) return prev;
          return {
            ...prev,
            tools: prev.tools.map((t) =>
              ids.includes(t.tool_id)
                ? {
                    ...t,
                    user_enabled: enabled,
                    effective_enabled: enabled && t.available,
                  }
                : t,
            ),
          };
        });
        return;
      }

      setCatalog((prev) => {
        if (!prev) return prev;
        return {
          ...prev,
          tools: prev.tools.map((t) =>
            ids.includes(t.tool_id)
              ? {
                  ...t,
                  user_enabled: enabled,
                  effective_enabled: enabled && t.policy_allowed && t.available,
                }
              : t,
          ),
        };
      });

      try {
        await patchSessionToolPolicy(sessionId, {
          tool_overrides: ids.map((tool_id) => ({ tool_id, enabled })),
        });
      } catch {
        refresh();
      }
    },
    [sessionId, refresh, onLocalOverride],
  );

  const toggleExpand = useCallback((key: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }, []);

  // ── Render ──

  return (
    <Box sx={{ display: "flex", flexDirection: "column", height: "100%" }}>
      {/* Header */}
      <Box
        sx={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          px: 1.5,
          py: 1,
          borderBottom: 1,
          borderColor: "divider",
          flexShrink: 0,
        }}
      >
        <Typography variant="subtitle2" sx={{ fontWeight: 600 }}>
          {t("tools.panel.title", "Tools")}
          {tools.length > 0 && (
            <Typography
              component="span"
              variant="caption"
              color="text.secondary"
              sx={{ ml: 0.75 }}
            >
              {enabledCount}/{tools.length}
            </Typography>
          )}
        </Typography>
        <IconButton
          size="small"
          onClick={refresh}
          disabled={loading}
          aria-label={t("tools.panel.refresh", "Refresh")}
        >
          <RefreshIcon fontSize="small" />
        </IconButton>
      </Box>

      {/* Body — scrollable */}
      <Box sx={{ flex: 1, overflow: "auto", py: 0.5 }}>
        {loading && tools.length === 0 && (
          <Box sx={{ px: 2, pt: 1 }}>
            {[1, 2, 3].map((i) => (
              <Skeleton
                key={i}
                variant="rectangular"
                height={28}
                sx={{ mb: 0.75, borderRadius: 0.5 }}
              />
            ))}
          </Box>
        )}

        {!loading && tools.length === 0 && (
          <Typography
            variant="body2"
            color="text.secondary"
            sx={{ px: 2, py: 1.5, lineHeight: 1.5 }}
          >
            {t(
              "tools.panel.empty",
              "No tools available. Check that MCP services are configured and running.",
            )}
          </Typography>
        )}

        {groups.map((group) => {
          const isExpanded = expanded.has(group.key);
          const enabledInGroup = group.tools.filter(
            (t) => t.user_enabled,
          ).length;
          const allEnabled = enabledInGroup === group.tools.length;
          const someEnabled = enabledInGroup > 0 && !allEnabled;
          const icon = SOURCE_ICON[group.source] ?? SOURCE_ICON.mcp;

          return (
            <Box key={group.key}>
              {/* Group header */}
              <Box
                sx={{
                  display: "flex",
                  alignItems: "center",
                  px: 0.5,
                  py: 0.25,
                  cursor: "pointer",
                  borderRadius: 1,
                  "&:hover": { bgcolor: "action.hover" },
                  userSelect: "none",
                }}
                onClick={() => toggleExpand(group.key)}
              >
                <IconButton size="small" tabIndex={-1} sx={{ p: 0.25 }}>
                  {isExpanded ? (
                    <ExpandMoreIcon fontSize="small" />
                  ) : (
                    <ChevronRightIcon fontSize="small" />
                  )}
                </IconButton>

                <Checkbox
                  size="small"
                  checked={allEnabled}
                  indeterminate={someEnabled}
                  onClick={(e) => e.stopPropagation()}
                  onChange={(_, checked) => toggleGroup(group, checked)}
                  sx={{ p: 0.25, mr: 0.5 }}
                />

                <Box
                  sx={{
                    display: "flex",
                    alignItems: "center",
                    gap: 0.5,
                    color: "text.secondary",
                    mr: 0.5,
                  }}
                >
                  {icon}
                </Box>

                <Typography
                  variant="body2"
                  sx={{
                    fontWeight: 500,
                    flex: 1,
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                  }}
                >
                  {group.label}
                </Typography>

                <Typography
                  variant="caption"
                  color="text.secondary"
                  sx={{ mr: 0.5, flexShrink: 0 }}
                >
                  {enabledInGroup}/{group.tools.length}
                </Typography>
              </Box>

              {/* Tool items */}
              <Collapse in={isExpanded} unmountOnExit>
                {group.tools.map((tool) => (
                  <ToolRow
                    key={tool.tool_id}
                    tool={tool}
                    onToggle={toggleTool}
                  />
                ))}
              </Collapse>
            </Box>
          );
        })}
      </Box>
    </Box>
  );
}

// ── ToolRow ────────────────────────────────────────────────────

interface ToolRowProps {
  tool: ToolAvailabilityEntry;
  onToggle: (toolId: string, enabled: boolean) => void;
}

function ToolRow({ tool, onToggle }: ToolRowProps) {
  const disabled = !tool.policy_allowed;
  const dimmed = !tool.effective_enabled;

  return (
    <Box
      sx={{
        display: "flex",
        alignItems: "flex-start",
        pl: 4.5,
        pr: 1,
        py: 0.25,
        opacity: dimmed ? 0.55 : 1,
        borderRadius: 1,
        "&:hover": { bgcolor: "action.hover" },
      }}
    >
      <Checkbox
        size="small"
        checked={tool.user_enabled}
        disabled={disabled}
        onChange={(_, checked) => onToggle(tool.tool_id, checked)}
        sx={{ p: 0.25, mt: 0.125, mr: 0.75, flexShrink: 0 }}
      />

      <Box sx={{ minWidth: 0, flex: 1 }}>
        <Typography
          variant="body2"
          sx={{
            fontWeight: 400,
            wordBreak: "break-word",
            lineHeight: 1.4,
          }}
        >
          {toolShortName(tool)}
        </Typography>

        {tool.description && (
          <Typography
            variant="caption"
            color="text.secondary"
            sx={{
              display: "block",
              lineHeight: 1.35,
              wordBreak: "break-word",
              mt: 0.125,
            }}
          >
            {tool.description}
          </Typography>
        )}

        {tool.availability_reason && (
          <Tooltip title={tool.availability_reason} placement="bottom-start">
            <Typography
              variant="caption"
              color="warning.main"
              sx={{
                display: "block",
                lineHeight: 1.35,
                wordBreak: "break-word",
                mt: 0.125,
              }}
            >
              {tool.availability_reason}
            </Typography>
          </Tooltip>
        )}
      </Box>
    </Box>
  );
}
