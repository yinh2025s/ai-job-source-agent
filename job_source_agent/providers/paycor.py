from __future__ import annotations

from html.parser import HTMLParser
import ipaddress
import re
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse

from ..web import FetchError, Page
from .base import AdapterResult, JobBoard, JobCandidate, JobQuery


_HOST = "recruitingbypaycor.com"
_CLIENT_ID = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9_-]{14,126}[A-Za-z0-9])$")
_JOB_ID = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9_-]{14,126}[A-Za-z0-9])$")
_FORM_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
_LANG = re.compile(r"^[A-Za-z]{2}(?:-[A-Za-z]{2})?$")
_SAFE_OPTIONAL_VALUE = re.compile(r"^[A-Za-z0-9 _.,@|+-]{0,128}$")
_PATHS = {
    "/career/iframe.action": "iframe",
    "/career/CareerHome.action": "home",
    "/career/JobIntroduction.action": "detail",
}
_SEARCH_PATHS = {"/career/CareerHome.action", "/career/CareerHomeSearch.action"}
_HOME_QUERY_KEYS = {"clientId", "specialization", "source", "lang", "parentUrl"}
_DETAIL_QUERY_KEYS = {"clientId", "id", "source", "lang", "fromAggregate"}
_SEARCH_QUERY_KEYS = {
    "clientId",
    "keyword",
    "location",
    "stateName",
    "cityName",
    "departmentId",
    "specialization",
    "source",
    "lang",
    "internal",
    "showAllJobs",
    "sortField",
    "descend",
    "sortFieldSecond",
    "descendSecond",
    "clickField",
    "fromAggregate",
    "parentUrl",
}
_MAX_FORM_CONTROLS = 24
_VOID_TAGS = {
    "area",
    "base",
    "br",
    "col",
    "embed",
    "hr",
    "img",
    "input",
    "link",
    "meta",
    "param",
    "source",
    "track",
    "wbr",
}


class PaycorAdapter:
    name = "paycor"
    supports_listing = True

    def recognizes(self, url: str) -> bool:
        return _url_identity(url) is not None

    def identify_board(self, url: str) -> JobBoard | None:
        identity = _url_identity(url)
        if identity is None:
            return None
        host, client_id, kind = identity
        parent_url = _home_parent_url(url) if kind == "home" else None
        return JobBoard(
            url=_board_url(host, client_id, parent_url=parent_url),
            provider=self.name,
            identifier=f"{host}|{client_id}",
        )

    def identify_board_from_page(self, page: Page) -> JobBoard | None:
        parser = _PaycorJobsParser()
        try:
            parser.feed(page.html or "")
        except (TypeError, ValueError):
            return None
        boards = {
            board.identifier: JobBoard(
                url=_board_url(
                    _board_identity(board)[0],
                    _board_identity(board)[1],
                    parent_url=page.final_url or page.url,
                ),
                provider=self.name,
                identifier=board.identifier,
            )
            for url in parser.provider_script_urls
            if (
                board := self.identify_board(
                    urljoin(page.final_url or page.url, url)
                )
            ) is not None
        }
        return next(iter(boards.values())) if len(boards) == 1 else None

    def list_jobs(self, fetcher, board: JobBoard, query: JobQuery) -> AdapterResult:
        identity = _board_identity(board)
        if identity is None:
            return _unsupported(board, "invalid Paycor board identity")
        host, client_id = identity
        board_url = board.url
        fetched_urls = [board_url]

        try:
            page = fetcher.fetch(board_url)
        except (FetchError, OSError, TimeoutError) as error:
            return _fetch_failure(board, fetched_urls, error)
        if not _is_expected_page(page.final_url or page.url, host, client_id, _SEARCH_PATHS):
            return _unsupported(
                board,
                "Paycor board redirected outside the tenant",
                page.final_url or page.url,
            )

        parser = _PaycorJobsParser()
        try:
            parser.feed(page.html or "")
        except (TypeError, ValueError):
            return _invalid(board, fetched_urls, "malformed Paycor HTML")

        search_url = _safe_search_url(parser.get_form, host, client_id, query)
        if search_url and search_url != board_url:
            fetched_urls.append(search_url)
            try:
                page = fetcher.fetch(search_url)
            except (FetchError, OSError, TimeoutError) as error:
                return _fetch_failure(board, fetched_urls, error)
            if not _is_expected_page(
                page.final_url or page.url,
                host,
                client_id,
                _SEARCH_PATHS,
            ):
                return _unsupported(
                    board,
                    "Paycor search redirected outside the tenant",
                    page.final_url or page.url,
                )
            parser = _PaycorJobsParser()
            try:
                parser.feed(page.html or "")
            except (TypeError, ValueError):
                return _invalid(board, fetched_urls, "malformed Paycor search HTML")

        if not parser.has_public_board_fingerprint:
            return _invalid(board, fetched_urls, "missing Paycor public-board fingerprint")

        candidates: list[JobCandidate] = []
        seen: set[str] = set()
        rejected_links = 0
        for title, location, href in parser.jobs:
            detail = _detail_identity(href, host, client_id)
            if detail is None:
                rejected_links += 1
                continue
            job_id = detail
            detail_url = _detail_url(host, client_id, job_id)
            if detail_url in seen:
                continue
            seen.add(detail_url)
            candidates.append(
                JobCandidate(
                    title=title,
                    url=detail_url,
                    provider=self.name,
                    location=location,
                    raw={"job_id": job_id},
                )
            )

        target = _normalized(query.title)
        scope = "title_filtered" if search_url else "full"
        return AdapterResult(
            provider=self.name,
            board=board,
            candidates=candidates,
            reason_code=None if candidates else "EMPTY_PROVIDER_RESPONSE",
            inventory_scope=scope,
            inventory_complete=True,
            trace={
                "adapter": self.name,
                "variant": "career_home_html",
                "board_urls": fetched_urls,
                "response_source": page.source,
                "candidate_count": len(candidates),
                "public_link_count": len(parser.jobs),
                "rejected_link_count": rejected_links,
                "used_get_search_form": bool(search_url),
                "exact_title_found": bool(
                    target
                    and any(_normalized(candidate.title) == target for candidate in candidates)
                ),
                "inventory_scope": scope,
            },
        )


class _PaycorJobsParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.has_public_board_fingerprint = False
        self.jobs: list[tuple[str, str | None, str]] = []
        self.get_form: dict[str, object] | None = None
        self.provider_script_urls: list[str] = []
        self._stack: list[set[str]] = []
        self._row_links: list[tuple[str, str]] | None = None
        self._row_location: list[str] = []
        self._active_href: str | None = None
        self._active_title: list[str] = []
        self._form: dict[str, object] | None = None
        self._table_row = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        normalized_tag = tag.casefold()
        attributes = {key.casefold(): value or "" for key, value in attrs}
        classes = set(attributes.get("class", "").split())
        if normalized_tag == "script" and attributes.get("src"):
            self.provider_script_urls.append(attributes["src"])
        if normalized_tag not in _VOID_TAGS:
            self._stack.append(classes)
        if attributes.get("id") == "gnewtonCareerBody":
            self.has_public_board_fingerprint = True
        if "gnewtonCareerGroupRowClass" in classes:
            self.has_public_board_fingerprint = True
            self._row_links = []
            self._row_location = []
        if normalized_tag == "td" and "gnewtonJobLink" in classes:
            self.has_public_board_fingerprint = True
            if self._row_links is None:
                self._row_links = []
                self._row_location = []
                self._table_row = True
        if (
            normalized_tag == "a"
            and self._row_links is not None
            and (
                self._inside("gnewtonCareerGroupJobTitleClass")
                or self._inside("gnewtonJobLink")
            )
        ):
            self._active_href = attributes.get("href")
            self._active_title = []
        if normalized_tag == "form":
            method = attributes.get("method", "get").casefold()
            action = attributes.get("action", "")
            if method == "get" and action:
                self._form = {"action": action, "controls": {}}
        elif normalized_tag == "input" and self._form is not None:
            self._add_form_control(attributes)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag.casefold() not in _VOID_TAGS:
            self.handle_endtag(tag)

    def handle_data(self, data: str) -> None:
        if self._active_href is not None:
            self._active_title.append(data)
        elif self._row_links is not None and self._inside(
            "gnewtonCareerGroupJobDescriptionClass"
        ) or self._row_links is not None and self._inside("gnewtonJobLocation"):
            self._row_location.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() in _VOID_TAGS:
            return
        classes = self._stack[-1] if self._stack else set()
        if tag.casefold() == "a" and self._active_href is not None:
            title = " ".join("".join(self._active_title).split())
            if title and self._row_links is not None:
                self._row_links.append((title, self._active_href))
            self._active_href = None
            self._active_title = []
        if "gnewtonCareerGroupRowClass" in classes and self._row_links is not None:
            self._finish_row()
        if tag.casefold() == "tr" and self._table_row:
            self._finish_row()
        if tag.casefold() == "form" and self._form is not None:
            self.get_form = self._form
            self._form = None
        if self._stack:
            self._stack.pop()

    def _inside(self, class_name: str) -> bool:
        return any(class_name in classes for classes in self._stack)

    def _finish_row(self) -> None:
        if self._row_links is not None:
            location = " ".join("".join(self._row_location).split()) or None
            self.jobs.extend((title, location, href) for title, href in self._row_links)
        self._row_links = None
        self._row_location = []
        self._table_row = False

    def _add_form_control(self, attributes: dict[str, str]) -> None:
        controls = self._form["controls"]
        if not isinstance(controls, dict) or len(controls) >= _MAX_FORM_CONTROLS:
            self._form = None
            return
        name = attributes.get("name", "")
        if (
            not _FORM_NAME.fullmatch(name)
            or name not in _SEARCH_QUERY_KEYS
            or name in controls
        ):
            self._form = None
            return
        value = attributes.get("value", "")
        if len(value) > 512:
            self._form = None
            return
        controls[name] = {
            "value": value,
            "semantic": " ".join(
                (name, attributes.get("id", ""), attributes.get("placeholder", ""))
            ).casefold(),
            "type": attributes.get("type", "text").casefold(),
        }


def _safe_url(url: str):
    try:
        parsed = urlparse(url)
        port = parsed.port
    except (TypeError, ValueError):
        return None
    if (
        parsed.scheme.casefold() != "https"
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
        or (parsed.hostname or "").casefold() != _HOST
        or parsed.fragment
    ):
        return None
    return parsed


def _query_pairs(parsed) -> list[tuple[str, str]] | None:
    try:
        pairs = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)
    except (ValueError, TypeError):
        return None
    names = [name for name, _value in pairs]
    return pairs if len(names) == len(set(names)) else None


def _url_identity(url: str) -> tuple[str, str, str] | None:
    parsed = _safe_url(url)
    if parsed is None or parsed.path not in _PATHS:
        return None
    pairs = _query_pairs(parsed)
    if pairs is None:
        return None
    params = dict(pairs)
    kind = _PATHS[parsed.path]
    allowed = (
        {"clientId"}
        if kind == "iframe"
        else _HOME_QUERY_KEYS if kind == "home" else _DETAIL_QUERY_KEYS
    )
    if not set(params).issubset(allowed):
        return None
    client_id = params.get("clientId", "")
    if not _CLIENT_ID.fullmatch(client_id):
        return None
    if kind == "home" and not _home_params(params):
        return None
    if kind == "detail" and _detail_params(params, client_id) is None:
        return None
    return ((parsed.hostname or "").casefold(), client_id, kind)


def _home_params(params: dict[str, str]) -> bool:
    lang = params.get("lang", "")
    parent_url = params.get("parentUrl", "")
    return (
        _SAFE_OPTIONAL_VALUE.fullmatch(params.get("source", "")) is not None
        and _SAFE_OPTIONAL_VALUE.fullmatch(params.get("specialization", "")) is not None
        and (not lang or _LANG.fullmatch(lang) is not None)
        and (not parent_url or _safe_parent_url(parent_url) is not None)
    )


def _home_parent_url(url: str) -> str | None:
    parsed = _safe_url(url)
    if parsed is None:
        return None
    pairs = _query_pairs(parsed)
    if pairs is None:
        return None
    return _safe_parent_url(dict(pairs).get("parentUrl", ""))


def _safe_parent_url(value: str) -> str | None:
    if not value:
        return None
    try:
        parsed = urlparse(value)
        port = parsed.port
    except (TypeError, ValueError):
        return None
    host = (parsed.hostname or "").casefold().rstrip(".")
    if (
        parsed.scheme.casefold() != "https"
        or not host
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
        or parsed.query
        or parsed.fragment
        or host == "localhost"
        or host.endswith((".localhost", ".local", ".internal"))
    ):
        return None
    try:
        if not ipaddress.ip_address(host).is_global:
            return None
    except ValueError:
        if "." not in host:
            return None
    return value


def _detail_params(params: dict[str, str], client_id: str) -> str | None:
    job_id = params.get("id", "")
    if params.get("clientId") != client_id or not _JOB_ID.fullmatch(job_id):
        return None
    source = params.get("source", "")
    lang = params.get("lang", "")
    aggregate = params.get("fromAggregate", "")
    if (
        _SAFE_OPTIONAL_VALUE.fullmatch(source) is None
        or (lang and not _LANG.fullmatch(lang))
        or (aggregate and aggregate not in {"true", "false"})
    ):
        return None
    return job_id


def _board_url(host: str, client_id: str, *, parent_url: str | None = None) -> str:
    params = {"clientId": client_id}
    if parent_url:
        params["parentUrl"] = parent_url
    return f"https://{host}/career/CareerHome.action?{urlencode(params)}"


def _detail_url(host: str, client_id: str, job_id: str) -> str:
    return "https://{}/career/JobIntroduction.action?{}".format(
        host,
        urlencode({"clientId": client_id, "id": job_id}),
    )


def _board_identity(board: JobBoard) -> tuple[str, str] | None:
    if board.provider != "paycor" or not board.identifier:
        return None
    parts = board.identifier.split("|", 1)
    if len(parts) != 2:
        return None
    host, client_id = parts
    if host != _HOST or not _CLIENT_ID.fullmatch(client_id):
        return None
    identity = _url_identity(board.url)
    parent_url = _home_parent_url(board.url)
    if identity != (host, client_id, "home") or board.url != _board_url(
        host,
        client_id,
        parent_url=parent_url,
    ):
        return None
    return host, client_id


def _is_expected_page(url: str, host: str, client_id: str, paths: set[str]) -> bool:
    parsed = _safe_url(url)
    if (
        parsed is None
        or (parsed.hostname or "").casefold() != host
        or parsed.path not in paths
    ):
        return False
    pairs = _query_pairs(parsed)
    if pairs is None:
        return False
    params = dict(pairs)
    return (
        params.get("clientId") == client_id
        and set(params).issubset(_SEARCH_QUERY_KEYS)
        and all(len(value) <= 512 and "\x00" not in value for value in params.values())
        and (
            "parentUrl" not in params
            or _safe_parent_url(params["parentUrl"]) is not None
        )
    )


def _detail_identity(href: str, host: str, client_id: str) -> str | None:
    try:
        url = urljoin(_board_url(host, client_id), href)
    except (TypeError, ValueError):
        return None
    identity = _url_identity(url)
    if identity != (host, client_id, "detail"):
        return None
    parsed = _safe_url(url)
    params = dict(_query_pairs(parsed) or [])
    return _detail_params(params, client_id)


def _safe_search_url(
    form: dict[str, object] | None,
    host: str,
    client_id: str,
    query: JobQuery,
) -> str | None:
    if form is None or not (query.title or "").strip():
        return None
    action = form.get("action")
    controls = form.get("controls")
    if not isinstance(action, str) or not isinstance(controls, dict):
        return None
    try:
        action_url = urljoin(_board_url(host, client_id), action)
    except (TypeError, ValueError):
        return None
    parsed = _safe_url(action_url)
    if parsed is None or parsed.path not in _SEARCH_PATHS or parsed.query:
        return None

    values: dict[str, str] = {}
    populated = False
    for name, metadata in controls.items():
        if not isinstance(name, str) or not isinstance(metadata, dict):
            return None
        value = metadata.get("value")
        semantic = metadata.get("semantic")
        control_type = metadata.get("type")
        if not all(isinstance(item, str) for item in (value, semantic, control_type)):
            return None
        if name == "clientId":
            if value != client_id:
                return None
        elif query.title and control_type not in {"hidden", "submit"} and any(
            token in semantic for token in ("keyword", "title")
        ):
            value = query.title.strip()[:256]
            populated = True
        elif query.location and control_type not in {"hidden", "submit"} and any(
            token in semantic for token in ("location", "city", "state")
        ):
            value = query.location.strip()[:256]
            populated = True
        if control_type != "submit":
            values[name] = value
    if values.get("clientId") != client_id or not populated:
        return None
    return f"https://{host}{parsed.path}?{urlencode(values)}"


def _normalized(value: str | None) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", value.casefold())) if value else ""


def _unsupported(board: JobBoard, error: str, rejected_url: str | None = None) -> AdapterResult:
    trace = {"adapter": "paycor", "error": error}
    if rejected_url:
        trace["rejected_final_url"] = rejected_url
    return AdapterResult(
        provider="paycor",
        board=board,
        reason_code="PROVIDER_VARIANT_UNSUPPORTED",
        inventory_complete=False,
        trace=trace,
    )


def _fetch_failure(board: JobBoard, urls: list[str], error: Exception) -> AdapterResult:
    return AdapterResult(
        provider="paycor",
        board=board,
        reason_code="PROVIDER_FETCH_FAILED",
        retryable=True,
        inventory_complete=False,
        trace={"adapter": "paycor", "board_urls": urls, "error": str(error)},
    )


def _invalid(board: JobBoard, urls: list[str], error: str) -> AdapterResult:
    return AdapterResult(
        provider="paycor",
        board=board,
        reason_code="INVALID_STRUCTURED_DATA",
        inventory_complete=False,
        trace={"adapter": "paycor", "board_urls": urls, "error": error},
    )


ADAPTER = PaycorAdapter()
