#!/bin/bash
# Run acpx codex without OPENAI_API_KEY so it uses OAuth (chatgpt method)
unset OPENAI_API_KEY
exec /opt/homebrew/bin/acpx "$@"
