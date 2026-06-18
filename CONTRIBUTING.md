# Contributing to Lumeri

Thanks for your interest in Lumeri.

## Getting started

```bash
git clone https://github.com/Acrabxie/lumeri.git && cd lumeri
pip install -e ".[dev]"
```

You also need ffmpeg in PATH:

```bash
# macOS
brew install ffmpeg

# Ubuntu
sudo apt-get install ffmpeg
```

## Running tests

```bash
pytest tests/ -v --ignore=tests/test_ai --ignore=tests/test_video
```

## Submitting changes

1. Fork the repo and create a branch from main.
2. Make your change with a clear commit message.
3. Ensure tests pass locally.
4. Open a pull request.

## Reporting issues

Open a GitHub Issue. Include your OS, Python version, and the full error output.
