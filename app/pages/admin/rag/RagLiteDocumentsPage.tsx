/**
 * RAG Lite Documents page — list, upload, and delete documents
 * in the lightweight embedded RAG pipeline.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Chip from "@mui/material/Chip";
import CircularProgress from "@mui/material/CircularProgress";
import IconButton from "@mui/material/IconButton";
import Paper from "@mui/material/Paper";
import Table from "@mui/material/Table";
import TableBody from "@mui/material/TableBody";
import TableCell from "@mui/material/TableCell";
import TableContainer from "@mui/material/TableContainer";
import TableHead from "@mui/material/TableHead";
import TableRow from "@mui/material/TableRow";
import Typography from "@mui/material/Typography";

import DeleteIcon from "@mui/icons-material/Delete";
import UploadFileIcon from "@mui/icons-material/UploadFile";

import { ragLite, type RagLiteDocumentDTO } from "~/api/rag";

function statusColor(status: string) {
  switch (status) {
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

export default function RagLiteDocumentsPage() {
  const { t } = useTranslation();
  const [docs, setDocs] = useState<RagLiteDocumentDTO[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  const load = useCallback(async () => {
    try {
      const result = await ragLite.listDocuments();
      setDocs(result);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load documents");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  async function handleUpload(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    setUploading(true);
    try {
      await ragLite.upload(file);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Upload failed");
    } finally {
      setUploading(false);
      if (fileRef.current) fileRef.current.value = "";
    }
  }

  async function handleDelete(docId: string) {
    try {
      await ragLite.deleteDocument(docId);
      setDocs((prev) => prev.filter((d) => d.id !== docId));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Delete failed");
    }
  }

  if (loading) {
    return (
      <Box sx={{ display: "flex", justifyContent: "center", py: 8 }}>
        <CircularProgress />
      </Box>
    );
  }

  return (
    <Box sx={{ p: 3 }}>
      <Box sx={{ display: "flex", alignItems: "center", mb: 2, gap: 2 }}>
        <Typography variant="h5">
          {t("rag_lite.documents_title", "Documents")}
        </Typography>
        <Box sx={{ flex: 1 }} />
        <input
          type="file"
          ref={fileRef}
          style={{ display: "none" }}
          onChange={handleUpload}
        />
        <Button
          variant="contained"
          startIcon={
            uploading ? <CircularProgress size={18} /> : <UploadFileIcon />
          }
          onClick={() => fileRef.current?.click()}
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

      <TableContainer component={Paper}>
        <Table size="small">
          <TableHead>
            <TableRow>
              <TableCell>{t("rag_lite.filename", "Filename")}</TableCell>
              <TableCell>{t("rag_lite.type", "Type")}</TableCell>
              <TableCell align="right">{t("rag_lite.size", "Size")}</TableCell>
              <TableCell align="right">
                {t("rag_lite.chunks", "Chunks")}
              </TableCell>
              <TableCell>{t("rag_lite.status", "Status")}</TableCell>
              <TableCell>{t("rag_lite.created", "Created")}</TableCell>
              <TableCell />
            </TableRow>
          </TableHead>
          <TableBody>
            {docs.length === 0 ? (
              <TableRow>
                <TableCell colSpan={7} align="center">
                  <Typography color="text.secondary" sx={{ py: 4 }}>
                    {t("rag_lite.no_documents", "No documents uploaded yet.")}
                  </Typography>
                </TableCell>
              </TableRow>
            ) : (
              docs.map((doc) => (
                <TableRow key={doc.id}>
                  <TableCell>{doc.filename}</TableCell>
                  <TableCell>{doc.doc_type}</TableCell>
                  <TableCell align="right">
                    {(doc.size_bytes / 1024).toFixed(1)} KB
                  </TableCell>
                  <TableCell align="right">{doc.chunk_count}</TableCell>
                  <TableCell>
                    <Chip
                      label={doc.status}
                      size="small"
                      color={statusColor(doc.status) as any}
                    />
                  </TableCell>
                  <TableCell>
                    {new Date(doc.created_at).toLocaleString()}
                  </TableCell>
                  <TableCell>
                    <IconButton
                      size="small"
                      color="error"
                      onClick={() => handleDelete(doc.id)}
                    >
                      <DeleteIcon fontSize="small" />
                    </IconButton>
                  </TableCell>
                </TableRow>
              ))
            )}
          </TableBody>
        </Table>
      </TableContainer>
    </Box>
  );
}
