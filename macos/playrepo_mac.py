#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PlayRepo macOS Desktop Application
Ultra-modern Pitch-Black WebApp Desktop GUI for macOS.
Extracts Turkish Dubbed & Subtitled movies with all video providers (Vidmoly, VOE, Streamtape, Doodstream, etc.),
and publishes atomically to GitHub (csprofesor/apple) for instant playback on Nuvio / Stremio.
"""

import sys
import os
import re
import json
import time
import random
import urllib.parse
import subprocess
import threading
import tempfile
import shutil
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
import socket

CONFIG_FILE = os.path.expanduser("~/.playrepo_config.json")
REPO_OWNER = "csprofesor"
REPO_NAME = "apple"
DEFAULT_BRANCH = "main"
LOGO_URL = "https://cdn.jsdelivr.net/gh/csprofesor/apple@main/playrepo/logo.png"

USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
]

# -------------------------------------------------------------
# HTTP & Network Helpers
# -------------------------------------------------------------
def http_get(url: str, referer: str = None, headers: dict = None, timeout: int = 12) -> str:
    cmd = ["curl", "-s", "-L", "--max-time", str(timeout)]
    ua = random.choice(USER_AGENTS)
    cmd += ["-A", ua]
    cmd += ["-H", "Accept: text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"]
    cmd += ["-H", "Accept-Language: tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7"]
    if referer:
        cmd += ["-H", f"Referer: {referer}"]
    if headers:
        for k, v in headers.items():
            cmd += ["-H", f"{k}: {v}"]
    cmd.append(url)
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, errors="ignore")
        return res.stdout or ""
    except Exception:
        return ""

def http_json(url: str, method: str = "GET", data: dict = None, token: str = None, timeout: int = 30) -> dict:
    cmd = ["curl", "-s", "-L", "--max-time", str(timeout), "-X", method]
    cmd += ["-H", "Accept: application/vnd.github.v3+json"]
    cmd += ["-H", "User-Agent: PlayRepo-MacOS"]
    if token:
        cmd += ["-H", f"Authorization: token {token}"]
    if data is not None:
        cmd += ["-H", "Content-Type: application/json"]
        cmd += ["-d", json.dumps(data)]
    cmd.append(url)
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, errors="ignore")
        return json.loads(res.stdout) if res.stdout else {}
    except Exception:
        return {}

# -------------------------------------------------------------
# Packer (Dean Edwards) Decoder
# -------------------------------------------------------------
def unpack_packer_js(packed: str) -> str:
    match = re.search(
        r"eval\s*\(\s*function\s*\(\s*p\s*,\s*a\s*,\s*c\s*,\s*k\s*,\s*e\s*,\s*d?\s*\)\s*\{.*?\}\s*\(\s*['\"](.*?)['\"]\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*['\"](.*?)['\"]\.split\(\s*['\"]\|['\"]\s*\)",
        packed,
        re.DOTALL
    )
    if not match:
        return packed
    payload = match.group(1)
    radix = int(match.group(2))
    keywords = match.group(4).split("|")
    chars = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"

    def decode_base(s, b):
        res = 0
        for ch in s:
            idx = chars.find(ch)
            if idx < 0 or idx >= b:
                return -1
            res = res * b + idx
        return res

    def repl(m):
        w = m.group(0)
        idx = decode_base(w, radix)
        if 0 <= idx < len(keywords) and keywords[idx]:
            return keywords[idx]
        return w

    return re.sub(r"\b\w+\b", repl, payload).replace(r"\/", "/")

# -------------------------------------------------------------
# Scraper Engine
# -------------------------------------------------------------
class ScraperEngine:
    def __init__(self, log_callback=None):
        self.log_callback = log_callback or (lambda s: None)
        self.is_running = True

    def log(self, msg: str):
        self.log_callback(msg)

    def detect_category(self, url: str, text: str) -> str:
        lower = f"{url.lower()} {text.lower()}"
        if any(k in lower for k in ["korku", "gerilim", "horror", "thriller"]):
            return "Korku & Gerilim"
        if any(k in lower for k in ["aksiyon", "macera", "action", "adventure"]):
            return "Aksiyon & Macera"
        if any(k in lower for k in ["komedi", "mizah", "comedy"]):
            return "Komedi"
        if any(k in lower for k in ["dram", "romantik", "drama", "romance"]):
            return "Dram & Romantik"
        if any(k in lower for k in ["bilim", "kurgu", "fantastik", "sci-fi", "scifi", "fantasy"]):
            return "Bilim Kurgu & Fantastik"
        return "Popüler"

    def is_valid_movie_url(self, url: str, site_base: str) -> bool:
        if not url.startswith(site_base):
            return False
        lower = url.lower().rstrip("/")
        if lower in (site_base.lower(), f"{site_base.lower()}/"):
            return False
        disallowed = [
            "/kategori/", "/tur/", "/filmizle/", "/yil/", "/category/", "/genre/",
            "/koleksiyon/", "/forum", "/iletisim", "/page/", "page=", "orderby=",
            "/tag/", "/etiket/", "#", "javascript:", ".jpg", ".png", ".css", ".js",
            ".svg", ".webp", ".ico", "/login", "/uye", "/takvim", "/trendler",
            "/seri-filmler", "/istek", "/dmca", "/gizlilik", "/privacy", "/contact"
        ]
        if any(d in lower for d in disallowed):
            return False

        path = lower[len(site_base):].strip("/")
        if not path:
            return False

        matches_kw = any(k in lower for k in ["/film/", "-izle", "/dizi/", "/filmler/", "/movie/", "/watch/", "/izle/", "/sinema/", "/video/", "/title/", "/bolum/", "/episode/"])
        is_hyphenated_slug = "-" in path and "/page" not in path and "?" not in path

        return matches_kw or is_hyphenated_slug

    def is_valid_catalog_url(self, url: str, site_base: str) -> bool:
        if not url.startswith(site_base):
            return False
        lower = url.lower()
        if self.is_valid_movie_url(url, site_base):
            return False
        allowed = [
            "/page/", "page=", "sayfa=", "/kategori/", "/tur/", "/filmizle/",
            "/yil/", "/category/", "/genre/", "altyazi", "dublaj",
            f"{site_base}/film/", f"{site_base}/film", f"{site_base}/film-izle/",
            f"{site_base}/filmler/", "orderby="
        ]
        return any(k in lower for k in allowed)

    def discover_movie_links(self, site_url: str, max_pages: int = 20) -> dict:
        site_base = site_url
        if "://" in site_url:
            parts = site_url.split("/")
            site_base = f"{parts[0]}//{parts[2]}"

        movie_categories = {}
        queue = [site_url]
        visited = set()
        page_category_map = {site_url: "Popüler"}

        # Otomatik Sitemap ve RSS / Feed Keşfi (Doğrudan indeks çekme)
        sitemap_candidates = [
            f"{site_base}/sitemap.xml",
            f"{site_base}/sitemap_index.xml",
            f"{site_base}/post-sitemap.xml",
            f"{site_base}/film-sitemap.xml",
            f"{site_base}/feed",
            f"{site_base}/rss"
        ]
        sitemap_found_count = 0
        for sm_url in sitemap_candidates:
            if not self.is_running:
                break
            try:
                sm_content = http_get(sm_url, timeout=5)
                if sm_content and ("<urlset" in sm_content or "<sitemapindex" in sm_content or "<rss" in sm_content):
                    self.log(f"⚡ [Sitemap Keşfi] {sm_url} üzerinden doğrudan indeks taranıyor...")
                    locs = re.findall(r'<loc>(https?://[^<]+)</loc>', sm_content, re.I) or re.findall(r'<link>(https?://[^<]+)</link>', sm_content, re.I)
                    for loc in locs:
                        loc = loc.strip()
                        if self.is_valid_movie_url(loc, site_base):
                            cats = movie_categories.setdefault(loc, set())
                            cat_from_url = self.detect_category(loc, "")
                            cats.add(cat_from_url)
                            sitemap_found_count += 1
                        elif sm_url.endswith("_index.xml") and loc.endswith(".xml") and "post" in loc:
                            # Alt sitemap tespiti
                            sub_content = http_get(loc, timeout=5)
                            if sub_content:
                                sub_locs = re.findall(r'<loc>(https?://[^<]+)</loc>', sub_content, re.I)
                                for sloc in sub_locs:
                                    sloc = sloc.strip()
                                    if self.is_valid_movie_url(sloc, site_base):
                                        cats = movie_categories.setdefault(sloc, set())
                                        cats.add(self.detect_category(sloc, ""))
                                        sitemap_found_count += 1
                    if sitemap_found_count > 0:
                        self.log(f"✓ [Sitemap Başarılı] {sitemap_found_count} film bağlantısı doğrudan haritadan alındı!")
                        break
            except Exception:
                pass

        home_html = http_get(site_url)
        for href, text in re.findall(r'<a\s+[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', home_html, re.I | re.DOTALL):
            abs_url = urllib.parse.urljoin(site_url, href.strip())
            clean_text = re.sub(r'<[^>]+>', '', text).strip()
            cat = self.detect_category(abs_url, clean_text)
            if cat != "Popüler" and abs_url.startswith(site_base):
                if abs_url not in queue:
                    queue.append(abs_url)
                    page_category_map[abs_url] = cat

        page_count = 0
        while queue and page_count < max_pages and self.is_running:
            curr_url = queue.pop(0)
            if curr_url in visited:
                continue
            visited.add(curr_url)
            page_count += 1

            cat_for_page = page_category_map.get(curr_url, self.detect_category(curr_url, ""))
            html = http_get(curr_url)
            if not html:
                continue

            if "Just a moment..." in html or "Bir dakika lütfen..." in html or "cf-chl-opt" in html or "challenges.cloudflare.com" in html:
                self.log(f"⚠️ [Cloudflare Koruması] {curr_url} adresinde Cloudflare bot doğrulaması tespit edildi! Tarayıcı simülasyonu deneniyor...")
                sim_h = self.simulate_browser_render(curr_url)
                if sim_h and "cf-chl-opt" not in sim_h:
                    html = sim_h
                else:
                    self.log(f"✗ [Cloudflare Engeli] {curr_url} Cloudflare Turnstile tarafından korunduğu için taranamadı. Lütfen alternatif bir site (örn: https://filmdozu.com/ veya https://ufilmizle.com/) deneyin.")

            new_in_page = 0
            for href in re.findall(r'href=["\']([^"\']+)["\']', html, re.I):
                abs_url = urllib.parse.urljoin(curr_url, href.strip())
                if self.is_valid_movie_url(abs_url, site_base):
                    cats = movie_categories.setdefault(abs_url, set())
                    if cat_for_page not in cats:
                        cats.add(cat_for_page)
                        new_in_page += 1

            self.log(f"[{cat_for_page}] Sayfa {page_count}/{max_pages} taranıyor (+{new_in_page} yeni film, Toplam: {len(movie_categories)})")

            for href, text in re.findall(r'<a\s+[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', html, re.I | re.DOTALL):
                abs_url = urllib.parse.urljoin(curr_url, href.strip())
                clean_text = re.sub(r'<[^>]+>', '', text).strip()
                if self.is_valid_catalog_url(abs_url, site_base) and abs_url not in visited and abs_url not in queue:
                    queue.append(abs_url)
                    is_pagi = any(p in abs_url for p in ["/page/", "page=", "sayfa="])
                    page_category_map[abs_url] = cat_for_page if is_pagi else self.detect_category(abs_url, clean_text)

        return movie_categories

    def unpack_embed_stream(self, embed_url: str, referer: str, depth: int = 0) -> str:
        if depth > 3 or not embed_url or embed_url.startswith(("about:", "javascript:")):
            return ""

        lower = embed_url.lower()

        if (".m3u8" in lower or ".mp4" in lower) and not any(k in lower for k in ["/embed", "/e/", "/video/"]):
            return embed_url

        if "my.mail.ru/video/embed/" in lower or "my.mail.ru/+/video/meta/" in lower:
            try:
                vid = embed_url.split("/")[-1].split("?")[0].replace(".html", "")
                meta = http_json(f"https://my.mail.ru/+/video/meta/{vid}")
                videos = meta.get("videos", [])
                if videos:
                    best = next((v for v in videos if v.get("key") in ["1080p", "720p"]), videos[0])
                    v_url = best.get("url", "")
                    if v_url:
                        return f"https:{v_url}" if v_url.startswith("//") else v_url
            except Exception:
                pass

        html = http_get(embed_url, referer=referer)
        if not html:
            return ""

        if "eval(function(p,a,c,k,e,d)" in html:
            html += "\n" + unpack_packer_js(html)

        m3u8_m = re.search(r'file\s*:\s*["\'](https?://[^"\']+\.m3u8[^"\']*)["\']', html, re.I) or \
                 re.search(r'sources\s*:\s*\[\s*\{\s*file\s*:\s*["\'](https?://[^"\']+\.m3u8[^"\']*)["\']', html, re.I) or \
                 re.search(r'["\'](https?://[^"\']+\.m3u8[^"\']*)["\']', html, re.I)
        if m3u8_m:
            return m3u8_m.group(1)

        voe_m = re.search(r"['\"]hls['\"]\s*:\s*['\"](https?://[^'\"]+)['\"]", html, re.I)
        if voe_m:
            return voe_m.group(1)

        if "streamtape.com" in lower or "streamta.pe" in lower:
            st_m = re.search(r"document\.getElementById\('robotlink'\)\.innerHTML\s*=\s*['\"](.*?)['\"]", html)
            if st_m:
                tok = st_m.group(1)
                tok_add = re.search(r"\+ \('xcd(.*?)\.substring\(1\)", html)
                if tok_add:
                    tok += tok_add.group(1)
                st_url = f"https:{tok}" if tok.startswith("//") else (f"https://streamtape.com{tok}" if tok.startswith("/") else tok)
                return st_url

        if "dood" in lower or "ds2play" in lower:
            pass_m = re.search(r"/pass_md5/([^'\"]+)", html)
            if pass_m:
                pass_url = f"https://dood.to/pass_md5/{pass_m.group(1)}"
                token_res = http_get(pass_url, referer=embed_url)
                if token_res:
                    return f"{token_res.strip()}~PlayRepo~{int(time.time() * 1000)}"

        if "video.sibnet.ru" in lower:
            sib_m = re.search(r'["\'](/v/[^"\']+\.mp4)["\']', html)
            if sib_m:
                return f"https://video.sibnet.ru{sib_m.group(1)}"

        if "ok.ru" in lower or "odnoklassniki" in lower:
            ok_m = re.search(r'data-options=["\'](.*?)["\']', html)
            if ok_m:
                try:
                    ok_data = json.loads(ok_m.group(1).replace("&quot;", '"'))
                    hls = ok_data.get("flashvars", {}).get("metadata", {}).get("hlsManifestUrl")
                    if hls:
                        return hls
                except Exception:
                    pass

        if "bitchute.com" in lower:
            bc_m = re.search(r'(https?://[^\s"\'<>]+\.bitchute\.com/[^\s"\'<>]+\.mp4)', html, re.I)
            if bc_m:
                return bc_m.group(1)

        # Generic JSON sources / variable matching (JWPlayer, VideoJS, Plyr, etc.)
        json_stream_m = re.search(r'["\']?(?:file|src|url|hls|stream)["\']?\s*:\s*["\'](https?://[^"\']+\.(?:m3u8|mp4)[^"\']*)["\']', html, re.I)
        if json_stream_m:
            return json_stream_m.group(1)

        # Direct .m3u8 or .mp4 anywhere in scripts / HTML
        any_stream_m = re.search(r'https?://[^\s"\'<>]+\.(?:m3u8|mp4)[^\s"\'<>]*', html, re.I)
        if any_stream_m and not any(k in any_stream_m.group(0) for k in ["/thumb", "/preview", ".jpg", ".png"]):
            return any_stream_m.group(0)

        for nested_m in re.finditer(r'<iframe\s+[^>]*src=["\']([^"\']+)["\']', html, re.I):
            nested_src = urllib.parse.urljoin(embed_url, nested_m.group(1).strip())
            if nested_src != embed_url and not any(nested_src.endswith(ext) for ext in [".jpg", ".png", ".css", ".js"]):
                res = self.unpack_embed_stream(nested_src, referer=embed_url, depth=depth + 1)
                if res:
                    return res

        return ""

    def is_stream_alive(self, url: str, referer: str = None) -> bool:
        cmd = ["curl", "-s", "-I", "--max-time", "3", "-A", random.choice(USER_AGENTS)]
        if referer:
            cmd += ["-H", f"Referer: {referer}"]
        cmd.append(url)
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, errors="ignore")
            first_line = (res.stdout or "").splitlines()[0] if res.stdout else ""
            status = int(first_line.split()[1]) if len(first_line.split()) > 1 and first_line.split()[1].isdigit() else 200
            return status < 400 or status == 416
        except Exception:
            return True

    def simulate_browser_render(self, url: str) -> str:
        """
        Executes client-side JavaScript using headless Chrome/Chromium simulation,
        returning the fully populated DOM with dynamically injected video players/iframes.
        """
        chrome_candidates = [
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Chromium.app/Contents/MacOS/Chromium",
            "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
            "google-chrome",
            "chromium"
        ]
        chrome_bin = next((b for b in chrome_candidates if shutil.which(b) or os.path.exists(b)), None)
        if not chrome_bin:
            return ""

        temp_dir = tempfile.mkdtemp(prefix="playrepo_sim_")
        try:
            cmd = [
                chrome_bin,
                "--headless=new",
                "--dump-dom",
                "--disable-gpu",
                "--no-sandbox",
                "--disable-dev-shm-usage",
                f"--user-data-dir={temp_dir}",
                "--timeout=8000",
                url
            ]
            res = subprocess.run(cmd, capture_output=True, text=True, errors="ignore", timeout=12)
            return res.stdout or ""
        except Exception:
            return ""
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def scrape_movie_page(self, movie_url: str, initial_cats: set, tmdb_api_key: str = None) -> dict:
        html = http_get(movie_url, referer=movie_url)
        if not html:
            return None

        title_m = re.search(r'<h1[^>]*>(.*?)</h1>', html, re.I | re.DOTALL) or \
                  re.search(r'<meta\s+property=["\']og:title["\']\s+content=["\'](.*?)["\']', html, re.I) or \
                  re.search(r'<title>(.*?)</title>', html, re.I)
        raw_title = re.sub(r'<[^>]+>', '', title_m.group(1)).strip() if title_m else movie_url.split("/")[-1].replace("-", " ")

        clean_title = re.sub(r'\(?\b(19\d{2}|20\d{2})\b\)?', '', raw_title)
        for term in ["türkçe dublaj", "türkçe altyazı", "turkce dublaj", "turkce altyazili", "izle", "full hd", "1080p", "4k", "tek parça", "film kovası", "hintfilmizle"]:
            clean_title = re.sub(rf'\b{term}\b', '', clean_title, flags=re.I)
        clean_title = re.sub(r'\s+', ' ', clean_title).strip(" -|:")

        if not clean_title or clean_title.lower() in ("filmkovasi.co", "www.hintfilmizle.com"):
            return None

        year_m = re.search(r'\b(19\d{2}|20\d{2})\b', raw_title) or re.search(r'\b(19\d{2}|20\d{2})\b', html)
        year = year_m.group(1) if year_m else ""

        poster_m = re.search(r'<meta\s+property=["\']og:image["\']\s+content=["\']([^"\']+)["\']', html, re.I) or \
                   re.search(r'<img[^>]+class=["\'][^"\']*(?:poster|afis|cover)[^"\']*["\'][^>]+src=["\']([^"\']+)["\']', html, re.I) or \
                   re.search(r'<img[^>]+src=["\']([^"\']+)["\'][^>]+class=["\'][^"\']*(?:poster|afis|cover)[^"\']*["\']', html, re.I)
        poster = urllib.parse.urljoin(movie_url, poster_m.group(1).strip()) if poster_m else LOGO_URL

        desc_m = re.search(r'<meta\s+property=["\']og:description["\']\s+content=["\'](.*?)["\']', html, re.I) or \
                 re.search(r'<meta\s+name=["\']description["\']\s+content=["\'](.*?)["\']', html, re.I) or \
                 re.search(r'class=["\'][^"\']*(?:film-ozeti|ozet|story|summary|film-content|entry-content)[^"\']*["\'][^>]*>(.*?)</div>', html, re.I | re.DOTALL) or \
                 re.search(r'class=["\'][^"\']*(?:film-ozeti|ozet|story|summary|film-content|entry-content)[^"\']*["\'][^>]*>(.*?)</p>', html, re.I | re.DOTALL)
        desc = re.sub(r'<[^>]+>', '', desc_m.group(1)).strip() if desc_m else "Play • 1080p TR"

        imdb_m = re.search(r'class=["\'][^"\']*(?:imdb-puan|imdb|puan|rating)[^"\']*["\'][^>]*>([0-9.,]+)<', html, re.I)
        imdb_rating = imdb_m.group(1).replace(",", ".").strip() if imdb_m else None

        time_m = re.search(r'(\d{2,3})\s*(?:dk|dakika|min)', html, re.I)
        runtime = f"{time_m.group(1)} dk" if time_m else None

        genres = set(initial_cats) if initial_cats else {"Popüler"}
        for href, text in re.findall(r'<a\s+[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', html, re.I | re.DOTALL):
            if any(k in href.lower() for k in ["/kategori/", "/tur/", "/filmizle/", "/genre/"]):
                cat = self.detect_category(href, re.sub(r'<[^>]+>', '', text).strip())
                if cat != "Popüler":
                    genres.add(cat)

        candidate_embeds = []
        default_lang = "[TR Altyazılı]" if "altyaz" in raw_title.lower() or "altyaz" in movie_url.lower() else "[TR Dublaj]"

        def get_lang(txt: str) -> str:
            t = txt.lower()
            if any(k in t for k in ["altyaz", "sub", "original", "orijinal", "ingilizce"]):
                return "[TR Altyazılı]"
            if any(k in t for k in ["dublaj", "turkce", "türkçe"]):
                return "[TR Dublaj]"
            return default_lang

        for m in re.finditer(r'<[^>]+data-(?:frame|player|embed|video|url|link|file|src|source|href|target|stream|hls|mp4)=["\']([^"\']+)["\'][^>]*>(.*?)</[^>]+>', html, re.I | re.DOTALL):
            c_url = urllib.parse.urljoin(movie_url, m.group(1).strip())
            c_lang = get_lang(m.group(2) + " " + c_url)
            candidate_embeds.append((c_url, c_lang))

        for m in re.finditer(r'(?:loadSource|loadPlayer|changeSource|setSource|playVideo|loadEmbed)\s*\(\s*["\']([^"\']+)["\']', html, re.I):
            c_url = urllib.parse.urljoin(movie_url, m.group(1).replace("&amp;", "&").strip())
            candidate_embeds.append((c_url, default_lang))

        for m in re.finditer(r'<iframe\s+[^>]*(?:src|data-src|data-frame)=["\']([^"\']+)["\'][^>]*>', html, re.I):
            c_url = urllib.parse.urljoin(movie_url, m.group(1).strip())
            candidate_embeds.append((c_url, default_lang))

        for s_m in re.finditer(r'<script[^>]*>(.*?)</script>', html, re.I | re.DOTALL):
            s_code = s_m.group(1)
            for m in re.finditer(r'<iframe[^>]+src=["\']([^"\']+)["\']', s_code, re.I):
                candidate_embeds.append((urllib.parse.urljoin(movie_url, m.group(1).strip()), default_lang))
            for m in re.finditer(r'(?:playerUrl|embedUrl|videoUrl|iframeUrl|streamUrl|fileUrl|src|source)\s*=\s*["\'](https?://[^"\']+)["\']', s_code, re.I):
                candidate_embeds.append((m.group(1).strip(), default_lang))

        for m in re.finditer(r'["\'](https?://[^\s"\'<>]+\.(?:m3u8|mp4)[^\s"\'<>]*)["\']', html, re.I):
            candidate_embeds.append((m.group(1).strip(), default_lang))

        for href, text in re.findall(r'<a\s+[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', html, re.I | re.DOTALL):
            t_lower = text.lower()
            if "altyaz" in t_lower or "dublaj" in t_lower:
                alt_lang = "[TR Altyazılı]" if "altyaz" in t_lower else "[TR Dublaj]"
                if alt_lang != default_lang:
                    alt_url = urllib.parse.urljoin(movie_url, href.strip())
                    alt_html = http_get(alt_url, referer=movie_url)
                    if alt_html:
                        for if_m in re.finditer(r'<iframe\s+[^>]*src=["\']([^"\']+)["\'][^>]*>', alt_html, re.I):
                            candidate_embeds.append((urllib.parse.urljoin(alt_url, if_m.group(1).strip()), alt_lang))
                        for df_m in re.finditer(r'data-frame=["\']([^"\']+)["\']', alt_html, re.I):
                            candidate_embeds.append((urllib.parse.urljoin(alt_url, df_m.group(1).strip()), alt_lang))

        # WordPress / DooPlay player AJAX simulation for dynamic source tabs
        parsed_origin = urllib.parse.urlsplit(movie_url)
        site_base = f"{parsed_origin.scheme}://{parsed_origin.netloc}"
        for opt_m in re.finditer(r'data-post=["\'](\d+)["\'][^>]*data-nume=["\']([^"\']+)["\']', html, re.I):
            post_id, nume = opt_m.group(1), opt_m.group(2)
            try:
                ajax_cmd = [
                    "curl", "-s", "-X", "POST", f"{site_base}/wp-admin/admin-ajax.php",
                    "-d", f"action=doo_player_ajax&post={post_id}&nume={nume}&type=movie",
                    "-H", f"Referer: {movie_url}",
                    "-H", "X-Requested-With: XMLHttpRequest",
                    "-A", random.choice(USER_AGENTS)
                ]
                ajax_res = subprocess.run(ajax_cmd, capture_output=True, text=True, timeout=5)
                if ajax_res.stdout:
                    for if_m in re.finditer(r'<iframe\s+[^>]*src=["\']([^"\']+)["\']', ajax_res.stdout, re.I):
                        candidate_embeds.append((urllib.parse.urljoin(movie_url, if_m.group(1).strip()), default_lang))
            except Exception:
                pass

        # Extract subtitles (.vtt / .srt)
        subtitles = []
        for tr_m in re.finditer(r'<track\s+[^>]*kind=["\'](?:subtitles|captions)["\'][^>]*>', html, re.I):
            tr_tag = tr_m.group(0)
            src_m = re.search(r'src=["\']([^"\']+)["\']', tr_tag, re.I)
            lang_m = re.search(r'(?:srclang|label)=["\']([^"\']+)["\']', tr_tag, re.I)
            if src_m:
                s_url = urllib.parse.urljoin(movie_url, src_m.group(1).strip())
                s_lang = lang_m.group(1).strip() if lang_m else "Turkish"
                subtitles.append({"id": s_lang.lower(), "url": s_url, "lang": s_lang})

        for vtt_m in re.finditer(r'https?://[^\s"\'<>]+\.(?:vtt|srt)[^\s"\'<>]*', html, re.I):
            u = vtt_m.group(0)
            if not any(s["url"] == u for s in subtitles):
                subtitles.append({"id": "tr", "url": u, "lang": "Turkish"})

        # Series / Season / Episode detection
        is_series = "/dizi/" in movie_url.lower() or "/series/" in movie_url.lower() or ("sezon" in html.lower() and "bölüm" in html.lower())
        season_m = re.search(r'(?:sezon|season)[-\s_]*(\d+)', movie_url, re.I) or re.search(r'(\d+)\s*\.\s*(?:sezon|season)', raw_title, re.I)
        episode_m = re.search(r'(?:bolum|bölüm|episode)[-\s_]*(\d+)', movie_url, re.I) or re.search(r'(\d+)\s*\.\s*(?:bolum|bölüm|episode)', raw_title, re.I)
        season_num = int(season_m.group(1)) if season_m else None
        episode_num = int(episode_m.group(1)) if episode_m else None

        resolved_streams = []
        seen_urls = set()

        for cand_url, cand_lang in candidate_embeds:
            if not cand_url or cand_url in seen_urls:
                continue
            direct_url = self.unpack_embed_stream(cand_url, referer=movie_url)
            if direct_url and direct_url not in seen_urls:
                seen_urls.add(direct_url)
                is_m3u8 = ".m3u8" in direct_url.lower()
                st_item = {
                    "name": "Play",
                    "title": f"Play • 1080p • {cand_lang}",
                    "url": direct_url,
                    "quality": 1080,
                    "format": "m3u8" if is_m3u8 else "mp4",
                    "referer": cand_url,
                    "headers": {
                        "User-Agent": random.choice(USER_AGENTS),
                        "Referer": cand_url
                    }
                }
                if subtitles:
                    st_item["subtitles"] = subtitles
                resolved_streams.append(st_item)

        if not resolved_streams:
            # Fallback: Headless Browser DOM Simulation for dynamic JS players
            sim_html = self.simulate_browser_render(movie_url)
            if sim_html:
                sim_cands = []
                for if_m in re.finditer(r'<iframe\s+[^>]*(?:src|data-src|data-frame)=["\']([^"\']+)["\'][^>]*>', sim_html, re.I):
                    sim_cands.append(urllib.parse.urljoin(movie_url, if_m.group(1).strip()))
                for v_m in re.finditer(r'<video[^>]+src=["\']([^"\']+)["\']', sim_html, re.I):
                    sim_cands.append(urllib.parse.urljoin(movie_url, v_m.group(1).strip()))
                for m_m in re.finditer(r'["\'](https?://[^\s"\'<>]+\.(?:m3u8|mp4)[^\s"\'<>]*)["\']', sim_html, re.I):
                    sim_cands.append(m_m.group(1).strip())

                for c_url in sim_cands:
                    if c_url in seen_urls:
                        continue
                    d_url = self.unpack_embed_stream(c_url, referer=movie_url)
                    if d_url and d_url not in seen_urls:
                        seen_urls.add(d_url)
                        is_m3u8 = ".m3u8" in d_url.lower()
                        st_item = {
                            "name": "Play",
                            "title": f"Play • 1080p • {default_lang}",
                            "url": d_url,
                            "quality": 1080,
                            "format": "m3u8" if is_m3u8 else "mp4",
                            "referer": c_url,
                            "headers": {
                                "User-Agent": random.choice(USER_AGENTS),
                                "Referer": c_url
                            }
                        }
                        if subtitles:
                            st_item["subtitles"] = subtitles
                        resolved_streams.append(st_item)

        if not resolved_streams:
            return None

        # Filter dead links via fast HEAD check
        alive_streams = [s for s in resolved_streams if self.is_stream_alive(s["url"], s.get("referer"))]
        valid_streams = alive_streams if alive_streams else resolved_streams

        valid_streams.sort(key=lambda s: 1 if s["format"] == "m3u8" else 0, reverse=True)

        dub_c = 0
        sub_c = 0
        oth_c = 0
        for st in valid_streams:
            if "Dublaj" in st["title"]:
                dub_c += 1
                st["title"] = f"Play • Dublaj {dub_c} • 1080p • [TR Dublaj]"
            elif "Altyaz" in st["title"]:
                sub_c += 1
                st["title"] = f"Play • Altyazı {sub_c} • 1080p • [TR Altyazılı]"
            else:
                oth_c += 1
                st["title"] = f"Play • Sunucu {oth_c} • 1080p • [TR]"

        meta_id, official_title, official_poster, official_desc, release_year = self.resolve_meta(
            clean_title, poster, desc, year, tmdb_api_key
        )

        return {
            "tmdbId": meta_id,
            "title": official_title,
            "poster": official_poster,
            "description": official_desc,
            "year": release_year,
            "imdbRating": imdb_rating,
            "runtime": runtime,
            "genres": list(genres),
            "streams": valid_streams,
            "type": "series" if is_series else "movie",
            "season": season_num,
            "episode": episode_num
        }

    def resolve_meta(self, title: str, poster: str, desc: str, year: str, tmdb_key: str = None):
        try:
            q = urllib.parse.quote(title)
            cm_res = http_json(f"https://v3-cinemeta.strem.io/catalog/movie/top/search={q}.json")
            metas = cm_res.get("metas", [])
            if metas:
                m0 = metas[0]
                m_id = m0.get("id")
                if m_id and m_id.startswith("tt"):
                    turkish_desc = desc if desc and desc != "Play • 1080p TR" else (m0.get("description") or "Play • 1080p TR")
                    return (
                        m_id,
                        m0.get("name") or title,
                        m0.get("poster") or poster,
                        turkish_desc,
                        m0.get("releaseInfo") or year
                    )
        except Exception:
            pass

        if tmdb_key:
            try:
                q = urllib.parse.quote(title)
                tm_res = http_json(f"https://api.themoviedb.org/3/search/movie?api_key={tmdb_key}&query={q}&language=tr-TR")
                results = tm_res.get("results", [])
                if results:
                    r0 = results[0]
                    r_id = r0.get("id")
                    if r_id:
                        p_path = r0.get("poster_path")
                        p_url = f"https://image.tmdb.org/t/p/w500{p_path}" if p_path else poster
                        return (
                            f"tmdb-{r_id}",
                            r0.get("title") or title,
                            p_url,
                            r0.get("overview") or desc,
                            (r0.get("release_date") or year)[:4]
                        )
            except Exception:
                pass

        slug = re.sub(r'[^a-z0-9]+', '-', title.lower()).strip('-')
        safe_id = f"playrepo-{slug}" if slug else f"playrepo-film-{abs(hash(title))}"
        return (safe_id, title, poster, desc, year)

# -------------------------------------------------------------
# GitHub Atomic Git Data API Publisher
# -------------------------------------------------------------
class GitHubAtomicPublisher:
    def __init__(self, token: str, owner: str = REPO_OWNER, repo: str = REPO_NAME, log_callback=None):
        self.token = token
        self.owner = owner
        self.repo = repo
        self.log_callback = log_callback or (lambda s: None)

    def log(self, msg: str):
        self.log_callback(msg)

    def publish_movies(self, scraped_movies: list) -> bool:
        if not self.token:
            self.log("✗ [ERROR] GitHub Token girilmemiş! Ayarlar bölümünden GitHub Token kaydedin.")
            return False

        self.log(f"[GITHUB] {len(scraped_movies)} film için Git Data API yükleme paketi hazırlanıyor...")

        files_to_commit = {}

        cat_url = f"https://raw.githubusercontent.com/{self.owner}/{self.repo}/{DEFAULT_BRANCH}/data/catalog.json?t={int(time.time())}"
        cat_json = http_json(cat_url)
        metas = cat_json.get("metas", []) if isinstance(cat_json, dict) else []
        meta_dict = {m.get("id"): m for m in metas if isinstance(m, dict) and m.get("id")}

        str_url = f"https://raw.githubusercontent.com/{self.owner}/{self.repo}/{DEFAULT_BRANCH}/data/streams.json?t={int(time.time())}"
        str_json = http_json(str_url)
        streams_map = str_json if isinstance(str_json, dict) else {}

        for m in scraped_movies:
            m_id = m["tmdbId"]
            hyphen_id = m_id.replace(":", "-").replace("_", "-")
            safe_id = m_id.replace(":", "_")

            m_type = m.get("type", "movie")
            s_num = m.get("season")
            e_num = m.get("episode")
            vid_id = f"{m_id}:{s_num}:{e_num}" if (m_type == "series" and s_num and e_num) else m_id

            cat_item = {
                "id": m_id,
                "type": m_type,
                "name": m["title"],
                "poster": m["poster"],
                "background": m["poster"],
                "logo": LOGO_URL,
                "description": m["description"],
                "releaseInfo": m["year"],
                "genres": m["genres"]
            }
            if m.get("imdbRating"):
                cat_item["imdbRating"] = m["imdbRating"]
            if m.get("runtime"):
                cat_item["runtime"] = m["runtime"]

            meta_dict[m_id] = cat_item
            streams_map[m_id] = m["streams"]
            streams_map[hyphen_id] = m["streams"]
            streams_map[safe_id] = m["streams"]

            vid_obj = {
                "id": vid_id,
                "title": m["title"],
                "released": f"{m['year']}-01-01T00:00:00.000Z" if m.get("year") else None
            }
            if s_num:
                vid_obj["season"] = s_num
            if e_num:
                vid_obj["episode"] = e_num

            detail_meta = {
                "meta": {
                    "id": m_id,
                    "type": m_type,
                    "name": m["title"],
                    "poster": m["poster"],
                    "background": m["poster"],
                    "logo": LOGO_URL,
                    "description": m["description"],
                    "releaseInfo": m["year"],
                    "genres": m["genres"],
                    "behaviorHints": {
                        "defaultVideoId": vid_id,
                        "hasScheduledVideos": False
                    },
                    "videos": [vid_obj]
                }
            }
            if m.get("imdbRating"):
                detail_meta["meta"]["imdbRating"] = m["imdbRating"]
            if m.get("runtime"):
                detail_meta["meta"]["runtime"] = m["runtime"]

            stream_obj = {"streams": m["streams"]}

            m_json_str = json.dumps(detail_meta, ensure_ascii=False, indent=2)
            s_json_str = json.dumps(stream_obj, ensure_ascii=False, indent=2)

            clean_id = m_id.replace("playrepo-", "").replace("playrepo:", "").replace("tmdb-", "").replace("tmdb:", "")
            distinct_ids = {m_id, hyphen_id, safe_id, clean_id}
            for did in distinct_ids:
                if did:
                    files_to_commit[f"playrepo/meta/movie/{did}.json"] = m_json_str
                    files_to_commit[f"playrepo/stream/movie/{did}.json"] = s_json_str
                    if m_type == "series":
                        files_to_commit[f"playrepo/meta/series/{did}.json"] = m_json_str
                        if s_num and e_num:
                            files_to_commit[f"playrepo/stream/series/{did}:{s_num}:{e_num}.json"] = s_json_str
                            files_to_commit[f"playrepo/stream/series/{did}-{s_num}-{e_num}.json"] = s_json_str

        all_metas = list(meta_dict.values())
        files_to_commit["data/catalog.json"] = json.dumps({"metas": all_metas}, ensure_ascii=False, indent=2)
        files_to_commit["data/streams.json"] = json.dumps(streams_map, ensure_ascii=False, indent=2)

        def has_genre(item, *genres):
            g_list = [g.lower() for g in item.get("genres", [])]
            return any(any(tgt in g for tgt in genres) for g in g_list)

        cat_configs = {
            "playrepo/catalog/movie/playrepo-popular.json": lambda x: True,
            "playrepo/catalog/movie/playrepo-catalog-movies.json": lambda x: True,
            "playrepo/catalog/movie/playrepo-aksiyon.json": lambda x: has_genre(x, "aksiyon", "macera"),
            "playrepo/catalog/movie/playrepo-komedi.json": lambda x: has_genre(x, "komedi", "mizah"),
            "playrepo/catalog/movie/playrepo-dram.json": lambda x: has_genre(x, "dram", "romantik"),
            "playrepo/catalog/movie/playrepo-korku.json": lambda x: has_genre(x, "korku", "gerilim"),
            "playrepo/catalog/movie/playrepo-bilimkurgu.json": lambda x: has_genre(x, "bilim", "kurgu", "fantastik")
        }

        for path, pred in cat_configs.items():
            filtered = [m for m in all_metas if pred(m)]
            files_to_commit[path] = json.dumps({"metas": filtered}, ensure_ascii=False, indent=2)

        self.log(f"[GITHUB] Toplam {len(files_to_commit)} dosya tek bir atomik commit olarak GitHub'a aktarılıyor...")

        ref_data = http_json(f"https://api.github.com/repos/{self.owner}/{self.repo}/git/ref/heads/{DEFAULT_BRANCH}", token=self.token)
        latest_commit_sha = ref_data.get("object", {}).get("sha")
        if not latest_commit_sha:
            self.log(f"✗ [ERROR] GitHub ref alınamadı: {ref_data.get('message', 'Yetkisiz erişim / Token geçersiz')}")
            return False

        commit_data = http_json(f"https://api.github.com/repos/{self.owner}/{self.repo}/git/commits/{latest_commit_sha}", token=self.token)
        base_tree_sha = commit_data.get("tree", {}).get("sha")

        # GitHub Git Trees API creates blobs automatically from inline content in 1 request
        tree_items = [
            {
                "path": path,
                "mode": "100644",
                "type": "blob",
                "content": content
            }
            for path, content in files_to_commit.items()
        ]

        tree_res = http_json(
            f"https://api.github.com/repos/{self.owner}/{self.repo}/git/trees",
            method="POST",
            data={"base_tree": base_tree_sha, "tree": tree_items},
            token=self.token
        )
        new_tree_sha = tree_res.get("sha")
        if not new_tree_sha:
            self.log(f"✗ [ERROR] Git ağacı oluşturulamadı: {tree_res.get('message')}")
            return False

        commit_msg = f"PlayRepo macOS: Sync {len(scraped_movies)} filmleri ve kategorileri güncelle"
        new_commit_res = http_json(
            f"https://api.github.com/repos/{self.owner}/{self.repo}/git/commits",
            method="POST",
            data={
                "message": commit_msg,
                "tree": new_tree_sha,
                "parents": [latest_commit_sha]
            },
            token=self.token
        )
        new_commit_sha = new_commit_res.get("sha")
        if not new_commit_sha:
            self.log(f"✗ [ERROR] Commit oluşturulamadı: {new_commit_res.get('message')}")
            return False

        update_ref = http_json(
            f"https://api.github.com/repos/{self.owner}/{self.repo}/git/refs/heads/{DEFAULT_BRANCH}",
            method="PATCH",
            data={"sha": new_commit_sha, "force": True},
            token=self.token
        )

        if "object" in update_ref or update_ref.get("ref"):
            self.log(f"✓ [SUCCESS] Toplam {len(scraped_movies)} film, kategoriler ve detay sayfaları %100 eksiksiz yüklendi! (Commit: {new_commit_sha[:8]})")
            return True
        else:
            self.log(f"✗ [ERROR] Dal güncellenemedi: {update_ref.get('message')}")
            return False

    def delete_movie(self, movie_id: str) -> bool:
        if not self.token:
            self.log("✗ [ERROR] GitHub Token girilmemiş!")
            return False

        self.log(f"[GITHUB SİLME] {movie_id} filmi depodan ve eklentiden siliniyor...")

        cat_url = f"https://raw.githubusercontent.com/{self.owner}/{self.repo}/{DEFAULT_BRANCH}/data/catalog.json?t={int(time.time())}"
        cat_json = http_json(cat_url)
        metas = cat_json.get("metas", []) if isinstance(cat_json, dict) else []
        meta_dict = {m.get("id"): m for m in metas if isinstance(m, dict) and m.get("id")}

        str_url = f"https://raw.githubusercontent.com/{self.owner}/{self.repo}/{DEFAULT_BRANCH}/data/streams.json?t={int(time.time())}"
        str_json = http_json(str_url)
        streams_map = str_json if isinstance(str_json, dict) else {}

        hyphen_id = movie_id.replace(":", "-").replace("_", "-")
        safe_id = movie_id.replace(":", "_")
        clean_id = movie_id.replace("playrepo-", "").replace("playrepo:", "").replace("tmdb-", "").replace("tmdb:", "")
        del_ids = {movie_id, hyphen_id, safe_id, clean_id}

        keys_to_del = [k for k in meta_dict.keys() if any(d in k for d in del_ids)]
        for k in keys_to_del:
            meta_dict.pop(k, None)
        for d in del_ids:
            streams_map.pop(d, None)

        all_metas = list(meta_dict.values())
        files_to_commit = {
            "data/catalog.json": json.dumps({"metas": all_metas}, ensure_ascii=False, indent=2),
            "data/streams.json": json.dumps(streams_map, ensure_ascii=False, indent=2)
        }

        def has_genre(item, *genres):
            g_list = [g.lower() for g in item.get("genres", [])]
            return any(any(tgt in g for tgt in genres) for g in g_list)

        cat_configs = {
            "playrepo/catalog/movie/playrepo-popular.json": lambda x: True,
            "playrepo/catalog/movie/playrepo-catalog-movies.json": lambda x: True,
            "playrepo/catalog/movie/playrepo-aksiyon.json": lambda x: has_genre(x, "aksiyon", "macera"),
            "playrepo/catalog/movie/playrepo-komedi.json": lambda x: has_genre(x, "komedi", "mizah"),
            "playrepo/catalog/movie/playrepo-dram.json": lambda x: has_genre(x, "dram", "romantik"),
            "playrepo/catalog/movie/playrepo-korku.json": lambda x: has_genre(x, "korku", "gerilim"),
            "playrepo/catalog/movie/playrepo-bilimkurgu.json": lambda x: has_genre(x, "bilim", "kurgu", "fantastik")
        }

        for path, pred in cat_configs.items():
            filtered = [m for m in all_metas if pred(m)]
            files_to_commit[path] = json.dumps({"metas": filtered}, ensure_ascii=False, indent=2)

        ref_data = http_json(f"https://api.github.com/repos/{self.owner}/{self.repo}/git/ref/heads/{DEFAULT_BRANCH}", token=self.token)
        latest_commit_sha = ref_data.get("object", {}).get("sha")
        if not latest_commit_sha:
            return False

        commit_data = http_json(f"https://api.github.com/repos/{self.owner}/{self.repo}/git/commits/{latest_commit_sha}", token=self.token)
        base_tree_sha = commit_data.get("tree", {}).get("sha")

        tree_items = [
            {"path": path, "mode": "100644", "type": "blob", "content": content}
            for path, content in files_to_commit.items()
        ]
        # Silinen meta ve stream dosyalarını Git ağacından kaldır (sha: null)
        for did in del_ids:
            if did:
                tree_items.append({"path": f"playrepo/meta/movie/{did}.json", "mode": "100644", "type": "blob", "sha": None})
                tree_items.append({"path": f"playrepo/stream/movie/{did}.json", "mode": "100644", "type": "blob", "sha": None})
                tree_items.append({"path": f"playrepo/meta/series/{did}.json", "mode": "100644", "type": "blob", "sha": None})

        tree_res = http_json(
            f"https://api.github.com/repos/{self.owner}/{self.repo}/git/trees",
            method="POST",
            data={"base_tree": base_tree_sha, "tree": tree_items},
            token=self.token
        )
        new_tree_sha = tree_res.get("sha")
        if not new_tree_sha:
            return False

        new_commit_res = http_json(
            f"https://api.github.com/repos/{self.owner}/{self.repo}/git/commits",
            method="POST",
            data={"message": f"PlayRepo: {movie_id} filmini kaldır", "tree": new_tree_sha, "parents": [latest_commit_sha]},
            token=self.token
        )
        new_commit_sha = new_commit_res.get("sha")
        if not new_commit_sha:
            return False

        update_ref = http_json(
            f"https://api.github.com/repos/{self.owner}/{self.repo}/git/refs/heads/{DEFAULT_BRANCH}",
            method="PATCH",
            data={"sha": new_commit_sha, "force": True},
            token=self.token
        )
        if "object" in update_ref or update_ref.get("ref"):
            self.log(f"✓ [SİLİNDİ] {movie_id} eklentiden ve depodan tamamen kaldırıldı!")
            return True
        return False

# -------------------------------------------------------------
# Global State & WebApp Engine
# -------------------------------------------------------------
LOG_BUFFER = ["[READY] PlayRepo macOS Pro (Saf Siyah Tema) hazır."]

def load_config():
    default_cfg = {
        "github_token": "",
        "tmdb_key": "",
        "max_pages": 20,
        "saved_sites": [
            "https://www.hintfilmizle.com/film-izle/"
        ]
    }
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                default_cfg.update(json.load(f))
        except Exception:
            pass
    return default_cfg

def save_config(cfg):
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

def append_global_log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    LOG_BUFFER.append(f"[{ts}] {msg}")
    if len(LOG_BUFFER) > 500:
        LOG_BUFFER.pop(0)

CRAWLER_LOCK = threading.Lock()
CURRENT_WORKER = None
ENGINE_INSTANCE = ScraperEngine(log_callback=append_global_log)

HTML_PAGE = """<!DOCTYPE html>
<html lang="tr">
<head>
  <meta charset="UTF-8">
  <title>PlayRepo Pro • macOS</title>
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <style>
    :root {
      --bg: #000000;
      --card-bg: #0d0f17;
      --border: #23283b;
      --accent: #2563eb;
      --accent-hover: #1d4ed8;
      --emerald: #059669;
      --emerald-hover: #047857;
      --red: #dc2626;
      --text: #ffffff;
      --dim: #94a3b8;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      background-color: var(--bg);
      color: var(--text);
      font-family: -apple-system, BlinkMacSystemFont, "SF Pro Display", "Segoe UI", Roboto, sans-serif;
      padding: 24px 32px;
      line-height: 1.5;
      user-select: none;
    }
    header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      border-bottom: 2px solid var(--border);
      padding-bottom: 18px;
      margin-bottom: 24px;
    }
    .brand { display: flex; align-items: baseline; gap: 12px; }
    .brand h1 { font-size: 26px; font-weight: 800; color: #fff; letter-spacing: -0.5px; }
    .brand span { font-size: 13px; font-weight: 600; color: #60a5fa; background: #172554; padding: 4px 10px; border-radius: 6px; }
    
    .card {
      background: var(--card-bg);
      border: 2px solid var(--border);
      border-radius: 14px;
      padding: 20px 24px;
      margin-bottom: 22px;
      box-shadow: 0 8px 30px rgba(0,0,0,0.8);
    }
    .card.highlight {
      border: 2px solid #3b82f6;
      background: #090b10;
    }
    .card-title {
      font-size: 13px;
      font-weight: 800;
      color: #60a5fa;
      letter-spacing: 0.8px;
      text-transform: uppercase;
      margin-bottom: 12px;
      display: flex;
      align-items: center;
      gap: 8px;
    }

    .row { display: flex; gap: 12px; align-items: center; }
    .col { display: flex; flex-direction: column; gap: 6px; }

    input[type="text"], input[type="password"], input[type="number"] {
      width: 100%;
      background: #141722;
      border: 2px solid #3b82f6;
      color: #ffffff;
      padding: 14px 16px;
      font-size: 15px;
      font-weight: 600;
      border-radius: 8px;
      outline: none;
      transition: all 0.2s;
    }
    input:focus {
      border-color: #60a5fa;
      box-shadow: 0 0 0 3px rgba(59, 130, 246, 0.35);
      background: #191e2b;
    }

    button {
      padding: 13px 24px;
      font-size: 14px;
      font-weight: 700;
      border-radius: 8px;
      cursor: pointer;
      border: none;
      color: #fff;
      display: inline-flex;
      align-items: center;
      gap: 8px;
      white-space: nowrap;
      transition: all 0.15s ease-in-out;
    }
    button:active { transform: scale(0.98); }
    .btn-blue { background: var(--accent); }
    .btn-blue:hover { background: var(--accent-hover); }
    .btn-green { background: var(--emerald); }
    .btn-green:hover { background: var(--emerald-hover); }
    .btn-red { background: var(--red); }
    .btn-red:hover { background: #b91c1c; }
    .btn-gray { background: #1e2433; border: 1px solid var(--border); color: #cbd5e1; }
    .btn-gray:hover { background: #272f42; }

    /* Sites List */
    .site-item {
      display: flex;
      justify-content: space-between;
      align-items: center;
      background: #11141e;
      border: 1px solid var(--border);
      padding: 12px 18px;
      border-radius: 8px;
      margin-bottom: 8px;
      font-size: 14px;
      font-weight: 600;
    }
    .site-item span { color: #f1f5f9; word-break: break-all; }
    .site-item button { padding: 6px 14px; font-size: 12px; }

    /* Terminal Console */
    #terminal {
      background: #040507;
      border: 2px solid var(--border);
      border-radius: 10px;
      padding: 16px;
      font-family: 'SF Mono', Menlo, Monaco, Consolas, monospace;
      font-size: 12.5px;
      color: #34d399;
      height: 250px;
      overflow-y: auto;
      line-height: 1.6;
      white-space: pre-wrap;
    }

    .badge {
      background: #1e293b;
      color: #94a3b8;
      font-size: 12px;
      padding: 3px 8px;
      border-radius: 4px;
      font-weight: 700;
    }
  </style>
</head>
<body>

  <header>
    <div class="brand">
      <h1>🎬 PlayRepo Pro</h1>
      <span>macOS Edition</span>
    </div>
    <div class="row">
      <button class="btn-gray" onclick="clearLogs()">🧹 Logları Temizle</button>
      <button id="btn-stop" class="btn-red" onclick="stopCrawler()" style="display:none">⏹ Durdur</button>
      <button id="btn-run" class="btn-blue" onclick="startMasterUpdate()">⚡ TÜM SİTELERİ GÜNCELLE VE YÜKLE</button>
    </div>
  </header>

  <!-- 1. YENİ SİTE EKLEME (EN BELİRGİN KUTU) -->
  <div class="card highlight">
    <div class="card-title">🌐 1. YENİ FİLM SİTESİ VEYA KATEGORİ ADRESİ EKLE</div>
    <div class="row">
      <input type="text" id="new-site-input" placeholder="Örn: https://www.hintfilmizle.com/film-izle/ veya taranacak site linki" />
      <button class="btn-green" onclick="addSite()">➕ SİTEYİ LİSTEYE EKLE</button>
    </div>
  </div>

  <!-- 2. KAYITLI SİTELER & AYARLAR KONTROLLERİ -->
  <div class="row" style="align-items: flex-start; margin-bottom: 22px;">
    
    <!-- Sol: Kayıtlı Siteler -->
    <div class="card" style="flex: 1.3; margin-bottom: 0;">
      <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px;">
        <div class="card-title" style="margin-bottom:0">📋 TARANACAK KAYITLI SİTELER (<span id="site-count">0</span>)</div>
        <button class="btn-gray" style="padding: 5px 12px; font-size: 11px;" onclick="resetSites()">🔄 Sıfırla</button>
      </div>
      <div id="sites-list" style="max-height: 160px; overflow-y: auto;"></div>
    </div>

    <!-- Sağ: Sayfa Limiti & Token -->
    <div class="card" style="flex: 1; margin-bottom: 0;">
      <div class="card-title">⚙️ AYARLAR</div>
      <div class="col" style="margin-bottom: 12px;">
        <label style="font-size:12px; font-weight:700; color:var(--dim)">SAYFA LİMİTİ (SİTE BAŞI):</label>
        <input type="number" id="max-pages-input" value="20" style="padding: 10px;" />
      </div>
      <div class="col" style="margin-bottom: 14px;">
        <label style="font-size:12px; font-weight:700; color:var(--dim)">GİTHUB TOKEN (PAT):</label>
        <input type="password" id="token-input" placeholder="ghp_xxxxxxxxxxxx" style="padding: 10px;" />
      </div>
      <button class="btn-gray" style="width:100%; justify-content:center;" onclick="saveSettings()">💾 Ayarları Kaydet</button>
    </div>

  </div>

  <!-- 3. CANLI TERMİNAL -->
  <div class="card" style="padding: 16px 20px;">
    <div class="card-title" style="display: flex; justify-content: space-between;">
      <span>💻 CANLI TERMİNAL / İŞLEM ÇIKTISI</span>
      <span id="status-badge" class="badge">HAZIR</span>
    </div>
    <div id="terminal"></div>
  </div>

  <!-- 4. YAYINLANMIŞ FİLMLER VE DİZİLER (LİSTE VE SİLME YÖNETİMİ) -->
  <div class="card" style="padding: 20px 24px;">
    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 14px;">
      <div class="card-title" style="margin-bottom:0">🎬 EKLENTİDE YAYINLANAN FİLMLER & DİZİLER (<span id="pub-count">0</span>)</div>
      <button class="btn-gray" style="padding: 6px 14px; font-size: 12px;" onclick="loadPublishedMovies()">🔄 Listeyi Yenile</button>
    </div>
    <div style="margin-bottom: 12px;">
      <input type="text" id="movie-search" placeholder="🔍 Yayındaki filmlerde veya dizilerde ara..." oninput="filterPublishedMovies()" style="padding: 10px 14px; font-size: 13px;" />
    </div>
    <div id="movies-container" style="max-height: 380px; overflow-y: auto; display: flex; flex-direction: column; gap: 8px;">
      <div style="color: var(--dim); font-size: 13px; padding: 10px;">Filmler yükleniyor...</div>
    </div>
  </div>

  <script>
    let currentConfig = { saved_sites: [], max_pages: 20, github_token: "", tmdb_key: "" };

    async function loadConfig() {
      try {
        const res = await fetch('/api/config');
        currentConfig = await res.json();
        document.getElementById('max-pages-input').value = currentConfig.max_pages || 20;
        document.getElementById('token-input').value = currentConfig.github_token || "";
        renderSites();
      } catch (e) {
        console.error(e);
      }
    }

    function renderSites() {
      const list = document.getElementById('sites-list');
      list.innerHTML = "";
      const sites = currentConfig.saved_sites || [];
      document.getElementById('site-count').innerText = sites.length;
      if (sites.length === 0) {
        list.innerHTML = "<div style='color:#64748b; font-size:13px; padding:10px;'>Kayıtlı site bulunmuyor. Yukarıdan site ekleyin.</div>";
        return;
      }
      sites.forEach((site, idx) => {
        const item = document.createElement('div');
        item.className = 'site-item';
        item.innerHTML = `
          <span>🌐 ${site}</span>
          <button class="btn-red" onclick="deleteSite(${idx})">🗑️ Sil</button>
        `;
        list.appendChild(item);
      });
    }

    async function addSite() {
      const inp = document.getElementById('new-site-input');
      const url = inp.value.trim();
      if (!url.startsWith('http')) {
        alert('Lütfen geçerli bir site linki girin! (http:// veya https://)');
        return;
      }
      await fetch('/api/add-site', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ url })
      });
      inp.value = "";
      await loadConfig();
    }

    async function deleteSite(idx) {
      await fetch('/api/delete-site', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ index: idx })
      });
      await loadConfig();
    }

    async function resetSites() {
      if (confirm('Site listesi varsayılana sıfırlansın mı?')) {
        await fetch('/api/reset-sites', { method: 'POST' });
        await loadConfig();
      }
    }

    async function saveSettings() {
      const maxPages = parseInt(document.getElementById('max-pages-input').value) || 20;
      const token = document.getElementById('token-input').value.trim();
      await fetch('/api/save-config', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ max_pages: maxPages, github_token: token })
      });
      alert('Ayarlar başarıyla kaydedildi!');
      await loadConfig();
    }

    async function startMasterUpdate() {
      document.getElementById('btn-run').style.display = 'none';
      document.getElementById('btn-stop').style.display = 'inline-flex';
      document.getElementById('status-badge').innerText = 'ÇALIŞIYOR';
      document.getElementById('status-badge').style.background = '#065f46';
      document.getElementById('status-badge').style.color = '#34d399';

      await fetch('/api/start-all', { method: 'POST' });
    }

    async function stopCrawler() {
      await fetch('/api/stop', { method: 'POST' });
      document.getElementById('btn-run').style.display = 'inline-flex';
      document.getElementById('btn-stop').style.display = 'none';
      document.getElementById('status-badge').innerText = 'DURDURULDU';
      document.getElementById('status-badge').style.background = '#7f1d1d';
      document.getElementById('status-badge').style.color = '#f87171';
    }

    async function clearLogs() {
      await fetch('/api/clear-logs', { method: 'POST' });
      document.getElementById('terminal').innerText = "";
    }

    async function pollLogs() {
      try {
        const res = await fetch('/api/logs');
        const data = await res.json();
        const term = document.getElementById('terminal');
        term.innerText = data.logs.join('\\n');
        term.scrollTop = term.scrollHeight;

        if (!data.is_running) {
          document.getElementById('btn-run').style.display = 'inline-flex';
          document.getElementById('btn-stop').style.display = 'none';
          document.getElementById('status-badge').innerText = 'HAZIR';
          document.getElementById('status-badge').style.background = '#1e293b';
          document.getElementById('status-badge').style.color = '#94a3b8';
        }
      } catch (e) {}
      setTimeout(pollLogs, 1000);
    }

    let allPublishedMovies = [];

    async function loadPublishedMovies() {
      try {
        const res = await fetch('/api/published-movies');
        const data = await res.json();
        allPublishedMovies = data.movies || [];
        document.getElementById('pub-count').innerText = allPublishedMovies.length;
        renderPublishedMovies(allPublishedMovies);
      } catch (e) {
        console.error(e);
      }
    }

    function renderPublishedMovies(movies) {
      const cont = document.getElementById('movies-container');
      cont.innerHTML = "";
      if (movies.length === 0) {
        cont.innerHTML = "<div style='color:#64748b; font-size:13px; padding:10px;'>Henüz yayınlanmış film/dizi bulunmuyor.</div>";
        return;
      }
      movies.forEach(m => {
        const item = document.createElement('div');
        item.style.cssText = "display: flex; justify-content: space-between; align-items: center; background: #11141e; border: 1px solid var(--border); padding: 10px 16px; border-radius: 8px;";
        const genres = (m.genres || []).slice(0, 3).join(', ');
        item.innerHTML = `
          <div style="display: flex; align-items: center; gap: 12px; overflow: hidden;">
            <img src="${m.poster || ''}" onerror="this.src='${currentConfig.logo || ''}'" style="width: 38px; height: 52px; object-fit: cover; border-radius: 4px; background: #1e2433;" />
            <div style="display: flex; flex-direction: column; overflow: hidden;">
              <span style="font-weight: 700; font-size: 14px; color: #fff; text-overflow: ellipsis; white-space: nowrap; overflow: hidden;">${m.name} <span style="font-size:12px; color:var(--dim);">(${m.releaseInfo || m.year || ''})</span></span>
              <span style="font-size: 11px; color: #60a5fa;">${genres || 'Genel'} • <span style="color:#94a3b8;">${m.type === 'series' ? 'Dizi' : 'Film'}</span></span>
            </div>
          </div>
          <button class="btn-red" style="padding: 6px 14px; font-size: 12px;" onclick="deleteMovie('${m.id}', '${m.name.replace(/'/g, "\\'")}')">🗑️ Sil</button>
        `;
        cont.appendChild(item);
      });
    }

    function filterPublishedMovies() {
      const q = document.getElementById('movie-search').value.toLowerCase().trim();
      if (!q) {
        renderPublishedMovies(allPublishedMovies);
        return;
      }
      const filtered = allPublishedMovies.filter(m => (m.name || '').toLowerCase().includes(q) || (m.genres || []).some(g => g.toLowerCase().includes(q)));
      renderPublishedMovies(filtered);
    }

    async function deleteMovie(id, name) {
      if (!confirm(`"${name}" filmini eklentiden ve depodan silmek istediğinize emin misiniz?`)) return;
      document.getElementById('terminal').innerText += `\\n[İŞLEM] "${name}" siliniyor...\\n`;
      const res = await fetch('/api/delete-movie', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ id })
      });
      const data = await res.json();
      if (data.status === 'ok') {
        alert(`"${name}" başarıyla silindi!`);
        await loadPublishedMovies();
      } else {
        alert('Silme sırasında hata oluştu: ' + (data.message || ''));
      }
    }

    window.onload = () => {
      loadConfig();
      loadPublishedMovies();
      pollLogs();
    };
  </script>
</body>
</html>
"""

# -------------------------------------------------------------
# Web Server Request Handler
# -------------------------------------------------------------
class RequestHandler(BaseHTTPRequestHandler):
    def _send_json(self, data, code=200):
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode("utf-8"))

    def _read_json(self):
        len_str = self.headers.get("Content-Length", 0)
        length = int(len_str)
        if length > 0:
            body = self.rfile.read(length).decode("utf-8")
            return json.loads(body)
        return {}

    def do_GET(self):
        if self.path == "/" or self.path == "/index.html":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(HTML_PAGE.encode("utf-8"))
        elif self.path == "/api/config":
            self._send_json(load_config())
        elif self.path == "/api/logs":
            self._send_json({
                "logs": LOG_BUFFER,
                "is_running": ENGINE_INSTANCE.is_running and CURRENT_WORKER is not None and CURRENT_WORKER.is_alive()
            })
        elif self.path == "/api/published-movies":
            cat_url = f"https://raw.githubusercontent.com/{REPO_OWNER}/{REPO_NAME}/{DEFAULT_BRANCH}/data/catalog.json?t={int(time.time())}"
            cat_json = http_json(cat_url)
            metas = cat_json.get("metas", []) if isinstance(cat_json, dict) else []
            self._send_json({"movies": metas})
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        global CURRENT_WORKER
        if self.path == "/api/delete-movie":
            body = self._read_json()
            movie_id = body.get("id", "").strip()
            cfg = load_config()
            token = cfg.get("github_token", "").strip()
            if movie_id and token:
                pub = GitHubAtomicPublisher(token=token, log_callback=append_global_log)
                success = pub.delete_movie(movie_id)
                self._send_json({"status": "ok" if success else "error"})
            else:
                self._send_json({"status": "error", "message": "Geçersiz ID veya Token eksik"})
        elif self.path == "/api/save-config":
            body = self._read_json()
            cfg = load_config()
            cfg.update(body)
            save_config(cfg)
            self._send_json({"status": "ok"})
        elif self.path == "/api/add-site":
            body = self._read_json()
            url = body.get("url", "").strip()
            if url:
                cfg = load_config()
                saved = cfg.setdefault("saved_sites", [])
                if url not in saved:
                    saved.append(url)
                    save_config(cfg)
                    append_global_log(f"[SİTE EKLENDİ] {url}")
            self._send_json({"status": "ok"})
        elif self.path == "/api/delete-site":
            body = self._read_json()
            idx = body.get("index", -1)
            cfg = load_config()
            saved = cfg.get("saved_sites", [])
            if 0 <= idx < len(saved):
                removed = saved.pop(idx)
                save_config(cfg)
                append_global_log(f"[SİTE SİLİNDİ] {removed}")
            self._send_json({"status": "ok"})
        elif self.path == "/api/reset-sites":
            cfg = load_config()
            cfg["saved_sites"] = ["https://www.hintfilmizle.com/film-izle/"]
            save_config(cfg)
            append_global_log("[SIFIRLANDI] Site listesi varsayılana sıfırlandı.")
            self._send_json({"status": "ok"})
        elif self.path == "/api/clear-logs":
            LOG_BUFFER.clear()
            self._send_json({"status": "ok"})
        elif self.path == "/api/stop":
            ENGINE_INSTANCE.is_running = False
            append_global_log("[DURDURULDU] İşlem durduruldu.")
            self._send_json({"status": "ok"})
        elif self.path == "/api/start-all":
            cfg = load_config()
            sites = cfg.get("saved_sites", [])
            max_p = int(cfg.get("max_pages", 20))
            token = cfg.get("github_token", "").strip()
            tmdb = cfg.get("tmdb_key", "").strip()

            ENGINE_INSTANCE.is_running = True

            def worker():
                append_global_log(f"[GÜNCELLEME BAŞLADI] Toplam {len(sites)} kayıtlı sitenin tamamı taranıyor...")
                all_scraped = []
                for s_idx, site_url in enumerate(sites):
                    if not ENGINE_INSTANCE.is_running:
                        break
                    append_global_log(f"--- [SİTE {s_idx + 1}/{len(sites)}] Taranıyor: {site_url} ---")
                    movie_dict = ENGINE_INSTANCE.discover_movie_links(site_url, max_pages=max_p)
                    append_global_log(f"✓ {site_url} üzerinde {len(movie_dict)} film bulundu. Video akışları çözülüyor...")

                    pub = GitHubAtomicPublisher(token=token, log_callback=append_global_log)
                    batch_buffer = []

                    for m_idx, (m_url, cats) in enumerate(movie_dict.items()):
                        if not ENGINE_INSTANCE.is_running:
                            break
                        short = m_url.rstrip("/").split("/")[-1]
                        append_global_log(f"[{m_idx + 1}/{len(movie_dict)}] Çözülüyor: {short}...")
                        m_data = ENGINE_INSTANCE.scrape_movie_page(m_url, cats, tmdb_api_key=tmdb)
                        if m_data and m_data.get("streams"):
                            batch_buffer.append(m_data)
                            s_count = len(m_data["streams"])
                            c_str = ", ".join(m_data["genres"])
                            append_global_log(f"  ✓ {m_data['title']} ({s_count} Sunucu • [{c_str}])")

                            # Her 3 film çözüldüğünde beklemeden anında GitHub ve Nuvio'ya aktar!
                            if len(batch_buffer) >= 3:
                                append_global_log(f"⚡ [ANLIK YÜKLEME] Çözülen {len(batch_buffer)} film anında Nuvio/GitHub'a yükleniyor...")
                                pub.publish_movies(batch_buffer)
                                batch_buffer.clear()
                        else:
                            append_global_log(f"  ↷ {short} (Aktif akış yok, atlandı)")

                    # Kalan filmleri hemen aktar
                    if batch_buffer and ENGINE_INSTANCE.is_running:
                        append_global_log(f"⚡ [ANLIK YÜKLEME] Kalan {len(batch_buffer)} film Nuvio/GitHub'a aktarılıyor...")
                        pub.publish_movies(batch_buffer)
                        batch_buffer.clear()

                append_global_log("✓ [BİTTİ] Tüm tarama ve anlık yükleme tamamlandı.")

            CURRENT_WORKER = threading.Thread(target=worker, daemon=True)
            CURRENT_WORKER.start()
            self._send_json({"status": "started"})
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        pass

def run_cli_crawl(site_url: str, max_pages: int = 20, token: str = None):
    print(f"🎬 [PlayRepo CLI] Taranıyor: {site_url} (Max sayfa: {max_pages})...")
    engine = ScraperEngine(log_callback=print)
    movie_dict = engine.discover_movie_links(site_url, max_pages=max_pages)
    all_scraped = []
    for m_idx, (m_url, cats) in enumerate(movie_dict.items()):
        short = m_url.rstrip("/").split("/")[-1]
        print(f"[{m_idx + 1}/{len(movie_dict)}] Çözülüyor: {short}...")
        m_data = engine.scrape_movie_page(m_url, cats)
        if m_data and m_data.get("streams"):
            all_scraped.append(m_data)
            print(f"  ✓ {m_data['title']} ({len(m_data['streams'])} Sunucu)")
    if all_scraped:
        print(f"✓ [PlayRepo CLI] {len(all_scraped)} film ve detayları çözüldü. GitHub'a aktarılıyor...")
        eff_token = token or os.getenv("GITHUB_TOKEN", "")
        pub = GitHubAtomicPublisher(token=eff_token, log_callback=print)
        pub.publish_movies(all_scraped)
    else:
        print("✗ [PlayRepo CLI] Film bulunamadı veya akışlar çözülemedi.")

ACTIVE_PORT_FILE = "/tmp/playrepo_active.port"

def is_server_alive(port: int) -> bool:
    try:
        with socket.create_connection(('127.0.0.1', port), timeout=0.8):
            return True
    except Exception:
        return False

def open_browser_window(url: str):
    chrome_bin = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    profile_dir = "/tmp/playrepo_desktop_profile"
    if os.path.exists(chrome_bin):
        try:
            subprocess.Popen([
                chrome_bin,
                f"--app={url}",
                f"--user-data-dir={profile_dir}",
                "--window-size=1150,880",
                "--no-first-run",
                "--no-default-browser-check"
            ])
            subprocess.run(["osascript", "-e", 'tell application "Google Chrome" to activate'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return
        except Exception:
            pass
    subprocess.run(["open", url])

def run_app():
    if os.path.exists(ACTIVE_PORT_FILE):
        try:
            with open(ACTIVE_PORT_FILE, "r") as f:
                saved_port = int(f.read().strip())
            if is_server_alive(saved_port):
                open_browser_window(f"http://127.0.0.1:{saved_port}")
                sys.exit(0)
        except Exception:
            pass

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]
    sock.close()

    try:
        with open(ACTIVE_PORT_FILE, "w") as f:
            f.write(str(port))
    except Exception:
        pass

    server = HTTPServer(('127.0.0.1', port), RequestHandler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    url = f"http://127.0.0.1:{port}"
    open_browser_window(url)

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        server.shutdown()
        if os.path.exists(ACTIVE_PORT_FILE):
            try:
                os.remove(ACTIVE_PORT_FILE)
            except Exception:
                pass

if __name__ == "__main__":
    if "--crawl" in sys.argv:
        import argparse
        parser = argparse.ArgumentParser(description="PlayRepo Headless Scraper")
        parser.add_argument("--crawl", type=str, required=True, help="Site URL to crawl")
        parser.add_argument("--max-pages", type=int, default=20)
        parser.add_argument("--token", type=str, default=os.getenv("GITHUB_TOKEN", ""))
        args = parser.parse_args()
        run_cli_crawl(args.crawl, args.max_pages, args.token)
    else:
        run_app()
