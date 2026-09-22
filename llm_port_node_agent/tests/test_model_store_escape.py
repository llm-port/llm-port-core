"""A symlink out of the model store must be refused, and said so clearly.

The escape that actually happens is not a crafted ``../`` in the payload. It
is a stale symlink on the node: the DGX pair had

    ~/.cache/huggingface/hub/models--Qwen--Qwen2.5-0.5B-Instruct
      -> /home/sachi/.cache/huggingface/models--Qwen--Qwen2.5-0.5B-Instruct

left over from an earlier model-store root, one level above the current one.
The check correctly refused to write through it. The *message* was
"Unsafe model_dir_name: 'models--Qwen--Qwen2.5-0.5B-Instruct'", which sends
whoever reads it hunting for a malicious path that does not exist.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from llm_port_node_agent.model_puller import ModelPullerError, _safe_join

NAME = "models--Qwen--Qwen2.5-0.5B-Instruct"


def test_an_ordinary_name_joins(tmp_path: Path) -> None:
    root = tmp_path / "hub"
    root.mkdir()
    assert _safe_join(root, NAME, field="model_dir_name") == (root / NAME).resolve()


def test_a_symlink_out_of_the_store_is_refused(tmp_path: Path) -> None:
    root = tmp_path / "hub"
    root.mkdir()
    outside = tmp_path / NAME  # one level up, exactly as on the nodes
    outside.mkdir()
    try:
        (root / NAME).symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):  # pragma: no cover - needs privilege on Windows
        pytest.skip("symlinks not permitted in this environment")

    with pytest.raises(ModelPullerError) as err:
        _safe_join(root, NAME, field="model_dir_name")

    message = str(err.value)
    # The three things the operator needs: that it is a symlink, where it
    # goes, and what to do about it.
    assert "symlink" in message
    assert str(outside) in message
    assert "Remove the symlink" in message


def test_a_traversal_is_refused_with_its_own_reason(tmp_path: Path) -> None:
    root = tmp_path / "hub"
    root.mkdir()
    with pytest.raises(ModelPullerError, match="not a relative path inside the store"):
        _safe_join(root, "../escape", field="model_dir_name")


def test_an_absolute_path_is_refused(tmp_path: Path) -> None:
    root = tmp_path / "hub"
    root.mkdir()
    with pytest.raises(ModelPullerError):
        _safe_join(root, str(tmp_path / "elsewhere"), field="model_dir_name")
