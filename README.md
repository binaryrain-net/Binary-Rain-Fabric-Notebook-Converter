# Binary Rain Fabric notebook converter

Convert Microsoft Fabric notebooks (`.Notebook` folders with a `notebook-content.py`) to Jupyter notebooks or REPL scripts, edit them locally, then convert them back.

## Install

```bash
uv sync
```

This installs the `fabric-nb` command into the project environment. Run it with `uv run fabric-nb`, `python -m converter` or `python main.py`.

### just recipes

With [just](https://github.com/casey/just) installed, `just` lists the recipes:

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

`to-fabric` reads the file suffix to pick the format. REPL scripts use the `# %%` percent format, which VS Code and Jupytext run cell by cell.

### Local dev cell

`to-ipynb` and `to-repl` add a setup cell at the top of every output file. It loads `.env`, logs a reminder to set up the local dev environment and creates `spark` with `binaryrain_fabric_local_pyspark`. The cell carries the `local-dev` tag, and `to-fabric` drops every cell with that tag, so the setup code never reaches Fabric. Don't edit or untag this cell.

Before running a converted notebook, install `binaryrain-fabric-local-pyspark` and `python-dotenv` in your local environment and create a `.env` file. `fabric-nb setup` does both.

### Local setup

```bash
uv run fabric-nb setup
```

`setup` asks these questions:

1. Where the local Fabric setup should take place. Defaults to the working directory.
2. Whether to use `uv` or `python`. Defaults to `uv` when it is installed.
3. The SQL Analytics Endpoint of Fabric.
4. The name of the default Fabric Lakehouse.

It then writes `requirements.txt` with `python-dotenv` and `binaryrain-fabric-local-pyspark`, and `.env` with `FABRIC_SERVER` and `FABRIC_DATABASE` set from your answers. Existing entries in both files are kept, and existing `FABRIC_SERVER` and `FABRIC_DATABASE` values are replaced.

It also writes `.gitignore` with `.cache/`, `.venv/`, `__pycache__/` and `.env`, keeping existing entries.

With `uv` it runs `uv init` (skipped when a `pyproject.toml` exists) and `uv add -r requirements.txt`. With `python` it creates a `.venv` virtual environment and runs `pip install -r requirements.txt` in it.

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

A relative `workdir` is resolved against the repository root. `config.toml` is git ignored because it holds machine specific paths. Set `FABRIC_NB_CONFIG` to use another file.

### Output files

The converter keeps the newline style of an existing target file. When `to-fabric` writes a `.Notebook` folder without a `.platform` file, it prints a warning, because Fabric git sync needs that file.

The command exits with code 1 if any source fails and prints the error for each failed source.

## Linting

```bash
uvx ruff check .
uvx ruff format --check .
```

The rules live in `ruff.toml`.
