from dataclasses import dataclass
from decimal import Decimal, localcontext
from operator import add, eq, ge, gt, le, lt, mul, sub

from .documents import DomainError
from .models import Amount, Identifier

COMPARISONS = {"eq": eq, "gt": gt, "ge": ge, "lt": lt, "le": le}
ARITHMETIC = {"add": add, "sub": sub, "mul": mul}
ARITY = {**dict.fromkeys((*COMPARISONS, *ARITHMETIC, "div", "and", "or"), 2), "not": 1}


@dataclass(frozen=True)
class Quantity:
    value: Decimal
    dimension: str
    currency: str | None
    basis: tuple | None = None

    def as_dict(self) -> dict:
        return {
            "value": format(self.value, "f"),
            "dimension": self.dimension,
            "currency": self.currency,
        }


def fact_quantity(item: dict) -> Quantity:
    value, dimension, currency = Amount.model_validate(
        {key: item[key] for key in ("value", "unit", "currency")}
    ).normalized()
    if item["scope"] == "UNKNOWN":
        raise DomainError("INCOMPATIBLE_FACTS", "事实缺少合并范围")
    basis = tuple(item[key] for key in ("subject", "period_start", "period_end", "scope"))
    return Quantity(value, dimension, currency, basis)


def validate_expression(expression: dict) -> None:
    from pydantic import TypeAdapter

    stack = [(expression, 1)]
    identifiers = TypeAdapter(Identifier)
    count = 0
    while stack:
        node, depth = stack.pop()
        count += 1
        if depth > 8 or count > 48 or not isinstance(node, dict):
            raise ValueError("表达式超出深度、节点数量或结构限制")
        keys = set(node)
        if keys == {"literal"}:
            Amount.model_validate(node["literal"])
        elif keys == {"fact_id"}:
            identifiers.validate_python(node["fact_id"])
        elif keys == {"op", "args"}:
            op, args = node["op"], node["args"]
            if (
                not isinstance(op, str)
                or op not in ARITY
                or not isinstance(args, list)
                or len(args) != ARITY[op]
            ):
                raise ValueError("运算或参数数量不受支持")
            stack.extend((argument, depth + 1) for argument in args)
        else:
            raise ValueError("仅允许事实引用、字面量及预定义运算")


def evaluate(
    expression: dict, facts: dict[str, dict]
) -> tuple[Quantity | bool, list[dict], set[str]]:
    validate_expression(expression)
    steps = []
    used = set()

    def visit(node):
        if "literal" in node:
            return Quantity(*Amount.model_validate(node["literal"]).normalized())
        if "fact_id" in node:
            fact_id = node["fact_id"]
            if fact_id not in facts:
                raise DomainError("FACT_UNAVAILABLE", f"事实 {fact_id} 不在适用证据范围内")
            used.add(fact_id)
            return fact_quantity(facts[fact_id])
        op = node["op"]
        args = [visit(argument) for argument in node["args"]]
        if op in {"and", "or", "not"}:
            if not all(isinstance(argument, bool) for argument in args):
                raise DomainError("EXPRESSION_TYPE", "布尔运算需要布尔参数")
            value = {"and": all, "or": any, "not": lambda values: not values[0]}[op](args)
        else:
            left, right = args
            if not isinstance(left, Quantity) or not isinstance(right, Quantity):
                raise DomainError("EXPRESSION_TYPE", "数值运算需要数值参数")
            if left.basis and right.basis and left.basis != right.basis:
                raise DomainError("INCOMPATIBLE_FACTS", "主体、报告期或合并范围不同")
            same_unit = (left.dimension, left.currency) == (right.dimension, right.currency)
            basis = left.basis or right.basis
            if op in COMPARISONS or op in {"add", "sub"}:
                if not same_unit:
                    raise DomainError("INCOMPATIBLE_FACTS", "不能直接比较不同维度或币种")
                if op in COMPARISONS:
                    value = COMPARISONS[op](left.value, right.value)
                else:
                    value = Quantity(
                        ARITHMETIC[op](left.value, right.value),
                        left.dimension,
                        left.currency,
                        basis,
                    )
            elif op == "mul":
                if "ratio" not in {left.dimension, right.dimension}:
                    raise DomainError("INCOMPATIBLE_FACTS", "乘法需要至少一个无量纲因子")
                typed = right if left.dimension == "ratio" else left
                value = Quantity(
                    mul(left.value, right.value), typed.dimension, typed.currency, basis
                )
            else:
                if not right.value:
                    raise DomainError("DIVISION_BY_ZERO", "除数为零")
                if right.dimension == "ratio":
                    value = Quantity(left.value / right.value, left.dimension, left.currency, basis)
                elif same_unit:
                    value = Quantity(left.value / right.value, "ratio", None, basis)
                else:
                    raise DomainError("INCOMPATIBLE_FACTS", "除法需要相同口径或无量纲除数")
        steps.append(
            {
                "op": op,
                "inputs": [
                    argument.as_dict() if isinstance(argument, Quantity) else argument
                    for argument in args
                ],
                "result": value.as_dict() if isinstance(value, Quantity) else value,
            }
        )
        return value

    with localcontext() as context:
        context.prec = 64
        result = visit(expression)
    return result, steps, used
