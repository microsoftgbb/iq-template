resource "azapi_resource" "foundry" {
  type      = "Microsoft.CognitiveServices/accounts@2025-06-01"
  name      = "ai-${local.resource_prefix}"
  parent_id = azurerm_resource_group.main.id
  location  = azurerm_resource_group.main.location
  tags      = local.tags

  identity {
    type = "SystemAssigned"
  }

  body = {
    kind = "AIServices"
    sku  = { name = "S0" }
    properties = {
      allowProjectManagement = true
      customSubDomainName    = "ai-${local.resource_prefix}"
      disableLocalAuth       = true
      publicNetworkAccess    = "Enabled"
    }
  }
}

resource "azapi_resource" "foundry_project" {
  type      = "Microsoft.CognitiveServices/accounts/projects@2025-06-01"
  name      = "${var.project_name}-project"
  parent_id = azapi_resource.foundry.id
  location  = azurerm_resource_group.main.location
  tags      = local.tags
  locks     = [azapi_resource.foundry.id]

  identity {
    type = "SystemAssigned"
  }

  body = {
    properties = {
      displayName = "${var.project_name} project"
      description = "Fabric and Foundry IQ for ${var.project_name}."
    }
  }
}

resource "azapi_resource" "chat_model" {
  type      = "Microsoft.CognitiveServices/accounts/deployments@2025-06-01"
  name      = "chat"
  parent_id = azapi_resource.foundry.id
  locks     = [azapi_resource.foundry.id]

  body = {
    sku = {
      name     = var.chat_model.sku
      capacity = var.chat_model.capacity
    }
    properties = {
      model = {
        format  = "OpenAI"
        name    = var.chat_model.name
        version = var.chat_model.version
      }
      versionUpgradeOption = "NoAutoUpgrade"
    }
  }
}

resource "azapi_resource" "embedding_model" {
  type      = "Microsoft.CognitiveServices/accounts/deployments@2025-06-01"
  name      = "embedding"
  parent_id = azapi_resource.foundry.id
  locks     = [azapi_resource.foundry.id]

  body = {
    sku = {
      name     = var.embedding_model.sku
      capacity = var.embedding_model.capacity
    }
    properties = {
      model = {
        format  = "OpenAI"
        name    = var.embedding_model.name
        version = var.embedding_model.version
      }
      versionUpgradeOption = "NoAutoUpgrade"
    }
  }
}

# The prompt agents run on their own deployment, so their tokens-per-minute budget never competes with the knowledge
# bases' query planning and answer synthesis on the chat deployment.
resource "azapi_resource" "agent_model" {
  type      = "Microsoft.CognitiveServices/accounts/deployments@2025-06-01"
  name      = "agent"
  parent_id = azapi_resource.foundry.id
  locks     = [azapi_resource.foundry.id]

  body = {
    sku = {
      name     = var.agent_model.sku
      capacity = var.agent_model.capacity
    }
    properties = {
      model = {
        format  = "OpenAI"
        name    = var.agent_model.name
        version = var.agent_model.version
      }
      versionUpgradeOption = "NoAutoUpgrade"
    }
  }
}

# The published connection schema omits the documented connection properties.
resource "azapi_resource" "search_connection" {
  type                      = "Microsoft.CognitiveServices/accounts/projects/connections@2025-06-01"
  name                      = "knowledge-search"
  parent_id                 = azapi_resource.foundry_project.id
  schema_validation_enabled = false
  locks                     = [azapi_resource.foundry.id]

  body = {
    properties = {
      category      = "CognitiveSearch"
      authType      = "AAD"
      target        = local.search_endpoint
      isSharedToAll = false
      metadata = {
        ApiType    = "Azure"
        ResourceId = azapi_resource.search.id
      }
    }
  }
}

# RemoteTool/ProjectManagedIdentity is documented but absent from AzAPI's schema. The agents' Foundry IQ tool reads
# the knowledge base as the project identity; Agent Service adds the caller's identity in
# x-ms-query-source-authorization. Foundry stores every RemoteTool connection with isSharedToAll false, whatever is
# sent, so every tool connection declares false: true would plan an update on every run.
resource "azapi_resource" "knowledge_base_connection" {
  count = var.knowledge_lakehouse_id == null ? 0 : 1

  type                      = "Microsoft.CognitiveServices/accounts/projects/connections@2025-10-01-preview"
  name                      = "knowledge-base"
  parent_id                 = azapi_resource.foundry_project.id
  schema_validation_enabled = false
  locks                     = [azapi_resource.foundry.id]

  body = {
    properties = {
      category      = "RemoteTool"
      authType      = "ProjectManagedIdentity"
      target        = local.knowledge_base_mcp_endpoint
      audience      = "https://search.azure.com/"
      isSharedToAll = false
      metadata      = { ApiType = "Azure" }
    }
  }

  depends_on = [azurerm_role_assignment.project_search_reader]
}

# UserEntraToken is documented on the preview RemoteTool API, not AzAPI's schema. Foundry exchanges the caller's
# Foundry token for a Fabric token, so the with-IQ agent queries the ontology as the signed-in user.
resource "azapi_resource" "fabric_iq_connection" {
  for_each = var.knowledge_ontologies

  type                      = "Microsoft.CognitiveServices/accounts/projects/connections@2025-10-01-preview"
  name                      = "fabric-${each.key}"
  parent_id                 = azapi_resource.foundry_project.id
  schema_validation_enabled = false
  locks                     = [azapi_resource.foundry.id]

  body = {
    properties = {
      category      = "RemoteTool"
      authType      = "UserEntraToken"
      target        = "https://api.fabric.microsoft.com/v1/mcp/dataPlane/workspaces/${fabric_workspace.dev.id}/items/${data.fabric_ontology.knowledge[each.key].id}/ontologyEndpoint"
      audience      = "https://analysis.windows.net/powerbi/api"
      isSharedToAll = false
      metadata      = { ApiType = "Azure" }
    }
  }
}

# The agents' Web IQ tool: the public-context knowledge base, whose only source is Web IQ, read as the project
# identity like the agents' knowledge base. Search calls Web IQ with the key it holds.
resource "azapi_resource" "public_context_connection" {
  count = var.knowledge_lakehouse_id == null ? 0 : 1

  type                      = "Microsoft.CognitiveServices/accounts/projects/connections@2025-10-01-preview"
  name                      = "public-context"
  parent_id                 = azapi_resource.foundry_project.id
  schema_validation_enabled = false
  locks                     = [azapi_resource.foundry.id]

  body = {
    properties = {
      category      = "RemoteTool"
      authType      = "ProjectManagedIdentity"
      target        = local.public_context_mcp_endpoint
      audience      = "https://search.azure.com/"
      isSharedToAll = false
      metadata      = { ApiType = "Azure" }
    }
  }

  depends_on = [azurerm_role_assignment.project_search_reader]
}

# The agents' Work IQ mail tool, only with work_iq_enabled: Work IQ's MCP server, signed in as each user (OAuth
# identity passthrough). The first question asks the user to consent through Foundry's link, which the app shows;
# Foundry then keeps a token per user and connection. The client is the deployment's Work IQ registration, public, so
# the connection holds no secret. OAuth2 isn't in AzAPI's schema for connections.
resource "azapi_resource" "work_iq_connection" {
  count = var.work_iq_enabled ? 1 : 0

  type                      = "Microsoft.CognitiveServices/accounts/projects/connections@2025-10-01-preview"
  name                      = "work-iq"
  parent_id                 = azapi_resource.foundry_project.id
  schema_validation_enabled = false
  locks                     = [azapi_resource.foundry.id]

  body = {
    properties = {
      category         = "RemoteTool"
      authType         = "OAuth2"
      target           = local.work_iq_mcp_endpoint
      credentials      = { clientId = azuread_application.work_iq[0].client_id }
      authorizationUrl = "${local.entra_oauth_endpoint}/authorize"
      tokenUrl         = "${local.entra_oauth_endpoint}/token"
      refreshUrl       = "${local.entra_oauth_endpoint}/token"
      scopes           = local.work_iq_mcp_scopes
      isSharedToAll    = false
      metadata         = { type = "generic_mcp" }
    }
  }

  # Foundry's consent-service URL, which the registration lists as its redirect.
  response_export_values = ["properties.redirectUrl"]
}

resource "azurerm_role_assignment" "deployer_foundry_user" {
  scope        = azapi_resource.foundry.id
  principal_id = data.azurerm_client_config.current.object_id

  # Foundry User, formerly Azure AI User; the role ID survives the rename.
  role_definition_id = "/subscriptions/${data.azurerm_client_config.current.subscription_id}/providers/Microsoft.Authorization/roleDefinitions/53ca6127-db72-4b80-b1b0-d745d6d5456d"
}
