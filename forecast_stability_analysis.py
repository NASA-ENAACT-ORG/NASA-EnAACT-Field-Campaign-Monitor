"""
forecast_stability_analysis.py

Analyzes how cloud cover forecasts change over time for the same date/TOD.

For each PDF in Forecast/, extracts raw cloud % per (date, TOD) and uses
the file's modification time as the "forecast issue date". Then computes
how much predictions drift as a function of lead time (days before the target).
"""

import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from collections import defaultdict
from typing import Dict, Tuple, Optional, List

try:
    import pdfplumber
except ImportError:
    sys.exit("pdfplumber not installed. Run: pip install pdfplumber")

# -- Config --------------------------------------------------------------------

FORECAST_DIR = Path(__file__).parent / "Forecast"
CLOUD_THRESHOLD = 33  # % used by the scheduler

# -- PDF Parsing ---------------------------------------------------------------

def parse_forecast_pdf_raw(pdf_path: Path) -> Dict[Tuple[date, str], int]:
    """
    Like walk_scheduler.parse_forecast_pdf, but returns raw cloud % instead
    of boolean. Returns {(date, tod): cloud_pct}.
    """
    tmp = pdf_path  # read directly (no OneDrive lock issues in analysis)
    result: Dict[Tuple[date, str], int] = {}

    with pdfplumber.open(tmp) as pdf:
        for page in pdf.pages:
            for table in page.extract_tables():
                for row in table:
                    if not row or row[0] is None:
                        continue
                    cell0 = str(row[0])
                    m = re.search(r"(\d+)/(\d+)/(\d{2,4})", cell0)
                    if not m:
                        continue
                    mo, dy, yr_ = int(m.group(1)), int(m.group(2)), int(m.group(3))
                    yr = (2000 + yr_) if yr_ < 100 else yr_
                    try:
                        d = date(yr, mo, dy)
                    except ValueError:
                        continue
                    if len(row) < 13:
                        continue

                    def pct(cell) -> Optional[int]:
                        if cell is None:
                            return None
                        m2 = re.search(r"(\d+)", str(cell))
                        return int(m2.group(1)) if m2 else None

                    am = pct(row[4])
                    md = pct(row[8])
                    pm = pct(row[12])
                    if am is not None:
                        result[(d, "AM")] = am
                    if md is not None:
                        result[(d, "MD")] = md
                    if pm is not None:
                        result[(d, "PM")] = pm

    # Fallback: regex text extraction
    if not result:
        result = _parse_raw_text_fallback(pdf_path)

    return result


def _parse_raw_text_fallback(pdf_path: Path) -> Dict[Tuple[date, str], int]:
    result: Dict[Tuple[date, str], int] = {}
    with pdfplumber.open(pdf_path) as pdf:
        text = "\n".join(p.extract_text() or "" for p in pdf.pages)
    flat = re.sub(r"\s+", " ", text)
    pattern = re.compile(
        r"(?:Sunday|Monday|Tuesday|Wednesday|Thursday|Friday|Saturday)\s+"
        r"(\d+)/(\d+)/(\d+)\s+"
        r"\d+%\s+\d+%\s+\d+%\s+(\d+)%\s+"
        r"\d+%\s+\d+%\s+\d+%\s+(\d+)%\s+"
        r"\d+%\s+\d+%\s+\d+%\s+(\d+)%"
    )
    for m in pattern.finditer(flat):
        mo, dy, yr_ = int(m.group(1)), int(m.group(2)), int(m.group(3))
        yr = (2000 + yr_) if yr_ < 100 else yr_
        try:
            d = date(yr, mo, dy)
        except ValueError:
            continue
        result[(d, "AM")] = int(m.group(4))
        result[(d, "MD")] = int(m.group(5))
        result[(d, "PM")] = int(m.group(6))
    return result


# -- Main Analysis -------------------------------------------------------------

def main():
    pdfs = sorted(FORECAST_DIR.glob("*.pdf"))
    if not pdfs:
        sys.exit(f"No PDFs found in {FORECAST_DIR}")

    print(f"Found {len(pdfs)} forecast PDFs\n")

    # For each PDF: extract data + record issue datetime (file mtime)
    # Structure: {(target_date, tod): [(issue_datetime, cloud_pct, filename), ...]}
    slot_history: Dict[Tuple[date, str], List[Tuple[datetime, int, str]]] = defaultdict(list)

    for pdf in pdfs:
        mtime = datetime.fromtimestamp(pdf.stat().st_mtime)
        print(f"  Parsing: {pdf.name:35s} (modified {mtime.strftime('%b %d %H:%M')})")
        data = parse_forecast_pdf_raw(pdf)
        if not data:
            print(f"    [WARN] No data extracted from {pdf.name}")
            continue
        for (d, tod), pct_val in data.items():
            slot_history[(d, tod)].append((mtime, pct_val, pdf.name))

    print(f"\n{'-'*60}")
    print("FORECAST STABILITY ANALYSIS")
    print(f"{'-'*60}\n")

    # Only analyze slots that have >=2 forecasts
    multi = {k: v for k, v in slot_history.items() if len(v) >= 2}
    print(f"Slots with multiple forecasts (analyzable): {len(multi)}")
    print(f"Total unique date/TOD slots seen: {len(slot_history)}\n")

    # -- Build lead-time drift table -------------------------------------------
    # For each slot, sort by issue time, then compare each earlier forecast
    # to the most recent ("final") forecast.
    # lead_time = (target_date - issue_date.date()).days

    # Collect: lead_days -> list of (delta_pct, flipped_bool)
    from collections import defaultdict as dd
    lead_data: Dict[int, List[dict]] = dd(list)

    for (target_date, tod), history in multi.items():
        history_sorted = sorted(history, key=lambda x: x[0])  # oldest first
        final_dt, final_pct, final_file = history_sorted[-1]

        for issue_dt, issue_pct, issue_file in history_sorted[:-1]:
            lead_days = (target_date - issue_dt.date()).days
            if lead_days < 0:
                continue  # issued after the date (shouldn't happen)

            delta = abs(issue_pct - final_pct)
            final_good = final_pct <= CLOUD_THRESHOLD
            issue_good = issue_pct <= CLOUD_THRESHOLD
            flipped = final_good != issue_good

            lead_data[lead_days].append({
                "delta": delta,
                "flipped": flipped,
                "issue_pct": issue_pct,
                "final_pct": final_pct,
                "tod": tod,
                "target_date": target_date,
                "issue_file": issue_file,
                "final_file": final_file,
            })

    if not lead_data:
        print("Not enough overlapping data to compute lead-time analysis.")
        return

    # -- Summary table by lead time --------------------------------------------
    print("DRIFT vs. LEAD TIME  (compared to most-recent forecast for same slot)")
    print(f"{'-'*60}")
    print(f"{'Lead Days':>10}  {'N':>4}  {'Mean |D%|':>10}  {'Max |D%|':>9}  {'Flip Rate':>10}")
    print(f"{'-'*60}")

    rows_for_summary = []
    for lead_days in sorted(lead_data.keys()):
        entries = lead_data[lead_days]
        n = len(entries)
        deltas = [e["delta"] for e in entries]
        mean_delta = sum(deltas) / n
        max_delta = max(deltas)
        flip_rate = sum(1 for e in entries if e["flipped"]) / n * 100
        rows_for_summary.append((lead_days, n, mean_delta, max_delta, flip_rate))
        print(f"{lead_days:>10}  {n:>4}  {mean_delta:>10.1f}  {max_delta:>9}  {flip_rate:>9.0f}%")

    print(f"\n* 'Flip Rate' = % of forecasts that changed good<->bad vs final forecast")
    print(f"* Cloud threshold for good/bad = {CLOUD_THRESHOLD}%\n")

    # -- Per-TOD breakdown -----------------------------------------------------
    print(f"\n{'-'*60}")
    print("DRIFT BY TIME-OF-DAY (all lead times combined)")
    print(f"{'-'*60}")
    tod_data: Dict[str, List[dict]] = dd(list)
    for entries in lead_data.values():
        for e in entries:
            tod_data[e["tod"]].append(e)

    print(f"{'TOD':>6}  {'N':>4}  {'Mean |D%|':>10}  {'Flip Rate':>10}")
    print(f"{'-'*35}")
    for tod in ["AM", "MD", "PM"]:
        if tod not in tod_data:
            continue
        entries = tod_data[tod]
        n = len(entries)
        mean_delta = sum(e["delta"] for e in entries) / n
        flip_rate = sum(1 for e in entries if e["flipped"]) / n * 100
        print(f"{tod:>6}  {n:>4}  {mean_delta:>10.1f}  {flip_rate:>9.0f}%")

    # -- Notable changes --------------------------------------------------------
    all_entries = [e for entries in lead_data.values() for e in entries]
    big_flips = [e for e in all_entries if e["flipped"]]

    if big_flips:
        print(f"\n{'-'*60}")
        print(f"CONFIRMED FORECAST FLIPS (good<->bad, {len(big_flips)} total)")
        print(f"{'-'*60}")
        # Sort by delta descending
        big_flips.sort(key=lambda x: x["delta"], reverse=True)
        print(f"{'Date':>12}  {'TOD':>4}  {'Earlier%':>8}  {'Final%':>7}  {'|D|':>5}  Earlier File")
        print(f"{'-'*75}")
        for e in big_flips[:20]:  # top 20
            earlier_status = "GOOD" if e["issue_pct"] <= CLOUD_THRESHOLD else "BAD "
            final_status   = "GOOD" if e["final_pct"] <= CLOUD_THRESHOLD else "BAD "
            print(
                f"{str(e['target_date']):>12}  {e['tod']:>4}  "
                f"{e['issue_pct']:>6}% {earlier_status}  {e['final_pct']:>5}% {final_status}  "
                f"{e['delta']:>3}pp  {e['issue_file']}"
            )

    # -- Recommendation --------------------------------------------------------
    print(f"\n{'-'*60}")
    print("SCHEDULING RECOMMENDATION")
    print(f"{'-'*60}")

    # Find the lead time where flip rate crosses 50% (i.e., coin-flip territory)
    # and where mean delta drops below 10%
    stable_threshold_delta = 10
    stable_threshold_flip  = 20  # %

    stable_days = None
    for lead_days, n, mean_delta, max_delta, flip_rate in sorted(rows_for_summary):
        if mean_delta <= stable_threshold_delta and flip_rate <= stable_threshold_flip:
            stable_days = lead_days
            break

    if stable_days is not None:
        print(f"\n  Forecasts appear relatively stable within {stable_days} days of the target date.")
        print(f"  (mean |D| ? {stable_threshold_delta}% and flip rate ? {stable_threshold_flip}%)")
    else:
        print(f"\n  Forecasts remain volatile across all observed lead times in this dataset.")
        print(f"  Consider waiting for the most recent forecast before confirming schedules.")

    print(f"\n  Dataset spans {len(pdfs)} forecasts, {len(multi)} analyzable date/TOD slots.\n")


if __name__ == "__main__":
    main()
