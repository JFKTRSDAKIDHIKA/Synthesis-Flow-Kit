#!/usr/bin/env python3
"""Fill an @KEY@ template.

    render.py --template flow/yosys/synth.ys.in --out build/.../synth.ys \\
              TOP=AmeB8OOO ABC_D=400 ...

Substitution is literal and single pass, so a value that happens to contain
@SOMETHING@ is not rescanned. Any @KEY@ left over after substitution is an
error: a half-rendered Yosys or Tcl script fails much later and much less
clearly than this does.
"""

from __future__ import annotations

import argparse
import re
import sys

LEFTOVER = re.compile(r"@[A-Z][A-Z0-9_]*@")


def render(text: str, values: dict[str, str], name: str = "template") -> str:
    """Substitute @KEY@ holes; every hole left over is an error."""
    out = []
    pos = 0
    for m in LEFTOVER.finditer(text):
        key = m.group(0)[1:-1]
        if key not in values:
            continue
        out.append(text[pos:m.start()])
        out.append(values[key])
        pos = m.end()
    out.append(text[pos:])
    rendered = "".join(out)

    missing = sorted({m.group(0) for m in LEFTOVER.finditer(rendered)})
    if missing:
        raise SystemExit("ERROR: %s has unsubstituted keys: %s"
                         % (name, " ".join(missing)))
    return rendered


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--template", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("assignments", nargs="*", metavar="KEY=VALUE")
    args = ap.parse_args()

    values = {}
    for a in args.assignments:
        if "=" not in a:
            raise SystemExit("ERROR: expected KEY=VALUE, got %r" % a)
        key, _, value = a.partition("=")
        values[key] = value

    text = open(args.template, encoding="utf-8").read()
    rendered = render(text, values, args.template)

    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write(rendered)
    return 0


if __name__ == "__main__":
    sys.exit(main())
