"""Pure-function classifier: dyn_show_info → captcha type string.

No I/O, no state, deterministic. First matching rule wins. When no rule
matches returns Classification(captcha_type="unknown", matched_rule="fallback_unknown").
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

CAPTCHA_TYPES = ("slide", "icon_click", "image_select", "unknown")


@dataclass(frozen=True, slots=True)
class Classification:
    captcha_type: str
    matched_rule: str


@dataclass(frozen=True, slots=True)
class _TypeRule:
    name: str
    captcha_type: str
    predicate: Callable[[dict[str, Any]], bool]


def _is_image_select_show_type(dyn: dict[str, Any]) -> bool:
    return dyn.get("show_type") == "click_image_uncheck"


def _is_image_select_uc(dyn: dict[str, Any]) -> bool:
    click = dyn.get("bg_elem_cfg", {}).get("click_cfg", {})
    return "DynAnswerType_UC" in click.get("data_type", [])


def _is_slide(dyn: dict[str, Any]) -> bool:
    return "fg_binding_list" in dyn


def _is_icon_click(dyn: dict[str, Any]) -> bool:
    click = dyn.get("bg_elem_cfg", {}).get("click_cfg", {})
    if "DynAnswerType_POS" not in click.get("data_type", []):
        return False
    if "ins_elem_cfg" in dyn:
        return False
    instr = dyn.get("instruction", "")
    if not instr.startswith("请依次点击"):
        return False
    after = instr.split("：", 1)[1] if "：" in instr else ""
    return bool(after.strip())


_RULES: tuple[_TypeRule, ...] = (
    _TypeRule("image_select_show_type", "image_select", _is_image_select_show_type),
    _TypeRule("image_select_uc", "image_select", _is_image_select_uc),
    _TypeRule("slide_fg_binding", "slide", _is_slide),
    _TypeRule("icon_click_pos", "icon_click", _is_icon_click),
)


def classify(dyn: dict[str, Any]) -> Classification:
    for rule in _RULES:
        if rule.predicate(dyn):
            return Classification(captcha_type=rule.captcha_type, matched_rule=rule.name)
    return Classification(captcha_type="unknown", matched_rule="fallback_unknown")


__all__ = ["classify", "Classification", "CAPTCHA_TYPES"]
