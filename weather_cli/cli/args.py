"""Argument parsing with the semantics (and error wording) of Node's strict `util.parseArgs`.

Accessed via: weather_cli/cli/main.py. Hand-written instead of argparse because argparse accepts
abbreviations and prints its own usage text; scripts that call `weather` depend on the exact
errors and on `--lat -5` being rejected as ambiguous.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

Value = bool | str


class ArgsError(Exception):
    """A malformed command line: reported as a runtime failure (exit 1), not a usage error."""


class UsageError(Exception):
    """A bad flag value or command: exit code 2."""


@dataclass(slots=True)
class Parsed:
    values: dict[str, Value] = field(default_factory=dict)
    positionals: list[str] = field(default_factory=list)


def _unknown(shown: str) -> ArgsError:
    return ArgsError(
        f"Unknown option '{shown}'. To specify a positional argument starting with a '-', "
        f"place it at the end of the command after '--', as in '-- \"{shown}\""
    )


def _short_unknown(arg: str) -> ArgsError:
    # A lone short flag is echoed with its dash, a cluster is reported by its first letter.
    return _unknown(arg if len(arg) == 2 else arg[1])


def _string_value(args: list[str], index: int, name: str) -> str:
    if index + 1 >= len(args):
        raise ArgsError(f"Option '--{name} <value>' argument missing")
    following = args[index + 1]
    if following.startswith("-") and len(following) > 1:
        raise ArgsError(
            f"Option '--{name}' argument is ambiguous.\n"
            f"Did you forget to specify the option argument for '--{name}'?\n"
            f"To specify an option argument starting with a dash use '--{name}=-XYZ'."
        )
    return following


def _long_option(args: list[str], index: int, options: Mapping[str, str], parsed: Parsed) -> int:
    """Consume `--name`, `--name=value` or `--name value`; returns the last index used."""
    arg = args[index]
    name, has_inline, inline = arg[2:].partition("=")
    if name not in options:
        raise _unknown(arg if name == "" else f"--{name}")
    if options[name] == "boolean":
        if has_inline:
            raise ArgsError(f"Option '--{name}' does not take an argument")
        parsed.values[name] = True
        return index
    if has_inline:
        parsed.values[name] = inline
        return index
    parsed.values[name] = _string_value(args, index, name)
    return index + 1


def parse_args(args: list[str], options: Mapping[str, str]) -> Parsed:
    """`options` maps a long name to `"boolean"` or `"string"`; positionals are allowed."""
    parsed = Parsed()
    index = 0
    while index < len(args):
        arg = args[index]
        if arg == "--":
            parsed.positionals.extend(args[index + 1 :])
            break
        if arg.startswith("--"):
            index = _long_option(args, index, options, parsed)
        elif arg.startswith("-") and len(arg) > 1:
            raise _short_unknown(arg)
        else:
            parsed.positionals.append(arg)
        index += 1
    return parsed
