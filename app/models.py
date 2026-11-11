from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Index,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_uuid() -> str:
    return str(uuid.uuid4())


class Book(Base):
    __tablename__ = "books"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    uuid: Mapped[str] = mapped_column(String(36), unique=True, default=new_uuid, index=True)
    title: Mapped[str] = mapped_column(String(1024), default="未知书名")
    subtitle: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    sort_title: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    isbn13: Mapped[Optional[str]] = mapped_column(String(13), nullable=True, unique=True)
    isbn10: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    language: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    translators: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    publisher_id: Mapped[Optional[int]] = mapped_column(ForeignKey("publishers.id"), nullable=True)
    series_id: Mapped[Optional[int]] = mapped_column(ForeignKey("series.id"), nullable=True)
    series_index: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    published_date: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    page_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    rating: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    cover_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # 不建 FK，避免与 covers.book_id 循环引用
    scrape_status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    field_sources_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)

    files: Mapped[list[BookFile]] = relationship(
        back_populates="book", cascade="all, delete-orphan"
    )
    authors: Mapped[list[BookAuthor]] = relationship(
        back_populates="book", cascade="all, delete-orphan"
    )
    tags: Mapped[list[BookTag]] = relationship(
        back_populates="book", cascade="all, delete-orphan"
    )
    locks: Mapped[list[FieldLock]] = relationship(
        back_populates="book", cascade="all, delete-orphan"
    )
    scrape_records: Mapped[list[ScrapeRecord]] = relationship(
        back_populates="book", cascade="all, delete-orphan"
    )
    covers: Mapped[list[Cover]] = relationship(
        back_populates="book",
        foreign_keys="Cover.book_id",
        cascade="all, delete-orphan",
    )
    publisher: Mapped[Optional[Publisher]] = relationship()
    series: Mapped[Optional[Series]] = relationship()
    primary_cover: Mapped[Optional[Cover]] = relationship(
        foreign_keys=[cover_id],
        primaryjoin="Book.cover_id==Cover.id",
        post_update=True,
        viewonly=True,
    )

    __table_args__ = (Index("ix_books_title", "title"),)


class BookFile(Base):
    __tablename__ = "book_files"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    book_id: Mapped[int] = mapped_column(ForeignKey("books.id"), index=True)
    rel_path: Mapped[str] = mapped_column(String(2048), unique=True)
    filename: Mapped[str] = mapped_column(String(1024))
    format: Mapped[str] = mapped_column(String(16), index=True)
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    sha256: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    xxhash64: Mapped[Optional[str]] = mapped_column(String(16), nullable=True, index=True)
    mtime: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    inode: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    extracted_title: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    extracted_author: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    extracted_isbn: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    missing_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)

    book: Mapped[Book] = relationship(back_populates="files")


class Author(Base):
    __tablename__ = "authors"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    uuid: Mapped[str] = mapped_column(String(36), unique=True, default=new_uuid)
    name: Mapped[str] = mapped_column(String(512), unique=True)
    sort_name: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    aliases_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    bio: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)

    books: Mapped[list[BookAuthor]] = relationship(back_populates="author")


class BookAuthor(Base):
    __tablename__ = "book_authors"

    book_id: Mapped[int] = mapped_column(ForeignKey("books.id"), primary_key=True)
    author_id: Mapped[int] = mapped_column(ForeignKey("authors.id"), primary_key=True)
    role: Mapped[str] = mapped_column(String(32), primary_key=True, default="author")
    position: Mapped[int] = mapped_column(Integer, default=0)

    book: Mapped[Book] = relationship(back_populates="authors")
    author: Mapped[Author] = relationship(back_populates="books")


class Publisher(Base):
    __tablename__ = "publishers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(512), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class Series(Base):
    __tablename__ = "series"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    uuid: Mapped[str] = mapped_column(String(36), unique=True, default=new_uuid)
    name: Mapped[str] = mapped_column(String(512), unique=True)
    sort_name: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)

    books: Mapped[list[Book]] = relationship(back_populates="series")


class Tag(Base):
    __tablename__ = "tags"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(256), unique=True)
    kind: Mapped[str] = mapped_column(String(32), default="user")

    books: Mapped[list[BookTag]] = relationship(back_populates="tag")


class BookTag(Base):
    __tablename__ = "book_tags"

    book_id: Mapped[int] = mapped_column(ForeignKey("books.id"), primary_key=True)
    tag_id: Mapped[int] = mapped_column(ForeignKey("tags.id"), primary_key=True)

    book: Mapped[Book] = relationship(back_populates="tags")
    tag: Mapped[Tag] = relationship(back_populates="books")


class FieldLock(Base):
    __tablename__ = "field_locks"
    __table_args__ = (UniqueConstraint("book_id", "field_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    book_id: Mapped[int] = mapped_column(ForeignKey("books.id"), index=True)
    field_name: Mapped[str] = mapped_column(String(64))
    locked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    reason: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)

    book: Mapped[Book] = relationship(back_populates="locks")


class ScrapeRecord(Base):
    __tablename__ = "scrape_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    book_id: Mapped[int] = mapped_column(ForeignKey("books.id"), index=True)
    source: Mapped[str] = mapped_column(String(64))
    source_id: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    source_url: Mapped[Optional[str]] = mapped_column(String(2048), nullable=True)
    confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    raw_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    fields_applied_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    previous_values_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    applied_mode: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="candidate")
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    book: Mapped[Book] = relationship(back_populates="scrape_records")


class Cover(Base):
    __tablename__ = "covers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    book_id: Mapped[int] = mapped_column(ForeignKey("books.id"), index=True)
    source: Mapped[str] = mapped_column(String(64), default="embedded")
    rel_path: Mapped[str] = mapped_column(String(1024))
    thumb_path: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    mime: Mapped[str] = mapped_column(String(64), default="image/jpeg")
    width: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    height: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    book: Mapped[Book] = relationship(back_populates="covers", foreign_keys=[book_id])


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    type: Mapped[str] = mapped_column(String(32), index=True)
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    payload_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    result_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class OrganizeBatch(Base):
    __tablename__ = "organize_batches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    status: Mapped[str] = mapped_column(String(32), default="applied", index=True)
    template: Mapped[str] = mapped_column(String(512))
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    rolled_back_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    moves: Mapped[list["OrganizeMove"]] = relationship(
        back_populates="batch", cascade="all, delete-orphan"
    )


class OrganizeMove(Base):
    __tablename__ = "organize_moves"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("organize_batches.id"), index=True)
    file_id: Mapped[Optional[int]] = mapped_column(ForeignKey("book_files.id"), nullable=True)
    from_path: Mapped[str] = mapped_column(String(2048))
    to_path: Mapped[str] = mapped_column(String(2048))
    status: Mapped[str] = mapped_column(String(32), default="moved")
    note: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)

    batch: Mapped[OrganizeBatch] = relationship(back_populates="moves")

