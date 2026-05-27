# 机构新闻抓取策略总结

> 5 家第一梯队量子机构官网的爬取经验，261 篇文章入库。按网站类型分为 4 种抓取模式。

---

## 一、四种抓取模式

### 类型 A：HTML 博客列表 + 模板翻页（IBM）

**特征**：服务器端渲染文章列表，但翻页用 `<button>` 而非 `<a>` 标签，JS 驱动。IBM 有 `?page=N` 翻页（共 5 页 81 篇），`data-page` 属性挂在 `<button>` 上，无法从 `href` 提取。

**策略**：`page_url_template: '?page={n}'` 手动构造翻页 URL。
- 日期在列表页即可提取（`DD Mon YYYY` 纯文本格式）
- `_find_next_page()` 的 `page_template` 参数自动拼接 `?page=2`, `?page=3`...

**示例配置**：
```python
{'url': 'https://www.ibm.com/quantum/blog',
 'url_pattern': '/quantum/blog/',
 'quantum_native': True,
 'max_pages': 5,
 'page_url_template': '?page={n}'}   # <-- button 翻页的替代方案
```

### 类型 B：HTML 博客列表 + 链接翻页（Quantinuum、Microsoft）

**特征**：文章列表有 `<a>` 标签分页，URL 格式 `?<hash>_page=N`，链接文本为 "View More"。Microsoft 的翻页也被自动识别。

**策略**：
- `_find_next_page()` 优先匹配 `href` 中 `_page=N` 格式
- 跨页去重（`seen_urls` 集合）
- **注意**：Quantinuum 列表页标题破碎（"Read our blogpost"），需从详情页 `og:title` 补全

**示例配置**：
```python
{'url': 'https://www.quantinuum.com/news/blog',
 'url_pattern': '/blog/',
 'quantum_native': True,
 'max_pages': 5}
```

### 类型 C：Sitemap 全量扫描（Google）

**特征**：Google 博客无量子专属栏目，量子文章散落于 `/innovation-and-ai/` 各子路径。博客首页 RSS 仅 20 条且无量子。但 `sitemap.xml` 含全站 11296 个 URL，搜索 "quantum" 命中 37 个。

**策略**：`crawl_sitemap()` 解析 XML sitemap。
- `<lastmod>` 直接作为发布日期
- URL slug 初步作为标题，`fetch_detail()` 从 `og:title` 补全真实标题
- **优点**：全量覆盖，不依赖列表页结构
- **缺点**：标题需二次请求详情页

**示例配置**：
```python
{'type': 'sitemap',
 'url': 'https://blog.google/en-us/sitemap.xml',
 'url_pattern': 'quantum',
 'quantum_native': True}
```

### 类型 D：Atom/RSS Feed（NVIDIA）

**特征**：NVIDIA HTML 列表页只有 15 篇，翻页 `page/2/` 返回相同内容（JS 无限滚动加载）。但 Atom Feed（`/feed/`）包含 **47 篇**全量文章，数据结构干净（标题、链接、发布日期都在 XML 中）。

**策略**：`crawl_atom()` 解析 Atom XML。
- 从 `<entry>` 提取 `<title>`、`<link>`、`<published>`
- 无需二次请求详情页取标题/日期
- **推荐**：当网站提供 Feed 时优先使用，数据质量最高

**示例配置**：
```python
{'type': 'atom',
 'url': 'https://developer.nvidia.com/blog/tag/quantum-computing/feed/',
 'url_pattern': '/blog/',
 'quantum_native': True}
```

---

## 二、通用技术经验

### 2.1 日期提取四层回退

| 优先级 | 来源 | 示例 |
|--------|------|------|
| 1 | `<meta>` 标签 | `article:published_time`, `date`, `published` |
| 2 | `<time>` 标签 | `datetime` 属性 |
| 3 | JSON-LD | `datePublished`, `dateModified` |
| 4 | 正文正则 | `DD Mon YYYY` / `Month DD, YYYY` / `YYYY-MM-DD` |

**踩坑**：IBM 博客日期在 `<p>` 纯文本 `16 Mar 2026`，前三层全漏。

### 2.2 标题修正

列表页标题不可靠（按钮文字、卡片标签、"Read our blogpost" 等）。`_extract_page_title()` 优先取 `og:title` → `<meta name="title">` → `<h1>` → `<title>`。`main()` 中选更长的标题入库。

### 2.3 URL 过滤铁律

**先补全绝对路径，再匹配 `url_pattern`**。

```python
# 正确顺序
if href.startswith('/'):
    href = urljoin(base_url, href)
if url_pattern not in href:
    continue
```

**踩坑**：早期代码对 `/` 开头的链接不做过滤直接放行，Quantinuum 产品页/About 页全量漏入。

### 2.4 翻页链接识别

**踩坑**：`'next' in text` 误匹配 "GuppyProgram the **next** generation..."。

**正确做法**：
1. 优先用 `page_url_template` 手动构造（button 翻页时）
2. 其次检查 `href` 中 `_page=N` 或 `page=N`
3. 最后才用文本关键词 `view more` / `older`

### 2.5 量子相关性过滤

**规则**：量子原生公司（IBM/Quantinuum/Microsoft/NVIDIA）设 `quantum_native: True` 跳过 LLM 过滤。

**原因**：列表标题破碎 → LLM 无法判断 → 大量误拒。这些公司博客 100% 量子相关，过滤无意义。

### 2.6 内容提取

`_extract_body()` 按优先级找正文容器：`<article>` → `[role=main]` → `<main>` → `[class*=article-body]` → `[class*=post-body]` → ... 找不到则清理全页面文本。

提取前后对比：NVIDIA 平均 4333 vs 其他机构 3000 字符（提升 44%）。

### 2.7 双语中文摘要

新文章入库时 LLM 实时生成一句话中文摘要（≤100 字），存入 `summary_cn` 字段。存量文章用 `backfill_cn.py` 批量补全。

### 2.8 Windows 编码

GBK 终端乱码中文和特殊字符。
- 脚本加 `sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')`
- 文件统一 UTF-8

---

## 三、决策树：新机构选哪种模式

```
新机构官网
 ├─ 有 sitemap.xml？ → 类型 C（全量 URL + 日期）
 ├─ 有 Atom/RSS Feed？ → 类型 D（最优，结构干净）
 ├─ 有 `<a>` 翻页链接？ → 类型 B（自动识别 _page=N）
 ├─ 有 `<button>` 翻页？ → 类型 A（page_url_template 硬编码）
 └─ 单页够用？ → 类型 A（无需翻页配置）
```

---

## 四、源配置速查（当前 5 家）

| 机构 | 类型 | 文章数 | URL | 翻页方式 |
|------|------|--------|-----|----------|
| IBM Quantum | blog+template | 81 | ibm.com/quantum/blog | `?page={n}` (5页) |
| Microsoft Azure Quantum | blog+pagination | 58 | cloudblogs.microsoft.com/quantum/ | `_page=N` 自动 |
| NVIDIA Quantum | atom | 45 | developer.nvidia.com/.../feed/ | Feed 全量 |
| Quantinuum | blog+pagination | 40 | quantinuum.com/news/blog | `_page=N` (5页) |
| Google Quantum AI | sitemap | 37 | blog.google/.../sitemap.xml | Sitemap 全量 |
| **合计** | | **261** | | |

---

## 五、待扩展

1. **增量更新**：全量拉取后，只抓新文章（对比 DB 中已有 URL）
2. **第二梯队**：IonQ、Rigetti、D-Wave、QuEra、Xanadu、本源量子 等
3. **定时调度**：Windows Task Scheduler 每日运行 `.bat`
4. **存量摘要补全**：`backfill_cn.py` 给旧文章补中文摘要（230+ 篇）
5. **内容进一步清洗**：当前 body 提取仍有面包屑/元数据残留，可加 LLM 清洗
