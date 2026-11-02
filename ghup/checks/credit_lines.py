"""AI credit lines (co-author trailers, "generated with" notes) in files and in every commit message.

Allowed only when the project's CLAUDE.md says `Claude credit: yes`.
"""
import re

NAME = "credit_lines"
TITLE = "No AI credit lines (all commits)"

CREDIT = re.compile(r"(?i)^\s*co-authored-by\s*:.*\b(claude|anthropic|copilot|chatgpt|openai)\b|"
                    r"generated (by|with) \[?(claude|chatgpt|copilot)\b|noreply@anthropic\.com")


def run(ctx):
    if ctx.credit_allowed():
        return []
    return [ctx.finding(NAME, where, "credit line", line)
            for where, kind, line in ctx.texts() if kind != "name" and CREDIT.search(line)]
