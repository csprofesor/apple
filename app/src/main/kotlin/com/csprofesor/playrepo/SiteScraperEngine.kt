package com.csprofesor.playrepo

import com.google.gson.Gson
import com.google.gson.JsonObject
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.withContext
import okhttp3.Cookie
import okhttp3.CookieJar
import okhttp3.HttpUrl
import okhttp3.OkHttpClient
import okhttp3.Request
import org.jsoup.Jsoup
import java.net.URLEncoder
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.TimeUnit
import kotlin.random.Random

class SiteScraperEngine {

    private val cookieStore = ConcurrentHashMap<String, List<Cookie>>()

    private val cookieJar = object : CookieJar {
        override fun saveFromResponse(url: HttpUrl, cookies: List<Cookie>) {
            cookieStore[url.host] = cookies
        }

        override fun loadForRequest(url: HttpUrl): List<Cookie> {
            return cookieStore[url.host] ?: emptyList()
        }
    }

    private val client = OkHttpClient.Builder()
        .connectTimeout(15, TimeUnit.SECONDS)
        .readTimeout(15, TimeUnit.SECONDS)
        .followRedirects(true)
        .followSslRedirects(true)
        .cookieJar(cookieJar)
        .build()

    private val gson = Gson()

    private val userAgents = listOf(
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4.1 Safari/605.1.15",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0"
    )

    private fun getRandomUserAgent(): String = userAgents[Random.nextInt(userAgents.size)]

    /**
     * Finds ALL movie page links dynamically across catalog pages.
     */
    suspend fun discoverAllMovieLinks(
        siteUrl: String,
        maxPages: Int = 30,
        onPageProgress: suspend (pageNumber: Int, foundMoviesCount: Int, statusMsg: String) -> Unit
    ): List<String> = withContext(Dispatchers.IO) {
        val siteBase = if (siteUrl.contains("://")) {
            val parts = siteUrl.split("/")
            "${parts[0]}//${parts[2]}"
        } else siteUrl

        val cleanSiteUrl = siteUrl.trimEnd('/')
        val discoveredMovies = mutableSetOf<String>()
        val visitedCatalogPages = mutableSetOf<String>()
        val catalogPagesQueue = mutableListOf<String>()

        catalogPagesQueue.add(siteUrl)
        if (!cleanSiteUrl.endsWith("/film") && !cleanSiteUrl.endsWith("/film-izle") && !cleanSiteUrl.endsWith("/filmler")) {
            catalogPagesQueue.add("$siteBase/film/")
            catalogPagesQueue.add("$siteBase/film-izle/")
        }

        var pageIndex = 0

        while (catalogPagesQueue.isNotEmpty() && pageIndex < maxPages) {
            val currentPageUrl = catalogPagesQueue.removeAt(0)
            if (visitedCatalogPages.contains(currentPageUrl)) continue
            visitedCatalogPages.add(currentPageUrl)

            pageIndex++

            try {
                val request = Request.Builder()
                    .url(currentPageUrl)
                    .header("User-Agent", getRandomUserAgent())
                    .header("Accept", "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8")
                    .header("Accept-Language", "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7")
                    .build()

                client.newCall(request).execute().use { response ->
                    if (!response.isSuccessful) {
                        onPageProgress(pageIndex, discoveredMovies.size, "Sayfa $pageIndex es geçildi (${response.code})")
                        return@use
                    }

                    val html = response.body?.string().orEmpty()
                    val doc = Jsoup.parse(html, currentPageUrl)

                    var newMoviesInPage = 0
                    doc.select("a[href]").forEach { a ->
                        val href = a.absUrl("href").trim()
                        if (isValidMovieUrl(href, siteBase)) {
                            if (discoveredMovies.add(href)) {
                                newMoviesInPage++
                            }
                        }
                    }

                    onPageProgress(pageIndex, discoveredMovies.size, "Sayfa $pageIndex taranıyor ($newMoviesInPage yeni film eklendi)")

                    doc.select("a[href]").forEach { a ->
                        val href = a.absUrl("href").trim()
                        if (isValidCatalogPageUrl(href, siteBase) && !visitedCatalogPages.contains(href) && !catalogPagesQueue.contains(href)) {
                            catalogPagesQueue.add(href)
                        }
                    }
                }
            } catch (e: Exception) {
                onPageProgress(pageIndex, discoveredMovies.size, "Sayfa $pageIndex hatası: ${e.localizedMessage ?: "Bağlantı kesildi"}")
            }

            delay(50)
        }

        discoveredMovies.toList()
    }

    private fun isValidMovieUrl(url: String, siteBase: String): Boolean {
        if (!url.startsWith(siteBase)) return false
        val lower = url.lowercase().trimEnd('/')

        if (lower == siteBase.lowercase() || lower == "${siteBase.lowercase()}/" ||
            lower.contains("/kategori/") || lower.contains("/tur/") ||
            lower.contains("/koleksiyon/") || lower.contains("/forum") ||
            lower.contains("/iletisim") || lower.contains("/page/") ||
            lower.contains("page=") || lower.contains("orderby=") ||
            lower.contains("/tag/") || lower.contains("#") ||
            lower.contains("javascript:") || lower.endsWith(".jpg") ||
            lower.endsWith(".png") || lower.endsWith(".css") || lower.endsWith(".js") ||
            lower.contains("/login") || lower.contains("/uye") || lower.contains("/takvim") ||
            lower.contains("/trendler") || lower.contains("/seri-filmler") || lower.contains("/istek")
        ) {
            return false
        }

        return lower.contains("/film/") || lower.contains("-izle") || lower.contains("/dizi/") || lower.contains("/filmler/")
    }

    private fun isValidCatalogPageUrl(url: String, siteBase: String): Boolean {
        if (!url.startsWith(siteBase)) return false
        val lower = url.lowercase()
        if (isValidMovieUrl(url, siteBase)) return false

        return lower.contains("/page/") || lower.contains("page=") || lower.contains("sayfa=") ||
               lower.contains("/kategori/") || lower.contains("/tur/") ||
               lower == "$siteBase/film/" || lower == "$siteBase/film" ||
               lower == "$siteBase/film-izle/" || lower == "$siteBase/film-izle" ||
               lower == "$siteBase/filmler/" || lower == "$siteBase/filmler" ||
               lower.contains("orderby=")
    }

    /**
     * Crawls all movie links across the whole site and resolves details & stream URLs for each movie.
     */
    suspend fun crawlEntireSite(
        siteUrl: String,
        maxPages: Int = 30,
        tmdbApiKey: String? = null,
        onStatusUpdate: suspend (status: String) -> Unit
    ): List<ScrapedMovie> = withContext(Dispatchers.IO) {
        onStatusUpdate("Sitedeki sayfalar taranıyor (Kategoriler & Sayfalandırma)...")

        val allMovieUrls = discoverAllMovieLinks(
            siteUrl = siteUrl,
            maxPages = maxPages,
            onPageProgress = { pageNum, count, msg ->
                onStatusUpdate("[$pageNum / $maxPages] $msg\nToplam Benzersiz Film: $count")
            }
        )

        if (allMovieUrls.isEmpty()) {
            onStatusUpdate("✗ Sitede film bağlantısı bulunamadı! Lütfen adresi kontrol edin.")
            return@withContext emptyList()
        }

        onStatusUpdate("✓ Toplam ${allMovieUrls.size} film tespit edildi! Video oynatıcılar ve posterler çözülüyor...")

        val results = mutableListOf<ScrapedMovie>()

        allMovieUrls.forEachIndexed { index, url ->
            try {
                val movie = scrapeMoviePage(url, tmdbApiKey)
                if (movie != null && movie.streams.isNotEmpty()) {
                    results.add(movie)
                    onStatusUpdate("[${index + 1} / ${allMovieUrls.size}] ✓ Çözüldü: ${movie.title} (${movie.streams.size} Çalışan Yayın Kaynağı)")
                } else if (movie != null) {
                    onStatusUpdate("[${index + 1} / ${allMovieUrls.size}] - ${movie.title}: Aktif video akışı bulunamadı (Atlandı)")
                }
            } catch (e: Exception) {
                onStatusUpdate("[${index + 1} / ${allMovieUrls.size}] Sayfa hatası: ${e.localizedMessage}")
            }
        }

        results
    }

    /**
     * Scrapes a single movie page, unpacks iframe/player embeds into direct playable M3U8/MP4 stream URLs,
     * extracts official movie poster and metadata (via Cinemeta/IMDb or direct webpage scraping).
     */
    suspend fun scrapeMoviePage(movieUrl: String, tmdbApiKey: String? = null): ScrapedMovie? = withContext(Dispatchers.IO) {
        val request = Request.Builder()
            .url(movieUrl)
            .header("User-Agent", getRandomUserAgent())
            .header("Accept", "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8")
            .header("Referer", movieUrl)
            .build()

        client.newCall(request).execute().use { response ->
            if (!response.isSuccessful) return@withContext null

            val html = response.body?.string().orEmpty()
            val doc = Jsoup.parse(html, movieUrl)

            val rawTitle = doc.selectFirst("h1")?.text()?.trim()
                ?: doc.selectFirst("meta[property=og:title]")?.attr("content")?.trim()
                ?: doc.selectFirst("title")?.text()?.trim()
                ?: movieUrl.substringAfterLast("/").replace("-", " ")

            val cleanTitle = cleanMovieTitle(rawTitle)
            if (cleanTitle.isBlank() || cleanTitle.lowercase() == "filmkovasi.co" || cleanTitle.lowercase() == "www.hintfilmizle.com") {
                return@withContext null
            }

            // Extract real poster from page
            val scrapedPoster = extractScrapedPoster(doc, movieUrl)

            // Extract description / plot
            val scrapedDesc = doc.selectFirst("meta[property=og:description]")?.attr("content")?.trim()
                ?: doc.selectFirst("meta[name=description]")?.attr("content")?.trim()
                ?: doc.selectFirst(".film-ozeti, .entry-content p, .ozet, .story")?.text()?.trim()
                ?: ""

            // Extract release year
            val year = Regex("""\b(19\d{2}|20\d{2})\b""").find(rawTitle)?.value
                ?: Regex("""\b(19\d{2}|20\d{2})\b""").find(html)?.value
                ?: ""

            // Detect language & quality flags
            val isDub = html.contains("dublaj", true) || rawTitle.contains("dublaj", true)
            val langTag = if (isDub) "[TR Dublaj]" else "[TR Altyazılı]"
            val qualityTag = if (html.contains("4k", true) || rawTitle.contains("4k", true)) "4K" else "1080p"

            // Extract genres / category classification
            val genreList = mutableListOf<String>()
            val lowerHtml = html.lowercase()
            if (lowerHtml.contains("korku") || lowerHtml.contains("gerilim")) genreList.add("Korku & Gerilim")
            if (lowerHtml.contains("aksiyon") || lowerHtml.contains("macera")) genreList.add("Aksiyon & Macera")
            if (lowerHtml.contains("komedi") || lowerHtml.contains("mizah")) genreList.add("Komedi")
            if (lowerHtml.contains("dram") || lowerHtml.contains("romantik")) genreList.add("Dram & Romantik")
            if (lowerHtml.contains("bilim") || lowerHtml.contains("kurgu") || lowerHtml.contains("fantastik")) genreList.add("Bilim Kurgu & Fantastik")
            if (genreList.isEmpty()) genreList.add("Popüler")

            val candidateEmbedUrls = mutableListOf<String>()

            fun addCandidate(raw: String?) {
                if (raw.isNullOrBlank()) return
                val trimmed = raw.trim()
                if (trimmed == movieUrl || trimmed == "$movieUrl/") return
                if (trimmed.contains("about:blank") || trimmed.startsWith("javascript:")) return
                if (trimmed.endsWith(".jpg") || trimmed.endsWith(".png") || trimmed.endsWith(".webp") ||
                    trimmed.endsWith(".css") || trimmed.endsWith(".js") || trimmed.endsWith(".svg")) return

                val full = if (trimmed.startsWith("//")) {
                    "https:$trimmed"
                } else if (trimmed.startsWith("http://") || trimmed.startsWith("https://")) {
                    trimmed
                } else {
                    return // Ignore relative paths that are not video embeds
                }

                if (!candidateEmbedUrls.contains(full)) {
                    candidateEmbedUrls.add(full)
                }
            }

            // 1. data-frame (very common in Turkish movie CMS themes like Hintfilmizle, Filmmodu, etc.)
            doc.select("[data-frame], a[data-frame]").forEach { el ->
                addCandidate(el.attr("data-frame"))
            }

            // 2. iframes & video tags
            doc.select("iframe[src], iframe[data-src], iframe[data-litespeed-src], video[src], video source[src]").forEach { el ->
                addCandidate(el.absUrl("src").ifBlank { el.attr("src") })
                addCandidate(el.attr("data-src"))
                addCandidate(el.attr("data-litespeed-src"))
            }

            // 3. data-player / data-embed / data-video / data-url attributes
            doc.select("[data-player], [data-embed], [data-video], [data-url], [data-link], [data-file]").forEach { el ->
                addCandidate(el.attr("data-player"))
                addCandidate(el.attr("data-embed"))
                addCandidate(el.attr("data-video"))
                addCandidate(el.attr("data-url"))
                addCandidate(el.attr("data-link"))
                addCandidate(el.attr("data-file"))
            }

            // 4. External video host links (vidmoly, voe, ok.ru, mail.ru, bitchute, streamtape, mixdrop, etc.)
            doc.select("a[href]").forEach { a ->
                val href = a.absUrl("href").ifBlank { a.attr("href") }
                if (isKnownVideoHost(href)) {
                    addCandidate(href)
                }
            }

            // 5. Scrape Base64 encoded atob("...") player embeds
            val atobRegex = Regex("""atob\s*\(\s*["'`]([A-Za-z0-9+/=]+)["'`]\s*\)""")
            atobRegex.findAll(html).forEach { match ->
                try {
                    val b64 = match.groupValues[1]
                    val decodedBytes = android.util.Base64.decode(b64, android.util.Base64.DEFAULT)
                    val decodedJson = String(decodedBytes, Charsets.UTF_8).replace("\\/", "/")
                    val urlRegex = Regex("""https?://[^\s"'\\]+""")
                    urlRegex.findAll(decodedJson).forEach { urlMatch ->
                        val u = urlMatch.value
                        if (isKnownVideoHost(u) || u.contains(".m3u8") || u.contains(".mp4")) {
                            addCandidate(u)
                        }
                    }
                } catch (_: Exception) { }
            }

            // 6. Scrape direct .m3u8 / .mp4 URLs in raw HTML/JS code
            val m3u8Regex = Regex("""https?://[^\s"'`<>]+?\.(?:m3u8|mp4)[^\s"'`<>]*""", RegexOption.IGNORE_CASE)
            m3u8Regex.findAll(html).forEach { match ->
                addCandidate(match.value)
            }

            val resolvedStreams = mutableListOf<StreamItem>()

            candidateEmbedUrls.forEach { embedUrl ->
                val directStreamUrl = unpackEmbedStreamUrl(embedUrl, movieUrl)
                if (directStreamUrl.isNotBlank() && resolvedStreams.none { it.url == directStreamUrl }) {
                    val lowerUrl = directStreamUrl.lowercase()
                    if (lowerUrl.contains(".m3u8") || lowerUrl.contains(".mp4")) {
                        val isM3u8 = lowerUrl.contains(".m3u8")
                        resolvedStreams.add(
                            StreamItem(
                                name = "Play",
                                title = "Play • Sunucu ${resolvedStreams.size + 1} • $qualityTag • $langTag",
                                url = directStreamUrl,
                                quality = if (qualityTag == "4K") 2160 else 1080,
                                format = if (isM3u8) "m3u8" else "mp4",
                                referer = embedUrl
                            )
                        )
                    }
                }
            }

            if (resolvedStreams.isEmpty()) {
                return@withContext null
            }

            // Resolve official Metadata: Cinemeta (IMDb/Stremio standard) or TMDB or Scraped Page Info
            val resolvedMeta = resolveMovieMetadata(cleanTitle, scrapedPoster, scrapedDesc, year, tmdbApiKey)

            ScrapedMovie(
                tmdbId = resolvedMeta.id,
                title = resolvedMeta.title,
                poster = resolvedMeta.poster,
                streams = resolvedStreams,
                genres = genreList,
                description = resolvedMeta.description,
                year = resolvedMeta.year
            )
        }
    }

    private fun isKnownVideoHost(url: String): Boolean {
        val lower = url.lowercase()
        return lower.contains("vidmoly") || lower.contains("vidhide") || lower.contains("filelions") ||
               lower.contains("streamwish") || lower.contains("voe.sx") || lower.contains("streamtape") ||
               lower.contains("ok.ru") || lower.contains("my.mail.ru") || lower.contains("bitchute") ||
               lower.contains("mixdrop") || lower.contains("dood") || lower.contains("vidsrc") ||
               lower.contains("vixsrc") || lower.contains("videa.hu") || lower.contains("vk.com") ||
               lower.contains("vkvideo.ru")
    }

    private fun extractScrapedPoster(doc: org.jsoup.nodes.Document, siteUrl: String): String {
        val siteBase = if (siteUrl.contains("://")) {
            val parts = siteUrl.split("/")
            "${parts[0]}//${parts[2]}"
        } else siteUrl

        val ogImage = doc.selectFirst("meta[property=og:image]")?.attr("content")?.trim()
        if (!ogImage.isNullOrBlank() && !ogImage.endsWith("/logo.png") && !ogImage.endsWith("/icon.png")) {
            return if (ogImage.startsWith("//")) "https:$ogImage" else ogImage
        }

        val twitterImage = doc.selectFirst("meta[name=twitter:image]")?.attr("content")?.trim()
        if (!twitterImage.isNullOrBlank() && !twitterImage.endsWith("/logo.png")) {
            return if (twitterImage.startsWith("//")) "https:$twitterImage" else twitterImage
        }

        val posterEl = doc.selectFirst(".film-afis img, .poster img, .movie-poster img, .f-poster img")
        if (posterEl != null) {
            val src = posterEl.attr("data-src").ifBlank { posterEl.attr("src") }.trim()
            if (src.isNotBlank() && !src.startsWith("data:") && !src.endsWith("/logo.png")) {
                return if (src.startsWith("//")) "https:$src" else if (src.startsWith("http")) src else "$siteBase/$src".replace("//", "/")
            }
        }

        return ""
    }

    private fun cleanMovieTitle(title: String): String {
        return title
            .replace(Regex("""\s*-\s*Full\s*HD\s*izle.*""", RegexOption.IGNORE_CASE), "")
            .replace(Regex("""\s*-\s*Hintfilmizle.*""", RegexOption.IGNORE_CASE), "")
            .replace(Regex("""\s*\|\s*Hintfilmizle.*""", RegexOption.IGNORE_CASE), "")
            .replace(Regex("""\s*\|\s*Filmkovası.*""", RegexOption.IGNORE_CASE), "")
            .replace(Regex("""\s*Türkçe\s*Dublaj.*""", RegexOption.IGNORE_CASE), "")
            .replace(Regex("""\s*Türkçe\s*Altyazılı.*""", RegexOption.IGNORE_CASE), "")
            .replace(Regex("""\s*Full\s*HD.*""", RegexOption.IGNORE_CASE), "")
            .replace(Regex("""\s*1080p.*""", RegexOption.IGNORE_CASE), "")
            .replace(Regex("""\s*izle.*""", RegexOption.IGNORE_CASE), "")
            .replace(Regex("""\s*İzle.*""", RegexOption.IGNORE_CASE), "")
            .replace(Regex("""\(\s*\d{4}\s*\)"""), "")
            .trim(' ', '-', '–', ':', '|', '(')
    }

    /**
     * Unpacks/decrypts embed players into direct playable M3U8 / MP4 URLs.
     */
    private fun unpackEmbedStreamUrl(embedUrl: String, referer: String): String {
        val lowerEmbed = embedUrl.lowercase()

        // If direct playable stream already, return immediately
        if ((lowerEmbed.contains(".m3u8") || lowerEmbed.contains(".mp4")) && !lowerEmbed.contains("/embed") && !lowerEmbed.contains("/videoembed")) {
            return embedUrl
        }

        // 1. Mail.ru metadata API
        if (lowerEmbed.contains("my.mail.ru/video/embed/") || lowerEmbed.contains("my.mail.ru/+/video/meta/")) {
            val videoId = embedUrl.substringAfterLast("/").substringBefore("?").substringBefore(".html")
            try {
                val metaUrl = "https://my.mail.ru/+/video/meta/$videoId"
                val req = Request.Builder().url(metaUrl).header("User-Agent", getRandomUserAgent()).build()
                client.newCall(req).execute().use { resp ->
                    if (resp.isSuccessful) {
                        val body = resp.body?.string().orEmpty()
                        val jsonObj = gson.fromJson(body, JsonObject::class.java)
                        val videos = jsonObj.getAsJsonArray("videos")
                        if (videos != null && videos.size() > 0) {
                            var bestUrl = ""
                            videos.forEach { v ->
                                val vObj = v.asJsonObject
                                val u = vObj.get("url")?.asString.orEmpty()
                                if (u.isNotBlank()) {
                                    val full = if (u.startsWith("//")) "https:$u" else u
                                    bestUrl = full // Later ones in array are higher resolution (1080p)
                                }
                            }
                            if (bestUrl.isNotBlank()) return bestUrl
                        }
                    }
                }
            } catch (_: Exception) { }
        }

        try {
            val request = Request.Builder()
                .url(embedUrl)
                .header("User-Agent", getRandomUserAgent())
                .header("Referer", referer)
                .build()

            client.newCall(request).execute().use { response ->
                if (!response.isSuccessful) return ""
                val html = response.body?.string().orEmpty()

                // 2. Bitchute direct MP4
                if (lowerEmbed.contains("bitchute.com")) {
                    val bcMatch = Regex("""(https?://[^\s"'<>]+\.bitchute\.com/[^\s"'<>]+\.mp4)""", RegexOption.IGNORE_CASE).find(html)
                    if (bcMatch != null) return bcMatch.groupValues[1]
                }

                // 3. Vidmoly, Vidhide, FileLions, StreamWish, JWPlayer m3u8
                val m3u8Match = Regex("""file\s*:\s*["'](https?://[^"']+\.m3u8[^"']*)["']""", RegexOption.IGNORE_CASE).find(html)
                    ?: Regex("""sources\s*:\s*\[\s*\{\s*file\s*:\s*["'](https?://[^"']+\.m3u8[^"']*)["']""", RegexOption.IGNORE_CASE).find(html)
                    ?: Regex("""src\s*:\s*["'](https?://[^"']+\.m3u8[^"']*)["']""", RegexOption.IGNORE_CASE).find(html)
                    ?: Regex("""["'](https?://[^"']+\.m3u8[^"']*)["']""", RegexOption.IGNORE_CASE).find(html)
                if (m3u8Match != null) {
                    return m3u8Match.groupValues[1]
                }

                // 4. VOE regex
                val voeMatch = Regex("""'hls'\s*:\s*['"](https?://[^'"]+)['"]""", RegexOption.IGNORE_CASE).find(html)
                    ?: Regex("""["']hls["']\s*:\s*["'](https?://[^"']+)["']""", RegexOption.IGNORE_CASE).find(html)
                if (voeMatch != null) {
                    return voeMatch.groupValues[1]
                }

                // 5. Ok.ru HLS Manifest or MP4
                val okRuMatch = Regex("""hlsManifestUrl["']?\s*:\s*["'](https?://[^"']+)["']""", RegexOption.IGNORE_CASE).find(html)
                if (okRuMatch != null) {
                    return okRuMatch.groupValues[1].replace("\\u0026", "&")
                }

                // 6. Mixdrop regex
                val mixdropMatch = Regex("""MDCore\.wurl\s*=\s*["']([^"']+)["']""", RegexOption.IGNORE_CASE).find(html)
                if (mixdropMatch != null) {
                    val streamUrl = mixdropMatch.groupValues[1]
                    return if (streamUrl.startsWith("//")) "https:$streamUrl" else streamUrl
                }

                // 7. Direct MP4 regex
                val mp4Match = Regex("""["'](https?://[^"']+\.mp4[^"']*)["']""", RegexOption.IGNORE_CASE).find(html)
                if (mp4Match != null) {
                    return mp4Match.groupValues[1]
                }
            }
        } catch (_: Exception) { }

        return ""
    }

    private data class MetaResolution(
        val id: String,
        val title: String,
        val poster: String,
        val description: String,
        val year: String
    )

    private fun resolveMovieMetadata(
        cleanTitle: String,
        scrapedPoster: String,
        scrapedDesc: String,
        year: String,
        tmdbApiKey: String?
    ): MetaResolution {
        val fallbackPoster = scrapedPoster.ifBlank { "https://cdn.jsdelivr.net/gh/csprofesor/apple@main/playrepo/logo.png" }
        val slug = cleanTitle.lowercase()
            .replace("ç", "c").replace("ğ", "g").replace("ı", "i")
            .replace("ö", "o").replace("ş", "s").replace("ü", "u")
            .replace(Regex("""[^a-z0-9]+"""), "-")
            .trim('-')
        val fallbackId = "playrepo:$slug"

        // 1. First attempt: Cinemeta (Stremio/Nuvio Universal Open API - NO API KEY REQUIRED)
        try {
            val encodedQuery = URLEncoder.encode(cleanTitle, "UTF-8")
            val cinemetaUrl = "https://v3-cinemeta.strem.io/catalog/movie/top/search=$encodedQuery.json"
            val request = Request.Builder().url(cinemetaUrl).header("User-Agent", getRandomUserAgent()).build()
            client.newCall(request).execute().use { response ->
                if (response.isSuccessful) {
                    val body = response.body?.string().orEmpty()
                    val jsonObj = gson.fromJson(body, JsonObject::class.java)
                    val metas = jsonObj.getAsJsonArray("metas")
                    if (metas != null && metas.size() > 0) {
                        val firstMeta = metas.get(0).asJsonObject
                        val id = firstMeta.get("id")?.asString
                        val name = firstMeta.get("name")?.asString ?: cleanTitle
                        val poster = firstMeta.get("poster")?.asString ?: fallbackPoster
                        val releaseYear = firstMeta.get("releaseInfo")?.asString ?: year
                        val desc = firstMeta.get("description")?.asString ?: scrapedDesc

                        if (!id.isNullOrBlank()) {
                            return MetaResolution(
                                id = id,
                                title = name,
                                poster = if (poster.isNotBlank()) poster else fallbackPoster,
                                description = if (desc.isNotBlank()) desc else "Play • 1080p TR",
                                year = releaseYear
                            )
                        }
                    }
                }
            }
        } catch (_: Exception) { }

        // 2. Second attempt: TMDB if user provided an API key
        if (!tmdbApiKey.isNullOrBlank()) {
            try {
                val encodedQuery = URLEncoder.encode(cleanTitle, "UTF-8")
                val url = "https://api.themoviedb.org/3/search/movie?api_key=$tmdbApiKey&query=$encodedQuery&language=tr-TR"
                val request = Request.Builder().url(url).header("User-Agent", getRandomUserAgent()).build()
                client.newCall(request).execute().use { response ->
                    if (response.isSuccessful) {
                        val body = response.body?.string().orEmpty()
                        val jsonObj = gson.fromJson(body, JsonObject::class.java)
                        val results = jsonObj.getAsJsonArray("results")
                        if (results != null && results.size() > 0) {
                            val firstMovie = results.get(0).asJsonObject
                            val id = firstMovie.get("id")?.asString
                            val officialTitle = firstMovie.get("title")?.asString ?: firstMovie.get("original_title")?.asString ?: cleanTitle
                            val posterPath = firstMovie.get("poster_path")?.asString
                            val posterUrl = if (!posterPath.isNullOrBlank()) "https://image.tmdb.org/t/p/w500$posterPath" else fallbackPoster
                            val overview = firstMovie.get("overview")?.asString ?: scrapedDesc
                            val releaseDate = firstMovie.get("release_date")?.asString?.take(4) ?: year

                            if (!id.isNullOrBlank()) {
                                return MetaResolution(
                                    id = "tmdb:$id",
                                    title = officialTitle,
                                    poster = posterUrl,
                                    description = overview,
                                    year = releaseDate
                                )
                            }
                        }
                    }
                }
            } catch (_: Exception) { }
        }

        // 3. Fallback: Use scraped movie page data (NEVER lose the movie's real poster!)
        return MetaResolution(
            id = fallbackId,
            title = cleanTitle,
            poster = fallbackPoster,
            description = if (scrapedDesc.isNotBlank()) scrapedDesc else "Play • Film Yayın Kaynağı",
            year = year
        )
    }
}
