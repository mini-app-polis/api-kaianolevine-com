// api-kaianolevine-com as Railway Infrastructure as Code, applied by CI: a
// pull request that changes .railway/ gets a plan comment, and merging it
// applies exactly that plan (.github/workflows/railway-plan.yml,
// railway-apply.yml). Pull requests into dev plan and apply in development;
// into main, in production. Nothing is applied from a workstation, and
// Railway itself does not read this file when it deploys. Replaces
// railway.json, which Railway stops reading on 2026-12-01. MINI-69.
//
// A named partial: the ecosystem project holds services from several
// repositories, so this file manages api-kaianolevine-com and nothing else.
// Removing the service from this file would delete it on the next apply.
//
// The start command is the image's CMD (Dockerfile), and is deliberately not
// declared here as well: two declarations are two to keep in step.
//
// Variables are references to the environment's shared variables, which
// Doppler keeps current (`ctx.shared.NAME` is `${{shared.NAME}}`); no value
// lives in this file. Only the names the service reads are declared: the
// Settings fields in src/kaianolevine_api/config.py, each declared
// machine's <NAME>_API_KEY (identity_registry.py), and what mini_app_polis
// reads on its behalf. DATABASE_URL is not shared — it references the
// Postgres service — and is kept as Railway has it.

import { defineRailway, github, preserve, project, service } from "railway/iac";

export const partial = "api-kaianolevine-com";

const REGION = "us-east4-eqdc4a";

const BRANCH: Record<string, string> = { production: "main", development: "dev" };
const DOMAIN: Record<string, string> = {
  production: "api.kaianolevine.com",
  development: "dev-api.kaianolevine.com",
};

const SHARED = [
  "ANTHROPIC_API_KEY",
  "AWS_REGION",
  "BREVO_API_KEY",
  "CLERK_ISSUER",
  "CLERK_JWKS_URL",
  "CONTACT_FROM_EMAIL",
  "CONTACT_TO_EMAIL",
  "CORS_ORIGINS",
  "DISCORD_WEBHOOK_URL_ACTIVITY",
  "DISCORD_WEBHOOK_URL_DEFAULT",
  "DISCORD_WEBHOOK_URL_ERRORS",
  "DISCORD_WEBHOOK_URL_RUNS",
  "ENVIRONMENT",
  "EVALUATION_QUEUE_PRODUCER_KEY_ID",
  "EVALUATION_QUEUE_PRODUCER_SECRET",
  "GH_TOKEN",
  "GITHUB_DASHBOARD_TOKEN",
  "GITHUB_TOKEN",
  "GITHUB_WEBHOOK_SECRET",
  "GOOGLE_CLIENT_EMAIL",
  "GOOGLE_PRIVATE_KEY",
  "LOGGING_LEVEL",
  "OPENAI_API_KEY",
  "RESUME_FILE_ID",
  "SENTRY_DSN_APIS",
  "SPOTIPY_CLIENT_ID",
  "SPOTIPY_CLIENT_SECRET",
  "TURNSTILE_SECRET_KEY",
  // Machine principals' keys (identity_registry.MACHINES).
  "CI_VALIDATOR_API_KEY",
  "DEEJAY_COG_API_KEY",
  "ECOSYSTEM_STANDARDS_API_KEY",
  "EVALUATOR_COG_API_KEY",
  "OPS_NOTIFIER_API_KEY",
  "TRANSCRIPTION_COG_API_KEY",
  "WATCHER_COG_API_KEY",
  "WIKI_CURATOR_COG_API_KEY",
];

// Read in one environment only. The API base URL is suffixed outside
// production (mini_app_polis.environment.env_var); the Spotify re-auth runs
// against prd only; contract-suite is a development-only machine.
const ONLY: Record<string, string[]> = {
  production: ["KAIANO_API_BASE_URL", "DOPPLER_SPOTIFY_WRITE_TOKEN", "SPOTIFY_OWNER_USER_ID"],
  development: ["KAIANO_API_BASE_URL_DEV", "CONTRACT_SUITE_API_KEY"],
};

export default defineRailway((ctx) => {
  const environment = ctx.environment ?? "";
  const name = ctx.projectName ?? "mini-app-polis ecosystem";
  if (!(environment in BRANCH)) {
    return project(name, { resources: [] });
  }

  const names = [...SHARED, ...ONLY[environment]];
  const api = service("api-kaianolevine-com", {
    source: github("mini-app-polis/api-kaianolevine-com", { branch: BRANCH[environment] }),
    // Root Dockerfile with digest-pinned bases and a non-root user (CD-021).
    build: { builder: "DOCKERFILE" as const, dockerfilePath: "Dockerfile" },
    replicas: { [REGION]: 1 },
    domains: [{ domain: DOMAIN[environment], port: 8080 }],
    // Its private hostname, courteous-purpose.railway.internal.
    networking: { privateNetworkEndpoint: "courteous-purpose" },
    deploy: {
      healthcheckPath: "/health",
      healthcheckTimeout: 300,
      restartPolicyType: "ON_FAILURE" as const,
      restartPolicyMaxRetries: 10,
      limitOverride: { containers: { cpu: 4, memoryBytes: 4294967296 } },
    },
    env: {
      ...Object.fromEntries(names.map((n) => [n, ctx.shared[n]])),
      DATABASE_URL: preserve(),
    },
  });

  return project(name, { resources: [api] });
});
