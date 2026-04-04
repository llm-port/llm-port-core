/**
 * DataTable — reusable MUI data table powered by TanStack Table with:
 *  - global text search (matches against `searchValue()` per column)
 *  - click-to-sort on any column marked `sortable: true`
 *  - declarative column filter dropdowns rendered in the toolbar
 *  - loading, error, and empty states
 *  - sticky header + scrollable body that fits into flex containers
 *  - optional client-side pagination
 */
import { useState, useMemo, useEffect, useRef, useCallback } from "react";
import {
  useReactTable,
  getCoreRowModel,
  getSortedRowModel,
  getFilteredRowModel,
  getPaginationRowModel,
  flexRender,
  type SortingState,
  type ColumnDef as TanColumnDef,
  type ColumnSizingState,
} from "@tanstack/react-table";

import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Checkbox from "@mui/material/Checkbox";
import CircularProgress from "@mui/material/CircularProgress";
import FormControl from "@mui/material/FormControl";
import IconButton from "@mui/material/IconButton";
import InputAdornment from "@mui/material/InputAdornment";
import InputLabel from "@mui/material/InputLabel";
import ListItemText from "@mui/material/ListItemText";
import MenuItem from "@mui/material/MenuItem";
import Paper from "@mui/material/Paper";
import Select from "@mui/material/Select";
import Stack from "@mui/material/Stack";
import Table from "@mui/material/Table";
import TableBody from "@mui/material/TableBody";
import TableCell from "@mui/material/TableCell";
import TableContainer from "@mui/material/TableContainer";
import TableHead from "@mui/material/TableHead";
import TablePagination from "@mui/material/TablePagination";
import TableRow from "@mui/material/TableRow";
import TableSortLabel from "@mui/material/TableSortLabel";
import TextField from "@mui/material/TextField";
import Tooltip from "@mui/material/Tooltip";
import Typography from "@mui/material/Typography";

import ClearIcon from "@mui/icons-material/Clear";
import RefreshIcon from "@mui/icons-material/Refresh";
import SearchIcon from "@mui/icons-material/Search";
import ViewColumnIcon from "@mui/icons-material/ViewColumn";

// ─────────────────────────────────────────────────────────────────────────────
// Types
// ─────────────────────────────────────────────────────────────────────────────

/** A single column definition. */
export interface ColumnDef<T> {
  /** Unique key — used as sort key and React key. */
  key: string;
  /** Header label. */
  label: string;
  /** If true, clicking the header toggles ascending/descending sort. */
  sortable?: boolean;
  /** Text alignment for both header and cells. Defaults to "left". */
  align?: "left" | "center" | "right";
  /** Renders the cell content for a given row. */
  render: (row: T) => React.ReactNode;
  /**
   * Returns a string used by the global search filter.
   * If omitted the column is excluded from search.
   */
  searchValue?: (row: T) => string;
  /**
   * Returns a comparable primitive used by client-side sorting.
   * Required when `sortable: true`.
   */
  sortValue?: (row: T) => string | number;
  /** Optional min-width hint passed to the <TableCell>. */
  minWidth?: number | string;
  /** Optional explicit width passed as TanStack column `size`. */
  width?: number;
  /**
   * Whether the user can hide this column via the column-visibility toggle.
   * Defaults to `true`. Set to `false` for columns that must always be visible
   * (e.g. actions).
   */
  hideable?: boolean;
}

/** A declarative column-filter dropdown shown in the toolbar. */
export interface ColumnFilter {
  /** Visible label on the Select. */
  label: string;
  /** Current selected value for single-select (empty string = "all"). */
  value: string;
  /** Available options. */
  options: { value: string; label: string }[];
  onChange?: (value: string) => void;
  minWidth?: number;
  /** Enable multi-select with checkboxes. */
  multi?: boolean;
  /** Current selected values for multi-select (empty array = all). */
  multiValue?: string[];
  onMultiChange?: (values: string[]) => void;
}

export interface DataTableProps<T> {
  /** Column definitions. */
  columns: ColumnDef<T>[];
  /** Source data. */
  rows: T[];
  /** Must return a unique stable string for each row (used as React key). */
  rowKey: (row: T) => string;
  /** While true, a centred spinner replaces the table body. */
  loading?: boolean;
  /** Non-null shows a red error banner above the table. */
  error?: string | null;
  /** Text shown when the filtered result set is empty. */
  emptyMessage?: string;
  /** Title displayed on the left side of the toolbar. */
  title?: React.ReactNode;
  /** Additional controls rendered on the right side of the toolbar (buttons, etc.). */
  toolbarActions?: React.ReactNode;
  /** Column-filter dropdowns rendered between the search box and actions. */
  columnFilters?: ColumnFilter[];
  /** Called when the user clicks the refresh icon. */
  onRefresh?: () => void;
  /** Placeholder text for the search input. */
  searchPlaceholder?: string;
  /** Row click handler. */
  onRowClick?: (row: T) => void;
  /**
   * Enable client-side pagination.
   * Pass a number to set the initial page size, or true for a default of 25.
   */
  pagination?: boolean | number;
  /** Available page size options. Defaults to [10, 25, 50, 100]. */
  pageSizeOptions?: number[];
  /**
   * When set, the row whose `rowKey` matches this value receives a
   * highlighted background and is scrolled into view on mount.
   */
  highlightId?: string | null;
  /**
   * Unique localStorage key for persisting column visibility preferences.
   * When provided, a column-toggle button appears in the toolbar.
   * Example: `"dt-containers"` or `"dt-images"`.
   */
  columnVisibilityKey?: string;
  /** CSS table-layout. Defaults to `"fixed"`. Use `"auto"` when columns should size to content. */
  tableLayout?: "fixed" | "auto";
}

// ─────────────────────────────────────────────────────────────────────────────
// Component
// ─────────────────────────────────────────────────────────────────────────────

export function DataTable<T>({
  columns,
  rows,
  rowKey,
  loading = false,
  error = null,
  emptyMessage = "No data.",
  title,
  toolbarActions,
  columnFilters = [],
  onRefresh,
  searchPlaceholder = "Search…",
  onRowClick,
  pagination,
  pageSizeOptions = [10, 25, 50, 100],
  highlightId,
  columnVisibilityKey,
  tableLayout = "fixed",
}: DataTableProps<T>) {
  const [search, setSearch] = useState("");
  const [sorting, setSorting] = useState<SortingState>([]);
  const [columnSizing, setColumnSizing] = useState<ColumnSizingState>({});
  const highlightRef = useRef<HTMLTableRowElement>(null);
  const [colMenuAnchor, setColMenuAnchor] = useState<HTMLElement | null>(null);

  // ── Column visibility ──────────────────────────────────────────────────
  const loadHiddenCols = useCallback((): Set<string> => {
    if (!columnVisibilityKey) return new Set();
    try {
      const raw = localStorage.getItem(columnVisibilityKey);
      if (raw) return new Set(JSON.parse(raw) as string[]);
    } catch {
      /* ignore bad data */
    }
    return new Set();
  }, [columnVisibilityKey]);

  const [hiddenCols, setHiddenCols] = useState<Set<string>>(loadHiddenCols);

  const toggleColumn = useCallback(
    (key: string) => {
      setHiddenCols((prev) => {
        const next = new Set(prev);
        if (next.has(key)) next.delete(key);
        else next.add(key);
        if (columnVisibilityKey) {
          localStorage.setItem(columnVisibilityKey, JSON.stringify([...next]));
        }
        return next;
      });
    },
    [columnVisibilityKey],
  );

  // Columns that are actually rendered (respecting visibility)
  const visibleColumns = useMemo(
    () => columns.filter((col) => !hiddenCols.has(col.key)),
    [columns, hiddenCols],
  );

  // Columns the user can toggle (hideable !== false)
  const hideableColumns = useMemo(
    () => columns.filter((col) => col.hideable !== false),
    [columns],
  );

  // Scroll highlighted row into view on first render
  useEffect(() => {
    if (highlightId && highlightRef.current) {
      highlightRef.current.scrollIntoView({
        behavior: "smooth",
        block: "center",
      });
    }
  }, [highlightId, loading]);

  const isPaginated = pagination != null && pagination !== false;
  const initialPageSize = typeof pagination === "number" ? pagination : 25;

  // Map our ColumnDef<T> to TanStack column defs
  const tanColumns = useMemo<TanColumnDef<T, unknown>[]>(
    () =>
      visibleColumns.map((col) => ({
        id: col.key,
        header: col.label,
        accessorFn: (row: T) => col.sortValue?.(row) ?? "",
        cell: ({ row }) => col.render(row.original),
        enableSorting: col.sortable ?? false,
        ...(col.width != null ? { size: col.width } : {}),
        meta: { align: col.align, minWidth: col.minWidth },
      })),
    [visibleColumns],
  );

  // Global text filter
  const filteredRows = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return rows;
    return rows.filter((row) =>
      columns.some(
        (col) =>
          col.searchValue && col.searchValue(row).toLowerCase().includes(q),
      ),
    );
  }, [rows, search, columns]);

  const table = useReactTable<T>({
    data: filteredRows,
    columns: tanColumns,
    state: { sorting, columnSizing },
    onSortingChange: setSorting,
    onColumnSizingChange: setColumnSizing,
    columnResizeMode: "onChange",
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
    ...(isPaginated
      ? {
          getPaginationRowModel: getPaginationRowModel(),
          initialState: { pagination: { pageSize: initialPageSize } },
        }
      : {}),
    getRowId: (row) => rowKey(row),
  });

  const visibleRows = isPaginated
    ? table.getRowModel().rows
    : table.getSortedRowModel().rows;

  // ─────────────────────────────────────────────────────────────────────────
  return (
    <Box
      sx={{
        display: "flex",
        flexDirection: "column",
        height: "100%",
        overflow: "hidden",
      }}
    >
      {/* ── Toolbar ────────────────────────────────────────────────────────── */}
      <Box sx={{ flexShrink: 0, mb: 2 }}>
        <Stack
          direction="row"
          alignItems="center"
          justifyContent="space-between"
          flexWrap="wrap"
          gap={1}
        >
          {/* Left: title */}
          <Box sx={{ flexShrink: 0 }}>
            {typeof title === "string" ? (
              <Typography variant="h5">{title}</Typography>
            ) : (
              title
            )}
          </Box>

          {/* Right: search + filters + actions + refresh */}
          <Stack
            direction="row"
            spacing={1}
            alignItems="center"
            flexWrap="wrap"
            sx={{ pt: "6px" }}
          >
            {/* Global search */}
            <TextField
              size="small"
              placeholder={searchPlaceholder}
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              sx={{ width: 200 }}
              slotProps={{
                input: {
                  startAdornment: (
                    <InputAdornment position="start">
                      <SearchIcon
                        fontSize="small"
                        sx={{ color: "text.disabled" }}
                      />
                    </InputAdornment>
                  ),
                  endAdornment: search ? (
                    <InputAdornment position="end">
                      <IconButton
                        size="small"
                        onClick={() => setSearch("")}
                        edge="end"
                      >
                        <ClearIcon fontSize="small" />
                      </IconButton>
                    </InputAdornment>
                  ) : undefined,
                },
              }}
            />

            {/* Column filters */}
            {columnFilters.map((f) =>
              f.multi ? (
                <FormControl
                  key={f.label}
                  size="small"
                  sx={{ minWidth: f.minWidth ?? 160 }}
                >
                  <InputLabel>{f.label}</InputLabel>
                  <Select
                    multiple
                    value={f.multiValue ?? []}
                    label={f.label}
                    onChange={(e) => {
                      const val = e.target.value as string[];
                      f.onMultiChange?.(val);
                    }}
                    renderValue={(selected) => {
                      if (
                        !selected.length ||
                        selected.length === f.options.length
                      )
                        return "All";
                      if (selected.length <= 2) return selected.join(", ");
                      return `${selected.length} selected`;
                    }}
                  >
                    {f.options.map((opt) => (
                      <MenuItem key={opt.value} value={opt.value} dense>
                        <Checkbox
                          size="small"
                          checked={(f.multiValue ?? []).includes(opt.value)}
                          sx={{ py: 0 }}
                        />
                        <ListItemText
                          primary={opt.label}
                          primaryTypographyProps={{ fontSize: "0.85rem" }}
                        />
                      </MenuItem>
                    ))}
                  </Select>
                </FormControl>
              ) : (
                <FormControl
                  key={f.label}
                  size="small"
                  sx={{ minWidth: f.minWidth ?? 140 }}
                >
                  <InputLabel>{f.label}</InputLabel>
                  <Select
                    value={f.value}
                    label={f.label}
                    onChange={(e) => f.onChange?.(e.target.value)}
                  >
                    <MenuItem value="">All</MenuItem>
                    {f.options.map((opt) => (
                      <MenuItem key={opt.value} value={opt.value}>
                        {opt.label}
                      </MenuItem>
                    ))}
                  </Select>
                </FormControl>
              ),
            )}

            {/* Caller-supplied actions */}
            {toolbarActions}

            {/* Column visibility toggle */}
            {columnVisibilityKey && hideableColumns.length > 0 && (
              <>
                <Tooltip title="Toggle columns">
                  <IconButton
                    size="small"
                    onClick={(e) => setColMenuAnchor(e.currentTarget)}
                  >
                    <ViewColumnIcon />
                  </IconButton>
                </Tooltip>
                {colMenuAnchor && (
                  <Paper
                    elevation={8}
                    sx={{
                      position: "fixed",
                      zIndex: 1300,
                      top: colMenuAnchor.getBoundingClientRect().bottom + 4,
                      left: colMenuAnchor.getBoundingClientRect().left,
                      minWidth: 180,
                      maxHeight: 320,
                      overflow: "auto",
                      py: 0.5,
                    }}
                  >
                    {/* Click-away overlay */}
                    <Box
                      sx={{
                        position: "fixed",
                        inset: 0,
                        zIndex: -1,
                      }}
                      onClick={() => setColMenuAnchor(null)}
                    />
                    {hideableColumns.map((col) => (
                      <MenuItem
                        key={col.key}
                        dense
                        onClick={() => toggleColumn(col.key)}
                      >
                        <Checkbox
                          size="small"
                          checked={!hiddenCols.has(col.key)}
                          sx={{ py: 0 }}
                        />
                        <ListItemText
                          primary={col.label}
                          primaryTypographyProps={{ fontSize: "0.85rem" }}
                        />
                      </MenuItem>
                    ))}
                  </Paper>
                )}
              </>
            )}

            {/* Refresh */}
            {onRefresh && (
              <Tooltip title="Refresh">
                <IconButton size="small" onClick={onRefresh} disabled={loading}>
                  <RefreshIcon />
                </IconButton>
              </Tooltip>
            )}
          </Stack>
        </Stack>
      </Box>

      {/* ── Error ─────────────────────────────────────────────────────────── */}
      {error && (
        <Alert severity="error" sx={{ mb: 2, flexShrink: 0 }}>
          {error}
        </Alert>
      )}

      {/* ── Loading ───────────────────────────────────────────────────────── */}
      {loading && (
        <Box sx={{ display: "flex", justifyContent: "center", py: 6 }}>
          <CircularProgress size={32} />
        </Box>
      )}

      {/* ── Table ─────────────────────────────────────────────────────────── */}
      {!loading && (
        <TableContainer
          component={Paper}
          variant="outlined"
          sx={{ flexGrow: 1, overflow: "auto" }}
        >
          <Table
            size="small"
            stickyHeader
            style={{
              width: "100%",
              minWidth:
                tableLayout === "fixed"
                  ? table.getCenterTotalSize()
                  : undefined,
              tableLayout,
            }}
          >
            <TableHead>
              {table.getHeaderGroups().map((headerGroup) => (
                <TableRow key={headerGroup.id}>
                  {headerGroup.headers.map((header) => {
                    const meta = header.column.columnDef.meta as
                      | { align?: string; minWidth?: number | string }
                      | undefined;
                    const align = (meta?.align ?? "left") as
                      | "left"
                      | "center"
                      | "right";
                    const canSort = header.column.getCanSort();
                    const sorted = header.column.getIsSorted();
                    return (
                      <TableCell
                        key={header.id}
                        align={align}
                        sortDirection={sorted || false}
                        sx={{
                          width:
                            tableLayout === "fixed"
                              ? header.getSize()
                              : meta?.minWidth,
                          minWidth: meta?.minWidth,
                          userSelect: "none",
                          overflow: "hidden",
                          textOverflow: "ellipsis",
                          whiteSpace: "nowrap",
                          position: "relative",
                        }}
                      >
                        {canSort ? (
                          <TableSortLabel
                            active={!!sorted}
                            direction={sorted || "asc"}
                            onClick={header.column.getToggleSortingHandler()}
                          >
                            {flexRender(
                              header.column.columnDef.header,
                              header.getContext(),
                            )}
                          </TableSortLabel>
                        ) : (
                          flexRender(
                            header.column.columnDef.header,
                            header.getContext(),
                          )
                        )}
                        {/* Column resize handle */}
                        <Box
                          onMouseDown={header.getResizeHandler()}
                          onTouchStart={header.getResizeHandler()}
                          sx={{
                            position: "absolute",
                            right: -3,
                            top: 0,
                            height: "100%",
                            width: 8,
                            cursor: "col-resize",
                            zIndex: 1,
                            "&::after": {
                              content: '""',
                              position: "absolute",
                              top: "25%",
                              left: 3,
                              width: 2,
                              height: "50%",
                              borderRadius: 1,
                              bgcolor: header.column.getIsResizing()
                                ? "primary.main"
                                : "divider",
                              transition: "background-color 0.15s",
                            },
                            "&:hover::after": {
                              bgcolor: "primary.main",
                            },
                          }}
                        />
                      </TableCell>
                    );
                  })}
                </TableRow>
              ))}
            </TableHead>
            <TableBody>
              {visibleRows.length === 0 && (
                <TableRow>
                  <TableCell
                    colSpan={visibleColumns.length}
                    align="center"
                    sx={{ py: 4, color: "text.secondary" }}
                  >
                    {emptyMessage}
                  </TableCell>
                </TableRow>
              )}
              {visibleRows.map((row) => {
                const isHighlighted =
                  highlightId != null && row.id === highlightId;
                return (
                  <TableRow
                    key={row.id}
                    ref={isHighlighted ? highlightRef : undefined}
                    hover
                    onClick={
                      onRowClick ? () => onRowClick(row.original) : undefined
                    }
                    sx={{
                      ...(onRowClick ? { cursor: "pointer" } : {}),
                      ...(isHighlighted
                        ? {
                            bgcolor: "action.selected",
                            animation: "highlight-fade 3s ease-out",
                            "@keyframes highlight-fade": {
                              "0%": { bgcolor: "primary.dark" },
                              "100%": { bgcolor: "transparent" },
                            },
                          }
                        : {}),
                    }}
                  >
                    {row.getVisibleCells().map((cell) => {
                      const meta = cell.column.columnDef.meta as
                        | { align?: string }
                        | undefined;
                      return (
                        <TableCell
                          key={cell.id}
                          align={
                            (meta?.align ?? "left") as
                              | "left"
                              | "center"
                              | "right"
                          }
                          style={
                            tableLayout === "fixed"
                              ? { width: cell.column.getSize() }
                              : undefined
                          }
                          sx={{
                            maxWidth:
                              tableLayout === "fixed"
                                ? cell.column.getSize()
                                : undefined,
                            overflow: "hidden",
                            whiteSpace: "normal",
                            overflowWrap: "anywhere",
                            wordBreak: "break-word",
                          }}
                        >
                          {flexRender(
                            cell.column.columnDef.cell,
                            cell.getContext(),
                          )}
                        </TableCell>
                      );
                    })}
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        </TableContainer>
      )}

      {/* ── Pagination ─────────────────────────────────────────────────────── */}
      {!loading && isPaginated && (
        <TablePagination
          component="div"
          count={table.getFilteredRowModel().rows.length}
          page={table.getState().pagination.pageIndex}
          onPageChange={(_, page) => table.setPageIndex(page)}
          rowsPerPage={table.getState().pagination.pageSize}
          onRowsPerPageChange={(e) => table.setPageSize(Number(e.target.value))}
          rowsPerPageOptions={pageSizeOptions}
          sx={{ flexShrink: 0, borderTop: 1, borderColor: "divider" }}
        />
      )}
    </Box>
  );
}
