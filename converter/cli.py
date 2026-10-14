"""Command line interface to convert Fabric notebooks to Jupyter or REPL files and back.

Usage:
    fabric-nb to-ipynb nb_x.Notebook
    fabric-nb to-repl nb_a.Notebook nb_b.Notebook
    fabric-nb to-repl nb_x.Notebook -o repl/nb_x.py
    fabric-nb to-fabric nb_x.ipynb repl/nb_y.py
    fabric-nb to-fabric nb_x.ipynb -o nb_x.Notebook
    fabric-nb setup
    fabric-nb config set workdir C:/projects/fabric
    fabric-nb to-repl nb_x.Notebook -w C:/other/folder

``setup`` asks a few questions and prepares a local Fabric development environment.
``to-fabric`` picks the input format from the file suffix, ``.ipynb`` for Jupyter
notebooks and ``.py`` for REPL scripts in the ``# %%`` percent format.
Relative paths are resolved against the working directory, see ``converter.settings``.
"""

import argparse
import json
import sys
from collections.abc import Callable
from pathlib import Path

from converter.fabric_notebook_converter import (
    FABRIC_CONTENT_FILE,
    NOTEBOOK_FOLDER_SUFFIX,
    FabricNotebookError,
    detect_newline,
    dump_notebook,
    fabric_to_ipynb,
    fabric_to_percent,
    ipynb_to_fabric,
    notebook_name,
    percent_to_fabric,
    read_text,
    resolve_fabric_file,
    write_text,
)
from converter.local_setup import run_setup
from converter.settings import (
    SETTING_KEYS,
    WORKDIR_ENV_VAR,
    WORKDIR_KEY,
    SettingsError,
    config_path,
    get_setting,
    load_settings,
    resolve_path,
    resolve_workdir,
    save_workdir,
    unset_setting,
)

Converter = Callable[[Path, Path | None], Path]


def _local_target(fabric_file: Path, output: Path | None, suffix: str) -> Path:
    """Determine the local file path to write for a Fabric notebook.

    :param Path fabric_file: Path to ``notebook-content.py``.
    :param Path | None output: Target file, optional.
    :param str suffix: File suffix used when ``output`` is not given.
    :returns Path: Target file path, defaults to ``<name><suffix>`` next to the
        ``.Notebook`` folder.
    """
    if output is not None:
        return output
    return fabric_file.resolve().parent.parent / f"{notebook_name(fabric_file)}{suffix}"


def _fabric_target(source: Path, output: Path | None) -> Path:
    """Determine the ``notebook-content.py`` path to write.

    :param Path source: Source Jupyter notebook or REPL script.
    :param Path | None output: ``.Notebook`` folder or content file, optional.
    :returns Path: Target ``notebook-content.py`` path.
    """
    if output is None:
        return source.parent / f"{source.stem}{NOTEBOOK_FOLDER_SUFFIX}" / FABRIC_CONTENT_FILE
    if output.suffix == ".py":
        return output
    return output / FABRIC_CONTENT_FILE


def _warn_missing_platform(target: Path) -> None:
    """Print a warning when the ``.Notebook`` folder has no ``.platform`` file.

    :param Path target: Written ``notebook-content.py`` path.
    """
    if not (target.parent / ".platform").is_file():
        print(f"WARNING {target.parent}: no .platform file, Fabric git sync needs one")


def to_ipynb(source: Path, output: Path | None = None) -> Path:
    """Convert one Fabric notebook to a Jupyter notebook.

    :param Path source: ``.Notebook`` folder or ``notebook-content.py`` path.
    :param Path | None output: Target ``.ipynb`` file. Defaults to
        ``<name>.ipynb`` next to the ``.Notebook`` folder.
    :returns Path: Path of the written notebook.
    :raises FabricNotebookError: If the Fabric notebook cannot be parsed.
    :raises FileNotFoundError: If the Fabric notebook does not exist.
    """
    fabric_file = resolve_fabric_file(source)
    target = _local_target(fabric_file, output, ".ipynb")
    notebook = fabric_to_ipynb(read_text(fabric_file))
    write_text(target, dump_notebook(notebook), detect_newline(target))
    return target


def to_repl(source: Path, output: Path | None = None) -> Path:
    """Convert one Fabric notebook to a REPL script in the percent format.

    :param Path source: ``.Notebook`` folder or ``notebook-content.py`` path.
    :param Path | None output: Target ``.py`` file. Defaults to ``<name>.py``
        next to the ``.Notebook`` folder.
    :returns Path: Path of the written script.
    :raises FabricNotebookError: If the Fabric notebook cannot be parsed.
    :raises FileNotFoundError: If the Fabric notebook does not exist.
    """
    fabric_file = resolve_fabric_file(source)
    target = _local_target(fabric_file, output, ".py")
    if target.resolve() == fabric_file.resolve():
        raise FabricNotebookError("Target is the source file, choose another output")
    write_text(target, fabric_to_percent(read_text(fabric_file)), detect_newline(target))
    return target


def to_fabric(source: Path, output: Path | None = None) -> Path:
    """Convert one Jupyter notebook or REPL script to a Fabric notebook.

    :param Path source: ``.ipynb`` notebook or ``.py`` REPL script.
    :param Path | None output: Target ``.Notebook`` folder or content file.
        Defaults to ``<name>.Notebook`` next to the source.
    :returns Path: Path of the written ``notebook-content.py``.
    :raises FabricNotebookError: If the source has an unsupported suffix, is not
        valid JSON or cannot be parsed.
    """
    suffix = source.suffix.lower()
    if suffix not in {".ipynb", ".py"}:
        raise FabricNotebookError(f"Unsupported file type {source.suffix!r}, use .ipynb or .py")

    target = _fabric_target(source, output)
    if target.resolve() == source.resolve():
        raise FabricNotebookError("Target is the source file, choose another output")

    text = read_text(source)
    if suffix == ".ipynb":
        try:
            notebook = json.loads(text)
        except json.JSONDecodeError as exc:
            raise FabricNotebookError(f"Invalid notebook JSON: {exc}") from exc
        content = ipynb_to_fabric(notebook)
    else:
        content = percent_to_fabric(text)

    write_text(target, content, detect_newline(target))
    _warn_missing_platform(target)
    return target


COMMANDS: dict[str, tuple[Converter, str, str, str]] = {
    "to-ipynb": (
        to_ipynb,
        "Convert Fabric notebooks to Jupyter notebooks.",
        ".Notebook folders or notebook-content.py files",
        "output .ipynb file, single source only",
    ),
    "to-repl": (
        to_repl,
        "Convert Fabric notebooks to REPL scripts in the # %% percent format.",
        ".Notebook folders or notebook-content.py files",
        "output .py file, single source only",
    ),
    "to-fabric": (
        to_fabric,
        "Convert Jupyter notebooks or REPL scripts to Fabric notebooks.",
        ".ipynb notebooks or .py REPL scripts",
        "output .Notebook folder or content file, single source only",
    ),
}


def _workdir_parent() -> argparse.ArgumentParser:
    """Build a parent parser with the shared ``--workdir`` option.

    :returns argparse.ArgumentParser: Parser to pass as ``parents`` to subcommands.
    """
    parent = argparse.ArgumentParser(add_help=False)
    parent.add_argument(
        "-w",
        "--workdir",
        type=Path,
        help=(
            f"folder to resolve relative paths against, overrides {WORKDIR_ENV_VAR} and the "
            "default in config.toml"
        ),
    )
    return parent


def _add_config_parser(subparsers: argparse._SubParsersAction) -> None:
    """Add the ``config`` subcommand to manage the project settings in ``config.toml``.

    :param argparse._SubParsersAction subparsers: Subparsers of the main parser.
    """
    config_help = "Show or change project settings in config.toml, such as the working directory."
    config = subparsers.add_parser("config", help=config_help, description=config_help)
    actions = config.add_subparsers(dest="action", required=True, metavar="ACTION")
    set_parser = actions.add_parser("set", help="store a setting")
    set_parser.add_argument("key", choices=SETTING_KEYS)
    set_parser.add_argument("value")
    get_parser = actions.add_parser("get", help="print a stored setting")
    get_parser.add_argument("key", choices=SETTING_KEYS)
    unset_parser = actions.add_parser("unset", help="remove a stored setting")
    unset_parser.add_argument("key", choices=SETTING_KEYS)
    actions.add_parser("list", help="print all settings and the settings file")


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser with one subcommand per conversion.

    :returns argparse.ArgumentParser: Configured parser.
    """
    parser = argparse.ArgumentParser(
        prog="fabric-nb",
        description="Convert Fabric notebooks to Jupyter notebooks or REPL scripts and back.",
    )
    workdir = _workdir_parent()
    subparsers = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")
    for name, (converter, description, sources_help, output_help) in COMMANDS.items():
        sub = subparsers.add_parser(
            name, help=description, description=description, parents=[workdir]
        )
        sub.add_argument("sources", nargs="+", type=Path, help=sources_help)
        sub.add_argument("-o", "--output", type=Path, help=output_help)
        sub.set_defaults(converter=converter)
    setup_help = "Interactively set up a local Fabric development environment."
    subparsers.add_parser("setup", help=setup_help, description=setup_help, parents=[workdir])
    _add_config_parser(subparsers)
    return parser


def run_config(args: argparse.Namespace) -> int:
    """Run the ``config`` subcommand.

    :param argparse.Namespace args: Parsed arguments with ``action`` and optional
        ``key`` and ``value``.
    :returns int: Exit code, ``1`` if a requested setting is not stored.
    :raises SettingsError: If the settings file is invalid or the folder does not exist.
    :raises OSError: If the settings file cannot be written.
    """
    if args.action == "set":
        value = save_workdir(Path(args.value)) if args.key == WORKDIR_KEY else args.value
        print(f"{args.key} = {value}")
        return 0
    if args.action == "get":
        value = get_setting(args.key)
        if value is None:
            print(f"{args.key} is not set", file=sys.stderr)
            return 1
        print(value)
        return 0
    if args.action == "unset":
        removed = unset_setting(args.key)
        print(f"Removed {args.key}" if removed else f"{args.key} is not set")
        return 0
    print(f"# {config_path()}")
    for key, value in sorted(load_settings().items()):
        print(f"{key} = {value}")
    return 0


def main(argv: list[str] | None = None) -> int:
    """Run the command line interface.

    :param list[str] | None argv: Arguments, defaults to ``sys.argv[1:]``.
    :returns int: Exit code, ``1`` if any source failed.
    """
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "config":
            return run_config(args)
        workdir = resolve_workdir(args.workdir)
    except (SettingsError, OSError) as exc:
        print(f"ERROR {exc}", file=sys.stderr)
        return 1
    if args.command == "setup":
        return run_setup(workdir=workdir)
    if args.output and len(args.sources) > 1:
        parser.error("--output can only be used with a single source")

    converter: Converter = args.converter
    output = resolve_path(args.output, workdir) if args.output else None
    exit_code = 0
    converted = False
    for source in (resolve_path(source, workdir) for source in args.sources):
        try:
            target = converter(source, output)
        except (FabricNotebookError, OSError) as exc:
            print(f"ERROR {source}: {exc}", file=sys.stderr)
            exit_code = 1
        else:
            print(f"{source} -> {target}")
            converted = True
    if converted and converter is not to_fabric:
        print(
            "NOTE The first cell is a local dev setup cell. Set up the local dev environment "
            "(binaryrain-fabric-local-pyspark, python-dotenv, .env) before running it. "
            "Run fabric-nb setup to create it."
        )
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
