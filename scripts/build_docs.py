#!/usr/bin/env python3
"""Build the dependency-free, single-file Flora manual.

The input is an intentionally small CommonMark-compatible Markdown subset.
It renders source text only; embedded HTML is escaped. No network or JS build
system is required. Outputs may be rebuilt from the release source tree.
"""

from __future__ import annotations

import argparse
import html
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def inline(text: str) -> str:
    chunks = re.split(r"(`[^`]*`)", text)
    rendered = []
    for chunk in chunks:
        if chunk.startswith("`") and chunk.endswith("`"):
            rendered.append("<code>" + html.escape(chunk[1:-1]) + "</code>")
            continue
        chunk = html.escape(chunk)
        chunk = re.sub(
            r"\[([^\]]+)\]\(([^\s)]+)\)",
            lambda m: (
                '<a href="' + html.escape(m.group(2), quote=True) + '">' + m.group(1) + "</a>"
            ),
            chunk,
        )
        chunk = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", chunk)
        rendered.append(chunk)
    return "".join(rendered)


def render_markdown(source: str, prefix: str) -> str:
    lines = source.splitlines()
    out: list[str] = []
    paragraph: list[str] = []
    list_kind: str | None = None
    index = 0
    heading_index = 0

    def flush_paragraph() -> None:
        if paragraph:
            out.append("<p>" + inline(" ".join(paragraph)) + "</p>")
            paragraph.clear()

    def close_list() -> None:
        nonlocal list_kind
        if list_kind:
            out.append("</" + list_kind + ">")
            list_kind = None

    while index < len(lines):
        line = lines[index]
        if line.startswith("```"):
            flush_paragraph()
            close_list()
            language = line[3:].strip()
            code: list[str] = []
            index += 1
            while index < len(lines) and not lines[index].startswith("```"):
                code.append(lines[index])
                index += 1
            out.append(
                '<div class="code-wrap"><div class="code-head"><span>'
                + html.escape(language or "text")
                + '</span><button class="copy" type="button">复制代码</button></div><pre><code>'
                + html.escape("\n".join(code))
                + "</code></pre></div>"
            )
        elif re.match(r"^#{1,6} ", line):
            flush_paragraph()
            close_list()
            match = re.match(r"^(#{1,6}) (.*)$", line)
            assert match
            level = min(6, len(match.group(1)) + 1)
            heading_index += 1
            out.append(
                f'<h{level} id="{prefix}-h{heading_index}">'
                + inline(match.group(2))
                + f"</h{level}>"
            )
        elif (
            line.startswith("|")
            and index + 1 < len(lines)
            and re.match(r"^\|[ :|\-]+\|?$", lines[index + 1])
        ):
            flush_paragraph()
            close_list()
            headers = [cell.strip() for cell in line.strip("|").split("|")]
            out.append(
                '<div class="table-wrap"><table><thead><tr>'
                + "".join("<th>" + inline(cell) + "</th>" for cell in headers)
                + "</tr></thead><tbody>"
            )
            index += 2
            while index < len(lines) and lines[index].startswith("|"):
                cells = [cell.strip() for cell in lines[index].strip("|").split("|")]
                out.append(
                    "<tr>" + "".join("<td>" + inline(cell) + "</td>" for cell in cells) + "</tr>"
                )
                index += 1
            out.append("</tbody></table></div>")
            index -= 1
        elif line.startswith("> "):
            flush_paragraph()
            close_list()
            quotes = []
            while index < len(lines) and lines[index].startswith("> "):
                quotes.append(lines[index][2:])
                index += 1
            out.append('<aside class="note">' + inline(" ".join(quotes)) + "</aside>")
            index -= 1
        elif re.match(r"^(- |\d+\. )", line):
            flush_paragraph()
            new_kind = "ul" if line.startswith("- ") else "ol"
            if list_kind != new_kind:
                close_list()
                list_kind = new_kind
                out.append("<" + list_kind + ">")
            content = re.sub(r"^(- |\d+\. )", "", line)
            out.append("<li>" + inline(content) + "</li>")
        elif not line.strip():
            flush_paragraph()
            if index + 1 >= len(lines) or not re.match(r"^(- |\d+\. )", lines[index + 1]):
                close_list()
        elif line.strip() == "---":
            flush_paragraph()
            close_list()
            out.append("<hr>")
        else:
            close_list()
            paragraph.append(line.strip())
        index += 1
    flush_paragraph()
    close_list()
    return "\n".join(out)


STYLE = r"""
:root{--bg:#f6f8fc;--surface:#fff;--ink:#152235;--muted:#53657b;--line:#dce3ee;--accent:#3458db;--soft:#ecf0ff;--code:#142039;--codeInk:#e8efff;--sidebar:280px}
[data-theme=dark]{--bg:#101623;--surface:#172031;--ink:#e6edf9;--muted:#afbbcf;--line:#303e55;--accent:#a4b6ff;--soft:#222e4e;--code:#0b1220;--codeInk:#e8efff}
*{box-sizing:border-box}html{scroll-behavior:smooth;scroll-padding-top:32px}body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.8 system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI","Noto Sans CJK SC","Microsoft YaHei",sans-serif}a{color:var(--accent);text-decoration:none}a:hover{text-decoration:underline}button,input{font:inherit}button{cursor:pointer}button:focus-visible,a:focus-visible,input:focus-visible{outline:3px solid var(--accent);outline-offset:3px}.sidebar{position:fixed;inset:0 auto 0 0;width:var(--sidebar);padding:24px 20px;background:var(--surface);border-right:1px solid var(--line);overflow-y:auto}.brand{font-size:23px;font-weight:800;letter-spacing:-.7px}.version{display:inline-block;margin-left:8px;padding:1px 7px;background:var(--soft);border-radius:6px;font-size:12px;color:var(--accent);vertical-align:middle}.tagline{color:var(--muted);font-size:12px;margin:6px 0 18px}.search{width:100%;border:1px solid var(--line);border-radius:8px;background:var(--bg);padding:9px 11px;color:var(--ink);font-size:13px}.search-status{font-size:11px;color:var(--muted);min-height:24px}.nav-group{margin-top:19px;font-size:11px;font-weight:750;text-transform:uppercase;letter-spacing:1.1px;color:var(--muted)}.nav-link{display:block;color:var(--muted);padding:5px 9px;margin:2px -1px;border-radius:6px;font-size:13px;line-height:1.6}.nav-link:hover,.nav-link.active{background:var(--soft);color:var(--accent);text-decoration:none}.sidebar-tools{display:flex;gap:6px;flex-wrap:wrap;margin-top:22px}.sidebar-tools button,.mobile-nav{border:1px solid var(--line);background:var(--surface);color:var(--ink);border-radius:6px;padding:5px 10px;font-size:12px}main{margin-left:var(--sidebar);padding:42px 5vw 64px;max-width:calc(1180px + var(--sidebar))}.hero{padding:34px 38px 36px;border:1px solid var(--line);border-radius:18px;background:var(--surface);margin-bottom:36px}.eyebrow{color:var(--accent);font-size:12px;font-weight:750;letter-spacing:2px}.hero h1{font-size:38px;line-height:1.2;margin:14px 0 18px;letter-spacing:-1.3px}.hero p{max-width:780px;color:var(--muted);margin:12px 0}.pills{display:flex;gap:8px;flex-wrap:wrap;margin-top:22px}.pill{border:1px solid var(--line);border-radius:30px;padding:3px 11px;font-size:12px;color:var(--muted)}.chapter{background:var(--surface);border:1px solid var(--line);border-radius:14px;padding:30px 38px;margin:25px 0;scroll-margin-top:28px}.chapter.hidden{display:none}.chapter-label{color:var(--accent);font-size:11px;font-weight:750;letter-spacing:1.4px}.chapter h2{margin:7px 0 26px;font-size:29px;line-height:1.4;letter-spacing:-.5px}.chapter h3{margin:33px 0 12px;font-size:20px;line-height:1.5;padding-top:6px}.chapter h4{margin:25px 0 9px;font-size:17px}.chapter p{margin:13px 0}.chapter ul,.chapter ol{padding-left:25px;margin:13px 0}.chapter li{margin:6px 0}code{font:13px/1.7 ui-monospace,SFMono-Regular,Consolas,"Liberation Mono",monospace;word-break:break-word}p code,li code,td code,th code,h3 code{background:var(--soft);color:var(--accent);border-radius:4px;padding:2px 5px}.code-wrap{margin:18px 0;border-radius:9px;overflow:hidden;border:1px solid var(--line);background:var(--code)}.code-head{display:flex;justify-content:space-between;align-items:center;padding:5px 13px;font:11px/1.8 ui-monospace,monospace;color:#a5b7d8;border-bottom:1px solid #33435f}.copy{border:0;background:transparent;color:#c4d3ed;padding:3px 7px;font-size:11px}.copy:hover{color:#fff}pre{margin:0;padding:16px 18px;overflow:auto;color:var(--codeInk);line-height:1.7;tab-size:4}pre code{white-space:pre;word-break:normal}.table-wrap{overflow:auto;margin:20px 0;border:1px solid var(--line);border-radius:8px}table{width:100%;border-collapse:collapse;font-size:13px;line-height:1.75}th{text-align:left;background:var(--soft);font-weight:700}th,td{padding:10px 13px;border-bottom:1px solid var(--line);vertical-align:top}tr:last-child td{border-bottom:0}.note{border-left:4px solid var(--accent);background:var(--soft);padding:13px 17px;margin:20px 0;border-radius:0 8px 8px 0;font-size:14px}.chapter-footer{border-top:1px solid var(--line);margin-top:28px;padding-top:14px;font-size:12px;color:var(--muted)}.mobile-nav{display:none}.empty{display:none;padding:40px;text-align:center;color:var(--muted)}footer{font-size:12px;color:var(--muted);margin-top:35px}hr{border:0;border-top:1px solid var(--line);margin:25px 0}@media(max-width:950px){:root{--sidebar:236px}main{padding:26px 24px}.chapter,.hero{padding:24px}.hero h1{font-size:32px}.chapter h2{font-size:25px}}@media(max-width:700px){.sidebar{display:none;z-index:20;width:290px;box-shadow:0 0 30px #0003}.sidebar.open{display:block}main{margin-left:0;padding:62px 14px 30px}.mobile-nav{display:block;position:fixed;top:12px;left:14px;z-index:30;background:var(--surface)}.chapter,.hero{padding:20px 17px}.hero h1{font-size:29px}.chapter h2{font-size:24px}pre{padding:12px}body{font-size:15px}}@media print{.sidebar,.mobile-nav,.copy,.hero .pills,.chapter-footer,footer{display:none!important}main{margin:0;padding:0;max-width:none}.hero,.chapter{border:0;border-radius:0;padding:0;margin:0 0 24px}.hero{break-after:page}.chapter{break-before:page}.chapter.hidden{display:block!important}body{font:10.5pt/1.6 "Noto Sans CJK SC","Microsoft YaHei",sans-serif;color:#111;background:#fff}h2,h3,h4{break-after:avoid}pre{white-space:pre-wrap!important;background:#f2f4f8;color:#17243b;overflow:visible;font-size:8pt}.code-wrap{background:#f2f4f8;border-color:#ccc;break-inside:avoid}.code-head{color:#344;border-color:#ccc}pre code{white-space:pre-wrap;word-break:break-word}table{font-size:8.5pt}.note{break-inside:avoid}.chapter h2{font-size:22pt}.chapter h3{font-size:14pt}a{color:#2445a2}}
"""
SCRIPT = r"""
(()=>{
 const search=document.getElementById('search');
 const chapters=[...document.querySelectorAll('.chapter')];
 const links=[...document.querySelectorAll('.nav-link')];
 const status=document.getElementById('search-status');
 function filter(){const q=search.value.trim().toLocaleLowerCase();let count=0;chapters.forEach(ch=>{const hit=!q||ch.textContent.toLocaleLowerCase().includes(q);ch.classList.toggle('hidden',!hit);const link=links.find(x=>x.getAttribute('href')==='#'+ch.id);if(link)link.style.display=hit?'block':'none';count+=hit?1:0});status.textContent=q?`${count} / ${chapters.length} 个章节匹配`:'支持中文、API 名称与命令';document.getElementById('empty').style.display=count?'none':'block'}
 search.addEventListener('input',filter);filter();
 document.addEventListener('keydown',e=>{if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='k'){e.preventDefault();document.querySelector('.sidebar').classList.add('open');search.focus()}if(e.key==='Escape'){search.value='';filter();document.querySelector('.sidebar').classList.remove('open')}});
 document.querySelectorAll('.copy').forEach(button=>button.addEventListener('click',async()=>{const code=button.closest('.code-wrap').querySelector('pre code').textContent;try{if(navigator.clipboard&&window.isSecureContext){await navigator.clipboard.writeText(code)}else{const area=document.createElement('textarea');area.value=code;area.style.position='fixed';area.style.opacity='0';document.body.append(area);area.select();if(!document.execCommand('copy'))throw Error('copy');area.remove()}button.textContent='已复制';setTimeout(()=>button.textContent='复制代码',1400)}catch{button.textContent='请选中代码复制'}}));
 document.getElementById('theme').addEventListener('click',()=>{const next=document.documentElement.dataset.theme==='dark'?'light':'dark';document.documentElement.dataset.theme=next;try{localStorage.setItem('flora-theme',next)}catch{}});
 try{document.documentElement.dataset.theme=localStorage.getItem('flora-theme')||'light'}catch{}
 document.getElementById('print').addEventListener('click',()=>window.print());
 document.getElementById('expand').addEventListener('click',()=>{search.value='';filter()});
 document.getElementById('menu').addEventListener('click',()=>document.querySelector('.sidebar').classList.toggle('open'));
 links.forEach(link=>link.addEventListener('click',()=>{document.querySelector('.sidebar').classList.remove('open');links.forEach(x=>x.classList.remove('active'));link.classList.add('active')}));
 if('IntersectionObserver'in window){const io=new IntersectionObserver(entries=>{entries.forEach(e=>{if(e.isIntersecting){links.forEach(x=>x.classList.toggle('active',x.getAttribute('href')==='#'+e.target.id))}})},{rootMargin:'-10% 0px -75% 0px'});chapters.forEach(x=>io.observe(x))}
})();
"""


def build(output: Path) -> None:
    manifest = json.loads((ROOT / "docs" / "manifest.json").read_text(encoding="utf-8"))
    nav: list[str] = []
    sections: list[str] = []
    group = None
    for i, chapter in enumerate(manifest["chapters"], 1):
        if group != chapter["group"]:
            group = chapter["group"]
            nav.append('<div class="nav-group">' + html.escape(group) + "</div>")
        ident = chapter["id"]
        title = chapter["title"]
        source = (ROOT / "docs" / chapter["file"]).read_text(encoding="utf-8")
        nav.append('<a class="nav-link" href="#' + ident + '">' + html.escape(title) + "</a>")
        sections.append(
            '<section class="chapter" id="'
            + ident
            + '"><div class="chapter-label">'
            + f"{i:02d} / "
            + html.escape(group)
            + "</div>"
            + render_markdown(source, ident)
            + '<div class="chapter-footer">Flora 0.1.0 · 源文档：docs/'
            + html.escape(chapter["file"])
            + ' · <a href="#top">返回顶部</a></div></section>'
        )
    page = (
        """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="description" content="Flora 0.1.0 中文操作手册：直接使用 CLI、Python 函数、工作目录、模型配置、会话与恢复；附完整内部语言和研究参考。"><title>Flora 0.1.0 — 完整操作手册</title><style>"""
        + STYLE
        + """</style></head><body><button id="menu" class="mobile-nav" type="button" aria-label="打开导航">目录 / 搜索</button><aside class="sidebar"><a class="brand" href="#top">Flora</a><span class="version">0.1.0</span><p class="tagline">芙洛拉 · 任务 · 可检查的执行</p><label for="search" class="tagline">搜索手册 · Ctrl / ⌘ K</label><input class="search" id="search" type="search" placeholder="例如：ask、工作目录、恢复"><div id="search-status" class="search-status" aria-live="polite"></div><nav aria-label="手册目录">"""
        + "".join(nav)
        + """</nav><div class="sidebar-tools"><button id="theme" type="button">明暗主题</button><button id="expand" type="button">显示全部</button><button id="print" type="button">打印 / PDF</button></div></aside><main id="top"><header class="hero"><div class="eyebrow">USER &amp; DEVELOPER DOCUMENTATION · 中文</div><h1>给出任务、模型和工具。<br>直接开始执行。</h1><p>用命令行提交任务，或在 Python 中传入普通函数。程序生成、双重控制和可综合合约在内部运行；你不需要先学习内部语言。</p><div class="pills"><span class="pill">Python 3.11+</span><span class="pill">内核仅标准库</span><span class="pill">CLI / Python 直接使用</span><span class="pill">无外部文档资源</span><span class="pill">独立子 agent 与持久化状态</span></div><p>先读「安装与五分钟开始」或「Python 快速开始」，接着按需查工作目录、会话与模型配置。内部语言和研究机制完整保留在后半部分；真实模型任务需要你自己的模型服务。</p></header><div id="empty" class="empty">没有匹配章节。尝试更短的中文词或完整 API 名称。</div>"""
        + "".join(sections)
        + """<footer>文档结构参考成熟开源库的「入门 → 教程 → 指南 → API → 参考」分层；内容与实现由本项目独立编写。此文件可离线打开，不加载分析脚本、字体或 CDN。</footer></main><script>"""
        + SCRIPT
        + "</script></body></html>"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(page, encoding="utf-8")
    print(f"{output} ({output.stat().st_size:,} bytes; {len(manifest['chapters'])} chapters)")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "manual.html")
    args = parser.parse_args()
    build(args.output)


if __name__ == "__main__":
    main()
