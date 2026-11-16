variable "location" {
  description = "Explicit Foundry/resource-group region supporting the selected agent, chat and embedding models; also the default Fabric/Search region unless an approved service-specific override is supplied."
  type        = string
  nullable    = false

  validation {
    condition     = can(regex("^[a-z][a-z0-9]+$", var.location))
    error_message = "Set location to an Azure region short name, for example the name returned by az account list-locations."
  }
}

variable "project_name" {
  description = "Short, project-specific name used for the Entra application, workspaces, Azure resource names and tags. Keep stable after deployment."
  type        = string
  nullable    = false

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{1,14}[a-z0-9]$", var.project_name)) && !strcontains(var.project_name, "--")
    error_message = "project_name must be 3-16 lowercase letters, digits or single hyphens, starting with a letter and ending with a letter or digit."
  }
}

variable "fabric_location" {
  description = "Optional Fabric capacity region override. Defaults to location; choose explicitly when regional CU quota prevents the common-region deployment."
  type        = string
  default     = null

  validation {
    condition     = var.fabric_location == null ? true : can(regex("^[a-z][a-z0-9]+$", var.fabric_location))
    error_message = "fabric_location must be null or an explicit Azure region short name."
  }
}

variable "search_location" {
  description = "Optional Search region override. Defaults to location; choose explicitly when service capacity or feature availability prevents the common-region deployment."
  type        = string
  default     = null

  validation {
    condition     = var.search_location == null ? true : can(regex("^[a-z][a-z0-9]+$", var.search_location))
    error_message = "search_location must be null or an explicit Azure region short name."
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

variable "fabric_git_enabled" {
  description = "Connect DEV to GitHub using a PAT. Defaults to native Fabric item import without Git synchronization."
  type        = bool
  default     = false
  nullable    = false
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
  description = "Published GitHub branch containing the reviewed DEV definitions."
  type        = string
  default     = "main"
}

variable "github_directory_name" {
  description = "Repository directory containing Fabric definitions for opt-in Git synchronization."
  type        = string
  default     = "/workspace"

  validation {
    condition     = startswith(var.github_directory_name, "/") && length(var.github_directory_name) <= 256
    error_message = "github_directory_name must start with / and be at most 256 characters."
  }
}

variable "github_token" {
  description = "GitHub PAT with repository Contents read/write, required only when fabric_git_enabled is true. Supply through TF_VAR_github_token."
  type        = string
  default     = null
  sensitive   = true
  ephemeral   = true

  validation {
    condition     = !var.fabric_git_enabled || try(length(trimspace(var.github_token)) > 0, false)
    error_message = "When fabric_git_enabled is true, supply a nonempty GitHub PAT through TF_VAR_github_token."
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

variable "knowledge_lakehouse_id" {
  description = "DEV lakehouse GUID created by bootstrap. Leave null for platform setup; set after bootstrap succeeds to enable the knowledge_folders sources, the knowledge bases and their MCP connections."
  type        = string
  default     = null

  validation {
    condition = var.knowledge_lakehouse_id == null ? true : (
      can(regex("^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$", var.knowledge_lakehouse_id)) &&
      var.knowledge_lakehouse_id != "00000000-0000-0000-0000-000000000000"
    )
    error_message = "knowledge_lakehouse_id must be null or the nonzero GUID of the bootstrapped DEV lakehouse, not its logical ID or an Azure resource ID."
  }
}

variable "knowledge_ontologies" {
  description = "Optional Git-owned DEV ontologies, keyed by a stable short name, to register as live fabricOntology sources of the check-only knowledge base <project_name>-ontology-check (never of the agents' knowledge base) and as Foundry Fabric IQ connections. Supply physical item GUIDs after binding succeeds. Queries require a delegated end-user token."
  type = map(object({
    ontology_id = string
    description = string
  }))
  default  = {}
  nullable = false

  validation {
    condition = alltrue([
      for name in keys(var.knowledge_ontologies) :
      can(regex("^[a-z][a-z0-9-]{0,30}[a-z0-9]$", name)) && !strcontains(name, "--")
    ])
    error_message = "Ontology source keys must be 2-32 lowercase letters, digits or single hyphens, starting with a letter and ending with a letter or digit."
  }

  validation {
    condition = alltrue([
      for ontology in values(var.knowledge_ontologies) : try(
        can(regex("^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$", ontology.ontology_id)) &&
        ontology.ontology_id != "00000000-0000-0000-0000-000000000000" &&
        length(trimspace(ontology.description)) > 0,
        false
      )
    ])
    error_message = "Each ontology must have a nonzero physical item GUID and a nonempty description of the questions it can answer."
  }

  validation {
    condition     = length(var.knowledge_ontologies) == 0 || var.knowledge_lakehouse_id != null
    error_message = "Register ontologies during knowledge setup, after setting knowledge_lakehouse_id to the bootstrapped DEV lakehouse GUID."
  }
}

variable "knowledge_base_name" {
  description = "Optional name of the Foundry IQ knowledge base of approved documents for the agents' Foundry IQ tool; null uses <project_name>-knowledge. It holds only the attached knowledge_folders sources, never an ontology. Renaming it replaces the base; the agents follow the knowledge_base_name output."
  type        = string
  default     = null

  validation {
    condition = var.knowledge_base_name == null ? true : (
      can(regex("^[a-z][a-z0-9-]{0,62}[a-z0-9]$", var.knowledge_base_name)) && !strcontains(var.knowledge_base_name, "--")
    )
    error_message = "knowledge_base_name must be null or 2-64 lowercase letters, digits or single hyphens, starting with a letter and ending with a letter or digit."
  }

  validation {
    condition = var.knowledge_base_name == null ? true : (
      !contains(["${var.project_name}-ontology-check", "${var.project_name}-public-context"], var.knowledge_base_name)
    )
    error_message = "knowledge_base_name must differ from the ontology check-only and public-context knowledge bases."
  }
}

variable "knowledge_folders" {
  description = "Document folders in the knowledge lakehouse, keyed by a stable short name. At knowledge setup each becomes the indexedOneLake knowledge source <project_name>-<key>, checked for changes every five minutes, with description as its display name. target_path is the source's targetPath, the lakehouse folder or shortcut it indexes. Give it relative to the Files area, the only area the OneLake indexer reads, without a Files/ prefix or surrounding slashes: documents indexes Files/documents. attach adds the source to the agents' knowledge base; a detached source is still created and indexed, so it can be attached in the Foundry portal without waiting. An override replaces every default entry."
  type = map(object({
    description = string
    target_path = string
    attach      = bool
  }))
  default = {
    documents = {
      description = "Starter documents"
      target_path = "documents"
      attach      = true
    }
  }
  nullable = false

  validation {
    condition = alltrue([
      for name in keys(var.knowledge_folders) :
      can(regex("^[a-z][a-z0-9-]{0,30}[a-z0-9]$", name)) && !strcontains(name, "--")
    ])
    error_message = "Knowledge folder keys must be 2-32 lowercase letters, digits or single hyphens, starting with a letter and ending with a letter or digit."
  }

  # Folder source names share Search's namespace with the <project_name>-web and <project_name>-<key>-ontology sources.
  validation {
    condition     = alltrue([for name in keys(var.knowledge_folders) : name != "web" && !endswith(name, "-ontology")])
    error_message = "Knowledge folder keys can't be web or end with -ontology: those source names belong to the Web IQ and ontology sources."
  }

  validation {
    condition = alltrue([
      for folder in values(var.knowledge_folders) : try(
        length(trimspace(folder.description)) > 0 && !strcontains(folder.description, "\n") &&
        folder.attach != null &&
        trimspace(folder.target_path) == folder.target_path &&
        can(regex("^[^/\\\\]+(/[^/\\\\]+)*$", folder.target_path)) &&
        length(setintersection(toset(split("/", folder.target_path)), toset([".", ".."]))) == 0 &&
        lower(split("/", folder.target_path)[0]) != "files",
        false
      )
    ])
    error_message = "Each knowledge folder needs a one-line description, attach set to true or false, and a target_path relative to the lakehouse Files area, such as documents or documents/current: no Files/ prefix, no leading, trailing or doubled slashes, no backslashes and no . or .. segments."
  }

  validation {
    condition     = anytrue([for folder in values(var.knowledge_folders) : try(folder.attach == true, false)])
    error_message = "Attach at least one knowledge folder: the knowledge base needs a source."
  }
}

variable "knowledge_editor_principal_ids" {
  description = "Optional Entra object IDs of users or groups to grant Search Service Contributor on the Search service, so they can attach a detached knowledge source to the agents' knowledge base in the Foundry portal, or remove it again. Owners and Contributors of the service don't need it. The role manages every Search object, not only knowledge bases: grant it only to people who edit knowledge live."
  type        = list(string)
  default     = []
  nullable    = false

  validation {
    condition = alltrue([
      for id in var.knowledge_editor_principal_ids :
      can(regex("^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$", id)) &&
      id != "00000000-0000-0000-0000-000000000000"
    ])
    error_message = "knowledge_editor_principal_ids must hold nonzero Entra object IDs (GUIDs), not user principal names."
  }
}

variable "web_iq_terms_accepted" {
  description = "Required for deployment: set true only after explicitly accepting https://www.microsoft.com/en-us/webiq/webiq-legal and paid public-endpoint usage. Does not grant service eligibility or perform portal onboarding."
  type        = bool
  default     = false
  nullable    = false
}

variable "web_iq_api_key" {
  description = "Web IQ API key for the public-context knowledge source. Never set it in tfvars: deploy.py passes WEB_IQ_API_KEY as TF_VAR_web_iq_api_key to every plan and apply after knowledge setup, never to teardown. A plan without it would remove the stored key. Write-only; Search masks it on read."
  type        = string
  default     = null
  sensitive   = true
  ephemeral   = true

  validation {
    condition     = var.web_iq_api_key == null || can(regex("^[!-~]+$", var.web_iq_api_key))
    error_message = "web_iq_api_key must be a nonempty Web IQ portal key without spaces or control characters."
  }
}

variable "web_iq_api_key_version" {
  description = "Increment when rotating the write-only Web IQ API key, so the public-context source is updated with the new key."
  type        = number
  default     = 1

  validation {
    condition     = var.web_iq_api_key_version >= 1 && floor(var.web_iq_api_key_version) == var.web_iq_api_key_version
    error_message = "web_iq_api_key_version must be a positive integer."
  }
}

variable "work_iq_enabled" {
  description = "Opt in to Work IQ for the with-IQ agent's mail tool: a Work IQ public-client registration, Foundry's work-iq connection, through which the agent reads each user's mail as that user, and the agent's Work IQ tool. Prerequisites: a Microsoft 365 mailbox per user; a Copilot Credits spending policy covering the Work IQ API, which bills custom agents' Work IQ calls even for users with a Microsoft 365 Copilot licence; administrator consent to WorkIQAgent.Ask and offline_access; and each user's one-time consent, on their first Work IQ question."
  type        = bool
  default     = false
  nullable    = false
}

variable "search_sku" {
  description = "AI Search tier. serverless (preview, default) is ready in seconds but needs the subscription's Microsoft.Search/Serverless feature and a region that offers it; it has no SLA, no tier migration, and while its preview defect lasts any Search setting or tag change replaces the service. basic or standard tiers take about 20 minutes to provision."
  type        = string
  default     = "serverless"

  validation {
    condition     = contains(["serverless", "basic", "standard", "standard2", "standard3"], var.search_sku)
    error_message = "search_sku must be serverless, basic, standard, standard2 or standard3."
  }
}

variable "search_identity_ready" {
  description = "Leave true. deploy.py plans with false only when a plan would create or replace a serverless Search service after knowledge setup: that service gets its identity after creation, so its role assignments wait for the next plan."
  type        = bool
  default     = true
  nullable    = false
}

variable "chat_model" {
  description = "Supported Foundry IQ query-planning and answer-synthesis model, version, deployment SKU and quota units. Every Web IQ public-context call and verify's ontology check use it; the agents' knowledge base runs at minimal effort, which runs no model. A low capacity throttles them with HTTP 429."
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
  description = "Embedding model, version, deployment SKU and quota units. Deployment changes rebuild the knowledge_folders sources."
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

variable "telemetry" {
  description = "Log Analytics retention, in days, for the app's Container Apps logs and the Application Insights traces of the app and the agents. Foundry's traces include message text and tool inputs and outputs."
  type = object({
    retention_days = optional(number, 30)
  })
  default  = {}
  nullable = false

  validation {
    condition     = var.telemetry.retention_days >= 30 && var.telemetry.retention_days <= 730 && floor(var.telemetry.retention_days) == var.telemetry.retention_days
    error_message = "telemetry.retention_days must be a whole number from 30 to 730."
  }
}

variable "agent_model" {
  description = "Model, version, deployment SKU and quota units of the agent deployment the prompt agents run on, separate from chat_model so agent turns never wait behind knowledge-base query planning. The default favours speed: a fast model with tool calling, run at agent_reasoning_effort."
  type = object({
    name     = optional(string, "gpt-5.4-mini")
    version  = optional(string, "2026-03-17")
    sku      = optional(string, "GlobalStandard")
    capacity = optional(number, 100)
  })
  default  = {}
  nullable = false

  validation {
    condition     = var.agent_model.capacity >= 1 && floor(var.agent_model.capacity) == var.agent_model.capacity
    error_message = "agent_model.capacity must be a positive integer supported by your model quota."
  }
}

variable "agent_reasoning_effort" {
  description = "Reasoning effort of the prompt agents: low keeps chained tool calls quick; medium or high trade seconds for care. null omits the setting, for a model without reasoning."
  type        = string
  default     = "low"

  validation {
    condition     = var.agent_reasoning_effort == null ? true : contains(["none", "minimal", "low", "medium", "high", "xhigh"], var.agent_reasoning_effort)
    error_message = "agent_reasoning_effort must be null or one of none, minimal, low, medium, high and xhigh, as the agent model supports."
  }
}

variable "app" {
  description = "Container Apps sizing of the web app's server. min_replicas = 1 keeps one replica warm, so no question waits for a cold start; set 0 between sessions to stop paying for idle time. cpu and memory must be a Consumption pair (memory = 2 x cpu, in Gi)."
  type = object({
    cpu          = optional(number, 1)
    memory       = optional(string, "2Gi")
    min_replicas = optional(number, 1)
    max_replicas = optional(number, 3)
  })
  default  = {}
  nullable = false

  validation {
    condition     = contains([0.25, 0.5, 0.75, 1, 1.25, 1.5, 1.75, 2, 2.5, 3, 3.5, 4], var.app.cpu) && var.app.memory == format("%gGi", var.app.cpu * 2)
    error_message = "app.cpu must be a Consumption size from 0.25 to 4 vCPU, with app.memory twice that in Gi, such as cpu = 1 and memory = \"2Gi\"."
  }

  validation {
    condition     = var.app.min_replicas >= 0 && var.app.max_replicas >= max(1, var.app.min_replicas) && var.app.max_replicas <= 10 && floor(var.app.min_replicas) == var.app.min_replicas && floor(var.app.max_replicas) == var.app.max_replicas
    error_message = "app.min_replicas must be a whole number from 0, and app.max_replicas a whole number from max(1, min_replicas) to 10."
  }
}

variable "app_image_tag" {
  description = "Tag of the web app image in the registry: a digest of the reviewed app files it was built from. Leave null: deploy.py's app phase builds the image with ACR Tasks, sets this tag and applies a reviewed plan that creates or updates the container app."
  type        = string
  default     = null

  validation {
    condition     = var.app_image_tag == null ? true : can(regex("^[0-9a-f]{12}$", var.app_image_tag))
    error_message = "app_image_tag must be null or the 12-character tag deploy.py builds."
  }
}

variable "container_registry_sku" {
  description = "Azure Container Registry tier for the web app image that deploy.py's app phase builds with ACR Tasks."
  type        = string
  default     = "Basic"
  nullable    = false

  validation {
    condition     = contains(["Basic", "Standard", "Premium"], var.container_registry_sku)
    error_message = "container_registry_sku must be Basic, Standard or Premium."
  }
}

variable "app_local_redirect_uris" {
  description = "Extra sign-in redirect URIs for a local live run of the web app, each the MSAL redirect bridge of a localhost server, such as [\"http://localhost:3000/redirect.html\"] for npm run dev. The deployed app's https://<app>/redirect.html is always registered. Keep empty for shared environments."
  type        = list(string)
  default     = []
  nullable    = false

  validation {
    condition     = alltrue([for uri in var.app_local_redirect_uris : can(regex("^http://localhost(:[0-9]{1,5})?/redirect\\.html$", uri))])
    error_message = "app_local_redirect_uris accepts only http://localhost[:port]/redirect.html, the app's MSAL redirect bridge; the deployed app's URL is always registered."
  }
}
