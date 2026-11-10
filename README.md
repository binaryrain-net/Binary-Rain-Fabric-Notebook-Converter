# Binary Rain Fabric notebook converter

Convert Microsoft Fabric notebooks (`.Notebook` folders with a `notebook-content.py`) to Jupyter notebooks or REPL scripts, edit them locally, then convert them back.

## Install

Requires Python 3.13 or newer and [uv](https://docs.astral.sh/uv/). Run from the repository root:

```bash
uv sync
```

This installs the `fabric-nb` command into the project environment. Run it with `uv run fabric-nb`, `python -m converter.cli` or `python main.py`.

### just recipes

With [just](https://github.com/casey/just) installed, `just --list` lists the recipes. Running `just` without a recipe runs `install`.

| Recipe                      | Runs                                                           |
| --------------------------- | -------------------------------------------------------------- |
| `just install`              | `uv sync`                                                      |
| `just setup`                | `fabric-nb setup`                                              |
| `just to-ipynb SOURCES...`  | `fabric-nb to-ipynb`, extra options such as `-o` are passed on |
| `just to-repl SOURCES...`   | `fabric-nb to-repl`                                            |
| `just to-fabric SOURCES...` | `fabric-nb to-fabric`                                          |
| `just workdir PATH`         | `fabric-nb config set workdir PATH`                            |
| `just config`               | `fabric-nb config list`                                        |

## Usage

`fabric-nb` has one subcommand per conversion. Each takes one or more sources, an optional `-o/--output` that works with a single source only, and an optional `-w/--workdir`, see [Working directory](#working-directory).

| Command     | Input                                       | Default output                                           |
| ----------- | ------------------------------------------- | -------------------------------------------------------- |
| `to-ipynb`  | `.Notebook` folder or `notebook-content.py` | `<name>.ipynb` next to the `.Notebook` folder            |
| `to-repl`   | `.Notebook` folder or `notebook-content.py` | `<name>.py` next to the `.Notebook` folder               |
| `to-fabric` | `.ipynb` notebook or `.py` REPL script      | `<name>.Notebook/notebook-content.py` next to the source |

```bash
uv run fabric-nb to-ipynb nb_x.Notebook
uv run fabric-nb to-repl nb_a.Notebook nb_b.Notebook
uv run fabric-nb to-repl nb_x.Notebook -o repl/nb_x.py
uv run fabric-nb to-fabric nb_x.ipynb repl/nb_y.py
uv run fabric-nb to-fabric nb_x.ipynb -o nb_x.Notebook
```

`to-fabric` reads the file suffix to pick the format. Its `-o/--output` option accepts either a `.Notebook` folder or a `.py` content file. REPL scripts use the `# %%` percent format understood by VS Code and Jupytext.

### Local dev cell

`to-ipynb` and `to-repl` add a setup cell at the top of every output file. It imports the local `display` and `notebookutils` helpers from `binaryrain_fabric_local_pyspark`, loads `.env` and creates `spark` with `get_local_spark()`.

After loading `.env`, the cell sets `FABRIC_WORKSPACE_ID` and `FABRIC_LAKEHOUSE_ID` from the notebook's default lakehouse metadata under `dependencies.lakehouse`. It uses `default_lakehouse_workspace_id` and `default_lakehouse`, respectively. These values override `.env`. An assignment is left out when the notebook has no such value.

The cell carries the `local-dev` tag, and `to-fabric` drops every cell with that tag, so the setup code never reaches Fabric. Don't edit or untag this cell.

Before running a converted notebook, install `binaryrain-fabric-local-pyspark` and `python-dotenv` in your local environment and create a `.env` file. `fabric-nb setup` does both. The CLI prints a setup reminder after a successful conversion to either local format.

### Local setup

```bash
uv run fabric-nb setup
```

`setup` asks these questions:

1. Where the local Fabric setup should take place. Defaults to the working directory.
2. The name of the local environment. Defaults to a name derived from the setup folder, with invalid characters replaced or removed. It is used as the name of a new uv project and as the Jupyter kernel name.
3. Whether to use `uv` or `python`. Defaults to `uv` when it is installed.
4. The SQL Analytics Endpoint of Fabric.
5. The name of the default Fabric Lakehouse.

It then writes `requirements.txt` with `python-dotenv` and `binaryrain-fabric-local-pyspark`, and `requirements-dev.txt` with `ipykernel`. Existing requirements entries are kept.

It writes `FABRIC_SERVER` and `FABRIC_DEFAULT_DATABASE` to `.env` from your answers. It replaces existing values for these keys and keeps other entries.

It also writes `.gitignore` with `.cache/`, `.venv/`, `__pycache__/` and `.env`, keeping existing entries.

With `uv` it runs `uv init --name <name>`, skipping this step when a `pyproject.toml` exists. It then runs `uv add -r requirements.txt` and `uv add --dev -r requirements-dev.txt`. With `python` it creates or reuses a `.venv` virtual environment and runs `pip install -r requirements.txt` and `pip install -r requirements-dev.txt` in it.

It then registers the `.venv` as a Jupyter kernel for your user under the environment name. With `uv` this is `uv run python -m ipykernel install --user --env VIRTUAL_ENV <setup folder>\.venv --name=<name>`. With `python` the same command runs with the Python of the `.venv`. The module form is used because Windows can block the `ipython.exe` launcher with "Access is denied". Any active `VIRTUAL_ENV` is removed before the setup commands run, so the converter's own environment does not leak into the target.

Select the registered kernel in Jupyter or VS Code when running a converted notebook.

Then it runs `az login`, so the [Azure CLI](https://aka.ms/azure-cli) must be installed.

As a last step it asks whether to set a working directory in `config.toml`, see [Working directory](#working-directory). If you answer yes, it asks for the folder, suggesting the setup folder, creates it if needed and stores it as `workdir`.

### Working directory

Relative paths, for sources, `-o/--output` and the `setup` folder, are resolved against a working directory instead of the folder you run the command from. It comes from the first source that is set:

1. The `-w/--workdir` option of a command.
2. The `FABRIC_NB_WORKDIR` environment variable.
3. `workdir` in `config.toml` in the repository root.
4. The current folder.

```bash
uv run fabric-nb config set workdir C:/projects/fabric
uv run fabric-nb to-repl nb_x.Notebook
uv run fabric-nb to-repl nb_x.Notebook -w C:/other/folder
uv run fabric-nb config get workdir
uv run fabric-nb config unset workdir
uv run fabric-nb config list
```

| Command                   | Effect                                                         |
| ------------------------- | -------------------------------------------------------------- |
| `config set workdir PATH` | Stores `PATH` as an absolute folder. The folder must exist.    |
| `config get workdir`      | Prints the stored folder, exits with code 1 if none is stored. |
| `config unset workdir`    | Removes the stored folder.                                     |
| `config list`             | Prints the path of `config.toml` and all stored settings.      |

`setup` also offers to store a folder as its last step.

Absolute paths are used as given. The settings live in `config.toml` in the root of this repository, next to `pyproject.toml`, for example:

```toml
workdir = "C:\\projects\\fabric"
```

`config.toml` is git ignored because it holds machine-specific paths. Set `FABRIC_NB_CONFIG` to use another file. A relative `workdir` stored in the file is resolved against that file's directory, which is the repository root by default.

Relative values for `-w/--workdir` and `FABRIC_NB_WORKDIR`, and paths passed to `config set workdir`, are resolved against the current folder. The selected working directory must exist.

### Output files

Existing output files are overwritten without confirmation, and missing parent folders are created. The converter keeps the newline style of an existing target file. New files use LF line endings.

When `to-fabric` writes a `.Notebook` folder without a `.platform` file, it prints a warning, because Fabric git sync needs that file. The converter does not create `.platform` files.

The command exits with code 1 if any source fails and prints the error for each failed source.

## Linting

```bash
uvx ruff check .
uvx ruff format --check .
```

The rules live in `ruff.toml`.
