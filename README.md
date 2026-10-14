# Python-Ruff-Config

This repository provides a template for configuring your Python projects with predefined settings for Ruff, a fast Python linter and formatter. It includes a `.ruff.toml` configuration file that sets up common rules and ignores, making it easier to maintain code quality across your projects.

## Customized Settings

- Ignores Jupyter notebook files (`.ipynb`).
- Excludes directories like `__pycache__`, `.venv`, and `env`.
- Adds linting rules from pycodestyle, pyflake, pylint and numpy.
- Excludes specific rules that may not be relevant for all projects, such as `PLR0911` (Too many return statements) and more.

## Implementation

To use this configuration in your Python project, follow these steps:

- Create a file named `ruff.toml` in the root directory of your project. Recommended path is your projects root directory.
- Copy the contents of the `.ruff.toml` file from this repository into your project's `ruff.toml`.
- Ensure you have Ruff installed in your environment. Or use UV.
- Inside your project directory, locate the `pyproject.toml` file and add the following line under the `[tool.ruff]` section (Keep in mind that the path may vary based on your project structure):
  ```toml
  [tool.ruff]
  # Extend the settings from the ruff.toml file.
  extend = "../../ruff.toml"
  ```
- Run Ruff to check your code:
  ```bash
  ruff check . # If ruff is installed globally
  uv run ruff check . # If ruff is installed via UV
  ```

## GH Action
To automate linting with Ruff in your GitHub Actions workflow, you can add a step in your workflow file (e.g., `.github/workflows/linter.yml`). Here’s an example of how to set it up:

```yaml
name: Lint Codebase with Ruff

on:
  push:
    branches: [main]

jobs:
  linter-for-suffering:
    runs-on: ubuntu-latest

    steps:
      - name: "Checkout GitHub Action"
        uses: actions/checkout@v4

      - name: Lint with Ruff
        uses: astral-sh/ruff-action@v3
        with:
          args: "check --config ruff.toml"
```
