data "azurerm_client_config" "current" {}

resource "random_string" "suffix" {
  length  = 6
  lower   = true
  upper   = false
  numeric = true
  special = false
}

locals {
  resource_prefix               = "${var.project_name}-${random_string.suffix.result}"
  compact_prefix                = replace(var.project_name, "-", "")
  github_repository_url         = "https://github.com/${var.github_owner}/${var.github_repository_name}"
  search_api_version            = "2026-08-01-preview"
  search_management_api_version = "2026-09-01-preview"
  knowledge_source_names        = { for key in keys(var.knowledge_folders) : key => "${var.project_name}-${key}" }
  knowledge_base_name           = coalesce(var.knowledge_base_name, "${var.project_name}-knowledge")
  ontology_check_base_name      = "${var.project_name}-ontology-check"
  public_context_source_name    = "${var.project_name}-web"
  public_context_base_name      = "${var.project_name}-public-context"
  web_iq_mcp_endpoint           = "https://api.microsoft.ai/v3/mcp"
  openai_endpoint               = "https://${azapi_resource.foundry.name}.openai.azure.com"
  knowledge_base_mcp_endpoint   = var.knowledge_lakehouse_id == null ? null : "${local.search_endpoint}/knowledgebases/${restful_resource.knowledge_base[0].body.name}/mcp?api-version=${local.search_api_version}"
  public_context_mcp_endpoint   = var.knowledge_lakehouse_id == null ? null : "${local.search_endpoint}/knowledgebases/${restful_resource.public_context_base[0].body.name}/mcp?api-version=${local.search_api_version}"

  tags = merge(var.tags, {
    project    = var.project_name
    managed_by = "Terraform"
  })
}

resource "azurerm_resource_group" "main" {
  name     = "rg-${local.resource_prefix}"
  location = var.location
  tags     = local.tags
}