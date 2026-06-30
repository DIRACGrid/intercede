# Renovate Configuration

This repository uses [Renovate](https://www.mend.io/renovate/) to automatically keep dependencies up to date.

## Configuration

The Renovate configuration is defined in `renovate.json` and includes:

### Dependency Groups

1. **Python dependencies** - Core Python packages from `pyproject.toml`
2. **Testing dependencies** - Pytest and related testing tools
3. **Build dependencies** - Hatchling and build tools
4. **GitHub Actions** - GitHub Actions workflow dependencies

### Update Strategy

- **Minimum release age**: Updates are only proposed once a release is at least 7 days old (30 days for major Python updates)
- **Update types**: Minor, patch, pin, and digest updates
- **Grouping**: Related dependencies are grouped together
- **Major versions**: Handled separately for better control
- **Automerge**: Disabled (requires manual review)

### Security

- Vulnerability alerts are enabled
- Security updates are labeled and prioritized

## How It Works

1. Renovate checks for dependency updates on schedule
2. Creates pull requests for available updates
3. Groups related dependencies together
4. Applies labels and follows the project's update strategy
5. Requires manual review and approval before merging

## Customization

To modify the Renovate behavior:

1. Edit `renovate.json`
2. Adjust package rules, schedules, or grouping strategies
3. Refer to the [Renovate documentation](https://docs.renovatebot.com/) for details

## Ignored Paths

Renovate ignores:
- `.pixi/` environments
- `__pycache__/` directories
- Virtual environments

This ensures Renovate only updates the actual dependency definitions in `pyproject.toml` and GitHub Actions workflows.
