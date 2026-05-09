#!/usr/bin/env python3
import copy
import difflib
import glob
import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import glob as globlib
from urllib.parse import urlparse
from typing import List, Dict, Any, Optional

MANGO_KEY = os.environ.get("MANGO_KEY")
MANGO_API_URL = os.environ.get("MANGO_API_URL")
MANGO_MODEL = os.environ.get("MANGO_MODEL")
MANGO_MAX_CONTEXT = int(os.environ.get("MANGO_MAX_CONTEXT", 128000))

project_root = os.getcwd()
base_persist_dir = os.path.join(project_root, '.mangocli')

# ANSI colors
RESET, BOLD, DIM = "\033[0m", "\033[1m", "\033[2m"
BLUE, CYAN, GREEN, YELLOW, RED, GREY = ("\033[34m", "\033[36m", "\033[32m", "\033[33m", "\033[31m", "\033[90m")


def _c(text, color):
    return f"{color}{text}{RESET}"


class Printer:
    SPINNER_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]

    def __init__(self):
        self._spinner_running = False
        self._spinner_thread = None
        self._spinner_message = ""
        self._lock = threading.RLock()

    @staticmethod
    def _clear_spinner_line():
        sys.stdout.write("\r\033[K")
        sys.stdout.flush()

    def _write_line(self, text: str = ""):
        with self._lock:
            was_running = self._spinner_running
            if was_running:
                self._clear_spinner_line()
            print(text)
            if was_running:
                self._render_spinner_frame()

    def _render_spinner_frame(self, frame: str = "⠋"):
        text = f"{frame} {self._spinner_message}"
        sys.stdout.write("\r" + text)
        sys.stdout.flush()

    def section(self, title):
        self._write_line()
        self._write_line(_c(f"• {title}", GREY))

    def kv(self, data: Dict[str, Any]):
        for k, v in data.items():
            self._write_line(f"{_c(str(k), GREY)}{_c(': ', GREY)}{_c(str(v), GREY)}")

    def tool_call(self, name: str, desc: str):
        self.section("Tool Call")
        self._write_line(f"{_c('› ', GREY)}{_c(name, CYAN)}  {_c(desc, GREY)}")

    def tool_result(self, ok=True, meta=""):
        icon = "✓" if ok else "✗"
        color = GREEN if ok else RED
        suffix = f" {meta}" if meta else ""
        self._write_line(f"  {_c(icon, color)}{_c(suffix, GREY)}")

    def success(self, msg: str):
        self._write_line(f"{_c('✓ ', GREEN)}{_c(msg, GREY)}")

    def error(self, msg: str):
        self._write_line(f"{_c('✗ ', RED)}{_c(msg, GREY)}")

    def warning(self, msg: str):
        self._write_line(f"{_c('! ', YELLOW)}{_c(msg, GREY)}")

    def info(self, msg: str):
        self._write_line(f"{_c('i ', GREY)}{_c(msg, GREY)}")

    def text(self, msg: str):
        self._write_line(_c(msg, GREY))

    def separator(self):
        self._write_line(f"{DIM}{'─' * min(os.get_terminal_size().columns, 80)}{RESET}")

    def thinking(self, content: str):
        self.section("Thinking")
        for line in content.splitlines():
            self._write_line("  " + _c(line, GREY))

    def output(self, content: str):
        self.section("Output")
        for line in content.splitlines():
            self._write_line("  " + _c(line, GREY))

    def token_usage(self, iteration: int, input_tokens: int, output_tokens: int, context_tokens: int, max_context: int):
        def fmt(n):
            if n >= 1000:
                return f"{n / 1000:.1f}k"
            return str(n)

        ratio = context_tokens / max_context if max_context else 0
        percent = int(ratio * 100)
        color = GREEN if percent < 50 else YELLOW if percent < 70 else RED

        self._write_line()
        self._write_line(
            _c(f"round: {iteration} | tokens: {fmt(input_tokens)} in / {fmt(output_tokens)} out |  ctx: ", GREY) +
            _c(f"{percent}%", color)
        )

    @staticmethod
    def prompt_apply(message: str) -> bool:
        while True:
            resp = input(f"{YELLOW}{message} [y/n]: {RESET}").strip().lower()
            if resp in ("y", "yes"):
                return True
            elif resp in ("n", "no"):
                return False
            else:
                print("请输入 y 或 n")

    def diff(self, old: str, new: str, context: int = 3, filename: str = "file.py"):
        old_lines = old.splitlines()
        new_lines = new.splitlines()

        diff_lines = difflib.unified_diff(
            old_lines, new_lines, fromfile=f"a/{filename}", tofile=f"b/{filename}", lineterm="", n=context,
        )

        for dl in diff_lines:
            if dl.startswith("+") and not dl.startswith("+++"):
                self._write_line(_c(dl, GREEN))
            elif dl.startswith("-") and not dl.startswith("---"):
                self._write_line(_c(dl, RED))
            elif dl.startswith("@@"):
                self._write_line(_c(dl, CYAN))
            else:
                self._write_line(_c(dl, GREY))

    def start_spinner(self, message: str = "Running..."):
        if self._spinner_running:
            return
        self._spinner_running = True
        self._spinner_message = message

        def run():
            i = 0
            while self._spinner_running:
                with self._lock:
                    frame = self.SPINNER_FRAMES[i % len(self.SPINNER_FRAMES)]
                    self._render_spinner_frame(frame)
                time.sleep(0.1)
                i += 1

        self._spinner_thread = threading.Thread(
            target=run,
            daemon=True
        )
        self._spinner_thread.start()

    def end_spinner(self):
        if not self._spinner_running:
            return
        self._spinner_running = False
        if self._spinner_thread:
            self._spinner_thread.join()
        with self._lock:
            self._clear_spinner_line()


console = Printer()


# --- i18n


# --- Init dir, Base data ---
def initialize_system():
    if os.path.exists(base_persist_dir):
        os.mkdir(base_persist_dir)


def helper():
    console.text(f"帮助信息")


# --- Tool definitions: (description, schema, function) ---
def read(args):
    lines = open(args["path"]).readlines()
    offset = args.get("offset", 0)
    limit = args.get("limit", len(lines))
    selected = lines[offset: offset + limit]
    return "".join(f"{offset + idx + 1:4}| {line}" for idx, line in enumerate(selected))


def write(args):
    with open(args["path"], "w") as f:
        f.write(args["content"])
    return f"write {len(args['content'])}byte to {len(args['path'])} ok"


def edit(args):
    text = open(args["path"]).read()
    old, new = args["old"], args["new"]
    if old not in text:
        return "edit error: old_string not found"
    count = text.count(old)
    if not args.get("all") and count > 1:
        return f"error: old_string appears {count} times, must be unique (use all=true)"
    replacement = (
        text.replace(old, new) if args.get("all") else text.replace(old, new, 1)
    )
    with open(args["path"], "w") as f:
        f.write(replacement)
    return f"edit {len(args['path'])} ok"


def search(args):
    pattern = (args.get("path", ".") + "/" + args["pat"]).replace("//", "/")
    files = globlib.glob(pattern, recursive=True)
    files = sorted(
        files,
        key=lambda f: os.path.getmtime(f) if os.path.isfile(f) else 0,
        reverse=True,
    )
    return "\n".join(files) or "none"


def grep(args):
    pattern = re.compile(args["pat"])
    hits = []
    for filepath in glob.glob(args.get("path", ".") + "/**", recursive=True):
        try:
            for line_num, line in enumerate(open(filepath), 1):
                if pattern.search(line):
                    hits.append(f"{filepath}:{line_num}:{line.rstrip()}")
        except Exception as err:
            return f"grep tool error: {err}"
    return "\n".join(hits[:50]) or "none"


def bash(args):
    proc = subprocess.Popen(
        args["cmd"], shell=True,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True
    )
    output_lines = []
    try:
        while True:
            line = proc.stdout.readline()
            if not line and proc.poll() is not None:
                break
            if line:
                # print(f"  {DIM}│ {line.rstrip()}{RESET}", flush=True)
                output_lines.append(line)
        proc.wait(timeout=60)
    except subprocess.TimeoutExpired:
        proc.kill()
        output_lines.append("\n(timed out after 60s)")
    return "".join(output_lines).strip() or "(empty)"


def attempt_completion(args):
    return args["result"]


TOOLS = {
    "read": (
        "Read a file from the local filesystem",
        {
            "path": {"type": "string", "description": "Path to the file to read"},
            "offset": {"type": "number?", "description": "Line number to start reading from (0-indexed, default 0)"},
            "limit": {"type": "number?", "description": "Maximum number of lines to read (default: all lines)"}
        },
        read,
    ),
    "write": (
        "Write content to a file, overwriting if it exists",
        {
            "path": {"type": "string", "description": "Path to the file to write"},
            "content": {"type": "string", "description": "Content to write to the file"}
        },
        write,
    ),
    "edit": (
        "Edit a file by replacing an exact string with a new string",
        {
            "path": {"type": "string", "description": "Path to the file to edit"},
            "old": {"type": "string", "description": "Exact string to be replaced"},
            "new": {"type": "string", "description": "String to replace it with"},
            "all": {"type": "boolean?", "description": "Replace all occurrences (default: false)"}
        },
        edit,
    ),
    "search": (
        "Search for files using a glob pattern",
        {
            "pat": {"type": "string", "description": "Glob pattern to match file paths (e.g. '**/*.py')"},
            "path": {"type": "string?", "description": "Directory to start search from (default: current directory)"}
        },
        search,
    ),
    "grep": (
        "Search file contents recursively using a regular expression pattern",
        {
            "pat": {
                "type": "string",
                "description": "Regular expression pattern to search for (Python regex syntax)"},
            "path": {
                "type": "string?",
                "description": "Search directory to recursively (defaults to current working directory if omitted)"}
        },
        grep,
    ),
    "bash": (
        "Execute a shell command and return its stdout/stderr output (timeout after 60s)",
        {
            "cmd": {"type": "string", "description": "The shell command to execute, e.g., 'ls -la' or 'git status'"}
        },
        bash,
    ),
    "attempt_completion": (
        "Indicate that the task is complete and provide the final result/answer to the user",
        {
            "result": {"type": "string", "description": "The final result or summary of the completed task"}
        },
        attempt_completion,
    ),
}


def tool_schema():
    result = []
    for name, (description, params, _fn) in TOOLS.items():
        properties = {}
        required = []
        for param_name, param_info in params.items():
            param_type = param_info['type']
            is_optional = param_type.endswith("?")
            base_type = param_type.rstrip("?")
            properties[param_name] = {
                "type": "integer" if base_type == "number" else base_type,
                "description": param_info['description']
            }
            if not is_optional:
                required.append(param_name)
        result.append(
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": description,
                    "parameters": {
                        "type": "object",
                        "properties": properties,
                        "required": required,
                    }
                }
            }
        )
    return result


# --- Context manager: () ---
class ContextManager:
    def __init__(self):
        self.messages: List[Dict] = []
        self.white_tool_list = []

        self.auto_compact_threshold = int(MANGO_MAX_CONTEXT * 0.8)
        self.auto_compact_disabled = False
        self.continuous_failures = 0
        self.max_failures = 3

    def __len__(self):
        return len(self.messages)

    def disabled_compact(self):
        self.auto_compact_disabled = True

    def enabled_compact(self):
        self.auto_compact_disabled = False

    def set_max_failures(self, n: int = 3):
        self.max_failures = n

    def append_system(self, content: str):
        self.messages.append({"role": "system", "content": content})

    def append_user(self, content: str):
        self.messages.append({"role": "user", "content": content, "ts": int(time.time())})

    def append_assistant(self, content: dict):
        content.update({"ts": int(time.time())})
        self.messages.append(content)

    def append_tool(self, tool_call_id: str, content: str):
        self.messages.append({"role": "tool", "tool_call_id": tool_call_id, "content": content, "ts": int(time.time())})

    def load(self, persist_file: str):
        if os.path.exists(persist_file):
            with open(persist_file, "r", encoding="utf-8") as f:
                self.messages = json.load(f)

    def save(self, persist_file: str):
        with open(persist_file, "w", encoding="utf-8") as fp:
            fp.write(json.dumps(self.messages, indent=2, ensure_ascii=False))

    def add(self, message: dict):
        message.update({"ts": int(time.time())})
        self.messages.append(message)

    def get_messages(self) -> List[Dict[str, Any]]:
        return self.messages

    def get_latest(self, n: int = 10) -> List[Dict]:
        return self.messages[-n:]

    @staticmethod
    def estimated_tokens(msg: Dict[str, Any]) -> int:  # token 估算 (粗略)
        content_len = len(msg.get("content", ""))
        return content_len // 4 + 4

    def total_tokens(self) -> int:
        return sum(self.estimated_tokens(m) for m in self.messages)

    def auto_compact_if_needed(self):
        if self.auto_compact_disabled:
            return

        if self.total_tokens() < self.auto_compact_threshold:
            return

        if self.continuous_failures >= self.max_failures:
            return

        try:    # 尝试会话记忆压缩
            success = self.session_memory_compact()
            if success and self.total_tokens() < self.auto_compact_threshold:
                self.continuous_failures = 0
                return
        except Exception:
            self.continuous_failures += 1

        try:    # 回退传统压缩
            self.compact_conversation()
            self.continuous_failures = 0
        except Exception:
            self.continuous_failures += 1

    def micro_compact(self, max_age_seconds: int = 21_600):
        """ 扫描消息数组，查找来自可压缩工具白名单的 tool_result 块，并将其内容替换为 <Old tool result content cleared> """
        now = int(time.time())
        for m in self.messages:  # 如果是工具消息且很旧 → 用占位符替换
            if m.get("role") == "tool" and now - m.get("ts", now) > max_age_seconds:
                m["content"] = "<Old tool result content expired(6hours)>"

    def session_memory_compact(self, retain_count: int = 100) -> bool:
        """ 保留最近用户 + 助手消息，剥离旧工具结果, 返回: True 压缩成功 """
        new_msgs = []
        for m in self.messages:
            if m.get("role") == "system":  # 先保留 system 消息
                new_msgs.append(copy.deepcopy(m))

        non_system = [m for m in self.messages if m.get("role") != "system"]
        recent_msgs = non_system[-retain_count:]    # 保留最后 100 条消息
        for m in recent_msgs:
            if m.get("role") == "tool":
                m = copy.deepcopy(m)
                m["content"] = "<Old tool result content compacted>"
            new_msgs.append(m)
        self.messages = new_msgs
        return True

    def compact_conversation(self):
        """ 剥离大附件， 工具输出等内容，用占位符替代旧内容，保证 token 降到阈值以下 """
        new_msgs = []
        for m in self.messages:
            m_copy = copy.deepcopy(m)
            role = m_copy.get("role")
            if role == "tool" and len(m_copy.get("content", "")) > 200:
                m_copy["content"] = "<Old tool result content removed>"
            elif role == "assistant":
                if len(m_copy.get("content", "")) > 500:
                    m_copy["content"] = "<Old assistant content removed>"
                if len(m_copy.get("reasoning_content", "")) > 500:
                    m_copy["reasoning_content"] = "<Old assistant reasoning_content removed>"
            new_msgs.append(m_copy)

        systems = [m for m in new_msgs if m.get("role") == "system"]
        others = [m for m in new_msgs if m.get("role") != "system"]
        while sum(self.estimated_tokens(m) for m in systems + others) > self.auto_compact_threshold:  # 如果还是超长, 从头删除旧消息
            if not others:  # 防止无限循环和 IndexError
                break
            others.pop(0)

        self.messages = systems + others

    def full_compact(self):    # 手动执行，调用模型进行大规模的摘要生成，后续实现
        pass

    def prepare_for_api(self):
        self.micro_compact()
        self.auto_compact_if_needed()
        return self.get_messages()


def chat_completion(messages: List[Dict[str, str]], timeout: int = 60):
    extra_body = {
        "thinking": {"type": "enabled"}
    }
    body = {
        "model": MANGO_MODEL,
        "messages": messages,
        "stream": False,
        "extra_body": extra_body,
        "tools": tool_schema()
    }
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {MANGO_KEY}",
    }
    request = urllib.request.Request(MANGO_API_URL, data=json.dumps(body).encode(), headers=headers, method="POST", )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw_data = response.read().decode("utf-8")
            return json.loads(raw_data)
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8", errors="ignore")
        raise urllib.error.HTTPError(e.url, e.code, f"{e.msg} - {error_body}", e.hdrs, e.fp) from e
    except urllib.error.URLError as e:
        raise urllib.error.URLError(f"请求失败: {e.reason}") from e
    except json.JSONDecodeError as e:
        raise json.JSONDecodeError(f"响应非 JSON: {raw_data[:200]}", e.doc, e.pos, ) from e


def parse_chat_completion(response: Dict[str, Any]) -> Dict[str, Any]:
    choices = response.get("choices", [])
    if not choices:
        return {
            "finish_reason": None,
            "raw_message": {},
            "content": "",
            "reasoning_content": None,
            "tool_calls": [],
            "has_tool_calls": False,
            "model": response.get("model", ""),
            "usage": response.get("usage", {})
        }

    choice = choices[0]
    message = choice.get("message", {})
    finish_reason = choice.get("finish_reason", "stop")
    # 提取文本内容
    content = message.get("content", "") or ""
    # 提取推理内容（DeepSeek reasoner 模型专用）
    reasoning_content = message.get("reasoning_content", "") or ""
    # 处理工具调用
    raw_tool_calls = message.get("tool_calls", [])
    tool_calls = []
    for tc in raw_tool_calls:
        function = tc.get("function", {})
        args_str = function.get("arguments", "{}")
        try:
            arguments = json.loads(args_str) if args_str else {}
        except json.JSONDecodeError as e:
            raise json.JSONDecodeError(f"工具调用参数响应非 JSON: {args_str[:200]}", e.doc, e.pos, ) from e
        tool_calls.append({
            "name": function.get("name", ""),
            "arguments": arguments,
            "id": tc.get("id", ""),
            "type": tc.get("type", "function")
        })

    return {
        "finish_reason": finish_reason,
        "raw_message": message,
        "content": content,
        "reasoning_content": reasoning_content,
        "tool_calls": tool_calls,
        "has_tool_calls": bool(tool_calls),
        "model": response.get("model", ""),
        "usage": response.get("usage", {})
    }


def run_tool(tool_name, tool_args):
    try:
        result = ""
        arg_preview = str(list(tool_args.values())[0])[:50]
        console.tool_call(tool_name, arg_preview)

        if tool_name == "edit":
            console.diff(old=tool_args["old"], new=tool_args["new"])
            if console.prompt_apply(f"Apply changes to {tool_args['path']}?"):
                result = TOOLS[tool_name][2](tool_args)
        else:
            console.start_spinner()
            result = TOOLS[tool_name][2](tool_args)
            console.end_spinner()

            result_lines = result.split("\n")
            preview = result_lines[0][:60]
            if len(result_lines) > 1:
                preview += f" ... +{len(result_lines) - 1} lines"
            elif len(result_lines[0]) > 60:
                preview += "..."
            print(f"  {DIM}⎿  {preview}{RESET}")

        console.tool_result(True)

        return result
    except Exception as err:
        return f"error: {err}"


def main():
    initialize_system()

    print(f"{BOLD}Mango Cli{RESET} | {DIM}{MANGO_MODEL} ({urlparse(MANGO_API_URL).netloc}) | {project_root}{RESET}\n")

    ctx_file_path = os.path.join(project_root, ".mangocli", "session.json")
    ctx = ContextManager()
    ctx.enabled_compact()
    ctx.load(ctx_file_path)

    if len(ctx) == 0:    # 刚初始化的ctx才需要system prompt
        ctx.append_system(f"Concise coding assistant. cwd: {project_root}")

    while True:
        try:
            console.separator()
            user_input = input(f"{BOLD}{BLUE}❯{RESET} ").strip()
            if not user_input:
                continue
            if user_input.startswith('/'):
                if user_input.strip() == "/q":
                    break
                if user_input.strip() == "/c":
                    pass
                if user_input.strip() == "/m":
                    pass
                if user_input.strip() == "/h":
                    helper()

            ctx.append_user(user_input)

            # agentic loop: keep calling API until no more tool calls
            iteration = 0
            context_tokens = 0
            while True:
                console.start_spinner("Request...")
                response = parse_chat_completion(chat_completion(ctx.prepare_for_api()))
                console.end_spinner()

                ctx.append_assistant(response["raw_message"])

                iteration += 1
                context_tokens += response["usage"]["total_tokens"]
                console.token_usage(
                    iteration=iteration,
                    input_tokens=response["usage"]["prompt_tokens"],
                    output_tokens=response["usage"]["completion_tokens"],
                    context_tokens=ctx.total_tokens(),
                    max_context=MANGO_MAX_CONTEXT)

                if response["content"]:
                    console.output(response["content"])

                if response["reasoning_content"]:
                    console.thinking(response["reasoning_content"])

                if response["has_tool_calls"]:
                    has_attempt = False
                    tool_calls = response["tool_calls"]
                    for tool in tool_calls:
                        tool_name, tool_args = tool["name"], tool["arguments"]

                        result = run_tool(tool_name, tool_args)

                        if tool_name == "attempt_completion":
                            has_attempt = True
                            console.text(result)

                        ctx.append_tool(tool["id"], result)
                    if has_attempt:
                        break
            ctx.save(ctx_file_path)
        except (KeyboardInterrupt, EOFError):
            break
        except Exception as err:
            print(f"{RED}⏺ Error: {err}{RESET}")
        finally:
            ctx.save(ctx_file_path)


if __name__ == '__main__':
    main()
