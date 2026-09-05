from __future__ import annotations

import ast
import json
import operator
from dataclasses import dataclass
from typing import Any, Callable

from .memory_store import redact_secrets


class CodeModeError(ValueError):
    """Raised when restricted Code Mode source violates the interpreter contract."""


@dataclass(frozen=True, slots=True)
class CodeModeLimits:
    max_source_chars: int = 24_000
    max_statements: int = 240
    max_loop_iterations: int = 200
    max_nested_tool_calls: int = 20
    max_value_chars: int = 120_000
    max_output_chars: int = 20_000

    def __post_init__(self) -> None:
        for name in (
            "max_source_chars",
            "max_statements",
            "max_loop_iterations",
            "max_nested_tool_calls",
            "max_value_chars",
            "max_output_chars",
        ):
            value = int(getattr(self, name))
            if value < 1:
                raise ValueError(f"{name} must be positive")
            object.__setattr__(self, name, value)


@dataclass(frozen=True, slots=True)
class CodeModeExecution:
    output: str
    statements: int
    loop_iterations: int
    nested_tool_calls: int
    tool_summaries: tuple[dict[str, Any], ...]


NestedToolInvoker = Callable[[str, dict[str, Any]], dict[str, Any]]
CancelCheck = Callable[[], bool]


class _BreakSignal(Exception):
    pass


class _ContinueSignal(Exception):
    pass


class CodeModeInterpreter:
    """Small Python-like interpreter for composing Loom tools.

    This intentionally does not use ``eval`` or ``exec``. Source is parsed with
    ``ast`` and only an allowlisted JSON-oriented subset is interpreted. Host OS,
    imports, arbitrary attributes, file APIs and Python object methods are never
    exposed. Side effects can only happen through ``tools.<name>(...)`` or
    ``tool(name, ...)``, whose callback remains inside Loom's tool boundary.
    """

    def __init__(self, *, limits: CodeModeLimits | None = None) -> None:
        self.limits = limits or CodeModeLimits()
        self._env: dict[str, Any] = {}
        self._outputs: list[str] = []
        self._output_chars = 0
        self._statements = 0
        self._loop_iterations = 0
        self._nested_tool_calls = 0
        self._tool_summaries: list[dict[str, Any]] = []
        self._invoke_tool: NestedToolInvoker | None = None
        self._is_cancelled: CancelCheck = lambda: False

    def execute(
        self,
        source: str,
        *,
        invoke_tool: NestedToolInvoker,
        is_cancelled: CancelCheck | None = None,
    ) -> CodeModeExecution:
        code = str(source or "")
        if not code.strip():
            raise CodeModeError("code_mode source must not be empty")
        if len(code) > self.limits.max_source_chars:
            raise CodeModeError("code_mode source exceeds the configured size limit")
        try:
            tree = ast.parse(code, mode="exec")
        except SyntaxError as exc:
            raise CodeModeError(f"invalid code_mode syntax: {exc.msg}") from exc

        self._env = {}
        self._outputs = []
        self._output_chars = 0
        self._statements = 0
        self._loop_iterations = 0
        self._nested_tool_calls = 0
        self._tool_summaries = []
        self._invoke_tool = invoke_tool
        self._is_cancelled = is_cancelled or (lambda: False)

        self._exec_block(tree.body)
        return CodeModeExecution(
            output="\n".join(self._outputs),
            statements=self._statements,
            loop_iterations=self._loop_iterations,
            nested_tool_calls=self._nested_tool_calls,
            tool_summaries=tuple(self._tool_summaries),
        )

    def _check_cancelled(self) -> None:
        if self._is_cancelled():
            raise RuntimeError("code_mode execution cancelled")

    def _tick_statement(self) -> None:
        self._check_cancelled()
        self._statements += 1
        if self._statements > self.limits.max_statements:
            raise CodeModeError("code_mode statement limit reached")

    def _exec_block(self, statements: list[ast.stmt]) -> None:
        for statement in statements:
            self._exec_stmt(statement)

    def _exec_stmt(self, statement: ast.stmt) -> None:
        self._tick_statement()
        if isinstance(statement, ast.Expr):
            self._eval(statement.value)
            return
        if isinstance(statement, ast.Assign):
            if len(statement.targets) != 1 or not isinstance(statement.targets[0], ast.Name):
                raise CodeModeError("code_mode assignments require one simple variable name")
            name = self._variable_name(statement.targets[0].id)
            self._env[name] = self._bounded(self._eval(statement.value))
            return
        if isinstance(statement, ast.AugAssign):
            if not isinstance(statement.target, ast.Name):
                raise CodeModeError("code_mode augmented assignments require a simple variable name")
            name = self._variable_name(statement.target.id)
            if name not in self._env:
                raise CodeModeError(f"unknown code_mode variable: {name}")
            self._env[name] = self._bounded(
                self._binary(statement.op, self._env[name], self._eval(statement.value))
            )
            return
        if isinstance(statement, ast.If):
            branch = statement.body if self._truthy(self._eval(statement.test)) else statement.orelse
            self._exec_block(branch)
            return
        if isinstance(statement, ast.For):
            if not isinstance(statement.target, ast.Name):
                raise CodeModeError("code_mode for loops require a simple loop variable")
            name = self._variable_name(statement.target.id)
            iterable = self._iterable(self._eval(statement.iter))
            broke = False
            for item in iterable:
                self._check_cancelled()
                self._loop_iterations += 1
                if self._loop_iterations > self.limits.max_loop_iterations:
                    raise CodeModeError("code_mode loop iteration limit reached")
                self._env[name] = self._bounded(item)
                try:
                    self._exec_block(statement.body)
                except _ContinueSignal:
                    continue
                except _BreakSignal:
                    broke = True
                    break
            if not broke:
                self._exec_block(statement.orelse)
            return
        if isinstance(statement, ast.Break):
            raise _BreakSignal()
        if isinstance(statement, ast.Continue):
            raise _ContinueSignal()
        if isinstance(statement, ast.Pass):
            return
        raise CodeModeError(
            f"unsupported code_mode statement: {type(statement).__name__}; "
            "imports, functions, classes, while/try/with and arbitrary Python execution are disabled"
        )

    def _eval(self, expression: ast.expr) -> Any:
        self._check_cancelled()
        if isinstance(expression, ast.Constant):
            if isinstance(expression.value, (str, int, float, bool)) or expression.value is None:
                return self._bounded(expression.value)
            raise CodeModeError("unsupported code_mode literal")
        if isinstance(expression, ast.Name):
            name = self._variable_name(expression.id)
            if name not in self._env:
                raise CodeModeError(f"unknown code_mode variable: {name}")
            return self._env[name]
        if isinstance(expression, ast.List):
            return self._bounded([self._eval(item) for item in expression.elts])
        if isinstance(expression, ast.Tuple):
            return self._bounded([self._eval(item) for item in expression.elts])
        if isinstance(expression, ast.Dict):
            result: dict[str, Any] = {}
            for key_node, value_node in zip(expression.keys, expression.values):
                if key_node is None:
                    raise CodeModeError("dictionary unpacking is disabled in code_mode")
                key = self._eval(key_node)
                if not isinstance(key, str):
                    raise CodeModeError("code_mode dictionary keys must be strings")
                result[key] = self._eval(value_node)
            return self._bounded(result)
        if isinstance(expression, ast.Subscript):
            value = self._eval(expression.value)
            if isinstance(expression.slice, ast.Slice):
                if not isinstance(value, (list, str)):
                    raise CodeModeError("code_mode slices require a list or string")
                lower = self._eval(expression.slice.lower) if expression.slice.lower else None
                upper = self._eval(expression.slice.upper) if expression.slice.upper else None
                step = self._eval(expression.slice.step) if expression.slice.step else None
                for item in (lower, upper, step):
                    if item is not None and not isinstance(item, int):
                        raise CodeModeError("code_mode slice indices must be integers")
                return self._bounded(value[slice(lower, upper, step)])
            key = self._eval(expression.slice)
            try:
                if isinstance(value, dict):
                    if not isinstance(key, str):
                        raise CodeModeError("mapping subscripts require string keys")
                    return self._bounded(value[key])
                if isinstance(value, (list, str)):
                    if not isinstance(key, int) or isinstance(key, bool):
                        raise CodeModeError("sequence subscripts require integer indices")
                    return self._bounded(value[key])
            except (KeyError, IndexError) as exc:
                raise CodeModeError(f"code_mode subscript failed: {exc}") from exc
            raise CodeModeError("code_mode subscripting only supports JSON mappings, lists and strings")
        if isinstance(expression, ast.UnaryOp):
            value = self._eval(expression.operand)
            if isinstance(expression.op, ast.Not):
                return not self._truthy(value)
            if isinstance(expression.op, ast.USub) and self._is_number(value):
                return self._bounded(-value)
            if isinstance(expression.op, ast.UAdd) and self._is_number(value):
                return self._bounded(+value)
            raise CodeModeError("unsupported code_mode unary operation")
        if isinstance(expression, ast.BinOp):
            return self._bounded(self._binary(expression.op, self._eval(expression.left), self._eval(expression.right)))
        if isinstance(expression, ast.BoolOp):
            if isinstance(expression.op, ast.And):
                result: Any = True
                for item in expression.values:
                    result = self._eval(item)
                    if not self._truthy(result):
                        return result
                return result
            if isinstance(expression.op, ast.Or):
                result = False
                for item in expression.values:
                    result = self._eval(item)
                    if self._truthy(result):
                        return result
                return result
            raise CodeModeError("unsupported code_mode boolean operation")
        if isinstance(expression, ast.Compare):
            left = self._eval(expression.left)
            for op_node, comparator in zip(expression.ops, expression.comparators):
                right = self._eval(comparator)
                if not self._compare(op_node, left, right):
                    return False
                left = right
            return True
        if isinstance(expression, ast.IfExp):
            return self._eval(expression.body if self._truthy(self._eval(expression.test)) else expression.orelse)
        if isinstance(expression, ast.Call):
            return self._call(expression)
        if isinstance(expression, ast.Attribute):
            raise CodeModeError("arbitrary attribute access is disabled in code_mode")
        raise CodeModeError(f"unsupported code_mode expression: {type(expression).__name__}")

    def _call(self, call: ast.Call) -> Any:
        if any(keyword.arg is None for keyword in call.keywords):
            raise CodeModeError("keyword unpacking is disabled in code_mode")
        if isinstance(call.func, ast.Name):
            name = call.func.id
            if name == "tool":
                return self._call_tool_function(call)
            if name in {"emit", "print"}:
                if len(call.args) != 1 or call.keywords:
                    raise CodeModeError("emit(value) accepts exactly one positional argument")
                value = self._eval(call.args[0])
                self._emit(value)
                return value
            return self._safe_builtin(name, call)
        if isinstance(call.func, ast.Attribute):
            tool_name = self._tool_attribute_name(call.func)
            if call.args:
                raise CodeModeError("tools.<name>(...) accepts keyword arguments only")
            arguments = {keyword.arg: self._eval(keyword.value) for keyword in call.keywords if keyword.arg}
            return self._invoke_nested(tool_name, arguments)
        raise CodeModeError("code_mode may only call safe builtins and Loom tools")

    def _call_tool_function(self, call: ast.Call) -> Any:
        if not call.args:
            raise CodeModeError("tool(name, ...) requires a tool name")
        tool_name = self._eval(call.args[0])
        if not isinstance(tool_name, str) or not tool_name.strip():
            raise CodeModeError("tool(name, ...) requires a non-empty string name")
        arguments: dict[str, Any] = {}
        if len(call.args) > 2:
            raise CodeModeError("tool(name, mapping?, **kwargs) accepts at most two positional arguments")
        if len(call.args) == 2:
            mapping = self._eval(call.args[1])
            if not isinstance(mapping, dict):
                raise CodeModeError("the second tool() argument must be a mapping")
            arguments.update(mapping)
        for keyword in call.keywords:
            if keyword.arg is None:
                raise CodeModeError("tool keyword unpacking is disabled")
            arguments[keyword.arg] = self._eval(keyword.value)
        return self._invoke_nested(tool_name.strip(), arguments)

    def _invoke_nested(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        self._check_cancelled()
        self._nested_tool_calls += 1
        if self._nested_tool_calls > self.limits.max_nested_tool_calls:
            raise CodeModeError("code_mode nested tool call limit reached")
        if self._invoke_tool is None:
            raise RuntimeError("code_mode nested tool invoker is unavailable")
        result = self._invoke_tool(tool_name, self._bounded(arguments))
        if not isinstance(result, dict):
            raise TypeError("code_mode nested tool invoker must return a JSON object")
        safe = self._bounded(self._redact_value(result))
        self._tool_summaries.append(
            {
                "tool": tool_name,
                "ok": bool(safe.get("ok")),
                "requires_approval": bool(safe.get("requires_approval")),
            }
        )
        return safe

    def _safe_builtin(self, name: str, call: ast.Call) -> Any:
        args = [self._eval(item) for item in call.args]
        kwargs = {keyword.arg: self._eval(keyword.value) for keyword in call.keywords if keyword.arg}
        if name == "len" and len(args) == 1 and not kwargs:
            return len(args[0])
        if name == "str" and len(args) == 1 and not kwargs:
            return self._bounded(str(args[0]))
        if name == "int" and len(args) == 1 and not kwargs:
            return int(args[0])
        if name == "float" and len(args) == 1 and not kwargs:
            return float(args[0])
        if name == "bool" and len(args) == 1 and not kwargs:
            return bool(args[0])
        if name == "list" and len(args) == 1 and not kwargs:
            return self._bounded(list(self._iterable(args[0])))
        if name == "range" and 1 <= len(args) <= 3 and not kwargs:
            if not all(isinstance(value, int) and not isinstance(value, bool) for value in args):
                raise CodeModeError("range() arguments must be integers")
            values = list(range(*args))
            if len(values) > self.limits.max_loop_iterations:
                raise CodeModeError("range() exceeds the code_mode loop limit")
            return values
        if name == "get" and 2 <= len(args) <= 3 and not kwargs:
            mapping, key = args[0], args[1]
            default = args[2] if len(args) == 3 else None
            if not isinstance(mapping, dict) or not isinstance(key, str):
                raise CodeModeError("get(mapping, key, default?) requires a mapping and string key")
            return self._bounded(mapping.get(key, default))
        if name in {"keys", "values", "items"} and len(args) == 1 and not kwargs:
            if not isinstance(args[0], dict):
                raise CodeModeError(f"{name}() requires a mapping")
            if name == "keys":
                return list(args[0].keys())
            if name == "values":
                return self._bounded(list(args[0].values()))
            return self._bounded([[key, value] for key, value in args[0].items()])
        if name == "sorted" and len(args) == 1 and not kwargs:
            value = list(self._iterable(args[0]))
            try:
                return self._bounded(sorted(value))
            except TypeError as exc:
                raise CodeModeError("sorted() values must be mutually comparable") from exc
        if name in {"min", "max", "sum"} and len(args) == 1 and not kwargs:
            values = list(self._iterable(args[0]))
            if name == "sum" and not all(self._is_number(value) for value in values):
                raise CodeModeError("sum() requires numeric values")
            try:
                return self._bounded({"min": min, "max": max, "sum": sum}[name](values))
            except (TypeError, ValueError) as exc:
                raise CodeModeError(f"{name}() failed: {exc}") from exc
        if name == "json" and len(args) == 1 and not kwargs:
            return self._bounded(json.dumps(args[0], ensure_ascii=False, separators=(",", ":")))
        raise CodeModeError(f"unsupported code_mode function: {name}")

    def _tool_attribute_name(self, attribute: ast.Attribute) -> str:
        parts: list[str] = []
        node: ast.expr = attribute
        while isinstance(node, ast.Attribute):
            if node.attr.startswith("_"):
                raise CodeModeError("private tool attributes are disabled")
            parts.append(node.attr)
            node = node.value
        if not isinstance(node, ast.Name) or node.id != "tools":
            raise CodeModeError("arbitrary method and attribute calls are disabled in code_mode")
        parts.reverse()
        if not parts:
            raise CodeModeError("tool name must not be empty")
        return ".".join(parts)

    def _emit(self, value: Any) -> None:
        safe = self._redact_value(value)
        if isinstance(safe, str):
            text = safe
        else:
            text = json.dumps(safe, ensure_ascii=False, separators=(",", ":"))
        remaining = self.limits.max_output_chars - self._output_chars
        if remaining <= 0:
            return
        if len(text) > remaining:
            text = text[: max(0, remaining - 13)] + "…[truncated]"
        self._outputs.append(text)
        self._output_chars += len(text)

    def _binary(self, op_node: ast.operator, left: Any, right: Any) -> Any:
        operations: tuple[tuple[type[ast.operator], Callable[[Any, Any], Any]], ...] = (
            (ast.Add, operator.add),
            (ast.Sub, operator.sub),
            (ast.Mult, operator.mul),
            (ast.Div, operator.truediv),
            (ast.FloorDiv, operator.floordiv),
            (ast.Mod, operator.mod),
        )
        for op_type, operation in operations:
            if isinstance(op_node, op_type):
                if op_type is ast.Add and (
                    (isinstance(left, str) and isinstance(right, str))
                    or (isinstance(left, list) and isinstance(right, list))
                ):
                    return operation(left, right)
                if self._is_number(left) and self._is_number(right):
                    return operation(left, right)
                raise CodeModeError(f"unsupported operands for {op_type.__name__}")
        raise CodeModeError(f"unsupported code_mode binary operator: {type(op_node).__name__}")

    def _compare(self, op_node: ast.cmpop, left: Any, right: Any) -> bool:
        if isinstance(op_node, ast.Eq):
            return left == right
        if isinstance(op_node, ast.NotEq):
            return left != right
        if isinstance(op_node, ast.In):
            return left in right
        if isinstance(op_node, ast.NotIn):
            return left not in right
        try:
            if isinstance(op_node, ast.Lt):
                return left < right
            if isinstance(op_node, ast.LtE):
                return left <= right
            if isinstance(op_node, ast.Gt):
                return left > right
            if isinstance(op_node, ast.GtE):
                return left >= right
        except TypeError as exc:
            raise CodeModeError("code_mode comparison operands are incompatible") from exc
        raise CodeModeError(f"unsupported code_mode comparison: {type(op_node).__name__}")

    def _iterable(self, value: Any) -> list[Any]:
        if isinstance(value, dict):
            return list(value.keys())
        if isinstance(value, (list, str)):
            return list(value)
        raise CodeModeError("code_mode loops require a list, string, mapping, or range() result")

    @staticmethod
    def _truthy(value: Any) -> bool:
        return bool(value)

    @staticmethod
    def _is_number(value: Any) -> bool:
        return isinstance(value, (int, float)) and not isinstance(value, bool)

    @staticmethod
    def _variable_name(name: str) -> str:
        value = str(name or "")
        if not value.isidentifier() or value.startswith("_"):
            raise CodeModeError("code_mode variable names must be public Python identifiers")
        if value in {"tools", "tool", "emit", "print"}:
            raise CodeModeError(f"reserved code_mode variable name: {value}")
        return value

    def _bounded(self, value: Any) -> Any:
        try:
            serialized = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        except (TypeError, ValueError) as exc:
            raise CodeModeError("code_mode values must remain JSON serializable") from exc
        if len(serialized) > self.limits.max_value_chars:
            raise CodeModeError("code_mode intermediate value exceeds the configured size limit")
        return value

    def _redact_value(self, value: Any) -> Any:
        if isinstance(value, str):
            return redact_secrets(value)
        if isinstance(value, list):
            return [self._redact_value(item) for item in value]
        if isinstance(value, tuple):
            return [self._redact_value(item) for item in value]
        if isinstance(value, dict):
            return {str(key): self._redact_value(item) for key, item in value.items()}
        return value


__all__ = [
    "CodeModeError",
    "CodeModeExecution",
    "CodeModeInterpreter",
    "CodeModeLimits",
    "NestedToolInvoker",
]
