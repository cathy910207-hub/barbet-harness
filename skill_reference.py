"""每個 skill 的參考實作：verify.py 用來獨立重算規則題的答案。

為什麼需要：題目答案由 AI 起草，不能直接採信。這裡把每份 skill 的規則寫成程式，
verify.py 會（1）用程式重算答案、（2）檢查 skill 文字裡真的寫著同樣的數字。
skill 文字和這裡任何一邊改了而另一邊沒跟著改，驗證就會失敗。

新增 skill 時：在 SKILLS 加一筆，寫明 family（用哪個計算函式）、consts（規則常數）、
must_appear（skill 文字中必須出現的字串，用來核對兩邊一致）。
"""
from decimal import ROUND_FLOOR, ROUND_HALF_UP, Decimal


def _round(x):
    return int(Decimal(str(x)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _d(x):
    return Decimal(str(x))


# ---------------------------------------------------------------- 計算函式
def refund(c, price, opened):
    if not opened:
        return f"退款金額：{_round(price)} 元"
    if c.get("reject_opened"):
        return "退款金額：0 元"
    return f"退款金額：{_round(_d(price) * _d(c['multiplier']) - c['fee'])} 元"


def shipping(c, amount, island):
    base = 0 if amount >= c["free_threshold"] else c["base_fee"]
    return f"運費：{base + (c['island_fee'] if island else 0)} 元"


def discount(c, amount, tier):
    if tier in ("gold", "silver"):
        return f"應付金額：{_round(_d(amount) * _d(c[tier]))} 元"
    if c.get("normal_threshold") and amount >= c["normal_threshold"]:
        return f"應付金額：{amount - c['normal_off']} 元"
    return f"應付金額：{amount} 元"


def points(c, amount, birthday_month):
    p = int((_d(amount) / c["per"]).to_integral_value(rounding=ROUND_FLOOR))
    if birthday_month:
        p = p * 2 if c["birthday"] == "double" else p + c["birthday_bonus"]
    return f"獲得點數：{p} 點"


def compensation(c, delay_days):
    if c["mode"] == "per_day":
        v = min(delay_days * c["per_day"], c["cap"])
    else:
        v = c["flat"] if delay_days > c["min_days"] else 0
    return f"補償購物金：{v} 元"


def installment(c, amount, periods):
    rate = _d(c["rates"][str(periods)])
    return f"分期總金額：{_round(_d(amount) * (1 + rate))} 元"


FAMILIES = {"refund": refund, "shipping": shipping, "discount": discount, "points": points,
            "compensation": compensation, "installment": installment}

# ---------------------------------------------------------------- 各 skill 的規則常數
SKILLS = {
    "skill_refund_v1": {"family": "refund", "consts": {"multiplier": "0.9", "fee": 0},
                        "must_appear": ["全額退款", "× 0.9"]},
    "skill_refund_v2": {"family": "refund", "consts": {"multiplier": "0.8", "fee": 30},
                        "must_appear": ["全額退款", "× 0.8", "30 元"]},
    "skill_refund_v3": {"family": "refund", "consts": {"reject_opened": True},
                        "must_appear": ["全額退款", "不接受退款", "0 元"]},
    "skill_shipping_v1": {"family": "shipping", "consts": {"free_threshold": 1000, "base_fee": 80, "island_fee": 150},
                          "must_appear": ["1000 元（含）", "80 元", "150 元"]},
    "skill_shipping_v2": {"family": "shipping", "consts": {"free_threshold": 1500, "base_fee": 100, "island_fee": 200},
                          "must_appear": ["1500 元（含）", "100 元", "200 元"]},
    "skill_discount_v1": {"family": "discount", "consts": {"gold": "0.9", "silver": "0.95"},
                          "must_appear": ["× 0.9", "× 0.95", "不打折", "四捨五入"]},
    "skill_discount_v2": {"family": "discount",
                          "consts": {"gold": "0.85", "silver": "0.9", "normal_threshold": 2000, "normal_off": 100},
                          "must_appear": ["× 0.85", "× 0.9", "2000 元（含）", "100 元", "四捨五入"]},
    "skill_points_v1": {"family": "points", "consts": {"per": 100, "birthday": "double"},
                        "must_appear": ["100 元得 1 點", "無條件捨去", "加倍"]},
    "skill_points_v2": {"family": "points", "consts": {"per": 50, "birthday": "bonus", "birthday_bonus": 50},
                        "must_appear": ["50 元得 1 點", "無條件捨去", "50 點"]},
    "skill_compensation_v1": {"family": "compensation", "consts": {"mode": "per_day", "per_day": 20, "cap": 100},
                              "must_appear": ["1 天", "20 元", "100 元"]},
    "skill_compensation_v2": {"family": "compensation", "consts": {"mode": "flat", "min_days": 3, "flat": 50},
                              "must_appear": ["超過 3 天", "50 元", "0 元"]},
    "skill_installment_v1": {"family": "installment", "consts": {"rates": {"3": "0", "6": "0.03", "12": "0.05"}},
                             "must_appear": ["3 期：手續費 0%", "6 期：手續費 3%", "12 期：手續費 5%", "四捨五入"]},
    "skill_installment_v2": {"family": "installment", "consts": {"rates": {"3": "0.01", "6": "0.04", "12": "0.06"}},
                             "must_appear": ["3 期：手續費 1%", "6 期：手續費 4%", "12 期：手續費 6%", "四捨五入"]},
}


def compute(skill_id, inputs):
    s = SKILLS[skill_id]
    return FAMILIES[s["family"]](s["consts"], **inputs)
