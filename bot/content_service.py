"""内容处理 — 移植自 iOS 端 Metadata.swift + GitHubService.swift 路径生成逻辑"""

import random
import re
from datetime import datetime, timezone, timedelta

_CST = timezone(timedelta(hours=8))


# 缓存正则 — 对应 GitHubService.swift 第 23-30 行
_RE_FRONTMATTER = re.compile(r"^---[\s\S]*?---\n*")
_RE_MD_IMAGE = re.compile(r"!\[.*?\]\(.*?\)")
_RE_MD_LINK = re.compile(r"\[([^\]]+)\]\([^\)]+\)")
_RE_MD_SYMBOL = re.compile(r"[#*`_~\->|/]")
_RE_WHITESPACE = re.compile(r"\s+")
_RE_CJK_ALPHA = re.compile(r"[\u4e00-\u9fa5a-zA-Z]")


def generate_frontmatter(pub_date: datetime | None = None) -> str:
    """生成 Essay Frontmatter — 移植自 Metadata.toFrontmatter(.essay)"""
    if pub_date is None:
        pub_date = datetime.now(_CST)
    date_str = pub_date.strftime("%Y-%m-%d %H:%M:%S")
    return f'---\npubDate: "{date_str}"\n---\n\n'


def generate_file_path(content: str, title: str | None = None, now: datetime | None = None, content_type: str = "essays") -> str:
    """生成文件路径 — 忠实移植 GitHubService.swift 第 287-317 行"""
    if now is None:
        now = datetime.now(_CST)

    date_prefix = now.strftime("%Y-%m-%d")
    timestamp = now.strftime("%H%M%S") + f"-{random.randint(100, 999)}"

    # 提取纯文本：优先用 title，否则从 content 中提取
    if title:
        plain = title
    else:
        plain = content
        plain = _RE_FRONTMATTER.sub("", plain)
        plain = _RE_MD_IMAGE.sub("", plain)
        plain = _RE_MD_LINK.sub(r"\1", plain)
        plain = _RE_MD_SYMBOL.sub("", plain)
    plain = _RE_WHITESPACE.sub("", plain)

    # 提取前 4 个 CJK/字母字符
    first4 = ""
    count = 0
    for ch in plain:
        if count >= 4:
            break
        if _RE_CJK_ALPHA.match(ch):
            first4 += ch
            count += 1

    if first4:
        return f"src/content/{content_type}/{date_prefix}-{first4}-{timestamp}.md"
    return f"src/content/{content_type}/{date_prefix}-{timestamp}.md"


def assemble_content(body: str, pub_date: datetime | None = None) -> str:
    """组装最终内容: frontmatter + body"""
    return generate_frontmatter(pub_date) + body


def parse_frontmatter(content: str) -> tuple[dict[str, str], str]:
    """分离 frontmatter 和 body，手动解析 key: "value" 键值对"""
    m = _RE_FRONTMATTER.match(content)
    if not m:
        return {}, content

    fm_block = m.group(0)
    body = content[m.end():]

    # 解析 frontmatter 键值对
    fields: dict[str, str] = {}
    for line in fm_block.splitlines():
        line = line.strip()
        if line == "---" or not line:
            continue
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        val = val.strip().strip('"').strip("'")
        fields[key.strip()] = val

    return fields, body


def assemble_content_with_title(body: str, title: str, pub_date: datetime | None = None) -> str:
    """组装 .md 文件内容：保留已有 frontmatter 或生成新的，确保 title 和 pubDate 存在"""
    if pub_date is None:
        pub_date = datetime.now(_CST)
    date_str = pub_date.strftime("%Y-%m-%d %H:%M:%S")

    fields, body_text = parse_frontmatter(body)

    if fields:
        # 已有 frontmatter，仅在缺失时注入
        fields.setdefault("title", title)
        fields.setdefault("pubDate", date_str)
    else:
        # 无 frontmatter，生成新的
        fields = {"title": title, "pubDate": date_str}
        body_text = body

    # 重建 frontmatter
    fm_lines = ["---"]
    for k, v in fields.items():
        fm_lines.append(f'{k}: "{v}"')
    fm_lines.append("---")

    return "\n".join(fm_lines) + "\n\n" + body_text
