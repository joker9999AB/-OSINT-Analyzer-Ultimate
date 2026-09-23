from flask import Flask, render_template, request, jsonify
from html.parser import HTMLParser
from urllib.parse import quote_plus, urljoin
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
import re
import traceback

app = Flask(__name__)


class DuckDuckGoParser(HTMLParser):
    """Small dependency-free parser for DuckDuckGo's HTML results page."""

    def __init__(self):
        super().__init__()
        self.results = []
        self._current = None
        self._in_title = False
        self._title_parts = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        classes = set((attrs.get("class") or "").split())

        if tag == "a" and "result__a" in classes:
            self._current = {
                "title": "",
                "link": attrs.get("href") or "",
            }
            self._in_title = True
            self._title_parts = []

    def handle_data(self, data):
        if self._in_title:
            self._title_parts.append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self._in_title:
            if self._current is not None:
                self._current["title"] = " ".join(
                    "".join(self._title_parts).split()
                )
                if self._current["title"] and self._current["link"]:
                    self.results.append(self._current)
            self._current = None
            self._in_title = False
            self._title_parts = []


def classify_site(link, title):
    link_lower = (link or "").lower()
    title_lower = (title or "").lower()

    categories = {
        "Facebook": ["facebook.com", "facebook", "fb"],
        "YouTube": ["youtube.com", "youtube", "youtu.be"],
        "Telegram": ["web.telegram.org", "t.me", "telegram"],
        "Twitter": ["twitter.com", "x.com", "twitter"],
        "Instagram": ["instagram.com", "instagram"],
        "LinkedIn": ["linkedin.com", "linkedin"],
        "WhatsApp": ["whatsapp.com", "whatsapp"],
        "GitHub": ["github.com", "github"],
        "Reddit": ["reddit.com", "reddit"],
        "TikTok": ["tiktok.com", "tiktok"],
        "Pinterest": ["pinterest.com", "pinterest"],
        "Udemy": ["udemy.com", "udemy"],
        "Coursera": ["coursera.org", "coursera"],
    }

    for site, keywords in categories.items():
        if any(k in link_lower or k in title_lower for k in keywords):
            return site

    if any(k in link_lower or k in title_lower for k in [
        "cnn.com", "bbc.com", "aljazeera.net"
    ]):
        return "News Sites"

    if "@" in title_lower or any(k in title_lower for k in ["mail", "email"]):
        return "Emails"

    if any(k in title_lower for k in ["phone", "mobile", "number", "telephone"]):
        return "Phone Numbers"

    if any(k in link_lower or k in title_lower for k in [
        ".edu", ".org", ".gov", "government", "official"
    ]):
        return "Official Sites"

    return "Websites"


def _clean_result_link(link):
    """Normalize common DDG redirect links into the real destination."""
    link = (link or "").strip()
    if not link:
        return ""

    # DDG may return a /l/?uddg=<encoded URL> redirect.
    match = re.search(r"[?&]uddg=([^&]+)", link)
    if match:
        from urllib.parse import unquote
        decoded = unquote(match.group(1))
        if decoded.startswith(("http://", "https://")):
            return decoded

    if link.startswith("//"):
        return "https:" + link

    return urljoin("https://duckduckgo.com", link)


def fetch_ddg_page(query, offset):
    url = f"https://html.duckduckgo.com/html/?q={quote_plus(query)}&s={offset}"
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/140.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9,ar;q=0.8",
        "Connection": "close",
    }

    req = Request(url, headers=headers, method="GET")
    with urlopen(req, timeout=30) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        return response.read().decode(charset, errors="replace")


def osint_ex(query, depth=2):
    query = (query or "").strip()
    if not query:
        return []

    try:
        depth = max(1, min(int(depth), 5))
    except (TypeError, ValueError):
        depth = 2

    all_results = []
    seen_links = set()

    for page_number in range(depth):
        offset = page_number * 30
        html = fetch_ddg_page(query, offset)

        # If DDG returns a block/challenge page, report it clearly instead of
        # silently returning an empty result set.
        lower_html = html.lower()
        if "captcha" in lower_html or "unusual traffic" in lower_html:
            raise RuntimeError(
                "DuckDuckGo طلب تحقق (CAPTCHA/anti-bot). أعد المحاولة لاحقاً أو استخدم شبكة مختلفة."
            )

        parser = DuckDuckGoParser()
        parser.feed(html)

        page_results = parser.results
        if not page_results:
            # No results on this page: stop pagination.
            break

        for item in page_results:
            title = item["title"].strip()
            link = _clean_result_link(item["link"])
            if not title or not link or link in seen_links:
                continue

            seen_links.add(link)
            all_results.append({
                "title": title,
                "link": link,
                "image": None,
                "type": classify_site(link, title),
                "page": page_number + 1,
            })

    return all_results


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/search", methods=["POST"])
def search():
    query = request.form.get("query", "").strip()
    depth = request.form.get("depth", "2")

    if not query:
        return jsonify({
            "error": "يرجى إدخال كلمة للبحث",
            "results": []
        }), 400

    try:
        results = osint_ex(query, depth)
        return jsonify({"results": results})
    except (URLError, HTTPError) as exc:
        traceback.print_exc()
        detail = getattr(exc, "reason", str(exc))
        return jsonify({
            "error": f"تعذر الاتصال بمحرك البحث: {detail}",
            "results": []
        }), 502
    except Exception as exc:
        traceback.print_exc()
        return jsonify({
            "error": f"حدث خطأ أثناء البحث: {exc}",
            "results": []
        }), 500


if __name__ == "__main__":
    app.run(
        debug=False,
        use_reloader=False,
        host="127.0.0.1",
        port=5000,
    )
