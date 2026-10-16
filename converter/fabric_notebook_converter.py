"""Shared logic to convert Fabric notebooks to Jupyter notebooks or REPL scripts and back.

A Fabric notebook is stored in git as ``<name>.Notebook/notebook-content.py``.
The file is a plain Python script with comment markers that separate cells and
metadata blocks. This module parses that format into Jupyter ``.ipynb`` JSON and
renders it back, keeping all Fabric metadata so a round trip is lossless.

REPL scripts use the percent format understood by the VS Code Interactive Window,
Spyder, PyCharm and Jupytext. Cells start with ``# %%`` and markdown cells with
``# %% [markdown]``. Fabric metadata that cannot be derived from the cell content
is stored as ``key=<json>`` pairs on the marker line.
"""

import json
import re
from pathlib import Path
from typing import Any

FABRIC_HEADER = "# Fabric notebook source"
FABRIC_CONTENT_FILE = "notebook-content.py"
NOTEBOOK_FOLDER_SUFFIX = ".Notebook"

MARKER_CELL = "# CELL ********************"
MARKER_PARAMETERS = "# PARAMETERS CELL ********************"
MARKER_MARKDOWN = "# MARKDOWN ********************"
MARKER_METADATA = "# METADATA ********************"
MARKERS = (MARKER_CELL, MARKER_PARAMETERS, MARKER_MARKDOWN, MARKER_METADATA)

META_PREFIX = "# META "
MAGIC_PREFIX = "# MAGIC "
MARKDOWN_PREFIX = "# "

CONVERTER_KEY = "fabric"
PARAMETERS_TAG = "parameters"
DEFAULT_CELL_META: dict[str, str] = {
    "language": "python",
    "language_group": "synapse_pyspark",
}
SQL_CELL_META: dict[str, str] = {
    "language": "sparksql",
    "language_group": "synapse_pyspark",
}
DEFAULT_KERNELSPEC: dict[str, str] = {
    "name": "synapse_pyspark",
    "display_name": "synapse_pyspark",
}
DEFAULT_LANGUAGE_INFO: dict[str, str] = {"name": "python"}
FABRIC_NOTEBOOK_KEYS = ("kernel_info", "dependencies")

PERCENT_HEADER = "# Fabric notebook REPL script, convert back with: fabric-nb to-fabric <file>"
PERCENT_MARKER = "# %%"
PERCENT_MARKDOWN = "[markdown]"
PERCENT_NOTEBOOK_PREFIX = "# fabric-notebook: "
PERCENT_MARKER_RE = re.compile(r"^# %%(?:\s|$)")
PERCENT_KEY_RE = re.compile(r"\s*([A-Za-z_][\w.-]*)=")

LOCAL_DEV_TAG = "local-dev"
LOCAL_DEV_CELL_HEADER = """\
# LOCAL DEVELOPMENT ONLY. Do not change this cell.
# It creates a local Spark session in place of the Fabric runtime.
# `fabric-nb to-fabric` removes every cell tagged "local-dev", so it never reaches Fabric.
import os

from binaryrain_fabric_local_pyspark.display import display
from binaryrain_fabric_local_pyspark.spark import get_local_spark
from dotenv import load_dotenv

load_dotenv()
"""
LOCAL_DEV_CELL_FOOTER = "spark = get_local_spark()\n"
WORKSPACE_ID_ENV = "FABRIC_WORKSPACE_ID"
LAKEHOUSE_ID_ENV = "FABRIC_LAKEHOUSE_ID"
LAKEHOUSE_META_KEYS: dict[str, str] = {
    WORKSPACE_ID_ENV: "default_lakehouse_workspace_id",
    LAKEHOUSE_ID_ENV: "default_lakehouse",
}


class FabricNotebookError(ValueError):
    """Raised when a notebook cannot be parsed or rendered."""


def _strip_blank_edges(lines: list[str]) -> list[str]:
    """Remove leading and trailing empty lines.

    :param list[str] lines: Lines to trim.
    :returns list[str]: Lines without surrounding blank lines.
    """
    start, end = 0, len(lines)
    while start < end and not lines[start].strip():
        start += 1
    while end > start and not lines[end - 1].strip():
        end -= 1
    return lines[start:end]


def _strip_prefix(line: str, prefix: str) -> str:
    """Remove a comment prefix from a line, tolerating a missing trailing space.

    :param str line: Line to process.
    :param str prefix: Prefix such as ``"# MAGIC "``.
    :returns str: Line content without the prefix.
    """
    if line.startswith(prefix):
        return line[len(prefix) :]
    if line == prefix.rstrip():
        return ""
    return line


def _has_prefix(line: str, prefix: str) -> bool:
    """Check whether a line carries a comment prefix, with or without trailing space.

    :param str line: Line to check.
    :param str prefix: Prefix such as ``"# MAGIC "``.
    :returns bool: True if the line is prefixed.
    """
    return line.startswith(prefix) or line == prefix.rstrip()


def _parse_meta(lines: list[str]) -> dict[str, Any]:
    """Parse the JSON payload of a ``# META`` block.

    :param list[str] lines: Lines of the METADATA section.
    :returns dict[str, Any]: Parsed metadata.
    :raises FabricNotebookError: If the block is not valid JSON.
    """
    payload = "\n".join(_strip_prefix(line, META_PREFIX) for line in _strip_blank_edges(lines))
    try:
        return json.loads(payload)
    except json.JSONDecodeError as exc:
        raise FabricNotebookError(f"Invalid METADATA block: {exc}") from exc


def _split_sections(lines: list[str]) -> list[tuple[str, list[str]]]:
    """Split Fabric notebook lines into marker sections.

    Only the single separator blank line after a marker and before the next marker
    is removed. Any further blank lines belong to the section body.

    :param list[str] lines: Lines of the file after the header line.
    :returns list[tuple[str, list[str]]]: Marker and body per section.
    :raises FabricNotebookError: If content appears before the first marker.
    """
    sections: list[tuple[str, list[str]]] = []
    for line in lines:
        if line in MARKERS:
            if sections and sections[-1][1] and sections[-1][1][-1] == "":
                sections[-1][1].pop()
            sections.append((line, []))
        elif sections:
            sections[-1][1].append(line)
        elif line.strip():
            raise FabricNotebookError(f"Unexpected content before first marker: {line!r}")
    return [(marker, body[1:] if body[:1] == [""] else body) for marker, body in sections]


def _split_edges(body: list[str]) -> tuple[list[str], list[str], list[str]]:
    """Split a section body into leading blank lines, content and trailing blank lines.

    :param list[str] body: Section body.
    :returns tuple[list[str], list[str], list[str]]: Leading, content and trailing lines.
    """
    content = _strip_blank_edges(body)
    if not content:
        return body, [], []
    start = next(index for index, line in enumerate(body) if line.strip())
    end = start + len(content)
    return body[:start], content, body[end:]


def _make_cell(cell_type: str, body: list[str], prefix: str | None) -> dict[str, Any]:
    """Build a Jupyter cell and keep Fabric layout details in its metadata.

    :param str cell_type: ``"code"`` or ``"markdown"``.
    :param list[str] body: Section body as stored in the Fabric file.
    :param str | None prefix: Comment prefix to remove from content lines, if any.
    :returns dict[str, Any]: Jupyter cell.
    """
    leading, content, trailing = _split_edges(body)
    lines = [_strip_prefix(line, prefix) for line in content] if prefix else content
    converter: dict[str, Any] = {"meta": None, "magic": prefix == MAGIC_PREFIX}
    if leading:
        converter["leading_lines"] = leading
    if trailing:
        converter["trailing_lines"] = trailing
    cell: dict[str, Any] = {
        "cell_type": cell_type,
        "metadata": {CONVERTER_KEY: converter},
        "source": "\n".join(lines).splitlines(keepends=True),
    }
    if cell_type == "code":
        cell["execution_count"] = None
        cell["outputs"] = []
    return cell


def _parse_code_cell(body: list[str], *, parameters: bool) -> dict[str, Any]:
    """Parse a CELL or PARAMETERS CELL section into a Jupyter code cell.

    :param list[str] body: Section body.
    :param bool parameters: Whether the section is a parameters cell.
    :returns dict[str, Any]: Jupyter code cell.
    """
    content = _strip_blank_edges(body)
    magic = bool(content) and all(_has_prefix(line, MAGIC_PREFIX) for line in content)
    cell = _make_cell("code", body, MAGIC_PREFIX if magic else None)
    if parameters:
        cell["metadata"]["tags"] = [PARAMETERS_TAG]
    return cell


def _lakehouse_env(notebook_meta: dict[str, Any]) -> dict[str, str]:
    """Read the default lakehouse and workspace IDs from Fabric notebook metadata.

    :param dict[str, Any] notebook_meta: Fabric notebook level metadata.
    :returns dict[str, str]: Environment variable names mapped to IDs. IDs missing
        from the metadata are left out.
    """
    lakehouse = notebook_meta.get("dependencies", {}).get("lakehouse", {})
    if not isinstance(lakehouse, dict):
        return {}
    return {
        env: str(lakehouse[key]) for env, key in LAKEHOUSE_META_KEYS.items() if lakehouse.get(key)
    }


def _local_dev_cell_source(notebook_meta: dict[str, Any]) -> str:
    """Build the source of the local development setup cell.

    The lakehouse and workspace IDs are set after ``load_dotenv()`` so the values from
    the notebook take precedence over ``.env``.

    :param dict[str, Any] notebook_meta: Fabric notebook level metadata.
    :returns str: Python source of the cell.
    """
    env_lines = "".join(
        f"os.environ[{json.dumps(name)}] = {json.dumps(value)}\n"
        for name, value in _lakehouse_env(notebook_meta).items()
    )
    return LOCAL_DEV_CELL_HEADER + env_lines + LOCAL_DEV_CELL_FOOTER


def _local_dev_cell(notebook_meta: dict[str, Any]) -> dict[str, Any]:
    """Build the code cell that sets up a local Spark session for development.

    :param dict[str, Any] notebook_meta: Fabric notebook level metadata.
    :returns dict[str, Any]: Jupyter code cell tagged with ``LOCAL_DEV_TAG``.
    """
    return {
        "cell_type": "code",
        "metadata": {"tags": [LOCAL_DEV_TAG]},
        "source": _local_dev_cell_source(notebook_meta).splitlines(keepends=True),
        "execution_count": None,
        "outputs": [],
    }


def _is_local_dev_cell(cell: dict[str, Any]) -> bool:
    """Return whether a cell is the local development setup cell.

    :param dict[str, Any] cell: Jupyter cell.
    :returns bool: True if the cell carries the ``LOCAL_DEV_TAG`` tag.
    """
    return LOCAL_DEV_TAG in cell.get("metadata", {}).get("tags", [])


def fabric_to_ipynb(text: str) -> dict[str, Any]:
    """Convert Fabric ``notebook-content.py`` text to a Jupyter notebook.

    The local development setup cell is added as the first cell.

    :param str text: Content of the Fabric notebook file.
    :returns dict[str, Any]: Jupyter notebook in nbformat 4 JSON structure.
    :raises FabricNotebookError: If the text is not a Fabric notebook.
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != FABRIC_HEADER:
        raise FabricNotebookError(f"Missing header line {FABRIC_HEADER!r}")

    notebook_meta: dict[str, Any] | None = None
    cells: list[dict[str, Any]] = []
    for marker, body in _split_sections(lines[1:]):
        if marker == MARKER_METADATA:
            meta = _parse_meta(body)
            if not cells and notebook_meta is None:
                notebook_meta = meta
            elif cells and cells[-1]["metadata"][CONVERTER_KEY]["meta"] is None:
                cells[-1]["metadata"][CONVERTER_KEY]["meta"] = meta
            else:
                raise FabricNotebookError("METADATA block without a preceding cell")
        elif marker == MARKER_MARKDOWN:
            cells.append(_make_cell("markdown", body, MARKDOWN_PREFIX))
        else:
            cells.append(_parse_code_cell(body, parameters=marker == MARKER_PARAMETERS))

    notebook_meta = notebook_meta or {}
    cells.insert(0, _local_dev_cell(notebook_meta))
    return _build_notebook(cells, notebook_meta)


def _build_notebook(cells: list[dict[str, Any]], notebook_meta: dict[str, Any]) -> dict[str, Any]:
    """Assemble a Jupyter notebook from cells and Fabric notebook metadata.

    :param list[dict[str, Any]] cells: Jupyter cells.
    :param dict[str, Any] notebook_meta: Fabric notebook level metadata.
    :returns dict[str, Any]: Jupyter notebook in nbformat 4 JSON structure.
    """
    metadata: dict[str, Any] = dict(notebook_meta)
    metadata.setdefault("kernelspec", dict(DEFAULT_KERNELSPEC))
    metadata.setdefault("language_info", dict(DEFAULT_LANGUAGE_INFO))
    metadata[CONVERTER_KEY] = {"notebook_metadata_keys": list(notebook_meta)}
    return {"cells": cells, "metadata": metadata, "nbformat": 4, "nbformat_minor": 5}


def _render_meta(meta: dict[str, Any]) -> list[str]:
    """Render metadata as ``# META`` comment lines.

    :param dict[str, Any] meta: Metadata to render.
    :returns list[str]: Prefixed JSON lines.
    """
    dumped = json.dumps(meta, indent=2, ensure_ascii=False)
    return [f"{META_PREFIX}{line}" for line in dumped.splitlines()]


def _cell_source(cell: dict[str, Any]) -> list[str]:
    """Return the source of a Jupyter cell as lines without newline characters.

    :param dict[str, Any] cell: Jupyter cell.
    :returns list[str]: Source lines.
    """
    source = cell.get("source", "")
    text = "".join(source) if isinstance(source, list) else source
    return _strip_blank_edges(text.splitlines())


def _notebook_meta(metadata: dict[str, Any]) -> dict[str, Any]:
    """Extract the Fabric notebook level metadata from Jupyter metadata.

    :param dict[str, Any] metadata: Jupyter notebook metadata.
    :returns dict[str, Any]: Fabric notebook metadata.
    """
    converter = metadata.get(CONVERTER_KEY) or {}
    keys = converter.get("notebook_metadata_keys")
    if keys is None:
        keys = [key for key in FABRIC_NOTEBOOK_KEYS if key in metadata]
    meta = {key: metadata[key] for key in keys if key in metadata}
    meta.setdefault("kernel_info", {"name": "synapse_pyspark"})
    return meta


def _wrap_edges(fabric: dict[str, Any], lines: list[str]) -> list[str]:
    """Restore blank lines around a cell body that were recorded during parsing.

    :param dict[str, Any] fabric: Converter metadata of the cell.
    :param list[str] lines: Rendered content lines.
    :returns list[str]: Content lines with original leading and trailing blank lines.
    """
    leading = fabric.get("leading_lines") or []
    trailing = fabric.get("trailing_lines") or []
    return [*leading, *lines, *trailing]


def _default_cell_meta(lines: list[str]) -> dict[str, str]:
    """Return the Fabric cell metadata implied by the cell source.

    :param list[str] lines: Cell source lines.
    :returns dict[str, str]: SQL metadata for ``%%sql`` cells, otherwise Python metadata.
    """
    is_sql = bool(lines) and lines[0].strip().lower().startswith("%%sql")
    return SQL_CELL_META if is_sql else DEFAULT_CELL_META


def _default_magic(meta: dict[str, Any]) -> bool:
    """Return whether a code cell is stored with ``# MAGIC`` prefixes by default.

    :param dict[str, Any] meta: Fabric cell metadata.
    :returns bool: True for every language other than Python.
    """
    return meta.get("language") != DEFAULT_CELL_META["language"]


def _render_code_cell(cell: dict[str, Any], lines: list[str]) -> list[str]:
    """Render a Jupyter code cell as Fabric lines.

    :param dict[str, Any] cell: Jupyter code cell.
    :param list[str] lines: Cell source lines.
    :returns list[str]: Fabric lines for the cell and its metadata block.
    """
    metadata = cell.get("metadata", {})
    fabric = metadata.get(CONVERTER_KEY) or {}
    meta = fabric.get("meta") or _default_cell_meta(lines)
    magic = fabric.get("magic", _default_magic(meta))
    marker = MARKER_PARAMETERS if PARAMETERS_TAG in metadata.get("tags", []) else MARKER_CELL

    if magic:
        lines = [f"{MAGIC_PREFIX}{line}" for line in lines]
    body = _wrap_edges(fabric, lines)
    return [marker, "", *body, "", MARKER_METADATA, "", *_render_meta(meta)]


def _render_markdown_cell(cell: dict[str, Any], lines: list[str]) -> list[str]:
    """Render a Jupyter markdown cell as Fabric lines.

    :param dict[str, Any] cell: Jupyter markdown cell.
    :param list[str] lines: Cell source lines.
    :returns list[str]: Fabric lines for the cell and its optional metadata block.
    """
    fabric = cell.get("metadata", {}).get(CONVERTER_KEY) or {}
    body = _wrap_edges(fabric, [f"{MARKDOWN_PREFIX}{line}" for line in lines])
    out = [MARKER_MARKDOWN, "", *body]
    meta = fabric.get("meta")
    if meta:
        out.extend(["", MARKER_METADATA, "", *_render_meta(meta)])
    return out


def ipynb_to_fabric(notebook: dict[str, Any]) -> str:
    """Convert a Jupyter notebook to Fabric ``notebook-content.py`` text.

    Raw cells are skipped because Fabric has no equivalent. Cells tagged with
    ``LOCAL_DEV_TAG`` are skipped because they only run locally.

    :param dict[str, Any] notebook: Jupyter notebook in nbformat 4 JSON structure.
    :returns str: Fabric notebook text using ``\\n`` line endings.
    :raises FabricNotebookError: If the notebook contains unsupported cells.
    """
    out = [FABRIC_HEADER, "", MARKER_METADATA, ""]
    out.extend(_render_meta(_notebook_meta(notebook.get("metadata", {}))))

    for cell in notebook.get("cells", []):
        if _is_local_dev_cell(cell):
            continue
        cell_type = cell.get("cell_type")
        lines = _cell_source(cell)
        if cell_type == "markdown":
            out.extend(["", *_render_markdown_cell(cell, lines)])
        elif cell_type == "code":
            out.extend(["", *_render_code_cell(cell, lines)])
        elif cell_type != "raw":
            raise FabricNotebookError(f"Unsupported cell type: {cell_type!r}")
    return "\n".join(out) + "\n"


def _same_items(left: dict[str, Any] | None, right: dict[str, Any]) -> bool:
    """Compare two dicts including key order, since order is visible in the Fabric file.

    :param dict[str, Any] | None left: First dict.
    :param dict[str, Any] right: Second dict.
    :returns bool: True if both dicts have the same items in the same order.
    """
    return left is not None and list(left.items()) == list(right.items())


def _percent_fabric_meta(cell: dict[str, Any], lines: list[str]) -> dict[str, Any]:
    """Return the converter metadata of a cell without values implied by its content.

    :param dict[str, Any] cell: Jupyter cell.
    :param list[str] lines: Cell source lines.
    :returns dict[str, Any]: Converter metadata that must be stored on the marker line.
    """
    fabric = dict(cell.get("metadata", {}).get(CONVERTER_KEY) or {})
    if cell.get("cell_type") == "code":
        meta = fabric.get("meta") or _default_cell_meta(lines)
        if not fabric.get("meta") or _same_items(fabric["meta"], _default_cell_meta(lines)):
            fabric.pop("meta", None)
        if fabric.get("magic", _default_magic(meta)) == _default_magic(meta):
            fabric.pop("magic", None)
    else:
        if not fabric.get("meta"):
            fabric.pop("meta", None)
        if not fabric.get("magic"):
            fabric.pop("magic", None)
    return fabric


def _render_percent_marker(cell: dict[str, Any], lines: list[str]) -> str:
    """Render the ``# %%`` marker line of a cell including its metadata pairs.

    :param dict[str, Any] cell: Jupyter cell.
    :param list[str] lines: Cell source lines.
    :returns str: Marker line.
    :raises FabricNotebookError: If a metadata key cannot be written as ``key=<json>``.
    """
    parts = [PERCENT_MARKER]
    if cell.get("cell_type") == "markdown":
        parts.append(PERCENT_MARKDOWN)
    metadata = dict(cell.get("metadata", {}))
    metadata[CONVERTER_KEY] = _percent_fabric_meta(cell, lines)
    for key, value in metadata.items():
        if key == CONVERTER_KEY and not value:
            continue
        if not PERCENT_KEY_RE.fullmatch(f"{key}="):
            raise FabricNotebookError(f"Cell metadata key not supported in REPL script: {key!r}")
        parts.append(f"{key}={json.dumps(value, separators=(',', ':'), ensure_ascii=False)}")
    return " ".join(parts)


def ipynb_to_percent(notebook: dict[str, Any]) -> str:
    """Convert a Jupyter notebook to a percent format REPL script.

    Raw cells are skipped because Fabric has no equivalent.

    :param dict[str, Any] notebook: Jupyter notebook in nbformat 4 JSON structure.
    :returns str: REPL script text using ``\\n`` line endings.
    :raises FabricNotebookError: If the notebook contains unsupported cells or code
        lines that would be read as cell markers.
    """
    notebook_meta = _notebook_meta(notebook.get("metadata", {}))
    out = [
        PERCENT_HEADER,
        f"{PERCENT_NOTEBOOK_PREFIX}{json.dumps(notebook_meta, ensure_ascii=False)}",
    ]
    for cell in notebook.get("cells", []):
        cell_type = cell.get("cell_type")
        lines = _cell_source(cell)
        if cell_type == "markdown":
            body = [f"{MARKDOWN_PREFIX}{line}" if line else "#" for line in lines]
        elif cell_type == "code":
            body = lines
        elif cell_type == "raw":
            continue
        else:
            raise FabricNotebookError(f"Unsupported cell type: {cell_type!r}")
        if any(PERCENT_MARKER_RE.match(line) for line in body):
            raise FabricNotebookError(f"Cell contains a line read as {PERCENT_MARKER!r} marker")
        out.extend(["", _render_percent_marker(cell, lines), *body])
    return "\n".join(out) + "\n"


def _parse_percent_marker(line: str) -> tuple[str, dict[str, Any]]:
    """Parse a ``# %%`` marker line into the cell type and cell metadata.

    Text that is neither a cell type tag nor a ``key=<json>`` pair is treated as a
    cell title and ignored.

    :param str line: Marker line.
    :returns tuple[str, dict[str, Any]]: Cell type and cell metadata.
    :raises FabricNotebookError: If a metadata value is not valid JSON.
    """
    rest = line[len(PERCENT_MARKER) :]
    decoder = json.JSONDecoder()
    cell_type, metadata, pos = "code", {}, 0
    while pos < len(rest):
        match = PERCENT_KEY_RE.match(rest, pos)
        if match:
            try:
                value, pos = decoder.raw_decode(rest, match.end())
            except json.JSONDecodeError as exc:
                raise FabricNotebookError(f"Invalid metadata in line {line!r}: {exc}") from exc
            metadata[match.group(1)] = value
            continue
        token = rest[pos:].split(maxsplit=1)
        if not token:
            break
        if token[0] in (PERCENT_MARKDOWN, "[md]"):
            cell_type = "markdown"
        pos = rest.index(token[0], pos) + len(token[0])
    return cell_type, metadata


def _unwrap_markdown_string(lines: list[str]) -> list[str] | None:
    """Extract markdown written as a triple quoted string, as Jupytext allows.

    :param list[str] lines: Markdown cell body without surrounding blank lines.
    :returns list[str] | None: Unwrapped markdown lines, or None if the body is not a
        single triple quoted string.
    """
    if not lines:
        return None
    first, last = lines[0].lstrip(), lines[-1].rstrip()
    for quote in ('"""', "'''"):
        if not (first.startswith(quote) and last.endswith(quote)):
            continue
        if len(lines) == 1:
            inner = first.rstrip()[len(quote) : -len(quote)]
            return None if quote in inner else [inner]
        if quote in first[len(quote) :] or quote in last[: -len(quote)]:
            return None
        if any(quote in line for line in lines[1:-1]):
            return None
        body = [first[len(quote) :], *lines[1:-1], last[: -len(quote)]]
        return _strip_blank_edges(body)
    return None


def _make_percent_cell(marker: str, body: list[str]) -> dict[str, Any]:
    """Build a Jupyter cell from a percent marker line and its body.

    :param str marker: ``# %%`` marker line.
    :param list[str] body: Lines between this marker and the next one.
    :returns dict[str, Any]: Jupyter cell with complete converter metadata.
    """
    cell_type, metadata = _parse_percent_marker(marker)
    if body and not body[-1].strip():
        body = body[:-1]
    lines = _strip_blank_edges(body)
    if cell_type == "markdown":
        unwrapped = _unwrap_markdown_string(lines)
        if unwrapped is None:
            lines = _strip_blank_edges([_strip_prefix(line, MARKDOWN_PREFIX) for line in body])
        else:
            lines = unwrapped

    fabric = dict(metadata.get(CONVERTER_KEY) or {})
    if cell_type == "code":
        meta = fabric.get("meta") or _default_cell_meta(lines)
        magic = fabric.get("magic", _default_magic(meta))
    else:
        meta = fabric.get("meta")
        magic = fabric.get("magic", False)
    metadata[CONVERTER_KEY] = {"meta": meta, "magic": magic, **fabric}

    cell: dict[str, Any] = {
        "cell_type": cell_type,
        "metadata": metadata,
        "source": "\n".join(lines).splitlines(keepends=True),
    }
    if cell_type == "code":
        cell["execution_count"] = None
        cell["outputs"] = []
    return cell


def percent_to_ipynb(text: str) -> dict[str, Any]:
    """Convert a percent format REPL script to a Jupyter notebook.

    :param str text: Content of the REPL script.
    :returns dict[str, Any]: Jupyter notebook in nbformat 4 JSON structure.
    :raises FabricNotebookError: If code appears before the first cell marker or the
        notebook metadata is not valid JSON.
    """
    notebook_meta: dict[str, Any] = {}
    sections: list[tuple[str, list[str]]] = []
    for line in text.splitlines():
        if PERCENT_MARKER_RE.match(line):
            sections.append((line, []))
        elif sections:
            sections[-1][1].append(line)
        elif line.startswith(PERCENT_NOTEBOOK_PREFIX):
            try:
                notebook_meta = json.loads(line[len(PERCENT_NOTEBOOK_PREFIX) :])
            except json.JSONDecodeError as exc:
                raise FabricNotebookError(f"Invalid notebook metadata: {exc}") from exc
        elif line.strip() and not line.startswith("#"):
            raise FabricNotebookError(f"Unexpected code before first cell marker: {line!r}")

    cells = [_make_percent_cell(marker, body) for marker, body in sections]
    return _build_notebook(cells, notebook_meta)


def fabric_to_percent(text: str) -> str:
    """Convert Fabric ``notebook-content.py`` text to a percent format REPL script.

    :param str text: Content of the Fabric notebook file.
    :returns str: REPL script text using ``\\n`` line endings.
    :raises FabricNotebookError: If the text is not a Fabric notebook.
    """
    return ipynb_to_percent(fabric_to_ipynb(text))


def percent_to_fabric(text: str) -> str:
    """Convert a percent format REPL script to Fabric ``notebook-content.py`` text.

    :param str text: Content of the REPL script.
    :returns str: Fabric notebook text using ``\\n`` line endings.
    :raises FabricNotebookError: If the script cannot be parsed.
    """
    return ipynb_to_fabric(percent_to_ipynb(text))


def resolve_fabric_file(path: Path) -> Path:
    """Resolve a ``.Notebook`` folder or content file to the content file path.

    :param Path path: Folder or ``notebook-content.py`` path.
    :returns Path: Path to ``notebook-content.py``.
    :raises FileNotFoundError: If the content file does not exist.
    """
    file_path = path / FABRIC_CONTENT_FILE if path.is_dir() else path
    if not file_path.is_file():
        raise FileNotFoundError(f"Fabric notebook not found: {file_path}")
    return file_path


def notebook_name(fabric_file: Path) -> str:
    """Derive the notebook name from its ``.Notebook`` folder.

    :param Path fabric_file: Path to ``notebook-content.py``.
    :returns str: Notebook name without the ``.Notebook`` suffix.
    """
    folder = fabric_file.resolve().parent.name
    return folder.removesuffix(NOTEBOOK_FOLDER_SUFFIX) or fabric_file.stem


def detect_newline(path: Path, default: str = "\n") -> str:
    """Detect the newline style of an existing file.

    :param Path path: File to inspect.
    :param str default: Newline used when the file does not exist.
    :returns str: ``"\\r\\n"`` or ``"\\n"``.
    """
    if not path.is_file():
        return default
    return "\r\n" if b"\r\n" in path.read_bytes() else "\n"


def read_text(path: Path) -> str:
    """Read UTF-8 text and normalize line endings to ``\\n``.

    :param Path path: File to read.
    :returns str: File content.
    """
    return path.read_text(encoding="utf-8-sig").replace("\r\n", "\n")


def write_text(path: Path, text: str, newline: str = "\n") -> None:
    """Write UTF-8 text with the given newline style, creating parent folders.

    :param Path path: Target file.
    :param str text: Content with ``\\n`` line endings.
    :param str newline: Newline style to write.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline=newline) as handle:
        handle.write(text)


def dump_notebook(notebook: dict[str, Any]) -> str:
    """Serialize a notebook the same way Jupyter does.

    :param dict[str, Any] notebook: Jupyter notebook.
    :returns str: JSON text with a trailing newline.
    """
    return json.dumps(notebook, indent=1, ensure_ascii=False) + "\n"
