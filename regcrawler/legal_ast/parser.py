"""Legal AST 第二階段：語法分析（Parsing）。

由 lexer 的 token 串列建樹：
    Law → (Chapter，可選) → Article → Paragraph(項) → Subparagraph(款) → Item(目) → SubItem
英文（GDPR）：Article → Paragraph（「1.」）→ Subparagraph／point（「(a)」）

中文條文的「項」沒有編號，依換行判定：不以款／目／子目標記開頭的行即為新的一項；
「一、」開頭為款、「（一）」開頭為目、「1.」「(1)」開頭為子目（掛在最近的目，無目則掛在款）。
每個節點保有：原文文字（text）、自身文字（own_text，不含子節點）、正規化鍵（key，例如 "20-1/p2/s3"）、
token 清單與原文 span。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..models import Article, LawDocument
from ..normalize import article_key, norm_text
from . import lexer as L

LAW, CHAPTER, ARTICLE, PARAGRAPH, SUBPARAGRAPH, ITEM, SUBITEM = (
    "Law", "Chapter", "Article", "Paragraph", "Subparagraph", "Item", "SubItem")

_KEY_PREFIX = {PARAGRAPH: "p", SUBPARAGRAPH: "s", ITEM: "i", SUBITEM: "si"}
_ZH_UNIT = {PARAGRAPH: "項", SUBPARAGRAPH: "款", ITEM: "目", SUBITEM: "子目"}
_EN_UNIT = {PARAGRAPH: "paragraph", SUBPARAGRAPH: "point", ITEM: "item", SUBITEM: "sub-item"}


@dataclass
class Node:
    kind: str
    key: str = ""
    label: str = ""                       # 原文標示：條號、「一、」「（一）」，項為空
    index: int = 0                        # 在父節點中的序位（1 起算）
    own_text: str = ""                    # 節點自身原文（不含子節點）
    tokens: list[L.Token] = field(default_factory=list)
    children: list["Node"] = field(default_factory=list)
    span: tuple[int, int] = (0, 0)        # 在條文原文中的起訖（Article 層級以下有效）
    english: bool = False

    @property
    def text(self) -> str:
        """節點完整原文（自身 + 子節點，以換行連接）。"""
        parts = [self.own_text] if self.own_text else []
        parts += [c.text for c in self.children]
        return "\n".join(parts)

    @property
    def norm(self) -> str:
        return norm_text(self.text)

    @property
    def own_norm(self) -> str:
        return norm_text(self.own_text)

    def walk(self):
        yield self
        for c in self.children:
            yield from c.walk()

    def find(self, key: str) -> "Node | None":
        for n in self.walk():
            if n.key == key:
                return n
        return None

    def describe(self) -> str:
        """人可讀的位置標示，例如「第2項第1款」。"""
        return position_label(self.key, self.english)

    def pretty(self, indent: int = 0) -> str:
        head = f"{'  ' * indent}{self.kind}[{self.key}] {self.label} {self.own_text[:30]!r}"
        return "\n".join([head] + [c.pretty(indent + 1) for c in self.children])


# 型別別名（介面文件使用的名稱）
LawNode = Node
ArticleNode = Node


def position_label(key: str, english: bool = False) -> str:
    """由正規化鍵產生位置標示："20-1/p2/s3/i1" → 「第2項第3款第1目」；英文 → "paragraph 2 point (c)"。"""
    parts = key.split("/")[1:]
    out = []
    for p in parts:
        for kind, pre in sorted(_KEY_PREFIX.items(), key=lambda kv: -len(kv[1])):
            if p.startswith(pre) and p[len(pre):].isdigit():
                n = int(p[len(pre):])
                if english:
                    out.append(f"{_EN_UNIT[kind]} {n}" if kind != SUBPARAGRAPH else f"point ({chr(96 + n)})")
                else:
                    out.append(f"第{n}{_ZH_UNIT[kind]}")
                break
    return (" " if english else "").join(out)


_LEVEL = {L.SUBPARA_MARK: SUBPARAGRAPH, L.ITEM_MARK: ITEM, L.SUBITEM_MARK: SUBITEM,
          L.PARA_MARK: PARAGRAPH, L.POINT_MARK: SUBPARAGRAPH}
_DEPTH = {PARAGRAPH: 1, SUBPARAGRAPH: 2, ITEM: 3, SUBITEM: 4}


def _split_lines(tokens: list[L.Token]) -> list[list[L.Token]]:
    lines: list[list[L.Token]] = [[]]
    for t in tokens:
        if t.type == L.PARA_BREAK:
            lines.append([])
        else:
            lines[-1].append(t)
    return lines


def parse_article(article: Article) -> Node:
    """把一條 Article 解析成 ArticleNode（含項款目子樹）。"""
    text = article.text or ""
    english = L.is_english(text)
    akey = article_key(article.no)
    tokens = L.lex(text, english=english)
    art = Node(ARTICLE, key=akey, label=article.no, tokens=L.lex_heading(article.no),
               span=(0, len(text)), english=english)

    stack: list[Node] = [art]       # 目前路徑（Article, Paragraph, Subparagraph, ...）
    en_numbered = english and any(t.type == L.PARA_MARK for t in tokens)

    def add_child(parent: Node, kind: str, label: str, line_toks: list[L.Token], s: int, e: int, own: str) -> Node:
        idx = len(parent.children) + 1
        node = Node(kind, key=f"{parent.key}/{_KEY_PREFIX[kind]}{idx}", label=label, index=idx,
                    own_text=own, tokens=line_toks, span=(s, e), english=english)
        parent.children.append(node)
        return node

    for line_toks in _split_lines(tokens):
        if not line_toks:
            continue
        s, e = line_toks[0].start, line_toks[-1].end
        raw = text[s:e]
        if not raw.strip():
            continue
        mark = next((t for t in line_toks if t.type in L.STRUCTURAL), None)
        kind = _LEVEL[mark.type] if mark else None

        if english and not en_numbered and kind is None:
            kind = PARAGRAPH        # 無編號英文條文：每行一項
        if english and en_numbered and kind is None:
            # 編號項之後的未編號行：視為目前節點的續行
            cur = stack[-1] if len(stack) > 1 else None
            if cur is None:
                stack[1:] = [add_child(art, PARAGRAPH, "", line_toks, s, e, raw)]
            else:
                cur.own_text += "\n" + raw
                cur.tokens += line_toks
                cur.span = (cur.span[0], e)
            continue
        if kind is None:
            kind = PARAGRAPH        # 中文：無標記的行 = 新的一項

        depth = _DEPTH[kind]
        # 找到深度 depth-1 的父節點；缺層時補隱含節點（例如條文直接以「一、」開頭）
        while len(stack) > depth:
            stack.pop()
        while len(stack) < depth:
            parent = stack[-1]
            implicit_kind = [k for k, d in _DEPTH.items() if d == len(stack)][0]
            stack.append(add_child(parent, implicit_kind, "", [], s, s, ""))
        node = add_child(stack[-1], kind, mark.text if mark else "", line_toks, s, e, raw)
        stack.append(node)
    return art


def parse(doc: LawDocument) -> Node:
    """把整部 LawDocument 解析成 LawNode。Article.chapter 有值時插入 Chapter 層（連續相同章名合併）。"""
    law = Node(LAW, key=doc.name, label=doc.name)
    current_chapter: Node | None = None
    for a in doc.articles:
        art = parse_article(a)
        if a.chapter:
            if current_chapter is None or current_chapter.label != a.chapter:
                current_chapter = Node(CHAPTER, key=f"ch{len([c for c in law.children if c.kind == CHAPTER]) + 1}",
                                       label=a.chapter, own_text="")
                law.children.append(current_chapter)
            current_chapter.children.append(art)
        else:
            current_chapter = None
            law.children.append(art)
    return law


def articles_of(law: Node) -> list[Node]:
    return [n for n in law.walk() if n.kind == ARTICLE]


def structure_summary(art: Node) -> dict[str, int]:
    """各層節點數，例如 {"項": 3, "款": 4, "目": 0, "子目": 0}。"""
    d = {u: 0 for u in _ZH_UNIT.values()}
    for n in art.walk():
        if n.kind in _ZH_UNIT and (n.own_text or n.children):
            d[_ZH_UNIT[n.kind]] += 1
    return d
