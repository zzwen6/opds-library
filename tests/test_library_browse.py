from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.db import SessionLocal
from app.models import Author, Book, BookAuthor, BookTag, Cover, Publisher, Series, Tag
from app.services.books import series_index_key
from app.util import sort_key


def _seed() -> dict[str, int]:
    """建一个小书库：两套丛书、三位作者、标签、出版社、封面、刮削状态各不相同。"""
    base = datetime(2024, 1, 1, tzinfo=timezone.utc)
    with SessionLocal() as s:
        yu = Author(name="余华", sort_name="yuhua")
        liu = Author(name="刘慈欣", sort_name="liucixin")
        jin = Author(name="金庸", sort_name="jinyong")
        lonely = Author(name="无书作者", sort_name="wushu")
        trio = Series(name="三体", sort_name=sort_key("三体"))
        sdyx = Series(name="射雕三部曲", sort_name=sort_key("射雕三部曲"))
        empty_series = Series(name="空丛书", sort_name=sort_key("空丛书"))
        renmin = Publisher(name="人民文学出版社")
        chongqing = Publisher(name="重庆出版社")
        empty_pub = Publisher(name="没书的出版社")
        scifi = Tag(name="科幻", kind="user")
        wuxia = Tag(name="武侠", kind="user")
        s.add_all([yu, liu, jin, lonely, trio, sdyx, empty_series, renmin, chongqing, empty_pub, scifi, wuxia])
        s.flush()

        def book(title, i, **kw):
            b = Book(title=title, sort_title=sort_key(title), added_at=base + timedelta(days=i), **kw)
            s.add(b)
            s.flush()
            return b

        huozhe = book("活着", 0, publisher_id=renmin.id, rating=9.4, published_date="2012-08",
                      scrape_status="confirmed")
        t3 = book("死神永生", 1, series_id=trio.id, series_index="3", publisher_id=chongqing.id,
                  rating=9.1, published_date="2010-11", scrape_status="auto_applied")
        t1 = book("三体", 2, series_id=trio.id, series_index="1", publisher_id=chongqing.id,
                  rating=8.9, published_date="2008-01", scrape_status="auto_applied")
        t2 = book("黑暗森林", 3, series_id=trio.id, series_index="2", publisher_id=chongqing.id,
                  published_date="2008-05", scrape_status="pending")
        s3 = book("倚天屠龙记", 4, series_id=sdyx.id, series_index="第三部", scrape_status="pending")
        s1 = book("射雕英雄传", 5, series_id=sdyx.id, series_index="1", scrape_status="failed")
        s2 = book("神雕侠侣", 6, series_id=sdyx.id, series_index="二", scrape_status="pending")
        anon = book("佚名之书", 7, scrape_status="pending")

        for b, a in [(huozhe, yu), (t1, liu), (t2, liu), (t3, liu), (s1, jin), (s2, jin), (s3, jin)]:
            s.add(BookAuthor(book_id=b.id, author_id=a.id, role="author", position=0))
        for b in (t1, t2, t3):
            s.add(BookTag(book_id=b.id, tag_id=scifi.id))
        for b in (s1, s2, s3):
            s.add(BookTag(book_id=b.id, tag_id=wuxia.id))
        for b in (huozhe, t1):
            c = Cover(book_id=b.id, source="embedded", rel_path=f"covers/{b.id}.jpg")
            s.add(c)
            s.flush()
            b.cover_id = c.id
        # 只有封面记录、没设主封面的书，列表也会显示封面，不算“无封面”
        s.add(Cover(book_id=t2.id, source="douban", rel_path=f"covers/{t2.id}.jpg", is_primary=False))
        s.commit()
        return {
            "yu": yu.id, "liu": liu.id, "jin": jin.id, "lonely": lonely.id,
            "trio": trio.id, "sdyx": sdyx.id, "empty_series": empty_series.id,
            "renmin": renmin.id, "chongqing": chongqing.id, "empty_pub": empty_pub.id,
            "scifi": scifi.id, "wuxia": wuxia.id,
        }


def _titles(client, auth, **params) -> list[str]:
    params.setdefault("page_size", 50)
    res = client.get("/api/books", params=params, auth=auth)
    assert res.status_code == 200, res.text
    return [b["title"] for b in res.json()["items"]]


def test_search_matches_series_tag_and_publisher(client, auth, env):
    _seed()
    assert set(_titles(client, auth, q="三体")) == {"三体", "黑暗森林", "死神永生"}
    assert set(_titles(client, auth, q="武侠")) == {"射雕英雄传", "神雕侠侣", "倚天屠龙记"}
    assert set(_titles(client, auth, q="人民文学")) == {"活着"}
    assert set(_titles(client, auth, q="重庆")) == {"三体", "黑暗森林", "死神永生"}
    # 原有范围仍然有效：书名、作者
    assert _titles(client, auth, q="活着") == ["活着"]
    assert set(_titles(client, auth, q="金庸")) == {"射雕英雄传", "神雕侠侣", "倚天屠龙记"}
    assert _titles(client, auth, q="  ") != []  # 空白搜索等同不搜


def test_category_filters_and_with_search(client, auth, env):
    ids = _seed()
    assert set(_titles(client, auth, author_id=ids["liu"])) == {"三体", "黑暗森林", "死神永生"}
    assert set(_titles(client, auth, tag_id=ids["wuxia"], q="神雕")) == {"神雕侠侣"}
    assert _titles(client, auth, publisher_id=ids["chongqing"], tag_id=ids["wuxia"]) == []
    assert set(_titles(client, auth, author_id=ids["liu"], publisher_id=ids["chongqing"])) == {
        "三体", "黑暗森林", "死神永生"
    }


def test_sort_series_uses_natural_index_order(client, auth, env):
    ids = _seed()
    assert _titles(client, auth, series_id=ids["trio"], sort="series") == ["三体", "黑暗森林", "死神永生"]
    # 1 / 二 / 第三部：数字与中文数字都按数值排
    assert _titles(client, auth, series_id=ids["sdyx"], sort="series") == ["射雕英雄传", "神雕侠侣", "倚天屠龙记"]
    all_titles = _titles(client, auth, sort="series")
    # 按丛书名排，无丛书的书排最后
    assert all_titles[:3] == ["三体", "黑暗森林", "死神永生"]
    assert all_titles[3:6] == ["射雕英雄传", "神雕侠侣", "倚天屠龙记"]
    assert set(all_titles[6:]) == {"活着", "佚名之书"}
    # 分页仍然正确
    res = client.get("/api/books", params={"sort": "series", "page": 2, "page_size": 3}, auth=auth).json()
    assert res["total"] == 8
    assert [b["title"] for b in res["items"]] == ["射雕英雄传", "神雕侠侣", "倚天屠龙记"]


def test_series_index_key():
    values = [None, "10", "2", "1.5", "第三卷", "附录", "1"]
    assert sorted(values, key=series_index_key) == ["1", "1.5", "2", "第三卷", "10", "附录", None]


def test_sort_author_uses_first_author_sort_name(client, auth, env):
    _seed()
    titles = _titles(client, auth, sort="author")
    # jinyong < liucixin < yuhua，同一作者内按书名，无作者排最后
    assert titles[:3] == sorted(["射雕英雄传", "神雕侠侣", "倚天屠龙记"], key=lambda t: sort_key(t))
    assert set(titles[3:6]) == {"三体", "黑暗森林", "死神永生"}
    assert titles[6:] == ["活着", "佚名之书"]


def test_sort_rating_and_published_put_nulls_last(client, auth, env):
    _seed()
    assert _titles(client, auth, sort="rating")[:3] == ["活着", "死神永生", "三体"]
    assert _titles(client, auth, sort="published")[:4] == ["活着", "死神永生", "黑暗森林", "三体"]
    assert _titles(client, auth, sort="title") == sorted(_titles(client, auth, sort="title"),
                                                         key=lambda t: sort_key(t))
    assert _titles(client, auth)[0] == "佚名之书"  # 默认最新入库


def test_missing_info_filters(client, auth, env):
    ids = _seed()
    no_cover = set(_titles(client, auth, missing_cover=1))
    assert no_cover == {"死神永生", "倚天屠龙记", "射雕英雄传", "神雕侠侣", "佚名之书"}
    assert set(_titles(client, auth, missing_tags=1)) == {"活着", "佚名之书"}
    assert set(_titles(client, auth, scrape_status="pending")) == {"黑暗森林", "倚天屠龙记", "神雕侠侣", "佚名之书"}
    # 与分类、搜索叠加
    assert set(_titles(client, auth, missing_cover=1, series_id=ids["sdyx"], scrape_status="pending")) == {
        "倚天屠龙记", "神雕侠侣"
    }
    assert _titles(client, auth, missing_tags=1, q="活") == ["活着"]


def test_list_item_has_browse_fields(client, auth, env):
    ids = _seed()
    item = client.get("/api/books", params={"series_id": ids["trio"], "sort": "series"}, auth=auth).json()["items"][0]
    assert item["series"] == "三体" and item["series_index"] == "1"
    assert item["tags"] == ["科幻"]
    assert item["rating"] == 8.9 and item["published_date"] == "2008-01"


def test_category_lists_have_book_counts(client, auth, env):
    _seed()
    authors = {a["name"]: a["book_count"] for a in client.get("/api/authors", auth=auth).json()}
    assert authors["刘慈欣"] == 3 and authors["余华"] == 1 and authors["无书作者"] == 0
    series = {x["name"]: x["book_count"] for x in client.get("/api/series", auth=auth).json()}
    assert series == {"三体": 3, "射雕三部曲": 3, "空丛书": 0}
    tags = {x["name"]: x["book_count"] for x in client.get("/api/tags", auth=auth).json()}
    assert tags == {"科幻": 3, "武侠": 3}
    pubs = {x["name"]: x["book_count"] for x in client.get("/api/publishers", auth=auth).json()}
    assert pubs == {"人民文学出版社": 1, "重庆出版社": 3, "没书的出版社": 0}
