## Summary

- 

## Validation

- [ ] `uv run --locked black --check .`
- [ ] `uv run --locked ruff check .`
- [ ] `uv run --locked pytest`
- [ ] `uv run --locked python scripts/validate_structure.py`
- [ ] `uv run --locked python scripts/check_translations.py`
- [ ] `uv run --locked bandit -r custom_components scripts -c pyproject.toml`
- [ ] `uv export --locked --no-dev --no-emit-project --format requirements-txt --output-file /tmp/hombee-runtime-requirements.txt && uv run --locked pip-audit --requirement /tmp/hombee-runtime-requirements.txt`
- [ ] `bash scripts/ha_docker_smoke.sh`
