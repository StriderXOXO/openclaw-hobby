# Security Policy

## Reporting a Vulnerability

If you discover a security vulnerability, please report it responsibly:

1. **Do NOT** open a public issue
2. Email the maintainer or use [GitHub Security Advisories](https://github.com/StriderXOXO/openclaw-hobby/security/advisories/new)
3. Include steps to reproduce and potential impact

We will respond within 48 hours and work on a fix promptly.

## Security Best Practices

- Never commit `.env` files or API keys (`.gitignore` blocks these)
- Use SSH key authentication over password for remote operations
- All credentials are loaded from environment variables, never hardcoded
