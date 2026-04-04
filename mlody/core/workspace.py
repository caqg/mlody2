"""Workspace: two-phase loading of .mlody pipeline definitions."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, cast

from rich.console import Console
from rich.syntax import Syntax

from starlarkish.evaluator.evaluator import Evaluator
from mlody.common.context import build_ctx
from mlody.core.source_parser import extract_entity_ranges
from mlody.core.targets import TargetAddress, parse_target, resolve_target_value

_logger = logging.getLogger(__name__)
_DEFAULT_SKIPPED_MLODY_PATHS = ("mlody/common/sandbox.mlody",)


def force(v: object) -> object:
    """Materialise a virtual value Struct; return all other inputs unchanged.

    A "virtual value" is a Struct with ``kind == "value"`` whose ``location``
    has ``type == "virtual"``.  In that case ``location.materializer(v)`` is
    called and its return value is returned.  All other inputs pass through.
    """
    from starlarkish.core.struct import Struct

    if not isinstance(v, Struct):
        return v
    if getattr(v, "kind", None) != "value":
        return v
    loc = getattr(v, "location", None)
    if loc is None:
        return v
    if getattr(loc, "type", None) != "virtual":
        return v
    materializer = getattr(loc, "materializer", None)
    if materializer is None:
        return v
    return cast(Any, materializer)(v)


class WorkspaceLoadError(Exception):
    """One or more .mlody files failed to evaluate during Phase 2 loading."""

    def __init__(self, failures: list[tuple[Path, Exception]]) -> None:
        self.failures = failures
        lines = "\n".join(
            f"  {path}: {type(exc).__name__}: {exc}"
            for path, exc in failures
        )
        super().__init__(f"{len(failures)} file(s) failed to load:\n{lines}")


@dataclass(frozen=True)
class RootInfo:
    """Metadata for a registered root."""

    name: str
    path: str
    description: str


class Workspace:
    """Wraps the starlarkish Evaluator with two-phase loading and target resolution."""

    def __init__(
        self,
        monorepo_root: Path,
        roots_file: Path | None = None,
        full_workspace: bool = False,
        skipped_mlody_paths: tuple[str, ...] | list[str] | None = None,
        print_fn: Callable[..., None] = print,
        console: Console | None = None,
    ) -> None:
        self._monorepo_root = monorepo_root
        self._roots_file = roots_file or (monorepo_root / "mlody" / "roots.mlody")
        self._full_workspace = full_workspace
        self._skipped_mlody_paths = tuple(
            skipped_mlody_paths
            if skipped_mlody_paths is not None
            else _DEFAULT_SKIPPED_MLODY_PATHS
        )
        self._console = console if console is not None else Console()
        self._evaluator = Evaluator(
            root=monorepo_root,
            print_fn=print_fn,
            extra_ctx=build_ctx(monorepo_root),
            line_range_extractor=extract_entity_ranges,
        )
        self._root_infos: dict[str, RootInfo] = {}

    @property
    def evaluator(self) -> Evaluator:
        return self._evaluator

    @property
    def root_infos(self) -> dict[str, RootInfo]:
        return self._root_infos

    @property
    def info(self) -> object:
        """Synthesised workspace-level metadata (git state + registered roots).

        Returned as a Struct so field access works in .mlody files and the
        show command can traverse sub-fields (e.g. "'info.branch").
        """
        import subprocess

        from starlarkish.core.struct import struct

        def _git(*args: str) -> str:
            try:
                result = subprocess.run(
                    ["git", "-C", str(self._monorepo_root), *args],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                return result.stdout.strip()
            except Exception:
                return ""

        return struct(
            path=str(self._monorepo_root),
            branch=_git("branch", "--show-current"),
            sha=_git("rev-parse", "HEAD"),
            roots=sorted(self._root_infos.keys()),
        )

    def _is_skipped_mlody_file(self, mlody_file: Path) -> bool:
        """Return True when a file matches the configured skip patterns.

        Pattern rules:
        - `path/to/file.mlody` skips exactly that file.
        - `path/...` skips all files under `path/`.
        """
        rel = mlody_file.relative_to(self._monorepo_root).as_posix()
        for raw_pattern in self._skipped_mlody_paths:
            pattern = raw_pattern.strip().lstrip("./").lstrip("/")
            if not pattern:
                continue
            if pattern.endswith("/..."):
                prefix = pattern[:-4].rstrip("/")
                if not prefix:
                    return True
                if rel.startswith(f"{prefix}/"):
                    return True
                continue
            if rel == pattern:
                return True
        return False

    def load(self, verbose: bool = False) -> None:
        """Execute two-phase loading of pipeline definitions."""
        # Phase 1: Root discovery
        if not self._roots_file.exists():
            msg = f"Roots file not found: {self._roots_file}"
            raise FileNotFoundError(msg)

        self._evaluator.eval_file(self._roots_file)

        # Load type definitions (best-effort; may not be available in all environments)
        types_path = self._monorepo_root / "mlody" / "common" / "types.mlody"
        if types_path not in self._evaluator.loaded_files:
            try:
                self._evaluator.eval_file(types_path)
            except Exception:
                pass

        self._root_infos = {}
        for _key, root_obj in self._evaluator.roots.items():
            name = root_obj.name
            self._root_infos[name] = RootInfo(
                name=name,
                path=getattr(root_obj, "path", ""),
                description=getattr(root_obj, "description", ""),
            )

        # Phase 2: Full evaluation
        load_errors: list[tuple[Path, Exception]] = []
        for info in self._root_infos.values():
            root_abs = self._monorepo_root / info.path.lstrip("/")
            _logger.debug("Loading root: %s", root_abs)
            if not root_abs.is_dir():
                continue
            for mlody_file in sorted(root_abs.glob("**/*.mlody")):
                if not self._full_workspace and self._is_skipped_mlody_file(mlody_file):
                    _logger.debug("Skipping %s due to workspace skip list", mlody_file)
                    continue
                if mlody_file in self._evaluator.loaded_files:
                    continue
                try:
                    self._evaluator.eval_file(mlody_file)
                except Exception as exc:
                    _logger.error(
                        "Failed to load %s: %s: %s", mlody_file, type(exc).__name__, exc
                    )
                    load_errors.append((mlody_file, exc))

        if load_errors:
            raise WorkspaceLoadError(load_errors)

        self._evaluator.resolve()

        if verbose:
            data = {str(k): v.to_dict() if hasattr(v, "to_dict") else v for k, v in self._evaluator.all.items()}
            self._console.print(Syntax(json.dumps(data, indent=2, default=repr), "json"))

    def resolve(self, target: str | TargetAddress) -> object:
        """Parse (if string) and resolve a target to a value.

        Supports:
        - Entity-spec labels with a name:  @root//pkg:name, //pkg:name, :name
        - Entity-spec labels without name, no path: @root  → root struct
        - Entity-spec labels without name, with path: @root//pkg/module → dict of all
          entities registered from that module, keyed by ``"kind/name"``
        - Workspace-level attribute labels: 'attr, 'attr.subfield
        """
        def _step(obj: object, segment: str) -> object:
            # Support list traversal by value name, e.g. outputs.model.
            if isinstance(obj, list):
                for item in obj:
                    if getattr(item, "name", None) == segment:
                        return item
                raise KeyError(segment)
            return getattr(obj, segment)

        if isinstance(target, str) and target.startswith("'"):
            # Workspace-attribute label: return a virtual value Struct whose
            # materializer forces the attribute access lazily.
            from mlody.core.label import parse_label as _core_parse_label
            from starlarkish.core.struct import Struct

            lbl = _core_parse_label(target)
            if lbl.attribute_path is None:
                msg = f"Empty attribute path in label: {target!r}"
                raise ValueError(msg)

            ws_type = self._evaluator._types_by_name.get("mlody-workspace")  # type: ignore[attr-defined]
            if ws_type is None:
                msg = "Type 'mlody-workspace' is not registered; ensure load() is called before resolve()"
                raise RuntimeError(msg)

            attr_path = lbl.attribute_path
            _ws_ref = self

            def _materializer(_v: object) -> object:
                    _logger.debug("workspace materializer invoked for label %r with value %r", target, _v)
                    obj: object = _ws_ref
                    for segment in attr_path:
                        obj = _step(obj, segment)
                    return obj

            virtual_loc = Struct(
                kind="location",
                type="virtual",
                name="virtual",
                materializer=_materializer,
            )
            return Struct(
                kind="value",
                type=ws_type,
                location=virtual_loc,
                label=target,
                _lineage=[],
            )

        if isinstance(target, str) and (
            target.startswith("@") or target.startswith("//")
        ):
            # Use the core label parser to detect name-less entity specs
            # (e.g. @root//path or //path without a :name).  parse_target
            # requires a :name, so we handle this case directly.
            from mlody.core.label import parse_label as _core_parse_label
            from mlody.core.label.errors import LabelParseError as _LabelParseError

            try:
                lbl = _core_parse_label(target)
            except _LabelParseError:
                pass  # fall through to parse_target for legacy error handling
            else:
                if lbl.entity is not None and lbl.entity.name is not None:
                    # Resolve direct entity labels (with optional dotted field path)
                    # against evaluator registrations by (stem, name), where:
                    #   stem = "<root_path>/<entity.path>" for @root labels
                    #   stem = "<entity.path>" for // labels
                    entity = lbl.entity
                    name_parts = entity.name.split(".")
                    base_name = name_parts[0]
                    field_parts = tuple(name_parts[1:])
                    if lbl.attribute_path:
                        field_parts = field_parts + lbl.attribute_path

                    stem_parts: list[str] = []
                    can_registry_resolve = True
                    if entity.root is not None:
                        if entity.root in self._root_infos:
                            root_rel = self._root_infos[entity.root].path.lstrip("/").rstrip("/")
                            if root_rel:
                                stem_parts.append(root_rel)
                        elif entity.root in self._evaluator._roots_by_name:
                            # Dynamic/runtime roots (e.g. @bert in tests) do not have
                            # RootInfo metadata; defer to legacy parse_target path.
                            can_registry_resolve = False
                        else:
                            available = sorted(self._evaluator._roots_by_name)
                            msg = f"Root {entity.root!r} not found; available roots: {available}"
                            raise KeyError(msg)
                    if entity.path:
                        stem_parts.append(entity.path.lstrip("/").rstrip("/"))
                    stem = "/".join([p for p in stem_parts if p])
                    path_suffix = entity.path.lstrip("/").rstrip("/") if entity.path else ""
                    root_prefix = None
                    if entity.root is not None and entity.root in self._root_infos:
                        root_prefix = self._root_infos[entity.root].path.lstrip("/").rstrip("/")

                    matches: list[tuple[str, object]] = []
                    if can_registry_resolve:
                        for key, value in self._evaluator.all.items():
                            if (
                                isinstance(key, tuple)
                                and len(key) == 3
                                and key[1] == stem
                                and key[2] == base_name
                            ):
                                matches.append((key[0], value))

                    if can_registry_resolve and not matches:
                        # Fallback: match by entity name plus path suffix/root prefix.
                        for key, value in self._evaluator.all.items():
                            if not (isinstance(key, tuple) and len(key) == 3 and key[2] == base_name):
                                continue
                            key_stem = key[1]
                            if not isinstance(key_stem, str):
                                continue
                            if root_prefix and not key_stem.startswith(root_prefix):
                                continue
                            if path_suffix and not key_stem.endswith(path_suffix):
                                continue
                            matches.append((key[0], value))

                    if matches:
                        kind_order = {
                            "task": 0,
                            "action": 1,
                            "value": 2,
                            "type": 3,
                            "location": 4,
                            "root": 5,
                        }
                        matches.sort(key=lambda kv: kind_order.get(kv[0], 99))
                        obj = matches[0][1]
                        for field in field_parts:
                            obj = _step(obj, field)
                        return obj

                    if can_registry_resolve and entity.root is not None:
                        # We tried the registry-based path but found nothing.
                        # Give a clear error rather than falling through to parse_target
                        # which would produce a confusing "root not found" message.
                        label_str = target if isinstance(target, str) else str(target)
                        msg = (
                            f"Entity {base_name!r} not found"
                            + (f" in module {stem!r}" if stem else "")
                            + f" (label: {label_str!r})"
                        )
                        raise KeyError(msg)

                if lbl.entity is not None and lbl.entity.name is None:
                    # No specific entity name.
                    roots = self._evaluator._roots_by_name
                    if lbl.entity.root is not None:
                        if lbl.entity.root not in roots:
                            available = sorted(roots)
                            msg = f"Root {lbl.entity.root!r} not found; available roots: {available}"
                            raise KeyError(msg)
                        if lbl.entity.path and lbl.entity.root in self._root_infos:
                            # Module-level label (e.g. @common//huggingface/downloader):
                            # return all entities registered from that module as a dict.
                            stem_parts_mod: list[str] = []
                            root_rel_mod = self._root_infos[lbl.entity.root].path.lstrip("/").rstrip("/")
                            if root_rel_mod:
                                stem_parts_mod.append(root_rel_mod)
                            stem_parts_mod.append(lbl.entity.path.lstrip("/").rstrip("/"))
                            mod_stem = "/".join([p for p in stem_parts_mod if p])
                            return {
                                f"{k[0]}/{k[2]}": v
                                for k, v in self._evaluator.all.items()
                                if isinstance(k, tuple) and len(k) == 3 and k[1] == mod_stem
                            }
                        return roots[lbl.entity.root]
                    # No root and no name: return all roots dict
                    return dict(roots)

                if lbl.entity is not None and lbl.entity.root is None and lbl.entity.name is not None:
                    # No @root prefix: path is relative to monorepo top.
                    # Resolve by looking up the entity name directly in the
                    # evaluated file's module globals.
                    file_path = self._monorepo_root / (lbl.entity.path.lstrip("/") + ".mlody")
                    if file_path not in self._evaluator.loaded_files:
                        self._evaluator.eval_file(file_path)
                    module_globals: dict[str, object] = self._evaluator._module_globals.get(file_path, {})  # type: ignore[attr-defined]
                    name_parts = lbl.entity.name.split(".")
                    if name_parts[0] not in module_globals:
                        raise KeyError(f"Entity {name_parts[0]!r} not found in {file_path}")
                    obj = module_globals[name_parts[0]]
                    for field in name_parts[1:]:
                        obj = _step(obj, field)
                    return obj

        address = parse_target(target) if isinstance(target, str) else target
        return resolve_target_value(address, self._evaluator._roots_by_name)
