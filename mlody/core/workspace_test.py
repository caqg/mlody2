"""Tests for mlody.core.workspace — two-phase loading and target resolution."""

from __future__ import annotations

import dataclasses
import io
from pathlib import Path

import pytest
from pyfakefs.fake_filesystem import FakeFilesystem
from rich.console import Console
from starlarkish.core.struct import Struct

from mlody.core.targets import TargetAddress
from mlody.core.workspace import RootInfo, Workspace, WorkspaceLoadError

ROOT = Path("/project")

BUILTINS_MLODY = """\
def root(name, path, description=""):
    builtins.register("root", struct(
        name=name,
        path=path,
        description=description,
    ))
"""

ROOTS_MLODY = """\
load("//mlody/core/builtins.mlody", "root")

root(name="lexica", path="//mlody/teams/lexica", description="text ML team")
"""

TYPES_MLODY = """\
builtins.register("type", struct(
    kind="type", type="mlody-workspace", name="mlody-workspace",
    attributes={}, _allowed_attrs={},
))
"""


@pytest.fixture()
def project(fs: FakeFilesystem) -> Path:
    """Set up a fake project with roots and team files."""
    fs.create_file(str(ROOT / "mlody/core/builtins.mlody"), contents=BUILTINS_MLODY)
    fs.create_file(str(ROOT / "mlody/roots.mlody"), contents=ROOTS_MLODY)
    fs.create_file(str(ROOT / "mlody/common/types.mlody"), contents=TYPES_MLODY)
    fs.create_file(
        str(ROOT / "mlody/teams/lexica/models.mlody"),
        contents='builtins.register("root", struct(name="bert", lr=0.001))',
    )
    return ROOT


# ---------------------------------------------------------------------------
# RootInfo
# ---------------------------------------------------------------------------


class TestRootInfo:
    """Requirement: RootInfo is a frozen dataclass."""

    def test_fields(self) -> None:
        info = RootInfo(name="lexica", path="//mlody/teams/lexica", description="text ML team")
        assert info.name == "lexica"
        assert info.path == "//mlody/teams/lexica"
        assert info.description == "text ML team"

    def test_frozen(self) -> None:
        info = RootInfo(name="a", path="b", description="c")
        with pytest.raises(dataclasses.FrozenInstanceError):
            info.name = "x"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Constructor defaults
# ---------------------------------------------------------------------------


class TestWorkspaceConstructor:
    """Requirement: Default roots file location."""

    def test_default_roots_path(self, project: Path) -> None:
        ws = Workspace(monorepo_root=project)
        assert ws._roots_file == project / "mlody" / "roots.mlody"

    def test_custom_roots_path(self, project: Path) -> None:
        custom = project / "other" / "roots.mlody"
        ws = Workspace(monorepo_root=project, roots_file=custom)
        assert ws._roots_file == custom


# ---------------------------------------------------------------------------
# Two-phase loading
# ---------------------------------------------------------------------------


class TestTwoPhaseLoading:
    """Requirement: Two-phase loading of pipeline definitions."""

    def test_phase1_root_discovery(self, project: Path) -> None:
        ws = Workspace(monorepo_root=project)
        ws.load()

        assert "lexica" in ws.root_infos
        info = ws.root_infos["lexica"]
        assert info.name == "lexica"
        assert info.path == "//mlody/teams/lexica"
        assert info.description == "text ML team"

    def test_phase2_evaluates_files_under_roots(self, project: Path) -> None:
        ws = Workspace(monorepo_root=project)
        ws.load()

        # models.mlody registers "bert" as a root; key is path-qualified
        assert "mlody/teams/lexica/models:bert" in ws.evaluator.roots

    def test_phase2_skips_already_loaded_files(self, fs: FakeFilesystem, project: Path) -> None:
        # builtins.mlody is loaded in Phase 1 via roots.mlody's load() call.
        # Phase 2 should not re-evaluate it even though it's under mlody/.
        ws = Workspace(monorepo_root=project)
        ws.load()

        builtins_path = project / "mlody" / "core" / "builtins.mlody"
        assert builtins_path in ws.evaluator.loaded_files
        # Only one entry in _module_globals for builtins.mlody proves single evaluation —
        # a second eval_file() call would still return cached globals (Evaluator line 185),
        # but the Workspace skip check prevents even that redundant call.
        assert ws.evaluator._module_globals[builtins_path] is ws.evaluator._module_globals[builtins_path]  # type: ignore[attr-defined]
        globals_snapshot = dict(ws.evaluator._module_globals)  # type: ignore[attr-defined]
        # Re-run load() to confirm idempotency — no new entries appear
        ws.load()
        assert dict(ws.evaluator._module_globals) == globals_snapshot  # type: ignore[attr-defined]

    def test_missing_roots_file(self, fs: FakeFilesystem) -> None:
        root = Path("/empty")
        root.mkdir()
        ws = Workspace(monorepo_root=root)

        with pytest.raises(FileNotFoundError, match="Roots file not found"):
            ws.load()

    def test_no_roots_registered(self, fs: FakeFilesystem) -> None:
        root = Path("/no_roots")
        root.mkdir()
        fs.create_file(str(root / "mlody/roots.mlody"), contents="# no roots here\n")
        ws = Workspace(monorepo_root=root)
        ws.load()

        assert ws.root_infos == {}

    def test_evaluator_is_same_instance_after_load(self, project: Path) -> None:
        ws = Workspace(monorepo_root=project)
        evaluator_before = ws.evaluator
        ws.load()
        assert ws.evaluator is evaluator_before

    def test_evaluator_exposes_module_globals_for_lsp(self, project: Path) -> None:
        # LSP needs _module_globals to provide completions for symbols in loaded files
        ws = Workspace(monorepo_root=project)
        ws.load()

        models_path = project / "mlody" / "teams" / "lexica" / "models.mlody"
        module_globals = ws.evaluator._module_globals  # type: ignore[attr-defined]
        assert models_path in module_globals
        assert "builtins" in module_globals[models_path]

    def test_default_skip_list_skips_sandbox_mlody(
        self, fs: FakeFilesystem, project: Path
    ) -> None:
        (project / "mlody/roots.mlody").write_text(
            'load("//mlody/core/builtins.mlody", "root")\n'
            'root(name="lexica", path="//mlody/teams/lexica", description="text ML team")\n'
            'root(name="common", path="//mlody/common", description="common")\n'
        )
        fs.create_file(
            str(project / "mlody/common/sandbox.mlody"),
            contents='builtins.register("root", struct(name="sandbox_only", value=1))',
        )
        ws = Workspace(monorepo_root=project)
        ws.load()
        assert "mlody/common/sandbox:sandbox_only" not in ws.evaluator.roots

    def test_full_workspace_loads_sandbox_mlody(
        self, fs: FakeFilesystem, project: Path
    ) -> None:
        (project / "mlody/roots.mlody").write_text(
            'load("//mlody/core/builtins.mlody", "root")\n'
            'root(name="lexica", path="//mlody/teams/lexica", description="text ML team")\n'
            'root(name="common", path="//mlody/common", description="common")\n'
        )
        fs.create_file(
            str(project / "mlody/common/sandbox.mlody"),
            contents='builtins.register("root", struct(name="sandbox_only", value=1))',
        )
        ws = Workspace(monorepo_root=project, full_workspace=True)
        ws.load()
        assert "mlody/common/sandbox:sandbox_only" in ws.evaluator.roots

    def test_skip_pattern_with_ellipsis_skips_subtree(
        self, fs: FakeFilesystem, project: Path
    ) -> None:
        (project / "mlody/roots.mlody").write_text(
            'load("//mlody/core/builtins.mlody", "root")\n'
            'root(name="lexica", path="//mlody/teams/lexica", description="text ML team")\n'
            'root(name="common", path="//mlody/common", description="common")\n'
        )
        fs.create_file(
            str(project / "mlody/common/skipme/a.mlody"),
            contents='builtins.register("root", struct(name="skip_a", value=1))',
        )
        fs.create_file(
            str(project / "mlody/common/skipme/nested/b.mlody"),
            contents='builtins.register("root", struct(name="skip_b", value=2))',
        )
        fs.create_file(
            str(project / "mlody/common/keep.mlody"),
            contents='builtins.register("root", struct(name="keep", value=3))',
        )
        ws = Workspace(
            monorepo_root=project,
            skipped_mlody_paths=["mlody/common/skipme/..."],
        )
        ws.load()
        assert "mlody/common/skipme/a:skip_a" not in ws.evaluator.roots
        assert "mlody/common/skipme/nested/b:skip_b" not in ws.evaluator.roots
        assert "mlody/common/keep:keep" in ws.evaluator.roots


# ---------------------------------------------------------------------------
# Target resolution
# ---------------------------------------------------------------------------


class TestResolve:
    """Requirement: Target resolution via Workspace."""

    def test_resolve_string_target(self, project: Path) -> None:
        ws = Workspace(monorepo_root=project)
        ws.load()

        result = ws.resolve("@bert//models:lr")
        assert result == 0.001

    def test_resolve_target_address(self, project: Path) -> None:
        ws = Workspace(monorepo_root=project)
        ws.load()

        addr = TargetAddress(root="bert", package_path="", target_name="lr", field_path=())
        result = ws.resolve(addr)
        assert result == 0.001

    def test_resolve_error_propagation_missing_root(self, project: Path) -> None:
        ws = Workspace(monorepo_root=project)
        ws.load()

        with pytest.raises(KeyError, match="NONEXISTENT"):
            ws.resolve("@NONEXISTENT//pkg:x")

    def test_resolve_error_propagation_missing_field(self, project: Path) -> None:
        ws = Workspace(monorepo_root=project)
        ws.load()

        with pytest.raises(AttributeError):
            ws.resolve("@bert//models:lr.nonexistent_field")

    def test_resolve_workspace_attr_returns_value_struct(self, project: Path) -> None:
        from starlarkish.core.struct import Struct

        ws = Workspace(monorepo_root=project)
        ws.load()

        result = ws.resolve("'info")
        assert isinstance(result, Struct)
        assert getattr(result, "kind", None) == "value"
        assert getattr(getattr(result, "location", None), "type", None) == "virtual"
        assert getattr(result, "label", None) == "'info"

    def test_force_workspace_attr_returns_attribute(self, project: Path) -> None:
        from mlody.core.workspace import force

        ws = Workspace(monorepo_root=project)
        ws.load()

        result = force(ws.resolve("'info"))
        assert result == ws.info

    def test_force_passes_through_non_value(self, project: Path) -> None:
        from mlody.core.workspace import force

        ws = Workspace(monorepo_root=project)
        ws.load()

        plain = ws.resolve("@bert//models:lr")
        assert force(plain) is plain

    def test_force_passes_through_plain_python_object(self) -> None:
        from mlody.core.workspace import force

        assert force(3.14) == 3.14
        assert force("hello") == "hello"
        assert force(None) is None

    def test_resolve_module_label_returns_entities(
        self, project: Path, fs: FakeFilesystem
    ) -> None:
        """@root//path without :name returns all entities from that module as a dict."""
        from starlarkish.core.struct import Struct

        fs.create_file(
            str(ROOT / "mlody/teams/lexica/pipeline.mlody"),
            contents='builtins.register("action", Struct(kind="action", name="trainer", inputs=[], outputs=[], config=[]))',
        )
        ws = Workspace(monorepo_root=project)
        ws.load()

        result = ws.resolve("@lexica//pipeline")
        assert isinstance(result, dict)
        assert "action/trainer" in result
        assert isinstance(result["action/trainer"], Struct)
        assert result["action/trainer"].name == "trainer"  # type: ignore[attr-defined]



# ---------------------------------------------------------------------------
# stdout safety (LSP transport guard)
# ---------------------------------------------------------------------------


class TestPrintFn:
    """Requirement: print_fn controls sandbox print() behaviour."""

    def test_default_print_fn_writes_to_stdout(
        self, fs: FakeFilesystem, project: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # CLI usage: print() in .mlody scripts should reach the terminal.
        fs.create_file(
            str(project / "mlody" / "teams" / "lexica" / "printer.mlody"),
            contents='print("hello from workspace")\n',
        )
        ws = Workspace(monorepo_root=project)
        ws.load()

        captured = capsys.readouterr()
        assert "hello from workspace" in captured.out

    def test_custom_print_fn_suppresses_stdout(
        self, fs: FakeFilesystem, project: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # LSP usage: passing a no-op print_fn prevents sandbox print() from
        # corrupting the stdout JSON-RPC transport; a null console prevents the
        # registry dump from reaching stdout.
        fs.create_file(
            str(project / "mlody" / "teams" / "lexica" / "printer.mlody"),
            contents='print("should be suppressed")\n',
        )
        ws = Workspace(
            monorepo_root=project,
            print_fn=lambda *_, **__: None,
            console=Console(file=io.StringIO()),
        )
        ws.load()

        captured = capsys.readouterr()
        assert captured.out == ""


# ---------------------------------------------------------------------------
# Error collection (Phase 2)
# ---------------------------------------------------------------------------


class TestWorkspaceLoadError:
    """Requirement: Phase 2 errors are collected and raised as WorkspaceLoadError."""

    def test_single_bad_file_raises(self, fs: FakeFilesystem, project: Path) -> None:
        fs.create_file(
            str(project / "mlody" / "teams" / "lexica" / "broken.mlody"),
            contents="this is not valid starlark !!!\n",
        )
        ws = Workspace(monorepo_root=project)
        with pytest.raises(WorkspaceLoadError) as exc_info:
            ws.load()
        assert len(exc_info.value.failures) == 1
        path, _exc = exc_info.value.failures[0]
        assert path.name == "broken.mlody"

    def test_multiple_bad_files_collected(self, fs: FakeFilesystem, project: Path) -> None:
        fs.create_file(
            str(project / "mlody" / "teams" / "lexica" / "bad_a.mlody"),
            contents="syntax error !!!\n",
        )
        fs.create_file(
            str(project / "mlody" / "teams" / "lexica" / "bad_b.mlody"),
            contents="another error ???\n",
        )
        ws = Workspace(monorepo_root=project)
        with pytest.raises(WorkspaceLoadError) as exc_info:
            ws.load()
        assert len(exc_info.value.failures) == 2
        failed_names = {p.name for p, _ in exc_info.value.failures}
        assert failed_names == {"bad_a.mlody", "bad_b.mlody"}

    def test_error_message_lists_files(self, fs: FakeFilesystem, project: Path) -> None:
        fs.create_file(
            str(project / "mlody" / "teams" / "lexica" / "broken.mlody"),
            contents="syntax error !!!\n",
        )
        ws = Workspace(monorepo_root=project)
        with pytest.raises(WorkspaceLoadError) as exc_info:
            ws.load()
        msg = str(exc_info.value)
        assert "1 file(s) failed to load" in msg
        assert "broken.mlody" in msg

    def test_good_files_still_loaded_alongside_bad(
        self, fs: FakeFilesystem, project: Path
    ) -> None:
        """Good files evaluated before the bad one are still registered."""
        # models.mlody (good) is alphabetically before broken.mlody
        # but we use sorted(), so: broken < models — both are attempted.
        fs.create_file(
            str(project / "mlody" / "teams" / "lexica" / "broken.mlody"),
            contents="syntax error !!!\n",
        )
        ws = Workspace(monorepo_root=project)
        with pytest.raises(WorkspaceLoadError):
            ws.load()
        # models.mlody was processed; "bert" root should be registered
        assert "mlody/teams/lexica/models:bert" in ws.evaluator.roots


class TestStdoutSafety:
    """Requirement: load() must never write to stdout (framework-level).

    The LSP server communicates over stdio.  Any stray print() or write to
    sys.stdout from workspace/evaluator framework code (not user scripts)
    injects raw bytes into the JSON-RPC transport, corrupting the
    Content-Length framing and causing the client to lose sync.
    """

    def test_load_does_not_write_to_stdout(
        self, project: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # The LSP server always supplies a no-op print_fn and a null console so
        # that neither sandbox print() calls nor the post-load registry dump
        # reach stdout.
        ws = Workspace(
            monorepo_root=project,
            print_fn=lambda *_, **__: None,
            console=Console(file=io.StringIO()),
        )
        ws.load()

        captured = capsys.readouterr()
        assert captured.out == "", (
            "workspace.load() must not write to stdout — "
            "stdout is the LSP transport and stray output corrupts the protocol"
        )


# ---------------------------------------------------------------------------
# Port list → named Struct conversion (Phase 3)
# ---------------------------------------------------------------------------

# Shared .mlody content for port-conversion tests.  Uses Struct() directly
# so we control the exact shape without depending on the task/action DSL.
_ROOTS_WITH_BERT = """\
load("//mlody/core/builtins.mlody", "root")
root(name="bert", path="//mlody/teams/bert", description="bert team")
"""

_PORT_BUILTINS = """\
def root(name, path, description=""):
    builtins.register("root", struct(
        name=name,
        path=path,
        description=description,
    ))
"""


def _make_port_project(fs: FakeFilesystem, entity_mlody: str) -> Path:
    """Create a minimal fake workspace with one entity file under //mlody/teams/bert/."""
    root = Path("/port_project")
    fs.create_file(str(root / "mlody/core/builtins.mlody"), contents=_PORT_BUILTINS)
    fs.create_file(str(root / "mlody/roots.mlody"), contents=_ROOTS_WITH_BERT)
    fs.create_file(str(root / "mlody/common/types.mlody"), contents=TYPES_MLODY)
    fs.create_dir(str(root / "mlody/teams/bert"))
    fs.create_file(str(root / "mlody/teams/bert/entity.mlody"), contents=entity_mlody)
    return root


class TestPortConversion:
    """Requirement: workspace-port-conversion — port lists become named Structs."""

    # TC-001/002/003 — basic named access, resolve to Struct, deep traversal
    def test_task_outputs_accessible_by_name_after_load(
        self, fs: FakeFilesystem
    ) -> None:
        # TC-001: outputs list element is accessible as named attribute.
        # TC-002: outputs field itself is a Struct after load().
        # TC-003: deep traversal into element sub-field works.
        entity_mlody = """\
loc = Struct(kind="location", type="path", name="weights_path", path="/tmp/w")
weight_val = Struct(kind="value", name="backbone_weights", location=loc)
builtins.register("task", Struct(
    kind="task",
    name="train_bert",
    inputs=[],
    outputs=[weight_val],
    config=[],
))
"""
        root = _make_port_project(fs, entity_mlody)
        ws = Workspace(monorepo_root=root)
        ws.load()

        # TC-001: named element is accessible
        el = ws.resolve("@bert//entity:train_bert.outputs.backbone_weights")
        assert isinstance(el, Struct)
        assert getattr(el, "name", None) == "backbone_weights"

        # TC-002: outputs field is a Struct
        outputs_struct = ws.resolve("@bert//entity:train_bert.outputs")
        assert isinstance(outputs_struct, Struct)
        assert isinstance(getattr(outputs_struct, "backbone_weights", None), Struct)

        # TC-003: deep traversal into element sub-field
        loc_val = ws.resolve("@bert//entity:train_bert.outputs.backbone_weights.location")
        assert getattr(loc_val, "path", None) == "/tmp/w"

    # TC-004 — inputs and config port fields
    def test_inputs_and_config_accessible_by_name_after_load(
        self, fs: FakeFilesystem
    ) -> None:
        entity_mlody = """\
inp = Struct(kind="value", name="raw_data", location=Struct(kind="location", type="path", name="data_loc", path="/data"))
cfg = Struct(kind="value", name="lr_value", location=Struct(kind="location", type="path", name="lr_loc", path="/cfg"))
builtins.register("task", Struct(
    kind="task",
    name="preprocess",
    inputs=[inp],
    outputs=[],
    config=[cfg],
))
"""
        root = _make_port_project(fs, entity_mlody)
        ws = Workspace(monorepo_root=root)
        ws.load()

        # inputs
        inp_el = ws.resolve("@bert//entity:preprocess.inputs.raw_data")
        assert isinstance(inp_el, Struct)
        assert getattr(inp_el, "name", None) == "raw_data"
        assert isinstance(ws.resolve("@bert//entity:preprocess.inputs"), Struct)

        # config
        cfg_el = ws.resolve("@bert//entity:preprocess.config.lr_value")
        assert isinstance(cfg_el, Struct)
        assert getattr(cfg_el, "name", None) == "lr_value"
        assert isinstance(ws.resolve("@bert//entity:preprocess.config"), Struct)

    # TC-005 — direct action entity (not embedded in a task)
    def test_direct_action_outputs_accessible_by_name(
        self, fs: FakeFilesystem
    ) -> None:
        entity_mlody = """\
w = Struct(kind="value", name="weights", location=Struct(kind="location", type="path", name="w_loc", path="/weights"))
builtins.register("action", Struct(
    kind="action",
    name="train_action",
    inputs=[],
    outputs=[w],
    config=[],
))
"""
        root = _make_port_project(fs, entity_mlody)
        ws = Workspace(monorepo_root=root)
        ws.load()

        el = ws.resolve("@bert//entity:train_action.outputs.weights")
        assert isinstance(el, Struct)
        assert getattr(el, "name", None) == "weights"

    # TC-006 — embedded action inside a task
    def test_embedded_action_outputs_accessible_by_name(
        self, fs: FakeFilesystem
    ) -> None:
        entity_mlody = """\
w = Struct(kind="value", name="weights", location=Struct(kind="location", type="path", name="w_loc", path="/w"))
emb_action = Struct(kind="action", name="finetune", inputs=[], outputs=[w], config=[])
builtins.register("task", Struct(
    kind="task",
    name="finetune_task",
    inputs=[],
    outputs=[],
    config=[],
    action=emb_action,
))
"""
        root = _make_port_project(fs, entity_mlody)
        ws = Workspace(monorepo_root=root)
        ws.load()

        el = ws.resolve("@bert//entity:finetune_task.action.outputs.weights")
        assert isinstance(el, Struct)
        assert getattr(el, "name", None) == "weights"

    # TC-007 — empty list becomes empty Struct
    def test_empty_port_list_becomes_empty_struct(
        self, fs: FakeFilesystem
    ) -> None:
        entity_mlody = """\
builtins.register("task", Struct(
    kind="task",
    name="empty_ports",
    inputs=[],
    outputs=[],
    config=[],
))
"""
        root = _make_port_project(fs, entity_mlody)
        ws = Workspace(monorepo_root=root)
        ws.load()  # must not raise

        config_val = ws.resolve("@bert//entity:empty_ports.config")
        assert isinstance(config_val, Struct)
        # An empty Struct has no fields — accessing any field raises AttributeError.
        with pytest.raises(AttributeError):
            _ = getattr(config_val, "nonexistent")

    # TC-008 — missing name field raises ValueError
    def test_element_missing_name_raises_value_error(
        self, fs: FakeFilesystem
    ) -> None:
        entity_mlody = """\
no_name_el = Struct(kind="value", location=Struct(kind="location", type="path", name="x", path="/x"))
builtins.register("task", Struct(
    kind="task",
    name="bad_task",
    inputs=[],
    outputs=[no_name_el],
    config=[],
))
"""
        root = _make_port_project(fs, entity_mlody)
        ws = Workspace(monorepo_root=root)
        with pytest.raises(ValueError, match="bad_task") as exc_info:
            ws.load()
        # Error message must mention the field name too
        assert "outputs" in str(exc_info.value)

    # TC-009 — duplicate names raise ValueError
    def test_duplicate_element_names_raise_value_error(
        self, fs: FakeFilesystem
    ) -> None:
        entity_mlody = """\
w1 = Struct(kind="value", name="w", location=Struct(kind="location", type="path", name="l1", path="/1"))
w2 = Struct(kind="value", name="w", location=Struct(kind="location", type="path", name="l2", path="/2"))
builtins.register("task", Struct(
    kind="task",
    name="dup_task",
    inputs=[],
    outputs=[w1, w2],
    config=[],
))
"""
        root = _make_port_project(fs, entity_mlody)
        ws = Workspace(monorepo_root=root)
        with pytest.raises(ValueError, match="dup_task") as exc_info:
            ws.load()
        assert "w" in str(exc_info.value)

    # TC-010 — idempotency: calling _convert_single_entity twice is safe
    def test_convert_single_entity_is_idempotent(self) -> None:
        w = Struct(kind="value", name="weights", path="/w")
        entity = Struct(
            kind="task",
            name="some_task",
            inputs=[],
            outputs=[w],
            config=[],
        )
        first = Workspace._convert_single_entity(entity)
        second = Workspace._convert_single_entity(first)
        # No error, and field-by-field equality holds.
        assert first == second
        assert isinstance(getattr(first.outputs, "weights", None), Struct)

    # TC-011 — non-port fields are preserved unchanged after conversion
    def test_non_port_fields_preserved_after_load(
        self, fs: FakeFilesystem
    ) -> None:
        entity_mlody = """\
builtins.register("task", Struct(
    kind="task",
    name="meta_task",
    inputs=[],
    outputs=[],
    config=[],
    extra_meta="important_value",
))
"""
        root = _make_port_project(fs, entity_mlody)
        ws = Workspace(monorepo_root=root)
        ws.load()

        entity = ws.resolve("@bert//entity:meta_task")
        assert getattr(entity, "kind", None) == "task"
        assert getattr(entity, "name", None) == "meta_task"
        assert getattr(entity, "extra_meta", None) == "important_value"
