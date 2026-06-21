## Summary

- 

## Validation

- [ ] `black --check .`
- [ ] `ruff check .`
- [ ] `pytest`
- [ ] `python scripts/validate_structure.py`
- [ ] `python scripts/check_translations.py`
- [ ] `bandit -r custom_components scripts -c pyproject.toml`
- [ ] `pip-audit --requirement requirements.txt`
- [ ] `bash scripts/ha_docker_smoke.sh`
