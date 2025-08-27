from pathlib import Path

import json
import re
from typing import Any, Dict

# ---------------- Schema preprocessing helpers (OpenAI Structured Outputs subset) ----------------

_ALLOWED_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")


def _sanitize_name(name: str) -> str:
    name = re.sub(r"[^A-Za-z0-9_-]", "_", name or "")
    return name or "schema"


def _merge_dicts_strict(a: Dict[str, Any], b: Dict[str, Any], path: str) -> Dict[str, Any]:
    out = dict(a)
    for k, v in b.items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = _merge_dicts_strict(out[k], v, f"{path}.{k}")
        elif k in out and out[k] != v:
            # Irreconcilable conflict
            raise ValueError(f"Schema conflict at {path}.{k}: {out[k]} vs {v}")
        else:
            out[k] = v
    return out


def _combine_num_bounds(base: Dict[str, Any], other: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(base)
    if "minimum" in other:
        out["minimum"] = max(out.get("minimum", other["minimum"]), other["minimum"])
    if "maximum" in other:
        out["maximum"] = other["maximum"] if "maximum" not in out else min(out["maximum"], other["maximum"])
    return out


def _combine_str_bounds(base: Dict[str, Any], other: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(base)
    if "minLength" in other:
        out["minLength"] = max(out.get("minLength", other["minLength"]), other["minLength"])
    if "maxLength" in other:
        out["maxLength"] = other["maxLength"] if "maxLength" not in out else min(out["maxLength"], other["maxLength"])
    return out


def _combine_enums(base: Dict[str, Any], other: Dict[str, Any]) -> Dict[str, Any]:
    if "enum" in base and "enum" in other:
        inter = list(set(base["enum"]) & set(other["enum"]))
        if not inter:
            raise ValueError("Enum intersection is empty during allOf merge")
        return {**base, "enum": inter}
    return base if "enum" in base else other if "enum" in other else base


def _flatten_allOf(schema: Any, path: str = "$") -> Any:
    # Recurse and flatten object-wise allOf; keep arrays and scalars as-is
    if isinstance(schema, list):
        return [_flatten_allOf(s, f"{path}[]") for s in schema]
    if not isinstance(schema, dict):
        return schema

    # First, recurse into children
    def rec(x: Any, p: str) -> Any:
        return _flatten_allOf(x, p)

    if "allOf" not in schema:
        out: Dict[str, Any] = {}
        for k, v in schema.items():
            if k == "properties" and isinstance(v, dict):
                out[k] = {pk: rec(pv, f"{path}.properties.{pk}") for pk, pv in v.items()}
            elif k == "items":
                out[k] = rec(v, f"{path}.items")
            else:
                out[k] = rec(v, f"{path}.{k}")
        return out

    # Merge each branch of allOf
    parts = schema["allOf"]
    if not isinstance(parts, list) or not parts:
        raise ValueError(f"{path}.allOf must be a non-empty array")

    props: Dict[str, Any] = {}
    required: set[str] = set()
    addl: Any = None  # None=unspecified, True, False
    other_fields: Dict[str, Any] = {}

    for i, raw_part in enumerate(parts):
        part = _flatten_allOf(raw_part, f"{path}.allOf[{i}]")
        if not isinstance(part, dict):
            raise ValueError(f"{path}.allOf[{i}] must be an object")

        # Merge object shape
        if "properties" in part:
            p = part["properties"]
            if not isinstance(p, dict):
                raise ValueError(f"{path}.allOf[{i}].properties must be an object")
            for pname, ps in p.items():
                ps = _flatten_allOf(ps, f"{path}.allOf[{i}].properties.{pname}")
                if pname in props:
                    # deep-merge then conservatively combine constraints
                    merged = _merge_dicts_strict(props[pname], ps, f"{path}.properties.{pname}")
                    t = merged.get("type")
                    if t in {"number", "integer"}:
                        merged = _combine_num_bounds(merged, ps)
                    elif t == "string":
                        merged = _combine_str_bounds(merged, ps)
                    merged = _combine_enums(merged, ps)
                    props[pname] = merged
                else:
                    props[pname] = ps

        if "required" in part:
            r = part["required"]
            if not isinstance(r, list) or not all(isinstance(x, str) for x in r):
                raise ValueError(f"{path}.allOf[{i}].required must be an array of strings")
            required.update(r)

        if "additionalProperties" in part:
            ap = part["additionalProperties"]
            if ap is False:
                addl = False
            elif ap is True or ap is None:
                if addl is None:
                    addl = True
            elif isinstance(ap, dict):
                # SO subset often rejects schema-valued additionalProperties; choose False for safety
                addl = False
            else:
                raise ValueError(f"{path}.allOf[{i}].additionalProperties must be boolean or object")

        # copy over other identical simple fields if no conflict
        for k, v in part.items():
            if k in {"properties", "required", "additionalProperties", "allOf"}:
                continue
            if k in other_fields and other_fields[k] != v:
                raise ValueError(f"Conflicting values for {path}.{k}: {other_fields[k]} vs {v}")
            other_fields[k] = v

    flat: Dict[str, Any] = dict(other_fields)
    if props:
        flat["properties"] = props
    if required:
        flat["required"] = sorted(required)
    if addl is not None:
        flat["additionalProperties"] = addl
    if "type" not in flat and "properties" in flat:
        flat["type"] = "object"

    # Recurse into nested properties once more to remove inner allOf
    if "properties" in flat:
        for pname, ps in list(flat["properties"].items()):
            flat["properties"][pname] = _flatten_allOf(ps, f"{path}.properties.{pname}")

    return flat


def _strip_unsupported_keywords(schema: Any) -> Any:
    # Remove keywords commonly rejected by OpenAI Structured Outputs
    disallowed = {
        "allOf", "oneOf", "not", "$defs", "$ref",
        "patternProperties", "unevaluatedProperties",
        "if", "then", "else", "dependentRequired", "dependentSchemas",
    }
    if isinstance(schema, list):
        return [_strip_unsupported_keywords(s) for s in schema]
    if not isinstance(schema, dict):
        return schema
    out: Dict[str, Any] = {}
    for k, v in schema.items():
        if k in disallowed:
            continue
        if k == "properties" and isinstance(v, dict):
            out[k] = {pk: _strip_unsupported_keywords(pv) for pk, pv in v.items()}
        elif k == "items":
            out[k] = _strip_unsupported_keywords(v)
        else:
            out[k] = _strip_unsupported_keywords(v)
    return out


def _salvage_object_shape(schema: Any) -> Dict[str, Any] | None:
    """
    Traverse the original schema and attempt to recover a usable object shape by
    unioning any discovered 'properties' and 'required' arrays from nested locations
    such as within if/then/else or allOf branches.
    Returns a dict with 'type', 'properties', 'required', and 'additionalProperties' when possible.
    """
    props: Dict[str, Any] = {}
    req: set[str] = set()

    def visit(node: Any):
        if isinstance(node, dict):
            # Collect direct object shape
            if isinstance(node.get("properties"), dict):
                for k, v in node["properties"].items():
                    if k in props:
                        # conservative merge: later entries deep-merge into existing
                        props[k] = _merge_dicts_strict(props[k], v, f"$.properties.{k}")
                    else:
                        props[k] = v
            if isinstance(node.get("required"), list):
                for r in node["required"]:
                    if isinstance(r, str):
                        req.add(r)
            # Recurse into common containers
            for key in ("allOf", "oneOf", "anyOf", "then", "else", "if", "items", "$defs", "schema"):
                child = node.get(key)
                if isinstance(child, list):
                    for i in child:
                        visit(i)
                elif child is not None:
                    visit(child)
            # Recurse into nested properties
            if isinstance(node.get("properties"), dict):
                for v in node["properties"].values():
                    visit(v)
        elif isinstance(node, list):
            for x in node:
                visit(x)

    visit(schema)

    if not props:
        return None

    # Strip unsupported keywords from recovered property schemas
    cleaned_props = {k: _strip_unsupported_keywords(v) for k, v in props.items()}

    result: Dict[str, Any] = {
        "type": "object",
        "properties": cleaned_props,
        "required": sorted(req) if req else [],
        "additionalProperties": False,
    }
    return result


def _preprocess_for_openai(schema: Dict[str, Any]) -> Dict[str, Any]:
    s = _flatten_allOf(schema, "$")
    s = _strip_unsupported_keywords(s)
    # Ensure root is object-typed when object-shaped
    if isinstance(s, dict) and "type" not in s and isinstance(s.get("properties"), dict):
        s["type"] = "object"
    return s


# ----------------------------------- Public API -----------------------------------

def build_schema_object_2(schema_file: Path) -> dict:
    """
    Read a JSON Schema from `schema_file`, preprocess it into the OpenAI Structured Outputs subset,
    and return a dict suitable for `extra_body["text"]["format"]` on the Responses API.
    The returned object has keys: type, name, strict, schema.
    """
    
    if not (schema_file and Path(schema_file).exists()):
        return None
    
    raw_text = Path(schema_file).read_text()
    loaded = json.loads(raw_text)

    if not isinstance(loaded, dict):
        raise ValueError(f"{schema_file} must be a JSON object schema or an envelope with a 'schema' object")

    # Accept either an envelope {name?, strict?, schema: {...}} or a bare schema
    if "schema" in loaded and isinstance(loaded["schema"], dict):
        inner = loaded["schema"]
        provided_name = loaded.get("name")
        provided_strict = bool(loaded.get("strict", True))
    else:
        inner = loaded
        provided_name = None
        provided_strict = True

    # Preprocess for OpenAI subset
    pre = _preprocess_for_openai(inner)

    if not isinstance(pre, dict) or not pre:
        # Attempt to salvage object shape from the original (unprocessed) schema
        salvaged = _salvage_object_shape(inner)
        if salvaged and isinstance(salvaged, dict):
            pre = salvaged
        else:
            raise ValueError(f"{schema_file} produced an empty or invalid schema after preprocessing")
    inner = pre

    # Derive a legal name from the filename if none provided
    name = provided_name or _sanitize_name(schema_file.stem)
    if not _ALLOWED_NAME_PATTERN.match(name):
        name = _sanitize_name(name)

    return {
        "type": "json_schema",
        "name": name,
        "strict": provided_strict,
        "schema": inner,
    }
