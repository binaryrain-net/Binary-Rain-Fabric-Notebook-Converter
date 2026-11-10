# Recipes for the Fabric notebook converter.

# Install the project and the fabric-nb command
install:
    uv sync

# Interactively set up a local Fabric development environment
setup:
    uv run fabric-nb setup

# Convert Fabric notebooks to Jupyter notebooks
to-ipynb +args:
    uv run fabric-nb to-ipynb {{ args }}

# Convert Fabric notebooks to REPL scripts
to-repl +args:
    uv run fabric-nb to-repl {{ args }}

# Convert Jupyter notebooks or REPL scripts to Fabric notebooks
to-fabric +args:
    uv run fabric-nb to-fabric {{ args }}

# Store the working directory in config.toml
workdir path:
    uv run fabric-nb config set workdir "{{ path }}"

# Show the settings in config.toml
config:
    uv run fabric-nb config list
