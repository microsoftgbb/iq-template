output "authentication" {
  description = "Non-secret registration and consent handoff. Registration creation does not prove admin consent or delegated Fabric access."
  value = {
    tenant_id                   = data.azuread_client_config.current.tenant_id
    client_id                   = azuread_application.fabric.client_id
    application_object_id       = azuread_application.fabric.object_id
    service_principal_object_id = azuread_service_principal.fabric.object_id
    application_id_uri          = azuread_application_identifier_uri.fabric.identifier_uri
    login_scope                 = "${azuread_application_identifier_uri.fabric.identifier_uri}/.default"
    api_permissions_url         = "https://portal.azure.com/#@${data.azuread_client_config.current.tenant_id}/view/Microsoft_AAD_RegisteredApps/ApplicationMenuBlade/~/CallAnAPI/appId/${azuread_application.fabric.client_id}/isMSAApp~/false"
    delegated_scopes            = sort(tolist(local.delegated_scopes))
    authorized_client_ids       = local.authorized_clients
  }

  depends_on = [azuread_application_pre_authorized.clients]
}
