"""The landing zone: release files kept exactly as published, in a Volume, one folder per release.

  /Volumes/<catalog>/<schema>/<volume>/<landing>/<yyyymmdd>/<file>.zip     the zip as downloaded
  /Volumes/<catalog>/<schema>/<volume>/<landing>/<yyyymmdd>/<member>       its files, beside it (folders flattened)

Nothing there is edited or deleted. Landing the same release again is a no-op; a file re-published under the same
name with different content is refused (LandingConflict), because the landing zone is the record of what was
published. Every file landed gets a row in quote_ai_ops.ingestion_file with its size, MD5 and SHA-256.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import zipfile
from datetime import date, timedelta
from pathlib import Path, PurePosixPath
from typing import Callable

from quote_ai.ingest.config import Source
from quote_ai.ingest.http import Http
from quote_ai.ingest.lineage import load_id_for, release_date_from_path, release_tag
from quote_ai.ingest.log import FileRecord, LogWriter, utcnow

CHUNK = 1 << 20


class LandingConflict(RuntimeError):
    """A file already in the landing zone was published again with different content."""


def landing_dir(root: Path, source: Source, d: date) -> Path:
    return root / source.landing / release_tag(d)


def _digest(path: Path) -> tuple[int, str, str]:
    md5, sha, size = hashlib.md5(), hashlib.sha256(), 0
    with open(path, "rb") as f:
        while chunk := f.read(CHUNK):
            md5.update(chunk)
            sha.update(chunk)
            size += len(chunk)
    return size, md5.hexdigest(), sha.hexdigest()


def _rows(path: Path) -> int | None:
    """Data rows in a delimited text file (lines minus the header); None for other formats."""
    if path.suffix.lower() not in (".txt", ".csv"):
        return None
    with open(path, "rb") as f:
        lines = sum(1 for _ in f)
    return max(lines - 1, 0)


def _safe_member(name: str) -> str | None:
    """The member's file name, flattened; None for folders. Refuses absolute paths and '..' (zip slip)."""
    p = PurePosixPath(name.replace("\\", "/"))
    if p.is_absolute() or ".." in p.parts:
        raise LandingConflict(f"unsafe path in zip: {name!r}")
    return None if name.endswith("/") else p.name


def _record(source: Source, d: date, path: Path, url: str | None, job_run_id: str | None) -> FileRecord:
    size, md5, sha = _digest(path)
    return FileRecord(load_id=load_id_for(source.name, release_tag(d)), source=source.name,
                      kind=source.download.kind if source.download else (source.generator or "manual"),
                      path=str(path).replace("\\", "/"), source_url=url, release_date=d, size_bytes=size, md5=md5,
                      sha256=sha, downloaded_at=utcnow(), row_count=_rows(path), job_run_id=job_run_id)


def land_release(http: Http, source: Source, d: date, root: Path, log: LogWriter,
                 job_run_id: str | None = None) -> list[FileRecord] | None:
    """Download and unzip one release into the landing zone. None if there is no release that day (404)."""
    url = source.download.url_for(d)
    resp = http.get(url, stream=True)
    if resp is None:
        return None
    folder = landing_dir(root, source, d)
    folder.mkdir(parents=True, exist_ok=True)
    zip_path = folder / url.rsplit("/", 1)[-1]
    part = zip_path.with_name(zip_path.name + ".part")

    with open(part, "wb") as f:
        for chunk in resp.iter_content(CHUNK):
            f.write(chunk)

    if zip_path.exists():
        if _digest(zip_path)[2] != _digest(part)[2]:
            part.unlink()
            raise LandingConflict(f"{zip_path} was published again with different content; the landing zone keeps "
                                  "the original. Investigate before loading it.")
        part.unlink()
    else:
        os.replace(part, zip_path)

    records = [_record(source, d, zip_path, url, job_run_id)]
    with zipfile.ZipFile(zip_path) as z:
        for info in z.infolist():
            name = _safe_member(info.filename)
            if not name:
                continue
            target = folder / name
            if not target.exists():
                tmp = target.with_name(name + ".part")
                with z.open(info) as src, open(tmp, "wb") as dst:
                    shutil.copyfileobj(src, dst, CHUNK)
                os.replace(tmp, target)
            records.append(_record(source, d, target, f"{url}!{info.filename}", job_run_id))
    log.add_files(records)
    return records


def days_to_fetch(source: Source, log: LogWriter, today: date) -> list[date]:
    """Daily releases not landed yet: from the day after the last one landed (or lookback_days ago) to today."""
    landed = [f.release_date for f in log.files(source.name) if f.release_date]
    start = max(landed) + timedelta(days=1) if landed else today - timedelta(days=source.download.lookback_days)
    start = max(start, today - timedelta(days=source.download.lookback_days))
    return [start + timedelta(days=i) for i in range((today - start).days + 1)]


def record_dropped_files(source: Source, root: Path, log: LogWriter, job_run_id: str | None = None) -> list[FileRecord]:
    """Files dropped by hand into <landing>/<yyyymmdd>/: nothing to fetch, but each new file is recorded in the
    manifest, so its load ID belongs to a logged run like any other. Files already in the manifest are skipped."""
    base = root / source.landing
    if not base.exists():
        return []
    known = {f.path for f in log.files(source.name)}
    out = []
    for folder in sorted(p for p in base.iterdir() if p.is_dir()):
        d = release_date_from_path(str(folder) + "/x")
        if d is None or release_tag(d) != folder.name:
            continue                                  # only <yyyymmdd> folders are releases
        for f in sorted(p for p in folder.iterdir() if p.is_file() and not p.name.endswith(".part")):
            if str(f).replace("\\", "/") not in known:
                out.append(_record(source, d, f, None, job_run_id))
    log.add_files(out)
    return out


def land_source(http: Http, source: Source, root: Path, log: LogWriter, today: date,
                release_date: date | None = None, job_run_id: str | None = None,
                generators: dict[str, Callable] | None = None) -> list[FileRecord]:
    """Land an explicit or latest available full release, missing daily releases, or generated files.

    Full-release discovery searches backward through the configured lookback window.
    """
    out: list[FileRecord] = []
    if source.generator:
        gen = (generators or {}).get(source.generator)
        if gen is None:
            raise KeyError(f"{source.name}: generator {source.generator!r} not available")
        d = release_date or today
        folder = landing_dir(root, source, d)
        folder.mkdir(parents=True, exist_ok=True)
        paths = gen(folder, d)
        out = [_record(source, d, Path(p), None, job_run_id) for p in paths]
        log.add_files(out)
        return out
    if source.download is None:
        return record_dropped_files(source, root, log, job_run_id)
    if source.download.kind == "full":
        if release_date is not None:
            records = land_release(http, source, release_date, root, log, job_run_id)
            if records is None:
                raise ValueError(f"{source.name}: no full release published for release_date={release_date}")
            return records
        for offset in range(source.download.lookback_days + 1):
            candidate = today - timedelta(days=offset)
            records = land_release(http, source, candidate, root, log, job_run_id)
            if records is not None:
                return records
        raise ValueError(
            f"{source.name}: no full release found in the {source.download.lookback_days}-day lookback "
            "window; set the job's release_date parameter to a published release date (YYYY-MM-DD)"
        )
    for d in days_to_fetch(source, log, today):
        out += land_release(http, source, d, root, log, job_run_id) or []
    return out
