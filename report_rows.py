import asyncio
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo


VIETNAM_TZ = ZoneInfo("Asia/Ho_Chi_Minh")
_recovery_lock = asyncio.Lock()


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


def _poll_creation_timestamp(row):
    """Return a creation time only for the metadata rows written with a poll."""
    if len(row) < 7:
        return None
    if not str(row[0]).strip() or not str(row[1]).strip():
        return None
    # Vote rows populate the Telegram user id/name fields.  Creation rows leave
    # all of C, D and F empty and carry the serialized options in G.
    if any(str(row[index]).strip() for index in (2, 3, 5)):
        return None
    if not str(row[6]).strip():
        return None
    try:
        parsed = datetime.fromisoformat(str(row[4]).strip().replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=VIETNAM_TZ)
    return parsed.astimezone(VIETNAM_TZ)


async def recover_missing_report_occurrences(run_sheets, spreadsheet_id, groups):
    """Backfill missing Репорты occurrences in one process-local critical section."""
    # Manual updates and the scheduler can enter recovery concurrently.  Take
    # the lock before either read so a waiter always observes all rows appended
    # by the previous recovery rather than acting on a stale snapshot.
    async with _recovery_lock:
        return await _recover_missing_report_occurrences(
            run_sheets, spreadsheet_id, groups
        )


async def _recover_missing_report_occurrences(run_sheets, spreadsheet_id, groups):
    report_rows = await run_sheets(
        "recovery Репорты lookup",
        lambda service: service.values().get(
            spreadsheetId=spreadsheet_id, range="Репорты!A2:G"
        ).execute().get("values", []),
    )
    survey_rows = await run_sheets(
        "recovery Опросы lookup",
        lambda service: service.values().get(
            spreadsheetId=spreadsheet_id, range="Опросы!A2:G"
        ).execute().get("values", []),
    )

    groups_by_name = {group["name"]: group for group in groups}
    candidates = {}
    for position, row in enumerate(survey_rows):
        created_at = _poll_creation_timestamp(row)
        group_name = str(row[1]).strip() if len(row) > 1 else ""
        group = groups_by_name.get(group_name)
        if created_at is None or group is None:
            continue
        lesson_date = (
            created_at.date() + timedelta(days=group.get("check_day_offset", 0))
        ).isoformat()
        occurrence = (group_name, lesson_date)
        candidate = (created_at, position, str(row[0]).strip(), group)
        if occurrence not in candidates or candidate[:2] > candidates[occurrence][:2]:
            candidates[occurrence] = candidate

    existing = {
        (str(row[1]), str(row[6])[:10])
        for row in report_rows
        if len(row) >= 7
    }
    recovered = []
    for (group_name, lesson_date), (_, _, poll_id, group) in candidates.items():
        if (group_name, lesson_date) in existing:
            continue
        row = [
            poll_id,
            group_name,
            "",
            "",
            str(group["group_id"]),
            str(group["thread_id"]) if group.get("thread_id") is not None else "",
            lesson_date,
        ]
        await run_sheets(
            "Репорты recovery persistence",
            lambda service, row=row: service.values().append(
                spreadsheetId=spreadsheet_id,
                range="Репорты!A1",
                valueInputOption="USER_ENTERED",
                insertDataOption="INSERT_ROWS",
                body={"values": [row]},
            ).execute(),
        )
        report_rows.append(row)
        existing.add((group_name, lesson_date))
        recovered.append(row)
        logging.info(
            "Recovered Репорты occurrence: %s/%s poll_id=%s",
            group_name, lesson_date, poll_id,
        )
    return report_rows
