"""模擬工具、工具 schema 驗證與執行。

這裡只用「工具 schema」與「工具本身的執行結果」來產生錯誤訊息，
完全不會讀取題目的標準答案（gold）。
"""
import ast
import operator
import re


class ToolBox:
    def __init__(self, schema, db):
        self.schema_version = schema["version"]
        self.db_version = db["version"]
        self.tools = {t["name"]: t for t in schema["tools"]}
        self.db = db

    # ---------- 給 prompt 看的 schema ----------
    def describe(self, names):
        return [
            {"name": n, "description": self.tools[n]["description"], "parameters": self.tools[n]["parameters"]}
            for n in names if n in self.tools
        ]

    # ---------- 驗證（只依 schema） ----------
    def validate(self, call, allowed):
        """回傳 None 代表通過；否則回傳 (錯誤類型, 錯誤訊息)。"""
        if not isinstance(call, dict) or "name" not in call:
            return ("parse_error", "工具呼叫必須是 JSON 物件，並包含 name 與 arguments 欄位")
        name = call["name"]
        if name not in allowed:
            kind = "hallucinated_tool" if name not in self.tools else "tool_not_allowed"
            return (kind, f"找不到工具「{name}」。目前可用的工具：{', '.join(allowed) or '（無）'}")
        args = call.get("arguments", {})
        if not isinstance(args, dict):
            return ("parse_error", "arguments 必須是 JSON 物件")
        params = self.tools[name]["parameters"]
        props = params.get("properties", {})
        for req in params.get("required", []):
            if req not in args:
                return ("missing_param", f"缺少必要參數：{req}")
        for k, v in args.items():
            if k not in props:
                return ("unknown_param", f"未知參數：{k}。可用參數：{', '.join(props)}")
            spec = props[k]
            err = _check_type(k, v, spec)
            if err:
                return err
        return None

    # ---------- 執行 ----------
    def execute(self, name, args):
        try:
            if name == "calculator":
                return {"ok": True, "result": {"value": _safe_eval(args["expression"])}}
            if name == "convert_currency":
                rates = self.db["rates_to_twd"]
                twd = args["amount"] * rates[args["from_currency"]]
                out = twd / rates[args["to_currency"]]
                return {"ok": True, "result": {"converted_amount": round(out, 2), "to_currency": args["to_currency"]}}
            if name == "get_order_status":
                oid = args["order_id"]
                if oid in self.db["failing_orders"]:
                    return {"ok": False, "error": self.db["failing_orders"][oid]}
                if oid not in self.db["orders"]:
                    return {"ok": False, "error": f"NOT_FOUND：查無訂單 {oid}"}
                return {"ok": True, "result": dict(self.db["orders"][oid], order_id=oid)}
            return {"ok": False, "error": f"工具 {name} 尚未實作"}
        except Exception as e:  # 工具執行期錯誤
            return {"ok": False, "error": f"{type(e).__name__}：{e}"}


def _check_type(key, value, spec):
    t = spec.get("type")
    if t == "string" and not isinstance(value, str):
        return ("wrong_type", f"參數 {key} 的型別應為 string，收到 {type(value).__name__}")
    if t == "number" and (isinstance(value, bool) or not isinstance(value, (int, float))):
        return ("wrong_type", f"參數 {key} 的型別應為 number，收到 {type(value).__name__}")
    if t == "integer" and (isinstance(value, bool) or not isinstance(value, int)):
        return ("wrong_type", f"參數 {key} 的型別應為 integer，收到 {type(value).__name__}")
    if "enum" in spec and value not in spec["enum"]:
        return ("invalid_value", f"參數 {key} 必須是以下其中之一：{spec['enum']}")
    if "pattern" in spec and isinstance(value, str) and not re.fullmatch(spec["pattern"], value):
        desc = spec.get("description", spec["pattern"])
        return ("invalid_value", f"參數 {key} 格式錯誤（{desc}），收到「{value}」")
    return None


_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
        ast.Div: operator.truediv, ast.USub: operator.neg, ast.UAdd: operator.pos}


def _safe_eval(expr):
    """只允許數字與 + - * / 括號，不會執行任意程式碼。"""
    def ev(node):
        if isinstance(node, ast.Expression):
            return ev(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return node.value
        if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
            return _OPS[type(node.op)](ev(node.left), ev(node.right))
        if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
            return _OPS[type(node.op)](ev(node.operand))
        raise ValueError("算式只能包含數字、+ - * / 與括號")
    val = ev(ast.parse(expr.replace(",", ""), mode="eval"))
    return round(val, 6)
