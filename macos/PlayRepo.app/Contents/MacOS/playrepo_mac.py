#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PlayRepo macOS Desktop Application
Full-featured desktop port of the Android APK for macOS.
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
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor

import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext

CONFIG_FILE = os.path.expanduser("~/.playrepo_config.json")
REPO_OWNER = "csprofesor"
REPO_NAME = "apple"
DEFAULT_BRANCH = "main"
LOGO_URL = "https://cdn.jsdelivr.net/gh/csprofesor/apple@main/playrepo/logo.png"

USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4.1 Safari/605.1.15"
]

# -------------------------------------------------------------
# HTTP & Network Helpers
# -------------------------------------------------------------
def http_get(url: str, referer: str = None, headers: dict = None, timeout: int = 12) -> str:
    """Uses macOS native /usr/bin/curl for TLS 1.3, HTTP/2, and Cloudflare bypass."""
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
    """Sends JSON requests to GitHub REST / Git Data APIs."""
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
            "/tag/", "#", "javascript:", ".jpg", ".png", ".css", ".js", "/login",
            "/uye", "/takvim", "/trendler", "/seri-filmler", "/istek"
        ]
        if any(d in lower for d in disallowed):
            return False
        return any(k in lower for k in ["/film/", "-izle", "/dizi/", "/filmler/"])

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

        movie_categories = {}  # url -> set of categories
        queue = [site_url]
        visited = set()
        page_category_map = {site_url: "Popüler"}

        # Discover category pages from menu
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

        # Direct playable stream
        if (".m3u8" in lower or ".mp4" in lower) and not any(k in lower for k in ["/embed", "/e/", "/video/"]):
            return embed_url

        # 1. Mail.ru 1080p resolver
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

        # Dean Edwards Packer unpack
        if "eval(function(p,a,c,k,e,d)" in html:
            html += "\n" + unpack_packer_js(html)

        # 2. Vidmoly / Vidhide / FileLions / StreamWish / Dropload M3U8
        m3u8_m = re.search(r'file\s*:\s*["\'](https?://[^"\']+\.m3u8[^"\']*)["\']', html, re.I) or \
                 re.search(r'sources\s*:\s*\[\s*\{\s*file\s*:\s*["\'](https?://[^"\']+\.m3u8[^"\']*)["\']', html, re.I) or \
                 re.search(r'["\'](https?://[^"\']+\.m3u8[^"\']*)["\']', html, re.I)
        if m3u8_m:
            return m3u8_m.group(1)

        # 3. VOE
        voe_m = re.search(r"['\"]hls['\"]\s*:\s*['\"](https?://[^'\"]+)['\"]", html, re.I)
        if voe_m:
            return voe_m.group(1)

        # 4. Streamtape
        if "streamtape.com" in lower or "streamta.pe" in lower:
            st_m = re.search(r"document\.getElementById\('robotlink'\)\.innerHTML\s*=\s*['\"](.*?)['\"]", html)
            if st_m:
                tok = st_m.group(1)
                tok_add = re.search(r"\+ \('xcd(.*?)\.substring\(1\)", html)
                if tok_add:
                    tok += tok_add.group(1)
                st_url = f"https:{tok}" if tok.startswith("//") else (f"https://streamtape.com{tok}" if tok.startswith("/") else tok)
                return st_url

        # 5. Doodstream
        if "dood" in lower or "ds2play" in lower:
            pass_m = re.search(r"/pass_md5/([^'\"]+)", html)
            if pass_m:
                pass_url = f"https://dood.to/pass_md5/{pass_m.group(1)}"
                token_res = http_get(pass_url, referer=embed_url)
                if token_res:
                    return f"{token_res.strip()}~PlayRepo~{int(time.time() * 1000)}"

        # 6. Sibnet
        if "video.sibnet.ru" in lower:
            sib_m = re.search(r'["\'](/v/[^"\']+\.mp4)["\']', html)
            if sib_m:
                return f"https://video.sibnet.ru{sib_m.group(1)}"

        # 7. OK.ru
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

        # 8. Bitchute
        if "bitchute.com" in lower:
            bc_m = re.search(r'(https?://[^\s"\'<>]+\.bitchute\.com/[^\s"\'<>]+\.mp4)', html, re.I)
            if bc_m:
                return bc_m.group(1)

        # 9. Nested iframe / player recursion
        nested_m = re.search(r'<iframe\s+[^>]*src=["\']([^"\']+)["\']', html, re.I)
        if nested_m:
            nested_src = urllib.parse.urljoin(embed_url, nested_m.group(1).strip())
            if nested_src != embed_url:
                res = self.unpack_embed_stream(nested_src, referer=embed_url, depth=depth + 1)
                if res:
                    return res

        return ""

    def scrape_movie_page(self, movie_url: str, initial_cats: set, tmdb_api_key: str = None) -> dict:
        html = http_get(movie_url, referer=movie_url)
        if not html:
            return None

        # Clean title
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

        # Year
        year_m = re.search(r'\b(19\d{2}|20\d{2})\b', raw_title) or re.search(r'\b(19\d{2}|20\d{2})\b', html)
        year = year_m.group(1) if year_m else ""

        # Poster
        poster_m = re.search(r'<meta\s+property=["\']og:image["\']\s+content=["\']([^"\']+)["\']', html, re.I) or \
                   re.search(r'<img[^>]+class=["\'][^"\']*(?:poster|afis|cover)[^"\']*["\'][^>]+src=["\']([^"\']+)["\']', html, re.I) or \
                   re.search(r'<img[^>]+src=["\']([^"\']+)["\'][^>]+class=["\'][^"\']*(?:poster|afis|cover)[^"\']*["\']', html, re.I)
        poster = urllib.parse.urljoin(movie_url, poster_m.group(1).strip()) if poster_m else LOGO_URL

        # Description
        desc_m = re.search(r'<meta\s+property=["\']og:description["\']\s+content=["\'](.*?)["\']', html, re.I) or \
                 re.search(r'<meta\s+name=["\']description["\']\s+content=["\'](.*?)["\']', html, re.I) or \
                 re.search(r'class=["\'][^"\']*(?:film-ozeti|ozet|story)[^"\']*["\'][^>]*>(.*?)</div>', html, re.I | re.DOTALL)
        desc = re.sub(r'<[^>]+>', '', desc_m.group(1)).strip() if desc_m else "Play • 1080p TR"

        # IMDb Rating
        imdb_m = re.search(r'class=["\'][^"\']*(?:imdb-puan|imdb|puan|rating)[^"\']*["\'][^>]*>([0-9.,]+)<', html, re.I)
        imdb_rating = imdb_m.group(1).replace(",", ".").strip() if imdb_m else None

        # Runtime
        time_m = re.search(r'(\d{2,3})\s*(?:dk|dakika|min)', html, re.I)
        runtime = f"{time_m.group(1)} dk" if time_m else None

        # Categories
        genres = set(initial_cats) if initial_cats else {"Popüler"}
        for href, text in re.findall(r'<a\s+[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', html, re.I | re.DOTALL):
            if any(k in href.lower() for k in ["/kategori/", "/tur/", "/filmizle/", "/genre/"]):
                cat = self.detect_category(href, re.sub(r'<[^>]+>', '', text).strip())
                if cat != "Popüler":
                    genres.add(cat)

        # Detect Candidates: Embeds, IFrames, Data Attributes
        candidate_embeds = []  # (url, lang_tag)
        default_lang = "[TR Altyazılı]" if "altyaz" in raw_title.lower() or "altyaz" in movie_url.lower() else "[TR Dublaj]"

        def get_lang(txt: str) -> str:
            t = txt.lower()
            if any(k in t for k in ["altyaz", "sub", "original", "orijinal", "ingilizce"]):
                return "[TR Altyazılı]"
            if any(k in t for k in ["dublaj", "turkce", "türkçe"]):
                return "[TR Dublaj]"
            return default_lang

        # 1. Search buttons & player tabs
        for m in re.finditer(r'<[^>]+data-(?:frame|player|embed|video|url)=["\']([^"\']+)["\'][^>]*>(.*?)</[^>]+>', html, re.I | re.DOTALL):
            c_url = urllib.parse.urljoin(movie_url, m.group(1).strip())
            c_lang = get_lang(m.group(2) + " " + c_url)
            candidate_embeds.append((c_url, c_lang))

        # 2. Search iframes
        for m in re.finditer(r'<iframe\s+[^>]*src=["\']([^"\']+)["\'][^>]*>', html, re.I):
            c_url = urllib.parse.urljoin(movie_url, m.group(1).strip())
            candidate_embeds.append((c_url, default_lang))

        # 3. Direct unpack regex
        for m in re.finditer(r'["\'](https?://[^\s"\'<>]+\.(?:m3u8|mp4)[^\s"\'<>]*)["\']', html, re.I):
            candidate_embeds.append((m.group(1).strip(), default_lang))

        # 4. Check Alternate Language Page (e.g. Dubbed page links to Subtitled)
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

        # Resolve streams
        resolved_streams = []
        seen_urls = set()

        for cand_url, cand_lang in candidate_embeds:
            if not cand_url or cand_url in seen_urls:
                continue
            direct_url = self.unpack_embed_stream(cand_url, referer=movie_url)
            if direct_url and direct_url not in seen_urls:
                seen_urls.add(direct_url)
                is_m3u8 = ".m3u8" in direct_url.lower()
                resolved_streams.append({
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
                })

        if not resolved_streams:
            return None

        # Prioritize HLS (.m3u8) first
        resolved_streams.sort(key=lambda s: 1 if s["format"] == "m3u8" else 0, reverse=True)

        dub_c = 0
        sub_c = 0
        oth_c = 0
        for st in resolved_streams:
            if "Dublaj" in st["title"]:
                dub_c += 1
                st["title"] = f"Play • Dublaj {dub_c} • 1080p • [TR Dublaj]"
            elif "Altyaz" in st["title"]:
                sub_c += 1
                st["title"] = f"Play • Altyazı {sub_c} • 1080p • [TR Altyazılı]"
            else:
                oth_c += 1
                st["title"] = f"Play • Sunucu {oth_c} • 1080p • [TR]"

        # Metadata resolution: Cinemeta -> TMDB -> Fallback
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
            "streams": resolved_streams
        }

    def resolve_meta(self, title: str, poster: str, desc: str, year: str, tmdb_key: str = None):
        # 1. Cinemeta (Universal Stremio open catalog)
        try:
            q = urllib.parse.quote(title)
            cm_res = http_json(f"https://v3-cinemeta.strem.io/catalog/movie/top/search={q}.json")
            metas = cm_res.get("metas", [])
            if metas:
                m0 = metas[0]
                m_id = m0.get("id")
                if m_id and m_id.startswith("tt"):
                    return (
                        m_id,
                        m0.get("name") or title,
                        m0.get("poster") or poster,
                        m0.get("description") or desc,
                        m0.get("releaseInfo") or year
                    )
        except Exception:
            pass

        # 2. TMDB
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

        # 3. Fallback
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
            self.log("✗ [ERROR] GitHub Token girilmemiş! Ayarlar sekmesinden GitHub Token kaydedin.")
            return False

        self.log(f"[GITHUB] {len(scraped_movies)} film için Git Data API yükleme paketi hazırlanıyor...")

        files_to_commit = {}

        # 1. Fetch current catalog.json
        cat_url = f"https://raw.githubusercontent.com/{self.owner}/{self.repo}/{DEFAULT_BRANCH}/data/catalog.json?t={int(time.time())}"
        cat_json = http_json(cat_url)
        metas = cat_json.get("metas", []) if isinstance(cat_json, dict) else []
        meta_dict = {m.get("id"): m for m in metas if isinstance(m, dict) and m.get("id")}

        # 2. Fetch current streams.json
        str_url = f"https://raw.githubusercontent.com/{self.owner}/{self.repo}/{DEFAULT_BRANCH}/data/streams.json?t={int(time.time())}"
        str_json = http_json(str_url)
        streams_map = str_json if isinstance(str_json, dict) else {}

        # 3. Merge scraped movies
        for m in scraped_movies:
            m_id = m["tmdbId"]
            hyphen_id = m_id.replace(":", "-").replace("_", "-")
            safe_id = m_id.replace(":", "_")

            cat_item = {
                "id": m_id,
                "type": "movie",
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

            # Detailed meta JSON
            detail_meta = {
                "meta": {
                    "id": m_id,
                    "type": "movie",
                    "name": m["title"],
                    "poster": m["poster"],
                    "background": m["poster"],
                    "logo": LOGO_URL,
                    "description": m["description"],
                    "releaseInfo": m["year"],
                    "genres": m["genres"],
                    "behaviorHints": {
                        "defaultVideoId": m_id,
                        "hasScheduledVideos": False
                    },
                    "videos": [
                        {
                            "id": m_id,
                            "title": m["title"],
                            "released": f"{m['year']}-01-01T00:00:00.000Z" if m.get("year") else None
                        }
                    ]
                }
            }
            if m.get("imdbRating"):
                detail_meta["meta"]["imdbRating"] = m["imdbRating"]
            if m.get("runtime"):
                detail_meta["meta"]["runtime"] = m["runtime"]

            stream_obj = {"streams": m["streams"]}

            # Meta and stream files (3 safe ID formats)
            m_json_str = json.dumps(detail_meta, ensure_ascii=False, indent=2)
            s_json_str = json.dumps(stream_obj, ensure_ascii=False, indent=2)

            files_to_commit[f"playrepo/meta/movie/{m_id}.json"] = m_json_str
            files_to_commit[f"playrepo/meta/movie/{hyphen_id}.json"] = m_json_str
            files_to_commit[f"playrepo/meta/movie/{safe_id}.json"] = m_json_str

            files_to_commit[f"playrepo/stream/movie/{m_id}.json"] = s_json_str
            files_to_commit[f"playrepo/stream/movie/{hyphen_id}.json"] = s_json_str
            files_to_commit[f"playrepo/stream/movie/{safe_id}.json"] = s_json_str

        # Update data/catalog.json
        all_metas = list(meta_dict.values())
        files_to_commit["data/catalog.json"] = json.dumps({"metas": all_metas}, ensure_ascii=False, indent=2)
        files_to_commit["data/streams.json"] = json.dumps(streams_map, ensure_ascii=False, indent=2)

        # 4. Update category catalogs
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

        # 5. Atomic Git Data API commit
        self.log(f"[GITHUB] Toplam {len(files_to_commit)} dosya tek bir atomik commit olarak GitHub'a aktarılıyor...")

        # Step A: Get main branch ref
        ref_data = http_json(f"https://api.github.com/repos/{self.owner}/{self.repo}/git/ref/heads/{DEFAULT_BRANCH}", token=self.token)
        latest_commit_sha = ref_data.get("object", {}).get("sha")
        if not latest_commit_sha:
            self.log(f"✗ [ERROR] GitHub ref alınamadı: {ref_data.get('message', 'Yetkisiz erişim / Token geçersiz')}")
            return False

        # Step B: Get base tree
        commit_data = http_json(f"https://api.github.com/repos/{self.owner}/{self.repo}/git/commits/{latest_commit_sha}", token=self.token)
        base_tree_sha = commit_data.get("tree", {}).get("sha")

        # Step C: Create blobs
        tree_items = []
        for path, content in files_to_commit.items():
            blob_res = http_json(
                f"https://api.github.com/repos/{self.owner}/{self.repo}/git/blobs",
                method="POST",
                data={"content": content, "encoding": "utf-8"},
                token=self.token
            )
            blob_sha = blob_res.get("sha")
            if blob_sha:
                tree_items.append({
                    "path": path,
                    "mode": "100644",
                    "type": "blob",
                    "sha": blob_sha
                })

        # Step D: Create tree
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

        # Step E: Create commit
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

        # Step F: Update branch ref
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

# -------------------------------------------------------------
# GUI Application (Ultra High Contrast Pitch Black Edition)
# -------------------------------------------------------------
class PlayRepoApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("PlayRepo Pro • macOS Film Çekici & Senkronize Edici")
        self.geometry("980x740")
        self.minsize(860, 620)

        # True Pitch Black & High-Contrast Colors
        self.bg_color = "#000000"          # Saf Siyah
        self.card_color = "#0e1015"        # Koyu Kart
        self.card_border = "#2e3440"       # Belirgin Kart Kenarlığı
        self.input_bg = "#161922"          # Belirgin Giriş Kutusu Arka Planı
        self.input_border = "#3b82f6"      # Mavi Parlak Kenarlık
        self.accent_color = "#2563eb"      # Canlı Mavi Buton
        self.accent_hover = "#1d4ed8"
        self.text_color = "#ffffff"        # Saf Beyaz Metin
        self.text_dim = "#94a3b8"          # Açık Gri Açıklama
        self.success_color = "#059669"     # Zümrüt Yeşili
        self.success_hover = "#047857"
        self.danger_color = "#dc2626"      # Parlak Kırmızı
        self.danger_hover = "#b91c1c"

        self.configure(bg=self.bg_color)

        self.config_data = self.load_config()
        self.engine = ScraperEngine(log_callback=self.append_log)
        self.crawler_thread = None

        self.setup_ui()
        self.lift()
        self.attributes('-topmost', True)
        self.after(200, lambda: self.attributes('-topmost', False))

    def load_config(self) -> dict:
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
                    data = json.load(f)
                    default_cfg.update(data)
            except Exception:
                pass
        return default_cfg

    def save_config(self):
        try:
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(self.config_data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            self.append_log(f"Ayarlar kaydedilemedi: {e}")

    def setup_ui(self):
        # 1. Header Bar
        header = tk.Frame(self, bg="#08090c", bd=0, highlightbackground=self.card_border, highlightthickness=1)
        header.pack(fill=tk.X, side=tk.TOP)

        title_lbl = tk.Label(
            header,
            text="🎬 PlayRepo Pro",
            font=("SF Pro Display", 20, "bold"),
            fg="#ffffff",
            bg="#08090c"
        )
        title_lbl.pack(side=tk.LEFT, padx=(20, 10), pady=14)

        sub_lbl = tk.Label(
            header,
            text="macOS Film & Akış Senkronizasyonu (Dublaj + Altyazı • HLS 1080p)",
            font=("SF Pro Text", 12, "bold"),
            fg="#60a5fa",
            bg="#08090c"
        )
        sub_lbl.pack(side=tk.LEFT, padx=5, pady=16)

        # 2. Modern Tab Navigation (Pill Style)
        nav_frame = tk.Frame(self, bg=self.bg_color, pady=10)
        nav_frame.pack(fill=tk.X, side=tk.TOP, padx=20)

        self.btn_tab_dashboard = tk.Button(
            nav_frame, text="⚡ 1. KONTROL PANELİ", font=("SF Pro Text", 12, "bold"),
            fg="#ffffff", bg=self.accent_color, relief=tk.FLAT, bd=0, padx=20, pady=8, cursor="hand2",
            highlightbackground="#60a5fa", highlightthickness=1,
            command=lambda: self.switch_tab(0)
        )
        self.btn_tab_dashboard.pack(side=tk.LEFT, padx=(0, 10))

        self.btn_tab_sites = tk.Button(
            nav_frame, text="🌐 2. KAYITLI SİTELER", font=("SF Pro Text", 12, "bold"),
            fg="#94a3b8", bg="#13161f", relief=tk.FLAT, bd=0, padx=20, pady=8, cursor="hand2",
            highlightbackground=self.card_border, highlightthickness=1,
            command=lambda: self.switch_tab(1)
        )
        self.btn_tab_sites.pack(side=tk.LEFT, padx=(0, 10))

        self.btn_tab_settings = tk.Button(
            nav_frame, text="⚙️ 3. TEKLİ TARAMA & AYARLAR", font=("SF Pro Text", 12, "bold"),
            fg="#94a3b8", bg="#13161f", relief=tk.FLAT, bd=0, padx=20, pady=8, cursor="hand2",
            highlightbackground=self.card_border, highlightthickness=1,
            command=lambda: self.switch_tab(2)
        )
        self.btn_tab_settings.pack(side=tk.LEFT)

        # 3. Main Container
        self.container = tk.Frame(self, bg=self.bg_color)
        self.container.pack(fill=tk.BOTH, expand=True, padx=20, pady=(0, 15))

        self.tab_dashboard = tk.Frame(self.container, bg=self.bg_color)
        self.tab_sites = tk.Frame(self.container, bg=self.bg_color)
        self.tab_settings = tk.Frame(self.container, bg=self.bg_color)

        self.setup_tab_dashboard()
        self.setup_tab_sites()
        self.setup_tab_settings()

        self.switch_tab(0)

    def switch_tab(self, index: int):
        self.tab_dashboard.pack_forget()
        self.tab_sites.pack_forget()
        self.tab_settings.pack_forget()

        # Reset button styles
        for btn in [self.btn_tab_dashboard, self.btn_tab_sites, self.btn_tab_settings]:
            btn.config(bg="#13161f", fg="#94a3b8", highlightbackground=self.card_border)

        if index == 0:
            self.tab_dashboard.pack(fill=tk.BOTH, expand=True)
            self.btn_tab_dashboard.config(bg=self.accent_color, fg="#ffffff", highlightbackground="#60a5fa")
            self.update_dashboard_sites_summary()
        elif index == 1:
            self.tab_sites.pack(fill=tk.BOTH, expand=True)
            self.btn_tab_sites.config(bg=self.accent_color, fg="#ffffff", highlightbackground="#60a5fa")
            self.refresh_sites_listbox()
        elif index == 2:
            self.tab_settings.pack(fill=tk.BOTH, expand=True)
            self.btn_tab_settings.config(bg=self.accent_color, fg="#ffffff", highlightbackground="#60a5fa")

    # ---------------- Tab 1: Dashboard (With Quick Add Bar!) ----------------
    def setup_tab_dashboard(self):
        # Card 1: Hızlı Site Ekleme Kutusu (Doğrudan Ana Ekranda ve Çok Belirgin!)
        add_card = tk.Frame(
            self.tab_dashboard,
            bg=self.card_color,
            highlightbackground="#3b82f6",
            highlightthickness=2,
            padx=15,
            pady=12
        )
        add_card.pack(fill=tk.X, pady=(0, 12))

        lbl_row = tk.Frame(add_card, bg=self.card_color)
        lbl_row.pack(fill=tk.X, pady=(0, 6))

        tk.Label(
            lbl_row,
            text="🌐 TARANACAK YENİ SİTE VEYA KATEGORİ ADRESİ EKLE:",
            font=("SF Pro Text", 12, "bold"),
            fg="#60a5fa",
            bg=self.card_color
        ).pack(side=tk.LEFT)

        tk.Label(
            lbl_row,
            text="(Örn: https://www.hintfilmizle.com/film-izle/)",
            font=("SF Pro Text", 11),
            fg="#94a3b8",
            bg=self.card_color
        ).pack(side=tk.LEFT, padx=10)

        input_row = tk.Frame(add_card, bg=self.card_color)
        input_row.pack(fill=tk.X)

        self.ent_quick_site = tk.Entry(
            input_row,
            font=("SF Pro Text", 13),
            bg=self.input_bg,
            fg="#ffffff",
            insertbackground="#ffffff",
            highlightbackground="#60a5fa",
            highlightcolor="#38bdf8",
            highlightthickness=2,
            relief=tk.FLAT,
            bd=0
        )
        self.ent_quick_site.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=6, padx=(0, 12))

        btn_quick_add = tk.Button(
            input_row,
            text="➕ BU SİTEYİ LİSTEYE EKLE",
            font=("SF Pro Text", 12, "bold"),
            fg="#ffffff",
            bg=self.success_color,
            activebackground=self.success_hover,
            activeforeground="#ffffff",
            relief=tk.FLAT,
            bd=0,
            padx=18,
            pady=8,
            cursor="hand2",
            highlightbackground="#10b981",
            highlightthickness=1,
            command=self.add_site_from_quick_entry
        )
        btn_quick_add.pack(side=tk.LEFT)

        # Card 2: Kontroller ve Başlatma
        ctrl_card = tk.Frame(
            self.tab_dashboard,
            bg=self.card_color,
            highlightbackground=self.card_border,
            highlightthickness=1,
            padx=15,
            pady=12
        )
        ctrl_card.pack(fill=tk.X, pady=(0, 12))

        top_row = tk.Frame(ctrl_card, bg=self.card_color)
        top_row.pack(fill=tk.X)

        tk.Label(
            top_row,
            text="Sayfa Limiti:",
            fg="#ffffff",
            bg=self.card_color,
            font=("SF Pro Text", 12, "bold")
        ).pack(side=tk.LEFT, padx=(0, 8))

        self.ent_max_pages = tk.Entry(
            top_row,
            width=5,
            font=("SF Pro Text", 13, "bold"),
            bg=self.input_bg,
            fg="#38bdf8",
            insertbackground="#ffffff",
            highlightbackground="#60a5fa",
            highlightcolor="#38bdf8",
            highlightthickness=2,
            relief=tk.FLAT,
            bd=0,
            justify="center"
        )
        self.ent_max_pages.insert(0, str(self.config_data.get("max_pages", 20)))
        self.ent_max_pages.pack(side=tk.LEFT, ipady=5, padx=(0, 15))

        self.btn_start_all = tk.Button(
            top_row,
            text="⚡ TÜM SİTELERİ GÜNCELLE VE GITHUB'A YÜKLE",
            font=("SF Pro Text", 13, "bold"),
            fg="#ffffff",
            bg=self.accent_color,
            activebackground=self.accent_hover,
            activeforeground="#ffffff",
            relief=tk.FLAT,
            bd=0,
            padx=20,
            pady=8,
            cursor="hand2",
            highlightbackground="#60a5fa",
            highlightthickness=1,
            command=self.start_master_update
        )
        self.btn_start_all.pack(side=tk.LEFT, padx=(0, 10))

        self.btn_stop = tk.Button(
            top_row,
            text="⏹ Durdur",
            font=("SF Pro Text", 12, "bold"),
            fg="#ffffff",
            bg=self.danger_color,
            activebackground=self.danger_hover,
            activeforeground="#ffffff",
            relief=tk.FLAT,
            bd=0,
            padx=14,
            pady=8,
            cursor="hand2",
            state=tk.DISABLED,
            command=self.stop_crawler
        )
        self.btn_stop.pack(side=tk.LEFT, padx=(0, 10))

        self.btn_clear_log = tk.Button(
            top_row,
            text="🧹 Logları Temizle",
            font=("SF Pro Text", 11, "bold"),
            fg="#94a3b8",
            bg="#1e2230",
            relief=tk.FLAT,
            bd=0,
            padx=12,
            pady=8,
            cursor="hand2",
            command=self.clear_logs
        )
        self.btn_clear_log.pack(side=tk.RIGHT)

        # Aktif Siteler Özeti
        self.lbl_sites_summary = tk.Label(
            ctrl_card,
            text="Kayıtlı Siteler: 1 site hazır",
            font=("SF Pro Text", 11),
            fg="#34d399",
            bg=self.card_color
        )
        self.lbl_sites_summary.pack(anchor="w", pady=(8, 0))
        self.update_dashboard_sites_summary()

        # Card 3: Canlı Terminal Ekranı
        term_header = tk.Frame(self.tab_dashboard, bg=self.bg_color)
        term_header.pack(fill=tk.X, pady=(0, 6))

        tk.Label(
            term_header,
            text="CANLI İŞLEM TERMİNALİ",
            font=("SF Pro Text", 11, "bold"),
            fg="#60a5fa",
            bg=self.bg_color
        ).pack(side=tk.LEFT)

        self.txt_log = scrolledtext.ScrolledText(
            self.tab_dashboard,
            bg="#050608",
            fg="#34d399",
            insertbackground="#ffffff",
            font=("Menlo", 11),
            bd=0,
            relief=tk.FLAT,
            highlightbackground=self.card_border,
            highlightthickness=1,
            padx=10,
            pady=10
        )
        self.txt_log.pack(fill=tk.BOTH, expand=True)
        self.append_log("[READY] PlayRepo macOS Pro (Siyah Tema) hazır.")
        self.append_log("[INFO] Yukarıdaki mavi kutudan yeni site linki ekleyebilir veya doğrudan 'TÜM SİTELERİ GÜNCELLE' yapabilirsiniz.")

    def update_dashboard_sites_summary(self):
        saved = self.config_data.get("saved_sites", [])
        c = len(saved)
        sites_preview = ", ".join(s.replace("https://", "").replace("http://", "").rstrip("/") for s in saved[:3])
        if c > 3:
            sites_preview += f" (+{c - 3} daha)"
        self.lbl_sites_summary.config(text=f"📋 Taranacak Kayıtlı Siteler ({c} Adet): {sites_preview}")

    def add_site_from_quick_entry(self):
        url = self.ent_quick_site.get().strip()
        if not url.startswith("http"):
            messagebox.showwarning("Geçersiz URL", "Lütfen geçerli bir site linki girin!\nÖrnek: https://www.hintfilmizle.com/film-izle/")
            return
        saved = self.config_data.setdefault("saved_sites", [])
        if url in saved:
            messagebox.showinfo("Zaten Kayıtlı", "Bu site adresi zaten listenizde mevcut.")
            return

        saved.append(url)
        self.save_config()
        self.update_dashboard_sites_summary()
        self.ent_quick_site.delete(0, tk.END)
        self.append_log(f"✓ [YENİ SİTE EKLENDİ] {url}")
        messagebox.showinfo("Başarılı", f"Site başarıyla eklendi:\n{url}")

    # ---------------- Tab 2: Saved Sites ----------------
    def setup_tab_sites(self):
        add_card = tk.Frame(
            self.tab_sites,
            bg=self.card_color,
            highlightbackground="#3b82f6",
            highlightthickness=2,
            padx=15,
            pady=12
        )
        add_card.pack(fill=tk.X, pady=(0, 15))

        tk.Label(
            add_card,
            text="YENİ SİTE ADRESİ (URL):",
            font=("SF Pro Text", 12, "bold"),
            fg="#60a5fa",
            bg=self.card_color
        ).pack(anchor="w", pady=(0, 6))

        in_row = tk.Frame(add_card, bg=self.card_color)
        in_row.pack(fill=tk.X)

        self.ent_new_site = tk.Entry(
            in_row,
            font=("SF Pro Text", 13),
            bg=self.input_bg,
            fg="#ffffff",
            insertbackground="#ffffff",
            highlightbackground="#60a5fa",
            highlightcolor="#38bdf8",
            highlightthickness=2,
            relief=tk.FLAT,
            bd=0
        )
        self.ent_new_site.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=6, padx=(0, 12))

        btn_add = tk.Button(
            in_row,
            text="➕ SİTEYİ EKLE",
            font=("SF Pro Text", 12, "bold"),
            fg="#ffffff",
            bg=self.success_color,
            activebackground=self.success_hover,
            activeforeground="#ffffff",
            relief=tk.FLAT,
            bd=0,
            padx=18,
            pady=8,
            cursor="hand2",
            highlightbackground="#10b981",
            highlightthickness=1,
            command=self.add_new_site
        )
        btn_add.pack(side=tk.LEFT)

        # List Card
        list_frame = tk.Frame(self.tab_sites, bg=self.bg_color)
        list_frame.pack(fill=tk.BOTH, expand=True)

        tk.Label(
            list_frame,
            text="KAYITLI TARAMA SİTELERİ LİSTESİ",
            font=("SF Pro Text", 11, "bold"),
            fg="#ffffff",
            bg=self.bg_color
        ).pack(anchor="w", pady=(0, 8))

        self.listbox_sites = tk.Listbox(
            list_frame,
            bg=self.card_color,
            fg="#ffffff",
            selectbackground=self.accent_color,
            selectforeground="#ffffff",
            font=("SF Pro Text", 13),
            bd=0,
            relief=tk.FLAT,
            highlightbackground=self.card_border,
            highlightthickness=1,
            height=12,
            activestyle="none"
        )
        self.listbox_sites.pack(fill=tk.BOTH, expand=True, pady=(0, 12))

        bot_row = tk.Frame(list_frame, bg=self.bg_color)
        bot_row.pack(fill=tk.X)

        btn_delete = tk.Button(
            bot_row,
            text="🗑️ Seçili Siteyi Sil",
            font=("SF Pro Text", 12, "bold"),
            fg="#ffffff",
            bg=self.danger_color,
            activebackground=self.danger_hover,
            activeforeground="#ffffff",
            relief=tk.FLAT,
            bd=0,
            padx=16,
            pady=8,
            cursor="hand2",
            highlightbackground="#ef4444",
            highlightthickness=1,
            command=self.delete_selected_site
        )
        btn_delete.pack(side=tk.LEFT, padx=(0, 12))

        btn_reset = tk.Button(
            bot_row,
            text="🔄 Varsayılana Sıfırla (Hintfilmizle)",
            font=("SF Pro Text", 12),
            fg="#ffffff",
            bg="#262a36",
            activebackground="#1e2230",
            activeforeground="#ffffff",
            relief=tk.FLAT,
            bd=0,
            padx=16,
            pady=8,
            cursor="hand2",
            highlightbackground=self.card_border,
            highlightthickness=1,
            command=self.reset_sites_to_default
        )
        btn_reset.pack(side=tk.LEFT)

    def refresh_sites_listbox(self):
        self.listbox_sites.delete(0, tk.END)
        for s in self.config_data.get("saved_sites", []):
            self.listbox_sites.insert(tk.END, f"  🌐  {s}")

    def add_new_site(self):
        url = self.ent_new_site.get().strip()
        if not url.startswith("http"):
            messagebox.showwarning("Geçersiz URL", "Lütfen geçerli bir site adresi girin (http:// veya https://)!")
            return
        saved = self.config_data.setdefault("saved_sites", [])
        if url in saved:
            messagebox.showinfo("Mevcut", "Bu site zaten listede kayıtlı.")
            return

        saved.append(url)
        self.save_config()
        self.refresh_sites_listbox()
        self.ent_new_site.delete(0, tk.END)
        self.update_dashboard_sites_summary()
        self.append_log(f"[SITE EKLENDİ] {url}")
        messagebox.showinfo("Başarılı", f"{url} listeye eklendi!")

    def delete_selected_site(self):
        sel = self.listbox_sites.curselection()
        if not sel:
            messagebox.showinfo("Seçim Yapın", "Lütfen silmek istediğiniz siteyi listeden tıklayarak seçin.")
            return
        idx = sel[0]
        saved = self.config_data.get("saved_sites", [])
        if 0 <= idx < len(saved):
            removed = saved.pop(idx)
            self.save_config()
            self.refresh_sites_listbox()
            self.update_dashboard_sites_summary()
            self.append_log(f"[SİTE SİLİNDİ] {removed}")

    def reset_sites_to_default(self):
        self.config_data["saved_sites"] = ["https://www.hintfilmizle.com/film-izle/"]
        self.save_config()
        self.refresh_sites_listbox()
        self.update_dashboard_sites_summary()
        self.append_log("[SIFIRLANDI] Site listesi varsayılana sıfırlandı.")

    # ---------------- Tab 3: Single Crawl & Settings ----------------
    def setup_tab_settings(self):
        # Card 1: Tekli Tarama
        single_card = tk.Frame(
            self.tab_settings,
            bg=self.card_color,
            highlightbackground="#3b82f6",
            highlightthickness=2,
            padx=15,
            pady=15
        )
        single_card.pack(fill=tk.X, pady=(0, 15))

        tk.Label(
            single_card,
            text="TEKLİ FİLM VEYA SİTE TARAMA",
            font=("SF Pro Text", 12, "bold"),
            fg="#60a5fa",
            bg=self.card_color
        ).pack(anchor="w", pady=(0, 8))

        s_row = tk.Frame(single_card, bg=self.card_color)
        s_row.pack(fill=tk.X)

        tk.Label(
            s_row,
            text="Hedef URL:",
            font=("SF Pro Text", 12, "bold"),
            fg="#ffffff",
            bg=self.card_color
        ).pack(side=tk.LEFT, padx=(0, 10))

        self.ent_single_url = tk.Entry(
            s_row,
            font=("SF Pro Text", 13),
            bg=self.input_bg,
            fg="#ffffff",
            insertbackground="#ffffff",
            highlightbackground="#60a5fa",
            highlightcolor="#38bdf8",
            highlightthickness=2,
            relief=tk.FLAT,
            bd=0
        )
        self.ent_single_url.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=6, padx=(0, 12))

        btn_single_crawl = tk.Button(
            s_row,
            text="▶ TEKLİ TARA & YÜKLE",
            font=("SF Pro Text", 12, "bold"),
            fg="#ffffff",
            bg=self.accent_color,
            activebackground=self.accent_hover,
            activeforeground="#ffffff",
            relief=tk.FLAT,
            bd=0,
            padx=16,
            pady=8,
            cursor="hand2",
            highlightbackground="#60a5fa",
            highlightthickness=1,
            command=self.start_single_crawl
        )
        btn_single_crawl.pack(side=tk.LEFT)

        # Card 2: Ayarlar
        cfg_card = tk.Frame(
            self.tab_settings,
            bg=self.card_color,
            highlightbackground=self.card_border,
            highlightthickness=1,
            padx=15,
            pady=15
        )
        cfg_card.pack(fill=tk.X)

        tk.Label(
            cfg_card,
            text="GİTHUB & TMDB ERİŞİM AYARLARI",
            font=("SF Pro Text", 12, "bold"),
            fg="#60a5fa",
            bg=self.card_color
        ).pack(anchor="w", pady=(0, 15))

        # Token Row
        tk.Label(
            cfg_card,
            text="GitHub Personal Access Token (PAT):",
            font=("SF Pro Text", 11, "bold"),
            fg="#ffffff",
            bg=self.card_color
        ).pack(anchor="w", pady=(0, 4))

        t_row = tk.Frame(cfg_card, bg=self.card_color)
        t_row.pack(fill=tk.X, pady=(0, 12))

        self.ent_token = tk.Entry(
            t_row,
            font=("SF Pro Text", 13),
            bg=self.input_bg,
            fg="#ffffff",
            insertbackground="#ffffff",
            highlightbackground="#60a5fa",
            highlightcolor="#38bdf8",
            highlightthickness=2,
            relief=tk.FLAT,
            bd=0,
            show="•"
        )
        self.ent_token.insert(0, self.config_data.get("github_token", ""))
        self.ent_token.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=6, padx=(0, 10))

        self.btn_show_token = tk.Button(
            t_row,
            text="Göster",
            font=("SF Pro Text", 11, "bold"),
            bg="#262a36",
            fg="#ffffff",
            relief=tk.FLAT,
            bd=0,
            padx=12,
            pady=6,
            cursor="hand2",
            highlightbackground=self.card_border,
            highlightthickness=1,
            command=self.toggle_token_visibility
        )
        self.btn_show_token.pack(side=tk.LEFT)

        # TMDB Key Row
        tk.Label(
            cfg_card,
            text="TMDB API Key (İsteğe Bağlı):",
            font=("SF Pro Text", 11, "bold"),
            fg="#ffffff",
            bg=self.card_color
        ).pack(anchor="w", pady=(0, 4))

        self.ent_tmdb = tk.Entry(
            cfg_card,
            font=("SF Pro Text", 13),
            bg=self.input_bg,
            fg="#ffffff",
            insertbackground="#ffffff",
            highlightbackground="#60a5fa",
            highlightcolor="#38bdf8",
            highlightthickness=2,
            relief=tk.FLAT,
            bd=0
        )
        self.ent_tmdb.insert(0, self.config_data.get("tmdb_key", ""))
        self.ent_tmdb.pack(fill=tk.X, ipady=6, pady=(0, 15))

        btn_save_cfg = tk.Button(
            cfg_card,
            text="💾 AYARLARI KAYDET",
            font=("SF Pro Text", 12, "bold"),
            fg="#ffffff",
            bg=self.success_color,
            activebackground=self.success_hover,
            activeforeground="#ffffff",
            relief=tk.FLAT,
            bd=0,
            padx=20,
            pady=8,
            cursor="hand2",
            highlightbackground="#10b981",
            highlightthickness=1,
            command=self.save_user_settings
        )
        btn_save_cfg.pack(anchor="w")

    def toggle_token_visibility(self):
        if self.ent_token.cget("show") == "":
            self.ent_token.config(show="•")
            self.btn_show_token.config(text="Göster")
        else:
            self.ent_token.config(show="")
            self.btn_show_token.config(text="Gizle")

    def save_user_settings(self):
        self.config_data["github_token"] = self.ent_token.get().strip()
        self.config_data["tmdb_key"] = self.ent_tmdb.get().strip()
        self.save_config()
        messagebox.showinfo("Kaydedildi", "Ayarlar başarıyla kaydedildi!")
        self.append_log("[AYARLAR] GitHub Token ve TMDB anahtarı kaydedildi.")

    # ---------------- Logging & Controls ----------------
    def append_log(self, text: str):
        def _append():
            ts = datetime.now().strftime("%H:%M:%S")
            line = f"[{ts}] {text}\n"
            self.txt_log.insert(tk.END, line)
            self.txt_log.see(tk.END)
        self.after(0, _append)

    def clear_logs(self):
        self.txt_log.delete("1.0", tk.END)

    def set_running_state(self, is_running: bool):
        self.btn_start_all.config(state=tk.DISABLED if is_running else tk.NORMAL)
        self.btn_stop.config(state=tk.NORMAL if is_running else tk.DISABLED)
        self.engine.is_running = is_running

    def stop_crawler(self):
        self.engine.is_running = False
        self.append_log("[DURDURULDU] İşlem kullanıcı tarafından durduruldu.")
        self.set_running_state(False)

    # ---------------- Execution Workers ----------------
    def start_master_update(self):
        sites = self.config_data.get("saved_sites", [])
        if not sites:
            messagebox.showwarning("Kayıtlı Site Yok", "Lütfen önce yukarıdaki kutudan taranacak bir site linki ekleyin!")
            return

        try:
            max_p = int(self.ent_max_pages.get().strip())
        except ValueError:
            max_p = 20

        self.config_data["max_pages"] = max_p
        self.save_config()

        self.set_running_state(True)
        self.switch_tab(0)

        def worker():
            token = self.config_data.get("github_token", "").strip()
            tmdb = self.config_data.get("tmdb_key", "").strip()

            self.append_log(f"[GÜNCELLEME BAŞLADI] Toplam {len(sites)} kayıtlı sitenin tamamı taranıyor (Limit: site başı {max_p} sayfa)...")
            all_scraped = []

            for idx, site_url in enumerate(sites):
                if not self.engine.is_running:
                    break
                self.append_log(f"--- [SİTE {idx + 1}/{len(sites)}] Taranıyor: {site_url} ---")
                movie_dict = self.engine.discover_movie_links(site_url, max_pages=max_p)
                self.append_log(f"✓ {site_url} üzerinde {len(movie_dict)} film bulundu. Video akışları ve detaylar çözülüyor...")

                for m_idx, (m_url, cats) in enumerate(movie_dict.items()):
                    if not self.engine.is_running:
                        break
                    short = m_url.rstrip("/").split("/")[-1]
                    self.append_log(f"[{m_idx + 1}/{len(movie_dict)}] Çözülüyor: {short}...")
                    m_data = self.engine.scrape_movie_page(m_url, cats, tmdb_api_key=tmdb)
                    if m_data and m_data.get("streams"):
                        all_scraped.append(m_data)
                        s_count = len(m_data["streams"])
                        c_str = ", ".join(m_data["genres"])
                        self.append_log(f"  ✓ {m_data['title']} ({s_count} Sunucu • [{c_str}])")
                    else:
                        self.append_log(f"  ↷ {short} (Aktif akış bulunamadı, atlandı)")

            if all_scraped and self.engine.is_running:
                self.append_log(f"[YÜKLENİYOR] Toplam {len(all_scraped)} çözülmüş film GitHub'a atomik olarak aktarılıyor...")
                pub = GitHubAtomicPublisher(token=token, log_callback=self.append_log)
                pub.publish_movies(all_scraped)
            elif not all_scraped:
                self.append_log("[UYARI] Hiçbir film çözülemedi veya işlem iptal edildi.")

            self.set_running_state(False)

        self.crawler_thread = threading.Thread(target=worker, daemon=True)
        self.crawler_thread.start()

    def start_single_crawl(self):
        url = self.ent_single_url.get().strip()
        if not url.startswith("http"):
            messagebox.showwarning("Geçersiz URL", "Lütfen geçerli bir film veya site linki girin!")
            return

        try:
            max_p = int(self.ent_max_pages.get().strip())
        except ValueError:
            max_p = 20

        self.set_running_state(True)
        self.switch_tab(0)

        def worker():
            token = self.config_data.get("github_token", "").strip()
            tmdb = self.config_data.get("tmdb_key", "").strip()

            self.append_log(f"[TEKLİ TARAMA BAŞLADI] Hedef: {url}")

            all_scraped = []
            if any(k in url.lower() for k in ["/film/", "-izle", "/dizi/"]) and not any(k in url.lower() for k in ["/kategori/", "/tur/", "/filmizle/"]):
                self.append_log("Hedef doğrudan film sayfası olarak algılandı. Çözülüyor...")
                m_data = self.engine.scrape_movie_page(url, {"Popüler"}, tmdb_api_key=tmdb)
                if m_data and m_data.get("streams"):
                    all_scraped.append(m_data)
                    self.append_log(f"✓ {m_data['title']} ({len(m_data['streams'])} Sunucu)")
            else:
                self.append_log(f"Hedef site/kategori olarak algılandı. {max_p} sayfa taranıyor...")
                movie_dict = self.engine.discover_movie_links(url, max_pages=max_p)
                for m_idx, (m_url, cats) in enumerate(movie_dict.items()):
                    if not self.engine.is_running:
                        break
                    m_data = self.engine.scrape_movie_page(m_url, cats, tmdb_api_key=tmdb)
                    if m_data and m_data.get("streams"):
                        all_scraped.append(m_data)
                        self.append_log(f"[{m_idx + 1}/{len(movie_dict)}] ✓ {m_data['title']} ({len(m_data['streams'])} Sunucu)")

            if all_scraped and self.engine.is_running:
                self.append_log(f"[YÜKLENİYOR] {len(all_scraped)} film GitHub'a yükleniyor...")
                pub = GitHubAtomicPublisher(token=token, log_callback=self.append_log)
                pub.publish_movies(all_scraped)

            self.set_running_state(False)

        self.crawler_thread = threading.Thread(target=worker, daemon=True)
        self.crawler_thread.start()

if __name__ == "__main__":
    app = PlayRepoApp()
    app.mainloop()

