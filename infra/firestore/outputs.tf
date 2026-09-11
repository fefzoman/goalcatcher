output "firestore_database_id" {
  description = "Firestore database holding durable monitor state."
  value       = google_firestore_database.monitor.name
}

output "firestore_location" {
  description = "Location of the Firestore database."
  value       = google_firestore_database.monitor.location_id
}
