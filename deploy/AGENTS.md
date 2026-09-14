# Deployment component instructions

`football-goal-alert.service` is the canonical systemd unit embedded by the
Compute Engine startup script and installed during application deployment.

- Activate the repository with Serena before tracing runtime entry points.
- Use Context7 and the current systemd documentation before changing unit
  directives or sandboxing behavior.
- Keep the service unprivileged as `football-alert` with its current filesystem,
  device, kernel, and address-family restrictions.
- `.env` contains only non-secret values and Secret Manager IDs. Credential values
  are fetched into application memory and must never appear in an EnvironmentFile.
- Validate the unit and application configuration before restarting. A service
  restart begins normal provider activity, so do not restart merely to check
  syntax or status.
- After deployment, verify `is-active`, `is-enabled`, `NRestarts`, the deployed
  checksum, and sanitized journal events.
