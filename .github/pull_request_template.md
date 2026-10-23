## Summary

<!-- What changes and why. Link the issue, if any. -->

## Validation

<!-- The checks you ran, and what you could not verify: cloud permissions, preview availability, quota or ingestion. -->

## Checklist

- [ ] CI passes.
- [ ] No credentials, tfvars, `.env` files, state, saved plans, logs, or tenant, subscription or physical Fabric IDs.
- [ ] No live `plan`, `apply`, `destroy`, deployment or notebook run was used as a test.
- [ ] `.platform` logical IDs and lineage tags are unchanged, and `generated/` was regenerated rather than edited.
- [ ] README, getting started and `infra/terraform.tfvars.example` reflect any input or behavior change.
