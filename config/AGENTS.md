# Team configuration instructions

`teams.yaml` is the runtime source of truth for monitored teams. Preserve the 28
threshold values and league labels from `thresholds.json` unless the user supplies
an explicit correction.

- Activate the repository with Serena before tracing how a field is consumed.
- Use Context7 before adding a field whose meaning depends on provider data.
- Match country, league, team names, and aliases exactly after the normalization
  implemented in `src.config`; never add fuzzy matching.
- Keep team keys stable because they are part of Firestore watch identity.
- Prefer verified positive `api_team_id` and `api_league_id` values when available.
- Never store credentials or Secret Manager values in YAML.
- Validate with `uv run python -m src.monitor --check-config`; this command must
  remain offline.
