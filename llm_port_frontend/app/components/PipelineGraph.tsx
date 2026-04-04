/**
 * PipelineGraph — interactive React Flow visualisation of a single
 * gateway request's journey through the LLM.port pipeline.
 *
 * Node categories (each with a distinct colour scheme):
 *   gateway  → request ingress / egress (purple)
 *   security → PII, rate-limiting (amber)
 *   ai       → model resolve, upstream LLM, retry (blue)
 *   data     → RAG, skills, audit (teal)
 *   tool     → MCP tool calls (cyan)
 *   error    → any stage that errored (red)
 *   skipped  → stage not applicable (dimmed)
 */
import { memo, useMemo, type CSSProperties, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import ReactFlow, {
  Background,
  type Edge,
  Handle,
  type Node,
  type NodeProps,
  Position,
} from "reactflow";
import "reactflow/dist/style.css";

import { alpha, useTheme } from "@mui/material/styles";
import Box from "@mui/material/Box";

import type { RequestLog, ToolCallLog } from "~/api/observability";

// ── Category colour definitions ──────────────────────────────────────────────

type NodeCategory = "gateway" | "security" | "ai" | "data" | "tool";

/** Base colours per category — used to derive gradient + border + glow. */
const CATEGORY_COLOURS: Record<
  NodeCategory,
  { h: number; s: number; l: number }
> = {
  gateway: { h: 265, s: 70, l: 58 }, // purple
  security: { h: 38, s: 92, l: 50 }, // amber
  ai: { h: 217, s: 80, l: 56 }, // blue
  data: { h: 174, s: 62, l: 47 }, // teal
  tool: { h: 187, s: 85, l: 48 }, // cyan
};

const CATEGORY_ICONS: Record<NodeCategory, string> = {
  gateway: "⇌",
  security: "🛡",
  ai: "🧠",
  data: "📊",
  tool: "🔧",
};

// ── Stage definitions ────────────────────────────────────────────────────────

type StageStatus = "active" | "skipped" | "error";

interface StageConfig {
  id: string;
  labelKey: string;
  fallbackLabel: string;
  category: NodeCategory;
  status: (r: RequestLog) => StageStatus;
  detail?: (r: RequestLog) => string | null;
}

const STAGES: StageConfig[] = [
  {
    id: "request_in",
    labelKey: "observability.pipeline.request_in",
    fallbackLabel: "Request In",
    category: "gateway",
    status: () => "active",
    detail: (r) => r.endpoint,
  },
  {
    id: "rate_limit",
    labelKey: "observability.pipeline.rate_limit",
    fallbackLabel: "Rate Limit",
    category: "security",
    status: (r) => (r.status_code === 429 ? "error" : "active"),
  },
  {
    id: "model_resolve",
    labelKey: "observability.pipeline.model_resolve",
    fallbackLabel: "Model Resolve",
    category: "ai",
    status: () => "active",
    detail: (r) => r.model_alias ?? null,
  },
  {
    id: "provider_select",
    labelKey: "observability.pipeline.provider_select",
    fallbackLabel: "Provider Select",
    category: "ai",
    status: (r) =>
      r.provider_instance_id
        ? "active"
        : r.status_code >= 500
          ? "error"
          : "active",
    detail: (r) =>
      r.provider_instance_id
        ? r.provider_instance_id.length > 12
          ? `…${r.provider_instance_id.slice(-8)}`
          : r.provider_instance_id
        : null,
  },
  {
    id: "rag_inject",
    labelKey: "observability.pipeline.rag_inject",
    fallbackLabel: "RAG Context",
    category: "data",
    status: (r) => (r.rag_context ? "active" : "skipped"),
    detail: (r) =>
      r.rag_context ? `${r.rag_context.chunk_count} chunks` : null,
  },
  {
    id: "skills",
    labelKey: "observability.pipeline.skills",
    fallbackLabel: "Skills",
    category: "data",
    status: (r) =>
      r.skills_used && r.skills_used.length > 0 ? "active" : "skipped",
    detail: (r) =>
      r.skills_used && r.skills_used.length > 0
        ? r.skills_used.map((s) => s.name).join(", ")
        : null,
  },
  {
    id: "pii_egress",
    labelKey: "observability.pipeline.pii_egress",
    fallbackLabel: "PII Scan (out)",
    category: "security",
    status: () => "active",
  },
  {
    id: "upstream",
    labelKey: "observability.pipeline.upstream",
    fallbackLabel: "Upstream LLM",
    category: "ai",
    status: (r) => (r.error_code || r.status_code >= 500 ? "error" : "active"),
    detail: (r) => {
      const parts: string[] = [];
      if (r.latency_ms) parts.push(`${r.latency_ms} ms`);
      if (r.stream != null) parts.push(r.stream ? "stream" : "non-stream");
      return parts.join(" · ") || null;
    },
  },
  {
    id: "retry",
    labelKey: "observability.pipeline.retry",
    fallbackLabel: "Retry",
    category: "ai",
    status: (r) => ((r.retry_count ?? 0) > 0 ? "active" : "skipped"),
    detail: (r) =>
      (r.retry_count ?? 0) > 0 ? `${r.retry_count} retries` : null,
  },
  {
    id: "mcp_tools",
    labelKey: "observability.pipeline.mcp_tools",
    fallbackLabel: "MCP Tool Loop",
    category: "tool",
    status: (r) => ((r.mcp_tool_call_count ?? 0) > 0 ? "active" : "skipped"),
    detail: (r) =>
      (r.mcp_tool_call_count ?? 0) > 0
        ? `${r.mcp_tool_call_count} calls · ${r.mcp_tool_loop_iterations ?? 0} iters`
        : null,
  },
  {
    id: "pii_ingress",
    labelKey: "observability.pipeline.pii_ingress",
    fallbackLabel: "PII Detokenize",
    category: "security",
    status: (r) => (r.stream ? "skipped" : "active"),
  },
  {
    id: "audit",
    labelKey: "observability.pipeline.audit",
    fallbackLabel: "Audit Log",
    category: "data",
    status: () => "active",
    detail: (r) => (r.finish_reason ? `finish: ${r.finish_reason}` : null),
  },
  {
    id: "response_out",
    labelKey: "observability.pipeline.response_out",
    fallbackLabel: "Response",
    category: "gateway",
    status: (r) => (r.status_code >= 400 ? "error" : "active"),
    detail: (r) => `${r.status_code}`,
  },
];

// ── Custom node component ────────────────────────────────────────────────────

interface PipelineNodeData {
  label: string;
  detail: string | null;
  icon: string;
  status: StageStatus;
  catH: number;
  catS: number;
  catL: number;
  isDark: boolean;
}

const PipelineNode = memo(({ data }: NodeProps<PipelineNodeData>) => {
  const { label, detail, icon, status, catH, catS, catL, isDark } = data;

  const skipped = status === "skipped";
  const errored = status === "error";

  // Colours
  const baseH = errored ? 0 : catH;
  const baseS = errored ? 75 : catS;
  const baseL = catL;

  const gradFrom = `hsla(${baseH}, ${baseS}%, ${isDark ? baseL - 8 : baseL + 8}%, ${skipped ? 0.12 : 0.85})`;
  const gradTo = `hsla(${baseH}, ${baseS}%, ${isDark ? baseL + 4 : baseL - 4}%, ${skipped ? 0.08 : 0.72})`;
  const borderColor = skipped
    ? `hsla(${baseH}, 10%, 50%, 0.25)`
    : `hsla(${baseH}, ${baseS}%, ${baseL + 12}%, 0.6)`;
  const glowColor = skipped
    ? "transparent"
    : `hsla(${baseH}, ${baseS}%, ${baseL}%, 0.25)`;
  const textColor = skipped ? `hsla(0, 0%, ${isDark ? 60 : 45}%, 1)` : "#fff";

  const style: CSSProperties = {
    background: `linear-gradient(135deg, ${gradFrom}, ${gradTo})`,
    border: `1.5px solid ${borderColor}`,
    borderRadius: 12,
    backdropFilter: "blur(8px)",
    boxShadow: skipped
      ? "none"
      : `0 2px 12px ${glowColor}, inset 0 1px 0 hsla(0,0%,100%,0.12)`,
    padding: "8px 10px",
    opacity: skipped ? 0.5 : 1,
    transition: "box-shadow 0.2s, opacity 0.2s",
    minWidth: 140,
    textAlign: "center" as const,
  };

  return (
    <div style={style}>
      <Handle
        type="target"
        position={Position.Left}
        style={{ opacity: 0, width: 6, height: 6 }}
      />
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          gap: 5,
        }}
      >
        <span style={{ fontSize: 14, lineHeight: 1 }}>{icon}</span>
        <span
          style={{
            fontWeight: 600,
            fontSize: 11.5,
            color: textColor,
            letterSpacing: "0.01em",
          }}
        >
          {label}
        </span>
      </div>
      {detail && (
        <div
          style={{
            fontSize: 9.5,
            color: textColor,
            opacity: 0.78,
            marginTop: 3,
            whiteSpace: "nowrap",
            overflow: "hidden",
            textOverflow: "ellipsis",
            maxWidth: 130,
            margin: "3px auto 0",
          }}
        >
          {detail}
        </div>
      )}
      <Handle
        type="source"
        position={Position.Right}
        style={{ opacity: 0, width: 6, height: 6 }}
      />
    </div>
  );
});

PipelineNode.displayName = "PipelineNode";

// ── Tool call child node ─────────────────────────────────────────────────────

interface ToolNodeData {
  name: string;
  detail: string;
  isError: boolean;
  isDark: boolean;
}

const ToolNode = memo(({ data }: NodeProps<ToolNodeData>) => {
  const { name, detail, isError, isDark } = data;
  const h = isError ? 0 : CATEGORY_COLOURS.tool.h;
  const s = isError ? 72 : CATEGORY_COLOURS.tool.s;
  const l = CATEGORY_COLOURS.tool.l;

  const style: CSSProperties = {
    background: `linear-gradient(135deg, hsla(${h},${s}%,${isDark ? l - 6 : l + 6}%,0.80), hsla(${h},${s}%,${l}%,0.60))`,
    border: `1px solid hsla(${h},${s}%,${l + 15}%,0.5)`,
    borderRadius: 10,
    backdropFilter: "blur(6px)",
    boxShadow: `0 1px 8px hsla(${h},${s}%,${l}%,0.2)`,
    padding: "5px 8px",
    textAlign: "center" as const,
    minWidth: 100,
  };

  return (
    <div style={style}>
      <Handle
        type="target"
        position={Position.Top}
        style={{ opacity: 0, width: 4, height: 4 }}
      />
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          gap: 4,
        }}
      >
        <span style={{ fontSize: 10, lineHeight: 1 }}>
          {isError ? "⚠" : "🔧"}
        </span>
        <span style={{ fontWeight: 600, fontSize: 10, color: "#fff" }}>
          {name}
        </span>
      </div>
      <div
        style={{ fontSize: 8.5, color: "#fff", opacity: 0.75, marginTop: 2 }}
      >
        {detail}
      </div>
    </div>
  );
});

ToolNode.displayName = "ToolNode";

const NODE_TYPES = { pipeline: PipelineNode, toolcall: ToolNode };

// ── Layout constants ─────────────────────────────────────────────────────────

const NODE_W = 156;
const NODE_H = 58;
const GAP_X = 32;
const GAP_Y = 82;
const COLS = 5;
const GRAPH_H = 350;

// ── Component ────────────────────────────────────────────────────────────────

interface Props {
  request: RequestLog;
  toolCalls?: ToolCallLog[];
}

export default function PipelineGraph({ request, toolCalls }: Props) {
  const { t } = useTranslation();
  const theme = useTheme();
  const isDark = theme.palette.mode === "dark";

  const { nodes, edges } = useMemo(() => {
    const builtNodes: Node[] = [];
    const builtEdges: Edge[] = [];

    STAGES.forEach((stage, i) => {
      const row = Math.floor(i / COLS);
      const colInRow = i % COLS;
      const col = row % 2 === 0 ? colInRow : COLS - 1 - colInRow;

      const status = stage.status(request);
      const detail = stage.detail?.(request) ?? null;
      const cat = CATEGORY_COLOURS[stage.category];
      const label = t(stage.labelKey, stage.fallbackLabel);

      builtNodes.push({
        id: stage.id,
        type: "pipeline",
        position: { x: col * (NODE_W + GAP_X), y: row * (NODE_H + GAP_Y) },
        data: {
          label,
          detail,
          icon: CATEGORY_ICONS[stage.category],
          status,
          catH: cat.h,
          catS: cat.s,
          catL: cat.l,
          isDark,
        } satisfies PipelineNodeData,
      });

      if (i > 0) {
        const prev = STAGES[i - 1];
        const prevStatus = prev.status(request);
        const edgeActive = status !== "skipped" && prevStatus !== "skipped";
        const edgeError = status === "error";

        builtEdges.push({
          id: `e-${prev.id}-${stage.id}`,
          source: prev.id,
          target: stage.id,
          type: "smoothstep",
          animated: edgeActive,
          style: {
            stroke: edgeError
              ? theme.palette.error.main
              : edgeActive
                ? alpha(theme.palette.primary.main, 0.6)
                : alpha(theme.palette.text.disabled, 0.3),
            strokeWidth: edgeActive ? 2 : 1.5,
          },
        });
      }
    });

    // MCP tool-call child nodes
    if (toolCalls && toolCalls.length > 0) {
      const mcpIdx = STAGES.findIndex((s) => s.id === "mcp_tools");
      const mcpRow = Math.floor(mcpIdx / COLS);
      const mcpColInRow = mcpIdx % COLS;
      const mcpCol = mcpRow % 2 === 0 ? mcpColInRow : COLS - 1 - mcpColInRow;
      const mcpX = mcpCol * (NODE_W + GAP_X);
      const mcpY = mcpRow * (NODE_H + GAP_Y);

      toolCalls.forEach((tc, j) => {
        const tcId = `tc-${j}`;
        const displayName =
          tc.tool_name.length > 16
            ? `${tc.tool_name.slice(0, 14)}…`
            : tc.tool_name;

        builtNodes.push({
          id: tcId,
          type: "toolcall",
          position: {
            x: mcpX - 40 + (j % 3) * (NODE_W * 0.72 + 6),
            y: mcpY + NODE_H + 24 + Math.floor(j / 3) * 48,
          },
          data: {
            name: displayName,
            detail: `${tc.latency_ms} ms${tc.mcp_server ? ` · ${tc.mcp_server}` : ""}`,
            isError: tc.is_error,
            isDark,
          } satisfies ToolNodeData,
        });

        builtEdges.push({
          id: `e-mcp-${tcId}`,
          source: "mcp_tools",
          target: tcId,
          type: "smoothstep",
          animated: true,
          style: {
            stroke: tc.is_error
              ? theme.palette.error.main
              : alpha(
                  CATEGORY_COLOURS.tool.h === 187
                    ? "#00acc1"
                    : theme.palette.secondary.main,
                  0.55,
                ),
            strokeWidth: 1.5,
            strokeDasharray: "4 2",
          },
        });
      });
    }

    return { nodes: builtNodes, edges: builtEdges };
  }, [request, toolCalls, theme, t, isDark]);

  return (
    <Box
      sx={{
        width: "100%",
        height: GRAPH_H,
        borderRadius: 2,
        overflow: "hidden",
        border: `1px solid ${theme.palette.divider}`,
        bgcolor: alpha(theme.palette.background.default, 0.5),
      }}
    >
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={NODE_TYPES}
        fitView
        fitViewOptions={{ padding: 0.18 }}
        panOnDrag
        zoomOnScroll
        preventScrolling={false}
        nodesDraggable={false}
        nodesConnectable={false}
        proOptions={{ hideAttribution: true }}
      >
        <Background
          gap={20}
          size={1}
          color={alpha(theme.palette.divider, 0.4)}
        />
      </ReactFlow>
    </Box>
  );
}
