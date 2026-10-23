<!-- Template-owned: a derived repository should replace this file with its own security policy. -->

<!-- BEGIN MICROSOFT SECURITY.MD V1.0.0 BLOCK -->

## Security

Microsoft takes the security of our software products and services seriously, which
includes all source code repositories in our GitHub organizations.

**Please do not report security vulnerabilities through public GitHub issues.**

For security reporting information, locations, contact information, and policies,
please review the latest guidance for Microsoft repositories at
[https://aka.ms/SECURITY.md](https://aka.ms/SECURITY.md).

<!-- END MICROSOFT SECURITY.MD BLOCK -->

## Sensitive deployment data

Never put credentials, tokens, keys, tfvars, `.env` files, Terraform state, saved plans,
debug logs, or tenant, subscription or physical Fabric IDs in issues, pull requests or
commits. Redact them from any output you share. If you committed one by mistake, rotate
it first; deleting the file does not remove it from Git history.
