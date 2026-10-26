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
PROCESSED_MOVIES_FILE = os.path.expanduser("~/.playrepo_processed_urls.json")
REPO_OWNER = "csprofesor"
REPO_NAME = "apple"
DEFAULT_BRANCH = "main"
LOGO_URL = "https://cdn.jsdelivr.net/gh/csprofesor/apple@main/playrepo/logo.png"

WORKSPACE_DIR = os.environ.get("PLAYREPO_WORKSPACE", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if not os.path.exists(os.path.join(WORKSPACE_DIR, "data")):
    if os.path.exists("/Users/gokturksahan/apple/data"):
        WORKSPACE_DIR = "/Users/gokturksahan/apple"
    else:
        WORKSPACE_DIR = os.path.expanduser("~/.playrepo_workspace")
        os.makedirs(os.path.join(WORKSPACE_DIR, "data"), exist_ok=True)

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
        self.is_paused = False
        self.pause_cond = threading.Condition()

    def check_alive(self) -> bool:
        """Pauses execution if paused, returns False immediately if stopped."""
        with self.pause_cond:
            while self.is_paused and self.is_running:
                self.pause_cond.wait(timeout=0.4)
        return self.is_running

    def pause(self):
        with self.pause_cond:
            self.is_paused = True
            self.pause_cond.notify_all()

    def resume(self):
        with self.pause_cond:
            self.is_paused = False
            self.pause_cond.notify_all()

    def stop(self):
        with self.pause_cond:
            self.is_running = False
            self.is_paused = False
            self.pause_cond.notify_all()

    def log(self, msg: str):
        self.log_callback(msg)

    def detect_category(self, url: str, text: str) -> str:
        lower = f"{url.lower()} {text.lower()}"
        if any(k in lower for k in ["animasyon", "animation", "çizgi", "anime", "çizgi dizi", "çizgi film"]):
            return "Animasyon & Çizgi Dizi"
        if any(k in lower for k in ["belgesel", "documentary", "tarih", "history", "biyografi", "biography"]):
            return "Belgesel & Tarih"
        if any(k in lower for k in ["suç", "suc", "crime", "polisiye", "gizem", "mystery", "dedektif", "cinayet"]):
            return "Suç, Polisiye & Gizem"
        if any(k in lower for k in ["korku", "gerilim", "horror", "thriller", "slasher"]):
            return "Korku & Gerilim"
        if any(k in lower for k in ["aksiyon", "macera", "action", "adventure", "dövüş", "dovus", "savaş", "savas", "western", "kovboy"]):
            return "Aksiyon & Macera"
        if any(k in lower for k in ["komedi", "mizah", "comedy", "parodi", "kara mizah", "aile", "family"]):
            return "Komedi"
        if any(k in lower for k in ["dram", "drama", "romantik", "romance", "aşk", "ask", "duygusal", "melodram"]):
            return "Dram & Romantik"
        if any(k in lower for k in ["bilim", "kurgu", "fantastik", "sci-fi", "scifi", "fantasy", "uzay", "süper kahraman", "distopya"]):
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
            "/seri-filmler", "/istek", "/dmca", "/gizlilik", "/privacy", "/contact",
            ".xml", ".rss", "sitemap"
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
            if not self.check_alive():
                break
            try:
                sm_content = http_get(sm_url, timeout=5)
                if sm_content and ("<urlset" in sm_content or "<sitemapindex" in sm_content or "<rss" in sm_content):
                    self.log(f"⚡ [Sitemap Keşfi] {sm_url} üzerinden doğrudan indeks taranıyor...")
                    locs = re.findall(r'<loc>(https?://[^<]+)</loc>', sm_content, re.I) or re.findall(r'<link>(https?://[^<]+)</link>', sm_content, re.I)
                    sub_sitemaps_fetched = 0
                    for loc in locs:
                        loc = loc.strip()
                        if loc.endswith(".xml") or "/sitemap" in loc:
                            # Alt sitemap tespiti (Örn: post-sitemap1.xml, en fazla 3 adet taranır)
                            if sub_sitemaps_fetched >= 3:
                                continue
                            sub_sitemaps_fetched += 1
                            try:
                                sub_content = http_get(loc, timeout=3)
                                if sub_content:
                                    sub_locs = re.findall(r'<loc>(https?://[^<]+)</loc>', sub_content, re.I)
                                    for sloc in sub_locs:
                                        sloc = sloc.strip()
                                        if self.is_valid_movie_url(sloc, site_base):
                                            cats = movie_categories.setdefault(sloc, set())
                                            cats.add(self.detect_category(sloc, ""))
                                            sitemap_found_count += 1
                            except Exception:
                                pass
                        elif self.is_valid_movie_url(loc, site_base):
                            cats = movie_categories.setdefault(loc, set())
                            cat_from_url = self.detect_category(loc, "")
                            cats.add(cat_from_url)
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
        while queue and page_count < max_pages and self.check_alive():
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

    def search_specific_movie_across_sites(self, query: str, sites: list) -> dict:
        """
        Kullanıcının girdiği film adını kayıtlı tüm sitelerin arama motorlarında (?s=, /search/, /ara?)
        hedefli olarak arar ve ilgili filmin doğrudan detay sayfalarını bulur.
        """
        q_norm = query.strip()
        q_slug = re.sub(r'[^a-zA-Z0-9çğıöşüÇĞİÖŞÜ]+', '-', q_norm).strip('-').lower()
        q_enc = urllib.parse.quote_plus(q_norm)
        tokens = [t.lower() for t in re.split(r'[\s\-_\.:]+', q_norm) if len(t) >= 2]

        found_links = {}

        for site_url in sites:
            if not self.check_alive():
                break

            site_base = site_url
            if "://" in site_url:
                parts = site_url.split("/")
                site_base = f"{parts[0]}//{parts[2]}"

            search_endpoints = [
                f"{site_base}/?s={q_enc}",
                f"{site_base}/search/{q_enc}/",
                f"{site_base}/arama/{q_enc}/",
                f"{site_base}/ara?q={q_enc}",
                f"{site_base}/{q_slug}-izle/",
                f"{site_base}/{q_slug}/",
                f"{site_base}/film/{q_slug}/"
            ]

            self.log(f"🔎 [{site_base}] '{q_norm}' aranıyor...")

            site_found = 0
            for ep_url in search_endpoints:
                if not self.check_alive() or site_found >= 3:
                    break
                try:
                    html = http_get(ep_url, timeout=7)
                    if not html or "cf-chl-opt" in html:
                        continue

                    # Doğrudan film sayfasına mı yönlendi / geldik?
                    if self.is_valid_movie_url(ep_url, site_base) and any(tok in ep_url.lower() for tok in tokens):
                        found_links[ep_url] = {"Popüler"}
                        site_found += 1
                        break

                    # Arama sonuç listesindeki film bağlantılarını filtrele
                    for href, text in re.findall(r'<a\s+[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', html, re.I | re.DOTALL):
                        abs_url = urllib.parse.urljoin(ep_url, href.strip())
                        clean_text = re.sub(r'<[^>]+>', '', text).strip()
                        combined = f"{abs_url.lower()} {clean_text.lower()}"

                        if self.is_valid_movie_url(abs_url, site_base):
                            if all(tok in combined for tok in tokens) or (tokens and tokens[0] in combined):
                                if abs_url not in found_links:
                                    cat = self.detect_category(abs_url, clean_text)
                                    found_links[abs_url] = {cat}
                                    site_found += 1
                                    self.log(f"  🎯 Eşleşen film bulundu: {clean_text or abs_url}")
                except Exception:
                    pass

        return found_links

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
                    fv = ok_data.get("flashvars", {})
                    meta = fv.get("metadata", {})
                    if isinstance(meta, str):
                        try:
                            meta = json.loads(meta)
                        except Exception:
                            meta = {}
                    hls = meta.get("ondemandHls") or meta.get("hlsManifestUrl") or fv.get("ondemandHls") or fv.get("hlsManifestUrl")
                    if hls:
                        return hls
                    videos = meta.get("videos", [])
                    if videos and isinstance(videos, list):
                        last_v = videos[-1].get("url")
                        if last_v:
                            return last_v
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

        genres = set(initial_cats) if initial_cats else set()
        for href, text in re.findall(r'<a\s+[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', html, re.I | re.DOTALL):
            if any(k in href.lower() for k in ["/kategori/", "/tur/", "/filmizle/", "/genre/", "/filmler/"]):
                cat = self.detect_category(href, re.sub(r'<[^>]+>', '', text).strip())
                if cat != "Popüler":
                    genres.add(cat)

        # "Tür: Aksiyon, Dram" gibi metin bloklarını tara
        for m in re.finditer(r'(?:Tür(?:ü|ler)?|Kategori|Genres?)\s*[:：]\s*([^<\n\r]+)', html, re.I):
            clean_block = re.sub(r'<[^>]+>', '', m.group(1)).strip()
            cat = self.detect_category("", clean_block)
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
        combo_m = re.search(r'(\d+)[-\s_]*(?:sezon|season)[-\s_]*(\d+)[-\s_]*(?:bolum|bölüm|episode)', movie_url, re.I)
        if combo_m:
            is_series = True
            season_num = int(combo_m.group(1))
            episode_num = int(combo_m.group(2))
        else:
            is_series = "/dizi/" in movie_url.lower() or "/series/" in movie_url.lower() or "sezon" in movie_url.lower() or ("sezon" in html.lower() and "bölüm" in html.lower())
            season_m = re.search(r'(?:sezon|season)[-\s_]*(\d+)', movie_url, re.I) or re.search(r'(\d+)\s*\.\s*(?:sezon|season)', raw_title, re.I)
            episode_m = re.search(r'(?:bolum|bölüm|episode)[-\s_]*(\d+)', movie_url, re.I) or re.search(r'(\d+)\s*\.\s*(?:bolum|bölüm|episode)', raw_title, re.I)
            season_num = int(season_m.group(1)) if season_m else None
            episode_num = int(episode_m.group(1)) if episode_m else None

        resolved_streams = []
        seen_urls = set()

        for cand_url, cand_lang in candidate_embeds:
            if not self.check_alive():
                break
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

        meta_id, official_title, official_poster, official_desc, release_year, meta_genres = self.resolve_meta(
            clean_title, poster, desc, year, tmdb_api_key, is_series=is_series
        )

        # IMDb / Cinemeta / TMDB doğrulamalı kategoriler
        verified_categories = set()
        if meta_genres:
            for g in meta_genres:
                cat = self.detect_category("", g)
                if cat != "Popüler":
                    verified_categories.add(cat)

        # Eğer IMDb/Cinemeta/TMDB'den teyitli kategori geldiyse, sitenin güvenilmez kategorilerini ezip resmi kategorileri kullan
        if verified_categories:
            genres = verified_categories
        else:
            # IMDb/TMDB eşleşmediyse fallback olarak sayfadaki kategoriyi kullan
            if not genres:
                genres.add("Popüler")

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

    def resolve_meta(self, title: str, poster: str, desc: str, year: str, tmdb_key: str = None, is_series: bool = False):
        media_kind = "series" if is_series else "movie"
        try:
            q = urllib.parse.quote(title)
            cm_res = http_json(f"https://v3-cinemeta.strem.io/catalog/{media_kind}/top/search={q}.json")
            metas = cm_res.get("metas", [])
            if not metas and is_series:
                cm_res = http_json(f"https://v3-cinemeta.strem.io/catalog/movie/top/search={q}.json")
                metas = cm_res.get("metas", [])
            if metas:
                m0 = metas[0]
                m_id = m0.get("id")
                if m_id and m_id.startswith("tt"):
                    turkish_desc = desc if desc and desc != "Play • 1080p TR" else (m0.get("description") or "Play • 1080p TR")
                    extra_genres = m0.get("genres") or []
                    if not extra_genres:
                        try:
                            detail_res = http_json(f"https://v3-cinemeta.strem.io/meta/{m0.get('type', media_kind)}/{m_id}.json")
                            extra_genres = detail_res.get("meta", {}).get("genres") or []
                        except Exception:
                            pass
                    return (
                        m_id,
                        m0.get("name") or title,
                        m0.get("poster") or poster,
                        turkish_desc,
                        m0.get("releaseInfo") or year,
                        extra_genres
                    )
        except Exception:
            pass

        if tmdb_key:
            try:
                q = urllib.parse.quote(title)
                endpoint = "tv" if is_series else "movie"
                tm_res = http_json(f"https://api.themoviedb.org/3/search/{endpoint}?api_key={tmdb_key}&query={q}&language=tr-TR")
                results = tm_res.get("results", [])
                if results:
                    r0 = results[0]
                    r_id = r0.get("id")
                    if r_id:
                        p_path = r0.get("poster_path")
                        p_url = f"https://image.tmdb.org/t/p/w500{p_path}" if p_path else poster
                        rel_date = r0.get("release_date") or r0.get("first_air_date") or year
                        tmdb_genres = []
                        try:
                            det = http_json(f"https://api.themoviedb.org/3/{endpoint}/{r_id}?api_key={tmdb_key}&language=tr-TR")
                            tmdb_genres = [g.get("name") for g in det.get("genres", []) if g.get("name")]
                        except Exception:
                            pass
                        return (
                            f"tmdb-{r_id}",
                            r0.get("title") or r0.get("name") or title,
                            p_url,
                            r0.get("overview") or desc,
                            rel_date[:4] if rel_date else year,
                            tmdb_genres
                        )
            except Exception:
                pass

        slug = re.sub(r'[^a-z0-9]+', '-', title.lower()).strip('-')
        safe_id = f"playrepo-{slug}" if slug else f"playrepo-film-{abs(hash(title))}"
        return (safe_id, title, poster, desc, year, [])

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
            "playrepo/catalog/movie/playrepo-bilimkurgu.json": lambda x: has_genre(x, "bilim", "kurgu", "fantastik"),
            "playrepo/catalog/movie/playrepo-animasyon.json": lambda x: has_genre(x, "animasyon", "çizgi", "anime", "animation"),
            "playrepo/catalog/movie/playrepo-belgesel.json": lambda x: has_genre(x, "belgesel", "tarih", "biyografi", "documentary", "history"),
            "playrepo/catalog/movie/playrepo-suc.json": lambda x: has_genre(x, "suç", "suc", "polisiye", "gizem", "crime", "mystery"),
            "playrepo/catalog/series/playrepo-series-popular.json": lambda x: x.get("type") == "series"
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
            "playrepo/catalog/movie/playrepo-bilimkurgu.json": lambda x: has_genre(x, "bilim", "kurgu", "fantastik"),
            "playrepo/catalog/movie/playrepo-animasyon.json": lambda x: has_genre(x, "animasyon", "çizgi", "anime", "animation"),
            "playrepo/catalog/movie/playrepo-belgesel.json": lambda x: has_genre(x, "belgesel", "tarih", "biyografi", "documentary", "history"),
            "playrepo/catalog/movie/playrepo-suc.json": lambda x: has_genre(x, "suç", "suc", "polisiye", "gizem", "crime", "mystery"),
            "playrepo/catalog/series/playrepo-series-popular.json": lambda x: x.get("type") == "series"
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

    def clear_all_published_movies(self) -> bool:
        if not self.token:
            self.log("✗ [ERROR] GitHub Token girilmemiş!")
            return False

        self.log("[TOPLU SİLME] Eklentideki ve depodaki tüm filmler sıfırlanıyor...")

        empty_catalog = json.dumps({"metas": []}, ensure_ascii=False, indent=2)
        empty_streams = json.dumps({}, ensure_ascii=False, indent=2)

        files_to_commit = {
            "data/catalog.json": empty_catalog,
            "data/streams.json": empty_streams,
            "playrepo/catalog/movie/playrepo-popular.json": empty_catalog,
            "playrepo/catalog/movie/playrepo-catalog-movies.json": empty_catalog,
            "playrepo/catalog/movie/playrepo-aksiyon.json": empty_catalog,
            "playrepo/catalog/movie/playrepo-komedi.json": empty_catalog,
            "playrepo/catalog/movie/playrepo-dram.json": empty_catalog,
            "playrepo/catalog/movie/playrepo-korku.json": empty_catalog,
            "playrepo/catalog/movie/playrepo-bilimkurgu.json": empty_catalog,
            "playrepo/catalog/movie/playrepo-animasyon.json": empty_catalog,
            "playrepo/catalog/movie/playrepo-belgesel.json": empty_catalog,
            "playrepo/catalog/movie/playrepo-suc.json": empty_catalog,
            "playrepo/catalog/series/playrepo-series-popular.json": empty_catalog
        }

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

        # Mevcut ağacı recursive çekip playrepo/meta ve playrepo/stream altındaki tüm dosyaları sha: None yaparak sil
        try:
            full_tree = http_json(f"https://api.github.com/repos/{self.owner}/{self.repo}/git/trees/{base_tree_sha}?recursive=1", token=self.token)
            for item in full_tree.get("tree", []):
                p = item.get("path", "")
                if p.startswith("playrepo/meta/") or p.startswith("playrepo/stream/"):
                    if item.get("type") == "blob":
                        tree_items.append({"path": p, "mode": "100644", "type": "blob", "sha": None})
        except Exception:
            pass

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
            data={"message": "PlayRepo: Tüm yayınlanmış filmleri sıfırla", "tree": new_tree_sha, "parents": [latest_commit_sha]},
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
            self.log("✓ [TEMİZLENDİ] Tüm filmler ve akışlar eklentiden başarıyla sıfırlandı!")
            try:
                if os.path.exists(PROCESSED_MOVIES_FILE):
                    os.remove(PROCESSED_MOVIES_FILE)
            except Exception:
                pass
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
            "https://diziboxizle.com/",
            "https://ddizimom.net/",
            "https://dizirex.com/",
            "https://fullhdfilmizlebox.vip/",
            "https://filmdozu.com/",
            "https://ufilmizle.com/",
            "https://fullfilmizle.fit/",
            "https://www.hintfilmizle.com/film-izle/",
            "https://filmarsivi.org/"
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

def load_processed_urls() -> set:
    if os.path.exists(PROCESSED_MOVIES_FILE):
        try:
            with open(PROCESSED_MOVIES_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    return set(data)
        except Exception:
            pass
    return set()

def save_processed_urls(urls_set: set):
    try:
        with open(PROCESSED_MOVIES_FILE, "w", encoding="utf-8") as f:
            json.dump(list(urls_set), f, ensure_ascii=False, indent=2)
    except Exception:
        pass

def get_already_published_identifiers() -> tuple:
    """
    Returns (set of published movie names normalized, set of published IDs, set of processed urls)
    so the scraper never re-crawls or re-solves a movie that already exists in Nuvio.
    """
    names = set()
    ids = set()
    urls = load_processed_urls()

    # 1. Check local catalog file
    local_cat = os.path.join(WORKSPACE_DIR, "data", "catalog.json")
    if os.path.exists(local_cat):
        try:
            with open(local_cat, "r", encoding="utf-8") as f:
                d = json.load(f)
                metas = d.get("metas", []) if isinstance(d, dict) else d
                for m in metas:
                    if isinstance(m, dict):
                        if m.get("id"): ids.add(str(m["id"]).lower())
                        if m.get("name"): names.add(str(m["name"]).strip().lower())
        except Exception:
            pass

    # 2. Check remote GitHub catalog
    try:
        cat_url = f"https://raw.githubusercontent.com/{REPO_OWNER}/{REPO_NAME}/{DEFAULT_BRANCH}/data/catalog.json?t={int(time.time())}"
        cat_json = http_json(cat_url, timeout=5)
        if isinstance(cat_json, dict) and "metas" in cat_json:
            for m in cat_json.get("metas", []):
                if isinstance(m, dict):
                    if m.get("id"): ids.add(str(m["id"]).lower())
                    if m.get("name"): names.add(str(m["name"]).strip().lower())
    except Exception:
        pass

    return names, ids, urls

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
      --bg: #07090e;
      --card-bg: rgba(15, 20, 32, 0.75);
      --card-border: rgba(255, 255, 255, 0.08);
      --card-hover-border: rgba(59, 130, 246, 0.4);
      --accent: #2563eb;
      --accent-hover: #1d4ed8;
      --accent-gradient: linear-gradient(135deg, #2563eb, #38bdf8);
      --emerald: #059669;
      --emerald-gradient: linear-gradient(135deg, #059669, #34d399);
      --amber: #d97706;
      --red: #dc2626;
      --text: #f8fafc;
      --dim: #94a3b8;
      --muted: #64748b;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      background-color: var(--bg);
      color: var(--text);
      font-family: -apple-system, BlinkMacSystemFont, "SF Pro Display", "SF Pro Text", "Segoe UI", Roboto, sans-serif;
      padding-bottom: 96px;
      line-height: 1.5;
      user-select: none;
      overflow-x: hidden;
      min-height: 100vh;
    }

    /* Fixed Glass Header */
    header {
      position: sticky;
      top: 0;
      z-index: 50;
      background: rgba(7, 9, 14, 0.85);
      backdrop-filter: blur(20px);
      -webkit-backdrop-filter: blur(20px);
      border-bottom: 1px solid var(--card-border);
      padding: 14px 28px;
      display: flex;
      align-items: center;
      justify-content: space-between;
    }
    .brand { display: flex; align-items: center; gap: 12px; }
    .brand h1 { font-size: 20px; font-weight: 800; color: #fff; letter-spacing: -0.5px; display: flex; align-items: center; gap: 8px; }
    .brand span.badge-os { font-size: 11px; font-weight: 700; color: #38bdf8; background: rgba(56, 189, 248, 0.12); border: 1px solid rgba(56, 189, 248, 0.25); padding: 3px 8px; border-radius: 6px; }
    
    .header-actions { display: flex; align-items: center; gap: 10px; }

    /* Main App Container */
    .app-container {
      max-width: 1200px;
      margin: 0 auto;
      padding: 24px 28px;
    }

    /* Tab Panes */
    .view-pane {
      display: none;
      animation: fadeIn 0.2s cubic-bezier(0.16, 1, 0.3, 1);
    }
    .view-pane.active { display: block; }
    @keyframes fadeIn {
      from { opacity: 0; transform: translateY(6px); }
      to { opacity: 1; transform: translateY(0); }
    }

    /* Cards */
    .card {
      background: var(--card-bg);
      backdrop-filter: blur(16px);
      -webkit-backdrop-filter: blur(16px);
      border: 1px solid var(--card-border);
      border-radius: 16px;
      padding: 20px 24px;
      margin-bottom: 20px;
      box-shadow: 0 10px 30px rgba(0,0,0,0.5);
      transition: border-color 0.2s;
    }
    .card:hover { border-color: var(--card-hover-border); }
    .card-title {
      font-size: 13px;
      font-weight: 800;
      color: #60a5fa;
      letter-spacing: 0.8px;
      text-transform: uppercase;
      margin-bottom: 14px;
      display: flex;
      align-items: center;
      justify-content: space-between;
    }

    /* KPI Grid */
    .kpi-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
      gap: 14px;
      margin-bottom: 22px;
    }
    .kpi-card {
      background: rgba(15, 23, 42, 0.6);
      border: 1px solid var(--card-border);
      border-radius: 14px;
      padding: 16px 20px;
      display: flex;
      flex-direction: column;
      gap: 6px;
    }
    .kpi-label { font-size: 11px; font-weight: 700; color: var(--dim); text-transform: uppercase; letter-spacing: 0.5px; }
    .kpi-value { font-size: 24px; font-weight: 800; color: #fff; letter-spacing: -0.5px; }
    .kpi-sub { font-size: 11px; color: var(--muted); }

    /* Inputs & Buttons */
    .row { display: flex; gap: 12px; align-items: center; }
    .col { display: flex; flex-direction: column; gap: 6px; }

    input[type="text"], input[type="password"], input[type="number"] {
      width: 100%;
      background: #0f172a;
      border: 1px solid rgba(255, 255, 255, 0.12);
      color: #ffffff;
      padding: 12px 16px;
      font-size: 14px;
      font-weight: 600;
      border-radius: 10px;
      outline: none;
      transition: all 0.2s;
    }
    input:focus {
      border-color: #3b82f6;
      box-shadow: 0 0 0 3px rgba(59, 130, 246, 0.25);
      background: #141f36;
    }

    button {
      padding: 11px 20px;
      font-size: 13px;
      font-weight: 700;
      border-radius: 10px;
      cursor: pointer;
      border: none;
      color: #fff;
      display: inline-flex;
      align-items: center;
      gap: 8px;
      white-space: nowrap;
      transition: all 0.15s ease-in-out;
    }
    button:active { transform: scale(0.97); }
    .btn-blue { background: var(--accent); }
    .btn-blue:hover { background: var(--accent-hover); }
    .btn-gradient { background: var(--accent-gradient); box-shadow: 0 4px 14px rgba(37, 99, 235, 0.35); }
    .btn-gradient:hover { filter: brightness(1.1); }
    .btn-green { background: var(--emerald); }
    .btn-green:hover { background: #047857; }
    .btn-red { background: var(--red); }
    .btn-red:hover { background: #b91c1c; }
    .btn-gray { background: #1e293b; border: 1px solid var(--card-border); color: #cbd5e1; }
    .btn-gray:hover { background: #334155; color: #fff; }

    /* Terminal Console */
    #terminal {
      background: #030712;
      border: 1px solid rgba(255, 255, 255, 0.08);
      border-radius: 12px;
      padding: 16px 20px;
      font-family: "SF Mono", Menlo, Monaco, Consolas, monospace;
      font-size: 12.5px;
      color: #34d399;
      height: 380px;
      overflow-y: auto;
      line-height: 1.6;
      white-space: pre-wrap;
    }

    /* Badges */
    .badge {
      font-size: 11px;
      font-weight: 800;
      padding: 4px 10px;
      border-radius: 6px;
      text-transform: uppercase;
      letter-spacing: 0.5px;
    }
    .badge-ready { background: rgba(59, 130, 246, 0.15); color: #60a5fa; border: 1px solid rgba(59, 130, 246, 0.3); }
    .badge-running { background: rgba(16, 185, 129, 0.15); color: #34d399; border: 1px solid rgba(16, 185, 129, 0.3); }
    .badge-paused { background: rgba(245, 158, 11, 0.15); color: #fbbf24; border: 1px solid rgba(245, 158, 11, 0.3); }
    .badge-stopped { background: rgba(239, 68, 68, 0.15); color: #f87171; border: 1px solid rgba(239, 68, 68, 0.3); }

    /* Item Cards */
    .media-item {
      display: flex;
      justify-content: space-between;
      align-items: center;
      background: rgba(15, 23, 42, 0.5);
      border: 1px solid var(--card-border);
      padding: 12px 18px;
      border-radius: 12px;
      margin-bottom: 8px;
      transition: all 0.15s;
    }
    .media-item:hover {
      background: rgba(30, 41, 59, 0.6);
      border-color: rgba(59, 130, 246, 0.3);
    }

    /* Fixed Bottom Dock Bar */
    .bottom-dock {
      position: fixed;
      bottom: 0;
      left: 0;
      right: 0;
      height: 72px;
      background: rgba(9, 13, 22, 0.9);
      backdrop-filter: blur(28px) saturate(180%);
      -webkit-backdrop-filter: blur(28px) saturate(180%);
      border-top: 1px solid rgba(255, 255, 255, 0.08);
      display: flex;
      align-items: center;
      justify-content: center;
      gap: 12px;
      z-index: 1000;
      padding: 0 20px;
    }
    .dock-btn {
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      gap: 4px;
      padding: 8px 24px;
      border-radius: 12px;
      background: transparent;
      color: #94a3b8;
      cursor: pointer;
      transition: all 0.2s cubic-bezier(0.16, 1, 0.3, 1);
      position: relative;
    }
    .dock-btn * { pointer-events: none; }
    .dock-btn svg { width: 22px; height: 22px; stroke: currentColor; fill: none; stroke-width: 2; transition: transform 0.2s; }
    .dock-btn span { font-size: 11px; font-weight: 700; letter-spacing: 0.3px; }
    .dock-btn:hover { color: #f8fafc; background: rgba(255, 255, 255, 0.05); }
    .dock-btn.active {
      color: #38bdf8;
      background: rgba(56, 189, 248, 0.1);
      box-shadow: inset 0 0 0 1px rgba(56, 189, 248, 0.25);
    }
    .dock-btn.active svg { transform: translateY(-1px); stroke: #38bdf8; }
    .dock-badge {
      position: absolute;
      top: 4px;
      right: 18px;
      background: #ef4444;
      color: #fff;
      font-size: 10px;
      font-weight: 800;
      padding: 1px 6px;
      border-radius: 10px;
      min-width: 16px;
      text-align: center;
    }

    /* Toast Notification */
    #toast {
      position: fixed;
      bottom: 88px;
      right: 28px;
      background: rgba(15, 23, 42, 0.95);
      border: 1px solid #3b82f6;
      color: #fff;
      padding: 12px 20px;
      border-radius: 10px;
      font-size: 13px;
      font-weight: 700;
      box-shadow: 0 10px 25px rgba(0,0,0,0.6);
      opacity: 0;
      transform: translateY(12px);
      transition: all 0.25s cubic-bezier(0.16, 1, 0.3, 1);
      pointer-events: none;
      z-index: 2000;
    }
    #toast.show { opacity: 1; transform: translateY(0); }
  </style>
</head>
<body>

  <!-- TOP HEADER -->
  <header>
    <div class="brand">
      <h1>🎬 PlayRepo Pro</h1>
      <span class="badge-os">macOS Edition</span>
      <span id="status-badge" class="badge badge-ready">HAZIR</span>
    </div>
    <div class="header-actions">
      <button class="btn-gray" onclick="clearLogs()">🧹 Logları Temizle</button>
      <button id="btn-pause" class="btn-gray" onclick="pauseCrawler()" style="display:none; background:#d97706; color:#fff;">⏸ Duraklat</button>
      <button id="btn-resume" class="btn-green" onclick="resumeCrawler()" style="display:none">▶ Devam Et</button>
      <button id="btn-stop" class="btn-red" onclick="stopCrawler()" style="display:none">⏹ Durdur</button>
      <button id="btn-run" class="btn-gradient" onclick="startMasterUpdate()">⚡ TÜM SİTELERİ TARA & NUVIO'YA YÜKLE</button>
    </div>
  </header>

  <div class="app-container">

    <!-- TAB 1: TARAMA & KONTROL (DASHBOARD) -->
    <div id="view-dashboard" class="view-pane active">
      <div class="kpi-grid">
        <div class="kpi-card">
          <span class="kpi-label">Nuvio Yayındaki İçerik</span>
          <span id="kpi-published" class="kpi-value">0</span>
          <span class="kpi-sub">Aktif film ve dizi kataloğu</span>
        </div>
        <div class="kpi-card">
          <span class="kpi-label">Tarama Hafızası (Önbellek)</span>
          <span id="kpi-memory" class="kpi-value">0</span>
          <span class="kpi-sub">Taranmış linkler (tekrar taranmaz)</span>
        </div>
        <div class="kpi-card">
          <span class="kpi-label">Kayıtlı Siteler</span>
          <span id="kpi-sites" class="kpi-value">0</span>
          <span class="kpi-sub">DiziBOX, DiziMom, FilmDozu vb.</span>
        </div>
        <div class="kpi-card">
          <span class="kpi-label">Motor Durumu</span>
          <span id="kpi-status-text" class="kpi-value" style="font-size:18px; color:#38bdf8;">BEKLEMEDE</span>
          <span class="kpi-sub">Sistem v2.8 Pro Hazır</span>
        </div>
      </div>

      <div class="card" style="padding: 16px 20px;">
        <div class="card-title">
          <span>💻 CANLI TERMİNAL / İŞLEM ÇIKTISI</span>
          <button class="btn-gray" style="padding: 4px 10px; font-size: 11px;" onclick="copyLogs()">📋 Logları Kopyala</button>
        </div>
        <div id="terminal"></div>
      </div>
    </div>

    <!-- TAB 2: NUVIO KATALOĞU (YAYINDAKİ FİLMLER & DİZİLER) -->
    <div id="view-catalog" class="view-pane">
      <div class="card">
        <div class="card-title">
          <span>🎬 NUVİO GÜNCEL YAYIN KATALOĞU (<span id="pub-count">0</span>)</span>
          <div class="row" style="gap: 8px;">
            <button class="btn-gray" style="padding: 6px 14px; font-size: 12px;" onclick="loadPublishedMovies()">🔄 Yenile</button>
            <button class="btn-red" style="padding: 6px 14px; font-size: 12px;" onclick="clearAllPublishedMovies()">💥 Güncel Listeyi Toplu Sil</button>
          </div>
        </div>
        <div style="font-size: 13px; color: var(--dim); margin-bottom: 14px;">
          Nuvio eklentinizde şu anda izlenebilen güncel film ve diziler. Buradan istediğinizi silebilir veya tüm listeyi sıfırlayabilirsiniz:
        </div>
        <div style="margin-bottom: 14px;">
          <input type="text" id="movie-search" placeholder="🔍 Güncel yayındaki filmlerde veya dizilerde ara..." oninput="filterPublishedMovies()" />
        </div>
        <div id="movies-container" style="max-height: 520px; overflow-y: auto; display: flex; flex-direction: column; gap: 8px;">
          <div style="color: var(--dim); font-size: 13px; padding: 10px;">Filmler yükleniyor...</div>
        </div>
      </div>
    </div>

    <!-- TAB 3: ÖZEL KURTARMA (TEK FİLM / DİZİ EKLE) -->
    <div id="view-single" class="view-pane">
      <div class="card" style="border: 1px solid #3b82f6; background: rgba(12, 19, 34, 0.85); padding: 24px;">
        <div class="card-title" style="color: #60a5fa; font-size: 14px;">🎯 TEK BİR FİLM VEYA DİZİYİ BUL VE YENİDEN EKLE</div>
        <div style="font-size: 13px; color: var(--dim); margin-bottom: 16px; line-height: 1.6;">
          İçeriği bozulan, akışları çalışmayan veya eksik olan bir film veya dizinin adını ya da doğrudan site sayfasının linkini girin.<br>
          PlayRepo kayıtlı tüm siteleri tek tek kontrol ederek o içeriğin taze çalışan 1080p video kaynaklarını çözer ve anında Nuvio'ya aktarır.
        </div>
        <div class="row" style="margin-bottom: 16px;">
          <input type="text" id="target-movie-input" placeholder="Örn: Inception, Matrix, Testere veya https://diziboxizle.com/nails/ linki" style="font-size: 15px; padding: 14px;" />
          <button class="btn-gradient" style="padding: 14px 24px; font-size: 14px; font-weight: 800;" onclick="findAndAddSpecificMovie()">🎯 BUL VE YENİDEN EKLE</button>
        </div>
        <div style="background: rgba(15, 23, 42, 0.6); border: 1px solid rgba(255, 255, 255, 0.08); border-radius: 12px; padding: 16px;">
          <div style="font-size: 12px; font-weight: 700; color: #38bdf8; text-transform: uppercase; margin-bottom: 8px;">Nasıl Çalışır?</div>
          <div style="font-size: 12px; color: var(--dim); line-height: 1.6;">
            1. Hedef film adı girildiğinde tüm kayıtlı sitelerin sitemap ve arama motorları taranır.<br>
            2. TMDB veya Cinemeta üzerinden film/dizi afişi, türü ve Türkçe açıklaması bulunur.<br>
            3. OK.ru, Vidmoly veya HLS video kaynakları ayıklanıp GitHub'a push edilir ve Nuvio'da hemen görünür.
          </div>
        </div>
      </div>
    </div>

    <!-- TAB 4: TARAMA HAFIZASI (ÖNBELLEK VE ARŞİV) -->
    <div id="view-memory" class="view-pane">
      <div class="card">
        <div class="card-title">
          <span>💾 TARAMA HAFIZASI & ÖNBELLEK YÖNETİMİ</span>
          <div class="row" style="gap: 8px;">
            <button class="btn-gray" style="padding: 6px 14px; font-size: 12px;" onclick="loadProcessedUrls(); loadHistoryMovies();">🔄 Yenile</button>
            <button class="btn-red" style="padding: 6px 14px; font-size: 12px;" onclick="clearAllProcessedUrls()">💥 Tüm Hafızayı Temizle</button>
          </div>
        </div>
        <div style="font-size: 13px; color: var(--dim); margin-bottom: 16px;">
          PlayRepo daha önce tarayıp çözdüğü film ve dizi linklerini hafızaya kaydeder. Böylece bilgisayarı kapatıp ertesi gün açtığınızda daha önce taranmış olan filmler atlanır ve sadece <b>yeni eklenen filmler</b> taranır. Buradan dilediğiniz bir filmi hafızadan çıkarıp yeniden taranmasını sağlayabilirsiniz.
        </div>
        
        <!-- Sekmeler -->
        <div style="display: flex; gap: 8px; margin-bottom: 14px;">
          <button id="mem-tab-urls" class="btn-blue" style="padding: 7px 16px; font-size: 12px;" onclick="switchMemTab('urls')">🔗 Taranmış Linkler Hafızası (<span id="mem-url-count">0</span>)</button>
          <button id="mem-tab-his" class="btn-gray" style="padding: 7px 16px; font-size: 12px;" onclick="switchMemTab('his')">📜 Eski Çözülenler Arşivi (<span id="his-count">0</span>)</button>
        </div>

        <!-- Alt Bölüm 1: Linkler Hafızası -->
        <div id="mem-pane-urls">
          <input type="text" id="memory-search" placeholder="🔍 Taranmış linklerde ara..." oninput="filterProcessedUrls()" style="margin-bottom: 12px;" />
          <div id="memory-container" style="max-height: 480px; overflow-y: auto; display: flex; flex-direction: column; gap: 6px;">
            <div style="color: var(--dim); font-size: 13px; padding: 10px;">Hafıza yükleniyor...</div>
          </div>
        </div>

        <!-- Alt Bölüm 2: Eski Arşiv Metadata -->
        <div id="mem-pane-his" style="display:none;">
          <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom: 12px; gap: 8px;">
            <input type="text" id="history-search" placeholder="🔍 Eski arşivde ara..." oninput="filterHistoryMovies()" style="flex:1;" />
            <button class="btn-green" style="padding: 9px 16px; font-size: 12px; white-space:nowrap;" onclick="publishAllHistoryMovies()">⚡ Geçmişi Toplu Nuvio'ya Gönder</button>
          </div>
          <div id="history-container" style="max-height: 480px; overflow-y: auto; display: flex; flex-direction: column; gap: 6px;">
            <div style="color: var(--dim); font-size: 13px; padding: 10px;">Arşiv yükleniyor...</div>
          </div>
        </div>
      </div>
    </div>

    <!-- TAB 5: SİTELER & AYARLAR -->
    <div id="view-settings" class="view-pane">
      
      <!-- Kayıtlı Siteler -->
      <div class="card">
        <div class="card-title">
          <span>🌐 TARANACAK KAYITLI SİTELER (<span id="site-count">0</span>)</span>
          <button class="btn-gray" style="padding: 5px 12px; font-size: 11px;" onclick="resetSites()">🔄 En İyi Siteleri Sıfırla / Yükle</button>
        </div>
        <div class="row" style="margin-bottom: 14px;">
          <input type="text" id="new-site-input" placeholder="Örn: https://diziboxizle.com/ veya https://ddizimom.net/" />
          <button class="btn-green" onclick="addSite()">➕ SİTE EKLE</button>
        </div>
        <div id="sites-list" style="max-height: 240px; overflow-y: auto; margin-bottom: 8px;"></div>
      </div>

      <!-- Ayarlar -->
      <div class="card">
        <div class="card-title">⚙️ MOTOR & GİTHUB AYARLARI</div>
        <div class="col" style="margin-bottom: 12px;">
          <label style="font-size:12px; font-weight:700; color:var(--dim)">SİTE BAŞINA SAYFA LİMİTİ (1 - 100):</label>
          <input type="number" id="max-pages-input" value="20" />
        </div>
        <div class="col" style="margin-bottom: 14px;">
          <label style="font-size:12px; font-weight:700; color:var(--dim)">GİTHUB KİŞİSEL ERİŞİM TOKENİ (PAT):</label>
          <input type="password" id="token-input" placeholder="ghp_xxxxxxxxxxxxxxxxxxxx" />
        </div>
        <button class="btn-gradient" style="width: 100%; justify-content: center;" onclick="saveSettings()">💾 Ayarları Kaydet</button>
      </div>

      <!-- Nuvio Eklenti Linki -->
      <div class="card" style="border: 1px solid rgba(56, 189, 248, 0.3); background: rgba(12, 19, 34, 0.7);">
        <div class="card-title" style="color: #38bdf8;">📲 NUVİO EKLENTİ BAĞLANTISI (MANIFEST)</div>
        <div style="font-size: 13px; color: var(--dim); margin-bottom: 10px;">
          Nuvio uygulamanıza (Apple TV, Mac, iOS veya Android) ekleyeceğiniz resmi eklenti linkiniz:
        </div>
        <div class="row">
          <input type="text" id="manifest-url-input" readonly value="https://raw.githubusercontent.com/csprofesor/apple/main/playrepo/manifest.json" style="background:#090d16; color:#38bdf8;" />
          <button class="btn-blue" onclick="copyManifestUrl()">📋 Linki Kopyala</button>
        </div>
      </div>

    </div>

  </div>

  <!-- FIXED BOTTOM DOCK NAVIGATION -->
  <nav class="bottom-dock">
    <button id="dock-btn-dashboard" class="dock-btn active" onclick="switchNav('dashboard')">
      <svg viewBox="0 0 24 24"><path d="M12 2L2 7l10 5 10-5-10-5zM2 17l10 5 10-5M2 12l10 5 10-5"/></svg>
      <span>Tarama</span>
    </button>
    <button id="dock-btn-catalog" class="dock-btn" onclick="switchNav('catalog')">
      <svg viewBox="0 0 24 24"><rect x="2" y="2" width="20" height="20" rx="2.18" ry="2.18"/><line x1="7" y1="2" x2="7" y2="22"/><line x1="17" y1="2" x2="17" y2="22"/><line x1="2" y1="12" x2="22" y2="12"/><line x1="2" y1="7" x2="7" y2="7"/><line x1="2" y1="17" x2="7" y2="17"/><line x1="17" y1="17" x2="22" y2="17"/><line x1="17" y1="7" x2="22" y2="7"/></svg>
      <span>Nuvio</span>
      <span id="dock-badge-catalog" class="dock-badge">0</span>
    </button>
    <button id="dock-btn-single" class="dock-btn" onclick="switchNav('single')">
      <svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"/><line x1="22" y1="12" x2="18" y2="12"/><line x1="6" y1="12" x2="2" y2="12"/><line x1="12" y1="6" x2="12" y2="2"/><line x1="12" y1="22" x2="12" y2="18"/></svg>
      <span>Özel Kurtar</span>
    </button>
    <button id="dock-btn-memory" class="dock-btn" onclick="switchNav('memory')">
      <svg viewBox="0 0 24 24"><ellipse cx="12" cy="5" rx="9" ry="3"/><path d="M21 12c0 1.66-4 3-9 3s-9-1.34-9-3"/><path d="M3 5v14c0 1.66 4 3 9 3s9-1.34 9-3V5"/></svg>
      <span>Hafıza</span>
      <span id="dock-badge-memory" class="dock-badge" style="background:#3b82f6;">0</span>
    </button>
    <button id="dock-btn-settings" class="dock-btn" onclick="switchNav('settings')">
      <svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg>
      <span>Ayarlar</span>
    </button>
  </nav>

  <!-- TOAST NOTIFICATION -->
  <div id="toast"></div>

  <script>
    let currentConfig = { saved_sites: [], max_pages: 20, github_token: "", tmdb_key: "" };
    let allPublishedMovies = [];
    let allHistoryMovies = [];
    let allProcessedUrls = [];

    function showToast(msg) {
      const t = document.getElementById('toast');
      t.innerText = msg;
      t.className = 'show';
      setTimeout(() => { t.className = ''; }, 2600);
    }

    function switchNav(view) {
      document.querySelectorAll('.view-pane').forEach(el => el.classList.remove('active'));
      document.querySelectorAll('.dock-btn').forEach(el => el.classList.remove('active'));

      const targetPane = document.getElementById('view-' + view);
      const targetBtn = document.getElementById('dock-btn-' + view);
      if (targetPane) targetPane.classList.add('active');
      if (targetBtn) targetBtn.classList.add('active');

      if (view === 'catalog') loadPublishedMovies();
      if (view === 'memory') { loadProcessedUrls(); loadHistoryMovies(); }
      if (view === 'settings') loadConfig();
    }

    function switchMemTab(tab) {
      if (tab === 'urls') {
        document.getElementById('mem-pane-urls').style.display = 'block';
        document.getElementById('mem-pane-his').style.display = 'none';
        document.getElementById('mem-tab-urls').className = 'btn-blue';
        document.getElementById('mem-tab-his').className = 'btn-gray';
      } else {
        document.getElementById('mem-pane-urls').style.display = 'none';
        document.getElementById('mem-pane-his').style.display = 'block';
        document.getElementById('mem-tab-urls').className = 'btn-gray';
        document.getElementById('mem-tab-his').className = 'btn-blue';
      }
    }

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
      document.getElementById('kpi-sites').innerText = sites.length;
      if (sites.length === 0) {
        list.innerHTML = "<div style='color:#64748b; font-size:13px; padding:10px;'>Kayıtlı site bulunmuyor.</div>";
        return;
      }
      sites.forEach((site, idx) => {
        const item = document.createElement('div');
        item.className = 'media-item';
        item.innerHTML = `
          <span style="font-weight:600; font-size:13.5px; color:#f1f5f9; word-break:break-all;">🌐 ${site}</span>
          <button class="btn-red" style="padding:5px 12px; font-size:11px;" onclick="deleteSite(${idx})">🗑️ Sil</button>
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
      showToast('Yeni site başarıyla eklendi!');
      await loadConfig();
    }

    async function deleteSite(idx) {
      await fetch('/api/delete-site', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ index: idx })
      });
      showToast('Site listeden kaldırıldı!');
      await loadConfig();
    }

    async function resetSites() {
      if (confirm('Site listesi en iyi aktif dizi & film sitelerine sıfırlansın mı?')) {
        await fetch('/api/reset-sites', { method: 'POST' });
        showToast('Site listesi varsayılana sıfırlandı!');
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
      showToast('Ayarlar başarıyla kaydedildi!');
      await loadConfig();
    }

    function copyManifestUrl() {
      const input = document.getElementById('manifest-url-input');
      input.select();
      navigator.clipboard.writeText(input.value);
      showToast('Nuvio eklenti linki panoya kopyalandı!');
    }

    function copyLogs() {
      const t = document.getElementById('terminal').innerText;
      navigator.clipboard.writeText(t);
      showToast('Loglar panoya kopyalandı!');
    }

    async function findAndAddSpecificMovie() {
      const inp = document.getElementById('target-movie-input');
      const query = inp.value.trim();
      if (!query) {
        alert('Lütfen aramak istediğiniz film/dizi adını ya da doğrudan linkini girin!');
        return;
      }
      switchNav('dashboard');
      document.getElementById('btn-run').style.display = 'none';
      document.getElementById('btn-stop').style.display = 'inline-flex';

      const res = await fetch('/api/search-and-add-movie', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ query })
      });
      const data = await res.json();
      if (data.status === 'started') {
        inp.value = '';
        showToast('Hedef içerik araması başlatıldı!');
      } else {
        alert('İşlem başlatılamadı: ' + (data.message || ''));
      }
    }

    async function startMasterUpdate() {
      document.getElementById('btn-run').style.display = 'none';
      document.getElementById('btn-pause').style.display = 'inline-flex';
      document.getElementById('btn-resume').style.display = 'none';
      document.getElementById('btn-stop').style.display = 'inline-flex';
      showToast('Tüm siteler taranmaya başlandı...');
      await fetch('/api/start-all', { method: 'POST' });
    }

    async function pauseCrawler() {
      await fetch('/api/pause', { method: 'POST' });
      document.getElementById('btn-pause').style.display = 'none';
      document.getElementById('btn-resume').style.display = 'inline-flex';
      showToast('Tarama duraklatıldı.');
    }

    async function resumeCrawler() {
      await fetch('/api/resume', { method: 'POST' });
      document.getElementById('btn-resume').style.display = 'none';
      document.getElementById('btn-pause').style.display = 'inline-flex';
      showToast('Tarama devam ediyor...');
    }

    async function stopCrawler() {
      await fetch('/api/stop', { method: 'POST' });
      document.getElementById('btn-run').style.display = 'inline-flex';
      document.getElementById('btn-pause').style.display = 'none';
      document.getElementById('btn-resume').style.display = 'none';
      document.getElementById('btn-stop').style.display = 'none';
      showToast('Tarama durduruldu.');
    }

    async function clearLogs() {
      await fetch('/api/clear-logs', { method: 'POST' });
      document.getElementById('terminal').innerText = "";
      showToast('Loglar temizlendi.');
    }

    async function pollLogs() {
      try {
        const res = await fetch('/api/logs');
        const data = await res.json();
        const term = document.getElementById('terminal');
        term.innerText = data.logs.join(String.fromCharCode(10));
        term.scrollTop = term.scrollHeight;

        const badge = document.getElementById('status-badge');
        const kpiText = document.getElementById('kpi-status-text');

        if (!data.is_running) {
          document.getElementById('btn-run').style.display = 'inline-flex';
          document.getElementById('btn-pause').style.display = 'none';
          document.getElementById('btn-resume').style.display = 'none';
          document.getElementById('btn-stop').style.display = 'none';
          badge.innerText = 'HAZIR';
          badge.className = 'badge badge-ready';
          kpiText.innerText = 'BEKLEMEDE';
          kpiText.style.color = '#60a5fa';
        } else if (data.is_paused) {
          document.getElementById('btn-run').style.display = 'none';
          document.getElementById('btn-pause').style.display = 'none';
          document.getElementById('btn-resume').style.display = 'inline-flex';
          document.getElementById('btn-stop').style.display = 'inline-flex';
          badge.innerText = 'DURAKLATILDI';
          badge.className = 'badge badge-paused';
          kpiText.innerText = 'DURAKLATILDI';
          kpiText.style.color = '#fbbf24';
        } else {
          document.getElementById('btn-run').style.display = 'none';
          document.getElementById('btn-pause').style.display = 'inline-flex';
          document.getElementById('btn-resume').style.display = 'none';
          document.getElementById('btn-stop').style.display = 'inline-flex';
          badge.innerText = 'ÇALIŞIYOR';
          badge.className = 'badge badge-running';
          kpiText.innerText = 'ÇÖZÜLÜYOR...';
          kpiText.style.color = '#34d399';
        }
      } catch (e) {}
      setTimeout(pollLogs, 1000);
    }

    async function loadPublishedMovies() {
      try {
        const res = await fetch('/api/published-movies');
        const data = await res.json();
        allPublishedMovies = data.movies || [];
        const count = allPublishedMovies.length;
        document.getElementById('pub-count').innerText = count;
        document.getElementById('kpi-published').innerText = count;
        document.getElementById('dock-badge-catalog').innerText = count;
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
        item.className = 'media-item';
        const genres = (m.genres || []).slice(0, 3).join(', ');
        const isSeries = m.type === 'series';
        item.innerHTML = `
          <div style="display:flex; align-items:center; gap:14px; overflow:hidden;">
            <img src="${m.poster || ''}" onerror="this.src='${currentConfig.logo || 'https://cdn.jsdelivr.net/gh/csprofesor/apple@main/playrepo/logo.png'}'" style="width:42px; height:58px; object-fit:cover; border-radius:6px; background:#1e293b;" />
            <div style="display:flex; flex-direction:column; overflow:hidden;">
              <span style="font-weight:700; font-size:14.5px; color:#fff; text-overflow:ellipsis; white-space:nowrap; overflow:hidden;">
                ${m.name} <span style="font-size:12px; color:var(--dim); font-weight:500;">(${m.releaseInfo || m.year || ''})</span>
              </span>
              <span style="font-size:11.5px; color:#60a5fa; margin-top:2px;">
                <span style="background:${isSeries ? 'rgba(168, 85, 247, 0.2)' : 'rgba(59, 130, 246, 0.2)'}; color:${isSeries ? '#c084fc' : '#60a5fa'}; padding:2px 6px; border-radius:4px; font-weight:700;">${isSeries ? 'Dizi' : 'Film'}</span> • ${genres || 'Genel'}
              </span>
            </div>
          </div>
          <button class="btn-red" style="padding:6px 14px; font-size:12px;" onclick="deleteMovie('${m.id}', '${m.name.replace(/'/g, "\'")}')">🗑️ Sil</button>
        `;
        cont.appendChild(item);
      });
    }

    function filterPublishedMovies() {
      const q = document.getElementById('movie-search').value.toLowerCase().trim();
      if (!q) { renderPublishedMovies(allPublishedMovies); return; }
      const filtered = allPublishedMovies.filter(m => (m.name || '').toLowerCase().includes(q) || (m.genres || []).some(g => g.toLowerCase().includes(q)));
      renderPublishedMovies(filtered);
    }

    async function deleteMovie(id, name) {
      if (!confirm(`"${name}" içeriğini Nuvio eklentisinden silmek istediğinize emin misiniz?`)) return;
      showToast(`"${name}" siliniyor...`);
      const res = await fetch('/api/delete-movie', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ id })
      });
      const data = await res.json();
      if (data.status === 'ok') {
        showToast(`"${name}" başarıyla silindi!`);
        await loadPublishedMovies();
      } else {
        alert('Silme hatası: ' + (data.message || ''));
      }
    }

    async function clearAllPublishedMovies() {
      const count = allPublishedMovies.length;
      if (!confirm("DIKKAT: Nuvio yayinindaki " + count + " adet filmin ve dizinin TAMAMI SILINECEK! Onayliyor musunuz?")) return;
      showToast('Tüm yayınlar sıfırlanıyor...');
      const res = await fetch('/api/clear-all-movies', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'}
      });
      const data = await res.json();
      if (data.status === 'ok') {
        showToast('Tüm filmler başarıyla sıfırlandı!');
        await loadPublishedMovies();
      } else {
        alert('Hata: ' + (data.message || ''));
      }
    }

    async function loadProcessedUrls() {
      try {
        const res = await fetch('/api/processed-urls');
        const data = await res.json();
        allProcessedUrls = data.urls || [];
        const count = allProcessedUrls.length;
        document.getElementById('mem-url-count').innerText = count;
        document.getElementById('kpi-memory').innerText = count;
        document.getElementById('dock-badge-memory').innerText = count;
        renderProcessedUrls(allProcessedUrls);
      } catch (e) {
        console.error(e);
      }
    }

    function renderProcessedUrls(urls) {
      const cont = document.getElementById('memory-container');
      cont.innerHTML = "";
      if (urls.length === 0) {
        cont.innerHTML = "<div style='color:#64748b; font-size:13px; padding:10px;'>Önbellekte kayıtlı taranmış link bulunmuyor.</div>";
        return;
      }
      urls.forEach(u => {
        const item = document.createElement('div');
        item.className = 'media-item';
        item.innerHTML = `
          <span style="font-size:12.5px; color:#cbd5e1; word-break:break-all; font-family:monospace;">🔗 ${u}</span>
          <button class="btn-gray" style="padding:4px 10px; font-size:11px;" onclick="deleteProcessedUrl('${u.replace(/'/g, "\'")}')">🗑️ Hafızadan Çıkar</button>
        `;
        cont.appendChild(item);
      });
    }

    function filterProcessedUrls() {
      const q = document.getElementById('memory-search').value.toLowerCase().trim();
      if (!q) { renderProcessedUrls(allProcessedUrls); return; }
      renderProcessedUrls(allProcessedUrls.filter(u => u.toLowerCase().includes(q)));
    }

    async function deleteProcessedUrl(url) {
      await fetch('/api/delete-processed-url', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ url })
      });
      showToast('Link hafızadan çıkarıldı! Tekrar taranabilir.');
      await loadProcessedUrls();
    }

    async function clearAllProcessedUrls() {
      if (!confirm('Taranmis tum linkler onbellegi sifirlanacak! Sitelerdeki filmler sifirdan yeniden taranabilecek. Onayliyor musunuz?')) return;
      await fetch('/api/clear-processed-urls', { method: 'POST' });
      showToast('Tarama hafızası başarıyla temizlendi!');
      await loadProcessedUrls();
    }

    async function loadHistoryMovies() {
      try {
        const res = await fetch('/api/history-movies');
        const data = await res.json();
        allHistoryMovies = data.movies || [];
        document.getElementById('his-count').innerText = allHistoryMovies.length;
        renderHistoryMovies(allHistoryMovies);
      } catch (e) {
        console.error(e);
      }
    }

    function renderHistoryMovies(movies) {
      const cont = document.getElementById('history-container');
      cont.innerHTML = "";
      if (movies.length === 0) {
        cont.innerHTML = "<div style='color:#64748b; font-size:13px; padding:10px;'>Eski arşivde kayıtlı film bulunmuyor.</div>";
        return;
      }
      movies.forEach(m => {
        const item = document.createElement('div');
        item.className = 'media-item';
        const hasStreams = m.streams && m.streams.length > 0;
        const streamBadge = hasStreams 
          ? `<span style="font-size:11px; color:#10b981; background:rgba(16,185,129,0.12); padding:2px 6px; border-radius:4px;">${m.streams.length} Akış Hazır</span>`
          : `<span style="font-size:11px; color:#94a3b8; background:rgba(148,163,184,0.1); padding:2px 6px; border-radius:4px;">Akış Yok</span>`;

        item.innerHTML = `
          <div style="display:flex; align-items:center; gap:10px; overflow:hidden;">
            <span style="font-weight:700; font-size:13.5px; color:#fff; text-overflow:ellipsis; white-space:nowrap; overflow:hidden;">${m.name}</span>
            <span style="font-size:11px; color:#a855f7;">ID: ${m.id}</span>
            ${streamBadge}
          </div>
          <div style="display:flex; gap:6px;">
            <button class="btn-green" style="padding:4px 10px; font-size:11px;" onclick="publishSingleHistoryMovie('${m.id}', '${m.name.replace(/'/g, "\\'")}')">🚀 Nuvio'ya Gönder</button>
            <button class="btn-red" style="padding:4px 10px; font-size:11px;" onclick="deleteHistoryMovie('${m.id}', '${m.name.replace(/'/g, "\\'")}')">🗑️ Sil</button>
          </div>
        `;
        cont.appendChild(item);
      });
    }

    async function publishSingleHistoryMovie(id, name) {
      showToast(`"${name}" Nuvio'ya aktarılıyor...`);
      try {
        const res = await fetch('/api/publish-history-movie', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({ id })
        });
        const data = await res.json();
        if (data.status === 'ok') {
          showToast(`✓ "${name}" Nuvio & GitHub'a başarıyla aktarıldı!`);
          await loadPublishedMovies();
        } else {
          showToast(`Hata: ${data.message || 'Yüklenemedi'}`);
        }
      } catch (e) {
        showToast('Bağlantı hatası!');
      }
    }

    async function publishAllHistoryMovies() {
      if (!confirm('Eski arşivdeki çözülmüş tüm filmler toplu olarak Nuvio ve GitHub\\\'a aktarılacak. Onaylıyor musunuz?')) return;
      showToast('⚡ Toplu Nuvio aktarımı başlatıldı...');
      try {
        const res = await fetch('/api/publish-all-history', { method: 'POST' });
        const data = await res.json();
        if (data.status === 'ok') {
          showToast(`✓ ${data.count || ''} film başarıyla Nuvio\\\'ya aktarıldı!`);
          await loadPublishedMovies();
        } else {
          showToast(`Hata: ${data.message || 'Yüklenemedi'}`);
        }
      } catch (e) {
        showToast('Toplu yükleme sırasında hata oluştu!');
      }
    }

    function filterHistoryMovies() {
      const q = document.getElementById('history-search').value.toLowerCase().trim();
      if (!q) { renderHistoryMovies(allHistoryMovies); return; }
      renderHistoryMovies(allHistoryMovies.filter(m => (m.name || '').toLowerCase().includes(q) || (m.id || '').toLowerCase().includes(q)));
    }

    async function deleteHistoryMovie(id, name) {
      if (!confirm(`"${name}" kaydını eski arşivden silmek istiyor musunuz?`)) return;
      await fetch('/api/delete-history-movie', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ id })
      });
      showToast('Kayıt arşivden silindi!');
      await loadHistoryMovies();
    }

    // Keyboard shortcuts (1, 2, 3, 4, 5)
    window.addEventListener('keydown', (e) => {
      if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;
      if (e.key === '1') switchNav('dashboard');
      if (e.key === '2') switchNav('catalog');
      if (e.key === '3') switchNav('single');
      if (e.key === '4') switchNav('memory');
      if (e.key === '5') switchNav('settings');
    });

    window.onload = () => {
      loadConfig();
      loadPublishedMovies();
      loadProcessedUrls();
      loadHistoryMovies();
      pollLogs();
    };
  </script>
</body>
</html>"""

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
            is_active = CURRENT_WORKER is not None and CURRENT_WORKER.is_alive()
            self._send_json({
                "logs": LOG_BUFFER,
                "is_running": ENGINE_INSTANCE.is_running and is_active,
                "is_paused": ENGINE_INSTANCE.is_paused and is_active
            })
        elif self.path == "/api/published-movies":
            cat_url = f"https://raw.githubusercontent.com/{REPO_OWNER}/{REPO_NAME}/{DEFAULT_BRANCH}/data/catalog.json?t={int(time.time())}"
            cat_json = http_json(cat_url)
            metas = cat_json.get("metas", []) if isinstance(cat_json, dict) else []
            self._send_json({"movies": metas})
        elif self.path == "/api/history-movies":
            his_path = os.path.join(WORKSPACE_DIR, "data", "history_catalog.json")
            if os.path.exists(his_path):
                try:
                    with open(his_path, "r", encoding="utf-8") as f:
                        d = json.load(f)
                        self._send_json({"movies": d.get("metas", [])})
                        return
                except Exception:
                    pass
            self._send_json({"movies": []})
        elif self.path == "/api/processed-urls":
            urls = sorted(list(load_processed_urls()))
            self._send_json({"urls": urls, "count": len(urls)})
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
        elif self.path == "/api/clear-all-movies":
            cfg = load_config()
            token = cfg.get("github_token", "").strip()
            if token:
                pub = GitHubAtomicPublisher(token=token, log_callback=append_global_log)
                success = pub.clear_all_published_movies()
                self._send_json({"status": "ok" if success else "error"})
            else:
                self._send_json({"status": "error", "message": "GitHub Token eksik"})
        elif self.path == "/api/publish-history-movie":
            body = self._read_json()
            movie_id = body.get("id", "").strip()
            cfg = load_config()
            token = cfg.get("github_token", "").strip()
            if not token:
                self._send_json({"status": "error", "message": "GitHub Token eksik"})
                return

            his_path = os.path.join(WORKSPACE_DIR, "data", "history_catalog.json")
            if os.path.exists(his_path) and movie_id:
                try:
                    with open(his_path, "r", encoding="utf-8") as f:
                        d = json.load(f)
                    target = next((m for m in d.get("metas", []) if m.get("id") == movie_id), None)
                    if not target:
                        self._send_json({"status": "error", "message": "Film arşivde bulunamadı"})
                        return

                    # Format for publish_movies
                    streams = target.get("streams", [])
                    if not streams:
                        # Fallback dummy Play stream if streams missing
                        streams = [{
                            "name": "Play",
                            "title": "Play • 1080p • [TR Dublaj]",
                            "url": "https://commondatastorage.googleapis.com/gtv-videos-bucket/sample/BigBuckBunny.mp4",
                            "quality": 1080,
                            "format": "mp4"
                        }]

                    m_item = {
                        "tmdbId": target.get("id"),
                        "title": target.get("name") or target.get("title"),
                        "poster": target.get("poster") or target.get("background"),
                        "description": target.get("description", "Play • Film"),
                        "year": target.get("releaseInfo") or target.get("year", ""),
                        "genres": target.get("genres", ["Popüler"]),
                        "streams": streams,
                        "type": target.get("type", "movie")
                    }
                    pub = GitHubAtomicPublisher(token=token, log_callback=append_global_log)
                    append_global_log(f"🚀 [ARŞİVDEN NUVIO'YA AKTARILIYOR] {m_item['title']}...")
                    success = pub.publish_movies([m_item])
                    self._send_json({"status": "ok" if success else "error"})
                    return
                except Exception as e:
                    self._send_json({"status": "error", "message": str(e)})
                    return
            self._send_json({"status": "error", "message": "Geçersiz istek"})
        elif self.path == "/api/publish-all-history":
            cfg = load_config()
            token = cfg.get("github_token", "").strip()
            if not token:
                self._send_json({"status": "error", "message": "GitHub Token eksik"})
                return

            his_path = os.path.join(WORKSPACE_DIR, "data", "history_catalog.json")
            if os.path.exists(his_path):
                try:
                    with open(his_path, "r", encoding="utf-8") as f:
                        d = json.load(f)
                    raw_metas = d.get("metas", [])
                    valid_movies = []
                    for m in raw_metas:
                        s_list = m.get("streams", [])
                        if s_list:
                            valid_movies.append({
                                "tmdbId": m.get("id"),
                                "title": m.get("name") or m.get("title"),
                                "poster": m.get("poster") or m.get("background"),
                                "description": m.get("description", "Play • Film"),
                                "year": m.get("releaseInfo") or m.get("year", ""),
                                "genres": m.get("genres", ["Popüler"]),
                                "streams": s_list,
                                "type": m.get("type", "movie")
                            })

                    if not valid_movies:
                        self._send_json({"status": "error", "message": "Arşivde aktarılabilir akışlı film bulunamadı"})
                        return

                    pub = GitHubAtomicPublisher(token=token, log_callback=append_global_log)
                    append_global_log(f"⚡ [TOPLU ARŞİV AKTARIMI] Toplam {len(valid_movies)} film Nuvio & GitHub'a aktarılıyor...")
                    success = pub.publish_movies(valid_movies)
                    self._send_json({"status": "ok" if success else "error", "count": len(valid_movies)})
                    return
                except Exception as e:
                    self._send_json({"status": "error", "message": str(e)})
                    return
            self._send_json({"status": "error", "message": "Arşiv dosyası bulunamadı"})
        elif self.path == "/api/delete-history-movie":
            body = self._read_json()
            movie_id = body.get("id", "").strip()
            his_path = os.path.join(WORKSPACE_DIR, "data", "history_catalog.json")
            if os.path.exists(his_path) and movie_id:
                try:
                    with open(his_path, "r", encoding="utf-8") as f:
                        d = json.load(f)
                    metas = [m for m in d.get("metas", []) if m.get("id") != movie_id]
                    with open(his_path, "w", encoding="utf-8") as f:
                        json.dump({"metas": metas}, f, ensure_ascii=False, indent=2)
                    self._send_json({"status": "ok"})
                    return
                except Exception as e:
                    self._send_json({"status": "error", "message": str(e)})
                    return
            self._send_json({"status": "error", "message": "Bulunamadı"})
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
        elif self.path == "/api/delete-processed-url":
            body = self._read_json()
            u = body.get("url", "").strip()
            urls = load_processed_urls()
            if u in urls:
                urls.remove(u)
                save_processed_urls(urls)
                append_global_log(f"[HAFIZADAN ÇIKARILDI] {u}")
            self._send_json({"status": "ok"})
        elif self.path == "/api/clear-processed-urls":
            save_processed_urls(set())
            append_global_log("[HAFIZA TEMİZLENDİ] Taranmış linkler önbelleği sıfırlandı.")
            self._send_json({"status": "ok"})
        elif self.path == "/api/reset-sites":
            cfg = load_config()
            cfg["saved_sites"] = [
                "https://diziboxizle.com/",
                "https://ddizimom.net/",
                "https://dizirex.com/",
                "https://fullhdfilmizlebox.vip/",
                "https://filmdozu.com/",
                "https://ufilmizle.com/",
                "https://fullfilmizle.fit/",
                "https://www.hintfilmizle.com/film-izle/",
                "https://filmarsivi.org/"
            ]
            save_config(cfg)
            append_global_log("[SIFIRLANDI] Site listesi en iyi aktif dizi & film sitelerine sıfırlandı.")
            self._send_json({"status": "ok"})
        elif self.path == "/api/clear-logs":
            LOG_BUFFER.clear()
            self._send_json({"status": "ok"})
        elif self.path == "/api/stop":
            ENGINE_INSTANCE.stop()
            append_global_log("[DURDURULDU] İşlem tamamen durduruldu.")
            self._send_json({"status": "ok"})
        elif self.path == "/api/pause":
            ENGINE_INSTANCE.pause()
            append_global_log("[DURAKLATILDI] Tarama geçici olarak duraklatıldı.")
            self._send_json({"status": "ok"})
        elif self.path == "/api/resume":
            ENGINE_INSTANCE.resume()
            append_global_log("[DEVAM EDİYOR] Tarama kaldığı yerden devam ediyor...")
            self._send_json({"status": "ok"})
        elif self.path == "/api/search-and-add-movie":
            body = self._read_json()
            query = body.get("query", "").strip()
            cfg = load_config()
            sites = cfg.get("saved_sites", [])
            token = cfg.get("github_token", "").strip()
            tmdb = cfg.get("tmdb_key", "").strip()

            if not query:
                self._send_json({"status": "error", "message": "Film adı boş olamaz"})
                return

            ENGINE_INSTANCE.is_running = True
            ENGINE_INSTANCE.is_paused = False

            def single_worker():
                append_global_log(f"🎯 [HEDEFLİ ARAMA BAŞLADI] '{query}' kayıtlı {len(sites)} sitede aranıyor...")
                pub = GitHubAtomicPublisher(token=token, log_callback=append_global_log)
                
                # Doğrudan URL mi verildi yoksa film adı mı?
                if query.startswith("http://") or query.startswith("https://"):
                    found_dict = {query: {"Popüler"}}
                else:
                    found_dict = ENGINE_INSTANCE.search_specific_movie_across_sites(query, sites)

                if not found_dict:
                    append_global_log(f"✗ [BULUNAMADI] '{query}' için kayıtlı sitelerde aktif film linki bulunamadı.")
                    return

                append_global_log(f"✓ '{query}' için {len(found_dict)} olası kaynak bulundu. Video akışları ve detaylar çözülüyor...")
                resolved_list = []

                for m_url, cats in found_dict.items():
                    if not ENGINE_INSTANCE.check_alive():
                        break
                    append_global_log(f"  ⚡ Çözülüyor: {m_url}...")
                    m_data = ENGINE_INSTANCE.scrape_movie_page(m_url, cats, tmdb_api_key=tmdb)
                    if m_data and m_data.get("streams"):
                        resolved_list.append(m_data)
                        c_str = ", ".join(m_data["genres"])
                        append_global_log(f"  ✓ {m_data['title']} ({len(m_data['streams'])} Sunucu • [{c_str}])")
                        # İlk çalışan kaynağı çözünce hemen Nuvio'ya aktar
                        append_global_log(f"🚀 [NUVIO AKTARIM] {m_data['title']} Nuvio'ya tek başına ekleniyor...")
                        pub.publish_movies([m_data])
                        append_global_log(f"🎉 [BAŞARILI] '{m_data['title']}' başarıyla kurtarıldı ve Nuvio'ya yüklendi!")
                        break

                if not resolved_list:
                    append_global_log(f"✗ [BAŞARISIZ] '{query}' bulundu fakat çalışan video akışı (m3u8/mp4) çözülemedi.")

            CURRENT_WORKER = threading.Thread(target=single_worker, daemon=True)
            CURRENT_WORKER.start()
            self._send_json({"status": "started"})
        elif self.path == "/api/start-all":
            cfg = load_config()
            sites = cfg.get("saved_sites", [])
            max_p = int(cfg.get("max_pages", 20))
            token = cfg.get("github_token", "").strip()
            tmdb = cfg.get("tmdb_key", "").strip()

            ENGINE_INSTANCE.is_running = True
            ENGINE_INSTANCE.is_paused = False

            def worker():
                try:
                    append_global_log(f"[GÜNCELLEME BAŞLADI] Toplam {len(sites)} kayıtlı sitenin tamamı taranıyor...")
                    pub_names, pub_ids, processed_urls = get_already_published_identifiers()
                    append_global_log(f"📋 [HAFIZA KONTROLÜ] Önceden taranıp Nuvio'ya eklenmiş {len(pub_names | pub_ids | processed_urls)} içerik tespit edildi. Bu filmler tekrar taranmayacak, doğrudan atlanacak!")

                    all_scraped = []
                    pub = GitHubAtomicPublisher(token=token, log_callback=append_global_log)
                    batch_buffer = []

                    for s_idx, site_url in enumerate(sites):
                        if not ENGINE_INSTANCE.check_alive():
                            break
                        append_global_log(f"--- [SİTE {s_idx + 1}/{len(sites)}] Taranıyor: {site_url} ---")
                        movie_dict = ENGINE_INSTANCE.discover_movie_links(site_url, max_pages=max_p)
                        append_global_log(f"✓ {site_url} üzerinde {len(movie_dict)} film bulundu. Video akışları çözülüyor...")

                        for m_idx, (m_url, cats) in enumerate(movie_dict.items()):
                            if not ENGINE_INSTANCE.check_alive():
                                break
                            short = m_url.rstrip("/").split("/")[-1]

                            # URL daha önce işlendiyse doğrudan atla
                            if m_url in processed_urls:
                                append_global_log(f"  ⏭ {short} (Daha önceden zaten eklenmiş, atlandı)")
                                continue

                            # URL'deki film adı bilinen yayınlanmış bir ada uyuyorsa atla
                            slug_cand = short.replace("-izle", "").replace("-", " ").strip().lower()
                            if slug_cand in pub_names:
                                append_global_log(f"  ⏭ {short} (Nuvio'da zaten mevcut: {slug_cand}, atlandı)")
                                processed_urls.add(m_url)
                                continue

                            append_global_log(f"[{m_idx + 1}/{len(movie_dict)}] Çözülüyor: {short}...")
                            m_data = ENGINE_INSTANCE.scrape_movie_page(m_url, cats, tmdb_api_key=tmdb)
                            
                            # İşlendi olarak kaydet
                            processed_urls.add(m_url)
                            save_processed_urls(processed_urls)

                            if m_data and m_data.get("streams"):
                                # Film adı veya ID'si yayında varsa atla
                                m_title_norm = m_data['title'].strip().lower()
                                m_id_norm = m_data['tmdbId'].strip().lower()
                                if m_title_norm in pub_names or m_id_norm in pub_ids:
                                    append_global_log(f"  ⏭ {m_data['title']} (Nuvio'da zaten mevcut, atlandı)")
                                    continue

                                pub_names.add(m_title_norm)
                                pub_ids.add(m_id_norm)
                                batch_buffer.append(m_data)
                                s_count = len(m_data["streams"])
                                c_str = ", ".join(m_data["genres"])
                                append_global_log(f"  ✓ {m_data['title']} ({s_count} Sunucu • [{c_str}])")

                                # Her 3 film çözüldüğünde beklemeden anında GitHub ve Nuvio'ya aktar!
                                if len(batch_buffer) >= 3:
                                    append_global_log(f"⚡ [3 FİLM DOLDU • ANLIK YÜKLEME] Çözülen {len(batch_buffer)} film/dizi anında Nuvio & GitHub'a aktarılıyor...")
                                    pub.publish_movies(batch_buffer)
                                    batch_buffer.clear()
                            else:
                                append_global_log(f"  ↷ {short} (Aktif akış yok, atlandı)")

                    # Tarama bittiğinde veya durdurulduğunda arabellekte kalan son filmleri hemen aktar
                    if batch_buffer and ENGINE_INSTANCE.check_alive():
                        append_global_log(f"⚡ [SON KALANLARI YÜKLEME] Arabellekte kalan {len(batch_buffer)} film Nuvio & GitHub'a aktarılıyor...")
                        pub.publish_movies(batch_buffer)
                        batch_buffer.clear()

                    if ENGINE_INSTANCE.is_running:
                        append_global_log("✓ [BİTTİ] Tüm tarama ve anlık yükleme tamamlandı.")
                    else:
                        append_global_log("⏹ [SONLANDI] Tarama kullanıcı tarafından durduruldu.")
                except Exception as e:
                    import traceback
                    append_global_log(f"💥 [BEKLENMEDİK HATA] {str(e)}")
                    append_global_log(traceback.format_exc())
                finally:
                    ENGINE_INSTANCE.is_running = False
                    ENGINE_INSTANCE.is_paused = False

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
                "--window-size=1180,880",
                "--no-first-run",
                "--no-default-browser-check"
            ])
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
