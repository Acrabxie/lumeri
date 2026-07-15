# Contributing to Lumeri

Thanks for helping improve Lumeri. Keep changes focused, explain the creator
workflow they improve, and preserve compatibility with the local public build.

## Set up a development environment

Lumeri requires Python 3.12 or newer and FFmpeg.

```bash
python -m pip install -e ".[dev]"
python -m pytest tests/ -q
```

## Submit a change

1. Create a branch from the latest `main`.
2. Add or update tests for behavior changes.
3. Run the full test suite and check that no credentials, private media,
   personal filesystem paths, or generated artifacts are included.
4. Open a pull request describing the user-visible outcome, implementation,
   and verification performed.

Pull requests must pass the Tests and CodeQL checks before merging.

## Public repository boundary

This repository contains the single-user local creative runtime. Do not add
hosted authentication, email delivery, cloud account management, billing, or
subscription implementations. Keep API keys and other credentials in local
configuration only; never commit them to the repository, examples, logs, or
test fixtures.

## Security reports

Do not disclose vulnerabilities in a public issue. Follow
[SECURITY.md](SECURITY.md) and use GitHub private vulnerability reporting.
