# Regeneration proof

1. Generate Steward via CLI to `factory_outputs/Projectsix`
2. Generate again via Product Architect brief → `/tmp/steward_via_architect`
3. Unit test `test_generate_regenerate` proves delete/regenerate hash stability for basic + Steward scaffolds

Factory tests: **16 passed** including hat/workflow/architect coverage.
