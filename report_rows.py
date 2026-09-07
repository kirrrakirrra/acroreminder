def canonical_report_rows(rows, report_date=None):
    """Return the last Google Sheets row for each group/lesson-date pair."""
    canonical = {}
    for row in rows:
        if len(row) < 7:
            continue
        row_date = str(row[6])[:10]
        if report_date is not None and row_date != report_date:
            continue
        canonical[(row[1], row_date)] = row
    return list(canonical.values())
