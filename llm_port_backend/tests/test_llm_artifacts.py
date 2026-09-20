"""Unit tests for services.llm.artifacts (WI-2)."""

import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from llm_port_backend.services.llm.artifacts import (
    build_cache_manifest,
    build_model_sync_payload,
    manifest_digest,
    model_cache_dir,
    resolve_blob_hash,
)


def test_manifest_digest_deterministic() -> None:
    """Canonical digest must be invariant to dict ordering and whitespace."""
    m1 = {
        "blobs": [{"hash": "b2", "size": 200}, {"hash": "b1", "size": 100}],
        "refs": [{"name": "v1", "commit": "c1"}, {"name": "main", "commit": "c0"}],
        "snapshots": [
            {"commit": "c0", "links": [{"path": "config.json", "blob_hash": "b1"}]},
        ],
    }
    # Same content, different insertion order in lists and dicts
    m2 = {
        "snapshots": [
            {"links": [{"path": "config.json", "blob_hash": "b1"}], "commit": "c0"},
        ],
        "blobs": [{"size": 100, "hash": "b1"}, {"size": 200, "hash": "b2"}],
        "refs": [{"commit": "c0", "name": "main"}, {"commit": "c1", "name": "v1"}],
    }
    d1 = manifest_digest(m1)
    d2 = manifest_digest(m2)
    assert len(d1) == 64
    assert d1 == d2


def test_manifest_digest_sensitive_to_changes() -> None:
    """Digest must change if blob, ref, or snapshot content changes."""
    base = {
        "blobs": [{"hash": "b1", "size": 100}],
        "refs": [{"name": "main", "commit": "c0"}],
        "snapshots": [
            {"commit": "c0", "links": [{"path": "config.json", "blob_hash": "b1"}]},
        ],
    }
    base_digest = manifest_digest(base)

    # Blob hash changed
    m_blob = {
        "blobs": [{"hash": "b2", "size": 100}],
        "refs": [{"name": "main", "commit": "c0"}],
        "snapshots": [
            {"commit": "c0", "links": [{"path": "config.json", "blob_hash": "b1"}]},
        ],
    }
    assert manifest_digest(m_blob) != base_digest

    # Blob size changed
    m_size = {
        "blobs": [{"hash": "b1", "size": 101}],
        "refs": [{"name": "main", "commit": "c0"}],
        "snapshots": [
            {"commit": "c0", "links": [{"path": "config.json", "blob_hash": "b1"}]},
        ],
    }
    assert manifest_digest(m_size) != base_digest

    # Ref commit changed
    m_ref = {
        "blobs": [{"hash": "b1", "size": 100}],
        "refs": [{"name": "main", "commit": "c1"}],
        "snapshots": [
            {"commit": "c0", "links": [{"path": "config.json", "blob_hash": "b1"}]},
        ],
    }
    assert manifest_digest(m_ref) != base_digest

    # Snapshot link changed
    m_snap = {
        "blobs": [{"hash": "b1", "size": 100}],
        "refs": [{"name": "main", "commit": "c0"}],
        "snapshots": [
            {"commit": "c0", "links": [{"path": "model.bin", "blob_hash": "b1"}]},
        ],
    }
    assert manifest_digest(m_snap) != base_digest


def test_build_cache_manifest_from_directory(tmp_path: Path) -> None:
    """build_cache_manifest reconstructs blobs, refs, and snapshots."""
    model_dir = tmp_path / "models--org--test"
    blobs_dir = model_dir / "blobs"
    refs_dir = model_dir / "refs"
    snapshots_dir = model_dir / "snapshots"
    commit_dir = snapshots_dir / "1234abcd"

    blobs_dir.mkdir(parents=True)
    refs_dir.mkdir(parents=True)
    commit_dir.mkdir(parents=True)

    # Blob file
    blob_file = blobs_dir / "blobhash123"
    blob_file.write_bytes(b"hello world")

    # Ref file
    ref_file = refs_dir / "main"
    ref_file.write_text("1234abcd\n", encoding="utf-8")

    # Snapshot link
    snap_link = commit_dir / "config.json"
    try:
        snap_link.symlink_to(blob_file)
    except (OSError, NotImplementedError):
        # On Windows without symlink privilege, write matching content
        snap_link.write_bytes(b"hello world")

    manifest = build_cache_manifest(model_dir)
    assert manifest["model_dir_name"] == "models--org--test"
    assert len(manifest["blobs"]) == 1
    assert manifest["blobs"][0]["hash"] == "blobhash123"
    assert manifest["blobs"][0]["size"] == 11
    assert manifest["total_size"] == 11
    assert len(manifest["refs"]) == 1
    assert manifest["refs"][0]["name"] == "main"
    assert manifest["refs"][0]["commit"] == "1234abcd"
    assert "manifest_sha256" in manifest
    assert len(manifest["manifest_sha256"]) == 64


def test_build_model_sync_payload_behaviors(tmp_path: Path) -> None:
    """build_model_sync_payload produces expected dictionary for different sources."""
    model_no_repo = SimpleNamespace(id="m1", hf_repo_id=None)
    assert build_model_sync_payload(model_no_repo) is None

    model = SimpleNamespace(id="m2", hf_repo_id="org/repo")

    # download_from_hf returns base
    res_hf = build_model_sync_payload(model, source="download_from_hf")
    assert res_hf == {
        "model_id": "m2",
        "hf_repo_id": "org/repo",
        "source": "download_from_hf",
    }

    # model_cache_dir is None
    with patch("llm_port_backend.services.llm.artifacts.model_cache_dir", return_value=None):
        res_no_cache = build_model_sync_payload(model, source="sync_from_server")
        assert res_no_cache == {
            "model_id": "m2",
            "hf_repo_id": "org/repo",
            "source": "sync_from_server",
        }

    # model_cache_dir exists with blobs
    fake_manifest = {
        "model_dir_name": "models--org--repo",
        "blobs": [{"hash": "b1", "size": 10}],
        "refs": [{"name": "main", "commit": "c1"}],
        "snapshots": [],
        "total_size": 10,
        "manifest_sha256": "fake_sha256",
    }
    with patch("llm_port_backend.services.llm.artifacts.model_cache_dir", return_value=tmp_path):
        with patch("llm_port_backend.services.llm.artifacts.build_cache_manifest", return_value=fake_manifest):
            res_sync = build_model_sync_payload(model, source="sync_from_server")
            assert res_sync["manifest_sha256"] == "fake_sha256"
            assert res_sync["model_id"] == "m2"
            assert res_sync["blobs"] == [{"hash": "b1", "size": 10}]

