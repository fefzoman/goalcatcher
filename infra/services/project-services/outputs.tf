output "project_id" {
  description = "Project whose required APIs have been enabled."
  value       = var.project_id
  depends_on  = [google_project_service.required]
}
