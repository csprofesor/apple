package com.csprofesor.playrepo

import com.google.gson.Gson
import com.google.gson.JsonObject
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.async
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.delay
import kotlinx.coroutines.withContext
import okhttp3.Cookie
import okhttp3.CookieJar
import okhttp3.HttpUrl
import okhttp3.OkHttpClient
import okhttp3.Request
import org.jsoup.Jsoup
import java.net.URLEncoder
import java.util.Collections
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicInteger
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
        .connectTimeout(5, TimeUnit.SECONDS)
        .readTimeout(6, TimeUnit.SECONDS)
        .callTimeout(8, TimeUnit.SECONDS)
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

    fun detectCategoryFromUrlOrText(url: String, text: String): String? {
        val lower = "${url.lowercase()} ${text.lowercase()}"
        return when {
            lower.contains("korku") || lower.contains("gerilim") || lower.contains("horror") || lower.contains("thriller") -> "Korku & Gerilim"
            lower.contains("aksiyon") || lower.contains("macera") || lower.contains("action") || lower.contains("adventure") -> "Aksiyon & Macera"
            lower.contains("komedi") || lower.contains("mizah") || lower.contains("comedy") -> "Komedi"
            lower.contains("dram") || lower.contains("romantik") || lower.contains("drama") || lower.contains("romance") -> "Dram & Romantik"
            lower.contains("bilim") || lower.contains("kurgu") || lower.contains("fantastik") || lower.contains("sci-fi") || lower.contains("scifi") || lower.contains("fantasy") -> "Bilim Kurgu & Fantastik"
            else -> null
        }
    }

    private suspend fun discoverSiteCategories(siteUrl: String, siteBase: String): Map<String, List<String>> = withContext(Dispatchers.IO) {
        val categoryMap = mutableMapOf<String, MutableList<String>>()
        try {
            val request = Request.Builder()
                .url(siteUrl)
                .header("User-Agent", getRandomUserAgent())
                .header("Accept", "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8")
                .build()

            client.newCall(request).execute().use { response ->
                if (!response.isSuccessful) return@use
                val html = response.body?.string().orEmpty()
                val doc = Jsoup.parse(html, siteUrl)

                doc.select("a[href]").forEach { a ->
                    val href = a.absUrl("href").trim()
                    val text = a.text().trim()
                    if (isValidCatalogPageUrl(href, siteBase) && href.startsWith(siteBase)) {
                        val catName = detectCategoryFromUrlOrText(href, text)
                        if (catName != null) {
                            val list = categoryMap.getOrPut(catName) { mutableListOf() }
                            if (!list.contains(href)) {
                                list.add(href)
                            }
                        }
                    }
                }
            }
        } catch (_: Exception) {}
        categoryMap
    }

    /**
     * Crawls pages of the website to discover valid movie links and associates each movie with its category.
     */
    suspend fun discoverAllMovieLinks(
        siteUrl: String,
        maxPages: Int = 30,
        onPageProgress: suspend (pageNumber: Int, foundMoviesCount: Int, statusMsg: String) -> Unit
    ): Map<String, Set<String>> = withContext(Dispatchers.IO) {
        val siteBase = if (siteUrl.contains("://")) {
            val parts = siteUrl.split("/")
            "${parts[0]}//${parts[2]}"
        } else siteUrl

        val movieCategoriesMap = mutableMapOf<String, MutableSet<String>>()
        val visitedCatalogPages = mutableSetOf<String>()
        val catalogPagesQueue = mutableListOf<String>()
        val pageCategoryMap = mutableMapOf<String, String>()

        // 1. Discover categories directly from the site
        val detectedCategories = discoverSiteCategories(siteUrl, siteBase)
        detectedCategories.forEach { (catName, urls) ->
            urls.take(3).forEach { catUrl ->
                if (!catalogPagesQueue.contains(catUrl)) {
                    catalogPagesQueue.add(catUrl)
                    pageCategoryMap[catUrl] = catName
                }
            }
        }

        // Add homepage and base catalog
        if (!catalogPagesQueue.contains(siteUrl)) {
            catalogPagesQueue.add(0, siteUrl)
            pageCategoryMap[siteUrl] = "Popüler"
        }
        if (siteUrl != siteBase && siteUrl != "$siteBase/" && !catalogPagesQueue.contains("$siteBase/")) {
            catalogPagesQueue.add("$siteBase/")
            pageCategoryMap["$siteBase/"] = "Popüler"
        }

        var pageIndex = 0

        while (catalogPagesQueue.isNotEmpty() && pageIndex < maxPages) {
            val currentPageUrl = catalogPagesQueue.removeAt(0)
            if (visitedCatalogPages.contains(currentPageUrl)) continue
            visitedCatalogPages.add(currentPageUrl)

            pageIndex++
            val pageCat = pageCategoryMap[currentPageUrl] ?: detectCategoryFromUrlOrText(currentPageUrl, "") ?: "Popüler"

            try {
                val request = Request.Builder()
                    .url(currentPageUrl)
                    .header("User-Agent", getRandomUserAgent())
                    .header("Accept", "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8")
                    .header("Accept-Language", "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7")
                    .build()

                client.newCall(request).execute().use { response ->
                    if (!response.isSuccessful) {
                        onPageProgress(pageIndex, movieCategoriesMap.size, "Sayfa $pageIndex es geçildi (${response.code})")
                        return@use
                    }

                    val html = response.body?.string().orEmpty()
                    val doc = Jsoup.parse(html, currentPageUrl)

                    var newMoviesInPage = 0
                    doc.select("a[href]").forEach { a ->
                        val href = a.absUrl("href").trim()
                        if (isValidMovieUrl(href, siteBase)) {
                            val cats = movieCategoriesMap.getOrPut(href) { mutableSetOf() }
                            if (cats.add(pageCat)) {
                                newMoviesInPage++
                            }
                        }
                    }

                    onPageProgress(pageIndex, movieCategoriesMap.size, "[$pageCat] Sayfa $pageIndex taranıyor ($newMoviesInPage yeni film eklendi)")

                    doc.select("a[href]").forEach { a ->
                        val href = a.absUrl("href").trim()
                        if (isValidCatalogPageUrl(href, siteBase) && !visitedCatalogPages.contains(href) && !catalogPagesQueue.contains(href)) {
                            catalogPagesQueue.add(href)
                            val isPagination = href.contains("/page/") || href.contains("page=") || href.contains("sayfa=")
                            val catForPage = if (isPagination) pageCategoryMap[currentPageUrl] else detectCategoryFromUrlOrText(href, a.text().trim())
                            if (catForPage != null) {
                                pageCategoryMap[href] = catForPage
                            }
                        }
                    }
                }
            } catch (e: Exception) {
                onPageProgress(pageIndex, movieCategoriesMap.size, "Sayfa $pageIndex hatası: ${e.localizedMessage ?: "Bağlantı kesildi"}")
            }

            delay(50)
        }

        movieCategoriesMap
    }

    private fun isValidMovieUrl(url: String, siteBase: String): Boolean {
        if (!url.startsWith(siteBase)) return false
        val lower = url.lowercase().trimEnd('/')

        if (lower == siteBase.lowercase() || lower == "${siteBase.lowercase()}/" ||
            lower.contains("/kategori/") || lower.contains("/tur/") ||
            lower.contains("/filmizle/") || lower.contains("/yil/") ||
            lower.contains("/category/") || lower.contains("/genre/") ||
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
               lower.contains("/filmizle/") || lower.contains("/yil/") ||
               lower.contains("/category/") || lower.contains("/genre/") ||
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
        onStatusUpdate("Sitedeki kategoriler ve sayfalar tespit ediliyor...")

        val movieCategoriesMap = discoverAllMovieLinks(
            siteUrl = siteUrl,
            maxPages = maxPages,
            onPageProgress = { pageNum, count, msg ->
                onStatusUpdate("[$pageNum / $maxPages] $msg\nToplam Benzersiz Film: $count")
            }
        )

        if (movieCategoriesMap.isEmpty()) {
            onStatusUpdate("✗ Sitede film bağlantısı bulunamadı! Lütfen adresi kontrol edin.")
            return@withContext emptyList()
        }

        val allMovieUrls = movieCategoriesMap.keys.toList()
        onStatusUpdate("✓ Toplam ${allMovieUrls.size} film tespit edildi! Video oynatıcılar, kategoriler ve detaylar çözülüyor...")

        val results = Collections.synchronizedList(mutableListOf<ScrapedMovie>())
        val processedCounter = AtomicInteger(0)

        // Process concurrently in chunks of 3 for high speed and live real-time visual progress
        allMovieUrls.chunked(3).forEach { chunk ->
            coroutineScope {
                val jobs = chunk.map { url ->
                    async(Dispatchers.IO) {
                        val currentIdx = processedCounter.incrementAndGet()
                        val shortName = url.trimEnd('/').substringAfterLast('/')
                        onStatusUpdate("[$currentIdx / ${allMovieUrls.size}] İnceleniyor: $shortName...")
                        try {
                            val movie = scrapeMoviePage(url, movieCategoriesMap[url], tmdbApiKey)
                            if (movie != null && movie.streams.isNotEmpty()) {
                                results.add(movie)
                                val catSummary = movie.genres.joinToString(", ")
                                onStatusUpdate("[$currentIdx / ${allMovieUrls.size}] ✓ Çözüldü: ${movie.title} (${movie.streams.size} Sunucu) [$catSummary]")
                            } else {
                                onStatusUpdate("[$currentIdx / ${allMovieUrls.size}] ↷ $shortName (Aktif akış yok, atlandı)")
                            }
                        } catch (e: Exception) {
                            onStatusUpdate("[$currentIdx / ${allMovieUrls.size}] ✗ $shortName hatası: ${e.localizedMessage}")
                        }
                    }
                }
                jobs.forEach { it.await() }
            }
        }

        results.toList()
    }

    /**
     * Scrapes a single movie page, unpacks iframe/player embeds into direct playable M3U8/MP4 stream URLs,
     * extracts official movie poster and metadata (via Cinemeta/IMDb or direct webpage scraping).
     */
    suspend fun scrapeMoviePage(
        movieUrl: String,
        initialCategories: Set<String>? = null,
        tmdbApiKey: String? = null
    ): ScrapedMovie? = withContext(Dispatchers.IO) {
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
            val siteBase = if (movieUrl.contains("://")) {
                val parts = movieUrl.split("/")
                "${parts[0]}//${parts[2]}"
            } else movieUrl
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

            // Extract genres / category classification strictly from detail page links and discovery
            val genreSet = mutableSetOf<String>()

            // Extract ONLY from explicit category links within the movie details block
            doc.select("article, .film-bilgileri, #filmbilgileri, .info, .detaylar, .entry-content, .content")
                .select("a[href]").forEach { el ->
                    val href = el.absUrl("href").lowercase()
                    val txt = el.text().trim()
                    if (isValidCatalogPageUrl(href, siteBase) || href.contains("/tur/") || href.contains("/filmizle/") || href.contains("/kategori/") || href.contains("/genre/")) {
                        val cat = detectCategoryFromUrlOrText(href, txt)
                        if (cat != null) {
                            genreSet.add(cat)
                        }
                    }
                }

            // Fallback to initial discovery category only if movie details didn't specify one
            if (genreSet.isEmpty() && !initialCategories.isNullOrEmpty()) {
                genreSet.addAll(initialCategories.filter { it != "Popüler" })
            }
            if (genreSet.isEmpty()) genreSet.add("Popüler")

            // Extract IMDb rating
            val imdbRating = doc.selectFirst(".imdb-puan, .imdb, span.puan, .rating, [itemprop=ratingValue]")?.text()?.trim()
                ?.replace("IMDb:", "", true)?.replace("IMDb", "", true)?.trim()

            // Extract runtime
            val runtime = Regex("""\b(\d{2,3})\s*(?:dk|dakika|min)\b""", RegexOption.IGNORE_CASE).find(html)?.value

            // Extract director
            val director = doc.selectFirst("a[href*='/yonetmen/'], a[href*='/director/'], .yonetmen a, .yonetmen")?.text()?.trim()

            // Extract cast
            val castList = doc.select("a[href*='/oyuncu/'], a[href*='/actor/'], .oyuncular a")
                .map { it.text().trim() }
                .filter { it.isNotBlank() && it.length in 2..30 }
                .take(6)

            val candidateEmbedUrls = mutableListOf<String>()

            fun addCandidate(raw: String?) {
                if (raw.isNullOrBlank()) return
                val trimmed = raw.trim()
                if (trimmed == movieUrl || trimmed == "$movieUrl/") return
                if (trimmed.contains("about:blank") || trimmed.startsWith("javascript:")) return
                if (trimmed.contains("player.hintfilmizle.com") || trimmed.contains("youtube.com") || trimmed.contains("youtu.be")) return
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

                    // A. Parse JSON array
                    try {
                        val arr = org.json.JSONArray(decodedJson)
                        for (i in 0 until arr.length()) {
                            val item = arr.optString(i).trim()
                            if (item.isNotBlank() && item != "about:blank") {
                                val full = if (item.startsWith("//")) "https:$item" else item
                                addCandidate(full)
                            }
                        }
                    } catch (_: Exception) {}

                    // B. Scan via regex for protocol-relative or https URLs
                    val urlRegex = Regex("""(?:https?:)?//[^\s"'\\\]+""")
                    urlRegex.findAll(decodedJson).forEach { urlMatch ->
                        val u = urlMatch.value.trim()
                        if (u.isNotBlank() && u != "about:blank") {
                            val full = if (u.startsWith("//")) "https:$u" else u
                            if (isKnownVideoHost(full) || full.contains(".m3u8") || full.contains(".mp4") || full.contains("/videoembed/") || full.contains("/embed/")) {
                                addCandidate(full)
                            }
                        }
                    }
                } catch (_: Exception) { }
            }

            // 6. Direct Odnoklassniki / Ok.ru / Vidmoly / Mail.ru embeds in raw HTML
            val directEmbedRegex = Regex("""(?:https?:)?//[^\s"'`<>]+(?:odnoklassniki\.ru/videoembed|ok\.ru/videoembed|vkuser\.net|vidmoly\.me/embed|my\.mail\.ru/video/embed|voe\.sx/e/)[^\s"'`<>]*""", RegexOption.IGNORE_CASE)
            directEmbedRegex.findAll(html).forEach { match ->
                val u = match.value.replace("\\/", "/").trim('"', '\'', '`', '\\')
                if (!u.contains(".jpg") && !u.contains(".png") && !u.contains(".css") && !u.contains(".js")) {
                    val full = if (u.startsWith("//")) "https:$u" else u
                    addCandidate(full)
                }
            }

            // 7. Scrape direct .m3u8 / .mp4 URLs in raw HTML/JS code
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
                genres = genreSet.toList(),
                description = resolvedMeta.description,
                year = resolvedMeta.year,
                imdbRating = imdbRating,
                runtime = runtime,
                director = director,
                cast = if (castList.isNotEmpty()) castList else null
            )
        }
    }

    private fun isKnownVideoHost(url: String): Boolean {
        val lower = url.lowercase()
        return lower.contains("vidmoly") || lower.contains("vidhide") || lower.contains("filelions") ||
               lower.contains("streamwish") || lower.contains("voe.sx") || lower.contains("streamtape") ||
               lower.contains("ok.ru") || lower.contains("odnoklassniki.ru") || lower.contains("my.mail.ru") ||
               lower.contains("bitchute") || lower.contains("mixdrop") || lower.contains("dood") ||
               lower.contains("vidsrc") || lower.contains("vixsrc") || lower.contains("videa.hu") ||
               lower.contains("vk.com") || lower.contains("vkvideo.ru") || lower.contains("vkuser.net") ||
               lower.contains("dailymotion.com")
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
    private fun unpackEmbedStreamUrl(rawEmbedUrl: String, referer: String): String {
        val embedUrl = if (rawEmbedUrl.startsWith("//")) "https:$rawEmbedUrl" else rawEmbedUrl
        if (!embedUrl.startsWith("http://") && !embedUrl.startsWith("https://")) return ""
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

                // 5. Ok.ru / Odnoklassniki Direct MP4 or HLS Manifest
                if (lowerEmbed.contains("ok.ru") || lowerEmbed.contains("odnoklassniki.ru") || lowerEmbed.contains("vkuser.net") || html.contains("data-options")) {
                    try {
                        val optMatch = Regex("""data-options=(?:["']|&quot;)(.*?)(?:["']|&quot;)""").find(html)
                        if (optMatch != null) {
                            val optRaw = optMatch.groupValues[1]
                                .replace("&quot;", "\"")
                                .replace("&amp;", "&")
                                .replace("\\/", "/")
                            val rootJson = org.json.JSONObject(optRaw)
                            val flashvars = rootJson.optJSONObject("flashvars")
                            if (flashvars != null) {
                                val metaObj = when (val m = flashvars.opt("metadata")) {
                                    is org.json.JSONObject -> m
                                    is String -> org.json.JSONObject(m)
                                    else -> null
                                }
                                val videos = metaObj?.optJSONArray("videos")
                                if (videos != null && videos.length() > 0) {
                                    var bestUrl = ""
                                    for (i in 0 until videos.length()) {
                                        val vItem = videos.optJSONObject(i)
                                        val vUrl = vItem?.optString("url").orEmpty()
                                        if (vUrl.isNotBlank()) {
                                            bestUrl = vUrl
                                        }
                                    }
                                    if (bestUrl.isNotBlank()) {
                                        return bestUrl.replace("&amp;", "&").replace("\\u0026", "&")
                                    }
                                }
                                val hls = flashvars.optString("hlsManifestUrl")
                                if (hls.isNotBlank()) {
                                    return hls.replace("&amp;", "&").replace("\\u0026", "&")
                                }
                            }
                        }
                    } catch (_: Exception) {}

                    val m3u8Ok = Regex("""https?://[^\s"'<>&]+(?:video\.m3u8|\.m3u8)[^\s"'<>&]*""", RegexOption.IGNORE_CASE).find(html)
                    if (m3u8Ok != null) {
                        return m3u8Ok.value.replace("&amp;", "&").replace("\\u0026", "&")
                    }
                    val okRuMatch = Regex("""hlsManifestUrl(?:\\?["']|&quot;)\s*:\s*(?:\\?["']|&quot;)(https?://[^"'\s&]+)""", RegexOption.IGNORE_CASE).find(html)
                    if (okRuMatch != null) {
                        return okRuMatch.groupValues[1].replace("&amp;", "&").replace("\\u0026", "&")
                    }
                } else {
                    val okRuMatch = Regex("""hlsManifestUrl["']?\s*:\s*["'](https?://[^"']+)["']""", RegexOption.IGNORE_CASE).find(html)
                    if (okRuMatch != null) {
                        return okRuMatch.groupValues[1].replace("\\u0026", "&")
                    }
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
