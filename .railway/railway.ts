// Railway Infrastructure as Code for the remote MCP server (replaces railway.toml,
// which Railway stops reading on 2026-12-01).
// Docs: https://docs.railway.com/infrastructure-as-code
//
//   npm install --prefix .railway        # once: installs the `railway` SDK into .railway/node_modules
//   railway config plan                  # preview changes
//   railway config apply                 # apply them
//
// Windows: run the CLI binary itself, not the npm shim, e.g.
//   & "$env:APPDATA\npm\node_modules\@railway\cli\bin\railway.exe" config plan
// (railway SDK 3.17 re-runs the CLI through the `_` env var to check its version,
// and that fails for the shim or a wrapper such as `timeout`.)
//
// Variables are unmanaged on purpose: secrets (MCP_AUTH_TOKEN, DATABASE_URL,
// GOOGLE_APPLICATION_CREDENTIALS_JSON, ...) stay in Railway, never in this file.
import { defineRailway, project, service } from "railway/iac";

// This repository manages only its own resources in the environment.
// See https://docs.railway.com/infrastructure-as-code#multi-repo-projects
export const partial = "dialogflow-mcp-remote";

export default defineRailway(() => {
  const dialogflowMcpRemote = service("dialogflow-mcp-remote", {
    build: { buildEnvironment: "V3", builder: "DOCKERFILE", dockerfilePath: "Dockerfile" },
    // /ready is unauthenticated and skips the Host allowlist (like /health), but
    // also checks the database and required settings, so a deploy that can't
    // reach Neon or misses config fails its health check instead of going live.
    healthcheck: "/ready",
    healthcheckTimeout: 60,
    deploy: { restartPolicyType: "ON_FAILURE", restartPolicyMaxRetries: 5 },
    // US East (Virginia): next to Neon aws-us-east-1, so each SQL round trip stays in-region.
    replicas: { "us-east-1": 1 },
  });

  return project("dialogflow-mcp-server", {
    variables: { managed: false },
    resources: [dialogflowMcpRemote],
  });
});
