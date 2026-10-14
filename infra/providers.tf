# Azure providers use ARM_* environment variables or the active Azure CLI account.
provider "azuread" {}

provider "azapi" {}

provider "azurerm" {
  features {}

  resource_provider_registrations = "none"
  resource_providers_to_register = [
    "Microsoft.CognitiveServices",
    "Microsoft.Fabric",
    "Microsoft.Search",
  ]
}

provider "fabric" {
  # Ontology metadata lookup is a preview feature in the pinned provider.
  preview = length(var.knowledge_ontologies) > 0
}

# AzAPI does not register Search knowledge-base/source data-plane resource types.
# Keep their CRUD lifecycle in Terraform; do not log this provider's HTTP traffic.
# The admin key is read on every run and never stored in state or saved plans.
ephemeral "azapi_resource_action" "search_admin_keys" {
  type                   = "Microsoft.Search/searchServices@${local.search_management_api_version}"
  resource_id            = azapi_resource.search.id
  action                 = "listAdminKeys"
  response_export_values = { primary_key = "primaryKey" }
}

provider "restful" {
  base_url      = local.search_endpoint
  create_method = "PUT"
  update_method = "PUT"
  delete_method = "DELETE"
  header        = { Content-Type = "application/json" }
  query         = { api-version = [local.search_api_version] }

  security = {
    apikey = [{
      in    = "header"
      name  = "api-key"
      value = ephemeral.azapi_resource_action.search_admin_keys.output.primary_key
    }]
  }

  client = {
    retry = {
      status_codes    = [429, 500, 502, 503, 504]
      count           = 6
      wait_in_sec     = 5
      max_wait_in_sec = 30
    }
  }
}