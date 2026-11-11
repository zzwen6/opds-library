"""OPDS 2.0（/opds2）测试：结构、必填字段、分页、搜索、多格式、认证，并用官方 JSON Schema 校验。"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
from jsonschema import Draft7Validator, FormatChecker
from lxml import etree
from referencing import Registry, Resource

from app.db import SessionLocal
from app.models import Book, BookFile
from app.scan import scan_library
from app.services.books import BookUpdate, get_book_by_uuid, update_book
from tests.conftest import make_cbz, make_epub, make_pdf

SCHEMA_DIR = Path(__file__).parent / "schemas"
FEED = "application/opds+json"
PUB = "application/opds-publication+json"
AUTH = "application/opds-authentication+json"
ACQ_REL = "http://opds-spec.org/acquisition"


def _registry() -> tuple[Registry, dict[str, dict]]:
    resources = []
    by_id: dict[str, dict] = {}
    for path in SCHEMA_DIR.rglob("*.schema.json"):
        # 官方 schema 的 BCP 47 正则用 ECMAScript 命名分组 (?<name>…)，Python re 需要 (?P<name>…)
        text = re.sub(r"\(\?<(?=[A-Za-z])", "(?P<", path.read_text(encoding="utf-8"))
        schema = json.loads(text)
        by_id[schema["$id"]] = schema
        resources.append((schema["$id"], Resource.from_contents(schema)))
    return Registry().with_resources(resources), by_id


REGISTRY, SCHEMAS = _registry()


def _validator(schema_id: str) -> Draft7Validator:
    return Draft7Validator(SCHEMAS[schema_id], registry=REGISTRY, format_checker=FormatChecker())


FEED_V = _validator("https://specs.opds.io/schema/feed.schema.json")
PUB_V = _validator("https://specs.opds.io/schema/publication.schema.json")
AUTH_V = _validator("https://drafts.opds.io/schema/authentication.schema.json")


def _errors(validator: Draft7Validator, doc: dict) -> list[str]:
    return [f"{list(e.absolute_path)}: {e.message}" for e in validator.iter_errors(doc)]


def _rels(link: dict) -> list[str]:
    rel = link.get("rel") or []
    return [rel] if isinstance(rel, str) else list(rel)


def _link(doc: dict, rel: str) -> dict | None:
    return next((lnk for lnk in doc.get("links", []) if rel in _rels(lnk)), None)


def _path(href: str) -> str:
    u = urlparse(href)
    return u.path + (f"?{u.query}" if u.query else "")


def _get_feed(client, auth, path: str) -> dict:
    resp = client.get(path, auth=auth)
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith(FEED)
    doc = resp.json()
    assert not _errors(FEED_V, doc), _errors(FEED_V, doc)
    for pub in doc.get("publications", []):
        _check_publication(pub)
    for group in doc.get("groups", []):
        for pub in group.get("publications", []):
            _check_publication(pub)
    return doc


def _check_publication(pub: dict) -> None:
    assert not _errors(PUB_V, pub), _errors(PUB_V, pub)
    meta = pub["metadata"]
    assert meta["title"] and meta["identifier"].startswith(("urn:isbn:", "urn:uuid:"))
    assert meta["modified"].endswith("Z")
    # schema 的 contains 不强制 rel，这里自己检查：至少一个获取链接，且不是 open-access
    acq = [lnk for lnk in pub["links"] if ACQ_REL in _rels(lnk)]
    assert acq and all(lnk.get("type") for lnk in acq)
    assert not any("download" in _rels(lnk) or "open-access" in " ".join(_rels(lnk)) for lnk in pub["links"])
    assert _link(pub, "self")["type"] == PUB
    # metadata 不应有空值
    assert all(v not in (None, "", [], {}) for v in meta.values())


def _scan(env):
    with SessionLocal() as s:
        scan_library(s, env["library"])
        s.commit()


@pytest.fixture
def library(env):
    lib = env["library"]
    make_epub(lib / "pride.epub", "Pride and Prejudice", "Jane Austen", isbn="9780141439518", description="A novel.", subject="Fiction")
    make_epub(lib / "frank.epub", "Frankenstein", "Mary Shelley", isbn="9780141439471")
    make_epub(lib / "huozhe.epub", "活着", "余华", language="zh", subject="小说")
    _scan(env)
    return lib


def test_requires_auth_and_serves_auth_document(client, env):
    for path in ("/opds2", "/opds2/books", "/opds2/search?query=x", "/opds2/download/1"):
        resp = client.get(path)
        assert resp.status_code == 401
        assert resp.headers["content-type"].startswith(AUTH)
        assert resp.headers["www-authenticate"].startswith("Basic")
        assert 'rel="http://opds-spec.org/auth/document"' in resp.headers["link"]
        doc = resp.json()
        assert not _errors(AUTH_V, doc), _errors(AUTH_V, doc)
        assert doc["authentication"][0]["type"] == "http://opds-spec.org/auth/basic"
    # 错误密码同样 401
    assert client.get("/opds2", auth=("admin", "wrong")).status_code == 401
    # 认证文档本身免认证
    resp = client.get("/opds2/auth")
    assert resp.status_code == 200 and resp.headers["content-type"].startswith(AUTH)
    assert resp.json()["id"].endswith("/opds2/auth")
    # 1.2 的 401 行为不变
    resp = client.get("/opds")
    assert resp.status_code == 401 and resp.headers["www-authenticate"].startswith("Basic")
    assert "link" not in resp.headers


def test_root_navigation_and_cross_links(client, auth, library):
    root = _get_feed(client, auth, "/opds2")
    assert root["metadata"]["title"] == "我的书库"
    titles = {n["title"]: n for n in root["navigation"]}
    for expected in ("最新入库", "全部书目", "作者", "丛书", "标签", "语言"):
        assert expected in titles
        assert titles[expected]["type"] == FEED
    assert titles["全部书目"]["properties"]["numberOfItems"] == 3
    assert _link(root, "self")["href"].endswith("/opds2")
    search = _link(root, "search")
    assert search["templated"] is True and search["href"].endswith("/opds2/search{?query}")
    alt = _link(root, "alternate")
    assert alt["href"].endswith("/opds") and alt["type"].startswith("application/atom+xml;profile=opds-catalog")
    assert _link(root, "http://opds-spec.org/auth/document")["type"] == AUTH
    group = root["groups"][0]
    assert group["metadata"]["title"] == "最新入库" and len(group["publications"]) == 3

    # 1.2 根目录反向链接到 OPDS 2
    atom = etree.fromstring(client.get("/opds", auth=auth).content)
    alts = [l for l in atom.findall("{http://www.w3.org/2005/Atom}link") if l.get("rel") == "alternate"]
    assert alts and alts[0].get("type") == FEED and alts[0].get("href").endswith("/opds2")


def test_publication_metadata(client, auth, library, env):
    with SessionLocal() as s:
        book = s.query(Book).filter(Book.title == "活着").one()
        update_book(
            s,
            book,
            BookUpdate(
                publisher="作家出版社",
                published_date="2012-8",
                series="余华作品",
                series_index="2",
                translators="张三、李四",
                description="一部小说。",
                isbn13="9787506365437",
            ),
            auto_lock=True,
        )
        s.commit()
        uuid = book.uuid
    resp = client.get(f"/opds2/publications/{uuid}", auth=auth)
    assert resp.status_code == 200 and resp.headers["content-type"].startswith(PUB)
    pub = resp.json()
    _check_publication(pub)
    m = pub["metadata"]
    assert m["@type"] == "http://schema.org/Book"
    assert m["title"] == "活着"
    assert m["identifier"] == "urn:isbn:9787506365437"
    assert m["altIdentifier"] == [f"urn:uuid:{uuid}"]
    assert m["author"][0]["name"] == "余华" and m["author"][0]["links"][0]["type"] == FEED
    assert m["translator"] == ["张三", "李四"]
    assert m["language"] == "zh"
    assert m["publisher"] == "作家出版社"
    assert m["published"] == "2012-08-01"
    assert m["description"] == "一部小说。"
    assert {s["name"] for s in m["subject"]} >= {"小说"}
    series = m["belongsTo"]["series"][0]
    assert series["name"] == "余华作品" and series["position"] == 2
    images = pub["images"]
    assert images[0]["href"].endswith(f"/opds2/covers/{uuid}") and images[0]["type"] == "image/jpeg"
    assert images[0]["width"] > 0 and images[0]["height"] > 0
    assert any(i["href"].endswith(f"/opds2/thumbs/{uuid}") for i in images)
    for img in images:
        r = client.get(_path(img["href"]), auth=auth)
        assert r.status_code == 200 and r.headers["content-type"].startswith("image/")
    acq = [l for l in pub["links"] if ACQ_REL in _rels(l)]
    assert len(acq) == 1 and acq[0]["type"] == "application/epub+zip" and acq[0]["size"] > 0
    dl = client.get(_path(acq[0]["href"]), auth=auth)
    assert dl.status_code == 200 and dl.headers["content-type"] == "application/epub+zip"
    assert _link(pub, "self")["href"].endswith(f"/opds2/publications/{uuid}")

    # 丛书 feed 可达且按丛书顺序
    series_feed = _get_feed(client, auth, _path(series["links"][0]["href"]))
    assert series_feed["publications"][0]["metadata"]["title"] == "活着"
    sort_facet = series_feed["facets"][0]
    assert sort_facet["links"][0]["title"] == "丛书顺序" and "self" in _rels(sort_facet["links"][0])

    assert client.get("/opds2/publications/nope", auth=auth).status_code == 404


def test_pagination(client, auth, library):
    page1 = _get_feed(client, auth, "/opds2/books")  # PAGE_SIZE=2
    meta = page1["metadata"]
    assert meta == {"title": "全部书目", "numberOfItems": 3, "itemsPerPage": 2, "currentPage": 1}
    assert len(page1["publications"]) == 2
    assert _link(page1, "first") and _link(page1, "last") and _link(page1, "next")
    assert _link(page1, "previous") is None
    assert "page=2" in _link(page1, "last")["href"]

    page2 = _get_feed(client, auth, _path(_link(page1, "next")["href"]))
    assert page2["metadata"]["currentPage"] == 2
    assert len(page2["publications"]) == 1
    assert _link(page2, "next") is None and _link(page2, "previous")
    assert "page=2" in _link(page2, "self")["href"]
    titles = [p["metadata"]["title"] for p in page1["publications"] + page2["publications"]]
    assert len(set(titles)) == 3

    # 超出范围的页：返回空结果但仍是合法 feed
    page9 = _get_feed(client, auth, "/opds2/books?page=9")
    assert "publications" not in page9 and page9["navigation"]
    assert client.get("/opds2/books?page=0", auth=auth).status_code == 422


def test_search_query_and_q(client, auth, library):
    for param in ("query", "q"):
        doc = _get_feed(client, auth, f"/opds2/search?{param}=Pride")
        assert [p["metadata"]["title"] for p in doc["publications"]] == ["Pride and Prejudice"]
        assert doc["metadata"]["numberOfItems"] == 1
    by_author = _get_feed(client, auth, "/opds2/search?query=余华")
    assert by_author["publications"][0]["metadata"]["title"] == "活着"
    empty = _get_feed(client, auth, "/opds2/search?query=不存在的书")
    assert empty["metadata"]["numberOfItems"] == 0 and "publications" not in empty
    blank = _get_feed(client, auth, "/opds2/search")
    assert blank["metadata"]["numberOfItems"] == 0


def test_search_pagination_keeps_query(client, auth, env):
    lib = env["library"]
    for i in range(3):
        make_epub(lib / f"s{i}.epub", f"Saga {i}", "Writer")
    _scan(env)
    doc = _get_feed(client, auth, "/opds2/search?q=Saga")
    nxt = _link(doc, "next")["href"]
    qs = parse_qs(urlparse(nxt).query)
    assert qs["query"] == ["Saga"] and qs["page"] == ["2"]
    assert len(_get_feed(client, auth, _path(nxt))["publications"]) == 1


def test_navigation_subfeeds(client, auth, library):
    authors = _get_feed(client, auth, "/opds2/authors")
    names = {n["title"]: n for n in authors["navigation"]}
    assert {"Jane Austen", "Mary Shelley", "余华"} <= set(names)
    assert names["余华"]["properties"]["numberOfItems"] == 1
    feed = _get_feed(client, auth, _path(names["Jane Austen"]["href"]))
    assert feed["publications"][0]["metadata"]["title"] == "Pride and Prejudice"

    tags = _get_feed(client, auth, "/opds2/tags")
    tag = next(n for n in tags["navigation"] if n["title"] == "小说")
    assert _get_feed(client, auth, _path(tag["href"]))["publications"][0]["metadata"]["title"] == "活着"

    langs = _get_feed(client, auth, "/opds2/languages")
    zh = next(n for n in langs["navigation"] if n["href"].endswith("/languages/zh"))
    assert zh["title"].startswith("中文") and zh["properties"]["numberOfItems"] == 1
    assert len(_get_feed(client, auth, "/opds2/languages/en")["publications"]) == 2

    # 没有丛书时也要是合法 feed
    series = _get_feed(client, auth, "/opds2/series")
    assert series["metadata"]["numberOfItems"] == 0 and len(series["navigation"]) == 1
    assert client.get("/opds2/authors/999", auth=auth).status_code == 404


def test_facets_sort_and_language(client, auth, library):
    doc = _get_feed(client, auth, "/opds2/new")
    sort_facet, lang_facet = doc["facets"]
    assert sort_facet["metadata"]["title"] == "排序" and lang_facet["metadata"]["title"] == "语言"
    active = [l["title"] for l in sort_facet["links"] if "self" in _rels(l)]
    assert active == ["最新入库"]
    by_title = next(l for l in sort_facet["links"] if l["title"] == "书名")
    titles = [p["metadata"]["title"] for p in _get_feed(client, auth, _path(by_title["href"]))["publications"]]
    assert titles == ["Frankenstein", "Pride and Prejudice"]

    counts = {l["title"]: l["properties"]["numberOfItems"] for l in lang_facet["links"]}
    assert counts["全部语言"] == 3
    zh = next(l for l in lang_facet["links"] if "zh" in l["title"])
    filtered = _get_feed(client, auth, _path(zh["href"]))
    assert filtered["metadata"]["numberOfItems"] == 1
    assert [l["title"] for l in filtered["facets"][1]["links"] if "self" in _rels(l)] == [zh["title"]]


def test_multi_format_and_identical_copies(client, auth, env):
    lib = env["library"]
    make_epub(lib / "huozhe.epub", "《活着》", "余华", language="zh")
    make_pdf(lib / "huozhe.pdf", "活着（精校版）", "[中]余华")
    make_cbz(lib / "comic.cbz", "Comic One")
    (lib / "copy").mkdir()
    shutil.copy(lib / "huozhe.epub", lib / "copy" / "huozhe.epub")
    _scan(env)
    with SessionLocal() as s:
        epub_uuid = s.query(BookFile).filter(BookFile.rel_path == "huozhe.epub").one().book.uuid
        pdf_uuid = s.query(BookFile).filter(BookFile.rel_path == "huozhe.pdf").one().book.uuid
    res = client.post("/api/duplicates/merge", json={"primary_uuid": epub_uuid, "other_uuids": [pdf_uuid]}, auth=auth)
    assert res.status_code == 200, res.text

    pub = client.get(f"/opds2/publications/{epub_uuid}", auth=auth).json()
    _check_publication(pub)
    acq = [l for l in pub["links"] if ACQ_REL in _rels(l)]
    # epub 两份完全相同的副本只给一个链接，再加合并进来的 pdf
    assert [l["type"] for l in acq] == ["application/epub+zip", "application/pdf"]
    for l in acq:
        assert client.get(_path(l["href"]), auth=auth).status_code == 200
    with SessionLocal() as s:
        assert s.query(BookFile).count() == 4  # 数据库里的副本记录还在

    comic = _get_feed(client, auth, "/opds2/search?query=Comic")["publications"][0]
    assert [l["type"] for l in comic["links"] if ACQ_REL in _rels(l)] == ["application/vnd.comicbook+zip"]


def test_missing_files_are_hidden(client, auth, env):
    lib = env["library"]
    make_epub(lib / "gone.epub", "Gone Book", "Nobody")
    make_epub(lib / "here.epub", "Here Book", "Somebody")
    _scan(env)
    (lib / "gone.epub").unlink()
    _scan(env)
    doc = _get_feed(client, auth, "/opds2/books")
    assert [p["metadata"]["title"] for p in doc["publications"]] == ["Here Book"]
    assert doc["metadata"]["numberOfItems"] == 1
    with SessionLocal() as s:
        uuid = s.query(Book).filter(Book.title == "Gone Book").one().uuid
    assert client.get(f"/opds2/publications/{uuid}", auth=auth).status_code == 404
    # 1.2 行为不变：仍列出该书（无下载链接）
    atom = etree.fromstring(client.get("/opds/books", auth=auth).content)
    assert len(atom.findall("{http://www.w3.org/2005/Atom}entry")) == 2


def test_empty_library_feeds_are_valid(client, auth, env):
    root = _get_feed(client, auth, "/opds2")
    assert "groups" not in root
    for path in ("/opds2/new", "/opds2/books", "/opds2/authors", "/opds2/tags", "/opds2/languages"):
        _get_feed(client, auth, path)


def test_schema_validator_is_effective():
    # 防止 schema 引用没装好导致“永远通过”
    assert _errors(FEED_V, {"metadata": {"title": "x"}, "links": []})
    assert _errors(PUB_V, {"metadata": {"title": "x", "published": "2012-8"}, "links": []})
    assert _errors(PUB_V, {"metadata": {"title": "x", "language": "中文"}, "links": [{"href": "a", "rel": "acquisition"}]})
    assert not _errors(PUB_V, {"metadata": {"title": "x"}, "links": [{"href": "a", "rel": "acquisition"}]})
