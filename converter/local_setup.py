"""Interactive setup of a local Fabric development environment.

Asks for the target folder, the package manager, the SQL Analytics Endpoint and the
default Lakehouse. Then writes ``requirements.txt``, ``.env`` and ``.gitignore``, installs
the packages with ``uv`` or a ``python`` virtual environment and runs ``az login``.
As a last step it asks whether to set a working directory in ``config.toml``.
"""

import os
import re
import shutil
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from converter.settings import (
    WORKDIR_KEY,
    SettingsError,
    config_path,
    get_setting,
    resolve_path,
    save_workdir,
)

REQUIREMENTS_FILE = "requirements.txt"
ENV_FILE = ".env"
GITIGNORE_FILE = ".gitignore"
REQUIREMENTS = ("python-dotenv", "binaryrain-fabric-local-pyspark")
GITIGNORE_ENTRIES = (".cache/", ".venv/", "__pycache__/", ".env")
PACKAGE_MANAGERS = ("uv", "python")

Prompt = Callable[[str], str]


class SetupError(Exception):
    """Raised when the local setup cannot be completed."""


@dataclass(frozen=True)
class SetupConfig:
    """Answers collected by the interactive setup.

    :param Path target: Folder of the local Fabric setup.
    :param str manager: Package manager, ``uv`` or ``python``.
    :param str server: SQL Analytics Endpoint of Fabric.
    :param str database: Name of the default Fabric Lakehouse.
    """

    target: Path
    manager: str
    server: str
    database: str


def _ask(prompt: Prompt, question: str, default: str | None = None) -> str:
    """Ask a question until a non empty answer is given.

    :param Prompt prompt: Function that shows the question and returns the answer.
    :param str question: Question text.
    :param str | None default: Answer used when the user just presses enter. Default is None.
    :returns str: Stripped answer.
    """
    suffix = f" [{default}]" if default else ""
    while True:
        answer = prompt(f"{question}{suffix}: ").strip()
        if answer:
            return answer
        if default:
            return default
        print("A value is required.")


def _ask_choice(prompt: Prompt, question: str, choices: Sequence[str], default: str) -> str:
    """Ask a question until one of the allowed choices is given.

    :param Prompt prompt: Function that shows the question and returns the answer.
    :param str question: Question text.
    :param Sequence[str] choices: Allowed lower case answers.
    :param str default: Answer used when the user just presses enter.
    :returns str: Chosen answer in lower case.
    """
    while True:
        answer = _ask(prompt, f"{question} ({'/'.join(choices)})", default).lower()
        if answer in choices:
            return answer
        print(f"Choose one of: {', '.join(choices)}")


def ask_config(prompt: Prompt = input, workdir: Path | None = None) -> SetupConfig:
    """Collect the setup answers from the user.

    :param Prompt prompt: Function that shows a question and returns the answer.
        Default is ``input``.
    :param Path | None workdir: Working directory, used as the default target and to
        resolve a relative target. Default is None, the current directory.
    :returns SetupConfig: Collected answers with a resolved target folder.
    """
    workdir = workdir or Path.cwd()
    answer = _ask(prompt, "Where should the local Fabric setup take place", str(workdir))
    target = resolve_path(Path(answer), workdir).resolve()
    default_manager = "uv" if shutil.which("uv") else "python"
    manager = _ask_choice(prompt, "Use uv or python", PACKAGE_MANAGERS, default_manager)
    server = _ask(prompt, "SQL Analytics Endpoint of Fabric")
    database = _ask(prompt, "Name of the default Fabric Lakehouse")
    return SetupConfig(target, manager, server, database)


def ask_workdir(prompt: Prompt, default: Path, base: Path) -> Path | None:
    """Ask whether to set a working directory in ``config.toml`` and which folder.

    :param Prompt prompt: Function that shows a question and returns the answer.
    :param Path default: Folder suggested as the working directory.
    :param Path base: Folder a relative answer is resolved against.
    :returns Path | None: Chosen absolute folder, ``None`` if the user declined.
    :raises SettingsError: If ``config.toml`` is invalid.
    """
    current = get_setting(WORKDIR_KEY)
    note = f", currently {current}" if current else ""
    question = f"Set a working directory in {config_path()}{note}"
    if _ask_choice(prompt, question, ("y", "n"), "y") == "n":
        return None
    answer = _ask(prompt, "Working directory", str(default))
    return resolve_path(Path(answer), base).resolve()


def _requirement_name(line: str) -> str:
    """Extract the normalized package name from a requirements line.

    :param str line: Line of a requirements file.
    :returns str: Lower case package name with ``_`` replaced by ``-``.
    """
    name = re.split(r"[\s<>=!~;\[@]", line.strip(), maxsplit=1)[0]
    return name.lower().replace("_", "-")


def write_requirements(target: Path) -> Path:
    """Write ``requirements.txt`` and keep entries of an existing file.

    :param Path target: Folder of the local Fabric setup.
    :returns Path: Path of the written file.
    :raises OSError: If the file cannot be read or written.
    """
    path = target / REQUIREMENTS_FILE
    lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    present = {_requirement_name(line) for line in lines if line.strip()}
    lines.extend(package for package in REQUIREMENTS if package not in present)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return path


def write_env(target: Path, server: str, database: str) -> Path:
    """Write ``FABRIC_SERVER`` and ``FABRIC_DATABASE`` to ``.env``.

    Existing keys are replaced, other lines of an existing file are kept.

    :param Path target: Folder of the local Fabric setup.
    :param str server: SQL Analytics Endpoint of Fabric.
    :param str database: Name of the default Fabric Lakehouse.
    :returns Path: Path of the written file.
    :raises OSError: If the file cannot be read or written.
    """
    path = target / ENV_FILE
    values = {"FABRIC_SERVER": server, "FABRIC_DEFAULT_DATABASE": database}
    lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    for index, line in enumerate(lines):
        key = line.split("=", 1)[0].strip()
        if key in values:
            lines[index] = f"{key}={values.pop(key)}"
    lines.extend(f"{key}={value}" for key, value in values.items())
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return path


def write_gitignore(target: Path) -> Path:
    """Write ``.gitignore`` and keep entries of an existing file.

    :param Path target: Folder of the local Fabric setup.
    :returns Path: Path of the written file.
    :raises OSError: If the file cannot be read or written.
    """
    path = target / GITIGNORE_FILE
    lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    present = {line.strip() for line in lines}
    lines.extend(entry for entry in GITIGNORE_ENTRIES if entry not in present)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return path


def _run(command: list[str], cwd: Path) -> None:
    """Run a command and stop the setup when it fails.

    The executable is resolved with ``shutil.which`` so Windows wrappers such as
    ``az.cmd`` are found.

    :param list[str] command: Command and arguments.
    :param Path cwd: Working directory.
    :raises SetupError: If the command is missing or exits with an error.
    """
    print(f"> {' '.join(command)}")
    executable = shutil.which(command[0]) or command[0]
    try:
        subprocess.run([executable, *command[1:]], cwd=cwd, check=True)
    except FileNotFoundError as exc:
        raise SetupError(f"Command not found: {command[0]}") from exc
    except subprocess.CalledProcessError as exc:
        raise SetupError(f"{' '.join(command)} failed with exit code {exc.returncode}") from exc


def install_with_uv(target: Path) -> None:
    """Create a uv project and add the requirements.

    Skips ``uv init`` when the folder already has a ``pyproject.toml``.

    :param Path target: Folder of the local Fabric setup.
    :raises SetupError: If a uv command fails.
    """
    if (target / "pyproject.toml").is_file():
        print("pyproject.toml exists, skipping uv init")
    else:
        _run(["uv", "init"], target)
    _run(["uv", "add", "-r", REQUIREMENTS_FILE], target)


def install_with_python(target: Path) -> None:
    """Create a ``.venv`` virtual environment and pip install the requirements.

    Reuses an existing ``.venv`` folder.

    :param Path target: Folder of the local Fabric setup.
    :raises SetupError: If creating the environment or installing fails.
    """
    venv = target / ".venv"
    if not venv.is_dir():
        _run([sys.executable, "-m", "venv", ".venv"], target)
    python = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    _run([str(python), "-m", "pip", "install", "-r", REQUIREMENTS_FILE], target)


def azure_login(target: Path) -> None:
    """Sign in to Azure with the Azure CLI.

    :param Path target: Folder of the local Fabric setup.
    :raises SetupError: If the Azure CLI is missing or the login fails.
    """
    if shutil.which("az") is None:
        raise SetupError(
            "Azure CLI not found. Install it from https://aka.ms/azure-cli and run az login"
        )
    _run(["az", "login"], target)


def run_setup(prompt: Prompt = input, workdir: Path | None = None) -> int:
    """Run the interactive local Fabric setup.

    :param Prompt prompt: Function that shows a question and returns the answer.
        Default is ``input``.
    :param Path | None workdir: Working directory, used as the default target.
        Default is None, the current directory.
    :returns int: Exit code, ``1`` if the setup was cancelled or failed.
    """
    try:
        config = ask_config(prompt, workdir)
    except (EOFError, KeyboardInterrupt):
        print("\nSetup cancelled", file=sys.stderr)
        return 1
    except SettingsError as exc:
        print(f"ERROR {exc}", file=sys.stderr)
        return 1

    try:
        config.target.mkdir(parents=True, exist_ok=True)
        print(f"Wrote {write_requirements(config.target)}")
        print(f"Wrote {write_env(config.target, config.server, config.database)}")
        print(f"Wrote {write_gitignore(config.target)}")
        if config.manager == "uv":
            install_with_uv(config.target)
        else:
            install_with_python(config.target)
        azure_login(config.target)
    except (SetupError, SettingsError, OSError) as exc:
        print(f"ERROR {exc}", file=sys.stderr)
        return 1

    print(f"Local Fabric setup ready in {config.target}")
    try:
        folder = ask_workdir(prompt, config.target, workdir or Path.cwd())
        if folder is not None:
            folder.mkdir(parents=True, exist_ok=True)
            print(f"Saved working directory {save_workdir(folder)} to {config_path()}")
    except (EOFError, KeyboardInterrupt):
        print("\nWorking directory not saved", file=sys.stderr)
    except (SettingsError, OSError) as exc:
        print(f"ERROR {exc}", file=sys.stderr)
        return 1
    return 0
