---
name: find-skills
description: Helps users discover agent skills within the current authorization scope. In normal user sessions, only list or discuss skills allowed by the current agent's agent.yaml skills allowlist; do not expose other installed or external skills as current capabilities. In workflow sessions, the full installed skill catalog may be inspected.
description_cn: 帮助用户在当前授权范围内发现 agent skills。普通 user session 中，只能列出或讨论当前 agent 的 agent.yaml skills allowlist 允许的技能；不要把其他已安装或外部技能描述为当前可用能力。workflow session 中，可以查看完整的已安装技能目录。
category: system
---

# Find Skills

This skill helps you discover skills within the current SmartClaw authorization scope.

## Authorization Boundary

Before answering any question about "skills", "available skills", "skill sources", or "what skills can Titan use", decide which catalog is authorized:

- Normal `user` sessions: only use the skills allowed by the current agent's `agent.yaml` `skills` list. For Titan, answer from Titan's allowlist unless the runtime explicitly says otherwise.
- `workflow` sessions: the full installed skill catalog may be inspected.
- External registries and installable candidates are not current capabilities. If you mention them, clearly label them as external candidates that are not installed or not authorized for the current agent.

Do not infer current capability from local files, installed plugin directories, product names, or tool names if the skill is outside the current agent allowlist.

## When to Use This Skill

Use this skill when the user:

- Asks which skills are available in the current session
- Asks "find a skill for X" or "is there a skill for X"
- Asks whether the current agent is authorized to use a specialized skill
- Expresses interest in extending agent capabilities
- Wants to distinguish current skills from external installable candidates

## What is the Skills CLI?

The Skills CLI (`npx skills`) is the package manager for the open agent skills ecosystem. External skills are modular packages that can extend agent capabilities with specialized knowledge, workflows, and tools after installation and authorization.

**Key commands:**

- `npx skills find [query]` - Search for skills interactively or by keyword
- `npx skills add <package>` - Install a skill from GitHub or other sources
- `npx skills check` - Check for skill updates
- `npx skills update` - Update all installed skills

**Browse skills at:** https://skills.sh/

## How to Help Users Find Skills

### Step 1: Understand What They Need and What Is Authorized

When a user asks for help with something, identify:

1. The domain (e.g., React, testing, design, deployment)
2. The specific task (e.g., writing tests, creating animations, reviewing PRs)
3. Whether the current agent is authorized to use a matching installed skill
4. Whether the user is asking about current capabilities or external candidates

For current capabilities, use the current session's authorized skill list only. In normal user sessions, do not list installed skills outside the current agent's allowlist.

### Step 2: Check the Leaderboard First

Before running a CLI search, check the [skills.sh leaderboard](https://skills.sh/) to see if a well-known skill already exists for the domain. The leaderboard ranks skills by total installs, surfacing the most popular and battle-tested options.

For example, top skills for web development include:
- `vercel-labs/agent-skills` — React, Next.js, web design (100K+ installs each)
- `anthropics/skills` — Frontend design, document processing (100K+ installs)

### Step 3: Search for Skills

Only search external registries when the user explicitly asks for installable candidates or wants to extend the system. Do not use external search to answer "what can Titan currently use?".

If external search is appropriate, run the find command:

```bash
npx skills find [query]
```

For example:

- User asks "how do I make my React app faster?" → `npx skills find react performance`
- User asks "can you help me with PR reviews?" → `npx skills find pr review`
- User asks "I need to create a changelog" → `npx skills find changelog`

### Step 4: Verify Quality Before Recommending

**Do not recommend a skill based solely on search results.** Always verify:

1. **Install count** — Prefer skills with 1K+ installs. Be cautious with anything under 100.
2. **Source reputation** — Official sources (`vercel-labs`, `anthropics`, `microsoft`) are more trustworthy than unknown authors.
3. **GitHub stars** — Check the source repository. A skill from a repo with <100 stars should be treated with skepticism.

### Step 5: Present Options to the User

When you find relevant external candidates, present them to the user with:

1. The skill name and what it would do if installed and authorized
2. The install count and source
3. The install command they can run
4. A link to learn more at skills.sh

Example response for an external candidate:

```
I found an external skill candidate that might help. It is not a current
authorized capability yet. The "react-best-practices" skill provides
React and Next.js performance optimization guidelines from Vercel Engineering.
(185K installs)

To install it:
npx skills add vercel-labs/agent-skills@react-best-practices

Learn more: https://skills.sh/vercel-labs/agent-skills/react-best-practices
```

### Step 6: Offer to Install

If the user wants to proceed, you can install the skill for them:

```bash
npx skills add <owner/repo@skill> -g -y
```

The `-g` flag installs globally (user-level) and `-y` skips confirmation prompts.

## Common Skill Categories

When searching, consider these common categories:

| Category        | Example Queries                          |
| --------------- | ---------------------------------------- |
| Web Development | react, nextjs, typescript, css, tailwind |
| Testing         | testing, jest, playwright, e2e           |
| DevOps          | deploy, docker, kubernetes, ci-cd        |
| Documentation   | docs, readme, changelog, api-docs        |
| Code Quality    | review, lint, refactor, best-practices   |
| Design          | ui, ux, design-system, accessibility     |
| Productivity    | workflow, automation, git                |

## Tips for Effective Searches

1. **Use specific keywords**: "react testing" is better than just "testing"
2. **Try alternative terms**: If "deploy" doesn't work, try "deployment" or "ci-cd"
3. **Check popular sources**: Many skills come from `vercel-labs/agent-skills` or `ComposioHQ/awesome-claude-skills`

## When No Skills Are Found

If no relevant skills exist:

1. Acknowledge that no existing skill was found
2. Offer to help with the task directly using your general capabilities
3. Suggest the user could create their own skill with `npx skills init`

Example:

```
I searched for skills related to "xyz" but didn't find any matches.
I can still help you with this task directly! Would you like me to proceed?

If this is something you do often, you could create your own skill:
npx skills init my-xyz-skill
```
