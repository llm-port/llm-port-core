import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import ReactFlow, {
  Background,
  Controls,
  MiniMap,
  type Edge,
  type Node,
  type NodeMouseHandler,
  MarkerType,
} from "reactflow";

import {
  createColumnHelper,
  flexRender,
  getCoreRowModel,
  useReactTable,
} from "@tanstack/react-table";

import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Chip from "@mui/material/Chip";
import CloseIcon from "@mui/icons-material/Close";
import FormControl from "@mui/material/FormControl";
import IconButton from "@mui/material/IconButton";
import InputLabel from "@mui/material/InputLabel";
import MenuItem from "@mui/material/MenuItem";
import Paper from "@mui/material/Paper";
import Select from "@mui/material/Select";
import Stack from "@mui/material/Stack";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";

import {
  getLlmGraphTopology,
  getLlmGraphTraces,
  openLlmGraphTraceStream,
  type GraphEdge,
  type GraphNode,
  type TraceEvent,
} from "~/api/llmGraph";

/* ------------------------------------------------------------------ */
/*  Constants                                                          */
/* ------------------------------------------------------------------ */

const TRACE_FETCH_LIMIT = 100;
const MAX_TRACE_ROWS = 2000;
const FLUSH_INTERVAL_MS = 200;
const ROW_HEIGHT = 32;

/* ------------------------------------------------------------------ */
/*  Types                                                              */
/* ------------------------------------------------------------------ */

interface SelectedNode {
  id: string;
  label: string;
  type: string;
  status?: string;
  meta?: Record<string, unknown>;
}

/* ------------------------------------------------------------------ */
/*  Trace table column definitions                                     */
/* ------------------------------------------------------------------ */

const colHelper = createColumnHelper<TraceEvent>();

const traceColumns = [
  colHelper.accessor("status", {
    header: "Status",
    size: 56,
    cell: (info) => {
      const v = info.getValue();
      return (
        <Chip
          size="small"
          label={v}
          color={v >= 400 ? "error" : "success"}
          sx={{ height: 20, fontSize: "0.7rem", minWidth: 42 }}
        />
      );
    },
  }),
  colHelper.accessor("model_alias", {
    header: "Model",
    size: 130,
    cell: (info) => info.getValue() ?? "—",
  }),
  colHelper.accessor("latency_ms", {
    header: "Latency",
    size: 70,
    cell: (info) => `${info.getValue()}ms`,
  }),
  colHelper.accessor("total_tokens", {
    header: "Tokens",
    size: 60,
    cell: (info) => info.getValue() ?? "—",
  }),
  colHelper.accessor("ts", {
    header: "Time",
    size: 80,
    cell: (info) => {
      const d = new Date(info.getValue());
      return d.toLocaleTimeString();
    },
  }),
  colHelper.accessor("error_code", {
    header: "Error",
    size: 70,
    cell: (info) => info.getValue() ?? "",
  }),
];

export default function GraphPage() {
  const { t } = useTranslation();

  /* ---- topology state ------------------------------------------- */
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState("all");
  const [baseNodes, setBaseNodes] = useState<Node[]>([]);
  const [baseEdges, setBaseEdges] = useState<Edge[]>([]);

  /* ---- node-details overlay -------------------------------------- */
  const [selectedNode, setSelectedNode] = useState<SelectedNode | null>(null);

  /* ---- live traces state ----------------------------------------- */
  const [traceRows, setTraceRows] = useState<TraceEvent[]>([]);
  const [streamPaused, setStreamPaused] = useState(false);
  const queueRef = useRef<TraceEvent[]>([]);
  const seenEventIdsRef = useRef<Set<number>>(new Set());
  const lastEventIdRef = useRef<number | undefined>(undefined);
  const eventSourceRef = useRef<EventSource | null>(null);
  const reconnectTimerRef = useRef<number | null>(null);
  const reconnectAttemptRef = useRef(0);
  const tableContainerRef = useRef<HTMLDivElement | null>(null);
  const autoScrollRef = useRef(true);

  /* ================================================================ */
  /*  Topology helpers                                                 */
  /* ================================================================ */

  const toFlowNodes = useCallback(
    (nodes: GraphNode[], edges: GraphEdge[]): Node[] => {
      const groupWidth = 340;
      const rowHeight = 120;
      const groupOrder: Record<string, number> = {
        provider: 0,
        runtime: 1,
        model: 2,
      };

      const parentOf = new Map<string, string>();
      for (const edge of edges) {
        parentOf.set(edge.target, edge.source);
      }

      const groups: Record<string, GraphNode[]> = {};
      for (const node of nodes) {
        const g = groups[node.type] ?? [];
        g.push(node);
        groups[node.type] = g;
      }

      const nodeRow = new Map<string, number>();

      (groups["provider"] ?? []).forEach((p, i) => nodeRow.set(p.id, i));

      const rtCounters: Record<string, number> = {};
      for (const rt of groups["runtime"] ?? []) {
        const pid = parentOf.get(rt.id);
        const pRow = pid !== undefined ? (nodeRow.get(pid) ?? 0) : 0;
        const key = pid ?? "__orphan__";
        const off = rtCounters[key] ?? 0;
        nodeRow.set(rt.id, pRow + off);
        rtCounters[key] = off + 1;
      }

      const mCounters: Record<string, number> = {};
      for (const m of groups["model"] ?? []) {
        const pid = parentOf.get(m.id);
        const pRow = pid !== undefined ? (nodeRow.get(pid) ?? 0) : 0;
        const key = pid ?? "__orphan__";
        const off = mCounters[key] ?? 0;
        nodeRow.set(m.id, pRow + off);
        mCounters[key] = off + 1;
      }

      return nodes.map((node) => {
        const col = groupOrder[node.type] ?? 3;
        const row = nodeRow.get(node.id) ?? 0;
        const typeLabel =
          node.type.charAt(0).toUpperCase() + node.type.slice(1);
        return {
          id: node.id,
          type: "default",
          position: { x: col * groupWidth, y: row * rowHeight },
          data: {
            label: `[${typeLabel}] ${node.label}`,
            type: node.type,
            status: node.status,
            meta: node.meta ?? {},
          },
          draggable: false,
          selectable: true,
          style: nodeStyle(node.type, node.status ?? undefined),
        };
      });
    },
    [],
  );

  const toFlowEdge = useCallback(
    (edge: GraphEdge): Edge => ({
      id: edge.id,
      source: edge.source,
      target: edge.target,
      type: "smoothstep",
      markerEnd: { type: MarkerType.ArrowClosed, width: 16, height: 16 },
      style: { strokeWidth: 1.2 },
    }),
    [],
  );

  /* ================================================================ */
  /*  Data loading                                                     */
  /* ================================================================ */

  const loadTopology = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const topo = await getLlmGraphTopology();
      setBaseNodes(toFlowNodes(topo.nodes, topo.edges));
      setBaseEdges(topo.edges.map(toFlowEdge));
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : t("llm_graph.failed_load"));
    } finally {
      setLoading(false);
    }
  }, [t, toFlowEdge, toFlowNodes]);

  /* ================================================================ */
  /*  Live trace streaming (→ table rows, NOT graph nodes)             */
  /* ================================================================ */

  const closeStream = useCallback(() => {
    if (eventSourceRef.current) {
      eventSourceRef.current.close();
      eventSourceRef.current = null;
    }
    if (reconnectTimerRef.current !== null) {
      window.clearTimeout(reconnectTimerRef.current);
      reconnectTimerRef.current = null;
    }
  }, []);

  const connectStream = useCallback(() => {
    if (streamPaused) return;
    closeStream();
    const source = openLlmGraphTraceStream(
      (event) => {
        reconnectAttemptRef.current = 0;
        queueRef.current.push(event);
        lastEventIdRef.current = event.event_id;
      },
      () => {
        closeStream();
        reconnectAttemptRef.current += 1;
        const delay = Math.min(10_000, 1000 * 2 ** reconnectAttemptRef.current);
        reconnectTimerRef.current = window.setTimeout(connectStream, delay);
      },
      lastEventIdRef.current,
    );
    eventSourceRef.current = source;
  }, [closeStream, streamPaused]);

  /* seed initial traces then open SSE */
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const snap = await getLlmGraphTraces(TRACE_FETCH_LIMIT);
        if (cancelled) return;
        const fresh: TraceEvent[] = [];
        for (const item of snap.items) {
          if (!seenEventIdsRef.current.has(item.event_id)) {
            seenEventIdsRef.current.add(item.event_id);
            fresh.push(item);
          }
        }
        if (snap.items.length > 0) {
          lastEventIdRef.current = snap.items[snap.items.length - 1].event_id;
        }
        setTraceRows((prev) => [...prev, ...fresh].slice(-MAX_TRACE_ROWS));
      } catch {
        /* ignore seed errors silently; SSE will retry */
      }
      if (!cancelled) connectStream();
    })();
    return () => {
      cancelled = true;
      closeStream();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  /* flush queued SSE events into traceRows periodically */
  useEffect(() => {
    const handle = window.setInterval(() => {
      if (queueRef.current.length === 0) return;
      const batch = queueRef.current.splice(0, queueRef.current.length);
      const fresh: TraceEvent[] = [];
      for (const ev of batch) {
        if (!seenEventIdsRef.current.has(ev.event_id)) {
          seenEventIdsRef.current.add(ev.event_id);
          fresh.push(ev);
        }
      }
      if (fresh.length === 0) return;
      setTraceRows((prev) => [...prev, ...fresh].slice(-MAX_TRACE_ROWS));

      /* auto-scroll to bottom */
      if (autoScrollRef.current && tableContainerRef.current) {
        requestAnimationFrame(() => {
          tableContainerRef.current?.scrollTo({
            top: tableContainerRef.current.scrollHeight,
          });
        });
      }
    }, FLUSH_INTERVAL_MS);
    return () => window.clearInterval(handle);
  }, []);

  /* reconnect when pause toggled off */
  useEffect(() => {
    if (!streamPaused) connectStream();
    else closeStream();
    return () => closeStream();
  }, [streamPaused, connectStream, closeStream]);

  /* load topology on mount */
  useEffect(() => {
    void loadTopology();
  }, [loadTopology]);

  /* clean up on unmount */
  useEffect(() => () => closeStream(), [closeStream]);

  /* ================================================================ */
  /*  Filtered topology                                                */
  /* ================================================================ */

  const visibleNodes = useMemo(() => {
    const needle = search.trim().toLowerCase();
    return baseNodes.filter((node) => {
      const label = String(
        (node.data as { label?: string })?.label ?? "",
      ).toLowerCase();
      const status = String((node.data as { status?: string })?.status ?? "");
      const statusOk = statusFilter === "all" || status === statusFilter;
      const searchOk =
        needle.length === 0 ||
        label.includes(needle) ||
        node.id.toLowerCase().includes(needle);
      return statusOk && searchOk;
    });
  }, [baseNodes, search, statusFilter]);

  const visibleNodeIds = useMemo(
    () => new Set(visibleNodes.map((n) => n.id)),
    [visibleNodes],
  );

  const visibleEdges = useMemo(
    () =>
      baseEdges.filter(
        (e) => visibleNodeIds.has(e.source) && visibleNodeIds.has(e.target),
      ),
    [baseEdges, visibleNodeIds],
  );

  /* ================================================================ */
  /*  Node click handler                                               */
  /* ================================================================ */

  const onNodeClick: NodeMouseHandler = useCallback((_evt, node) => {
    const data = (node.data ?? {}) as {
      label?: string;
      type?: string;
      status?: string;
      meta?: Record<string, unknown>;
    };
    setSelectedNode({
      id: node.id,
      label: data.label ?? node.id,
      type: data.type ?? "unknown",
      status: data.status,
      meta: data.meta,
    });
  }, []);

  /* ================================================================ */
  /*  TanStack table instance                                          */
  /* ================================================================ */

  const table = useReactTable({
    data: traceRows,
    columns: traceColumns,
    getCoreRowModel: getCoreRowModel(),
  });

  const rows = table.getRowModel().rows;

  /* ================================================================ */
  /*  Render                                                           */
  /* ================================================================ */

  return (
    <Box
      sx={{
        display: "grid",
        gridTemplateColumns: "1fr 380px",
        gap: 1.5,
        height: "100%",
        minHeight: 0,
      }}
    >
      {/* ── Left: topology graph ─────────────────────────────────── */}
      <Stack spacing={1} sx={{ minWidth: 0, minHeight: 0, height: "100%" }}>
        {/* toolbar */}
        <Paper
          sx={{
            p: 1.5,
            display: "flex",
            alignItems: "center",
            gap: 1,
            flexWrap: "wrap",
          }}
        >
          <TextField
            size="small"
            label={t("llm_graph.search")}
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
          <FormControl size="small" sx={{ minWidth: 150 }}>
            <InputLabel>{t("llm_graph.status_filter")}</InputLabel>
            <Select
              label={t("llm_graph.status_filter")}
              value={statusFilter}
              onChange={(e) => setStatusFilter(e.target.value)}
            >
              <MenuItem value="all">{t("table.all")}</MenuItem>
              <MenuItem value="running">running</MenuItem>
              <MenuItem value="stopped">stopped</MenuItem>
              <MenuItem value="error">error</MenuItem>
              <MenuItem value="available">available</MenuItem>
            </Select>
          </FormControl>
          <Button size="small" onClick={() => void loadTopology()}>
            {t("dashboard.refresh")}
          </Button>
          <Chip
            size="small"
            label={`${t("llm_graph.nodes")}: ${visibleNodes.length}`}
          />
          <Chip
            size="small"
            label={`${t("llm_graph.edges")}: ${visibleEdges.length}`}
          />
        </Paper>

        {error && <Alert severity="error">{error}</Alert>}

        {/* graph canvas (position:relative for floating overlay) */}
        <Paper sx={{ flex: 1, minHeight: 0, position: "relative" }}>
          {loading ? (
            <Box
              sx={{
                height: "100%",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
              }}
            >
              <Typography color="text.secondary">
                {t("common.loading")}
              </Typography>
            </Box>
          ) : (
            <ReactFlow
              fitView
              nodes={visibleNodes}
              edges={visibleEdges}
              onNodeClick={onNodeClick}
              onPaneClick={() => setSelectedNode(null)}
              nodesDraggable={false}
              nodesConnectable={false}
              elementsSelectable
              panOnDrag
              zoomOnScroll
            >
              <MiniMap pannable zoomable />
              <Controls />
              <Background />
            </ReactFlow>
          )}

          {/* ── Floating node-details overlay (top-right) ──────── */}
          {selectedNode && (
            <Paper
              elevation={6}
              sx={{
                position: "absolute",
                top: 12,
                right: 12,
                width: 300,
                maxHeight: "60%",
                overflow: "auto",
                p: 2,
                zIndex: 10,
                borderRadius: 2,
                bgcolor: "background.paper",
              }}
            >
              <Stack
                direction="row"
                justifyContent="space-between"
                alignItems="center"
                sx={{ mb: 1 }}
              >
                <Typography variant="subtitle2">
                  {t("llm_graph.node_details")}
                </Typography>
                <IconButton size="small" onClick={() => setSelectedNode(null)}>
                  <CloseIcon fontSize="small" />
                </IconButton>
              </Stack>
              <Stack spacing={0.5}>
                <Typography variant="body2">
                  <strong>ID:</strong> {selectedNode.id}
                </Typography>
                <Typography variant="body2">
                  <strong>{t("common.name")}:</strong> {selectedNode.label}
                </Typography>
                <Typography variant="body2">
                  <strong>{t("llm_graph.type")}:</strong> {selectedNode.type}
                </Typography>
                {selectedNode.status && (
                  <Typography variant="body2">
                    <strong>{t("common.status")}:</strong> {selectedNode.status}
                  </Typography>
                )}
                <Typography
                  variant="body2"
                  sx={{
                    whiteSpace: "pre-wrap",
                    fontFamily: "monospace",
                    fontSize: "0.72rem",
                    mt: 0.5,
                  }}
                >
                  {JSON.stringify(selectedNode.meta ?? {}, null, 2)}
                </Typography>
              </Stack>
            </Paper>
          )}
        </Paper>
      </Stack>

      {/* ── Right: live trace table ──────────────────────────────── */}
      <Stack spacing={1} sx={{ minWidth: 0, minHeight: 0, height: "100%" }}>
        <Paper sx={{ p: 1, display: "flex", alignItems: "center", gap: 1 }}>
          <Typography variant="subtitle2" sx={{ flexGrow: 1 }}>
            {t("llm_graph.live_mode")}
          </Typography>
          <Chip
            size="small"
            label={traceRows.length}
            sx={{ fontSize: "0.7rem" }}
          />
          <Button
            size="small"
            variant="outlined"
            onClick={() => setStreamPaused((p) => !p)}
          >
            {streamPaused
              ? t("llm_graph.resume_stream")
              : t("llm_graph.pause_stream")}
          </Button>
        </Paper>

        <Paper
          ref={tableContainerRef}
          onScroll={() => {
            if (!tableContainerRef.current) return;
            const el = tableContainerRef.current;
            autoScrollRef.current =
              el.scrollHeight - el.scrollTop - el.clientHeight < 60;
          }}
          sx={{
            flex: 1,
            minHeight: 0,
            overflow: "auto",
            "& thead": {
              position: "sticky",
              top: 0,
              zIndex: 1,
              bgcolor: "background.paper",
            },
          }}
        >
          <table
            style={{
              width: "100%",
              borderCollapse: "collapse",
              fontSize: "0.75rem",
              tableLayout: "fixed",
            }}
          >
            <thead>
              {table.getHeaderGroups().map((hg) => (
                <tr key={hg.id}>
                  {hg.headers.map((header) => (
                    <th
                      key={header.id}
                      style={{
                        width: header.getSize(),
                        textAlign: "left",
                        padding: "6px 4px",
                        borderBottom: "2px solid #e0e0e0",
                        fontWeight: 600,
                        whiteSpace: "nowrap",
                      }}
                    >
                      {flexRender(
                        header.column.columnDef.header,
                        header.getContext(),
                      )}
                    </th>
                  ))}
                </tr>
              ))}
            </thead>
            <tbody>
              {rows.length === 0 ? (
                <tr>
                  <td
                    colSpan={traceColumns.length}
                    style={{ textAlign: "center", padding: 24, color: "#999" }}
                  >
                    Waiting for trace events…
                  </td>
                </tr>
              ) : (
                rows.map((row) => (
                  <tr
                    key={row.id}
                    style={{
                      height: ROW_HEIGHT,
                      borderBottom: "1px solid #f0f0f0",
                      cursor: "default",
                    }}
                  >
                    {row.getVisibleCells().map((cell) => (
                      <td
                        key={cell.id}
                        style={{
                          padding: "2px 4px",
                          overflow: "hidden",
                          textOverflow: "ellipsis",
                          whiteSpace: "nowrap",
                        }}
                      >
                        {flexRender(
                          cell.column.columnDef.cell,
                          cell.getContext(),
                        )}
                      </td>
                    ))}
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </Paper>
      </Stack>
    </Box>
  );
}

/* ================================================================== */
/*  Style helpers                                                      */
/* ================================================================== */

function nodeStyle(
  type: string,
  status?: string,
): Record<string, string | number> {
  const palette: Record<string, string> = {
    provider: "#0D47A1",
    runtime: "#1B5E20",
    model: "#4A148C",
    default: "#37474F",
  };
  const bgTint: Record<string, string> = {
    provider: "#E3F2FD",
    runtime: "#E8F5E9",
    model: "#F3E5F5",
  };
  const border =
    status === "error" ? "#D32F2F" : (palette[type] ?? palette.default);
  return {
    border: `2px solid ${border}`,
    borderRadius: 10,
    fontSize: 12,
    padding: 8,
    minWidth: 140,
    background: bgTint[type] ?? "#fff",
  };
}
