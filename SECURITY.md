# Security policy

## Supported versions

| Version | Supported |
|---|---|
| 1.0.x | yes |

## Reporting a vulnerability

Please report security issues privately through GitHub: open the repository's **Security** tab and choose **Report a vulnerability** (a private security advisory). Do not open a public issue for a vulnerability.

Include what you found, how to reproduce it and what an attacker could do with it. You should get a first answer within a week. This is a personal project maintained in spare time, so fixes are best effort.

## Scope

In scope: the MCP servers in `src/`, the Docker image, and the GitHub Actions workflows. The security model is described in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md#5-security-model). Known limits (shared-secret tokens, no rate limiting, sessions not yet bound to tokens) are listed in the README's [Roadmap](README.md#roadmap) and are not vulnerabilities by themselves.
