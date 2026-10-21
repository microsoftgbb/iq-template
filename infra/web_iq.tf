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
