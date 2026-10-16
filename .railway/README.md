# .railway

`railway.ts` declares the api-kaianolevine-com Railway service as Railway
Infrastructure as Code. It is a named partial (`api-kaianolevine-com`): it
manages that service and nothing else in the `mini-app-polis ecosystem`
project, and only in production.

It is applied by CI, never from a workstation, as mini-app-polis/infra
applies Terraform: a pull request into `main` that changes `.railway/` is
planned, with the plan in the job summary (`.github/workflows/railway-plan.yml`),
and merging it plans again and applies (`railway-apply.yml`). Removing a
service or a variable is destructive and applies only if the pull request is
labelled **`allow-destroy`**. Actions → Railway apply → Run workflow
re-applies `main` by hand. Both call the shared workflows in
`mini-app-polis/.github`. Railway itself does not read this directory at
deploy time.

Development is not managed here: Railway refuses `railway config plan`
with a development-scoped project token. The development service keeps the
settings its dashboard holds.

A variable the service starts reading must be added to `SHARED` here, or the
next apply removes it.
