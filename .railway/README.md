# .railway

`railway.ts` declares the api-kaianolevine-com Railway service as Railway
Infrastructure as Code. It is a named partial (`api-kaianolevine-com`): it
manages that service and nothing else in the `mini-app-polis ecosystem`
project, in production and in development.

It is applied by CI, never from a workstation, as mini-app-polis/infra
applies Terraform (`.github/workflows/railway-apply.yml`):

- A push to `dev` that changes `.railway/` plans and applies to
  development, removals included — there is no pull request to label, and
  development is where a mistake in the file should show up first.
- The `dev` → `main` pull request is planned against production, with the
  plan in the job summary (`railway-plan.yml`). Merging it plans again and
  applies to production. Removing a service or a variable is destructive
  and applies only if the pull request is labelled **`allow-destroy`**.

Actions → Railway apply → Run workflow re-applies either branch by hand. Both call the
shared workflows in `mini-app-polis/.github`. Railway itself does not read
this directory at deploy time.

A variable the service starts reading must be added to `SHARED` (or `ONLY`)
here, or the next apply removes it.
