# Security Policy

## Supported versions

composerV is pre-1.0 and moves fast. Security fixes land on `main` and in the latest
release only.

## Reporting a vulnerability

Please do not open a public issue for security problems.

Use GitHub's private vulnerability reporting: go to the repository's **Security** tab and
choose **Report a vulnerability**. This opens a private channel with the maintainer.

Include what you found, how to reproduce it, and the impact you expect. You will get a
response as soon as possible.

## Scope notes

composerV is local-first: it runs on your machine and your footage stays local. The main
areas worth scrutiny are the subprocess calls to `ffmpeg` and the `claude` CLI, the
handling of file paths from scanned media folders, and the optional Anthropic API path
(`ANTHROPIC_API_KEY`). The bundled ML models carry their own licenses and terms; see
`THIRD_PARTY_NOTICES.md`.
