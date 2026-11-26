"""The sitemap: pages arranged and linked, with page templates dropped in.

Section D of the planning library. A sitemap is an ordered tree of pages. This is
the level where a **page template** is dropped to create a page; inside a page
the library offers **widgets** instead (see ``library.panel_entries``).

Routes are the links between pages, so the sitemap is also the source of the
flows between screens.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Iterable

from distr.core.planning.page import Page, parse_page, page_from_template

logger = logging.getLogger(__name__)

MAX_PAGES = 200
MAX_DEPTH = 8


@dataclass
class SitemapNode:
    """One page in the tree, with its children."""

    page: Page
    children: list["SitemapNode"] = field(default_factory=list)

    @property
    def name(self) -> str:
        return self.page.name

    @property
    def route(self) -> str:
        return self.page.route


@dataclass
class Sitemap:
    """An ordered tree of pages."""

    title: str = "Sitemap"
    roots: list[SitemapNode] = field(default_factory=list)

    # ---------------------------------------------------------------- lookup

    def _walk(self, nodes: Iterable[SitemapNode] | None = None, depth: int = 0):
        for node in self.roots if nodes is None else nodes:
            yield node, depth
            if depth < MAX_DEPTH:
                yield from self._walk(node.children, depth + 1)

    def all_nodes(self) -> list[SitemapNode]:
        return [node for node, _ in self._walk()]

    def find(self, name: str) -> SitemapNode | None:
        wanted = str(name or "").strip().lower()
        if not wanted:
            return None
        for node in self.all_nodes():
            if node.name == wanted:
                return node
        return None

    def count(self) -> int:
        return len(self.all_nodes())

    def routes(self) -> list[str]:
        """Every page route, in tree order, deduplicated."""
        seen: list[str] = []
        for node in self.all_nodes():
            route = node.route.strip()
            if route and route not in seen:
                seen.append(route)
        return seen

    # ---------------------------------------------------------------- mutate

    def add(self, page: Page, *, parent: str | None = None) -> SitemapNode | None:
        """Add a page, optionally under ``parent``. None when the cap is hit."""
        if not isinstance(page, Page) or not page.name:
            return None
        if self.count() >= MAX_PAGES:
            logger.warning("sitemap: refusing to add %r; at the %d page cap", page.name, MAX_PAGES)
            return None
        if self.find(page.name) is not None:
            logger.warning("sitemap: %r is already in the sitemap; not added twice", page.name)
            return None

        node = SitemapNode(page=page)
        if parent:
            target = self.find(parent)
            if target is None:
                logger.warning("sitemap: parent %r not found; adding %r at the top level", parent, page.name)
                self.roots.append(node)
            else:
                target.children.append(node)
        else:
            self.roots.append(node)
        return node

    def drop_template(self, entry: Any, *, title: str = "", route: str = "", parent: str | None = None) -> SitemapNode | None:
        """Create a page from a page-template entry and add it.

        This is the drag-and-drop at sitemap level: a template becomes a page.
        """
        page = page_from_template(entry, title=title, route=route)
        return self.add(page, parent=parent)

    def remove(self, name: str) -> bool:
        """Remove a page and its subtree."""
        wanted = str(name or "").strip().lower()
        if not wanted:
            return False

        def _strip(nodes: list[SitemapNode]) -> bool:
            for i, node in enumerate(nodes):
                if node.name == wanted:
                    nodes.pop(i)
                    return True
                if _strip(node.children):
                    return True
            return False

        return _strip(self.roots)

    # ---------------------------------------------------------------- output

    def to_wire(self, *, library_module: Any = None) -> tuple[str, list[str]]:
        """Render every page to one wireframe document. Returns (wire, missing)."""
        from distr.core.planning.page import compose_wire

        blocks: list[str] = []
        missing: list[str] = []
        for node in self.all_nodes():
            wire, gone = compose_wire(node.page, library_module=library_module)
            blocks.append(wire.rstrip("\n"))
            missing.extend(gone)
        return "\n\n".join(blocks) + ("\n" if blocks else ""), missing

    def as_dict(self) -> dict[str, Any]:
        def node_dict(node: SitemapNode) -> dict[str, Any]:
            return {"page": node.page.as_dict(), "children": [node_dict(c) for c in node.children]}

        return {"title": self.title, "roots": [node_dict(n) for n in self.roots]}


def parse_sitemap(raw: Any) -> Sitemap:
    """Build a sitemap from a dict. Junk is skipped; never raises."""
    if not isinstance(raw, dict):
        return Sitemap()

    def build(items: Any, depth: int = 0) -> list[SitemapNode]:
        out: list[SitemapNode] = []
        if not isinstance(items, (list, tuple)) or depth > MAX_DEPTH:
            return out
        for item in items:
            if not isinstance(item, dict):
                continue
            page = parse_page(item.get("page") if isinstance(item.get("page"), dict) else item)
            if page is None:
                logger.warning("sitemap: skipped an unusable page at depth %d", depth)
                continue
            out.append(SitemapNode(page=page, children=build(item.get("children"), depth + 1)))
            if len(out) >= MAX_PAGES:
                break
        return out

    return Sitemap(title=str(raw.get("title") or "Sitemap")[:120], roots=build(raw.get("roots")))
