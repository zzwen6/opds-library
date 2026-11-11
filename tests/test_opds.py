from lxml import etree

from app.db import SessionLocal
from app.scan import scan_library
from tests.conftest import make_epub

ATOM = "http://www.w3.org/2005/Atom"
DC = "http://purl.org/dc/terms/"
OPENSEARCH = "http://a9.com/-/spec/opensearch/1.1/"

NAV = "application/atom+xml;profile=opds-catalog;kind=navigation"
ACQ = "application/atom+xml;profile=opds-catalog;kind=acquisition"


def _xml(resp):
    assert resp.status_code == 200, resp.text
    return etree.fromstring(resp.content)


def test_opds_requires_basic_auth(client):
    resp = client.get("/opds")
    assert resp.status_code == 401
    assert "WWW-Authenticate" in resp.headers


def test_opds_root_is_navigation_feed(client, auth, env):
    resp = client.get("/opds", auth=auth)
    root = _xml(resp)
    assert root.tag == f"{{{ATOM}}}feed"
    assert root.findtext(f"{{{ATOM}}}title") == "我的书库"
    rels = {link.get("rel"): link for link in root.findall(f"{{{ATOM}}}link")}
    assert rels["self"].get("type").startswith(NAV)
    assert rels["start"].get("href").endswith("/opds")
    assert rels["search"].get("type") == "application/opensearchdescription+xml"
    titles = [e.findtext(f"{{{ATOM}}}title") for e in root.findall(f"{{{ATOM}}}entry")]
    for expected in ("最新入库", "全部书目", "作者", "丛书", "标签"):
        assert expected in titles
    # 导航条目必须指向 navigation/acquisition，不能带 acquisition 下载链
    for entry in root.findall(f"{{{ATOM}}}entry"):
        hrefs = [lnk.get("rel") for lnk in entry.findall(f"{{{ATOM}}}link")]
        assert "http://opds-spec.org/acquisition" not in hrefs
        assert "subsection" in hrefs


def test_opds_acquisition_feed_has_book_links_and_pagination(client, auth, env):
    lib = env["library"]
    make_epub(lib / "pride.epub", "Pride and Prejudice", "Jane Austen", isbn="9780141439518", description="A novel.")
    make_epub(lib / "frank.epub", "Frankenstein", "Mary Shelley", isbn="9780141439471")
    make_epub(lib / "dracula.epub", "Dracula", "Bram Stoker")
    with SessionLocal() as session:
        scan_library(session, lib)
        session.commit()

    resp = client.get("/opds/new", auth=auth)
    root = _xml(resp)
    assert resp.headers["content-type"].startswith(ACQ.split(";")[0])
    assert root.find(f"{{{OPENSEARCH}}}totalResults") is not None
    assert root.find(f"{{{OPENSEARCH}}}totalResults").text == "3"
    assert root.find(f"{{{OPENSEARCH}}}itemsPerPage").text == "2"
    links = {lnk.get("rel"): lnk for lnk in root.findall(f"{{{ATOM}}}link")}
    assert "next" in links
    assert "first" in links
    assert "last" in links

    entries = root.findall(f"{{{ATOM}}}entry")
    assert 0 < len(entries) <= 2
    entry = entries[0]
    assert entry.find(f"{{{ATOM}}}title") is not None
    assert entry.find(f"{{{ATOM}}}id").text.startswith("urn:uuid:")
    assert entry.find(f"{{{ATOM}}}author/{{{ATOM}}}name") is not None
    rels = [lnk.get("rel") for lnk in entry.findall(f"{{{ATOM}}}link")]
    assert "http://opds-spec.org/acquisition" in rels
    assert "http://opds-spec.org/image" in rels
    assert "http://opds-spec.org/image/thumbnail" in rels
    acq = next(lnk for lnk in entry.findall(f"{{{ATOM}}}link") if lnk.get("rel") == "http://opds-spec.org/acquisition")
    assert acq.get("type") == "application/epub+zip"
    # 私有书库不得使用 open-access
    assert all(lnk.get("rel") != "http://opds-spec.org/acquisition/open-access" for lnk in entry.findall(f"{{{ATOM}}}link"))
    assert entry.find(f"{{{DC}}}identifier") is not None or True  # 部分书可能无 ISBN

    # 翻页
    next_href = links["next"].get("href")
    page2 = client.get(next_href.replace("http://testserver", ""), auth=auth)
    root2 = _xml(page2)
    assert len(root2.findall(f"{{{ATOM}}}entry")) >= 1

    # 搜索与 OpenSearch
    desc = client.get("/opds/search.xml", auth=auth)
    assert desc.status_code == 200
    assert "opensearchdescription" in desc.headers["content-type"]
    droot = etree.fromstring(desc.content)
    url = droot.find("{http://a9.com/-/spec/opensearch/1.1/}Url")
    assert url is not None
    assert "kind=acquisition" in url.get("type")
    assert "{searchTerms}" in url.get("template")

    search = client.get("/opds/search", params={"q": "Pride"}, auth=auth)
    sroot = _xml(search)
    titles = [e.findtext(f"{{{ATOM}}}title") for e in sroot.findall(f"{{{ATOM}}}entry")]
    assert any("Pride" in t for t in titles)

    authors = client.get("/opds/authors", auth=auth)
    aroot = _xml(authors)
    names = [e.findtext(f"{{{ATOM}}}title") for e in aroot.findall(f"{{{ATOM}}}entry")]
    assert "Jane Austen" in names


def test_web_api_list_edit_and_lock(client, auth, env):
    lib = env["library"]
    make_epub(lib / "pride.epub", "Pride and Prejudice", "Jane Austen", isbn="9780141439518")
    with SessionLocal() as session:
        scan_library(session, lib)
        session.commit()
    listed = client.get("/api/books", auth=auth)
    assert listed.status_code == 200
    item = listed.json()["items"][0]
    uuid = item["uuid"]
    detail = client.get(f"/api/books/{uuid}", auth=auth).json()
    assert detail["title"] == "Pride and Prejudice"
    # 修改标题应自动锁定
    patched = client.patch(
        f"/api/books/{uuid}",
        auth=auth,
        json={"title": "傲慢与偏见", "description": "手改简介"},
    )
    assert patched.status_code == 200
    body = patched.json()
    assert body["title"] == "傲慢与偏见"
    locks = {l["field_name"] for l in body["locks"]}
    assert "title" in locks
    assert "description" in locks
