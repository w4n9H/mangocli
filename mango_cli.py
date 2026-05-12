#!/usr/bin/env python3

__version__ = "0.1.2"
__author__ = "moofs"
__license__ = "Apache License 2.0"

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
import platform
from datetime import datetime
from typing import List, Dict, Any, Optional

# --- System Env ---
MANGO_KEY = os.environ.get("MANGO_KEY")
MANGO_API_URL = os.environ.get("MANGO_API_URL")
MANGO_MODEL = os.environ.get("MANGO_MODEL")
MANGO_MAX_CONTEXT = int(os.environ.get("MANGO_MAX_CONTEXT", 128000))

project_root = os.getcwd()
base_persist_dir = os.path.join(project_root, '.mangocli')
session_dir = os.path.join(project_root, ".mangocli", "session")

# ANSI colors
RESET, BOLD, DIM = "\033[0m", "\033[1m", "\033[2m"
BLUE, CYAN, GREEN, YELLOW, RED, GREY, ORANGE = (
    "\033[34m", "\033[36m", "\033[32m", "\033[33m", "\033[31m", "\033[90m", "\033[38;2;245;78;0m")


def _c(text, color): return f"{color}{text}{RESET}"


# --- UI ---
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
        # text = f"{frame} {self._spinner_message}"
        text = f"{_c(frame, ORANGE)} {_c(self._spinner_message, ORANGE)}"
        sys.stdout.write("\r" + text)
        sys.stdout.flush()

    def section(self, title):
        self._write_line()
        self._write_line(_c(f"• {title}", ORANGE))

    def kv(self, data: Dict[str, Any]):
        for k, v in data.items():
            self._write_line(f"{_c(str(k), GREY)}{_c(': ', GREY)}{_c(str(v), GREY)}")

    def tool_call(self, name: str, desc: str):
        self.section("Tool Call")
        self._write_line(f"{_c('› ', GREY)}{_c(name, CYAN)}  {_c(desc, GREY)}")

    def tool_result(self, ok=True, meta="applied"):
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
            return f"{n / 1000:.1f}k" if n >= 1000 else str(n)

        ratio = context_tokens / max_context if max_context else 0
        percent = int(ratio * 100)
        color = GREEN if percent < 50 else YELLOW if percent < 70 else RED

        self._write_line()
        self._write_line(
            _c(f"round: {iteration} | tokens: {fmt(input_tokens)} in / {fmt(output_tokens)} out |  ctx: ", GREY) +
            _c(f"{percent}%", color))

    def compact_status(self, before_tokens: int, after_tokens: int, max_context: int, strategy: str = "auto"):
        saved = before_tokens - after_tokens
        ratio = (after_tokens / max_context) if max_context else 0
        percent = int(ratio * 100)
        color = GREEN if percent < 50 else YELLOW if percent < 70 else RED

        self.section("Compact")
        self._write_line(f"  {_c('strategy', GREY)} {_c(strategy, ORANGE)}")
        self._write_line(
            f"  {_c('tokens', GREY)} "
            f"{_c(f'{before_tokens:,}', RED)}"
            f" {_c('→', GREY)} "
            f"{_c(f'{after_tokens:,}', GREEN)} "
            f"{_c(f'(-{saved:,})', ORANGE)}"
        )
        self._write_line(f"  {_c('context', GREY)} {_c(f'{percent}%', color)}")

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
        self.section("Code Diff")
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

        self._spinner_thread = threading.Thread(target=run, daemon=True)
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


# --- i18n ---


# --- Init dir, Base data ---
def initialize_system():
    if not os.path.exists(base_persist_dir):
        os.mkdir(base_persist_dir)
    if not os.path.exists(session_dir):
        os.mkdir(session_dir)


def helper():
    console.text("Mango CLI — 基于大模型的命令行编程助手")
    console.text("内置命令:")
    console.text("  /q, /quit      退出程序")
    console.text("  /c, /compact   手动压缩当前会话（释放上下文空间）")
    console.text("  /n, /new       结束当前会话并创建一个全新的会话")
    console.text("  /h, /help      显示本帮助信息")


# --- Utils function ---
def _check_command_safety(command: str):
    # 1.文件删除命令， 2.系统格式化和分区操作，3.危险权限修改， 4.提权命令，5.危险进程操作，6.环境变量和系统配置，7.历史和日志清理
    dangerous_patterns = [
        (r'\brm\s+.*-[rf]', 1), (r'\brm\s+-[rf]', 1), (r'\bunlink\b', 1),
        (r'\bmkfs\b', 2), (r'\bfdisk\b', 2), (r'\bparted\b', 2), (r'\bdd\s+.*if=.*of=', 2),
        (r'\bchmod\s+(?:-[a-zA-Z]+\s+)*\d*7\d*7\b', 3), (r'\bchmod\s+777\b', 3), (r'\bchmod\s+\d*7\d*7\b', 3),
        (r'\bchown\s+.*root\b', 3),
        (r'\bsudo\s+.*rm\b', 4), (r'\bsu\s+-\b', 4), (r'\bsu\s+root\b', 4),
        (r'\bkill\s+-9\s+1\b', 5), (r'\bkillall\s+-9\b', 5), (r'\bpkill\s+-9\b', 5), (r'\bkill\s+-9\s+-\d+\b', 5),
        (r'\bexport\s+PATH=', 6), (r'\bunset\s+PATH\b', 6), (r'>>?\s*/etc/', 6), (r'\becho\s+.*>\s*/etc/', 6),
        (r'\bhistory\s+-c\b', 7), (r'>\s*/dev/null\s+2>&1', 7),
    ]
    command = command.strip()
    if not command:
        return False, None
    for pattern, reason in dangerous_patterns:
        if re.search(pattern, command, re.IGNORECASE):
            return True, f"危险命令: {reason}"
    return False, None


def _validate_file_path(path: str) -> Optional[str]:
    """ 验证给定路径是否在项目根目录内, 返回 None 表示合法，否则返回错误描述字符串。"""
    abs_path = os.path.abspath(path)
    real_path = os.path.realpath(abs_path)
    real_root = os.path.realpath(project_root)
    if not real_path.startswith(real_root + os.sep) and real_path != real_root:    # 必须位于项目根目录下
        return f"path '{path}' is outside project root"
    if os.path.isdir(real_path):    # 不允许直接操作目录（write/edit 只能操作文件）
        return f"path '{path}' is a directory, not a file"
    return None


# --- Tool definitions: (description, schema, function) ---
def read(args):
    lines = open(args["path"]).readlines()
    offset = args.get("offset", 0)
    limit = args.get("limit", len(lines))
    selected = lines[offset: offset + limit]
    return "".join(f"{offset + idx + 1:4}| {line}" for idx, line in enumerate(selected))


def write(args):
    error = _validate_file_path(args["path"])
    if error:
        return f"write error: {error}"
    with open(args["path"], "w") as f:
        f.write(args["content"])
    return f"write {len(args['content'])}byte to {len(args['path'])} ok"


def edit(args):
    error = _validate_file_path(args["path"])
    if error:
        return f"edit error: {error}"
    text = open(args["path"]).read()
    old, new = args["old"], args["new"]
    if old not in text:
        return "edit error: old_string not found"
    count = text.count(old)
    if not args.get("all") and count > 1:
        return f"error: old_string appears {count} times, must be unique (use all=true)"
    replacement = (text.replace(old, new) if args.get("all") else text.replace(old, new, 1))
    with open(args["path"], "w") as f:
        f.write(replacement)
    return f"edit {len(args['path'])} ok"


def search(args):
    pattern = (args.get("path", ".") + "/" + args["pat"]).replace("//", "/")
    files = globlib.glob(pattern, recursive=True)
    files = sorted(files, key=lambda f: os.path.getmtime(f) if os.path.isfile(f) else 0, reverse=True,)
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
    proc = subprocess.Popen(args["cmd"], shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    output_lines = []
    try:
        while True:
            line = proc.stdout.readline()
            if not line and proc.poll() is not None:
                break
            if line:
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
                "type": "integer" if base_type == "number" else base_type, "description": param_info['description']
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

    def __len__(self): return len(self.messages)

    def disabled_compact(self): self.auto_compact_disabled = True

    def enabled_compact(self): self.auto_compact_disabled = False

    def set_max_failures(self, n: int = 3): self.max_failures = n

    def clear(self): self.messages = []

    def append_system(self, content: str): self.messages.append({"role": "system", "content": content})

    def append_user(self, content: str):
        self.messages.append({"role": "user", "content": content, "ts": int(time.time())})

    def append_assistant(self, content: dict):
        content.update({"ts": int(time.time())})
        self.messages.append(content)

    def append_tool(self, tool_call_id: str, content: str):
        self.messages.append({"role": "tool", "tool_call_id": tool_call_id, "content": content, "ts": int(time.time())})

    def load(self, persist_file: str):
        if os.path.exists(persist_file):
            try:
                with open(persist_file, "r", encoding="utf-8") as f:
                    self.messages = json.load(f)
            except (json.JSONDecodeError, IOError) as e:
                self.backup(persist_file)    # 备份损坏会话文件
                self.messages = []    # 清空消息列表，使后续流程以全新会话开始
                console.error(f"session.json file is corrupted ({e}). "
                              f"The corrupted file has been backed up and a new session.json has been generated.")

    def save(self, persist_file: str):
        with open(persist_file, "w", encoding="utf-8") as fp:
            fp.write(json.dumps(self.messages, indent=2, ensure_ascii=False))

    @staticmethod
    def backup(persist_file: str):
        backup_path = persist_file + f".{str(int(time.time()))}.backup"    # 备份会话文件
        if os.path.exists(persist_file):
            try:
                os.rename(persist_file, backup_path)
                console.warning(f"Session file backed up to {backup_path}")
            except Exception as e:
                console.warning(f"Failed to backup corrupted session file: {e}")

    def get_messages(self) -> List[Dict[str, Any]]: return self.messages

    def get_latest(self, n: int = 10) -> List[Dict]: return self.messages[-n:]

    @staticmethod
    def estimated_tokens(msg: Dict[str, Any]) -> int:  # token 估算 (粗略)
        content_len = len(msg.get("content", ""))
        return content_len // 4 + 4

    def total_tokens(self) -> int: return sum(self.estimated_tokens(m) for m in self.messages)

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
        except Exception as e:
            self.continuous_failures += 1

        try:    # 回退传统压缩
            self.compact_conversation()
            self.continuous_failures = 0
        except Exception as e:
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
        recent_msgs = non_system[-retain_count:]
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
        before = self.total_tokens()
        self.auto_compact_if_needed()
        after = self.total_tokens()
        if before > after:
            console.compact_status(
                before_tokens=before, after_tokens=after, max_context=MANGO_MAX_CONTEXT, strategy="auto")
        return self.get_messages()


def chat_completion(messages: List[Dict[str, str]], timeout: int = 60, max_retries: int = 3):
    extra_body = {"thinking": {"type": "enabled"}}
    body = {
        "model": MANGO_MODEL, "messages": messages, "stream": False, "extra_body": extra_body, "tools": tool_schema()
    }
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {MANGO_KEY}"}
    last_exception = None
    request = urllib.request.Request(MANGO_API_URL, data=json.dumps(body).encode(), headers=headers, method="POST", )
    for attempt in range(max_retries + 1):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw_data = response.read().decode("utf-8")
                return json.loads(raw_data)
        except urllib.error.HTTPError as e:
            if e.code >= 500 or e.code == 429:
                last_exception = e
            else:
                raise
        except (urllib.error.URLError, json.JSONDecodeError) as e:
            last_exception = e
        except Exception as e:
            raise e

        if attempt < max_retries:
            delay = 1 * (2 ** attempt)
            console.warning(
                f"Request failed (attempt {attempt + 1}/{max_retries + 1}), retrying in {delay:.1f}s: {last_exception}"
            )
            time.sleep(delay)
        else:
            break  # 所有重试均已耗尽，跳出循环并抛出最后一个异常
    raise last_exception


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
    content = message.get("content", "") or ""  # 提取文本内容
    reasoning_content = message.get("reasoning_content", "") or ""  # 提取推理内容
    raw_tool_calls = message.get("tool_calls", [])  # 处理工具调用
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
        arg_preview = str(list(tool_args.values())[0])[:50]
        console.tool_call(tool_name, arg_preview)

        if tool_name == "edit":
            console.diff(old=tool_args["old"], new=tool_args["new"])
            if console.prompt_apply(f"Apply changes to {tool_args['path']}?"):
                result = TOOLS[tool_name][2](tool_args)
            else:
                result = "error: User denied edit"
        elif tool_name == "bash":
            is_dangerous, reason = _check_command_safety(tool_args["cmd"])
            if is_dangerous and not console.prompt_apply(f"Execute dangerous cmd ({reason})? {tool_args['cmd']}"):
                result = "error: User denied dangerous command"
            else:
                console.start_spinner()
                result = TOOLS[tool_name][2](tool_args)
                console.end_spinner()
        else:
            console.start_spinner()
            result = TOOLS[tool_name][2](tool_args)
            console.end_spinner()

        if not result:
            print(f"  {DIM}⎿  (no output){RESET}")  # 空结果直接提示
        else:
            result_lines = result.split("\n")
            max_preview_lines = 20  # 最多展示前3行
            max_line_width = 100  # 单行最大宽度，超出截断
            lines_to_show = result_lines[:max_preview_lines]
            preview_lines = []
            for line in lines_to_show:
                if len(line) > max_line_width:
                    line = line[:max_line_width - 3] + "..."
                preview_lines.append(line)
            if len(result_lines) > max_preview_lines:
                more = len(result_lines) - max_preview_lines
                preview_lines.append(f"... and {more} more line{'s' if more > 1 else ''}")
            prefix = f"  {DIM}⎿  "
            for i, line in enumerate(preview_lines):
                if i == 0:
                    print(f"{prefix}{line}{RESET}")
                else:
                    print(f"     {DIM}{line}{RESET}")  # 后续行与第一行内容对齐（5个空格 + 颜色）

        console.tool_result(True)
        return result
    except Exception as err:
        return f"error: {err}"


class SystemPrompt:
    """ 分层装配的提示词运行时. 可根据会话状态、记忆、环境变量等动态生成完整的 system prompt."""
    def __init__(self):
        self.sections = []    # 有序的 section 列表，每个元素为 (section_name, content)
        self._init_default_sections()    # 默认加载基础 sections

    def _init_default_sections(self):
        self.sections.append(("base_intro", self._build_base_intro()))
        self.sections.append(("tool_guidance", self._build_tool_guidance()))
        self.sections.append(("safety", self._build_safety()))
        self.sections.append(("language", self._build_language()))
        self.sections.append(("memory", self._build_memory()))
        self.sections.append(("environment", self._build_environment()))

    @staticmethod
    def _build_base_intro() -> list[str]:  # 基础身份和核心约束
        return [
            "You are an interactive agent that helps users with software engineering tasks. Use the instructions "
            "below and the tools available to you to assist the user.",
            "",
            "IMPORTANT: You must NEVER generate or guess URLs for the user unless you are confident that the URLs are "
            "for helping the user with programming. For file paths, always prefer absolute paths when possible. If "
            "you need to read a directory, use the bash tool (ls) because the read tool cannot read directories.",
        ]

    @staticmethod
    def _build_tool_guidance() -> list[str]:  # 工具使用指导
        return [
            "## Tool Selection Guidelines",
            "You have access to the following dedicated tools: read/write/edit/search/grep/bash/attempt_completion.",
            "",
            "- For reading files: use **read**.",
            "- For writing or overwriting files: use **write**.",

            "- For replacing exact strings within a file: use **edit**. Prefer edit when you only need to change a "
            "small portion of a file.",

            "- For searching file names/paths: use **search** with a glob pattern.",
            "- For searching file content with regex: use **grep**.",

            "- Only use **bash** when no dedicated tool can accomplish the task, or for system commands (e.g., "
            "installing packages, running tests, managing directories).",

            "- Always use **attempt_completion** to present the final result to the user.",
            "- When using edit, ensure the `old` string is unique or set `all` to true.",
        ]

    @staticmethod
    def _build_environment() -> list[str]:  # 动态环境信息注入
        os_info = f"{platform.system()} {platform.release()} ({platform.machine()})"
        python_ver = sys.version.split()[0]

        return [
            "## Environment",
            "",
            f"- Current date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            f"- Working directory: {project_root}",
            f"- Operating system: {os_info}",
            f"- Python version: {python_ver}",
            f"- Shell: {os.environ.get('SHELL', 'unknown')}",
        ]

    @staticmethod
    def _build_language() -> list[str]:
        """语言偏好(可通过环境变量 MANGO_LANG 配置), 若未设置则默认使用 English. """
        lang = os.environ.get("MANGO_LANG", "zh")
        if lang.lower() == "chinese" or lang.lower() == "zh":
            return ["## Language", f"You should communicate with the user in Chinese (Simplified)."]
        return ["## Language", f"You should communicate with the user in {lang}."]

    @staticmethod
    def _build_memory() -> list[str]:
        """ 记忆加载, .mangocli/MEMORY.md 存在，则将其内容作为记忆注入. """
        memory_path = os.path.join(project_root, ".mangocli", "MANGO.md")
        if not os.path.exists(memory_path):
            return ["## Memory", "No persistent memory available."]
        if os.path.getsize(memory_path) == 0:
            return ["## Memory", "No persistent memory available."]
        content = open(memory_path, "r", encoding="utf-8").readlines()
        return [f"## Persisted Memory", ""] + content

    @staticmethod
    def _build_safety() -> list[str]:
        """ 安全边界提示. 要求模型在执行前对危险命令进行确认，并遵守工具的安全检查。"""
        return [
            "## Safety",
            "",
            "- Before executing any command that modifies the file system, deletes files, changes permissions, "
            "or performs system administration, you MUST ensure the command is safe and the user has confirmed if "
            "necessary.",
            "- Do not attempt to access files outside the project root unless explicitly required and confirmed by "
            "the user.",
        ]

    def assemble(self) -> str:  # 将所有 section 按顺序拼接成完整的 system prompt。
        _basic = []
        for _, content in self.sections:
            _basic.append("\n".join(content))
        return "\n\n".join(_basic)


def main():
    initialize_system()

    print(f"{BOLD}Mango Cli v{__version__}{RESET} | {DIM}{MANGO_MODEL} | {project_root}{RESET}\n")

    ctx_file_path = os.path.join(session_dir, "session.json")
    ctx = ContextManager()
    ctx.enabled_compact()
    ctx.set_max_failures()
    ctx.load(ctx_file_path)

    prompt_runtime = SystemPrompt()
    system_prompt = prompt_runtime.assemble()
    if len(ctx) == 0:  # 刚初始化的ctx才需要system prompt
        ctx.append_system(system_prompt)

    while True:
        try:
            console.separator()
            user_input = input(f"{BOLD}{BLUE}❯{RESET} ").strip()
            if not user_input:
                continue
            if user_input.startswith('/'):
                if user_input.strip() in ("/q", "/quit"):  # 退出
                    break
                if user_input.strip() in ("/c", "/compact"):  # 手动触发 full compact
                    continue
                if user_input.strip() in ("/n", "/new"):  # 创建新的session
                    ctx.backup(ctx_file_path)
                    ctx.clear()
                    ctx.append_system(system_prompt)
                    console.success("New session created.")
                    continue
                if user_input.strip() in ("/h", "/help"):
                    helper()
                    continue

            ctx.append_user(user_input)

            # agentic loop: keep calling API until no more tool calls
            iteration = 0
            while True:
                console.start_spinner("Request...")
                response = parse_chat_completion(chat_completion(ctx.prepare_for_api()))
                console.end_spinner()
                ctx.append_assistant(response["raw_message"])

                iteration += 1
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

                if response["finish_reason"] == "stop":
                    break  # 模型明确表示结束，退出循环
                if response["has_tool_calls"]:
                    tool_calls = response["tool_calls"]
                    for tool in tool_calls:
                        tool_name, tool_args = tool["name"], tool["arguments"]
                        result = run_tool(tool_name, tool_args)
                        ctx.append_tool(tool["id"], result)
                    if any(tc["name"] == "attempt_completion" for tc in tool_calls):
                        break
                else:
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
