variable "location" {
  description = "Azure region short name supporting Fabric, Search agentic retrieval, and both selected models."
  type        = string
  nullable    = false

  validation {
    condition     = can(regex("^[a-z][a-z0-9]+$", var.location))
    error_message = "Set location to an Azure region short name, for example the name returned by az account list-locations."
  }
}

variable "project_name" {
  description = "Short, project-specific name used for workspaces, Azure resource names and tags. Keep stable after deployment."
  type        = string
  nullable    = false

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{1,14}[a-z0-9]$", var.project_name)) && !strcontains(var.project_name, "--")
    error_message = "project_name must be 3-16 lowercase letters, digits or single hyphens, starting with a letter and ending with a letter or digit."
  }
}

variable "fabric_capacity_admins" {
  description = "Capacity admins: user UPNs or service-principal object IDs. Include the Fabric provider identity."
  type        = set(string)
  nullable    = false

  validation {
    condition = length(var.fabric_capacity_admins) > 0 && alltrue([
      for admin in var.fabric_capacity_admins :
      can(regex("^[^@[:space:]]+@[^@[:space:]]+$", admin)) ||
      can(regex("^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$", admin))
    ])
    error_message = "Provide at least one capacity administrator as a user UPN or service-principal object ID."
  }
}

variable "fabric_capacity_sku" {
  description = "SKU for the new, billable Fabric capacity."
  type        = string
  default     = "F2"

  validation {
    condition     = contains(["F2", "F4", "F8", "F16", "F32", "F64", "F128", "F256", "F512", "F1024", "F2048"], var.fabric_capacity_sku)
    error_message = "Use a supported Fabric F-series SKU."
  }
}

variable "github_owner" {
  description = "GitHub organization or user that owns the existing repository."
  type        = string
  nullable    = false

  validation {
    condition     = can(regex("^[A-Za-z0-9][A-Za-z0-9-]*$", var.github_owner))
    error_message = "github_owner must be an organization or user name, not a URL."
  }
}

variable "github_repository_name" {
  description = "Existing GitHub repository name, without an owner or .git suffix."
  type        = string
  nullable    = false

  validation {
    condition     = can(regex("^[A-Za-z0-9_.-]+$", var.github_repository_name)) && !endswith(var.github_repository_name, ".git")
    error_message = "Provide only the GitHub repository name, without an owner, URL or .git suffix."
  }
}

variable "github_branch_name" {
  description = "Existing GitHub branch connected to DEV."
  type        = string
  default     = "main"
}

variable "github_directory_name" {
  description = "Repository directory containing Fabric item definitions."
  type        = string
  default     = "/workspace"

  validation {
    condition     = startswith(var.github_directory_name, "/") && length(var.github_directory_name) <= 256
    error_message = "github_directory_name must start with / and be at most 256 characters."
  }
}

variable "github_token" {
  description = "GitHub PAT with repository Contents read/write. Supply through TF_VAR_github_token for plan and apply."
  type        = string
  sensitive   = true
  ephemeral   = true
  nullable    = false

  validation {
    condition     = length(trimspace(var.github_token)) > 0
    error_message = "Supply a nonempty GitHub PAT through TF_VAR_github_token."
  }
}

variable "github_token_version" {
  description = "Increment when rotating the write-only GitHub PAT."
  type        = number
  default     = 1

  validation {
    condition     = var.github_token_version >= 1 && floor(var.github_token_version) == var.github_token_version
    error_message = "github_token_version must be a positive integer."
  }
}

variable "search_sku" {
  description = "AI Search tier; Basic or Standard is required for managed identities and semantic ranking."
  type        = string
  default     = "basic"

  validation {
    condition     = contains(["basic", "standard", "standard2", "standard3"], var.search_sku)
    error_message = "search_sku must be basic, standard, standard2 or standard3."
  }
}

variable "chat_model" {
  description = "Supported Foundry IQ query-planning model, version, deployment SKU and quota units."
  type = object({
    name     = optional(string, "gpt-5.4-mini")
    version  = optional(string, "2026-03-17")
    sku      = optional(string, "GlobalStandard")
    capacity = optional(number, 10)
  })
  default  = {}
  nullable = false

  validation {
    condition     = var.chat_model.capacity >= 1 && floor(var.chat_model.capacity) == var.chat_model.capacity
    error_message = "chat_model.capacity must be a positive integer supported by your model quota."
  }
}

variable "embedding_model" {
  description = "Embedding model, version, deployment SKU and quota units. Deployment changes rebuild the knowledge source."
  type = object({
    name     = optional(string, "text-embedding-3-small")
    version  = optional(string, "1")
    sku      = optional(string, "GlobalStandard")
    capacity = optional(number, 10)
  })
  default  = {}
  nullable = false

  validation {
    condition     = var.embedding_model.capacity >= 1 && floor(var.embedding_model.capacity) == var.embedding_model.capacity
    error_message = "embedding_model.capacity must be a positive integer supported by your model quota."
  }
}

variable "tags" {
  description = "Additional Azure resource tags."
  type        = map(string)
  default     = {}
}

variable "contributor_principal_id" {
  description = "Optional principal to grant Contributor on DEV. Leave null for the workspace creator or an existing Admin."
  type        = string
  default     = null
}