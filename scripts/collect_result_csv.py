#!/usr/bin/env python3
"""Collect synthesis results from the result directory and export them as CSV."""

from __future__ import annotations

import argparse
import csv
import re
import sys
from pathlib import Path


DIR_PATTERN = re.compile(
    r"^(?P<design>.+)-(?P<tech>[^-]+)-(?P<freq_mhz>\d+(?:\.\d+)?)MHz-M(?P<m>\d+)-N(?P<n>\d+)$"
)
AREA_PATTERN = re.compile(r"Total cell area:\s+([0-9.eE+-]+)")
POWER_LINE_PATTERN = re.compile(
    r"^(?P<hierarchy>\S+)\s+"
    r"(?P<int_power>[0-9.eE+-]+)\s+"
    r"(?P<switch_power>[0-9.eE+-]+)\s+"
    r"(?P<leak_power>[0-9.eE+-]+)\s+"
    r"(?P<total_power>[0-9.eE+-]+)\s+"
    r"(?P<percent>[0-9.eE+-]+)\s*$"
)
POWER_VALUES_PATTERN = re.compile(
    r"^\s*"
    r"(?P<int_power>[0-9.eE+-]+)\s+"
    r"(?P<switch_power>[0-9.eE+-]+)\s+"
    r"(?P<leak_power>[0-9.eE+-]+)\s+"
    r"(?P<total_power>[0-9.eE+-]+)\s+"
    r"(?P<percent>[0-9.eE+-]+)\s*$"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Collect dc/pt report data under a result directory and write a CSV summary."
    )
    parser.add_argument(
        "result_dir",
        nargs="?",
        default="result",
        help="Result directory to scan. Default: %(default)s",
    )
    parser.add_argument(
        "-o",
        "--output",
        default="result_summary.csv",
        help="Output CSV path. Default: %(default)s",
    )
    return parser.parse_args()


def prettify_tech(raw_tech: str) -> str:
    lower = raw_tech.lower()
    if "asap7" in lower:
        return "ASAP7nm"
    return raw_tech


def parse_area(report_path: Path) -> float | None:
    if not report_path.exists():
        return None

    text = report_path.read_text(encoding="utf-8", errors="ignore")
    match = AREA_PATTERN.search(text)
    if not match:
        return None
    return float(match.group(1))


def parse_power(report_path: Path) -> float | None:
    if not report_path.exists():
        return None

    lines = report_path.read_text(encoding="utf-8", errors="ignore").splitlines()
    for line in reversed(lines):
        match = POWER_LINE_PATTERN.match(line.strip()) or POWER_VALUES_PATTERN.match(line)
        if not match:
            continue
        int_power = float(match.group("int_power"))
        switch_power = float(match.group("switch_power"))
        leak_power = float(match.group("leak_power"))
        total_power = float(match.group("total_power"))
        if abs((int_power + switch_power + leak_power) - total_power) > max(1e-6, total_power * 1e-3):
            continue
        return total_power
    return None


def build_entry(result_subdir: Path) -> dict[str, object]:
    match = DIR_PATTERN.match(result_subdir.name)
    if not match:
        return {
            "Entry": result_subdir.name,
            "Design": "",
            "Technology Node": "",
            "Clock Frequency (MHz)": "",
            "Clock Frequency (GHz)": "",
            "Array Size": "",
            "Cell Area (um²)": "",
            "Total Power (W)": "",
            "Energy per MAC (pJ/MAC)": "",
            "Status": "unrecognized directory name",
            "Source Directory": str(result_subdir),
        }

    design = match.group("design")
    raw_tech = match.group("tech")
    freq_mhz = float(match.group("freq_mhz"))
    freq_ghz = freq_mhz / 1000.0
    array_m = int(match.group("m"))
    array_n = int(match.group("n"))
    array_size = array_m * array_n

    area = parse_area(result_subdir / "dc.area.rpt")
    power = parse_power(result_subdir / "pt.power.rpt")

    energy_per_mac_pj = None
    if power is not None and freq_mhz > 0 and array_size > 0:
        energy_per_mac_pj = power / (freq_ghz * 1e9 * array_size) * 1e12

    missing = []
    if area is None:
        missing.append("dc.area.rpt")
    if power is None:
        missing.append("pt.power.rpt")

    return {
        "Entry": result_subdir.name,
        "Design": design,
        "Technology Node": prettify_tech(raw_tech),
        "Clock Frequency (MHz)": f"{freq_mhz:g}",
        "Clock Frequency (GHz)": f"{freq_ghz:g}",
        "Array Size": array_size,
        "Cell Area (um²)": "" if area is None else f"{area:.6f}",
        "Total Power (W)": "" if power is None else f"{power:.6e}",
        "Energy per MAC (pJ/MAC)": "" if energy_per_mac_pj is None else f"{energy_per_mac_pj:.6f}",
        "Status": "ok" if not missing else f"missing {', '.join(missing)}",
        "Source Directory": str(result_subdir),
    }


def main() -> int:
    args = parse_args()
    result_dir = Path(args.result_dir).resolve()
    output_path = Path(args.output).resolve()

    if not result_dir.exists() or not result_dir.is_dir():
        print(f"Result directory not found: {result_dir}", file=sys.stderr)
        return 1

    entries = [build_entry(path) for path in sorted(result_dir.iterdir()) if path.is_dir()]
    config_names = [f"Config {index}" for index in range(1, len(entries) + 1)]
    parameters = [
        "Technology Node",
        "Clock Frequency (GHz)",
        "Cell Area (um²)",
        "Array Size",
        "Total Power (W)",
        "Energy per MAC (pJ/MAC)",
    ]

    with output_path.open("w", newline="", encoding="utf-8") as csv_file:
        writer = csv.writer(csv_file)
        writer.writerow(["Parameter", *config_names])
        for parameter in parameters:
            writer.writerow([parameter, *[entry[parameter] for entry in entries]])

    ok_count = sum(1 for entry in entries if entry["Status"] == "ok")
    print(f"Wrote {len(parameters)} parameter rows for {len(entries)} entries to {output_path}")
    print(f"Complete entries: {ok_count}")
    print(f"Incomplete entries: {len(entries) - ok_count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
