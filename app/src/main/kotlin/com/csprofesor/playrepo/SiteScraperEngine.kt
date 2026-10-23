package com.csprofesor.playrepo

import com.google.gson.Gson
import com.google.gson.JsonObject
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.OkHttpClient
import okhttp3.Request
import org.jsoup.Jsoup
import java.net.URLEncoder
import java.util.concurrent.TimeUnit

class SiteScraperEngine {

    private val client = OkHttpClient.Builder()
        .connectTimeout(12, TimeUnit.SECONDS)
        .readTimeout(12, TimeUnit.SECONDS)
        .followRedirects(true)
        .build()

    private val gson = Gson()
    private val userAgent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"

    /**
     * Finds ALL movie page links across all catalog pagination pages.
     */
    suspend fun discoverAllMovieLinks(
        siteUrl: String,
        maxPages: Int = 20,
        onPageProgress: (pageNumber: Int, foundMoviesCount: Int, statusMsg: String) -> Unit
    ): List<String> = withContext(Dispatchers.IO) {
        val siteBase = if (siteUrl.contains("://")) {
            val parts = siteUrl.split("/")
            "${parts[0]}//${parts[2]}"
        } else siteUrl

        val cleanSiteUrl = siteUrl.trimEnd('/')
        val discoveredMovies = mutableSetOf<String>()
        val visitedCatalogPages = mutableSetOf<String>()
        val catalogPagesQueue = mutableListOf(siteUrl)

        for (i in 1..maxPages) {
            val pUrl = if (cleanSiteUrl.contains("page")) cleanSiteUrl else "$cleanSiteUrl/page/$i/"
            if (!catalogPagesQueue.contains(pUrl)) {
                catalogPagesQueue.add(pUrl)
            }
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
                    .header("User-Agent", userAgent)
                    .build()

                client.newCall(request).execute().use { response ->
                    if (!response.isSuccessful) {
                        onPageProgress(pageIndex, discoveredMovies.size, "Sayfa $pageIndex yanıt vermedi (${response.code})")
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

                    // Extract next catalog pages from pagination links
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
        }

        discoveredMovies.toList()
    }

    private fun isValidMovieUrl(url: String, siteBase: String): Boolean {
        if (!url.startsWith(siteBase)) return false
        val lower = url.lowercase()
        if (url == siteBase || url == "$siteBase/" ||
            lower.contains("/kategori/") || lower.contains("/tur/") ||
            lower.contains("/koleksiyon/") || lower.contains("/forum") ||
            lower.contains("/iletisim") || lower.contains("/page/") ||
            lower.contains("/tag/") || lower.contains("#") ||
            lower.contains("javascript:") || lower.endsWith(".jpg") ||
            lower.endsWith(".png") || lower.endsWith(".css") || lower.endsWith(".js")
        ) {
            return false
        }
        return true
    }

    private fun isValidCatalogPageUrl(url: String, siteBase: String): Boolean {
        if (!url.startsWith(siteBase)) return false
        val lower = url.lowercase()
        if (isValidMovieUrl(url, siteBase)) return false
        return lower.contains("/page/") || lower.contains("page=") || lower.contains("/kategori/") || lower.contains("/tur/")
    }

    /**
     * Crawls all movie links across the whole site and scrapes details and streams for each movie.
     */
    suspend fun crawlEntireSite(
        siteUrl: String,
        maxPages: Int = 20,
        tmdbApiKey: String? = null,
        onStatusUpdate: (status: String) -> Unit
    ): List<ScrapedMovie> = withContext(Dispatchers.IO) {
        onStatusUpdate("Sitedeki tüm sayfalar taranıyor (Sayfalandırmalar kontrol ediliyor)...")

        val allMovieUrls = discoverAllMovieLinks(
            siteUrl = siteUrl,
            maxPages = maxPages,
            onPageProgress = { _, count, msg ->
                onStatusUpdate("$msg\nToplam Tespit Edilen Film: $count")
            }
        )

        if (allMovieUrls.isEmpty()) {
            onStatusUpdate("✗ Sitede film bağlantısı bulunamadı! Lütfen adresi kontrol edin.")
            return@withContext emptyList()
        }

        onStatusUpdate("✓ Toplam ${allMovieUrls.size} film tespit edildi! Kaynaklar çözülüyor...")

        val results = mutableListOf<ScrapedMovie>()

        allMovieUrls.forEachIndexed { index, url ->
            try {
                val movie = scrapeMoviePage(url, tmdbApiKey)
                if (movie != null) {
                    results.add(movie)
                    onStatusUpdate("[${index + 1} / ${allMovieUrls.size}] Çözüldü: ${movie.title} (${movie.streams.size} yayın kaynağı)")
                }
            } catch (e: Exception) {
                onStatusUpdate("[${index + 1} / ${allMovieUrls.size}] Sayfa hatası: ${e.localizedMessage}")
            }
        }

        results
    }

    /**
     * Scrapes a single movie page, resolves iframe embeds into direct M3U8/MP4 streams, and resolves TMDB ID.
     */
    suspend fun scrapeMoviePage(movieUrl: String, tmdbApiKey: String? = null): ScrapedMovie? = withContext(Dispatchers.IO) {
        val request = Request.Builder()
            .url(movieUrl)
            .header("User-Agent", userAgent)
            .build()

        client.newCall(request).execute().use { response ->
            if (!response.isSuccessful) return@withContext null

            val html = response.body?.string().orEmpty()
            val doc = Jsoup.parse(html, movieUrl)

            val rawTitle = doc.selectFirst("h1")?.text()?.trim()
                ?: doc.selectFirst("meta[property=og:title]")?.attr("content")?.trim()
                ?: movieUrl.substringAfterLast("/").replace("-", " ")

            val cleanTitle = cleanMovieTitle(rawTitle)
            if (cleanTitle.isBlank()) return@withContext null

            val foundStreams = mutableListOf<StreamItem>()

            // 1. Scrape direct iframe & video tags
            doc.select("iframe[src], video[src], video source[src]").forEach { el ->
                val src = el.absUrl("src").ifBlank { el.attr("src") }
                if (src.isNotBlank()) {
                    val resolvedStream = resolveStreamUrl(src, movieUrl, foundStreams.size + 1)
                    foundStreams.add(resolvedStream)
                }
            }

            // 2. Scrape external links (vidmoly, vidhide, voe, streamtape, ok.ru, kinescope)
            doc.select("a[href]").forEach { a ->
                val href = a.absUrl("href").ifBlank { a.attr("href") }
                if (href.contains("vidmoly") || href.contains("vidhide") ||
                    href.contains("voe.sx") || href.contains("streamtape") ||
                    href.contains("ok.ru") || href.contains("kinescope")
                ) {
                    val resolvedStream = resolveStreamUrl(href, movieUrl, foundStreams.size + 1)
                    if (foundStreams.none { it.url == resolvedStream.url }) {
                        foundStreams.add(resolvedStream)
                    }
                }
            }

            // Fallback: If no embeds, use page URL
            if (foundStreams.isEmpty()) {
                foundStreams.add(
                    StreamItem(
                        name = "PlayRepo",
                        title = "PlayRepo • Sunucu 1 • 1080p",
                        url = movieUrl,
                        quality = 1080,
                        format = "m3u8",
                        referer = movieUrl
                    )
                )
            }

            // Resolve TMDB ID
            val tmdbId = resolveTmdbId(cleanTitle, tmdbApiKey)

            ScrapedMovie(
                tmdbId = tmdbId,
                title = cleanTitle,
                streams = foundStreams
            )
        }
    }

    private fun cleanMovieTitle(title: String): String {
        return title
            .removeSuffix(" izle")
            .removeSuffix(" İzle")
            .removeSuffix(" Türkçe Dublaj")
            .removeSuffix(" Türkçe Altyazılı")
            .removeSuffix(" Full HD")
            .removeSuffix(" 1080p")
            .replace(Regex("""\b(19|20)\d{2}\b.*"""), "")
            .trim()
    }

    private fun resolveStreamUrl(embedUrl: String, referer: String, serverIndex: Int): StreamItem {
        val directStreamUrl = unpackEmbedStreamUrl(embedUrl, referer)
        val format = if (directStreamUrl.contains(".m3u8")) "m3u8" else "mp4"

        return StreamItem(
            name = "PlayRepo",
            title = "PlayRepo • Sunucu $serverIndex • 1080p",
            url = directStreamUrl,
            quality = 1080,
            format = format,
            referer = embedUrl
        )
    }

    /**
     * Unpacks/decrypts embed players (Vidmoly, Vidhide, VOE, Kinescope, Streamtape) into direct M3U8 / MP4 URLs.
     */
    private fun unpackEmbedStreamUrl(embedUrl: String, referer: String): String {
        try {
            val request = Request.Builder()
                .url(embedUrl)
                .header("User-Agent", userAgent)
                .header("Referer", referer)
                .build()

            client.newCall(request).execute().use { response ->
                if (!response.isSuccessful) return embedUrl
                val html = response.body?.string().orEmpty()

                // 1. Vidmoly & standard M3U8 regex: file:"(https://...m3u8)"
                val m3u8Match = Regex("""file\s*:\s*["'](https?://[^"']+\.m3u8[^"']*)["']""", RegexOption.IGNORE_CASE).find(html)
                    ?: Regex("""["'](https?://[^"']+\.m3u8[^"']*)["']""", RegexOption.IGNORE_CASE).find(html)
                if (m3u8Match != null) {
                    return m3u8Match.groupValues[1]
                }

                // 2. VOE regex: 'hls': '(https://...m3u8)'
                val voeMatch = Regex("""'hls'\s*:\s*['"](https?://[^'"]+)['"]""", RegexOption.IGNORE_CASE).find(html)
                if (voeMatch != null) {
                    return voeMatch.groupValues[1]
                }

                // 3. Direct MP4 regex
                val mp4Match = Regex("""["'](https?://[^"']+\.mp4[^"']*)["']""", RegexOption.IGNORE_CASE).find(html)
                if (mp4Match != null) {
                    return mp4Match.groupValues[1]
                }
            }
        } catch (_: Exception) { }

        return embedUrl
    }

    private fun resolveTmdbId(title: String, tmdbApiKey: String?): String {
        if (!tmdbApiKey.isNullOrBlank()) {
            try {
                val encodedQuery = URLEncoder.encode(title, "UTF-8")
                val url = "https://api.themoviedb.org/3/search/movie?api_key=$tmdbApiKey&query=$encodedQuery&language=tr-TR"
                val request = Request.Builder().url(url).header("User-Agent", userAgent).build()
                client.newCall(request).execute().use { response ->
                    if (response.isSuccessful) {
                        val body = response.body?.string().orEmpty()
                        val jsonObj = gson.fromJson(body, JsonObject::class.java)
                        val results = jsonObj.getAsJsonArray("results")
                        if (results != null && results.size() > 0) {
                            val firstMovie = results.get(0).asJsonObject
                            val id = firstMovie.get("id")?.asString
                            if (!id.isNullOrBlank()) {
                                return id
                            }
                        }
                    }
                }
            } catch (_: Exception) { }
        }

        val hash = kotlin.math.abs(title.lowercase().hashCode()) % 800000 + 100000
        return hash.toString()
    }
}
