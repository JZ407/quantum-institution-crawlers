"""
QuEra scraper final — clicks ALL "View more" buttons until exhausted.
"""
import sys, os, json, time, re
from datetime import datetime
from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup

BASE = "https://www.quera.com"
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "quera")
os.makedirs(OUT, exist_ok=True)


def click_all_view_more(page, max_clicks=30):
    """Click all 'View more' / 'Load more' buttons until no new items or max reached."""
    prev_count = 0
    for i in range(max_clicks):
        buttons = page.query_selector_all(
            'a:has-text("View more"), button:has-text("View more"), '
            '[class*="load-more"]:has-text("View"), .w-pagination-next'
        )
        if not buttons:
            break

        clicked_any = False
        for btn in buttons:
            try:
                if btn.is_visible():
                    btn.scroll_into_view_if_needed()
                    btn.click()
                    page.wait_for_timeout(1500)
                    clicked_any = True
            except:
                pass

        if not clicked_any:
            break

        items_now = len(page.query_selector_all(".w-dyn-item"))
        if items_now == prev_count:
            break
        prev_count = items_now

    return len(page.query_selector_all(".w-dyn-item"))


def extract_all_links(soup):
    """Extract all unique links from w-dyn-items with source context."""
    results = []
    seen = set()
    for item in soup.select(".w-dyn-item"):
        for a in item.select("a[href]"):
            href = (a.get("href") or "").strip()
            if not href or href.startswith("#"):
                continue
            url = BASE + href if href.startswith("/") else href
            if url in seen:
                continue
            seen.add(url)
            title = a.get_text(strip=True)
            if not title or len(title) < 2:
                title = item.get_text(" ", strip=True)[:100]
            results.append({"url": url, "title": title, "raw_text": item.get_text(" ", strip=True)[:300]})
    return results


def categorize(items):
    """Tag items by content clues."""
    for item in items:
        text = item.get("raw_text", "") + item.get("title", "")
        url = item.get("url", "").lower()
        if "arxiv.org" in url:
            item["source_type"] = "scientific_paper"
        elif url.endswith(".pdf"):
            item["source_type"] = "whitepaper"
        elif "quera.com" in url:
            item["source_type"] = "blog"
        else:
            item["source_type"] = "external_link"
    return items


def fetch_blog(page, url):
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(3000)
        soup = BeautifulSoup(page.content(), "html.parser")
    except:
        return {"title": "", "content": ""}

    title = ""
    for sel in ['h1', 'meta[property="og:title"]']:
        el = soup.select_one(sel)
        if el:
            title = el.get("content", "") if sel.startswith("meta") else el.get_text(strip=True)
            if title: break

    body = ""
    for sel in ['.w-richtext', 'article', 'main']:
        el = soup.select_one(sel)
        if el:
            body = el.get_text(separator="\n", strip=True)
            if len(body) > 200: break
    if not body:
        body = (soup.body.get_text(separator="\n", strip=True) if soup.body else "")[:5000]
    for stop in ["Back to all", "Related Posts", "Share this"]:
        idx = body.find(stop)
        if idx > 500: body = body[:idx]

    return {"title": title, "content": body[:20000]}


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        ).new_page()

        all_articles = []

        # ═══ PUBLICATIONS ═══
        print("=" * 50)
        print("PUBLICATIONS PAGE")
        page.goto(f"{BASE}/publications", wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(8000)
        total = click_all_view_more(page, 30)
        print(f"  Loaded {total} items")
        pubs = extract_all_links(BeautifulSoup(page.content(), "html.parser"))
        pubs = categorize(pubs)
        print(f"  Articles: {len(pubs)}")

        by_type = {}
        for p in pubs:
            t = p["source_type"]
            by_type[t] = by_type.get(t, 0) + 1
        for t, c in by_type.items():
            print(f"    {t}: {c}")

        all_articles.extend(pubs)

        # ═══ NEWS ═══
        print("\n" + "=" * 50)
        print("NEWS PAGE")
        page.goto(f"{BASE}/news", wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(8000)
        total = click_all_view_more(page, 30)
        print(f"  Loaded {total} items")
        news = extract_all_links(BeautifulSoup(page.content(), "html.parser"))
        news = categorize(news)
        print(f"  Articles: {len(news)}")
        by_type = {}
        for n in news:
            t = n["source_type"]
            by_type[t] = by_type.get(t, 0) + 1
        for t, c in by_type.items():
            print(f"    {t}: {c}")
        all_articles.extend(news)

        # ═══ BLOG ═══
        print("\n" + "=" * 50)
        print("BLOG PAGE")
        page.goto(f"{BASE}/blog", wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(8000)
        total = click_all_view_more(page, 20)
        print(f"  Loaded {total} items")
        blogs = extract_all_links(BeautifulSoup(page.content(), "html.parser"))
        blogs = [b for b in blogs if "/blog-posts/" in b["url"]]
        for b in blogs:
            b["source_type"] = "blog"

        # Extract dates from listing cards
        soup = BeautifulSoup(page.content(), "html.parser")
        for item in soup.select(".all-news_item"):
            link = item.select_one('a[href*="/blog-posts/"]')
            if link:
                href = link.get("href", "").strip()
                text = item.get_text(" ", strip=True)
                m = re.search(
                    r'(January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},\s+\d{4}',
                    text)
                if m:
                    try:
                        dt = datetime.strptime(m.group(0), "%B %d, %Y")
                        for ba in blogs:
                            if href in ba["url"]:
                                ba["publish_date"] = dt.strftime("%Y-%m-%d")
                    except:
                        pass
        print(f"  Blogs: {len(blogs)}")
        all_articles.extend(blogs)

        # ═══ DEDUP ═══
        seen = set()
        unique = []
        for a in all_articles:
            if a["url"] not in seen:
                seen.add(a["url"])
                unique.append(a)
        print(f"\nTotal unique: {len(unique)}")

        # ═══ FETCH BLOG CONTENT ═══
        quera_blogs = [a for a in unique if a["source_type"] == "blog" and "quera.com" in a["url"]]
        print(f"\nFetching content for {len(quera_blogs)} blog posts...")
        for i, a in enumerate(quera_blogs):
            try:
                c = fetch_blog(page, a["url"])
                a["full_title"] = c["title"] or a.get("title", "")
                a["content"] = c["content"]
            except:
                a["full_title"] = a.get("title", "")
                a["content"] = ""
            if (i + 1) % 10 == 0:
                print(f"  [{i + 1}/{len(quera_blogs)}]")
            time.sleep(0.3)

        # ═══ SAVE ═══
        output = os.path.join(OUT, "quera_articles.json")
        with open(output, "w", encoding="utf-8") as f:
            json.dump(unique, f, ensure_ascii=False, indent=2)

        print(f"\nSaved {len(unique)} articles")
        by_type = {}
        for a in unique:
            t = a["source_type"]
            by_type[t] = by_type.get(t, 0) + 1
        for t, c in sorted(by_type.items(), key=lambda x: -x[1]):
            print(f"  {t}: {c}")

        browser.close()


if __name__ == "__main__":
    main()
