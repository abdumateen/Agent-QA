# Security Policy

## Supported versions

Security fixes are applied to the latest released version and the current
development branch.

| Version | Supported |
| --- | --- |
| `0.1.x` | Yes |
| Older releases | No |

Upgrade to the latest release before reporting a vulnerability that may already
be fixed.

## Reporting a vulnerability

Do not report security vulnerabilities in public issues, discussions, or pull
requests.

Use the repository's private security advisory mechanism to submit a report:

https://github.com/agent-qa/agent-qa/security/advisories/new

If private advisory submission is unavailable, contact the repository
maintainers through the private contact channel configured for the repository.
Do not include credentials, production data, or unnecessary personal
information in the initial report.

## Report contents

Include enough information to reproduce and assess the issue:

- A concise description of the vulnerability.
- Affected version or commit.
- Affected component and endpoint.
- Reproduction steps or a minimal proof of concept.
- Expected and observed behavior.
- Security impact.
- Any required configuration.
- Suggested mitigation, if known.

Please allow maintainers reasonable time to investigate before public
disclosure.

## Response process

Maintainers will:

1. Acknowledge receipt when practical.
2. Reproduce and assess the report.
3. Determine affected versions and severity.
4. Prepare a fix or mitigation.
5. Coordinate disclosure timing with the reporter.
6. Publish release notes when a fix is available.

Reports made in good faith will be handled confidentially where practical.

## Local service security

Agent QA is intended for local use. The core service and MCP SSE process bind
to `127.0.0.1` only. Do not change either listener to a wildcard address.

The REST service does not provide remote authentication. Any process that can
access the loopback listener can issue memory operations. Do not expose the
service through a reverse proxy or container network without adding an
appropriate authentication and authorization layer.

## Sensitive data

Do not store credentials, access tokens, private keys, session cookies, or
production secrets in:

- Fixtures.
- API contracts.
- Failure messages.
- Stack traces.
- Test-run error messages.
- Issue reports.
- Log files.
- Database backups.

Remove sensitive values before sharing logs or database extracts.

## Database and backup protection

The default database is:

```text
~/.agent-qa/memory.db