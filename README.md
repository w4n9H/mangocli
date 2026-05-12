# Mango CLI

> A lightweight AI coding assistant running directly in your terminal.

Mango CLI is a zero-dependency, local-first AI coding assistant inspired by Claude Code.

It supports:

* AI-powered coding workflows
* File editing and shell execution
* Tool calling
* Context-aware conversation management
* Automatic context compacting

All with instant startup and no heavy framework dependencies.

---

# Features

* Zero dependency (Python standard library only)
* Instant startup
* Claude Code–style terminal UX
* Built-in file and shell tools
* Automatic context compacting
* Local session persistence
* Fully hackable and easy to extend

---

# Installation

## From PyPI

```bash
pip install mangocli
```

Start Mango CLI:

```bash
mango-cli
```

---

## From Source

```bash
git clone git@github.com:w4n9H/mangocli.git
cd mangocli
python mango_cli.py
```

---

# Configuration

Set your API configuration:

```bash
export MANGO_KEY="your_api_key"
export MANGO_API_URL="https://api.deepseek.com/chat/completions"
export MANGO_MODEL="deepseek-v4-flash"
```

Optional:

```bash
export MANGO_MAX_CONTEXT=1000000
export MANGO_LANG=zh
```

---

# Usage

Start the CLI:

```bash
mango-cli
```

or:

```bash
python mango_cli.py
```

Built-in commands:

| Command | Description     |
|---------|-----------------|
| `/q`    | Quit            |
| `/n`    | New session     |
| `/c`    | Compact session |
| `/h`    | Help            |

---

# Built-in Tools

* `read`
* `write`
* `edit`
* `search`
* `grep`
* `bash`

Mango CLI can autonomously inspect files, modify code, search projects, and execute shell commands.

---

# Philosophy

Mango CLI focuses on:

* fast startup
* zero dependency
* local-first workflows
* terminal-native AI interaction
* lightweight runtime design

No Electron, Docker, Redis, or heavyweight AI frameworks.

Just a fast and hackable AI coding assistant for the terminal.

---

# License

Apache License 2.0
