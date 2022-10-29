import inspect, warnings
from typing import Any, Callable, List, Optional, Sequence, Mapping, Dict, Tuple
from pathlib import Path

from ..rule.rule import Rule
from ..core.abc import IEvent, IRule
from ..core import events
from ..logwriter import IWriter, RichStr


def get_rule_name(r: IRule, id2name: Callable[[int], str]):
    if isinstance(r, Rule):
        return id2name(r.id)  # pyright: ignore [reportUnknownMemberType]
    else:
        warnings.warn(
            "Internal Error: an event of unnamed Rule has been emitted."
        )
        return "<unknown>"
    

def log_make_event(w: IWriter, e: IEvent, id2name: Callable[[int], str]):
    if isinstance(e, events.ErrorRuleEvent):
        # Show stack trace and error message
        w.warning("".join(e.trace_exc.format()))

        r = e.rule

        name = RichStr(get_rule_name(r, id2name), c=(0, 0xCC, 0))

        if isinstance(e, events.PreProcError):
            w.error(
                "Failed to make ",
                name,
                ": An error occured during preprocessing",
            )
        elif isinstance(e, events.ExecError):
            w.error(
                "Failed to make ",
                name,
                f": Method {get_func_name(r.method)} failed",
            )
        elif isinstance(e, events.PostProcError):
            w.error(
                f"Failed to make {name}: "
                f"An error occured during post-processing. "
                f"Make sure to remove the output files (if any) "
                f"by yourself"
            )
        elif isinstance(e, events.FatalError):
            w.error("Fatal error")
        else:
            w.warning(f"Unhandled error event for {r}")

        return
    elif isinstance(e, events.RuleEvent):
        r = e.rule

        name = RichStr(get_rule_name(r, id2name), c=(0, 0xCC, 0))

        if isinstance(e, events.Skip):
            if e.is_direct_target:
                w.info("Skip ", name)
            else:
                w.debug("Skip ", name)
        elif isinstance(e, events.Start):
            msg = []
            tostrs_func_call(msg, r.method, r.args, r.kwargs)
            msg = add_indent(msg, "  ")
            w.info("Make ", name, "\n", *msg)
        elif isinstance(e, events.Done):
            w.info("Done ", name)
        elif isinstance(e, events.DryRun):
            msg = []
            tostrs_func_call(msg, r.method, r.args, r.kwargs)
            msg = add_indent(msg, "  ")
            w.info("Make (dry) ", name, "\n", *msg)
        elif isinstance(e, events.UpdateInfeasible):
            w.error(
                "Cannot make ",
                name,
                f": {e.reason}",
            )
        else:
            w.warning(f"Unhandled event for {r}")
        return
    elif isinstance(e, events.StopOnFail):
        w.warning("Execution aborted due to an error")
    else:
        w.warning(f"Unhandled event {e}")


def add_indent(sl: Sequence[str], indent: str) -> List[str]:
    res: List[str] = []
    for i, s in enumerate(sl):
        if i == 0 or (i > 0 and len(sl[i - 1]) > 0 and sl[i - 1][-1] == "\n"):
            res.append(indent + s)
        else:
            res.append(s)
    return res


def get_func_name(f: Callable[..., object]) -> str:
    try:
        name, mod = f.__qualname__, f.__module__

        if mod == "builtins" or mod == "__main__":
            return name
        else:
            return f"{mod}.{name}"
    except:
        return "<unkonw function>"


def tostrs_func_call(
    dst: List[str],
    f: Callable[..., object],
    args: Tuple[object, ...],
    kwargs: Dict[str, object]
):
    bn = inspect.signature(f).bind(*args, **kwargs)
    bn.apply_defaults()

    dst.append(RichStr(get_func_name(f), c=(0, 0x80, 0xFF)))
    dst.append("(\n")

    for k, v in bn.arguments.items():
        dst.append(RichStr(f"  {k}", c=(0xFF, 0x80, 0)))
        dst.append(f" = ")
        tostrs_obj(dst, v, capacity=500)
        dst.append(",\n")
    dst.append(")\n")


def tostrs_obj(dst: List[str], o: Any, capacity: Optional[int] = None):
    _tostrs_obj(dst, o, capacity or 10**10)


def _tostrs_obj(dst: List[str], o: object, capacity: int) -> int:
    if isinstance(o, (tuple, list)):
        dst.append("[")
        for i, v in enumerate(o):  # pyright: ignore [reportUnknownVariableType]
            if capacity <= 0:
                dst.append(", ...")
                capacity -= 5
                break
            capacity = _tostrs_obj(dst, v, capacity)
            if i < len(o) - 1:
                dst.append(", ")
                capacity -= 2
        dst.append("]")
        return capacity
    elif isinstance(o, Mapping):
        dst.append("{")
        for i, (k, v) in enumerate(o.items()):  # pyright: ignore [reportUnknownVariableType]
            if capacity <= 0:
                dst.append(", ... ")
                capacity -= 5
                break
            dst.append(repr(k) + ": ")
            capacity = _tostrs_obj(dst, v, capacity)
            if i < len(o) - 1:
                dst.append(", ")
                capacity -= 2
        dst.append("}")
        return capacity
    elif isinstance(o, Path):
        res = RichStr(repr(o), link=str(o))
        dst.append(res)
        return capacity - len(res)
    else:
        res = repr(o)
        if len(res) > capacity:
            res = res[: capacity // 2] + " ... " + res[-capacity // 2 :]
        dst.append(res)
        return capacity - len(res)