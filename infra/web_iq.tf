resource "azapi_resource" "web_iq" {
  type      = "Microsoft.WebIQ/accounts@2026-06-01-preview"
  name      = local.resource_prefix
  parent_id = azurerm_resource_group.main.id
  location  = "global"
  tags      = local.tags

  # AzAPI 2.13 has no embedded Web IQ schema; the pinned ARM API validates the request.
  schema_validation_enabled = false

  body = {
    kind = "WebIQ.Grounding"
    sku  = { name = "G3" }
    properties = {
      statisticsEnabled = false
    }
  }

  lifecycle {
    precondition {
      condition     = var.web_iq_terms_accepted
      error_message = "Review and explicitly accept Web IQ terms and paid usage before setting web_iq_terms_accepted = true. Azure credits cannot pay for Web IQ; the subscription must be enabled for the service."
    }
  }
}

# Public context for the agents' Web IQ tool: a knowledge base whose only source is Web IQ, kept apart from the agents'
# document knowledge base so that no document question can trigger a public web search.
resource "restful_resource" "public_context_source" {
  count = var.knowledge_lakehouse_id == null ? 0 : 1

  path            = "/knowledgesources/${local.public_context_source_name}"
  check_existance = true
  force_new_attrs = ["name", "kind"]

  body = {
    name              = local.public_context_source_name
    kind              = "mcpServer"
    description       = "Public web passages from Web IQ for ${var.project_name} (API key version ${var.web_iq_api_key_version})."
    resultsProcessing = "rerank"
    mcpServerParameters = {
      serverURL = local.web_iq_mcp_endpoint
      # Only the web tool: the account is passage-only, and news, images and videos aren't the public context the
      # agents cite.
      tools = [{
        name              = "web"
        outputParsing     = { kind = "auto" }
        resultsProcessing = "none"
        maxOutputTokens   = 2000
      }]
    }
  }

  # Write-only: the key reaches Search on create or update and is never stored in Terraform state or plans.
  ephemeral_body = var.web_iq_api_key == null ? null : {
    mcpServerParameters = {
      authentication = {
        kind                    = "storedHeaders"
        storedHeadersParameters = { headers = { "x-apikey" = var.web_iq_api_key } }
      }
    }
  }

  lifecycle {
    replace_triggered_by = [azapi_resource.search, azapi_resource.web_iq.id]
  }
}

resource "restful_resource" "public_context_base" {
  count = var.knowledge_lakehouse_id == null ? 0 : 1

  path            = "/knowledgebases/${local.public_context_base_name}"
  check_existance = true

  body = {
    name                  = local.public_context_base_name
    description           = "Public-context knowledge base for ${var.project_name}: public web context from Web IQ only, never company data."
    outputMode            = "answerSynthesis"
    answerInstructions    = "Answer only from the retrieved public web passages and cite each page's title and URL. This is public, general context, not company guidance: it never decides a requirement or an outcome. If no passage answers the question, say so."
    retrievalInstructions = "Use the web tool with contentFormat set to passage. Search only for public, general context, such as manufacturer or industry guidance. Never include a person's name, a site or asset name, or any other private detail in a query."
    knowledgeSources      = [{ name = restful_resource.public_context_source[count.index].body.name }]
    models = [{
      kind = "azureOpenAI"
      azureOpenAIParameters = {
        resourceUri  = local.openai_endpoint
        deploymentId = azapi_resource.chat_model.name
        modelName    = var.chat_model.name
      }
    }]
    # MCP server sources don't support the minimal effort.
    retrievalReasoningEffort = { kind = "low" }
  }

  lifecycle {
    # Search refuses to delete a source while a knowledge base references it.
    replace_triggered_by = [restful_resource.public_context_source[count.index]]
  }

  depends_on = [azurerm_role_assignment.search_foundry_user]
}
