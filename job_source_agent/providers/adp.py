from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse

from ..fetch_failure import project_fetch_error
from ..web import FetchError
from .base import AdapterResult, JobBoard, JobCandidate, JobQuery


_WFN_HOST = "workforcenow.adp.com"
_WFN_BOARD_PATH = "/mascsr/default/mdf/recruitment/recruitment.html"
_WFN_API_PATH = "/mascsr/default/careercenter/public/events/staffing/v1/job-requisitions"
_SRCCAR_HOST = "recruiting.adp.com"
_SRCCAR_PATHS = {"/srccar/public/rti.home", "/srccar/public/nghome.guid"}
_MYJOBS_HOST = "myjobs.adp.com"
_MYADP_HOST = "my.adp.com"
_MYJOBS_CAREER_SITE_PATH = "/public/staffing/v1/career-site/"
_MYJOBS_INVENTORY_PATH = "/myadp_prefix/mycareer/public/staffing/v1/job-requisitions/apply-custom-filters"
_UUID = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)
_CC_ID = re.compile(r"^[0-9]{8}_[0-9]{6}$")
_LOCALE = re.compile(r"^[A-Za-z]{2}_[A-Za-z]{2}$")
_POSITIVE_ID = re.compile(r"^[1-9][0-9]{0,19}$")
_ITEM_ID = re.compile(r"^[1-9][0-9]{0,19}_[1-9][0-9]{0,9}$")
_SITE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,99}$")
_PRC = re.compile(r"^RMPOD[1-9][0-9]?$", re.IGNORECASE)
_MAX_RESPONSE_CHARS = 8_000_000
_PAGE_SIZE = 20
_MAX_JOBS = 1_000
_MAX_PAGES = _MAX_JOBS // _PAGE_SIZE


class ADPAdapter:
    name = "adp"
    supports_listing = True

    def recognizes(self, url: str) -> bool:
        return _wfn_url_identity(url) is not None or _srccar_url_identity(url) is not None

    def identify_board(self, url: str) -> JobBoard | None:
        wfn = _wfn_url_identity(url)
        if wfn is not None:
            cid, cc_id, locale = wfn
            return _wfn_board(cid, cc_id, locale)
        srccar = _srccar_url_identity(url)
        if srccar is None:
            return None
        client, site, prc, requisition = srccar
        return _srccar_board(client, site, prc, requisition)

    def list_jobs(self, fetcher, board: JobBoard, query: JobQuery) -> AdapterResult:
        wfn = _wfn_board_identity(board)
        if wfn is not None:
            return _list_wfn(fetcher, board, query, *wfn)
        srccar = _srccar_board_identity(board)
        if srccar is not None:
            return _list_srccar(fetcher, board, query, *srccar)
        return _result(
            board,
            reason_code="PROVIDER_VARIANT_UNSUPPORTED",
            inventory_complete=False,
            error="invalid ADP public board locator",
        )


def _safe_url(url: str, host: str):
    try:
        parsed = urlparse(url)
        port = parsed.port
    except (TypeError, ValueError):
        return None
    if (
        parsed.scheme.casefold() != "https"
        or (parsed.hostname or "").casefold() != host
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
        or parsed.fragment
    ):
        return None
    return parsed


def _unique_query(url: str) -> dict[str, str] | None:
    pairs = parse_qsl(urlparse(url).query, keep_blank_values=True)
    result: dict[str, str] = {}
    for key, value in pairs:
        folded = key.casefold()
        if folded in result:
            return None
        result[folded] = value
    return result


def _wfn_url_identity(url: str) -> tuple[str, str, str] | None:
    parsed = _safe_url(url, _WFN_HOST)
    if parsed is None or parsed.path.casefold() != _WFN_BOARD_PATH:
        return None
    query = _unique_query(url)
    if query is None or not set(query).issubset(
        {"cid", "ccid", "type", "lang", "selectedmenukey", "jobid", "source"}
    ):
        return None
    cid = query.get("cid", "").casefold()
    cc_id = query.get("ccid", "")
    locale = query.get("lang", "en_US")
    if (
        not _UUID.fullmatch(cid)
        or not _CC_ID.fullmatch(cc_id)
        or not _LOCALE.fullmatch(locale)
        or (query.get("type") not in {None, "MP"})
        or (query.get("selectedmenukey") not in {None, "CurrentOpenings"})
        or (query.get("jobid") is not None and not _ITEM_ID.fullmatch(query["jobid"]))
        or (query.get("source") is not None and not _SITE_NAME.fullmatch(query["source"]))
    ):
        return None
    return cid, cc_id, locale


def _wfn_board_url(cid: str, cc_id: str, locale: str) -> str:
    return f"https://{_WFN_HOST}{_WFN_BOARD_PATH}?" + urlencode(
        {
            "cid": cid,
            "ccId": cc_id,
            "type": "MP",
            "lang": locale,
            "selectedMenuKey": "CurrentOpenings",
        }
    )


def _wfn_identifier(cid: str, cc_id: str, locale: str) -> str:
    return f"wfn|{cid}|{cc_id}|{locale}"


def _wfn_board(cid: str, cc_id: str, locale: str) -> JobBoard:
    return JobBoard(
        url=_wfn_board_url(cid, cc_id, locale),
        provider="adp",
        identifier=_wfn_identifier(cid, cc_id, locale),
        replay_safe=True,
    )


def _wfn_board_identity(board: JobBoard) -> tuple[str, str, str] | None:
    if board.provider != "adp" or not isinstance(board.identifier, str):
        return None
    identity = _wfn_url_identity(board.url)
    if identity is None or board.identifier != _wfn_identifier(*identity):
        return None
    return identity if board.url == _wfn_board_url(*identity) else None


def _wfn_inventory_url(cid: str, skip: int) -> str:
    return f"https://{_WFN_HOST}{_WFN_API_PATH}?" + urlencode(
        {"cid": cid, "$skip": skip, "$top": _PAGE_SIZE}
    )


def _wfn_detail_url(
    cid: str, cc_id: str, locale: str, item_id: str, source: str = "CC3"
) -> str:
    return f"https://{_WFN_HOST}{_WFN_BOARD_PATH}?" + urlencode(
        {
            "cid": cid,
            "ccId": cc_id,
            "type": "MP",
            "lang": locale,
            "jobId": item_id,
            "source": source,
        }
    )


def _same_wfn_inventory_url(response_url: str, requested_url: str) -> bool:
    parsed = _safe_url(response_url, _WFN_HOST)
    expected = _safe_url(requested_url, _WFN_HOST)
    if parsed is None or expected is None or parsed.path.casefold() != _WFN_API_PATH:
        return False
    return _unique_query(response_url) == _unique_query(requested_url)


def _list_wfn(
    fetcher,
    board: JobBoard,
    query: JobQuery,
    cid: str,
    cc_id: str,
    locale: str,
) -> AdapterResult:
    candidates: list[JobCandidate] = []
    seen: set[str] = set()
    api_urls: list[str] = []
    expected_total: int | None = None
    skip = 1
    for _page_number in range(_MAX_PAGES):
        inventory_url = _wfn_inventory_url(cid, skip)
        api_urls.append(inventory_url)
        try:
            page = fetcher.fetch(
                inventory_url,
                headers={"Accept": "application/json", "Referer": board.url},
            )
        except (FetchError, OSError, TimeoutError) as error:
            return _fetch_failure(board, error, api_urls=api_urls)
        if not _same_wfn_inventory_url(page.final_url or page.url, inventory_url):
            return _result(
                board,
                reason_code="PROVIDER_VARIANT_UNSUPPORTED",
                inventory_complete=False,
                error="ADP WFN inventory redirected outside the declared tenant endpoint",
                api_urls=api_urls,
                rejected_final_url=page.final_url or page.url,
            )
        parsed = _parse_wfn_inventory(page.html)
        if parsed is None:
            return _result(
                board,
                reason_code="INVALID_STRUCTURED_DATA",
                inventory_complete=False,
                error="invalid ADP WFN public inventory",
                api_urls=api_urls,
                response_source=page.source,
            )
        records, total, start = parsed
        if start != skip or (expected_total is not None and total != expected_total):
            return _invalid_wfn(board, api_urls, page.source, "ADP WFN pagination identity changed")
        expected_total = total
        if total > _MAX_JOBS:
            return _result(
                board,
                candidates=candidates,
                reason_code="OPENING_DISCOVERY_INCOMPLETE",
                inventory_complete=False,
                error="ADP WFN inventory exceeded the pagination limit",
                api_urls=api_urls,
                total=total,
                pagination_limit=_MAX_JOBS,
            )
        for record in records:
            candidate = _wfn_candidate(record, cid, cc_id, locale)
            job_id = candidate.raw.get("job_id") if candidate is not None else None
            if candidate is None or job_id in seen:
                return _invalid_wfn(
                    board, api_urls, page.source,
                    "ADP WFN inventory contained an invalid or duplicate opening",
                )
            seen.add(job_id)
            candidates.append(candidate)
        if len(candidates) == total:
            break
        if not records or len(candidates) > total:
            return _invalid_wfn(board, api_urls, page.source, "ADP WFN pagination did not cover the declared inventory")
        skip += len(records)
    else:
        return _result(
            board,
            candidates=candidates,
            reason_code="OPENING_DISCOVERY_INCOMPLETE",
            inventory_complete=False,
            error="ADP WFN pagination limit reached",
            api_urls=api_urls,
            total=expected_total,
            pagination_limit=_MAX_JOBS,
        )

    target = _normalized(query.title)
    return _result(
        board,
        candidates=candidates,
        reason_code=None if candidates else "EMPTY_PROVIDER_RESPONSE",
        inventory_complete=True,
        variant="workforce_now_public_requisitions",
        identity={"cid": cid, "career_center_id": cc_id, "locale": locale},
        api_urls=api_urls,
        records_seen=len(candidates),
        total=expected_total or 0,
        pages_fetched=len(api_urls),
        pagination_limit=_MAX_JOBS,
        exact_title_found=bool(
            target and any(_normalized(candidate.title) == target for candidate in candidates)
        ),
    )


def _parse_wfn_inventory(raw: str) -> tuple[list[Any], int, int] | None:
    if not isinstance(raw, str) or len(raw) > _MAX_RESPONSE_CHARS:
        return None
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    if not isinstance(payload, dict) or not isinstance(payload.get("jobRequisitions"), list):
        return None
    meta = payload.get("meta")
    if not isinstance(meta, dict):
        return None
    total = meta.get("totalNumber")
    start = meta.get("startSequence")
    if (
        isinstance(total, bool)
        or not isinstance(total, int)
        or total < 0
        or isinstance(start, bool)
        or not isinstance(start, int)
        or start < 1
    ):
        return None
    records = payload["jobRequisitions"]
    if len(records) > _PAGE_SIZE or start - 1 + len(records) > total:
        return None
    return records, total, start


def _wfn_candidate(
    record: Any, cid: str, cc_id: str, locale: str
) -> JobCandidate | None:
    if not isinstance(record, dict):
        return None
    item_id = record.get("itemID")
    title = record.get("requisitionTitle")
    if (
        not isinstance(item_id, str)
        or not _ITEM_ID.fullmatch(item_id)
        or not isinstance(title, str)
        or not title.strip()
        or len(title) > 500
    ):
        return None
    locations = record.get("requisitionLocations", [])
    if not isinstance(locations, list):
        return None
    location_names: list[str] = []
    for location in locations:
        if not isinstance(location, dict):
            return None
        name_code = location.get("nameCode")
        short_name = name_code.get("shortName") if isinstance(name_code, dict) else None
        if not isinstance(short_name, str) or not short_name.strip() or len(short_name) > 500:
            return None
        location_names.append(short_name.strip())
    return JobCandidate(
        title=title.strip(),
        url=_wfn_detail_url(cid, cc_id, locale, item_id),
        provider="adp",
        location="; ".join(dict.fromkeys(location_names)) or None,
        raw={"variant": "wfn", "job_id": item_id, "cid": cid, "cc_id": cc_id},
    )


def _srccar_url_identity(url: str) -> tuple[str, str, str | None, str | None] | None:
    parsed = _safe_url(url, _SRCCAR_HOST)
    if parsed is None or parsed.path.casefold() not in _SRCCAR_PATHS:
        return None
    query = _unique_query(url)
    if query is None or not set(query).issubset({"c", "d", "prc", "r", "rb", "_frompublish"}):
        return None
    client = query.get("c", "")
    site = query.get("d", "")
    prc = query.get("prc")
    requisition = query.get("r")
    if (
        not _POSITIVE_ID.fullmatch(client)
        or not _SITE_NAME.fullmatch(site)
        or (prc is not None and not _PRC.fullmatch(prc))
        or (requisition is not None and not _POSITIVE_ID.fullmatch(requisition))
        or (query.get("rb") is not None and not _SITE_NAME.fullmatch(query["rb"]))
        or (query.get("_frompublish") not in {None, "true"})
    ):
        return None
    return client, site, prc.upper() if prc else None, requisition


def _srccar_url(client: str, site: str, prc: str | None, requisition: str | None) -> str:
    query: dict[str, str] = {"c": client, "d": site}
    if prc is not None:
        query["prc"] = prc
    if requisition is not None:
        query["r"] = requisition
    return f"https://{_SRCCAR_HOST}/srccar/public/nghome.guid?" + urlencode(query)


def _srccar_identifier(client: str, site: str, prc: str | None, requisition: str | None) -> str:
    return "|".join(("srccar", client, site.casefold(), prc or "", requisition or ""))


def _srccar_board(client: str, site: str, prc: str | None, requisition: str | None) -> JobBoard:
    return JobBoard(
        url=_srccar_url(client, site, prc, requisition),
        provider="adp",
        identifier=_srccar_identifier(client, site, prc, requisition),
        replay_safe=True,
    )


def _srccar_board_identity(board: JobBoard) -> tuple[str, str, str | None, str | None] | None:
    if board.provider != "adp" or not isinstance(board.identifier, str):
        return None
    identity = _srccar_url_identity(board.url)
    if identity is None or board.identifier != _srccar_identifier(*identity):
        return None
    return identity if board.url == _srccar_url(*identity) else None


def _list_srccar(
    fetcher,
    board: JobBoard,
    query: JobQuery,
    client: str,
    site: str,
    prc: str | None,
    requisition: str | None,
) -> AdapterResult:
    if requisition is None:
        return _list_srccar_myjobs(fetcher, board, query, client, site)
    try:
        page = fetcher.fetch(board.url)
    except (FetchError, OSError, TimeoutError) as error:
        return _fetch_failure(board, error, board_urls=[board.url])
    if _srccar_url_identity(page.final_url or page.url) != (client, site, prc, requisition):
        return _result(
            board,
            reason_code="PROVIDER_VARIANT_UNSUPPORTED",
            inventory_complete=False,
            error="ADP SRCCAR detail redirected outside the declared tenant",
            board_urls=[board.url],
            rejected_final_url=page.final_url or page.url,
        )
    candidate = _srccar_json_ld_candidate(page.html, board.url, requisition)
    if candidate is None:
        return _result(
            board,
            reason_code="INVALID_STRUCTURED_DATA",
            inventory_complete=False,
            error="missing or contradictory ADP SRCCAR JobPosting evidence",
            board_urls=[board.url],
            response_source=page.source,
        )
    return _result(
        board,
        candidates=[candidate],
        inventory_complete=True,
        inventory_scope="single_opening",
        variant="srccar_direct_job_json_ld",
        identity={"client": client, "site": site, "prc": prc, "requisition": requisition},
        board_urls=[board.url],
        response_source=page.source,
        records_seen=1,
        exact_title_found=_normalized(candidate.title) == _normalized(query.title),
    )


def _list_srccar_myjobs(
    fetcher, board: JobBoard, query: JobQuery, client: str, site: str
) -> AdapterResult:
    try:
        landing = fetcher.fetch(board.url)
    except (FetchError, OSError, TimeoutError) as error:
        return _fetch_failure(board, error, board_urls=[board.url])
    slug = _srccar_myjobs_slug(landing.final_url or landing.url, client, site)
    if slug is None:
        return _result(
            board,
            reason_code="PROVIDER_VARIANT_UNSUPPORTED",
            inventory_complete=False,
            error="ADP SRCCAR board redirected outside its declared public tenant",
            board_urls=[board.url],
            rejected_final_url=landing.final_url or landing.url,
        )

    config_url = _myjobs_config_url(slug)
    try:
        config_page = fetcher.fetch(config_url, headers={"Accept": "application/json"})
    except (FetchError, OSError, TimeoutError) as error:
        return _fetch_failure(board, error, board_urls=[board.url], api_urls=[config_url])
    if (config_page.final_url or config_page.url) != config_url:
        return _result(
            board,
            reason_code="PROVIDER_VARIANT_UNSUPPORTED",
            inventory_complete=False,
            error="ADP MyJobs career-site configuration redirected",
            board_urls=[board.url],
            api_urls=[config_url],
            rejected_final_url=config_page.final_url or config_page.url,
        )
    config = _myjobs_config(config_page.html, slug, site)
    if config is None:
        return _result(
            board,
            reason_code="INVALID_STRUCTURED_DATA",
            inventory_complete=False,
            error="invalid or cross-tenant ADP MyJobs career-site configuration",
            board_urls=[board.url],
            api_urls=[config_url],
            response_source=config_page.source,
        )
    token, orgoid = config

    candidates: list[JobCandidate] = []
    seen: set[str] = set()
    api_urls = [config_url]
    expected_total: int | None = None
    for skip in range(0, _MAX_JOBS, _PAGE_SIZE):
        inventory_url = _myjobs_inventory_url(skip)
        api_urls.append(inventory_url)
        try:
            page = fetcher.fetch(
                inventory_url,
                headers={
                    "Accept": "application/json",
                    "Origin": f"https://{_MYJOBS_HOST}",
                    "Referer": f"https://{_MYJOBS_HOST}/{slug}",
                    "myJobsToken": token,
                    "orgoid": orgoid,
                    "rolecode": "manager",
                },
            )
        except (FetchError, OSError, TimeoutError) as error:
            return _fetch_failure(board, error, board_urls=[board.url], api_urls=api_urls)
        if (page.final_url or page.url) != inventory_url:
            return _result(
                board,
                reason_code="PROVIDER_VARIANT_UNSUPPORTED",
                inventory_complete=False,
                error="ADP MyJobs inventory redirected outside its declared endpoint",
                board_urls=[board.url],
                api_urls=api_urls,
                rejected_final_url=page.final_url or page.url,
            )
        parsed = _parse_myjobs_inventory(page.html)
        if parsed is None:
            return _invalid_myjobs(board, api_urls, page.source, "invalid ADP MyJobs public inventory")
        records, total = parsed
        if expected_total is not None and total != expected_total:
            return _invalid_myjobs(board, api_urls, page.source, "ADP MyJobs inventory total changed")
        expected_total = total
        if total > _MAX_JOBS:
            return _result(
                board,
                candidates=candidates,
                reason_code="OPENING_DISCOVERY_INCOMPLETE",
                inventory_complete=False,
                error="ADP MyJobs inventory exceeded the pagination limit",
                board_urls=[board.url],
                api_urls=api_urls,
                total=total,
                pagination_limit=_MAX_JOBS,
            )
        for record in records:
            candidate = _myjobs_candidate(record, slug)
            job_id = candidate.raw.get("job_id") if candidate is not None else None
            if candidate is None or job_id in seen:
                return _invalid_myjobs(
                    board, api_urls, page.source,
                    "ADP MyJobs inventory contained an invalid or duplicate opening",
                )
            seen.add(job_id)
            candidates.append(candidate)
        if len(candidates) == total:
            break
        if not records or len(records) != _PAGE_SIZE or len(candidates) > total:
            return _invalid_myjobs(
                board, api_urls, page.source,
                "ADP MyJobs pagination did not cover the declared inventory",
            )
    else:
        return _result(
            board,
            candidates=candidates,
            reason_code="OPENING_DISCOVERY_INCOMPLETE",
            inventory_complete=False,
            error="ADP MyJobs pagination limit reached",
            board_urls=[board.url],
            api_urls=api_urls,
            total=expected_total,
            pagination_limit=_MAX_JOBS,
        )
    target = _normalized(query.title)
    return _result(
        board,
        candidates=candidates,
        reason_code=None if candidates else "EMPTY_PROVIDER_RESPONSE",
        inventory_complete=True,
        variant="srccar_myjobs_public_requisitions",
        identity={"client": client, "site": site, "myjobs_slug": slug},
        board_urls=[board.url],
        api_urls=api_urls,
        records_seen=len(candidates),
        total=expected_total or 0,
        pages_fetched=len(api_urls) - 1,
        pagination_limit=_MAX_JOBS,
        exact_title_found=bool(
            target and any(_normalized(candidate.title) == target for candidate in candidates)
        ),
    )


def _srccar_myjobs_slug(url: str, client: str, site: str) -> str | None:
    parsed = _safe_url(url, _MYJOBS_HOST)
    if parsed is None:
        return None
    path_parts = [part for part in parsed.path.split("/") if part]
    query = _unique_query(url)
    if (
        len(path_parts) != 1
        or not _SITE_NAME.fullmatch(path_parts[0])
        or query is None
        or not set(query).issubset({"c", "d", "sor", "__tx_annotation"})
        or query.get("c") != client
        or query.get("d", "").casefold() != site.casefold()
        or query.get("sor") != "adprm"
        or query.get("__tx_annotation") not in {None, "false"}
    ):
        return None
    return path_parts[0].casefold()


def _myjobs_config_url(slug: str) -> str:
    return f"https://{_MYJOBS_HOST}{_MYJOBS_CAREER_SITE_PATH}{slug}"


def _myjobs_inventory_url(skip: int) -> str:
    return f"https://{_MYADP_HOST}{_MYJOBS_INVENTORY_PATH}?" + urlencode(
        {
            "$orderby": "postingDate desc",
            "$select": "reqId,jobTitle,publishedJobTitle,requisitionLocations",
            "$top": _PAGE_SIZE,
            "$skip": skip,
            "tz": "America/New_York",
        }
    )


def _myjobs_config(raw: str, slug: str, site: str) -> tuple[str, str] | None:
    if not isinstance(raw, str) or len(raw) > _MAX_RESPONSE_CHARS:
        return None
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    settings = payload.get("settings")
    token = payload.get("myJobsToken")
    orgoid = payload.get("orgoid")
    if (
        not isinstance(settings, dict)
        or not isinstance(payload.get("domain"), str)
        or payload["domain"].casefold() != slug
        or not isinstance(settings.get("externalDomain"), str)
        or settings["externalDomain"].casefold() != site.casefold()
        or not isinstance(settings.get("externalId"), str)
        or not _POSITIVE_ID.fullmatch(settings["externalId"])
        or not isinstance(token, str)
        or not 20 <= len(token) <= 8_000
        or not isinstance(orgoid, str)
        or not re.fullmatch(r"[A-Za-z0-9_-]{8,200}", orgoid)
    ):
        return None
    return token, orgoid


def _parse_myjobs_inventory(raw: str) -> tuple[list[Any], int] | None:
    if not isinstance(raw, str) or len(raw) > _MAX_RESPONSE_CHARS:
        return None
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    if not isinstance(payload, dict) or not isinstance(payload.get("jobRequisitions"), list):
        return None
    total = payload.get("count")
    records = payload["jobRequisitions"]
    if (
        isinstance(total, bool)
        or not isinstance(total, int)
        or total < 0
        or len(records) > _PAGE_SIZE
    ):
        return None
    return records, total


def _myjobs_candidate(record: Any, slug: str) -> JobCandidate | None:
    if not isinstance(record, dict):
        return None
    job_id = record.get("reqId")
    title = record.get("publishedJobTitle") or record.get("jobTitle")
    if (
        not isinstance(job_id, str)
        or not _POSITIVE_ID.fullmatch(job_id)
        or not isinstance(title, str)
        or not title.strip()
        or len(title) > 500
    ):
        return None
    location = _myjobs_location(record.get("requisitionLocations"))
    if record.get("requisitionLocations") is not None and location is None:
        return None
    return JobCandidate(
        title=title.strip(),
        url=f"https://{_MYJOBS_HOST}/{slug}/cx/job-details?" + urlencode({"reqId": job_id}),
        provider="adp",
        location=location,
        raw={"variant": "srccar_myjobs", "job_id": job_id, "myjobs_slug": slug},
    )


def _myjobs_location(value: Any) -> str | None:
    if not isinstance(value, list):
        return None
    locations: list[str] = []
    for item in value:
        if not isinstance(item, dict):
            return None
        address = item.get("address")
        if not isinstance(address, dict):
            return None
        region = address.get("countrySubdivisionLevel1")
        region_value = region.get("codeValue") if isinstance(region, dict) else None
        parts = [address.get("cityName"), region_value, address.get("countryCode")]
        text = ", ".join(part.strip() for part in parts if isinstance(part, str) and part.strip())
        if not text:
            return None
        locations.append(text)
    return "; ".join(dict.fromkeys(locations)) or None


def _invalid_myjobs(board: JobBoard, api_urls: list[str], source: str, error: str) -> AdapterResult:
    return _result(
        board,
        reason_code="INVALID_STRUCTURED_DATA",
        inventory_complete=False,
        error=error,
        api_urls=api_urls,
        response_source=source,
    )


def _srccar_json_ld_candidate(html: str, url: str, requisition: str) -> JobCandidate | None:
    if not isinstance(html, str) or len(html) > _MAX_RESPONSE_CHARS:
        return None
    scripts = re.findall(
        r"<script\b[^>]*type=[\"']application/ld\+json[\"'][^>]*>(.*?)</script>",
        html,
        flags=re.IGNORECASE | re.DOTALL,
    )
    postings: list[dict[str, Any]] = []
    for raw in scripts:
        try:
            value = json.loads(raw)
        except (json.JSONDecodeError, TypeError, ValueError):
            continue
        values = value if isinstance(value, list) else [value]
        postings.extend(
            item for item in values
            if isinstance(item, dict) and item.get("@type") == "JobPosting"
        )
    if len(postings) != 1:
        return None
    posting = postings[0]
    title = posting.get("title")
    identifier = posting.get("identifier")
    identifier_value = identifier.get("value") if isinstance(identifier, dict) else identifier
    if (
        not isinstance(title, str)
        or not title.strip()
        or len(title) > 500
        or str(identifier_value) != requisition
    ):
        return None
    location = _json_ld_location(posting.get("jobLocation"))
    return JobCandidate(
        title=title.strip(),
        url=url,
        provider="adp",
        location=location,
        raw={"variant": "srccar", "job_id": requisition},
    )


def _json_ld_location(value: object) -> str | None:
    values = value if isinstance(value, list) else [value]
    locations: list[str] = []
    for item in values:
        if not isinstance(item, dict):
            continue
        address = item.get("address")
        if not isinstance(address, dict):
            continue
        parts = [address.get(key) for key in ("addressLocality", "addressRegion", "addressCountry")]
        text = ", ".join(part.strip() for part in parts if isinstance(part, str) and part.strip())
        if text:
            locations.append(text)
    return "; ".join(dict.fromkeys(locations)) or None


def _invalid_wfn(board: JobBoard, api_urls: list[str], source: str, error: str) -> AdapterResult:
    return _result(
        board,
        reason_code="INVALID_STRUCTURED_DATA",
        inventory_complete=False,
        error=error,
        api_urls=api_urls,
        response_source=source,
    )


def _fetch_failure(board: JobBoard, error: Exception, **trace: Any) -> AdapterResult:
    if isinstance(error, FetchError):
        projection = project_fetch_error(error)
        reason_code = projection["reason_code"]
        retryable = projection["retryable"]
    else:
        reason_code = "PROVIDER_FETCH_FAILED"
        retryable = True
    return _result(
        board,
        reason_code=reason_code,
        retryable=retryable,
        inventory_complete=False,
        error=str(error),
        **trace,
    )


def _normalized(value: str | None) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", value.casefold())) if value else ""


def _result(
    board: JobBoard,
    *,
    candidates: list[JobCandidate] | None = None,
    reason_code: str | None = None,
    retryable: bool = False,
    inventory_complete: bool,
    inventory_scope: str = "full",
    error: str | None = None,
    **trace: Any,
) -> AdapterResult:
    trace.update(
        {
            "adapter": "adp",
            "inventory_scope": inventory_scope,
            "inventory_complete": inventory_complete,
        }
    )
    if error is not None:
        trace["error"] = error
    return AdapterResult(
        provider="adp",
        board=board,
        candidates=candidates or [],
        reason_code=reason_code,
        retryable=retryable,
        inventory_scope=inventory_scope,
        inventory_complete=inventory_complete,
        trace=trace,
    )


ADAPTER = ADPAdapter()
