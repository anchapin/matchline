# Security Policy

This is a research prototype. If you find a security issue:

- **Do not** open a public issue.
- Email the maintainer (see the repo owner on GitHub) with a description
  and, if possible, a minimal reproducer.

## Scope notes

- The project processes architectural drawings and IFC files supplied by the
  user; it makes no network calls at runtime.
- No credentials, tokens, or secrets belong in this repo — `.gitignore`
  excludes env files, and CI does not handle secrets.
- Dependencies are declared in `pyproject.toml`; report a vulnerable
  dependency the same way as any other issue.
