output "resource_group_name" {
  description = "Resource group containing the Azure infrastructure."
  value       = azurerm_resource_group.main.name
}

output "fabric_capacity_id" {
  description = "Fabric capacity GUID used by both workspaces."
  value       = data.fabric_capacity.capacity.id
}

output "fabric_capacity_resource_id" {
  description = "Azure resource ID of the newly provisioned capacity."
  value       = azurerm_fabric_capacity.main.id
}

output "dev_workspace_id" {
  description = "GitHub-connected DEV workspace."
  value       = fabric_workspace.dev.id
}

output "test_workspace_id" {
  description = "Initially empty TEST workspace."
  value       = fabric_workspace.test.id
}

output "test_workspace_name" {
  description = "Display name of the TEST item-promotion target."
  value       = fabric_workspace.test.display_name
}

output "github_repository_url" {
  description = "Repository connected to the DEV workspace."
  value       = local.github_repository_url
}

output "foundry_project_endpoint" {
  description = "Foundry project API endpoint."
  value       = "https://${azapi_resource.foundry.name}.services.ai.azure.com/api/projects/${azapi_resource.foundry_project.name}"
}

output "foundry_project_id" {
  description = "Foundry project Azure resource ID."
  value       = azapi_resource.foundry_project.id
}

output "model_deployments" {
  description = "Foundry model deployment names."
  value = {
    chat      = azapi_resource.chat_model.name
    embedding = azapi_resource.embedding_model.name
  }
}

output "search_endpoint" {
  description = "Azure AI Search endpoint."
  value       = azurerm_search_service.knowledge.endpoint
}

output "knowledge_base_name" {
  description = "Foundry IQ knowledge base, backed by Azure AI Search."
  value       = restful_resource.knowledge_base.body.name
}

output "knowledge_source_status_url" {
  description = "GET with a Search token to inspect asynchronous ingestion and errors."
  value       = "${azurerm_search_service.knowledge.endpoint}/knowledgesources/${restful_resource.knowledge_source.body.name}/status?api-version=${local.search_api_version}"
}

output "knowledge_base_mcp_endpoint" {
  description = "Knowledge-base MCP endpoint for Foundry agents."
  value       = local.knowledge_base_mcp_endpoint
}

output "knowledge_base_connection_id" {
  description = "Project connection resource ID for the knowledge-base MCP tool."
  value       = azapi_resource.knowledge_base_connection.id
}

output "knowledge_storage_account_name" {
  description = "Upload source documents here using Microsoft Entra authentication."
  value       = azurerm_storage_account.knowledge.name
}

output "knowledge_container_name" {
  description = "Private Blob container indexed by the knowledge source."
  value       = azurerm_storage_container.knowledge.name
}