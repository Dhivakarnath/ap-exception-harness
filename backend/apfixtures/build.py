"""CLI: generate the adversarial invoice dataset.

    python -m apfixtures.build                # 50 cases per mode (the FR-13.2 floor)
    python -m apfixtures.build --per-mode 3   # quick smoke run
    python -m apfixtures.build --verify-only  # check files against the manifest

Determinism: every case derives its randomness from
`SeedSequence(seed, case_index)`, so a case renders identically regardless of how
many other cases are generated or in what order. Generating 3 cases produces
byte-identical files to the first 3 of a 50-case run.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

import numpy as np

from apfixtures.cases import all_modes, build_case
from apfixtures.manifest import (
    build_entry,
    build_manifest,
    load_manifest,
    verify_manifest,
    write_manifest,
)
from apfixtures.render import render_case
from apfixtures.spec import FailureMode

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT_DIR = REPO_ROOT / "datasets" / "generated"
FILES_SUBDIR = "invoices"
MANIFEST_NAME = "manifest.json"

DEFAULT_SEED = 20260302
DEFAULT_PER_MODE = 50


def _case_rng(seed: int, case_ordinal: int) -> np.random.Generator:
    """Per-case generator, independent of generation order."""
    return np.random.default_rng(np.random.SeedSequence([seed, case_ordinal]))


def generate(
    *,
    output_dir: Path,
    seed: int = DEFAULT_SEED,
    per_mode: int = DEFAULT_PER_MODE,
    modes: tuple[FailureMode, ...] | None = None,
    clean: bool = True,
    quiet: bool = False,
) -> Path:
    """Generate artefacts and the manifest. Returns the manifest path."""
    modes = modes or all_modes()
    files_dir = output_dir / FILES_SUBDIR

    if clean and files_dir.exists():
        shutil.rmtree(files_dir)
    files_dir.mkdir(parents=True, exist_ok=True)

    entries = []
    ordinal = 0

    for mode in modes:
        for index in range(per_mode):
            case = build_case(mode, index)

            # Label variety is index-derived so it is reproducible. The
            # LABEL_VARIATION mode reaches for the deliberately awkward set.
            variant = index
            adversarial_labels = mode is FailureMode.LABEL_VARIATION

            payload = render_case(
                case,
                variant=variant,
                adversarial_labels=adversarial_labels,
                rng=_case_rng(seed, ordinal),
            )
            rel_filename = f"{case.tenant_id}/{case.filename}"
            artefact_path = files_dir / rel_filename
            artefact_path.parent.mkdir(parents=True, exist_ok=True)
            artefact_path.write_bytes(payload)
            entries.append(build_entry(case, payload, filename=rel_filename))
            ordinal += 1

        if not quiet:
            print(f"  {mode.value:36} {per_mode:4d} cases")

    manifest = build_manifest(entries, generator_seed=seed, cases_per_mode=per_mode)
    manifest_path = output_dir / MANIFEST_NAME
    write_manifest(manifest, manifest_path)

    if not quiet:
        total_bytes = sum(e.size_bytes for e in entries)
        print()
        print(f"  total cases : {manifest.total_cases}")
        print(f"  total size  : {total_bytes / 1_048_576:.1f} MiB")
        print(f"  formats     : {manifest.format_counts}")
        print(f"  files       : {files_dir}")
        print(f"  manifest    : {manifest_path}")

    return manifest_path


def verify(output_dir: Path, *, quiet: bool = False) -> bool:
    """Verify on-disk artefacts against the manifest."""
    manifest = load_manifest(output_dir / MANIFEST_NAME)
    drift = verify_manifest(manifest, output_dir / FILES_SUBDIR)
    if not quiet:
        status = "OK" if drift.is_clean else "DRIFT"
        print(f"[{status}] {drift.describe()} ({manifest.total_cases} entries)")
    return drift.is_clean


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="apfixtures.build",
        description="Generate the adversarial invoice dataset with a ground-truth manifest.",
    )
    parser.add_argument(
        "--per-mode",
        type=int,
        default=DEFAULT_PER_MODE,
        help=f"Cases per failure mode (default {DEFAULT_PER_MODE}; FR-13.2 requires >= 50).",
    )
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED, help="Generator seed.")
    parser.add_argument(
        "--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR, help="Output directory."
    )
    parser.add_argument(
        "--mode",
        action="append",
        dest="selected_modes",
        help="Only generate this failure mode (repeatable).",
    )
    parser.add_argument(
        "--no-clean", action="store_true", help="Keep existing files instead of clearing."
    )
    parser.add_argument(
        "--verify-only", action="store_true", help="Verify existing output and exit."
    )
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    if args.verify_only:
        return 0 if verify(args.output_dir, quiet=args.quiet) else 1

    selected: tuple[FailureMode, ...] | None = None
    if args.selected_modes:
        try:
            selected = tuple(FailureMode(m) for m in args.selected_modes)
        except ValueError as exc:
            known = ", ".join(m.value for m in all_modes())
            print(f"error: {exc}\nknown modes: {known}", file=sys.stderr)
            return 2

    if args.per_mode < 50 and not args.quiet:
        # Not blocked: small runs are useful for iteration. But the floor exists
        # because smaller eval sets produce pass rates that collapse under real
        # distribution shift, so understating it silently would be misleading.
        print(
            f"note: --per-mode {args.per_mode} is below the 50-case floor in FR-13.2. "
            "Use this for iteration, not for reported results.\n"
        )

    if not args.quiet:
        print(f"generating dataset (seed={args.seed}, per_mode={args.per_mode})\n")

    generate(
        output_dir=args.output_dir,
        seed=args.seed,
        per_mode=args.per_mode,
        modes=selected,
        clean=not args.no_clean,
        quiet=args.quiet,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
