#!/bin/bash
# Claude Code statusline hook — reads JSON from stdin, outputs status line
read -r data
model=$(echo "$data" | jq -r '.model // "?"')
cost=$(echo "$data" | jq -r '.total_cost_usd // 0' | xargs printf '$%.2f')
ctx=$(echo "$data" | jq -r '.context_window_percent // 0' | xargs printf '%.0f%%')
echo "$model | $cost | ctx: $ctx"
