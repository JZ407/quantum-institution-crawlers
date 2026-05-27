# 机构新闻抓取策略总结

> 5 家量子机构官网的爬取经验，按网站类型分类。

---

## 一、网站类型与对应策略

### 类型 A：标准博客列表页（IBM、NVIDIA）

**特征**：服务器端渲染的文章列表，URL 含固定路径前缀（如 `/quantum/blog/`），无前端分页/懒加载。

**策略**：
- `url_pattern` 设为博客路径前缀即可精确过滤
- 不需要翻页（IBM 文章量有限，一页够用）
- 日期在列表页即可提取（YYYY-MM-DD 或 DD Mon YYYY 格式）

**示例配置**：
```python
{'url': 'https://www.ibm.com/quantum/blog',
 'url_pattern': '/quantum/blog/',
 'quantum_native': True}
```

### 类型 B：分页博客列表（Quantinuum、Microsoft）

**特征**：文章列表有分页，URL 格式 `?<hash>_page=N`。链接文本通常为 "View More"。

**策略**：
- 设 `max_pages` 控制最大翻页数
- `_find_next_page()` 识别 `_page=N` 格式的分页链接
- 跨页去重（`seen_urls` 集合）
- **注意**：列表页标题可能不完整（"Read our blogpost"），需从详情页 `og:title` 补全

**示例配置**：
```python
{'url': 'https://www.quantinuum.com/news/blog',
 'url_pattern': '/blog/',
 'quantum_native': True,
 'max_pages': 5}
```

### 类型 C：无独立量子列表页（Google）

**特征**：Google 博客无量子专属栏目，量子文章散落于 `/innovation-and-ai/` 各子路径。博客首页 RSS 20 条中无量子相关。

**策略**：解析 `sitemap.xml`。
- `type: 'sitemap'` 触发 `crawl_sitemap()`
- `url_pattern` 作为关键词在 URL 中搜索
- `<lastmod>` 直接作为发布日期
- **优点**：全量覆盖，不依赖列表页结构
- **缺点**：标题需从详情页 `og:title` 提取，且需多一次 HTTP 请求

**示例配置**：
```python
{'type': 'sitemap',
 'url': 'https://blog.google/en-us/sitemap.xml',
 'url_pattern': 'quantum',
 'quantum_native': True}
```

---

## 二、通用技术经验

### 2.1 日期提取

**问题**：各网站日期格式/位置不统一。

**优先级链**（`_extract_date`）：
1. `<meta>` 标签（`article:published_time`、`date`、`published`）
2. `<time>` 标签（`datetime` 属性）
3. JSON-LD 结构化数据（`datePublished`、`dateModified`）
4. 正文正则（`DD Mon YYYY`、`Month DD, YYYY`、`YYYY-MM-DD`）

**踩坑**：IBM 博客日期藏在纯文本 `<p>` 中（`16 Mar 2026`），前三层全漏。

### 2.2 标题修正

**问题**：列表页链接文本可能不是文章标题（按钮文字、卡片标签等）。

**方案**：`_extract_page_title()` 从详情页提取：
1. `og:title`（最准确）
2. `<meta name="title">`
3. `<h1>`
4. `<title>`

`main()` 中比较列表标题与详情标题，选更长的。

### 2.3 URL 过滤

**核心规则**：**先补全绝对路径，再匹配 `url_pattern`**。

```python
# 正确顺序
if href.startswith('/'):
    href = urljoin(base_url, href)
if url_pattern not in href:
    continue  # 不是目标文章
```

**踩坑**：早期代码对 `/` 开头的链接不做过滤直接放行，导致产品页、About 页全量漏入。

### 2.4 翻页链接识别

**踩坑**：`'next' in text` 会误匹配 "GuppyProgram the **next** generation..."。

**正确做法**：优先检查 `href` 中的分页模式（`_page=\d+`），再结合文本关键词。

### 2.5 量子相关性过滤

**结论**：不要对量子原生公司（IBM/Quantinuum/Microsoft/NVIDIA）启用 LLM 过滤。

**原因**：
- 列表页标题破碎（"Read our blogpost"）→ LLM 无法判断 → 大量误拒
- 这些公司博客内容 100% 量子相关，过滤无意义

**适用场景**：仅对综合性博客（Google）保留 LLM 过滤，但 sitemap 模式下已不需过滤。

### 2.6 编码与输出

Windows GBK 终端会乱码中文和特殊字符（`•`、`✓` 等）。
- 脚本中加 `sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')`
- 文件读写统一 UTF-8

---

## 三、源配置速查

| 机构 | 类型 | URL | url_pattern | 特殊配置 |
|------|------|-----|-------------|----------|
| IBM Quantum | blog | ibm.com/quantum/blog | /quantum/blog/ | - |
| Quantinuum | blog | quantinuum.com/news/blog | /blog/ | max_pages=5 |
| Google Quantum AI | sitemap | blog.google/en-us/sitemap.xml | quantum | - |
| Microsoft | blog | cloudblogs.microsoft.com/quantum/ | /quantum/ | - |
| NVIDIA | blog | developer.nvidia.com/blog/tag/quantum-computing/ | /blog/ | - |

---

## 四、待扩展方向

1. **增量抓取**：全量拉取后，后续只抓新文章（对比 sitemap 或最新 N 篇）
2. **更多机构**：IonQ、Rigetti、QuEra、Xanadu、D-Wave、Origin Quantum（本源量子）等
3. **RSS 监控**：Google、NVIDIA 提供 RSS，可用于增量更新
4. **定时任务**：Windows Task Scheduler 每日运行 `crawl_institutions.py`
5. **反爬对抗**：部分网站可能需 Playwright 无头浏览器（如光子盒），当前 5 家 requests 足够
