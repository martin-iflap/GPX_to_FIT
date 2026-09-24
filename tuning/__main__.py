"""Command line for the pacing tuning harness.

    uv run python -m tuning compare corpus/run01.fit       # read one activity
    uv run python -m tuning sweep   corpus/                # what each activity wanted
    uv run python -m tuning fit     corpus/                # fit the constants
    uv run python -m tuning check   corpus/                # guard against regressions

Every command takes files or directories; a directory is searched for .fit
files recursively. An activity that fails a quality gate is reported with its
reason and skipped, never dropped silently.
"""

import argparse
import json
import sys
from io import TextIOWrapper
from pathlib import Path

from gpx2fit.core.models import SportType
from gpx2fit.core.pacing.curve_selection import MAX_SPEED_RATIO_BOUNDS
from tuning.cache import DEFAULT_CACHE_DIR, content_key, decoded_activity, warm_cache
from tuning.compare import DEFAULT_BUCKET_M, compare
from tuning.elevation import DEFAULT_CACHE_DIR as DEM_CACHE_DIR
from tuning.elevation import with_dem_elevation
from tuning.fit import ActivityOptimum, fit_constants, sweep_activity
from tuning.fit_reader import UnreadableActivity, read_decoded_activity
from tuning.model import PacingParams
from tuning.prepare import DEFAULT_SPACING_M, PreparedActivity, prepare_reference
from tuning.report import format_report, report_to_dict, write_json

DEFAULT_OUT_DIR = Path("tuning_out")


def _fit_paths(paths: list[str]) -> list[Path]:
    """Every .fit file under the given files or directories, sorted by name."""
    found: list[Path] = []
    for raw in paths:
        path = Path(raw)
        if path.is_dir():
            found.extend(sorted(path.rglob("*.fit")))
        elif path.is_file():
            found.append(path)
        else:
            print(f"  ! {path}: no such file or directory", file=sys.stderr)
    return found


def _drop_duplicates(paths: list[Path]) -> list[Path]:
    """Keep the first of any files with identical contents, reporting the rest.

    The same activity exported twice under two names would count double in a
    fit, and could land one copy in train and the other in held-out, making the
    held-out score look better than it is. Unreadable files are passed through
    so they're reported where every other failure is.
    """
    seen: dict[str, Path] = {}
    kept: list[Path] = []
    for path in paths:
        try:
            key = content_key(path.read_bytes())
        except OSError:
            kept.append(path)
            continue
        if key in seen:
            print(f"  ! skipped  {path}: same contents as {seen[key]}", file=sys.stderr)
            continue
        seen[key] = path
        kept.append(path)
    return kept


def _unique_names(paths: list[Path]) -> dict[Path, str]:
    """Name each activity by its file stem, qualified by folder where stems collide.

    A corpus sorted into per-sport folders routinely holds two `export1.fit`s,
    and the name is a key, not a label: `check` stores one objective per name
    in its baseline and `compare --json` writes one file per name, so a
    collision would silently drop an activity. Only the ambiguous ones get the
    longer name, so the common case still reads as a plain filename.
    """
    by_stem: dict[str, list[Path]] = {}
    for path in paths:
        by_stem.setdefault(path.stem, []).append(path)

    return {
        path: (path.stem if len(group) == 1 else f"{path.parent.name}/{path.stem}")
        for group in by_stem.values()
        for path in group
    }


def _load_trims(path: str | None) -> dict[str, tuple[float, float]]:
    """Load per-activity distance ranges from a JSON file.

    Shape is {"activity-name": [start_km, end_km]}. This is how a review
    decision — "only 1.0 to 8.5 km of this one is usable" — is recorded once
    and then applied by every later command, instead of living in a shell
    history somewhere.
    """
    if not path:
        return {}
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    return {name: (float(value[0]), float(value[1])) for name, value in raw.items()}


def _load_sports(path: str | None) -> dict[str, SportType]:
    """Load sport overrides from a JSON file, keyed by activity or folder name.

    Shape is {"name": "running"}, where a name matches either an activity's own
    name or any folder on its path — so a corpus already sorted into folders
    needs one line per folder rather than one per file. Same idea as `--trims`:
    a review decision recorded once and reused by every later command.

    This exists because `--sport` applies to everything given, and `fit` has to
    see both sports at once to fit their per-sport bounds in one pass.
    """
    if not path:
        return {}
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    return {name: SportType(value) for name, value in raw.items()}


def _sport_for(path: Path, sport: SportType | None, sports: dict[str, SportType]) -> SportType | None:
    """The override for one file: its own entry, else its nearest named folder, else the blanket one."""
    if path.stem in sports:
        return sports[path.stem]
    for parent in path.parts[-2::-1]:
        if parent in sports:
            return sports[parent]
    return sport


def _load_activities(
    paths: list[str],
    trims: dict[str, tuple[float, float]],
    spacing_m: float | None = None,
    sport: SportType | None = None,
    sports: dict[str, SportType] | None = None,
    cache_dir: Path | None = DEFAULT_CACHE_DIR,
    workers: int | None = None,
    dem_elevation: bool = False,
    trim_ends_m: float = 0.0,
) -> list[PreparedActivity]:
    """Read and prepare every activity, reporting each rejection with its reason.

    Decoding is the expensive part by an order of magnitude, so anything not
    already in the decode cache is decoded across a process pool first; the
    loop below then reads every activity from the cache. See tuning/cache.py.

    With `dem_elevation`, a file whose recorded elevation is missing or noisy
    gets DEM heights instead, before any reading rule runs (see
    tuning/elevation.py). Without it, those files fail prepare.py's elevation
    gate as before.
    """
    found = _drop_duplicates(_fit_paths(paths))
    names = _unique_names(found)

    decoded_count = warm_cache(found, cache_dir, workers)
    if decoded_count:
        print(f"  decoded {decoded_count} new file(s) into the cache", file=sys.stderr)

    prepared: list[PreparedActivity] = []
    for path in found:
        name = names[path]
        try:
            decoded = decoded_activity(path, cache_dir)
            elevation_problem = None
            if dem_elevation:
                decoded, elevation_problem = with_dem_elevation(decoded, content_key(path.read_bytes()), name)
            activity = read_decoded_activity(
                decoded, name,
                sport_override=_sport_for(path, sport, sports or {}),
            )
            activity_prepared = prepare_reference(
                activity,
                spacing_m=spacing_m if spacing_m is not None else DEFAULT_SPACING_M,
                # A trims file written before a name needed qualifying still
                # applies, so the bare stem is accepted as a fallback key.
                trim_km=trims.get(name, trims.get(path.stem)),
                trim_ends_m=trim_ends_m,
            )
            if elevation_problem is not None:
                activity_prepared.notes.insert(
                    0, f"elevation from the DEM (the recorded elevation was {elevation_problem})"
                )
            prepared.append(activity_prepared)
        except UnreadableActivity as error:
            print(f"  ! skipped  {error}", file=sys.stderr)
        except Exception as error:  # A corpus file shouldn't be able to kill the run.
            print(f"  ! skipped  {name}: unexpected {type(error).__name__}: {error}", file=sys.stderr)
    return prepared


def _activities_from(args: argparse.Namespace) -> list[PreparedActivity]:
    """Every usable activity named on the command line, prepared and ready to score."""
    return _load_activities(
        args.paths,
        _load_trims(args.trims),
        args.spacing,
        args.sport,
        _load_sports(args.sports),
        cache_dir=None if args.no_cache else Path(args.cache_dir),
        workers=args.workers,
        dem_elevation=args.dem_elevation,
        trim_ends_m=args.trim_ends,
    )


def _params_from(path: str | None) -> PacingParams:
    """Load a PacingParams from JSON, or today's shipped constants if no path is given."""
    if not path:
        return PacingParams()
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    return PacingParams(**raw.get("global", raw))


def command_compare(args: argparse.Namespace) -> int:
    """Print one report per activity, and optionally dump the JSON and the synthesized GPX."""
    activities = _activities_from(args)
    if not activities:
        print("No usable activities.", file=sys.stderr)
        return 1

    params = _params_from(args.params)
    out_dir = Path(args.out_dir)

    for activity in activities:
        report = compare(activity, params, args.bucket)
        print(format_report(report))
        if args.json:
            write_json(out_dir / "activities" / f"{activity.name}.json", report_to_dict(report))
        if args.dump_gpx:
            gpx_path = out_dir / "gpx" / f"{activity.name}.gpx"
            gpx_path.parent.mkdir(parents=True, exist_ok=True)
            gpx_path.write_bytes(activity.gpx_bytes)

    print()
    if args.json or args.dump_gpx:
        print(f"Wrote output under {out_dir}/")
    return 0


def _print_optima(optima: list[ActivityOptimum]) -> None:
    """The stage-one table: what each activity wanted, versus what it got."""
    header = (
        "  activity                       sport     dist   vert   flatEq"
        "   w:got  w:want   r:got  r:want    obj:got  obj:best   headroom"
    )
    rule = "  " + "-" * (len(header) - 2)
    print()
    print(header)
    print(rule)
    for best in sorted(optima, key=lambda item: (item.sport.value, item.name)):
        print(
            f"  {best.name[:28]:<28}{best.sport.value:>8}"
            f"{best.distance_m / 1000:>8.1f}"
            f"{best.verticality:>7.3f}"
            f"{best.flat_equivalent_mps:>8.2f}"
            f"{best.resolved_tobler_weight:>8.2f}{best.best_tobler_weight:>8.2f}"
            f"{best.resolved_max_speed_ratio:>8.2f}{best.best_max_speed_ratio:>8.2f}"
            f"{best.resolved_objective:>11.4f}{best.best_objective:>10.4f}"
            f"{best.headroom:>11.4f}"
        )
    print(rule)
    print("  w = tobler_weight, r = max_speed_ratio. 'got' is what the current constants chose,")
    print("  'want' is what the activity's own error surface preferred.")
    print("  headroom = how much of this activity's error the resolvers could fix by choosing better.")
    print("  A high obj:best with low headroom means the curve shape is wrong, not the resolvers.")


def command_sweep(args: argparse.Namespace) -> int:
    """Stage one across the corpus: each activity's empirical optimum."""
    activities = _activities_from(args)
    if not activities:
        print("No usable activities.", file=sys.stderr)
        return 1

    params = _params_from(args.params)
    optima = [sweep_activity(activity, params, args.bucket) for activity in activities]
    _print_optima(optima)

    out = Path(args.out_dir) / "optima.json"
    write_json(out, {
        "activities": [
            {
                "name": best.name,
                "sport": best.sport.value,
                "distance_m": best.distance_m,
                "moving_seconds": best.moving_seconds,
                "verticality": best.verticality,
                "flat_equivalent_mps": best.flat_equivalent_mps,
                "resolved_tobler_weight": best.resolved_tobler_weight,
                "resolved_max_speed_ratio": best.resolved_max_speed_ratio,
                "resolved_objective": best.resolved_objective,
                "best_tobler_weight": best.best_tobler_weight,
                "best_max_speed_ratio": best.best_max_speed_ratio,
                "best_objective": best.best_objective,
                "headroom": best.headroom,
            }
            for best in optima
        ]
    })
    print(f"\nWrote {out}")
    return 0


def command_fit(args: argparse.Namespace) -> int:
    """Stage two: fit the constants, and report how they hold up on held-out activities."""
    activities = _activities_from(args)
    if not activities:
        print("No usable activities.", file=sys.stderr)
        return 1

    base = _params_from(args.params)
    result = fit_constants(
        activities, base, rounds=args.rounds, holdout=args.holdout, seed=args.seed, bucket_m=args.bucket
    )

    print()
    print(f"  corpus      {len(activities)} activities"
          f"  ({len(result.train_names)} train / {len(result.test_names)} held out)")
    print(f"  train       {result.train_before:.4f} -> {result.train_after:.4f}")
    if result.test_names:
        print(f"  held out    {result.test_before:.4f} -> {result.test_after:.4f}")
        train_gain = result.train_before - result.train_after
        test_gain = result.test_before - result.test_after
        if train_gain > 0 and test_gain < train_gain * 0.4:
            print("  ! the held-out set improved far less than training — this fit is memorising.")
    else:
        print("  held out    (none; every activity was trained on, so these numbers are optimistic)")
    if result.route_groups:
        held_out = set(result.test_names)
        print()
        print("  same-route groups (similar distance and verticality, kept on one side of the split):")
        for group in result.route_groups:
            side = "held out" if group[0] in held_out else "train"
            print(f"    {side:<9} {', '.join(group)}")

    # The trace comes first: if a block still moves in the last round, the
    # numbers below it aren't settled, whatever the objective says.
    print()
    print("  round  stage             sport       train    values")
    print("  " + "-" * 72)
    for step in result.history:
        values = "  ".join(f"{name} {value:.3f}" for name, value in step.values.items())
        sport = step.sport.value if step.sport is not None else "all"
        print(f"  {step.round:>5}  {step.stage:<16}  {sport:<9}  {step.train_objective:.4f}"
              f"  {'*' if step.changed else ' '} {values}")
    print("  (* = the block moved in that step)")

    print()
    print("  global constant            before     after")
    print("  " + "-" * 44)
    for name in sorted(vars(result.global_params)):
        before, after = getattr(base, name), getattr(result.global_params, name)
        if before != after:
            print(f"  {name:<24}{before:>9.4f}{after:>10.4f}")

    for sport, values in sorted(result.sport_params.items(), key=lambda item: item[0].value):
        bounds = MAX_SPEED_RATIO_BOUNDS[sport]
        shipped = {"ratio_flat": bounds.flat, "ratio_hilly": bounds.hilly}
        print()
        print(f"  {sport.value:<12} constant      before     after")
        print("  " + "-" * 44)
        for name, after in sorted(values.items()):
            before = getattr(base, name)
            before = before if before is not None else shipped[name]
            print(f"  {name:<24}{before:>9.4f}{after:>10.4f}")
    print()
    print("  The fills are fitted per sport, but core/ has one UPHILL_FILL and one")
    print("  DOWNHILL_FILL. If the sports disagree, shipping both needs a per-sport table there.")

    hits = result.boundary_hits()
    if hits:
        print()
        print("  ! these settled on the edge of their search range, so the corpus wanted to go further:")
        for hit in hits:
            print(f"      {hit}")
        print("    Widen the range in tuning/fit.py and refit, or read them as 'at least this much'.")

    out = Path(args.out_dir) / "proposed.json"
    write_json(out, {
        "global": {name: getattr(result.global_params, name) for name in vars(result.global_params)},
        "per_sport": {sport.value: values for sport, values in result.sport_params.items()},
        "history": [
            {
                "round": step.round,
                "stage": step.stage,
                "sport": step.sport.value if step.sport is not None else None,
                "values": step.values,
                "changed": step.changed,
                "train_objective": step.train_objective,
            }
            for step in result.history
        ],
        "train_before": result.train_before,
        "train_after": result.train_after,
        "test_before": result.test_before,
        "test_after": result.test_after,
        "train": result.train_names,
        "test": result.test_names,
        "route_groups": result.route_groups,
    })
    print(f"\nWrote {out}")
    print("These are proposals, not a patch. Check them against the per-activity notes before applying.")
    return 0


def command_check(args: argparse.Namespace) -> int:
    """Compare the corpus objective against a pinned baseline, so a refactor can't quietly regress pacing."""
    activities = _activities_from(args)
    if not activities:
        print("No usable activities.", file=sys.stderr)
        return 1

    params = _params_from(args.params)
    scores = {activity.name: compare(activity, params, args.bucket).objective for activity in activities}
    total_distance = sum(activity.distance_m for activity in activities)
    overall = sum(
        scores[activity.name] * activity.distance_m for activity in activities
    ) / total_distance

    baseline_path = Path(args.baseline)
    if args.write_baseline or not baseline_path.exists():
        write_json(baseline_path, {"overall": overall, "activities": scores})
        print(f"Wrote baseline {baseline_path}  (overall {overall:.4f}, {len(scores)} activities)")
        return 0

    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    print(f"  overall   {baseline['overall']:.4f} -> {overall:.4f}")

    regressed = [
        (name, baseline["activities"][name], score)
        for name, score in sorted(scores.items())
        if name in baseline["activities"] and score > baseline["activities"][name] + args.tolerance
    ]
    for name, was, now in regressed:
        print(f"  ! {name}: {was:.4f} -> {now:.4f}")

    if overall > baseline["overall"] + args.tolerance:
        print(f"\nFAIL: overall objective regressed by more than {args.tolerance}.")
        return 1
    print(f"\nOK: within {args.tolerance} of the baseline.")
    return 0


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("paths", nargs="+", help=".fit files, or directories to search recursively")
    parser.add_argument("--trims", help="JSON of {activity-name: [start_km, end_km]} usable ranges")
    parser.add_argument("--trim-ends", type=float, default=0.0, metavar="METRES",
                        help="cut this many metres from the start and end of every activity, e.g. 400 "
                             "to drop a run's walk-in and walk-out (combines with --trims as their overlap)")
    parser.add_argument("--params", help="JSON of constants to run with (default: the shipped ones)")
    parser.add_argument("--bucket", type=float, default=DEFAULT_BUCKET_M,
                        help=f"residual bucket length in metres (default {DEFAULT_BUCKET_M:.0f})")
    parser.add_argument("--spacing", type=float,
                        help="resampled point spacing in metres (default 15, emulating a planned GPX)")
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR), help="where to write output")
    parser.add_argument("--sport", type=SportType, choices=list(SportType),
                        metavar="{" + ",".join(sport.value for sport in SportType) + "}",
                        help="pace every given activity as this sport, whatever the file declares "
                             "(exports mislabel runs from bike computers as cycling, and hikes as generic)")
    parser.add_argument("--sports", help="JSON of {activity-or-folder-name: sport} overrides, for a "
                                         "mixed corpus; takes precedence over --sport")
    parser.add_argument("--cache-dir", default=str(DEFAULT_CACHE_DIR),
                        help=f"where decoded .fit files are kept (default {DEFAULT_CACHE_DIR})")
    parser.add_argument("--no-cache", action="store_true",
                        help="decode every file again instead of using the cache")
    parser.add_argument("--workers", type=int,
                        help="processes used to decode uncached files (default: one per core)")
    parser.add_argument("--dem-elevation", action="store_true",
                        help="for recordings whose elevation is missing or noisy, fetch DEM heights from "
                             "Valhalla's public /height service. Sends those recordings' positions to a "
                             f"third party; fetched once per file, then cached under {DEM_CACHE_DIR}")


def main(argv: list[str] | None = None) -> int:
    # Every report here is drawn with em dashes and arrows. A Windows console
    # defaults to cp1252, which renders them as "?" and makes the skip reasons
    # harder to read than they need to be.
    # Guarded because these are only a real console when nothing has replaced
    # them, and a captured stream has no encoding to reconfigure.
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, TextIOWrapper):
            stream.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(prog="python -m tuning", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    subparsers = parser.add_subparsers(dest="command", required=True)

    compare_parser = subparsers.add_parser("compare", help="report one or more activities against the model")
    _add_common(compare_parser)
    compare_parser.add_argument("--json", action="store_true", help="also write each report as JSON")
    compare_parser.add_argument("--dump-gpx", action="store_true",
                                help="also write the synthesized GPX, to drop into the browser GUI")
    compare_parser.set_defaults(func=command_compare)

    sweep_parser = subparsers.add_parser("sweep", help="find each activity's own best weight and bound")
    _add_common(sweep_parser)
    sweep_parser.set_defaults(func=command_sweep)

    fit_parser = subparsers.add_parser("fit", help="fit the constants to the corpus")
    _add_common(fit_parser)
    fit_parser.add_argument("--rounds", type=int, default=3,
                            help="the most passes through the staged blocks (stops early once nothing moves)")
    fit_parser.add_argument("--holdout", type=float, default=0.3, help="fraction held out (0 to use all)")
    fit_parser.add_argument("--seed", type=int, default=20260920, help="fixes the train/test split")
    fit_parser.set_defaults(func=command_fit)

    check_parser = subparsers.add_parser("check", help="compare the corpus against a pinned baseline")
    _add_common(check_parser)
    check_parser.add_argument("--baseline", default=str(DEFAULT_OUT_DIR / "baseline.json"))
    check_parser.add_argument("--write-baseline", action="store_true", help="overwrite the baseline")
    check_parser.add_argument("--tolerance", type=float, default=0.002,
                              help="how much the objective may worsen before this fails")
    check_parser.set_defaults(func=command_check)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
