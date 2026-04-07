"""Workspace: two-phase loading of .mlody pipeline definitions."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, cast

from rich.console import Console
from rich.syntax import Syntax

from starlarkish.core.struct import Struct
from starlarkish.evaluator.evaluator import Evaluator
from mlody.common.context import build_ctx
from mlody.core.location_composition import (
    _LocationComposeError,
    compose_location,
)
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

    @staticmethod
    def _convert_single_entity(entity: Struct) -> Struct:
        """Convert ``inputs``, ``outputs``, and ``config`` port lists to named Structs.

        Returns a new ``Struct`` with those three fields replaced by ``Struct``
        objects keyed by element ``name``.  All other fields are preserved
        unchanged.

        Idempotent: if a field is already a ``Struct`` it is left as-is.
        Raises ``ValueError`` if any element lacks a ``name`` or if duplicate
        names appear within the same list.
        """
        # Recursively convert an embedded action Struct before reconstructing
        # the outer entity, so that task.action.outputs.X traversal works.
        action_field = getattr(entity, "action", None)
        if (
            isinstance(action_field, Struct)
            and getattr(action_field, "kind", None) == "action"
        ):
            action_field = Workspace._convert_single_entity(action_field)

        entity_kind = getattr(entity, "kind", "<unknown>")
        entity_name = getattr(entity, "name", "<unknown>")

        def _convert_port(field_name: str) -> Struct:
            lst: object = getattr(entity, field_name, None)
            # Idempotency: already a Struct — leave it unchanged.
            if isinstance(lst, Struct):
                return lst
            # Treat None or empty list as an empty Struct.
            if not lst:
                return Struct()
            # lst is a non-empty list; validate and build the named Struct.
            seen: dict[str, int] = {}
            for idx, el in enumerate(lst):  # type: ignore[union-attr]
                name = getattr(el, "name", None)
                if not name:
                    msg = (
                        f"Entity {entity_kind!r}/{entity_name!r}: "
                        f"element at index {idx} of field {field_name!r} "
                        f"is missing a non-empty 'name' field"
                    )
                    raise ValueError(msg)
                if name in seen:
                    msg = (
                        f"Entity {entity_kind!r}/{entity_name!r}: "
                        f"duplicate name {name!r} in field {field_name!r} "
                        f"(first at index {seen[name]}, repeated at index {idx})"
                    )
                    raise ValueError(msg)
                seen[name] = idx
            return Struct(**{el.name: el for el in lst})  # type: ignore[union-attr]

        new_inputs = _convert_port("inputs")
        new_outputs = _convert_port("outputs")
        new_config = _convert_port("config")

        updated: dict[str, object] = {
            **entity._fields,
            "inputs": new_inputs,
            "outputs": new_outputs,
            "config": new_config,
        }
        if action_field is not None:
            updated["action"] = action_field
        return Struct(**updated)

    def _convert_ports_to_structs(self) -> None:
        """Replace port lists on every task/action entity in the evaluator registry.

        Iterates ``self._evaluator.all``, converts each ``task`` and ``action``
        entity via ``_convert_single_entity``, and writes the results back.
        Updates are staged in a temporary dict to avoid mutating the dict
        during iteration.
        """
        staging: dict[object, Struct] = {}
        for key, value in self._evaluator.all.items():
            if not isinstance(value, Struct):
                continue
            if getattr(value, "kind", None) not in ("task", "action"):
                continue
            staging[key] = self._convert_single_entity(value)
        for key, new_value in staging.items():
            self._evaluator.all[key] = new_value  # type: ignore[index]

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

        # Phase 3: Convert port lists (inputs/outputs/config) on task and action
        # entities to named Structs, enabling pure getattr-based traversal.
        self._convert_ports_to_structs()

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
                    # entity.field_path carries dot-segments after the entity
                    # name (e.g. "outputs.weights" from ":task.outputs.weights").
                    # The parser stores these in field_path; name_parts[1:] is
                    # kept as a legacy fallback for labels parsed without the
                    # core parser's field_path support.
                    if entity.field_path:
                        field_parts = entity.field_path
                    else:
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

                        # Record-traversal branch (design D-3): activates only when
                        # the label has exactly one field-path segment and the resolved
                        # base value is a record-typed value struct.  Multi-segment
                        # paths fall through to the generic _step loop below.
                        #
                        # NOTE: avoid `isinstance(obj, Struct)` here — `Struct` is
                        # re-imported as a local variable later in this function (in the
                        # workspace-attribute branch), which would cause an
                        # UnboundLocalError before that assignment.  Use getattr guards
                        # instead; they are sufficient because only Struct instances carry
                        # `kind` and `type` attributes with string values.
                        obj_type = getattr(obj, "type", None)
                        _is_record_type = (
                            getattr(obj_type, "kind", None) == "record"
                            or getattr(obj_type, "_root_kind", None) == "record"
                        )
                        if (
                            len(field_parts) == 1
                            and getattr(obj, "kind", None) == "value"
                            and _is_record_type
                        ):
                            # Local imports — kept local to limit the impact of new
                            # dependency directions (workspace → resolver is new).
                            # _Struct alias avoids the UnboundLocalError caused by the
                            # `from starlarkish.core.struct import Struct` re-import
                            # in the workspace-attribute branch below, which Python's
                            # compiler treats as a local assignment for the whole function.
                            from starlarkish.core.struct import Struct as _Struct  # noqa: PLC0415
                            from mlody.resolver.label_value import (  # noqa: PLC0415
                                MlodyUnresolvedValue as _MlodyUnresolvedValue,
                            )
                            from mlody.core.label import (  # noqa: PLC0415
                                parse_label as _core_parse_label,
                            )

                            field_name = field_parts[0]
                            value_type = obj.type  # type: ignore[union-attr]

                            # Field lookup order (design D-4):
                            # 1. Search type.fields for a matching entry by name.
                            # 2. Fall back to getattr(value.type, field_name).
                            # 3. If both miss, return MlodyUnresolvedValue.
                            _SENTINEL = object()
                            field_obj: object = _SENTINEL
                            # fields may be a direct struct field (tests) or
                            # inside the `attributes` dict produced by
                            # _make_factory / extend_attrs (real typedefs).
                            _direct_fields = getattr(value_type, "fields", None)
                            _attrs_dict = getattr(value_type, "attributes", None)
                            _attrs_fields = (
                                _attrs_dict.get("fields")
                                if isinstance(_attrs_dict, dict)
                                else None
                            )
                            fields_list: list[object] = list(
                                _direct_fields or _attrs_fields or []
                            )
                            for f in fields_list:
                                if getattr(f, "name", None) == field_name:
                                    field_obj = f
                                    break

                            if field_obj is _SENTINEL:
                                # Not in fields list — try direct type attribute fallback.
                                fallback = getattr(value_type, field_name, _SENTINEL)
                                if fallback is _SENTINEL:
                                    available = [
                                        str(getattr(f, "name", "?")) for f in fields_list
                                    ]
                                    lbl_str = target if isinstance(target, str) else str(target)
                                    lbl_obj = _core_parse_label(lbl_str) if isinstance(lbl_str, str) else lbl_str
                                    return _MlodyUnresolvedValue(
                                        label=lbl_obj,
                                        reason=(
                                            f"field {field_name!r} not found on record type "
                                            f"{getattr(value_type, 'name', '?')!r}; "
                                            f"available fields: {available}"
                                        ),
                                    )
                                return fallback

                            # field_obj is a Struct from type.fields; compose location.
                            # Use object type hints to avoid referencing the locally
                            # re-imported Struct name (see note above on UnboundLocalError).
                            field_loc_obj: object = getattr(field_obj, "location", None)
                            parent_loc_obj: object = getattr(obj, "location", None)
                            try:
                                composed_loc = compose_location(
                                    parent_loc=parent_loc_obj,  # type: ignore[arg-type]
                                    field_loc=field_loc_obj,  # type: ignore[arg-type]
                                    field_name=field_name,
                                )
                            except _LocationComposeError as exc:
                                lbl_str = target if isinstance(target, str) else str(target)
                                lbl_obj = _core_parse_label(lbl_str) if isinstance(lbl_str, str) else lbl_str
                                return _MlodyUnresolvedValue(
                                    label=lbl_obj,
                                    reason=str(exc),
                                )

                            # Return the field struct with its location replaced by
                            # the composed location derived from the parent context.
                            # Use hasattr(_fields) as a Struct duck-type check to avoid
                            # referencing the locally re-imported Struct name.
                            if hasattr(field_obj, "_fields"):
                                updated_fields = dict(field_obj._fields)  # type: ignore[union-attr]
                                updated_fields["location"] = composed_loc
                                return _Struct(**updated_fields)
                            return field_obj

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

    def expand_wildcard_label(self, inner_label: str) -> list[str]:
        """Expand a wildcard inner label into concrete labels (wildcard=False).

        Scans the loaded evaluator registry for all stems matching the wildcard
        pattern and returns one concrete label string per matching stem.

        If the label is not a wildcard, returns ``[inner_label]`` unchanged.
        """
        from mlody.core.label import parse_label as _core_parse_label
        from mlody.core.label.errors import LabelParseError

        try:
            lbl = _core_parse_label(inner_label)
        except LabelParseError:
            return [inner_label]

        if lbl.entity is None or not lbl.entity.wildcard:
            return [inner_label]

        entity = lbl.entity
        base_name = entity.name.split(".")[0] if entity.name else None

        root_prefix: str | None = None
        if entity.root is not None and entity.root in self._root_infos:
            root_prefix = self._root_infos[entity.root].path.lstrip("/").rstrip("/")

        path_suffix = entity.path.lstrip("/").rstrip("/") if entity.path else ""

        stems: set[str] = set()
        for key in self._evaluator.all:
            if not (isinstance(key, tuple) and len(key) == 3):
                continue
            k_stem, k_name = key[1], key[2]
            if not isinstance(k_stem, str):
                continue
            if base_name is not None and k_name != base_name:
                continue
            if root_prefix is not None and not k_stem.startswith(root_prefix):
                continue
            if path_suffix and not k_stem.endswith(path_suffix):
                continue
            stems.add(k_stem)

        result: list[str] = []
        for stem in sorted(stems):
            if root_prefix and stem.startswith(root_prefix):
                rel_path = stem[len(root_prefix):].lstrip("/")
            else:
                rel_path = stem

            parts: list[str] = []
            if entity.root:
                parts.append(f"@{entity.root}//{rel_path}")
            else:
                parts.append(f"//{rel_path}")
            if entity.name:
                parts.append(f":{entity.name}")
            if lbl.attribute_path:
                parts.append(f"'{'.' .join(lbl.attribute_path)}")
            result.append("".join(parts))

        return result
