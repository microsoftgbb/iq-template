resource "azurerm_fabric_capacity" "main" {
  name                   = "fc${local.compact_prefix}${random_string.suffix.result}"
  resource_group_name    = azurerm_resource_group.main.name
  location               = azurerm_resource_group.main.location
  administration_members = var.fabric_capacity_admins
  tags                   = local.tags

  sku {
    name = var.fabric_capacity_sku
    tier = "Fabric"
  }
}

# Fabric workspace APIs require the Fabric GUID, not the Azure resource ID.
data "fabric_capacity" "capacity" {
  display_name = azurerm_fabric_capacity.main.name

  lifecycle {
    postcondition {
      condition     = self.state == "Active"
      error_message = "The new Fabric capacity must be Active and visible to the Fabric provider identity."
    }
  }
}

resource "fabric_workspace" "dev" {
  display_name = "${var.project_name}-dev"
  description  = "Development workspace."
  capacity_id  = data.fabric_capacity.capacity.id
}

resource "fabric_workspace" "test" {
  display_name = "${var.project_name}-test"
  description  = "Test workspace; initially empty, reserved for deployments from GitHub."
  capacity_id  = data.fabric_capacity.capacity.id
}

resource "fabric_connection" "github" {
  display_name      = "${var.project_name}-github-conn"
  connectivity_type = "ShareableCloud"
  privacy_level     = "Organizational"

  connection_details = {
    type            = "GitHubSourceControl"
    creation_method = "GitHubSourceControl.Contents"
    parameters      = [{ name = "url", value = local.github_repository_url }]
  }

  credential_details = {
    credential_type      = "Key"
    skip_test_connection = false
    key_credentials = {
      key_wo         = var.github_token
      key_wo_version = var.github_token_version
    }
  }
}

resource "fabric_workspace_git" "dev" {
  workspace_id            = fabric_workspace.dev.id
  initialization_strategy = "PreferRemote"

  git_provider_details = {
    git_provider_type = "GitHub"
    owner_name        = var.github_owner
    repository_name   = var.github_repository_name
    branch_name       = var.github_branch_name
    directory_name    = var.github_directory_name
  }

  git_credentials = {
    source        = "ConfiguredConnection"
    connection_id = fabric_connection.github.id
  }
}

# The creator already has Admin; only grant Contributor to a separate user.
resource "fabric_workspace_role_assignment" "dev_contributor" {
  count = var.contributor_principal_id == null ? 0 : 1

  workspace_id = fabric_workspace.dev.id
  role         = "Contributor"
  principal = {
    id   = var.contributor_principal_id
    type = "User"
  }
}
