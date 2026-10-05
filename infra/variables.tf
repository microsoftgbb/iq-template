variable "capacity_name" {
  description = "Name of an existing Fabric capacity that backs both workspaces."
  type        = string
}

variable "workspace_prefix" {
  description = "Prefix for the workspace display names."
  type        = string
  default     = "releaseflow"
}

# Azure DevOps Git settings for the DEV workspace.
variable "ado_organization_name" {
  type = string
}
variable "ado_project_name" {
  type = string
}
variable "ado_repository_name" {
  type = string
}
variable "ado_branch_name" {
  type    = string
  default = "main"
}
variable "ado_repo_url" {
  type = string
}
variable "ado_directory_name" {
  type    = string
  default = "/workspace"
}

# Service principal used by the source-control connection.
variable "tenant_id" {
  type = string
}
variable "client_id" {
  type = string
}
variable "client_secret" {
  type      = string
  sensitive = true
}

# Object id of the developer or group to grant Contributor on DEV.
variable "contributor_principal_id" {
  type = string
}