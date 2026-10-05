terraform {
  required_version = ">= 1.8, < 2.0"
  required_providers {
    fabric = {
      source  = "microsoft/fabric"
      version = "1.12.0"
    }
     azapi = {
            source  = "Azure/azapi"
            version = "~> 2.5"
        }
        random = {
            source  = "hashicorp/random"
            version = "~> 3.6"
        }
  }
}