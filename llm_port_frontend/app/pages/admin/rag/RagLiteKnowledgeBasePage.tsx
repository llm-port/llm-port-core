/**
 * RAG Lite Knowledge Base — unified tree view of collections and documents.
 * Supports hierarchical (nested) collections, drag-and-drop,
 * inline description editing, and AI summary generation.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Chip from "@mui/material/Chip";
import CircularProgress from "@mui/material/CircularProgress";
import Dialog from "@mui/material/Dialog";
import DialogActions from "@mui/material/DialogActions";
import DialogContent from "@mui/material/DialogContent";
import DialogTitle from "@mui/material/DialogTitle";
import IconButton from "@mui/material/IconButton";
import Paper from "@mui/material/Paper";
import Table from "@mui/material/Table";
import TableBody from "@mui/material/TableBody";
import TableCell from "@mui/material/TableCell";
import TableContainer from "@mui/material/TableContainer";
import TableHead from "@mui/material/TableHead";
import TableRow from "@mui/material/TableRow";
import TextField from "@mui/material/TextField";
import Tooltip from "@mui/material/Tooltip";
import Typography from "@mui/material/Typography";

import AutoAwesomeIcon from "@mui/icons-material/AutoAwesome";
import ChevronRightIcon from "@mui/icons-material/ChevronRight";
import CreateNewFolderIcon from "@mui/icons-material/CreateNewFolder";
import DeleteIcon from "@mui/icons-material/Delete";
import DescriptionIcon from "@mui/icons-material/Description";
import DragIndicatorIcon from "@mui/icons-material/DragIndicator";
import EditIcon from "@mui/icons-material/Edit";
import ExpandMoreIcon from "@mui/icons-material/ExpandMore";
import FolderIcon from "@mui/icons-material/Folder";
import FolderOpenIcon from "@mui/icons-material/FolderOpen";
import ReplayIcon from "@mui/icons-material/Replay";
import UploadFileIcon from "@mui/icons-material/UploadFile";

import {
  useReactTable,
  getCoreRowModel,
  getExpandedRowModel,
  flexRender,
  type ColumnDef,
  type ExpandedState,
  type Row,
} from "@tanstack/react-table";

import {
  DndContext,
  DragOverlay,
  closestCenter,
  PointerSensor,
  useSensor,
  useSensors,
  useDraggable,
  useDroppable,
  type DragStartEvent,
  type DragEndEvent,
} from "@dnd-kit/core";

import {
  ragLite,
  type RagLiteCollectionDTO,
  type RagLiteDocumentDTO,
} from "~/api/rag";

// ── Types ────────────────────────────────────────────────────────────────────

interface TreeNode {
  id: string;
  nodeType: "collection" | "document";
  name: string;
  description: string | null;
  parentId: string | null;
  documentCount?: number;
  docType?: string;
  sizeBytes?: number;
  chunkCount?: number;
  status?: string;
  collectionId?: string | null;
  createdAt: string;
  subRows?: TreeNode[];
}

// ── Helpers ──────────────────────────────────────────────────────────────────

function statusChipColor(
  s: string,
): "success" | "info" | "error" | "default" {
  switch (s) {
    case "ready":
      return "success";
    case "processing":
    case "pending":
      return "info";
    case "error":
      return "error";
    default:
      return "default";
  }
}

function fmtBytes(b: number): string {
  if (b < 1024) return `${b} B`;
  if (b < 1048576) return `${(b / 1024).toFixed(1)} KB`;
  return `${(b / 1048576).toFixed(1)} MB`;
}

/** Build a tree from flat collections + documents lists. */
function buildTree(
  collections: RagLiteCollectionDTO[],
  documents: RagLiteDocumentDTO[],
): TreeNode[] {
  const map = new Map<string, TreeNode>();
  for (const c of collections) {
    map.set(c.id, {
      id: c.id,
      nodeType: "collection",
      name: c.name,
      description: c.description,
      parentId: c.parent_id,
      documentCount: c.document_count,
      createdAt: c.created_at,
      subRows: [],
    });
  }

  const rootDocs: TreeNode[] = [];
  for (const d of documents) {
    const node: TreeNode = {
      id: d.id,
      nodeType: "document",
      name: d.filename,
      description: d.summary,
      parentId: null,
      docType: d.doc_type,
      sizeBytes: d.size_bytes,
      chunkCount: d.chunk_count,
      status: d.status,
      collectionId: d.collection_id,
      createdAt: d.created_at,
    };
    if (d.collection_id && map.has(d.collection_id)) {
      map.get(d.collection_id)!.subRows!.push(node);
    } else {
      rootDocs.push(node);
    }
  }

  const roots: TreeNode[] = [];
  for (const n of map.values()) {
    if (n.parentId && n.parentId !== n.id && map.has(n.parentId)) {
      map.get(n.parentId)!.subRows!.push(n);
    } else {
      roots.push(n);
    }
  }
  roots.push(...rootDocs);

  const sortFn = (nodes: TreeNode[]) => {
    nodes.sort((a, b) => {
      if (a.nodeType !== b.nodeType)
        return a.nodeType === "collection" ? -1 : 1;
      return a.name.localeCompare(b.name);
    });
    for (const n of nodes) if (n.subRows?.length) sortFn(n.subRows);
  };
  sortFn(roots);
  return roots;
}

// ── Inline Editable Description ──────────────────────────────────────────────

function DescriptionCell({
  value,
  nodeType,
  nodeId,
  onSave,
  onGenerateAI,
  generating,
}: {
  value: string | null;
  nodeType: "collection" | "document";
  nodeId: string;
  onSave: (id: string, nt: "collection" | "document", text: string) => void;
  onGenerateAI: (
    id: string,
    nt: "collection" | "document",
  ) => Promise<string>;
  generating: boolean;
}) {
  const { t } = useTranslation();
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(value ?? "");
  const inputRef = useRef<HTMLInputElement>(null);

  // Sync from prop only when NOT editing (preserves typed text).
  useEffect(() => {
    if (!editing) setText(value ?? "");
  }, [value, editing]);

  useEffect(() => {
    if (editing) inputRef.current?.focus();
  }, [editing]);

  function save() {
    setEditing(false);
    if (text !== (value ?? "")) onSave(nodeId, nodeType, text);
  }

  function onKey(e: React.KeyboardEvent) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      save();
    }
    if (e.key === "Escape") {
      setText(value ?? "");
      setEditing(false);
    }
  }

  async function handleAI(e: React.MouseEvent) {
    e.preventDefault(); // prevent blur on the TextField
    try {
      const summary = await onGenerateAI(nodeId, nodeType);
      setText(summary);
    } catch {
      /* error surfaced by parent */
    }
  }

  if (editing) {
    return (
      <Box
        sx={{ display: "flex", alignItems: "center", gap: 0.5, minWidth: 200 }}
      >
        <TextField
          inputRef={inputRef}
          size="small"
          fullWidth
          multiline
          maxRows={3}
          value={text}
          onChange={(e) => setText(e.target.value)}
          onBlur={save}
          onKeyDown={onKey}
          placeholder={t(
            "rag_lite.collection_description",
            "Description (optional)",
          )}
          sx={{ "& .MuiInputBase-input": { fontSize: "0.875rem" } }}
        />
        {!text.trim() && (
          <Tooltip title={t("rag_lite.generate_ai", "Generate with AI")}>
            <span>
              <IconButton
                size="small"
                color="secondary"
                disabled={generating}
                onMouseDown={handleAI}
              >
                {generating ? (
                  <CircularProgress size={16} />
                ) : (
                  <AutoAwesomeIcon fontSize="small" />
                )}
              </IconButton>
            </span>
          </Tooltip>
        )}
      </Box>
    );
  }

  return (
    <Box
      sx={{
        display: "flex",
        alignItems: "center",
        gap: 0.5,
        cursor: "pointer",
        minHeight: 28,
        "&:hover .edit-ico": { opacity: 1 },
      }}
      onClick={() => setEditing(true)}
    >
      <Typography
        variant="body2"
        color={value ? "text.primary" : "text.secondary"}
        noWrap
        sx={{ maxWidth: 300 }}
      >
        {value || t("rag_lite.no_description", "No description")}
      </Typography>
      <EditIcon
        className="edit-ico"
        sx={{ fontSize: 14, opacity: 0, transition: "opacity .15s" }}
      />
    </Box>
  );
}

// ── Tree Row (draggable + droppable) ─────────────────────────────────────────

function TreeRow({
  row,
  hoveredId,
  setHoveredId,
  onDelete,
  onRetry,
  onAddSub,
  onUploadTo,
  dragActiveId,
}: {
  row: Row<TreeNode>;
  hoveredId: string | null;
  setHoveredId: (id: string | null) => void;
  onDelete: (id: string, nt: "collection" | "document") => void;
  onRetry: (id: string) => void;
  onAddSub: (parentId: string) => void;
  onUploadTo: (colId: string) => void;
  dragActiveId: string | null;
}) {
  const { t } = useTranslation();
  const node = row.original;
  const isColl = node.nodeType === "collection";
  const hovered = hoveredId === node.id;

  const {
    setNodeRef,
    setActivatorNodeRef,
    attributes,
    listeners,
    isDragging,
  } = useDraggable({
    id: node.id,
    data: {
      nodeType: node.nodeType,
      parentId: node.parentId,
      collectionId: node.collectionId,
    },
  });

  const { setNodeRef: setDropRef, isOver } = useDroppable({
    id: `drop-${node.id}`,
    disabled: !isColl || dragActiveId === node.id,
  });

  const mergedRef = (el: HTMLTableRowElement | null) => {
    setNodeRef(el);
    if (isColl) setDropRef(el);
  };

  return (
    <TableRow
      ref={mergedRef}
      onMouseEnter={() => setHoveredId(node.id)}
      onMouseLeave={() => setHoveredId(null)}
      sx={{
        opacity: isDragging ? 0.35 : 1,
        backgroundColor: isOver ? "action.selected" : undefined,
        "&:hover": {
          backgroundColor: isOver ? "action.selected" : "action.hover",
        },
      }}
    >
      {/* Drag handle column */}
      <TableCell sx={{ py: 0.75, width: 28, px: 0.5 }}>
        <Box
          component="span"
          ref={setActivatorNodeRef}
          {...attributes}
          {...listeners}
          sx={{
            cursor: "grab",
            display: "inline-flex",
            verticalAlign: "middle",
            color: "text.disabled",
            "&:hover": { color: "text.primary" },
          }}
        >
          <DragIndicatorIcon sx={{ fontSize: 18 }} />
        </Box>
      </TableCell>

      {row.getVisibleCells().map((cell) => (
        <TableCell key={cell.id} sx={{ py: 0.75 }}>
          {flexRender(cell.column.columnDef.cell, cell.getContext())}
        </TableCell>
      ))}

      {/* Actions column */}
      <TableCell
        sx={{ py: 0.75, whiteSpace: "nowrap", width: 120, textAlign: "right" }}
      >
        {/* Contextual hover actions */}
        {hovered && !dragActiveId && (
          <>
            {isColl && (
              <>
                <Tooltip
                  title={t("rag_lite.add_subfolder", "Add Sub-Folder")}
                >
                  <IconButton size="small" onClick={() => onAddSub(node.id)}>
                    <CreateNewFolderIcon fontSize="small" />
                  </IconButton>
                </Tooltip>
                <Tooltip
                  title={t("rag_lite.upload_here", "Upload Here")}
                >
                  <IconButton
                    size="small"
                    onClick={() => onUploadTo(node.id)}
                  >
                    <UploadFileIcon fontSize="small" />
                  </IconButton>
                </Tooltip>
              </>
            )}
            {!isColl && node.status === "error" && (
              <Tooltip title={t("rag_lite.retry", "Retry")}>
                <IconButton
                  size="small"
                  color="primary"
                  onClick={() => onRetry(node.id)}
                >
                  <ReplayIcon fontSize="small" />
                </IconButton>
              </Tooltip>
            )}
            <Tooltip title={t("common.delete", "Delete")}>
              <IconButton
                size="small"
                color="error"
                onClick={() => onDelete(node.id, node.nodeType)}
              >
                <DeleteIcon fontSize="small" />
              </IconButton>
            </Tooltip>
          </>
        )}
      </TableCell>
    </TableRow>
  );
}

// ── Main Page ────────────────────────────────────────────────────────────────

export default function RagLiteKnowledgeBasePage() {
  const { t } = useTranslation();

  // Data
  const [collections, setCollections] = useState<RagLiteCollectionDTO[]>([]);
  const [documents, setDocuments] = useState<RagLiteDocumentDTO[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Table / interaction state
  const [expanded, setExpanded] = useState<ExpandedState>(true);
  const [hoveredId, setHoveredId] = useState<string | null>(null);
  const [dragActiveId, setDragActiveId] = useState<string | null>(null);
  const [generatingAI, setGeneratingAI] = useState<string | null>(null);

  // Create-collection dialog
  const [dlgOpen, setDlgOpen] = useState(false);
  const [dlgParentId, setDlgParentId] = useState<string | null>(null);
  const [dlgName, setDlgName] = useState("");
  const [dlgDesc, setDlgDesc] = useState("");
  const [creating, setCreating] = useState(false);

  // Upload
  const [uploading, setUploading] = useState(false);
  const [uploadColId, setUploadColId] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  // DnD
  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 8 } }),
  );

  // ── Data loading ──

  const load = useCallback(async () => {
    try {
      const [cols, docs] = await Promise.all([
        ragLite.listCollections(),
        ragLite.listAllDocuments(),
      ]);
      setCollections(cols);
      setDocuments(docs);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  // Auto-refresh while documents are being processed.
  useEffect(() => {
    const processing = documents.some(
      (d) => d.status === "processing" || d.status === "pending",
    );
    if (!processing) return;
    const id = setInterval(load, 5000);
    return () => clearInterval(id);
  }, [documents, load]);

  // ── Tree data ──

  const treeData = useMemo(
    () => buildTree(collections, documents),
    [collections, documents],
  );

  // ── Handlers ──

  async function handleDescSave(
    id: string,
    nt: "collection" | "document",
    text: string,
  ) {
    try {
      if (nt === "collection")
        await ragLite.updateCollection(id, { description: text || null });
      else await ragLite.updateDocumentSummary(id, text);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Save failed");
    }
  }

  async function handleGenAI(
    id: string,
    nt: "collection" | "document",
  ): Promise<string> {
    setGeneratingAI(id);
    try {
      const r =
        nt === "collection"
          ? await ragLite.generateCollectionSummary(id)
          : await ragLite.generateDocumentSummary(id);
      return r.summary;
    } catch (err) {
      setError(err instanceof Error ? err.message : "AI generation failed");
      throw err;
    } finally {
      setGeneratingAI(null);
    }
  }

  async function handleDelete(id: string, nt: "collection" | "document") {
    try {
      if (nt === "collection") await ragLite.deleteCollection(id);
      else await ragLite.deleteDocument(id);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Delete failed");
    }
  }

  async function handleRetry(id: string) {
    try {
      await ragLite.retryDocument(id);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Retry failed");
    }
  }

  function openCreateDlg(parentId: string | null) {
    setDlgParentId(parentId);
    setDlgOpen(true);
  }

  function triggerUpload(colId: string | null) {
    setUploadColId(colId);
    fileRef.current?.click();
  }

  async function handleUpload(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    setUploading(true);
    try {
      await ragLite.upload(file, uploadColId ?? undefined);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Upload failed");
    } finally {
      setUploading(false);
      setUploadColId(null);
      if (fileRef.current) fileRef.current.value = "";
    }
  }

  async function handleCreate() {
    if (!dlgName.trim()) return;
    setCreating(true);
    try {
      await ragLite.createCollection(
        dlgName.trim(),
        dlgDesc.trim() || null,
        dlgParentId,
      );
      setDlgOpen(false);
      setDlgName("");
      setDlgDesc("");
      setDlgParentId(null);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Create failed");
    } finally {
      setCreating(false);
    }
  }

  // ── DnD ──

  function onDragStart(e: DragStartEvent) {
    setDragActiveId(e.active.id as string);
  }

  async function onDragEnd(e: DragEndEvent) {
    setDragActiveId(null);
    const { active, over } = e;
    if (!over) return;
    const src = active.id as string;
    const tgt = String(over.id).replace("drop-", "");
    if (src === tgt) return;
    const data = active.data.current as
      | { nodeType: string }
      | undefined;
    if (!data) return;
    try {
      if (data.nodeType === "document")
        await ragLite.moveDocument(src, tgt);
      else await ragLite.updateCollection(src, { parent_id: tgt });
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Move failed");
    }
  }

  // ── Columns ──
  // Defined inline so closures stay fresh (table is small, no perf concern).

  const columns: ColumnDef<TreeNode>[] = [
    {
      id: "name",
      header: () => t("rag_lite.name", "Name"),
      cell: ({ row }) => {
        const n = row.original;
        const isColl = n.nodeType === "collection";
        const exp = row.getIsExpanded();
        const canExp = row.getCanExpand();
        return (
          <Box
            sx={{
              display: "flex",
              alignItems: "center",
              pl: row.depth * 3,
              gap: 0.5,
            }}
          >
            {canExp ? (
              <IconButton
                size="small"
                onClick={(e) => {
                  e.stopPropagation();
                  row.toggleExpanded();
                }}
                sx={{ p: 0.25 }}
              >
                {exp ? (
                  <ExpandMoreIcon fontSize="small" />
                ) : (
                  <ChevronRightIcon fontSize="small" />
                )}
              </IconButton>
            ) : (
              <Box sx={{ width: 28 }} />
            )}
            {isColl ? (
              exp ? (
                <FolderOpenIcon fontSize="small" color="primary" />
              ) : (
                <FolderIcon fontSize="small" color="primary" />
              )
            ) : (
              <DescriptionIcon fontSize="small" color="action" />
            )}
            <Typography
              variant="body2"
              sx={{ fontWeight: isColl ? 600 : 400, ml: 0.5 }}
            >
              {n.name}
            </Typography>
          </Box>
        );
      },
      size: 300,
    },
    {
      id: "description",
      header: () => t("rag_lite.description", "Description"),
      cell: ({ row }) => (
        <DescriptionCell
          value={row.original.description}
          nodeType={row.original.nodeType}
          nodeId={row.original.id}
          onSave={handleDescSave}
          onGenerateAI={handleGenAI}
          generating={generatingAI === row.original.id}
        />
      ),
      size: 350,
    },
    {
      id: "status",
      header: () => t("rag_lite.status", "Status"),
      cell: ({ row }) => {
        const n = row.original;
        if (n.nodeType === "collection")
          return (
            <Chip
              label={`${n.documentCount ?? 0} ${t("rag_lite.items", "items")}`}
              size="small"
              variant="outlined"
            />
          );
        return n.status ? (
          <Chip
            label={n.status}
            size="small"
            color={statusChipColor(n.status)}
          />
        ) : null;
      },
      size: 120,
    },
    {
      id: "size",
      header: () => t("rag_lite.size", "Size"),
      cell: ({ row }) => {
        const n = row.original;
        if (n.nodeType === "document" && n.sizeBytes != null)
          return (
            <Typography variant="body2">{fmtBytes(n.sizeBytes)}</Typography>
          );
        return null;
      },
      size: 100,
    },
  ];

  const table = useReactTable({
    data: treeData,
    columns,
    state: { expanded },
    onExpandedChange: setExpanded,
    getSubRows: (r) => r.subRows,
    getRowId: (r) => r.id,
    getCoreRowModel: getCoreRowModel(),
    getExpandedRowModel: getExpandedRowModel(),
  });

  // ── Render ────

  if (loading) {
    return (
      <Box sx={{ display: "flex", justifyContent: "center", py: 8 }}>
        <CircularProgress />
      </Box>
    );
  }

  // Resolved name for drag overlay.
  const draggedNode = dragActiveId
    ? (function find(nodes: TreeNode[]): TreeNode | null {
        for (const n of nodes) {
          if (n.id === dragActiveId) return n;
          if (n.subRows?.length) {
            const f = find(n.subRows);
            if (f) return f;
          }
        }
        return null;
      })(treeData)
    : null;

  return (
    <Box sx={{ p: 3 }}>
      {/* ── Toolbar ── */}
      <Box sx={{ display: "flex", alignItems: "center", mb: 2, gap: 2 }}>
        <Typography variant="h5">
          {t("rag_lite.knowledge_base_title", "Knowledge Base")}
        </Typography>
        <Box sx={{ flex: 1 }} />
        <input
          type="file"
          ref={fileRef}
          style={{ display: "none" }}
          onChange={handleUpload}
        />
        <Button
          variant="outlined"
          startIcon={<CreateNewFolderIcon />}
          onClick={() => openCreateDlg(null)}
        >
          {t("rag_lite.new_collection", "New Collection")}
        </Button>
        <Button
          variant="contained"
          startIcon={
            uploading ? <CircularProgress size={18} /> : <UploadFileIcon />
          }
          onClick={() => triggerUpload(null)}
          disabled={uploading}
        >
          {t("rag_lite.upload", "Upload")}
        </Button>
      </Box>

      {error && (
        <Alert severity="error" sx={{ mb: 2 }} onClose={() => setError(null)}>
          {error}
        </Alert>
      )}

      {/* ── Tree Table ── */}
      <DndContext
        sensors={sensors}
        collisionDetection={closestCenter}
        onDragStart={onDragStart}
        onDragEnd={onDragEnd}
      >
        <TableContainer
          component={Paper}
          sx={{ maxHeight: "calc(100vh - 200px)" }}
        >
          <Table size="small" stickyHeader>
            <TableHead>
              {table.getHeaderGroups().map((hg) => (
                <TableRow key={hg.id}>
                  <TableCell sx={{ width: 28, px: 0.5 }} />
                  {hg.headers.map((h) => (
                    <TableCell
                      key={h.id}
                      sx={{ fontWeight: 600, width: h.getSize() }}
                    >
                      {flexRender(
                        h.column.columnDef.header,
                        h.getContext(),
                      )}
                    </TableCell>
                  ))}
                  <TableCell sx={{ width: 120 }} />
                </TableRow>
              ))}
            </TableHead>
            <TableBody>
              {table.getRowModel().rows.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={columns.length + 2} align="center">
                    <Typography color="text.secondary" sx={{ py: 6 }}>
                      {t(
                        "rag_lite.empty_explorer",
                        "No collections or documents yet. Create a collection or upload a file to get started.",
                      )}
                    </Typography>
                  </TableCell>
                </TableRow>
              ) : (
                table.getRowModel().rows.map((row) => (
                  <TreeRow
                    key={row.id}
                    row={row}
                    hoveredId={hoveredId}
                    setHoveredId={setHoveredId}
                    onDelete={handleDelete}
                    onRetry={handleRetry}
                    onAddSub={openCreateDlg}
                    onUploadTo={triggerUpload}
                    dragActiveId={dragActiveId}
                  />
                ))
              )}
            </TableBody>
          </Table>
        </TableContainer>

        <DragOverlay>
          {draggedNode && (
            <Paper
              sx={{
                px: 2,
                py: 1,
                display: "flex",
                alignItems: "center",
                gap: 1,
              }}
            >
              {draggedNode.nodeType === "collection" ? (
                <FolderIcon fontSize="small" color="primary" />
              ) : (
                <DescriptionIcon fontSize="small" />
              )}
              <Typography variant="body2">{draggedNode.name}</Typography>
            </Paper>
          )}
        </DragOverlay>
      </DndContext>

      {/* ── Create Collection Dialog ── */}
      <Dialog
        open={dlgOpen}
        onClose={() => setDlgOpen(false)}
        maxWidth="sm"
        fullWidth
      >
        <DialogTitle>
          {dlgParentId
            ? t("rag_lite.add_subfolder", "Add Sub-Folder")
            : t("rag_lite.new_collection", "New Collection")}
        </DialogTitle>
        <DialogContent>
          <TextField
            autoFocus
            fullWidth
            label={t("rag_lite.collection_name", "Collection Name")}
            value={dlgName}
            onChange={(e) => setDlgName(e.target.value)}
            sx={{ mt: 1, mb: 2 }}
          />
          <TextField
            fullWidth
            multiline
            rows={2}
            label={t(
              "rag_lite.collection_description",
              "Description (optional)",
            )}
            value={dlgDesc}
            onChange={(e) => setDlgDesc(e.target.value)}
          />
        </DialogContent>
        <DialogActions>
          <Button
            onClick={() => {
              setDlgOpen(false);
              setDlgParentId(null);
            }}
          >
            {t("common.cancel", "Cancel")}
          </Button>
          <Button
            variant="contained"
            onClick={handleCreate}
            disabled={creating || !dlgName.trim()}
          >
            {creating ? (
              <CircularProgress size={18} />
            ) : (
              t("common.create", "Create")
            )}
          </Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
}
