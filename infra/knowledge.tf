resource "azurerm_search_service" "knowledge" {
  name                = "srch-${local.resource_prefix}"
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  sku                 = var.search_sku
  replica_count       = 1
  partition_count     = 1
  semantic_search_sku = "standard"
  tags                = local.tags

  # Terraform uses an admin key; Foundry queries use the project managed identity.
  local_authentication_enabled = true
  authentication_failure_mode  = "http401WithBearerChallenge"

  identity {
    type = "SystemAssigned"
  }
}

resource "azurerm_storage_account" "knowledge" {
  name                            = "st${local.compact_prefix}${random_string.suffix.result}"
  resource_group_name             = azurerm_resource_group.main.name
  location                        = azurerm_resource_group.main.location
  account_tier                    = "Standard"
  account_replication_type        = "LRS"
  min_tls_version                 = "TLS1_2"
  https_traffic_only_enabled      = true
  allow_nested_items_to_be_public = false
  shared_access_key_enabled       = true
  default_to_oauth_authentication = true
  tags                            = local.tags
}

resource "azurerm_storage_container" "knowledge" {
  name                  = "knowledge"
  storage_account_id    = azurerm_storage_account.knowledge.id
  container_access_type = "private"
}

# The provider uploads this bootstrap blob using a storage key, not a public URL.
resource "azurerm_storage_blob" "starter" {
  name                 = "starter.txt"
  storage_container_id = azurerm_storage_container.knowledge.id
  type                 = "Block"
  content_type         = "text/plain"
  source_content       = file("${path.module}/seed/starter.txt")
  content_md5          = filemd5("${path.module}/seed/starter.txt")
}

resource "azurerm_role_assignment" "search_blob_reader" {
  scope                            = azurerm_storage_account.knowledge.id
  role_definition_name             = "Storage Blob Data Reader"
  principal_id                     = azurerm_search_service.knowledge.identity[0].principal_id
  skip_service_principal_aad_check = true
}

resource "azurerm_role_assignment" "search_foundry_user" {
  scope                            = azapi_resource.foundry.id
  role_definition_name             = "Cognitive Services User"
  principal_id                     = azurerm_search_service.knowledge.identity[0].principal_id
  skip_service_principal_aad_check = true
}

resource "azurerm_role_assignment" "project_search_reader" {
  scope                            = azurerm_search_service.knowledge.id
  role_definition_name             = "Search Index Data Reader"
  principal_id                     = azapi_resource.foundry_project.identity[0].principal_id
  skip_service_principal_aad_check = true
}

resource "azurerm_role_assignment" "deployer_search_reader" {
  scope                = azurerm_search_service.knowledge.id
  role_definition_name = "Search Index Data Reader"
  principal_id         = data.azurerm_client_config.current.object_id
}

resource "azurerm_role_assignment" "deployer_blob_contributor" {
  scope                = azurerm_storage_account.knowledge.id
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = data.azurerm_client_config.current.object_id
}

resource "restful_resource" "knowledge_source" {
  path            = "/knowledgesources/${local.knowledge_source_name}"
  check_existance = true
  # Rebuild generated ingestion objects and resend the write-only connection.
  force_new_attrs = ["name", "kind", "description", "azureBlobParameters"]

  body = {
    name        = local.knowledge_source_name
    kind        = "azureBlob"
    description = "Documents from the private ${var.project_name} knowledge container."
    azureBlobParameters = {
      containerName = azurerm_storage_container.knowledge.name
      ingestionParameters = {
        contentExtractionMode     = "minimal"
        disableImageVerbalization = true
        ingestionSchedule         = { interval = "PT5M" }
        embeddingModel = {
          kind = "azureOpenAI"
          azureOpenAIParameters = {
            resourceUri  = local.openai_endpoint
            deploymentId = azapi_resource.embedding_model.name
            modelName    = var.embedding_model.name
          }
        }
      }
    }
  }

  # Search redacts connection strings on read, even keyless ResourceId values.
  ephemeral_body = {
    azureBlobParameters = {
      connectionString = "ResourceId=${azurerm_storage_account.knowledge.id}"
    }
  }

  lifecycle {
    replace_triggered_by = [
      azurerm_search_service.knowledge.id,
      azurerm_storage_account.knowledge.id,
      azapi_resource.embedding_model.body,
    ]
  }

  depends_on = [
    azurerm_storage_blob.starter,
    azurerm_role_assignment.search_blob_reader,
    azurerm_role_assignment.search_foundry_user,
  ]
}

resource "restful_resource" "knowledge_base" {
  path            = "/knowledgebases/${local.knowledge_base_name}"
  check_existance = true

  body = {
    name               = local.knowledge_base_name
    description        = "Foundry IQ knowledge base for ${var.project_name}."
    outputMode         = "answerSynthesis"
    answerInstructions = "Answer using the retrieved documents and include citations. If the sources do not contain the answer, say you do not know."
    knowledgeSources   = [{ name = restful_resource.knowledge_source.body.name }]
    models = [{
      kind = "azureOpenAI"
      azureOpenAIParameters = {
        resourceUri  = local.openai_endpoint
        deploymentId = azapi_resource.chat_model.name
        modelName    = var.chat_model.name
      }
    }]
    retrievalReasoningEffort = { kind = "low" }
  }

  lifecycle {
    # Search refuses to delete a source while a knowledge base references it.
    replace_triggered_by = [restful_resource.knowledge_source]
  }
}
