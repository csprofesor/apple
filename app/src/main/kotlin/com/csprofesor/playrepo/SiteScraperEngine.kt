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
            lower.contains("korku") || lower.contains("gerilim") || lower.contains("horror") || lower.contains("thriller") || lower.contains("slasher") -> "Korku & Gerilim"
            lower.contains("aksiyon") || lower.contains("macera") || lower.contains("action") || lower.contains("adventure") || lower.contains("dövüş") || lower.contains("dovus") || lower.contains("savaş") || lower.contains("savas") || lower.contains("western") || lower.contains("kovboy") -> "Aksiyon & Macera"
            lower.contains("komedi") || lower.contains("mizah") || lower.contains("comedy") || lower.contains("parodi") || lower.contains("kara mizah") || lower.contains("animasyon") || lower.contains("animation") || lower.contains("aile") || lower.contains("family") -> "Komedi"
            lower.contains("dram") || lower.contains("romantik") || lower.contains("drama") || lower.contains("romance") || lower.contains("aşk") || lower.contains("ask") || lower.contains("duygusal") || lower.contains("melodram") || lower.contains("biyografi") || lower.contains("biography") || lower.contains("tarih") || lower.contains("history") -> "Dram & Romantik"
            lower.contains("bilim") || lower.contains("kurgu") || lower.contains("fantastik") || lower.contains("sci-fi") || lower.contains("scifi") || lower.contains("fantasy") || lower.contains("uzay") || lower.contains("süper kahraman") || lower.contains("distopya") || lower.contains("gizem") || lower.contains("mystery") || lower.contains("suç") || lower.contains("suc") || lower.contains("polisiye") || lower.contains("crime") -> "Bilim Kurgu & Fantastik"
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
                        } else if (href.contains("altyaz", true) || text.contains("altyaz", true)) {
                            val list = categoryMap.getOrPut("Altyazılı") { mutableListOf() }
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
        // Otomatik Sitemap ve RSS / Feed Keşfi (Doğrudan indeks çekme)
        val sitemapCandidates = listOf(
            "$siteBase/sitemap.xml",
            "$siteBase/sitemap_index.xml",
            "$siteBase/post-sitemap.xml",
            "$siteBase/film-sitemap.xml",
            "$siteBase/feed",
            "$siteBase/rss"
        )
        var sitemapFoundCount = 0
        for (smUrl in sitemapCandidates) {
            try {
                val smReq = Request.Builder()
                    .url(smUrl)
                    .header("User-Agent", getRandomUserAgent())
                    .header("Accept", "text/xml,application/xml,text/html;q=0.9,*/*;q=0.8")
                    .build()
                client.newCall(smReq).execute().use { resp ->
                    if (resp.isSuccessful) {
                        val body = resp.body?.string().orEmpty()
                        if (body.contains("<urlset", true) || body.contains("<sitemapindex", true) || body.contains("<rss", true)) {
                            onPageProgress(0, movieCategoriesMap.size, "⚡ Sitemap keşfedildi: $smUrl")
                            val locRegex = Regex("""<loc>(https?://[^<]+)</loc>""", RegexOption.IGNORE_CASE)
                            val linkRegex = Regex("""<link>(https?://[^<]+)</link>""", RegexOption.IGNORE_CASE)
                            val allFound = (locRegex.findAll(body) + linkRegex.findAll(body)).map { it.groupValues[1].trim() }
                            for (loc in allFound) {
                                if (isValidMovieUrl(loc, siteBase)) {
                                    val cats = movieCategoriesMap.getOrPut(loc) { mutableSetOf() }
                                    val c = detectCategoryFromUrlOrText(loc, "") ?: "Popüler"
                                    cats.add(c)
                                    sitemapFoundCount++
                                }
                            }
                            if (sitemapFoundCount > 0) {
                                onPageProgress(0, movieCategoriesMap.size, "✓ Sitemap üzerinden $sitemapFoundCount film doğrudan alındı!")
                            }
                        }
                    }
                }
                if (sitemapFoundCount > 0) break
            } catch (_: Exception) {}
        }

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
            lower.contains("/tag/") || lower.contains("/etiket/") || lower.contains("#") ||
            lower.contains("javascript:") || lower.endsWith(".jpg") ||
            lower.endsWith(".png") || lower.endsWith(".css") || lower.endsWith(".js") ||
            lower.endsWith(".svg") || lower.endsWith(".webp") || lower.endsWith(".ico") ||
            lower.contains("/login") || lower.contains("/uye") || lower.contains("/takvim") ||
            lower.contains("/trendler") || lower.contains("/istek") || lower.contains("/dmca") ||
            lower.contains("/gizlilik") || lower.contains("/privacy") || lower.contains("/contact")
        ) {
            return false
        }

        val path = lower.removePrefix(siteBase.lowercase()).trim('/')
        if (path.isBlank()) return false

        val matchesKeyword = lower.contains("/film/") || lower.contains("-izle") || lower.contains("/dizi/") ||
               lower.contains("/filmler/") || lower.contains("/movie/") || lower.contains("/watch/") ||
               lower.contains("/izle/") || lower.contains("/sinema/") || lower.contains("/video/") ||
               lower.contains("/title/") || lower.contains("/bolum/") || lower.contains("/episode/")

        val isHyphenatedSlug = path.contains("-") && !path.contains("/page") && !path.contains("?")

        return matchesKeyword || isHyphenatedSlug
    }

    private fun isValidCatalogPageUrl(url: String, siteBase: String): Boolean {
        if (!url.startsWith(siteBase)) return false
        val lower = url.lowercase()
        if (isValidMovieUrl(url, siteBase)) return false

        return lower.contains("/page/") || lower.contains("page=") || lower.contains("sayfa=") ||
               lower.contains("/kategori/") || lower.contains("/tur/") ||
               lower.contains("/filmizle/") || lower.contains("/yil/") ||
               lower.contains("/category/") || lower.contains("/genre/") ||
               lower.contains("altyazi") || lower.contains("dublaj") ||
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
                ?: doc.selectFirst(".film-ozeti, .entry-content p, .ozet, .story, .film-content, .movie-summary, .summary, .description, #film-ozet, .content-text, [itemprop=description], .post-content, .wp-content p")?.text()?.trim()
                ?: ""

            // Extract release year
            val year = Regex("""\b(19\d{2}|20\d{2})\b""").find(rawTitle)?.value
                ?: Regex("""\b(19\d{2}|20\d{2})\b""").find(html)?.value
                ?: ""

            // Detect language & quality flags
            val pageDefaultLang = when {
                rawTitle.contains("altyaz", true) || movieUrl.contains("altyaz", true) -> "[TR Altyazılı]"
                rawTitle.contains("dublaj", true) || movieUrl.contains("dublaj", true) -> "[TR Dublaj]"
                doc.selectFirst("h1, .film-title, .title")?.text()?.contains("altyaz", true) == true -> "[TR Altyazılı]"
                else -> "[TR Dublaj]"
            }
            val qualityTag = if (html.contains("4k", true) || rawTitle.contains("4k", true)) "4K" else "1080p"

            fun detectLangFromContext(vararg texts: String?): String {
                val combined = texts.filterNotNull().joinToString(" ").lowercase()
                return when {
                    combined.contains("altyaz") || combined.contains("sub") || combined.contains("orijinal") || combined.contains("original") || combined.contains("ingilizce") -> "[TR Altyazılı]"
                    combined.contains("dublaj") || combined.contains("turkce") || combined.contains("türkçe") -> "[TR Dublaj]"
                    else -> pageDefaultLang
                }
            }

            // Extract genres / category classification strictly from detail page links and discovery
            val genreSet = mutableSetOf<String>()

            // Extract ONLY from explicit category links within the movie details block
            doc.select("article, .film-bilgileri, #filmbilgileri, .info, .detaylar, .entry-content, .content, .genres, .kategori")
                .select("a[href]").forEach { el ->
                    val href = el.absUrl("href").lowercase()
                    val txt = el.text().trim()
                    if (isValidCatalogPageUrl(href, siteBase) || href.contains("/tur/") || href.contains("/filmizle/") || href.contains("/kategori/") || href.contains("/genre/") || href.contains("/filmler/")) {
                        val cat = detectCategoryFromUrlOrText(href, txt)
                        if (cat != null) {
                            genreSet.add(cat)
                        }
                    }
                }

            // Metin bloklarındaki "Tür: Aksiyon, Dram" ifadelerini tara
            Regex("""(?:Tür(?:ü|ler)?|Kategori|Genres?)\s*[:：]\s*([^<\n\r]+)""", RegexOption.IGNORE_CASE).findAll(html).forEach { m ->
                val line = m.groupValues[1]
                val cat = detectCategoryFromUrlOrText("", line)
                if (cat != null) {
                    genreSet.add(cat)
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

            data class CandidateEmbed(
                val url: String,
                val langTag: String
            )

            val candidateEmbeds = mutableListOf<CandidateEmbed>()

            fun addCandidate(raw: String?, langOverride: String? = null) {
                if (raw.isNullOrBlank()) return
                val trimmed = raw.trim()
                if (trimmed == movieUrl || trimmed == "$movieUrl/") return
                if (trimmed.contains("about:blank") || trimmed.startsWith("javascript:")) return
                if (trimmed.contains("youtube.com") || trimmed.contains("youtu.be")) return
                if (trimmed.endsWith(".jpg") || trimmed.endsWith(".png") || trimmed.endsWith(".webp") ||
                    trimmed.endsWith(".css") || trimmed.endsWith(".js") || trimmed.endsWith(".svg")) return

                val full = if (trimmed.startsWith("//")) {
                    "https:$trimmed"
                } else if (trimmed.startsWith("http://") || trimmed.startsWith("https://")) {
                    trimmed
                } else if (trimmed.startsWith("/")) {
                    "$siteBase$trimmed"
                } else {
                    return
                }

                val finalLang = langOverride ?: detectLangFromContext(full)
                if (candidateEmbeds.none { it.url == full }) {
                    candidateEmbeds.add(CandidateEmbed(full, finalLang))
                }
            }

            // 1. data-frame (very common in Turkish movie CMS themes like Hintfilmizle, Filmmodu, etc.)
            doc.select("[data-frame], a[data-frame], li[data-frame], button[data-frame]").forEach { el ->
                val lang = detectLangFromContext(el.text(), el.attr("data-lang"), el.attr("data-audio"), el.attr("title"), el.parent()?.text(), el.attr("data-frame"))
                addCandidate(el.attr("data-frame"), lang)
            }

            // 2. iframes & video tags
            doc.select("iframe[src], iframe[data-src], iframe[data-litespeed-src], video[src], video source[src]").forEach { el ->
                val lang = detectLangFromContext(el.attr("title"), el.attr("class"), el.parent()?.text())
                addCandidate(el.absUrl("src").ifBlank { el.attr("src") }, lang)
                addCandidate(el.attr("data-src"), lang)
                addCandidate(el.attr("data-litespeed-src"), lang)
            }

            // 3. data-* attributes used by streaming CMS themes
            doc.select("[data-player], [data-embed], [data-video], [data-url], [data-link], [data-file], [data-src], [data-source], [data-href], [data-target], [data-content], [data-iframe], [data-stream], [data-video-url], [data-hls], [data-mp4]").forEach { el ->
                val lang = detectLangFromContext(el.text(), el.attr("data-lang"), el.attr("data-dil"), el.attr("data-audio"), el.attr("title"), el.parent()?.text())
                listOf("data-player", "data-embed", "data-video", "data-url", "data-link", "data-file", "data-src", "data-source", "data-href", "data-target", "data-content", "data-iframe", "data-stream", "data-video-url", "data-hls", "data-mp4").forEach { attr ->
                    val v = el.attr(attr)
                    if (v.isNotBlank()) addCandidate(v, lang)
                }
            }

            // 4. Server Tab Selectors (.keremiya_part, .part-list, .sources, .nav-tabs, .tab-content, .dil-secenekleri, etc.)
            doc.select(".keremiya_part a, .part-list li, .part-list a, .sources-list li, .sources li, .server-item, .tab-content iframe, #parts a, .alternatifler a, ul.nav-tabs li a, div.partlar a, .servers a, .filmi-izle a, .dil-secenekleri a, .languages a").forEach { el ->
                val lang = detectLangFromContext(el.text(), el.attr("data-lang"), el.attr("data-dil"), el.attr("data-audio"), el.attr("title"), el.parent()?.text(), el.absUrl("href"))
                addCandidate(el.attr("data-frame"), lang)
                addCandidate(el.attr("data-src"), lang)
                addCandidate(el.attr("data-url"), lang)
                addCandidate(el.attr("data-embed"), lang)
                val href = el.absUrl("href")
                if (isKnownVideoHost(href) || isInternalPlayerUrl(href, siteBase)) {
                    addCandidate(href, lang)
                }
            }

            // 4b. Inline onclick="loadSource('...')" buttons (common in Filmdozu etc.)
            val loadSourceRegex = Regex("""(?:loadSource|loadPlayer|changeSource|setSource|playVideo|loadEmbed)\s*\(\s*["']([^"']+)["']""", RegexOption.IGNORE_CASE)
            loadSourceRegex.findAll(html).forEach { m ->
                val u = m.groupValues[1].replace("&amp;", "&").trim()
                val lang = detectLangFromContext(u)
                addCandidate(u, lang)
            }

            // 5. External video host links
            doc.select("a[href]").forEach { a ->
                val href = a.absUrl("href").ifBlank { a.attr("href") }
                if (isKnownVideoHost(href)) {
                    val lang = detectLangFromContext(a.text(), a.attr("title"), href)
                    addCandidate(href, lang)
                }
            }

            // 6. Base64 encoded atob("...") player embeds
            val atobRegex = Regex("""atob\s*\(\s*["'`]([A-Za-z0-9+/=]+)["'`]\s*\)""")
            atobRegex.findAll(html).forEach { match ->
                try {
                    val b64 = match.groupValues[1]
                    val decodedBytes = android.util.Base64.decode(b64, android.util.Base64.DEFAULT)
                    val decodedJson = String(decodedBytes, Charsets.UTF_8).replace("\\/", "/")

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

            // 7. Direct Odnoklassniki / Ok.ru / Vidmoly / Mail.ru / VOE / Streamtape embeds in raw HTML
            val directEmbedRegex = Regex("""(?:https?:)?//[^\s"'`<>]+(?:odnoklassniki\.ru/videoembed|ok\.ru/videoembed|vkuser\.net|vidmoly\.[a-z]+/embed|vidmoly\.[a-z]+/w|my\.mail\.ru/video/embed|voe\.sx/e/|streamtape\.[a-z]+/e/|dood\.[a-z]+/e/|ds2play\.[a-z]+/e/|video\.sibnet\.ru/shell\.php)[^\s"'`<>]*""", RegexOption.IGNORE_CASE)
            directEmbedRegex.findAll(html).forEach { match ->
                val u = match.value.replace("\\/", "/").trim('"', '\'', '`', '\\')
                if (!u.contains(".jpg") && !u.contains(".png") && !u.contains(".css") && !u.contains(".js")) {
                    val full = if (u.startsWith("//")) "https:$u" else u
                    addCandidate(full)
                }
            }

            // 8. Direct .m3u8 / .mp4 URLs in raw HTML/JS code
            val m3u8Regex = Regex("""https?://[^\s"'`<>]+?\.(?:m3u8|mp4)[^\s"'`<>]*""", RegexOption.IGNORE_CASE)
            m3u8Regex.findAll(html).forEach { match ->
                addCandidate(match.value)
            }

            // 8b. Scan inline script tags for embedded player URLs or iframe HTML
            doc.select("script:not([src])").forEach { scriptEl ->
                val sHtml = scriptEl.data()
                if (sHtml.contains("http") || sHtml.contains("//")) {
                    val iframeInJs = Regex("""<iframe[^>]+src=["']([^"']+)["']""", RegexOption.IGNORE_CASE)
                    iframeInJs.findAll(sHtml).forEach { m ->
                        addCandidate(m.groupValues[1])
                    }
                    val playerVarRegex = Regex("""(?:playerUrl|embedUrl|videoUrl|iframeUrl|streamUrl|fileUrl|src|source)\s*=\s*["']([^"']+)["']""", RegexOption.IGNORE_CASE)
                    playerVarRegex.findAll(sHtml).forEach { m ->
                        addCandidate(m.groupValues[1])
                    }
                }
            }

            // 9. Inspect internal player iframes to discover nested video hosts
            val internalPlayers = candidateEmbeds.map { it.url }.filter { isInternalPlayerUrl(it, siteBase) }
            internalPlayers.forEach { playerUrl ->
                try {
                    val pReq = Request.Builder()
                        .url(playerUrl)
                        .header("User-Agent", getRandomUserAgent())
                        .header("Referer", movieUrl)
                        .build()
                    client.newCall(pReq).execute().use { pResp ->
                        if (pResp.isSuccessful) {
                            val pHtml = pResp.body?.string().orEmpty()
                            val pDoc = Jsoup.parse(pHtml, playerUrl)
                            pDoc.select("iframe[src], iframe[data-src], a[href], [data-frame], [data-src]").forEach { el ->
                                val src = el.absUrl("src").ifBlank { el.attr("src") }.ifBlank { el.attr("data-frame") }.ifBlank { el.attr("data-src") }
                                if (isKnownVideoHost(src) || src.contains(".m3u8") || src.contains(".mp4")) {
                                    val lang = detectLangFromContext(el.text(), el.attr("data-lang"), playerUrl, src)
                                    addCandidate(src, lang)
                                }
                            }
                            directEmbedRegex.findAll(pHtml).forEach { m ->
                                addCandidate(m.value.replace("\\/", "/").trim('"', '\'', '`', '\\'))
                            }
                            m3u8Regex.findAll(pHtml).forEach { m ->
                                addCandidate(m.value)
                            }
                        }
                    }
                } catch (_: Exception) {}
            }

            // 9b. DooPlay / Toroplay / WP-Player AJAX simulation for dynamic source tabs
            doc.select("[data-post][data-nume], .playex[data-post], .dooplay_player_option[data-post]").forEach { el ->
                val postId = el.attr("data-post")
                val nume = el.attr("data-nume")
                val type = el.attr("data-type").ifBlank { "movie" }
                if (postId.isNotBlank() && nume.isNotBlank()) {
                    try {
                        val ajaxUrl = "$siteBase/wp-admin/admin-ajax.php"
                        val formBody = okhttp3.FormBody.Builder()
                            .add("action", "doo_player_ajax")
                            .add("post", postId)
                            .add("nume", nume)
                            .add("type", type)
                            .build()
                        val aReq = Request.Builder()
                            .url(ajaxUrl)
                            .post(formBody)
                            .header("User-Agent", getRandomUserAgent())
                            .header("Referer", movieUrl)
                            .header("X-Requested-With", "XMLHttpRequest")
                            .build()
                        client.newCall(aReq).execute().use { aResp ->
                            if (aResp.isSuccessful) {
                                val aHtml = aResp.body?.string().orEmpty()
                                val ifMatch = Regex("""<iframe[^>]+src=["']([^"']+)["']""", RegexOption.IGNORE_CASE).find(aHtml)
                                if (ifMatch != null) {
                                    val lang = detectLangFromContext(el.text(), el.attr("data-lang"), ifMatch.groupValues[1])
                                    addCandidate(ifMatch.groupValues[1], lang)
                                }
                            }
                        }
                    } catch (_: Exception) {}
                }
            }

            // 10. Scan and fetch alternate language pages (e.g. Dublaj page links to Altyazılı, or vice versa)
            val alternatePageLinks = mutableListOf<Pair<String, String>>()
            doc.select("a[href]").forEach { a ->
                val href = a.absUrl("href").trim()
                val txt = a.text().trim()
                if (href.startsWith(siteBase) && href != movieUrl && href != "$movieUrl/") {
                    val linkLang = when {
                        txt.contains("altyaz", true) || href.contains("altyaz", true) -> "[TR Altyazılı]"
                        txt.contains("dublaj", true) || href.contains("dublaj", true) -> "[TR Dublaj]"
                        else -> null
                    }
                    if (linkLang != null && linkLang != pageDefaultLang) {
                        val baseSlug = movieUrl.trimEnd('/').substringAfterLast('/')
                            .replace("-turkce-dublaj", "").replace("-turkce-altyazili", "")
                            .replace("-dublaj", "").replace("-altyazili", "").replace("-izle", "")
                        if (href.contains(baseSlug) || txt.contains("altyaz", true) || txt.contains("dublaj", true)) {
                            if (alternatePageLinks.none { it.first == href }) {
                                alternatePageLinks.add(href to linkLang)
                            }
                        }
                    }
                }
            }

            alternatePageLinks.take(2).forEach { (altUrl, altLang) ->
                try {
                    val altReq = Request.Builder()
                        .url(altUrl)
                        .header("User-Agent", getRandomUserAgent())
                        .header("Referer", movieUrl)
                        .build()
                    client.newCall(altReq).execute().use { altResp ->
                        if (altResp.isSuccessful) {
                            val altHtml = altResp.body?.string().orEmpty()
                            val altDoc = Jsoup.parse(altHtml, altUrl)
                            altDoc.select("[data-frame], a[data-frame], li[data-frame], button[data-frame]").forEach { el ->
                                addCandidate(el.attr("data-frame"), altLang)
                            }
                            altDoc.select("iframe[src], iframe[data-src], iframe[data-litespeed-src]").forEach { el ->
                                val s = el.absUrl("src").ifBlank { el.attr("src") }.ifBlank { el.attr("data-src") }
                                addCandidate(s, altLang)
                            }
                            altDoc.select("[data-player], [data-embed], [data-video], [data-url]").forEach { el ->
                                addCandidate(el.attr("data-player").ifBlank { el.attr("data-embed") }.ifBlank { el.attr("data-video") }.ifBlank { el.attr("data-url") }, altLang)
                            }
                            directEmbedRegex.findAll(altHtml).forEach { m ->
                                addCandidate(m.value.replace("\\/", "/").trim('"', '\'', '`', '\\'), altLang)
                            }
                            m3u8Regex.findAll(altHtml).forEach { m ->
                                addCandidate(m.value, altLang)
                            }
                        }
                    }
                } catch (_: Exception) {}
            }

            // Extract external subtitles (.vtt / .srt)
            val extractedSubtitles = mutableListOf<SubtitleItem>()
            doc.select("track[kind=subtitles], track[kind=captions]").forEach { tr ->
                val src = tr.absUrl("src").ifBlank { tr.attr("src") }
                val srclang = tr.attr("srclang").ifBlank { tr.attr("label") }.ifBlank { "Turkish" }
                if (src.isNotBlank() && (src.contains(".vtt") || src.contains(".srt"))) {
                    extractedSubtitles.add(SubtitleItem(id = srclang.lowercase(), url = src, lang = srclang))
                }
            }
            val vttRegex = Regex("""https?://[^\s"'`<>]+?\.(?:vtt|srt)[^\s"'`<>]*""", RegexOption.IGNORE_CASE)
            vttRegex.findAll(html).forEach { m ->
                val u = m.value
                if (extractedSubtitles.none { it.url == u }) {
                    extractedSubtitles.add(SubtitleItem(id = "tr", url = u, lang = "Turkish"))
                }
            }

            // Detect Series / Season / Episode
            val isSeries = movieUrl.contains("/dizi/") || movieUrl.contains("/series/") ||
                           (html.contains("sezon", true) && html.contains("bölüm", true))
            val seasonNum = Regex("""(?:sezon|season)[-\s_]*(\d+)""", RegexOption.IGNORE_CASE).find(movieUrl)?.groupValues?.get(1)?.toIntOrNull()
                ?: Regex("""(\d+)\s*\.\s*(?:sezon|season)""", RegexOption.IGNORE_CASE).find(rawTitle)?.groupValues?.get(1)?.toIntOrNull()
            val episodeNum = Regex("""(?:bolum|bölüm|episode)[-\s_]*(\d+)""", RegexOption.IGNORE_CASE).find(movieUrl)?.groupValues?.get(1)?.toIntOrNull()
                ?: Regex("""(\d+)\s*\.\s*(?:bolum|bölüm|episode)""", RegexOption.IGNORE_CASE).find(rawTitle)?.groupValues?.get(1)?.toIntOrNull()

            val resolvedStreams = mutableListOf<StreamItem>()

            candidateEmbeds.forEach { cand ->
                val directStreamUrl = unpackEmbedStreamUrl(cand.url, movieUrl)
                if (directStreamUrl.isNotBlank() && resolvedStreams.none { it.url == directStreamUrl }) {
                    val lowerUrl = directStreamUrl.lowercase()
                    if (lowerUrl.contains(".m3u8") || lowerUrl.contains(".mp4") || lowerUrl.contains("/manifest")) {
                        val isM3u8 = lowerUrl.contains(".m3u8")
                        val ua = getRandomUserAgent()
                        val ref = cand.url.ifBlank { directStreamUrl }
                        resolvedStreams.add(
                            StreamItem(
                                name = "Play",
                                title = "Play • $qualityTag • ${cand.langTag}",
                                url = directStreamUrl,
                                quality = if (qualityTag == "4K") 2160 else 1080,
                                format = if (isM3u8) "m3u8" else "mp4",
                                referer = ref,
                                headers = mapOf(
                                    "User-Agent" to ua,
                                    "Referer" to ref
                                ),
                                subtitles = if (extractedSubtitles.isNotEmpty()) extractedSubtitles else null
                            )
                        )
                    }
                }
            }

            if (resolvedStreams.isEmpty()) {
                return@withContext null
            }

            // Stream health check: filter dead links
            val aliveStreams = resolvedStreams.filter { st -> isStreamAlive(st.url, st.referer) }
            val validStreams = if (aliveStreams.isNotEmpty()) aliveStreams else resolvedStreams

            // Prioritize instant-start HLS (.m3u8) streams first so playback starts in <1 second
            val sortedStreams = validStreams.sortedWith(
                compareByDescending<StreamItem> { it.format == "m3u8" || it.url.contains(".m3u8") }
                    .thenByDescending { it.quality }
            )

            val dubCount = java.util.concurrent.atomic.AtomicInteger(0)
            val subCount = java.util.concurrent.atomic.AtomicInteger(0)
            val otherCount = java.util.concurrent.atomic.AtomicInteger(0)

            val prioritizedStreams = sortedStreams.map { st ->
                val isDub = st.title.contains("Dublaj")
                val isSub = st.title.contains("Altyaz")
                val label = when {
                    isDub -> "Dublaj ${dubCount.incrementAndGet()}"
                    isSub -> "Altyazı ${subCount.incrementAndGet()}"
                    else -> "Sunucu ${otherCount.incrementAndGet()}"
                }
                val parts = st.title.split("•")
                val suffix = if (parts.size > 2) parts.drop(2).joinToString(" • ").trim() else if (isDub) "$qualityTag • [TR Dublaj]" else "$qualityTag • [TR Altyazılı]"
                st.copy(title = "Play • $label • $suffix")
            }

            // Resolve official Metadata: Cinemeta (IMDb/Stremio standard) or TMDB or Scraped Page Info
            val resolvedMeta = resolveMovieMetadata(cleanTitle, scrapedPoster, scrapedDesc, year, tmdbApiKey)

            ScrapedMovie(
                tmdbId = resolvedMeta.id,
                title = resolvedMeta.title,
                poster = resolvedMeta.poster,
                streams = prioritizedStreams,
                genres = genreSet.toList(),
                description = resolvedMeta.description,
                year = resolvedMeta.year,
                imdbRating = imdbRating,
                runtime = runtime,
                director = director,
                cast = if (castList.isNotEmpty()) castList else null,
                type = if (isSeries) "series" else "movie",
                season = seasonNum,
                episode = episodeNum
            )
        }
    }

    private fun isStreamAlive(url: String, referer: String? = null): Boolean {
        return try {
            val req = Request.Builder()
                .url(url)
                .header("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")
                .apply { if (!referer.isNullOrBlank()) header("Referer", referer) }
                .header("Range", "bytes=0-1024")
                .head()
                .build()

            client.newBuilder()
                .connectTimeout(2500, TimeUnit.MILLISECONDS)
                .readTimeout(2500, TimeUnit.MILLISECONDS)
                .build()
                .newCall(req)
                .execute()
                .use { resp ->
                    resp.code in 200..399 || resp.code == 416
                }
        } catch (_: Exception) {
            true // On timeout/refused HEAD, preserve stream conservatively
        }
    }

    private fun isInternalPlayerUrl(url: String, siteBase: String): Boolean {
        val lower = url.lowercase()
        val isSameHost = url.startsWith(siteBase) || (siteBase.contains("://") && url.contains(siteBase.substringAfter("://").substringBefore("/")))
        return (isSameHost && (lower.contains("/player") || lower.contains("/embed") || lower.contains("/video/") || lower.contains("play.php") || lower.contains("part=") || lower.contains("/v/") || lower.contains("/e/") || lower.contains("watch") || lower.contains("stream"))) ||
               lower.contains("player") || lower.contains("embed") || lower.contains("fplayer") || lower.contains("rapidrame") || lower.contains("trplayer")
    }

    private fun isKnownVideoHost(url: String): Boolean {
        val lower = url.lowercase()
        return lower.contains("vidmoly") || lower.contains("vidhide") || lower.contains("filelions") ||
               lower.contains("streamwish") || lower.contains("voe.sx") || lower.contains("voe-unblock") || lower.contains("voe.to") || lower.contains("voe.") ||
               lower.contains("streamtape") || lower.contains("streamta.pe") || lower.contains("dood") || lower.contains("ds2play") ||
               lower.contains("ok.ru") || lower.contains("odnoklassniki") || lower.contains("vkuser.net") || lower.contains("my.mail.ru") ||
               lower.contains("bitchute") || lower.contains("mixdrop") || lower.contains("sibnet.ru") ||
               lower.contains("vidsrc") || lower.contains("vixsrc") || lower.contains("vixcloud") || lower.contains("videa.hu") ||
               lower.contains("vk.com") || lower.contains("vkvideo.ru") || lower.contains("dailymotion.com") ||
               lower.contains("dropload") || lower.contains("vtube") || lower.contains("streamruby") || lower.contains("supervideo") ||
               lower.contains("turbovid") || lower.contains("playm4u") || lower.contains("rapid-cloud") || lower.contains("megacloud") ||
               lower.contains("rabbitstream") || lower.contains("closeload") || lower.contains("streamlare") || lower.contains("streamhub") ||
               lower.contains("upstream") || lower.contains("hexupload") || lower.contains("embed") || lower.contains("player") ||
               lower.contains("/e/") || lower.contains("/v/") || lower.contains(".m3u8") || lower.contains(".mp4")
    }

    private object JsPackerUnpacker {
        private val PACKER_REGEX = Regex(
            """eval\s*\(\s*function\s*\(\s*p\s*,\s*a\s*,\s*c\s*,\s*k\s*,\s*e\s*,\s*d?\s*\)\s*\{.*?\}\s*\(\s*['"](.*?)['"]\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*['"](.*?)['"]\.split\(\s*['"]\|['"]\s*\)""",
            RegexOption.DOT_MATCHES_ALL
        )

        private const val ALPHABET = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"

        private fun decodeBaseN(str: String, radix: Int): Int {
            var result = 0
            for (ch in str) {
                val idx = ALPHABET.indexOf(ch)
                if (idx < 0 || idx >= radix) return -1
                result = result * radix + idx
            }
            return result
        }

        fun unpack(packedJs: String): String {
            val match = PACKER_REGEX.find(packedJs) ?: return packedJs
            val payload = match.groupValues[1]
            val radix = match.groupValues[2].toIntOrNull() ?: return packedJs
            val keywords = match.groupValues[4].split("|")

            val wordRegex = Regex("""\b\w+\b""")
            return wordRegex.replace(payload) { m ->
                val word = m.value
                val idx = decodeBaseN(word, radix)
                if (idx in keywords.indices && keywords[idx].isNotBlank()) {
                    keywords[idx]
                } else {
                    word
                }
            }.replace("\\/", "/")
        }
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
     * Unpacks/decrypts embed players into direct playable M3U8 / MP4 URLs across ALL providers.
     */
    private fun unpackEmbedStreamUrl(rawEmbedUrl: String, referer: String, depth: Int = 0): String {
        val embedUrl = if (rawEmbedUrl.startsWith("//")) "https:$rawEmbedUrl" else rawEmbedUrl
        if (!embedUrl.startsWith("http://") && !embedUrl.startsWith("https://")) return ""
        val lowerEmbed = embedUrl.lowercase()

        // Direct stream already
        if ((lowerEmbed.contains(".m3u8") || lowerEmbed.contains(".mp4")) && !lowerEmbed.contains("/embed") && !lowerEmbed.contains("/videoembed") && !lowerEmbed.contains("/e/")) {
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
                            for (i in 0 until videos.size()) {
                                val vObj = videos[i].asJsonObject
                                val key = vObj.get("key")?.asString.orEmpty()
                                val u = vObj.get("url")?.asString.orEmpty()
                                if (u.isNotBlank()) {
                                    val full = if (u.startsWith("//")) "https:$u" else u
                                    if (key == "1080p" || key == "720p" || bestUrl.isBlank()) {
                                        bestUrl = full
                                        if (key == "1080p") break
                                    }
                                }
                            }
                            if (bestUrl.isNotBlank()) return bestUrl
                        }
                    }
                }
            } catch (_: Exception) { }
        }

        // 2. Doodstream / ds2play API
        if (lowerEmbed.contains("dood.") || lowerEmbed.contains("ds2play.")) {
            try {
                val req = Request.Builder().url(embedUrl).header("User-Agent", getRandomUserAgent()).header("Referer", referer).build()
                client.newCall(req).execute().use { resp ->
                    if (resp.isSuccessful) {
                        val html = resp.body?.string().orEmpty()
                        val passMatch = Regex("""/pass_md5/([^"'\s]+)""").find(html)
                        if (passMatch != null) {
                            val host = try { java.net.URI(embedUrl).host ?: "" } catch (_: Exception) { "" }
                            val passUrl = "https://$host/pass_md5/" + passMatch.groupValues[1]
                            val passReq = Request.Builder().url(passUrl).header("User-Agent", getRandomUserAgent()).header("Referer", embedUrl).build()
                            client.newCall(passReq).execute().use { pResp ->
                                if (pResp.isSuccessful) {
                                    val prefix = pResp.body?.string().orEmpty().trim()
                                    if (prefix.startsWith("http")) {
                                        val direct = prefix + "1234567890?token=" + passMatch.groupValues[1].substringAfterLast("/")
                                        return direct
                                    }
                                }
                            }
                        }
                    }
                }
            } catch (_: Exception) {}
        }

        try {
            val request = Request.Builder()
                .url(embedUrl)
                .header("User-Agent", getRandomUserAgent())
                .header("Referer", referer)
                .build()

            client.newCall(request).execute().use { response ->
                if (!response.isSuccessful) return ""
                var html = response.body?.string().orEmpty()

                // Check Dean Edwards packed JS and unpack it immediately!
                if (html.contains("eval(function(p,a,c,k,e,d)")) {
                    val unpacked = JsPackerUnpacker.unpack(html)
                    html = "$html\n$unpacked"
                }

                // 3. Vidmoly (m3u8 master playlist)
                if (lowerEmbed.contains("vidmoly")) {
                    val vmMatch = Regex("""["'](https?://[^"']+\.m3u8[^"']*)["']""").find(html)
                    if (vmMatch != null) return vmMatch.groupValues[1]
                }

                // 4. VOE (HLS)
                if (lowerEmbed.contains("voe")) {
                    val voeMatch = Regex("""'hls'\s*:\s*['"](https?://[^'"]+)['"]""", RegexOption.IGNORE_CASE).find(html)
                        ?: Regex("""["']hls["']\s*:\s*["'](https?://[^"']+)["']""", RegexOption.IGNORE_CASE).find(html)
                    if (voeMatch != null) return voeMatch.groupValues[1]
                }

                // 5. Streamtape (robotlink)
                if (lowerEmbed.contains("streamtape") || lowerEmbed.contains("streamta.pe")) {
                    val linkMatch = Regex("""id=['"](?:robotlink|ideoolink)['"][^>]*>(.*?)<""").find(html)
                    if (linkMatch != null && linkMatch.groupValues[1].startsWith("//")) {
                        return "https:" + linkMatch.groupValues[1]
                    }
                    val partMatch = Regex("""document\.getElementById\(['"](?:robotlink|ideoolink)['"]\)\.innerHTML\s*=\s*['"]([^'"]+)['"]\s*\+\s*\(['"]([^'"]+)['"]\)\.substring\((\d+)\)""").find(html)
                    if (partMatch != null) {
                        val p1 = partMatch.groupValues[1]
                        val p2 = partMatch.groupValues[2]
                        val sub = partMatch.groupValues[3].toIntOrNull() ?: 0
                        val combined = if (sub < p2.length) p1 + p2.substring(sub) else p1 + p2
                        return if (combined.startsWith("//")) "https:$combined" else combined
                    }
                }

                // 6. Sibnet (video.sibnet.ru)
                if (lowerEmbed.contains("sibnet.ru")) {
                    val sibMatch = Regex("""src\s*:\s*["'](/v/[^"']+\.mp4)["']""").find(html)
                    if (sibMatch != null) {
                        return "https://video.sibnet.ru" + sibMatch.groupValues[1]
                    }
                }

                // 7. Bitchute direct MP4
                if (lowerEmbed.contains("bitchute.com")) {
                    val bcMatch = Regex("""(https?://[^\s"'<>]+\.bitchute\.com/[^\s"'<>]+\.mp4)""", RegexOption.IGNORE_CASE).find(html)
                    if (bcMatch != null) return bcMatch.groupValues[1]
                }

                // 8. Ok.ru / Odnoklassniki HLS Manifest
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
                                val hls = flashvars.optString("hlsManifestUrl")
                                if (hls.isNotBlank()) {
                                    return hls.replace("&amp;", "&").replace("\\u0026", "&")
                                }

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
                }

                // 9. Mixdrop
                val mixdropMatch = Regex("""MDCore\.wurl\s*=\s*["']([^"']+)["']""", RegexOption.IGNORE_CASE).find(html)
                if (mixdropMatch != null) {
                    val streamUrl = mixdropMatch.groupValues[1]
                    return if (streamUrl.startsWith("//")) "https:$streamUrl" else streamUrl
                }

                // 10. Vidhide, FileLions, StreamWish, JWPlayer, VTube, Dropload, StreamRuby
                val m3u8Match = Regex("""file\s*:\s*["'](https?://[^"']+\.m3u8[^"']*)["']""", RegexOption.IGNORE_CASE).find(html)
                    ?: Regex("""sources\s*:\s*\[\s*\{\s*file\s*:\s*["'](https?://[^"']+\.m3u8[^"']*)["']""", RegexOption.IGNORE_CASE).find(html)
                    ?: Regex("""src\s*:\s*["'](https?://[^"']+\.m3u8[^"']*)["']""", RegexOption.IGNORE_CASE).find(html)
                    ?: Regex("""["'](https?://[^"']+\.m3u8[^"']*)["']""", RegexOption.IGNORE_CASE).find(html)
                if (m3u8Match != null) {
                    return m3u8Match.groupValues[1]
                }

                // 11. VK.com / VKVideo
                if (lowerEmbed.contains("vk.com") || lowerEmbed.contains("vkvideo")) {
                    val vkMatch = Regex("""["'](url(?:1080|720|480|360))["']\s*:\s*["'](https?://[^"']+)["']""").find(html)
                    if (vkMatch != null) {
                        return vkMatch.groupValues[2].replace("\\/", "/")
                    }
                }

                // 12. Direct MP4 regex
                val mp4Match = Regex("""["'](https?://[^"']+\.mp4[^"']*)["']""", RegexOption.IGNORE_CASE).find(html)
                if (mp4Match != null) {
                    return mp4Match.groupValues[1]
                }

                // 13. Generic JSON source variables (JWPlayer, VideoJS, Plyr, etc.)
                val jsonSourceRegex = Regex("""["']?(?:file|src|url|hls|stream)["']?\s*:\s*["'](https?://[^"']+\.(?:m3u8|mp4)[^"']*)["']""", RegexOption.IGNORE_CASE)
                val jsonMatch = jsonSourceRegex.find(html)
                if (jsonMatch != null) {
                    return jsonMatch.groupValues[1]
                }

                // 14. Nested iframe discovery (up to 2 levels deep for modern multi-layered embed players)
                if (depth < 2) {
                    val doc = Jsoup.parse(html, embedUrl)
                    doc.select("iframe[src], iframe[data-src], iframe[data-frame]").forEach { nestedIframe ->
                        val nestedSrc = nestedIframe.absUrl("src")
                            .ifBlank { nestedIframe.attr("src") }
                            .ifBlank { nestedIframe.attr("data-src") }
                            .ifBlank { nestedIframe.attr("data-frame") }
                        if (!nestedSrc.isNullOrBlank() && nestedSrc != embedUrl && !nestedSrc.endsWith(".jpg") && !nestedSrc.endsWith(".png") && !nestedSrc.endsWith(".css") && !nestedSrc.endsWith(".js")) {
                            val nestedRes = unpackEmbedStreamUrl(nestedSrc, embedUrl, depth = depth + 1)
                            if (nestedRes.isNotBlank()) return nestedRes
                        }
                    }
                }

                // 15. Any direct .m3u8 or .mp4 stream anywhere in page scripts/HTML
                val anyStreamRegex = Regex("""https?://[^\s"'`<>]+?\.(?:m3u8|mp4)[^\s"'`<>]*""", RegexOption.IGNORE_CASE)
                val streamMatch = anyStreamRegex.find(html)
                if (streamMatch != null && !streamMatch.value.contains("/thumb") && !streamMatch.value.contains("/preview")) {
                    return streamMatch.value
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
        val safeSlug = if (slug.isNotBlank()) slug else "film-" + Math.abs(cleanTitle.hashCode())
        val fallbackId = "playrepo-$safeSlug"

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
                        val desc = if (scrapedDesc.isNotBlank()) scrapedDesc else (firstMeta.get("description")?.asString ?: "Play • 1080p TR")

                        if (!id.isNullOrBlank()) {
                            return MetaResolution(
                                id = id,
                                title = name,
                                poster = if (poster.isNotBlank()) poster else fallbackPoster,
                                description = desc,
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
                            val overview = if (scrapedDesc.isNotBlank()) scrapedDesc else (firstMovie.get("overview")?.asString ?: "Play • 1080p TR")
                            val releaseDate = firstMovie.get("release_date")?.asString?.take(4) ?: year

                            if (!id.isNullOrBlank()) {
                                return MetaResolution(
                                    id = "tmdb-$id",
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

        // 3. Fallback: Use scraped movie page data (NEVER lose the movie's real poster or valid ID!)
        return MetaResolution(
            id = fallbackId,
            title = cleanTitle,
            poster = fallbackPoster,
            description = if (scrapedDesc.isNotBlank()) scrapedDesc else "Play • Film Yayın Kaynağı",
            year = year
        )
    }
}
