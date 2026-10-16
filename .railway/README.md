# .railway

`railway.ts` declares the api-kaianolevine-com Railway service as Railway
Infrastructure as Code. It is a named partial (`api-kaianolevine-com`): it
manages that service and nothing else in the `mini-app-polis ecosystem`
project, in production and in development.

It is applied by CI, never from a workstation: a pull request that changes
`.railway/` gets the plan as a comment (`.github/workflows/railway-plan.yml`),
and merging it applies exactly that plan (`railway-apply.yml`). A pull
request into `dev` plans development; into `main`, production. Both call the
shared workflows in `mini-app-polis/.github`. Railway itself does not read
this directory at deploy time.

A variable the service starts reading must be added to `SHARED` (or `ONLY`)
here, or the next apply removes it.
